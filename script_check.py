#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Script checker for beats.json (see SCRIPTS.md). Exit 1 when the script would break in production or in review.

Usage:
  python3 script_check.py <beats.json> [--audiomap audiomap.json] [--gate]
  --gate   the script is about to go to the client: an unsigned promise and an unchosen concept are FAILs, not warnings
"""
import argparse
import json
import re
import sys
from pathlib import Path

import prompt_lint

FIELDS = ["subject", "subject_motion", "scene", "framing", "camera", "intent", "role"]
ROLES = {"hook", "setup", "turn", "build", "payoff", "breath", "brand", "cta", "proof", "problem", "product", "drop", "establish"}
SECONDS = {"brand_film": (3, 8), "ugc_ad": (1.5, 6), "music_video": (0.4, 16)}
WPS = {"brand_film": (1.5, 2.6), "ugc_ad": (2.0, 3.0), "music_video": (1.5, 3.0)}     # per line; brand films keep warm pauses
TOTAL_WPS = {"brand_film": (1.7, 2.3), "ugc_ad": (2.2, 3.0)}                              # whole film: calm brand narration, 100 to 130 words per 60 s
HOOK_MAX = {"brand_film": 5, "ugc_ad": 3, "music_video": 8}
BANNED = ["great question", "you're absolutely right", "absolutely", "definitely", "honest answer", "honestly", "genuinely",
          "straightforward", "seamless", "leverage", "robust", "delve", "pivotal", "crucial", "landscape", "showcases",
          "tapestry", "testament", "vibrant", "fosters", "underscores", "in a world", "game changer", "game-changer"]
STOP = set("the a an and or but of to in on at for with by from is are was were be been it its this that these those his her "
           "their our your my we you they he she i as into over under up down out not no so then than just".split())


def words(t):
    return re.findall(r"[A-Za-z']+", t or "")


def check(b, am=None, gate=False):
    fails, warns = [], []
    F = lambda m: fails.append(m)
    W = lambda m: warns.append(m)
    fmt = b.get("format")
    if fmt not in SECONDS:
        F(f"format must be one of {sorted(SECONDS)}")
        fmt = "brand_film"
    pr = b.get("promise", {})
    if not pr.get("text"):
        F("no delivery promise: write what the film will and will not do (SCRIPTS.md 1.2)")
    elif not pr.get("signed"):
        (F if gate else W)("promise not signed by the client yet")
    if len(b.get("concepts", [])) < 3 and not b.get("chosen"):
        F("fewer than three concepts and none chosen")
    if gate and not b.get("chosen"):
        F("no concept chosen")
    chars = {c.lower() for c in b.get("characters", [])}
    rows = b.get("rows", [])
    if not rows:
        F("no rows")
    t = 0.0
    lines = []
    for i, r in enumerate(rows):
        rid = r.get("id", f"row{i + 1}")
        miss = [f for f in FIELDS if not str(r.get(f, "")).strip()]
        if miss:
            F(f"{rid}: missing {', '.join(miss)}")
        if r.get("role") and r["role"] not in ROLES:
            W(f"{rid}: role '{r['role']}' is not one of {sorted(ROLES)}")
        sec = float(r.get("seconds", 0) or 0)
        per = sec / max(1, int(r.get("cuts", 1)))          # music-video rows may group several cuts of one section
        lo, hi = SECONDS[fmt]
        if not sec:
            F(f"{rid}: no seconds")
        elif not lo <= per <= hi:
            W(f"{rid}: {per:.1f} s per shot is outside {lo} to {hi} s for {fmt}")
        t += sec
        motion = r.get("subject_motion", "")
        if re.search(r"\b(and then|then|while|as well as)\b", motion) or len(re.findall(r",|\band\b", motion)) >= 2:
            W(f"{rid}: subject_motion may hold two actions: '{motion}' (one action per shot)")
        cam = prompt_lint.lint(r.get("camera", "") + ". No people. No readable text.", "video")
        if any(x[1] == "camera" and x[0] == "FAIL" for x in cam):
            F(f"{rid}: more than one camera move in '{r.get('camera')}'")
        has_char = str(r.get("subject", "")).lower() in chars or any(c in str(r.get("subject", "")).lower() for c in chars)
        for lvl, rule, d in prompt_lint.lint(f"{r.get('subject', '')}, {r.get('scene', '')}. No readable text.", "image", characters=has_char):
            if lvl == "FAIL" and rule in ("crowd", "text"):
                F(f"{rid}: scene asks the model for {rule}: {d}")
        if r.get("magic") and not r.get("read_in_1s"):
            W(f"{rid}: magical image not confirmed readable in one second (read_in_1s)")
        vo = (r.get("vo") or "").strip()
        if vo:
            lines.append((rid, vo, sec))
    # hook
    if rows and rows[0].get("role") != "hook":
        W("first row is not the hook")
    elif rows and float(rows[0].get("seconds", 0) or 0) / max(1, int(rows[0].get("cuts", 1))) > HOOK_MAX[fmt]:
        W(f"hook shot is {float(rows[0].get('seconds')) / max(1, int(rows[0].get('cuts', 1))):.1f} s; {fmt} hooks land within {HOOK_MAX[fmt]} s")
    # ending
    if fmt == "brand_film" and rows and rows[-1].get("role") != "brand":
        W("brand film does not end on the brand line row")
    if fmt == "ugc_ad" and rows and rows[-1].get("role") != "cta":
        F("UGC ad does not end on a call to action")
    # narration
    lo, hi = WPS[fmt]
    for rid, vo, sec in lines:
        n = len(words(vo))
        role = next((r.get("role") for r in rows if r.get("id") == rid), "")
        if sec and (n / sec > hi * 1.15 or (n / sec < lo and role not in ("brand", "cta", "hook"))):
            W(f"{rid}: {n} words in {sec} s = {n / sec:.1f} words/s (aim {lo} to {hi})")
        if n < 3 or not re.search(r"[.!?]$", vo) or not vo[0].isupper():
            F(f"{rid}: line is not a complete sentence: '{vo}'")
        low = vo.lower()
        for bw in BANNED:
            if re.search(r"\b" + re.escape(bw) + r"\b", low):
                F(f"{rid}: banned phrase '{bw}'")
        if "\u2014" in vo or "\u2013" in vo:
            F(f"{rid}: em or en dash in narration")
        if re.search(r"\bnot (just|only) .+ but\b", low):
            F(f"{rid}: 'not just X but Y' pattern")
    for i in range(len(lines)):
        w0 = {w.lower() for w in words(lines[i][1]) if w.lower() not in STOP and len(w) > 3}
        for j in range(i + 1, min(len(lines), i + 3)):
            rep = w0 & {w.lower() for w in words(lines[j][1]) if len(w) > 3}
            if rep:
                W(f"{lines[i][0]} and {lines[j][0]} repeat '{', '.join(sorted(rep))}'")
    if lines and fmt in TOTAL_WPS:
        total = sum(len(words(v)) for _, v, _ in lines)
        tl, th = TOTAL_WPS[fmt]
        if t and not tl <= total / t <= th:
            (F if gate else W)(f"narration {total} words over {t:.0f} s = {total / t:.1f} words/s overall (aim {tl} to {th}; about {int(tl * t)} to {int(th * t)} words)")
    # music video: rows must cover the song and fit the plan
    if fmt == "music_video" and am:
        if abs(t - am["duration"]) > am["bar_s"]:
            F(f"rows cover {t:.1f} s, the song is {am['duration']:.1f} s")
        ids = [r.get("clip") for r in rows if r.get("clip")]
        if len(ids) != len(set(ids)) and b.get("promise", {}).get("no_repeat", True):
            F("a clip id appears twice and the promise says every shot once")
        n_cuts = sum(int(r.get("cuts", 1)) for r in rows)
        if abs(n_cuts - am["plan"]["cuts"]) > am["plan"]["cuts"] * 0.15:
            W(f"{n_cuts} cuts in the script vs {am['plan']['cuts']} the song map suggests")
    return fails, warns, t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("beats"); ap.add_argument("--audiomap"); ap.add_argument("--gate", action="store_true")
    a = ap.parse_args()
    b = json.loads(Path(a.beats).read_text())
    am = json.loads(Path(a.audiomap).read_text()) if a.audiomap else None
    fails, warns, t = check(b, am, a.gate)
    print(f"{b.get('title', a.beats)}: {len(b.get('rows', []))} rows, {t:.1f} s, format {b.get('format')}")
    for m in fails:
        print("FAIL", m)
    for m in warns:
        print("warn", m)
    print("SCRIPT OK" if not fails else f"{len(fails)} FAIL(s): fix before it goes to the client")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
