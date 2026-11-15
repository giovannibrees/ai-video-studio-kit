#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Song map: beats, bars, energy, sections, drops and a cut-density plan, measured from the audio (an agent cannot hear).

Why: a cut that uses one fixed pattern per section (every 2 bars, every bar, every half bar) chosen by reading a bar
table never quite matches the music. Cut density must follow what the music actually does:
how loud, how busy (onsets per second), where it drops, where it breathes.

Usage:
  python3 audiomap.py <song.mp3> <out.json> [--png chart.png] [--bars-per-phrase 8]
Output (audiomap.json):
  tempo, rhythm (0 to 1, how clearly the track pulses; below 0.35 do not trust the beat grid, cut on phrases)
  beats[], downbeats[], bars[{i, t0, t1, energy 0..1, density onsets/s, low 0..1}]
  sections[{i, bar0, bar1, t0, t1, energy, density, level low|mid|high, kind intro|build|drop|break|groove|outro,
            pacing beat_cut|phrase_flow|hold, cut_bars, cuts}]
  key_moments[{t, bar, kind drop|surge|break|hit}]   hero shots land here
  plan {cuts, mean_cut_s}                            the number plan_check.py starts from
Pacing rules (our own, measured on house and pop edits):
  hold         a break of at most 2 bars right before a drop: one held shot
  phrase_flow  low energy, or a track whose pulse is weak: cut every 4 bars (or every phrase)
  beat_cut     mid energy: cut every 2 bars; high energy: every bar; high energy and dense (top 15% onsets): every half bar
  never        a cut group shorter than half a bar, or a section boundary inside a cut
"""
import argparse
import json
import sys

import numpy as np


def analyse(path, bars_per_phrase=8):
    import librosa
    y, sr = librosa.load(path, sr=22050, mono=True)
    dur = len(y) / sr
    hop = 512
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop, tightness=120)
    tempo = float(np.atleast_1d(tempo)[0])
    beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
    if len(beats) < 16:
        sys.exit("fewer than 16 beats found: not a rhythmic track; edit by phrases by hand")
    # rhythm (pulse clarity): autocorrelation of the onset envelope at one beat period, 0 to 1
    ac = librosa.autocorrelate(oenv - oenv.mean())
    lag = int(round(60 / tempo * sr / hop))
    rhythm = float(np.clip(ac[lag] / (ac[0] + 1e-9), 0, 1)) if lag < len(ac) else 0.0
    # downbeat phase: the beat position (0..3) with the strongest low-band (kick) onsets
    S = np.abs(librosa.stft(y, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr)
    low_env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[freqs < 150]), sr=sr, hop_length=hop)
    phase = int(np.argmax([low_env[beat_frames[k::4]].mean() for k in range(4)]))
    downbeats = beats[phase::4]
    edges = list(downbeats)
    bar = 240 / tempo
    while edges[0] > 0.5:                      # beatless intro (bells, pads): extend the bar grid back to 0:00
        edges.insert(0, max(0.0, edges[0] - bar))
    edges.append(dur)
    # per-bar features
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    rt = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    onsets = librosa.onset.onset_detect(onset_envelope=oenv, sr=sr, hop_length=hop, units="time")
    lowband = S[freqs < 150].mean(axis=0)
    bars = []
    for i in range(len(edges) - 1):
        t0, t1 = edges[i], edges[i + 1]
        if t1 - t0 < 0.5:
            continue
        m = (rt >= t0) & (rt < t1)
        bars.append({"i": len(bars), "t0": round(float(t0), 3), "t1": round(float(t1), 3),
                     "rms": float(rms[m].mean()) if m.any() else 0.0,
                     "density": round(float(((onsets >= t0) & (onsets < t1)).sum() / (t1 - t0)), 2),
                     "lowraw": float(lowband[m[:len(lowband)]].mean()) if m.any() else 0.0})
    db = 20 * np.log10(np.array([b["rms"] for b in bars]) + 1e-6)
    lo, hi = np.percentile(db, 5), np.percentile(db, 95)
    lr = np.array([b["lowraw"] for b in bars]); lr = lr / (np.percentile(lr, 95) + 1e-9)
    for b, e, l in zip(bars, db, lr):
        b["energy"] = round(float(np.clip((e - lo) / (hi - lo + 1e-9), 0, 1)), 3)
        b["low"] = round(float(np.clip(l, 0, 1)), 3)
        del b["rms"], b["lowraw"]
    sections = segment(bars, bars_per_phrase)
    dens_hi = np.percentile([b["density"] for b in bars], 85)
    moments = []
    for s in sections:
        label(s, sections, rhythm, dens_hi)
        if s["kind"] in ("drop", "break"):
            moments.append({"t": s["t0"], "bar": s["bar0"], "kind": s["kind"]})
    # surges: single bars where energy jumps sharply inside a section (fills, hits)
    en = np.array([b["energy"] for b in bars])
    for i in range(1, len(en)):
        if en[i] - en[i - 1] > 0.25 and not any(m["bar"] == i for m in moments):
            moments.append({"t": bars[i]["t0"], "bar": i, "kind": "surge"})
    moments.sort(key=lambda m: m["t"])
    cuts = sum(s["cuts"] for s in sections)
    return {"file": str(path), "duration": round(dur, 3), "tempo": round(tempo, 2), "rhythm": round(rhythm, 3),
            "trust_beats": rhythm >= 0.35, "bar_s": round(240 / tempo, 4), "beats": [round(float(b), 3) for b in beats],
            "downbeats": [round(float(b), 3) for b in downbeats], "bars": bars, "sections": sections, "key_moments": moments,
            "plan": {"cuts": cuts, "mean_cut_s": round(dur / max(cuts, 1), 2)}}


def segment(bars, bpp):
    """Split the bar list into sections at 4-bar boundaries where energy, low end or density change most."""
    n = len(bars)
    f = np.array([[b["energy"], b["low"], b["density"] / 10] for b in bars])
    blocks = list(range(0, n, 4))
    score = []
    for b0 in blocks[1:]:
        a, c = f[max(0, b0 - 4):b0].mean(0), f[b0:min(n, b0 + 4)].mean(0)
        score.append((float(np.abs(a - c).sum()), b0))
    thr = max(0.18, np.percentile([s for s, _ in score], 60)) if score else 1
    cuts = sorted(b0 for s, b0 in score if s >= thr)
    # keep sections at least 8 bars unless the change is big (a 4-bar break before a drop is real)
    keep, last = [], 0
    for b0 in cuts:
        s = dict((b, v) for v, b in score)[b0]
        if b0 - last >= 8 or s >= 1.5 * thr:
            keep.append(b0); last = b0
    edges = [0] + keep + [n]
    out = []
    for i in range(len(edges) - 1):
        b0, b1 = edges[i], edges[i + 1]
        if b1 <= b0:
            continue
        seg = f[b0:b1]
        out.append({"i": len(out), "bar0": b0, "bar1": b1, "t0": bars[b0]["t0"], "t1": bars[b1 - 1]["t1"],
                    "energy": round(float(seg[:, 0].mean()), 3), "low": round(float(seg[:, 1].mean()), 3),
                    "density": round(float(seg[:, 2].mean() * 10), 2)})
    return out


def label(s, secs, rhythm, dens_hi):
    i, n = s["i"], len(secs)
    prev = secs[i - 1] if i else None
    nxt = secs[i + 1] if i + 1 < n else None
    e = s["energy"]
    s["level"] = "high" if e >= 0.66 else "mid" if e >= 0.4 else "low"
    if i == 0 and e < 0.6:
        kind = "intro"
    elif i == n - 1 and e < 0.6:
        kind = "outro"
    elif prev and e - prev["energy"] >= 0.2 and s["low"] >= 0.5:
        kind = "drop"
    elif nxt and prev and e < prev["energy"] - 0.2 and e < nxt["energy"] - 0.2:
        kind = "break"
    elif nxt and nxt["energy"] - e >= 0.15:
        kind = "build"
    else:
        kind = "groove"
    s["kind"] = kind
    bars = s["bar1"] - s["bar0"]
    if kind == "break" and bars <= 2 and nxt and nxt["energy"] > e:
        pacing, cb = "hold", float(bars)
    elif not rhythm >= 0.35 or s["level"] == "low" or kind in ("intro", "outro"):
        pacing, cb = "phrase_flow", 4.0
    elif s["level"] == "mid" or kind == "build":
        pacing, cb = "beat_cut", 2.0
    elif s["density"] >= dens_hi:
        pacing, cb = "beat_cut", 0.5
    else:
        pacing, cb = "beat_cut", 1.0
    cb = min(cb, float(bars))
    s["pacing"], s["cut_bars"] = pacing, cb
    s["cuts"] = int(np.ceil(bars / cb))


def chart(m, png):
    from PIL import Image, ImageDraw
    W, H = 1600, 300
    im = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(im)
    T = m["duration"]
    x = lambda t: int(t / T * (W - 20)) + 10
    col = {"intro": (180, 200, 230), "groove": (200, 220, 200), "build": (250, 220, 150), "drop": (240, 150, 150),
           "break": (200, 200, 200), "outro": (180, 200, 230)}
    for s in m["sections"]:
        d.rectangle([x(s["t0"]), 20, x(s["t1"]), H - 40], fill=col[s["kind"]])
        d.text((x(s["t0"]) + 3, 24), f'{s["kind"]} {s["pacing"]} {s["cut_bars"]:g}b x{s["cuts"]}', fill="black")
    pts = [(x((b["t0"] + b["t1"]) / 2), H - 40 - int(b["energy"] * (H - 80))) for b in m["bars"]]
    d.line(pts, fill="black", width=2)
    for k in m["key_moments"]:
        d.line([x(k["t"]), 20, x(k["t"]), H - 40], fill=(200, 0, 0) if k["kind"] == "drop" else (120, 120, 120), width=1)
    for t in range(0, int(T) + 1, 15):
        d.text((x(t), H - 30), f"{t // 60}:{t % 60:02d}", fill="black")
    d.text((10, H - 15), f'tempo {m["tempo"]} bpm, rhythm {m["rhythm"]}, {len(m["bars"])} bars, {m["plan"]["cuts"]} cuts, mean {m["plan"]["mean_cut_s"]} s', fill="black")
    im.save(png)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song"); ap.add_argument("out"); ap.add_argument("--png"); ap.add_argument("--bars-per-phrase", type=int, default=8)
    a = ap.parse_args()
    m = analyse(a.song, a.bars_per_phrase)
    json.dump(m, open(a.out, "w"), indent=1)
    if a.png:
        chart(m, a.png)
    print(f'tempo {m["tempo"]} bpm, rhythm {m["rhythm"]} ({"beat grid trusted" if m["trust_beats"] else "weak pulse: cut on phrases"}), {len(m["bars"])} bars, {m["duration"]} s')
    for s in m["sections"]:
        print(f'  {s["t0"]:7.2f}-{s["t1"]:7.2f}s bars {s["bar0"]:3d}-{s["bar1"]:3d} {s["kind"]:6s} {s["level"]:4s} energy {s["energy"]:.2f} '
              f'density {s["density"]:4.1f}/s  {s["pacing"]:11s} every {s["cut_bars"]:g} bar(s) -> {s["cuts"]} cuts')
    print(f'key moments: ' + ", ".join(f'{k["kind"]}@{k["t"]:.1f}s' for k in m["key_moments"][:20]))
    print(f'plan: {m["plan"]["cuts"]} cuts, mean {m["plan"]["mean_cut_s"]} s per cut')


if __name__ == "__main__":
    main()
