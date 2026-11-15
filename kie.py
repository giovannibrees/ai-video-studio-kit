# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""kie.ai client (images, video takes, music) with a free demo mode.

Keys come from .env (written by `python3 studio.py setup`), never from code or chat.
Demo mode (STUDIO_DEMO=1, or no KIE_API_KEY): nothing is sent anywhere and nothing is paid. Images become storyboard cards
drawn from the prompt, video takes become slow camera moves over the frame, music becomes a soft placeholder bed.
Use it to learn the flow, to test a script end to end, and to make screenshots.

Prices (kie.ai, Nov 2026, 200 credits = $1):
  nano-banana-pro image 2K      18 credits
  Seedance 2.5 480p draft       28 credits per second      720p 63 / s      1080p 158 / s
  Suno music (2 takes)          about 12 credits
"""
import json
import os
import subprocess
import textwrap
import time
import urllib.request
import uuid
from pathlib import Path

API = "https://api.kie.ai/api/v1"
UPLOAD = "https://kieai.redpandaai.co/api/file-stream-upload"
ROOT = Path(__file__).resolve().parent
RATE = {"480p": 28, "720p": 63, "1080p": 158}
IMAGE_CREDITS = 18


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


load_env()


def demo():
    return os.environ.get("STUDIO_DEMO") == "1" or not os.environ.get("KIE_API_KEY")


def _key():
    k = os.environ.get("KIE_API_KEY")
    if not k:
        raise RuntimeError("KIE_API_KEY missing: run `python3 studio.py setup`, or set STUDIO_DEMO=1 to try the free demo mode")
    return k


def http_json(url, payload=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def balance():
    if demo():
        return None
    try:
        r = http_json(f"{API}/chat/credit")
        return float(r["data"]) if r.get("code") == 200 else None
    except Exception:
        return None


def upload(path):
    if demo():
        return f"demo://{Path(path).name}"
    boundary = uuid.uuid4().hex
    body = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"uploadPath\"\r\n\r\nstudio\r\n".encode(),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{Path(path).name}\"\r\n"
        f"Content-Type: image/jpeg\r\n\r\n".encode(), Path(path).read_bytes(), f"\r\n--{boundary}--\r\n".encode()])
    req = urllib.request.Request(UPLOAD, data=body, headers={"Authorization": f"Bearer {_key()}",
                                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["data"]["downloadUrl"]


def jpeg(src, dst, edge=None):
    """Upload copy of an image. Full size, quality 95 for a start frame; edge=1600 for a style reference."""
    from PIL import Image
    im = Image.open(src).convert("RGB")
    if edge and max(im.size) > edge:
        im.thumbnail((edge, edge))
    im.save(dst, quality=95 if not edge else 85)
    return dst


def _poll(tid, out, tries=240, wait=8):
    for _ in range(tries):
        d = http_json(f"{API}/jobs/recordInfo?taskId={tid}").get("data") or {}
        if d.get("state") == "success":
            urllib.request.urlretrieve(json.loads(d["resultJson"])["resultUrls"][0], out)
            return True
        if d.get("state") == "fail":
            print("  task failed:", d.get("failMsg"), flush=True)
            return False
        time.sleep(wait)
    return False


# ---------- images ----------
def image(prompt, refs, out, label="", caption=None, inset=None):
    """One 16:9 2K image (Nano Banana Pro, Nano Banana 2 as fallback). Returns the model used or None."""
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if demo():
        _demo_card(prompt, out, label, caption, inset)
        return "demo"
    for model, tries in (("nano-banana-pro", 2), ("nano-banana-2", 2)):
        for _ in range(tries):
            r = http_json(f"{API}/jobs/createTask", {"model": model, "input": {"prompt": prompt, "image_input": list(refs),
                          "aspect_ratio": "16:9", "resolution": "2K", "output_format": "png"}})
            if r.get("code") == 200 and _poll(r["data"]["taskId"], out):
                return model
    return None


def _demo_card(prompt, out, label, caption=None, inset=None):
    """A storyboard card (demo mode): a dawn gradient, the shot number, what happens, and the blocking map when there is one."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import hashlib
    W, H = 1600, 900
    h = int(hashlib.md5((caption or prompt).encode()).hexdigest()[:6], 16)
    top = (34 + h % 30, 44 + (h >> 8) % 30, 86 + (h >> 16) % 50); bot = (214 + h % 30, 150 + (h >> 4) % 50, 104 + (h >> 12) % 40)
    grad = Image.linear_gradient("L").resize((W, H))
    im = Image.composite(Image.new("RGB", (W, H), bot), Image.new("RGB", (W, H), top), grad)
    glow = Image.new("L", (W, H), 0); ImageDraw.Draw(glow).ellipse([W * 0.55, H * 0.55, W * 1.15, H * 1.25], fill=150)
    im = Image.composite(Image.new("RGB", (W, H), (255, 214, 150)), im, glow.filter(ImageFilter.GaussianBlur(120)))
    d = ImageDraw.Draw(im)
    try:
        f0 = ImageFont.truetype("DejaVuSans-Bold.ttf", 120); f1 = ImageFont.truetype("DejaVuSans-Bold.ttf", 50)
        f2 = ImageFont.truetype("DejaVuSans.ttf", 34); f3 = ImageFont.truetype("DejaVuSans.ttf", 26)
    except OSError:
        f0 = f1 = f2 = f3 = ImageFont.load_default()
    d.rectangle([28, 28, W - 28, H - 28], outline=(255, 255, 255), width=2)
    d.text((70, 60), label or "", fill="white", font=f0)
    maxw = W - 140
    if inset and Path(inset).exists():
        m = Image.open(inset).convert("RGB"); m.thumbnail((620, 560))
        x0, y0 = W - m.width - 70, (H - m.height) // 2
        d.rectangle([x0 - 8, y0 - 8, x0 + m.width + 8, y0 + m.height + 8], fill=(255, 255, 255))
        im.paste(m, (x0, y0)); maxw = x0 - 120

    def wrap(text, font):
        out, cur = [], ""
        for w in text.split():
            if cur and d.textlength(cur + " " + w, font=font) > maxw:
                out.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        return out + ([cur] if cur else [])
    lines = (caption or prompt.split(" Same look as image 1")[0][:300]).split("\n")
    y = 230
    for i, ln in enumerate(lines):
        f = f1 if i == 0 else f2
        for part in wrap(ln, f)[:3]:
            d.text((70, y), part, fill="white" if i == 0 else (245, 238, 228), font=f); y += 62 if i == 0 else 46
        y += 14
    d.text((70, H - 80), "Demo storyboard card: no credits spent. A real run puts a 2K photoreal start frame here.", fill=(255, 255, 255), font=f3)
    im.save(out)


# ---------- video takes ----------
def take(first_frame, prompt, out, seconds=5, res="480p", draft=True, last_frame=None, draft_task_id=None):
    """One Seedance 2.5 take from a start frame. Returns the task id (keep it: the final re-render uses it)."""
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if demo():
        _demo_take(first_frame, out, seconds)
        return f"demo-{uuid.uuid4().hex[:8]}"
    inp = {"prompt": prompt, "first_frame_url": upload(jpeg(first_frame, out.with_suffix(".first.jpg"))), "generate_audio": False,
           "resolution": res, "aspect_ratio": "adaptive", "duration": seconds, "output_format": "mp4"}
    if last_frame:
        inp["last_frame_url"] = upload(jpeg(last_frame, out.with_suffix(".last.jpg")))
    if draft:
        inp["draft"] = True
    if draft_task_id:
        inp["draft_task_id"] = draft_task_id
    for _ in range(3):
        r = http_json(f"{API}/jobs/createTask", {"model": "bytedance/seedance-2-5", "input": inp})
        if r.get("code") != 200:
            print("  create rejected:", r.get("msg"), flush=True); time.sleep(15); continue
        if _poll(r["data"]["taskId"], out, tries=360, wait=10) and healthy(out):
            return r["data"]["taskId"]
    return None


def _demo_take(first_frame, out, seconds):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(first_frame), "-t", str(seconds), "-vf",
                    f"scale=1920:1080,zoompan=z='1+0.0008*on':x='iw/2-(iw/zoom/2)+on*0.6':y='ih/2-(ih/zoom/2)':d={seconds * 24}:s=1280x720:fps=24,format=yuv420p",
                    "-c:v", "libx264", "-crf", "23", str(out)], check=True)


def healthy(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return r.returncode == 0 and not r.stderr.strip() and d and float(d) >= 1.5


# ---------- music ----------
def music(style, title, out_dir, seconds=60):
    """Two instrumental takes from Suno (through kie.ai). Demo mode writes a soft placeholder bed."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    if demo():
        p = out_dir / "score_1.mp3"
        chord = [220.0, 277.2, 329.6, 440.0]          # a soft A major pad, slow tremolo
        ins = sum((["-f", "lavfi", "-i", f"sine=frequency={f}:duration={seconds}"] for f in chord), [])
        subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex",
                        f"amix=inputs={len(chord)},tremolo=f=0.25:d=0.5,volume=0.5,afade=t=in:d=2,afade=t=out:st={seconds - 3}:d=3",
                        str(p)], check=True)
        return [p]
    r = http_json(f"{API}/generate", {"prompt": "", "style": style, "title": title, "customMode": True, "instrumental": True,
                                       "model": "V4_5PLUS", "negativeTags": "vocals", "callBackUrl": "https://example.com/none"})
    tid = r["data"]["taskId"]
    for _ in range(120):
        d = http_json(f"{API}/generate/record-info?taskId={tid}").get("data") or {}
        tracks = ((d.get("response") or {}).get("sunoData")) or []
        if d.get("status") == "SUCCESS" and tracks:
            outs = []
            for i, t in enumerate(tracks, 1):
                p = out_dir / f"score_{i}.mp3"; urllib.request.urlretrieve(t["audioUrl"], p); outs.append(p)
            return outs
        if "FAIL" in str(d.get("status", "")):
            break
        time.sleep(10)
    return []
