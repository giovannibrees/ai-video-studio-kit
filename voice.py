# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Narration with ElevenLabs: voice samples first (the client picks by ear), then one file per script line.

Why line by line: each line lands on its own shot in the cut without alignment hacks, and a bad line is redone alone.
previous_text / next_text keep the flow natural across lines.
"""
import json
import os
import subprocess
import urllib.request
from pathlib import Path

import kie  # loads .env

API = "https://api.elevenlabs.io/v1"
# Calm storyteller voices from the public ElevenLabs library that suit brand films. The client always picks by ear.
SUGGESTED = {"Christopher (British, gentle)": "G17SuINrv2H9FC6nvetn", "Benedict (British, polished)": "oaGwHLz3csUaSnc2NBD4",
             "Kris (American)": "XR9GguuIygACVtitt58W"}
SETTINGS = {"stability": 0.5, "similarity_boost": 0.78, "style": 0.15, "use_speaker_boost": True, "speed": 0.95}


def tts(text, voice_id, out, prev="", nxt=""):
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("ELEVENLABS_API_KEY"):
        raise RuntimeError("ELEVENLABS_API_KEY missing: run `python3 studio.py setup` and add it")
    body = {"text": text, "model_id": "eleven_multilingual_v2", "previous_text": prev, "next_text": nxt, "voice_settings": SETTINGS}
    req = urllib.request.Request(f"{API}/text-to-speech/{voice_id}?output_format=mp3_44100_128", data=json.dumps(body).encode(),
                                 headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out.write_bytes(r.read())
    return out


def samples(project, text, voices=None):
    """A 10-second sample per candidate voice. The client listens and picks; labels like 'American' are not proof."""
    voices = voices or SUGGESTED
    out = []
    for name, vid in voices.items():
        p = Path(project) / "vo" / "samples" / f"{vid}.mp3"
        tts(text, vid, p)
        out.append({"name": name, "voice_id": vid, "file": str(p.relative_to(project))})
    (Path(project) / "vo" / "samples" / "samples.json").write_text(json.dumps(out, indent=1))
    return out


def lines(project, rows, voice_id, pronounce=None):
    """One file per row with narration, named after the row id. pronounce maps brand names to how they should be said
    (beats.json "pronounce": {"AcmeHQ": "Acme H Q"})."""
    vo = [(r["id"], r["vo"].strip()) for r in rows if (r.get("vo") or "").strip()]
    done = []
    for i, (rid, text) in enumerate(vo):
        spoken = text
        for k, v in (pronounce or {}).items():
            spoken = spoken.replace(k, v)
        p = tts(spoken, voice_id, Path(project) / "vo" / "lines" / f"{rid}.mp3",
                prev=vo[i - 1][1] if i else "", nxt=vo[i + 1][1] if i + 1 < len(vo) else "")
        done.append(p)
        print(f"  {rid}: {text}")
    return done


def duration(path):
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(d or 0)
