#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Clip QA: finds the AI-video defects the client caught by eye, before the client sees the clip.

What it measures, per generated clip (12 fps, 320 px analysis copy):
  popin      things that appear, vanish or morph: after warping the previous frame along its own optical flow, real motion
             leaves almost no error; an object that pops in, melts or swaps shape leaves a bright local error (a car
             turning see-through, riders popping in, a smoke blob growing from nothing)
  crossing   two opposite motion directions in the same region at once, after removing the camera move (motorbikes
             driving through each other, traffic switching direction)
  reversal   the dominant motion of a region flips direction mid-clip (a second road appearing, a rail track swapping)
  jump       a hard cut, flicker or corrupt frames (first 0.5 s of many Veo takes)
  text       readable letters by OCR (tesseract, confidence >= 70), when tesseract is installed
  people     person count rising between samples (YOLO, when ultralytics and models/yolov8x.pt exist)
Output: <clip>.qa.json next to the clip (or in --out), a strip image with flagged times in red, and the longest clean
range. PASS = no flag; REVIEW = flags but a clean range of at least --min-clean seconds (use that range); FAIL = no
usable range. Everything that is not PASS still goes to the independent reviewer with REVIEW_CHECKLIST.md.

Usage:
  python3 clip_qa.py <clip.mp4|dir> [...] [--out dir] [--min-clean 1.9] [--strict]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

FPS = 12
W, H = 320, 180
POPIN_T = 20.0     # local warp error (grey levels, 99th pct of 16x16 blocks) that marks a pop-in / morph
POPIN_R = 2.0      # ... and at least this many times the clip's own median (calibrated on 20 real Veo takes: rejected
                   # pop-ins 4.1, smoke blobs 2.0 to 2.5, road swaps 2.3 to 2.4; approved takes 1.1 to 1.7, a flame flare 2.5)
CROSS_T = 0.25     # each of two opposite motion streams holds at least 25% of a region's moving pixels (and both 65%+)
BLOB = 60         # pixels (at 320x180) of the largest solid blob in each opposite stream
PERSIST = 4        # frames in a row (at 12 fps, a third of a second) a crossing must last
JUMP_T = 28.0      # mean absolute frame difference (grey) after camera compensation that marks a cut or flicker


def frames(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={FPS},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)


def analyse(path):
    import cv2
    fr = frames(path)
    n = len(fr)
    popin, cross, jump, dirs = np.zeros(n), np.zeros(n), np.zeros(n), []
    grid = 4
    for i in range(1, n):
        a, b = fr[i - 1], fr[i]
        flow = cv2.calcOpticalFlowFarneback(a.astype(np.uint8), b.astype(np.uint8), None, 0.5, 3, 21, 3, 5, 1.1, 0)
        gx, gy = np.median(flow[..., 0]), np.median(flow[..., 1])          # camera move
        # warp b back onto a along the flow; the leftover error is what moved without being motion
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        warped = cv2.remap(b, xx + flow[..., 0], yy + flow[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        err = np.abs(warped - a)[8:-8, 8:-8]
        blocks = err[: (H - 16) // 16 * 16, : (W - 16) // 16 * 16].reshape((H - 16) // 16, 16, (W - 16) // 16, 16).mean(axis=(1, 3))
        popin[i] = float(np.percentile(blocks, 99))
        jump[i] = float(err.mean())
        # direction analysis on object motion (camera removed)
        fx, fy = flow[..., 0] - gx, flow[..., 1] - gy
        mag = np.hypot(fx, fy)
        moving = mag > max(0.6, np.percentile(mag, 80))
        rd = []
        for r in range(grid):
            for c in range(grid):
                sl = (slice(r * H // grid, (r + 1) * H // grid), slice(c * W // grid, (c + 1) * W // grid))
                m = moving[sl]
                if m.sum() < 40:
                    rd.append(None); continue
                ang = np.arctan2(fy[sl][m], fx[sl][m])
                R = np.hypot(np.sin(ang).mean(), np.cos(ang).mean())          # 1 = all one way, 0 = random (water, smoke, rain)
                mean = np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())
                # two rigid streams moving against each other: the angle histogram has two tight opposite peaks.
                # Find the strongest axis, then the share of pixels within 30 degrees of either end of it.
                h, _ = np.histogram(ang, bins=24, range=(-np.pi, np.pi))
                k = int(np.argmax(h[:12] + h[12:])); ax = -np.pi + (k + 0.5) * 2 * np.pi / 24
                near_a = np.cos(ang - ax) > np.cos(np.pi / 6); near_b = np.cos(ang - ax - np.pi) > np.cos(np.pi / 6)
                sa, sb = near_a.mean(), near_b.mean()
                if m.sum() > 150 and min(sa, sb) >= CROSS_T and sa + sb >= 0.65:
                    # rigid objects: each stream must contain one solid blob (water, rain, reflections are speckle)
                    full_a = np.zeros(m.shape, np.uint8); full_b = np.zeros(m.shape, np.uint8)
                    full_a[m] = near_a; full_b[m] = near_b
                    big = []
                    for fm in (full_a, full_b):
                        nlab, lab, stats, _ = cv2.connectedComponentsWithStats(cv2.morphologyEx(fm, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)))
                        big.append(stats[1:, cv2.CC_STAT_AREA].max() if nlab > 1 else 0)
                    if min(big) >= BLOB:
                        cross[i] = max(cross[i], float(min(sa, sb)))
                rd.append(float(mean) if R > 0.6 else None)
        dirs.append(rd)
    t = np.arange(n) / FPS
    flags = []
    base = np.median(popin[1:]) if n > 2 else 0
    for i in range(1, n):
        if popin[i] > max(POPIN_T, POPIN_R * base):
            flags.append({"t": round(float(t[i]), 2), "kind": "popin", "score": round(float(popin[i]), 1)})
        if cross[i] > CROSS_T and (cross[max(1, i - PERSIST + 1):i + 1] > CROSS_T).sum() >= PERSIST:
            flags.append({"t": round(float(t[i]), 2), "kind": "crossing", "score": round(float(cross[i]), 2)})
        if jump[i] > JUMP_T or (i > 1 and jump[i] > 4 * max(np.median(jump[1:]), 1)):
            flags.append({"t": round(float(t[i]), 2), "kind": "jump", "score": round(float(jump[i]), 1)})
    # reversal: a region whose mean direction flips by more than 135 degrees and stays flipped for 0.5 s
    D = np.array([[d if d is not None else np.nan for d in row] for row in dirs])
    if len(D) > 12:
        for k in range(D.shape[1]):
            col = D[:, k]
            for i in range(6, len(col) - 6):
                a0, a1 = col[i - 6:i], col[i:i + 6]
                if np.isnan(a0).any() or np.isnan(a1).any():
                    continue
                m0 = np.arctan2(np.sin(a0).mean(), np.cos(a0).mean()); m1 = np.arctan2(np.sin(a1).mean(), np.cos(a1).mean())
                if np.cos(m1 - m0) < -0.7:
                    flags.append({"t": round(float(t[i + 1]), 2), "kind": "reversal", "score": round(float(np.cos(m1 - m0)), 2), "region": k})
                    break
    extra = ocr_people(path)
    flags += extra
    flags.sort(key=lambda f: f["t"])
    return {"file": str(path), "duration": round(n / FPS, 2), "flags": flags,
            "series": {"popin": [round(float(x), 1) for x in popin], "cross": [round(float(x), 2) for x in cross]}}


def ocr_people(path):
    """Optional checks: OCR text and person counts on 4 sampled frames."""
    out = []
    import shutil
    tmpdir = Path("/tmp") / ("clipqa_" + Path(path).stem)
    tmpdir.mkdir(exist_ok=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout or 0)
    times = [dur * f for f in (0.1, 0.4, 0.7, 0.95)]
    pngs = []
    for i, tt in enumerate(times):
        p = tmpdir / f"s{i}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{tt:.2f}", "-i", str(path), "-frames:v", "1", str(p)])
        if p.exists():
            pngs.append((tt, p))
    if shutil.which("tesseract"):
        for tt, p in pngs:
            r = subprocess.run(["tesseract", str(p), "-", "--psm", "11", "tsv"], capture_output=True, text=True).stdout.splitlines()[1:]
            words = [l.split("\t")[-1] for l in r if len(l.split("\t")) == 12 and l.split("\t")[10].replace(".", "").lstrip("-").isdigit()
                     and float(l.split("\t")[10]) >= 70 and len(l.split("\t")[-1].strip()) >= 3 and any(ch.isalpha() for ch in l.split("\t")[-1])]
            if words:
                out.append({"t": round(tt, 2), "kind": "text", "score": len(words), "words": words[:5]})
    model = Path(__file__).resolve().parent / "models" / "yolov8x.pt"
    if model.exists():
        try:
            from ultralytics import YOLO
            y = YOLO(str(model)); counts = []
            for tt, p in pngs:
                res = y(str(p), verbose=False, classes=[0], conf=0.35)[0]
                counts.append((tt, len(res.boxes)))
            for (t0, c0), (t1, c1) in zip(counts, counts[1:]):
                if c1 > c0:
                    out.append({"t": round(t1, 2), "kind": "people", "score": c1 - c0})
            if counts and counts[0][1]:
                out.append({"t": round(counts[0][0], 2), "kind": "people", "score": counts[0][1]})
        except Exception as e:
            print("  people check skipped:", e, file=sys.stderr)
    return out


def clean_range(flags, dur, min_clean, pad=0.25):
    cuts = sorted({max(0.0, f["t"] - pad) for f in flags} | {min(dur, f["t"] + pad) for f in flags})
    bad = [(max(0.0, f["t"] - pad), min(dur, f["t"] + pad)) for f in flags]
    pts = sorted({0.0, dur, *cuts})
    best = (0, 0)
    for a in pts:
        for b in pts:
            if b - a > best[1] - best[0] and not any(x < b and y > a for x, y in bad):
                best = (a, b)
    return [round(best[0], 2), round(best[1], 2)] if best[1] - best[0] >= min_clean else None


def strip(path, res, out_png):
    from PIL import Image, ImageDraw
    dur = res["duration"]; n = 12
    tiles = []
    for i in range(n):
        tt = dur * (i + 0.5) / n
        p = Path(out_png).with_suffix(f".{i}.png")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{tt:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=240:-2", str(p)])
        im = Image.open(p).convert("RGB"); p.unlink()
        d = ImageDraw.Draw(im)
        hit = [f for f in res["flags"] if abs(f["t"] - tt) <= dur / n / 2]
        d.text((4, 4), f"{tt:.1f}s", fill="yellow")
        if hit:
            d.rectangle([0, 0, im.width - 1, im.height - 1], outline="red", width=4)
            d.text((4, 18), ",".join(sorted({h["kind"] for h in hit})), fill="red")
        tiles.append(im)
    sheet = Image.new("RGB", (tiles[0].width * 6, tiles[0].height * 2 + 20), "black")
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % 6) * im.width, (i // 6) * im.height))
    ImageDraw.Draw(sheet).text((4, tiles[0].height * 2 + 4), f"{Path(path).name}: {res['verdict']} clean {res['clean']}", fill="white")
    sheet.save(out_png, quality=85)


def run(path, out_dir, min_clean, strict=False):
    res = analyse(path)
    res["clean"] = clean_range(res["flags"], res["duration"], min_clean) if res["flags"] else [0.0, res["duration"]]
    res["verdict"] = "PASS" if not res["flags"] else ("REVIEW" if res["clean"] else "FAIL")
    if strict and res["flags"]:
        res["verdict"] = "FAIL" if not res["clean"] else "REVIEW"
    od = Path(out_dir) if out_dir else Path(path).parent
    od.mkdir(parents=True, exist_ok=True)
    (od / (Path(path).stem + ".qa.json")).write_text(json.dumps(res, indent=1))
    strip(path, res, od / (Path(path).stem + ".qa.jpg"))
    kinds = {}
    for f in res["flags"]:
        kinds.setdefault(f["kind"], []).append(f["t"])
    print(f"{res['verdict']:6s} {Path(path).name:28s} clean {res['clean']}  " + "  ".join(f"{k}@{v[0]}s(x{len(v)})" for k, v in kinds.items()))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+"); ap.add_argument("--out"); ap.add_argument("--min-clean", type=float, default=1.9)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    files = []
    for p in a.paths:
        p = Path(p)
        files += sorted(p.glob("*.mp4")) if p.is_dir() else [p]
    res = [run(f, a.out, a.min_clean, a.strict) for f in files]
    print(f"{sum(r['verdict'] == 'PASS' for r in res)} PASS, {sum(r['verdict'] == 'REVIEW' for r in res)} REVIEW, {sum(r['verdict'] == 'FAIL' for r in res)} FAIL")


if __name__ == "__main__":
    main()
