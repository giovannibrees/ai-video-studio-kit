# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""The cut: approved takes in row order, trimmed to the row length, narration on each row, the score ducked under the
voice, an end card, letterbox, and the look grade once over the whole timeline.

Called by `studio.py cut` (draft film from the 480p takes) and `studio.py final` (same edit from the finals).
"""
import json
import subprocess
from pathlib import Path

W, H, FPS = 1280, 720, 24
FONT_BOLD = "DejaVuSans-Bold.ttf"
FONT = "DejaVuSans.ttf"


def _font(name, size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


def card(png, title, line):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), (12, 12, 14)); d = ImageDraw.Draw(im)
    f1, f2 = _font(FONT_BOLD, 64), _font(FONT, 30)
    for txt, f, y in ((title, f1, H / 2 - 50), (line, f2, H / 2 + 40)):
        d.text(((W - d.textlength(txt, font=f)) / 2, y), txt, fill=(240, 236, 228), font=f)
    im.save(png)


def run(args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def build(p, b, ap, final=False):
    p = Path(p); work = p / "cut" / ("final_parts" if final else "draft_parts"); work.mkdir(parents=True, exist_ok=True)
    parts, t, vo = [], 0.0, []
    for i, r in enumerate(b["rows"]):
        secs = float(r["seconds"]); out = work / f"{i:02d}_{r['id']}.mp4"
        if r.get("card"):
            png = work / f"{r['id']}.png"; card(png, r["card"].get("title", ""), r["card"].get("line", ""))
            run(["-loop", "1", "-i", str(png), "-t", f"{secs}", "-vf", f"fps={FPS},fade=t=in:d=0.6,format=yuv420p", "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "18", str(out)])
        else:
            c = ap["clips"][r["id"]]; take = c["take"]
            src = p / "finals" / f"{r['id']}.mp4" if final else p / "takes" / r["id"] / f"{take}.mp4"
            meta = json.loads((p / "takes" / r["id"] / "takes.json").read_text()).get(take, {})
            start = float((c.get("clean") or meta.get("clean") or [0])[0])
            run(["-ss", f"{start:.2f}", "-i", str(src), "-t", f"{secs}", "-an", "-vf",
                 f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},tpad=stop_mode=clone:stop_duration=3,trim=duration={secs},format=yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", str(out)])
        parts.append(out)
        line = p / "vo" / "lines" / f"{r['id']}.mp3"
        if line.exists():
            vo.append((line, t + 0.3))
        t += secs
    lst = work / "list.txt"; lst.write_text("".join(f"file '{x.resolve()}'\n" for x in parts))
    picture = work / "picture.mp4"
    vf = []
    if b.get("aspect") == "2.39:1":
        band = int(W / 2.39) // 2 * 2
        vf.append(f"crop={W}:{band},pad={W}:{H}:0:{(H - band) // 2}:black")
    if (p / "look.json").exists():
        import look
        vf.append(look.grade_filter(look.load(p).get("grade", {})))
    run(["-f", "concat", "-safe", "0", "-i", str(lst), *(["-vf", ",".join(vf)] if vf else []), "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "18", "-pix_fmt", "yuv420p", str(picture)])
    # audio: narration lines at their row starts, score ducked under the voice
    music = sorted((p / "music").glob("score_*.mp3"))
    pick = ap.get("music", {}).get("file")
    music = [p / pick] if pick and (p / pick).exists() else music[:1]
    ins, fl = ["-i", str(picture)], []
    for k, (f, at) in enumerate(vo):
        ins += ["-i", str(f)]; fl.append(f"[{k + 1}:a]adelay={int(at * 1000)}:all=1,aresample=44100[v{k}]")
    out = p / "cut" / ("final.mp4" if final else "draft.mp4")
    if vo:
        fl.append("".join(f"[v{k}]" for k in range(len(vo))) + f"amix=inputs={len(vo)}:normalize=0,apad=whole_dur={t}[voice]")
    if music:
        ins += ["-i", str(music[0])]; m = len(vo) + 1
        fl.append(f"[{m}:a]atrim=0:{t},volume=0.55,afade=t=in:d=1.5,afade=t=out:st={max(0, t - 3)}:d=3,aresample=44100[mus]")
        if vo:
            fl.append("[voice]asplit[vk][vm]")
            fl.append("[mus][vk]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]")
            fl.append("[duck][vm]amix=inputs=2:normalize=0[mix]")
        else:
            fl.append("[mus]anull[mix]")
    elif vo:
        fl.append("[voice]anull[mix]")
    if fl:
        fl.append(f"[mix]loudnorm=I=-16:TP=-1.5:LRA=11,atrim=0:{t}[a]")
        run([*ins, "-filter_complex", ";".join(fl), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(out)])
    else:
        run(["-i", str(picture), "-c", "copy", "-movflags", "+faststart", str(out)])
    return out
