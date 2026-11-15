#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Variety pass on a cut list: no two neighbours that read as the same shot, a palette change at least every 4 cuts,
alternating motion, and hero shots on the drops.

Why: a cut can use every clip once and still feel repetitive when similar subjects land close together (three
fireworks shots, several riverside temples), and it feels off-beat when the strongest shots miss the drops.

Input: a timeline (our cut format: [[bar, bars, clip_id, in_s, use_s], ...]) plus a map {clip_id: {"video": path}},
optionally an audiomap.json (for drops) and a tags file {clip_id: {"subject": "fireworks", "hero": true}}.
Usage:
  python3 diversity.py <timeline.json> <map.json> [--audiomap audiomap.json] [--tags tags.json] [--fix out_timeline.json]
Measured per cut (middle frame of the used range + 0.5 s of flow): mean Lab colour, brightness, motion direction
(camera move removed), motion amount, and the subject tag when given.
Rules:
  twin       neighbours with nearly the same colour, brightness and motion direction, or the same subject tag
  flat       4 cuts in a row without a clear colour change
  subject    the same subject tag within 3 cuts (fireworks, temple, river ...)
  drop       a drop / surge from the audiomap that does not land on a cut tagged hero (or on the highest-motion cut nearby)
--fix swaps cuts inside the same section (same length, same pacing) to remove violations, never changes lengths.
"""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np


def measure(video, t_in, use):
    import cv2
    t = t_in + use / 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t - 0.25):.2f}", "-i", str(video), "-frames:v", "7", "-vf", "fps=12,scale=160:90",
                          "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 90, 160, 3)
    if len(fr) < 2:
        return None
    lab = cv2.cvtColor(fr[len(fr) // 2], cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    g0 = cv2.cvtColor(fr[0], cv2.COLOR_BGR2GRAY); g1 = cv2.cvtColor(fr[-1], cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 3, 15, 3, 5, 1.1, 0)
    cam = np.median(flow.reshape(-1, 2), axis=0)
    obj = flow.reshape(-1, 2) - cam
    mv = np.hypot(*obj.T); m = mv > np.percentile(mv, 75)
    dirv = obj[m].mean(0) if m.any() else np.zeros(2)
    total = cam + dirv
    return {"lab": lab.mean(0).tolist(), "L": float(lab[:, 0].mean()), "dir": float(np.degrees(np.arctan2(total[1], total[0]))),
            "motion": float(np.hypot(*total))}


def twin(a, b):
    dc = np.linalg.norm(np.array(a["lab"]) - np.array(b["lab"]))
    dd = abs((a["dir"] - b["dir"] + 180) % 360 - 180)
    return dc < 9 and (dd < 35 or (a["motion"] < 0.5 and b["motion"] < 0.5))


def check(tl, feats, tags, am):
    issues = []
    for i in range(1, len(tl)):
        a, b = feats.get(i - 1), feats.get(i)
        ta, tb = tags.get(tl[i - 1][2], {}).get("subject"), tags.get(tl[i][2], {}).get("subject")
        if a and b and twin(a, b):
            issues.append({"i": i, "rule": "twin", "detail": f"{tl[i - 1][2]} and {tl[i][2]} read as the same shot"})
        if ta and ta == tb:
            issues.append({"i": i, "rule": "twin", "detail": f"same subject '{ta}' back to back"})
    for i in range(3, len(tl)):
        win = [feats.get(k) for k in range(i - 3, i + 1)]
        if all(win) and max(np.linalg.norm(np.array(x["lab"]) - np.array(win[0]["lab"])) for x in win) < 10:
            issues.append({"i": i, "rule": "flat", "detail": f"cuts {i - 3}-{i}: no colour change in 4 cuts"})
    for i in range(len(tl)):
        s = tags.get(tl[i][2], {}).get("subject")
        if s and any(tags.get(tl[k][2], {}).get("subject") == s for k in range(max(0, i - 3), i - 1)):
            issues.append({"i": i, "rule": "subject", "detail": f"'{s}' again within 3 cuts"})
    if am:
        for km in am.get("key_moments", []):
            if km["kind"] not in ("drop", "surge"):
                continue
            k = min(range(len(tl)), key=lambda j: abs(tl[j][0] - km["bar"]))
            hero = tags.get(tl[k][2], {}).get("hero")
            near = [j for j in range(max(0, k - 2), min(len(tl), k + 3)) if feats.get(j)]
            strongest = max(near, key=lambda j: feats[j]["motion"]) if near else k
            if not hero and strongest != k:
                issues.append({"i": k, "rule": "drop", "detail": f"{km['kind']} at bar {km['bar']}: lands on {tl[k][2]}, "
                                                                  f"strongest nearby is {tl[strongest][2]} (cut {strongest})"})
    return issues


def fix(tl, feats, tags, am, rounds=200):
    """Greedy swaps between cuts of equal length; keep a swap only when the issue count drops."""
    best = check(tl, feats, tags, am)
    for _ in range(rounds):
        if not best:
            break
        i = best[0]["i"]
        improved = False
        for j in range(len(tl)):
            if j == i or tl[j][1] != tl[i][1]:
                continue
            tl2 = [list(e) for e in tl]; f2 = dict(feats)
            tl2[i][2:], tl2[j][2:] = tl[j][2:], tl[i][2:]
            f2[i], f2[j] = feats.get(j), feats.get(i)
            iss = check(tl2, f2, tags, am)
            if len(iss) < len(best):
                tl, feats, best, improved = tl2, f2, iss, True
                break
        if not improved:
            best = best[1:] + best[:1] if len(best) > 1 else best
            if all(x["rule"] == "drop" for x in best):
                break
    return tl, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline"); ap.add_argument("map"); ap.add_argument("--audiomap"); ap.add_argument("--tags"); ap.add_argument("--fix")
    a = ap.parse_args()
    tl = json.loads(Path(a.timeline).read_text()); mp = json.loads(Path(a.map).read_text())
    am = json.loads(Path(a.audiomap).read_text()) if a.audiomap else None
    tags = json.loads(Path(a.tags).read_text()) if a.tags else {}
    feats = {}
    for i, e in enumerate(tl):
        v = mp[e[2]]["video"] if isinstance(mp[e[2]], dict) else mp[e[2]]
        f = measure(v, e[3] if len(e) > 3 else 0, e[4] if len(e) > 4 else 2.0)
        if f:
            feats[i] = f
    iss = check(tl, feats, tags, am)
    counts = {}
    for x in iss:
        counts[x["rule"]] = counts.get(x["rule"], 0) + 1
    print(f"{len(tl)} cuts, {len(iss)} issues: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    for x in iss[:25]:
        print(f"  cut {x['i']:3d} {x['rule']:7s} {x['detail']}")
    if a.fix:
        tl2, left = fix(tl, feats, tags, am)
        Path(a.fix).write_text(json.dumps(tl2))
        print(f"fixed timeline -> {a.fix}: {len(iss)} -> {len(left)} issues ({sum(1 for x, y in zip(tl, tl2) if x[2] != y[2])} cuts moved)")


if __name__ == "__main__":
    main()
