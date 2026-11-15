#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Look sheet: one visual identity per project, used by every frame prompt, checked on every frame, applied once as a grade.

Why: films lose their look when style references come from elsewhere or when stock footage is mixed in. One look
sheet, one approved hero still per light state, and one grade over the whole timeline at the end.

<project>/look.json
{
  "name": "City after rain",
  "palette": {"#0E1A2B": "ink blue shadows", "#E8A33D": "sodium gold", "#D9467A": "neon magenta", "#2FB5A8": "teal"},
  "lens": "35 mm anamorphic, shallow depth of field, soft halation on highlights",
  "film": "Kodak 5219 look, fine grain, slightly lifted blacks",
  "anchors": ["wet reflective surfaces in every night shot", "gold and teal light, never pure white",
              "low camera, near the ground", "no blue sky at midday: early morning or golden hour only"],
  "hero": {"day": "keys/hero_day.png", "gold": "keys/hero_gold.png", "night": "keys/hero_night.png"},
  "people": "none",                              "none" or a short casting line
  "grade": {"contrast": 1.06, "saturation": 1.05, "gamma": 0.98, "shadows": [0.02, 0.0, 0.04], "highlights": [0.03, 0.01, -0.03],
            "grain": 6, "vignette": 0.25, "lut": null}
}
Commands:
  python3 look.py new <project>                          write a look.json template
  python3 look.py prompt <project> <tag>                 print the prompt prefix + clauses for a frame of light state <tag>
  python3 look.py profile <project> <approved frames...>   learn the look profile from approved frames (8 or more)
  python3 look.py check <project> <img|clip> [...]        outlier frames and batch drift against the profile
  python3 look.py match <project> <in.mp4> <out.mp4> [tag] move a clip's color statistics onto its hero still (before the edit)
  python3 look.py grade <project> <in.mp4> <out.mp4>      the ONE grade + grain + vignette pass over the finished timeline
In code: look.prefix(cfg, tag), look.hero(cfg, tag) (pass it as image 1 to every frame), look.features(img)
Rules:
  - Every start frame gets the hero still of its light state as reference image 1 (never a still from another film).
  - The grade runs once, on the final timeline, never per clip. `match` is for a clip that drifts, before the edit.
  - Stock or real footage is not mixed into AI-led films (measured: cooler highlights, half the saturation). If it must
    be, match + grade + grain.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

TEMPLATE = {
    "name": "", "palette": {"#0E1A2B": "ink blue shadows", "#E8A33D": "warm gold"},
    "lens": "35 mm anamorphic, shallow depth of field", "film": "fine film grain, slightly lifted blacks",
    "anchors": ["one anchor per line: a rule every frame obeys"],
    "hero": {"day": "keys/hero_day.png", "night": "keys/hero_night.png"},
    "people": "none",
    "grade": {"contrast": 1.05, "saturation": 1.04, "gamma": 1.0, "shadows": [0.02, 0.0, 0.03],
              "highlights": [0.02, 0.0, -0.02], "grain": 5, "vignette": 0.2, "lut": None},
}
NO_PEOPLE = ("The scene is completely empty of people: no pedestrians, crowds, silhouettes, figures, drivers, passengers, "
             "hands or reflections of people anywhere.")
NO_TEXT = "No readable or pseudo letters, numbers, plates or logos anywhere; surfaces that could carry writing are plain and unmarked."


def load(project):
    return json.loads((Path(project) / "look.json").read_text())


def hero(cfg, tag):
    h = cfg.get("hero", {})
    return h.get(tag) or next(iter(h.values()), None)


def prefix(cfg, tag=""):
    pal = ", ".join(f"{name} ({hx})" for hx, name in cfg.get("palette", {}).items())
    parts = [f"Same look as image 1 (the approved hero still): {cfg.get('lens', '')}, {cfg.get('film', '')}.",
             f"Palette: {pal}." if pal else "", " ".join(a.rstrip(".") + "." for a in cfg.get("anchors", [])),
             "Use image 1 only for colour, light and lens; do not copy its content."]
    tail = [NO_TEXT]
    if cfg.get("people", "none") == "none":
        tail.insert(0, NO_PEOPLE)
    return " ".join(p for p in parts if p), " ".join(tail)


def _lab_stats(img_bgr):
    import cv2
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    return lab.mean(0), lab.std(0)


def _read(path):
    import cv2
    p = Path(path)
    if p.suffix.lower() in (".mp4", ".mov", ".webm"):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1", "-vf", "scale=480:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    im = cv2.imread(str(p)); return cv2.resize(im, (480, int(480 * im.shape[0] / im.shape[1])))


FEATURES = ["highlight warmth", "saturation", "sharpness", "black level"]


def features(img_bgr):
    """Grading traits, not content: warmth of the highlights (Lab b of the top 10%), mean chroma, detail (Laplacian),
    black level. Measured: AI frames about 19.6 / 22.0 / 54 / 6; free stock footage about 2.1 / 9.2 / 38 / 6."""
    import cv2
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32); L = lab[:, 0]
    hi = lab[L >= np.percentile(L, 90)]
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return np.array([hi[:, 2].mean() - 128, np.hypot(lab[:, 1] - 128, lab[:, 2] - 128).mean(),
                     cv2.Laplacian(g, cv2.CV_32F).var() ** 0.5, np.percentile(L, 2) / 2.55])


def cmd_profile(project, paths):
    """Build the look profile from approved frames (8 or more) and store it in look.json."""
    F = np.array([features(_read(p)) for p in paths])
    cfg = load(project)
    cfg["profile"] = {"n": len(F), "mean": F.mean(0).round(2).tolist(), "sd": np.maximum(F.std(0), [3, 3, 5, 2]).round(2).tolist()}
    (Path(project) / "look.json").write_text(json.dumps(cfg, indent=1))
    print("profile", dict(zip(FEATURES, cfg["profile"]["mean"])))


def cmd_check(project, paths):
    """Per frame: flag only extreme outliers (any trait beyond 2.5 sd). Per batch: flag drift when the batch mean of a
    trait moves more than 1 sd from the approved profile (single frames vary too much by content to judge alone)."""
    cfg = load(project)
    prof = cfg.get("profile")
    if not prof:
        hs = [Path(project) / p for p in cfg.get("hero", {}).values() if (Path(project) / p).exists()]
        if not hs:
            sys.exit("no profile and no hero stills: run `look.py profile <project> <approved frames>` first")
        F = np.array([features(_read(h)) for h in hs]); prof = {"mean": F.mean(0), "sd": np.maximum(F.std(0), [3, 3, 5, 2])}
    mu, sd = np.array(prof["mean"]), np.array(prof["sd"])
    Z = []
    for p in paths:
        z = (features(_read(p)) - mu) / sd; Z.append(z)
        worst = int(np.argmax(np.abs(z)))
        flag = "OUTLIER" if abs(z[worst]) > 2.5 else "ok"
        print(f"{flag:8s} {Path(p).name:30s} " + " ".join(f"{n.split()[0][:5]} {v:+.1f}" for n, v in zip(FEATURES, z)))
    zm = np.array(Z).mean(0)
    drift = [f"{n} {v:+.1f} sd" for n, v in zip(FEATURES, zm) if abs(v) > 1.0]
    print(("BATCH DRIFT: " + ", ".join(drift)) if drift else "batch matches the look profile")
    return not drift


def cmd_match(project, src, dst, tag=None):
    """Reinhard colour transfer of every frame onto the hero still's Lab statistics (keeps motion, moves colour)."""
    import cv2
    cfg = load(project); h = _read(Path(project) / hero(cfg, tag))
    hm, hs = _lab_stats(h)
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip().split(",")
    w, hgt, rate = int(probe[0]), int(probe[1]), probe[2]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, hgt, w, 3)
    cm, cs = _lab_stats(fr[len(fr) // 2])
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{hgt}", "-r", rate, "-i", "-",
                            "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(dst)], stdin=subprocess.PIPE)
    k = (hs / (cs + 1e-6)).clip(0.6, 1.6)
    for f in fr:
        lab = cv2.cvtColor(f, cv2.COLOR_BGR2LAB).astype(np.float32)
        lab = 0.7 * ((lab - cm) * k + hm) + 0.3 * lab                   # Reinhard transfer, 70%: keeps some of the clip's own life
        enc.stdin.write(cv2.cvtColor(lab.clip(0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR).tobytes())
    enc.stdin.close(); enc.wait()
    print(dst)


def grade_filter(g):
    sh, hi = g.get("shadows", [0, 0, 0]), g.get("highlights", [0, 0, 0])
    f = [f"eq=contrast={g.get('contrast', 1)}:saturation={g.get('saturation', 1)}:gamma={g.get('gamma', 1)}",
         f"colorbalance=rs={sh[0]}:gs={sh[1]}:bs={sh[2]}:rh={hi[0]}:gh={hi[1]}:bh={hi[2]}"]
    if g.get("lut"):
        f.append(f"lut3d='{g['lut']}'")
    if g.get("vignette"):
        f.append(f"vignette=angle={0.6 * g['vignette'] + 0.2:.2f}")
    if g.get("grain"):
        f.append(f"noise=alls={g['grain']}:allf=t")
    return ",".join(f)


def cmd_grade(project, src, dst):
    g = load(project).get("grade", {})
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", grade_filter(g), "-c:v", "libx264", "-crf", "16", "-preset", "slow",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(dst)], check=True)
    print(dst)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, project = sys.argv[1], sys.argv[2]
    if cmd == "new":
        p = Path(project) / "look.json"
        if p.exists():
            sys.exit(f"{p} exists")
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(TEMPLATE, indent=1)); print(p)
    elif cmd == "prompt":
        a, b = prefix(load(project), sys.argv[3] if len(sys.argv) > 3 else ""); print(a); print(b)
    elif cmd == "profile":
        cmd_profile(project, sys.argv[3:])
    elif cmd == "check":
        cmd_check(project, sys.argv[3:])
    elif cmd == "match":
        cmd_match(project, sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else None)
    elif cmd == "grade":
        cmd_grade(project, sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
