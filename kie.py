# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""kie.ai client: start frames, video takes and music.

Keys come from .env (written by `python3 studio.py setup`), never from code or chat.

Prices (kie.ai, Nov 2026, 200 credits = $1):
  nano-banana-pro image 2K      18 credits
  Seedance 2.5 480p draft       28 credits per second      720p 63 / s      1080p 158 / s
  Suno music (2 takes)          about 12 credits
"""
import json
import os
import subprocess
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


def _key():
    k = os.environ.get("KIE_API_KEY")
    if not k:
        raise RuntimeError("KIE_API_KEY missing: run `python3 studio.py setup` first")
    return k


def http_json(url, payload=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def balance():
    if not os.environ.get("KIE_API_KEY"):
        return None
    try:
        r = http_json(f"{API}/chat/credit")
        return float(r["data"]) if r.get("code") == 200 else None
    except Exception:
        return None


def upload(path):
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
def image(prompt, refs, out, label=""):
    """One 16:9 2K image (Nano Banana Pro, Nano Banana 2 as fallback). Returns the model used or None."""
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    for model, tries in (("nano-banana-pro", 2), ("nano-banana-2", 2)):
        for _ in range(tries):
            r = http_json(f"{API}/jobs/createTask", {"model": model, "input": {"prompt": prompt, "image_input": list(refs),
                          "aspect_ratio": "16:9", "resolution": "2K", "output_format": "png"}})
            if r.get("code") == 200 and _poll(r["data"]["taskId"], out):
                return model
    return None


# ---------- video takes ----------
def take(first_frame, prompt, out, seconds=5, res="480p", draft=True, last_frame=None, draft_task_id=None):
    """One Seedance 2.5 take from a start frame. Returns the task id (keep it: the final re-render uses it)."""
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
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


def healthy(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return r.returncode == 0 and not r.stderr.strip() and d and float(d) >= 1.5


# ---------- music ----------
def music(style, title, out_dir, seconds=60):
    """Two instrumental takes from Suno (through kie.ai)."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
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
