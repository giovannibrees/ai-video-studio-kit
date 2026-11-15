#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""AI Video Studio: one command line for the whole film, from script to final cut, with a person approving every step.

  python3 studio.py setup                       ask for your name and API keys, write .env, test the keys
  python3 studio.py new <project> [--format brand_film|ugc_ad|music_video] [--from examples/first-pour]
  python3 studio.py check <project>             script check (fields, narration speed, banned words, the promise)
  python3 studio.py blocking <project>          top-view drawings of who stands and walks where, per shot
  python3 studio.py review <project>            open the review page: approve the script, blocking, frames, clips, voice, cut
  python3 studio.py frames <project>            start frames for every row (needs the approved script and blocking)
  python3 studio.py takes <project> [--n 4]     480p draft takes from every approved frame, each one checked by clip_qa
  python3 studio.py voices <project>            a short sample of each candidate narrator, to pick by ear
  python3 studio.py vo <project>                the narration, one file per row (needs the chosen voice)
  python3 studio.py music <project>             two instrumental score takes
  python3 studio.py cut <project>               the draft film from the approved takes, narration and score
  python3 studio.py final <project>             the approved takes again at 720p (needs the approved draft film), then the final cut
  python3 studio.py status <project>            where the project stands and what waits on whom
Every paid step checks the cost against your balance first, generates one sample before a batch, and stops an item
after three rounds.
"""
import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import kie  # noqa: E402  (loads .env)
import ledger  # noqa: E402
import prompt_lint  # noqa: E402

PROJECTS = ROOT / "projects"
TEMPLATES = {"brand_film": "beats_brand_film.json", "ugc_ad": "beats_ugc_ad.json", "music_video": "beats_music_video.json"}
NO_TEXT = "No readable text, letters, numbers or logos anywhere; plain, unmarked surfaces."
STEPS = ["script", "blocking", "frames", "clips", "voice", "cut", "final"]


# ---------- helpers ----------
def pdir(name):
    p = Path(name)
    if not p.exists() and (PROJECTS / name).exists():
        p = PROJECTS / name
    if not (p / "beats.json").exists():
        sys.exit(f"no beats.json in {p}: run `python3 studio.py new {name}` first")
    return p


def beats(p):
    return json.loads((p / "beats.json").read_text())


def approvals(p):
    f = p / "approvals.json"
    return json.loads(f.read_text()) if f.exists() else {}


def save_approvals(p, a):
    (p / "approvals.json").write_text(json.dumps(a, indent=1))


def approved(a, stage, item=None):
    x = a.get(stage, {})
    if item is not None:
        x = x.get(item, {})
    return x.get("status") == "approved"


def shot_rows(b):
    """Rows that need generated picture (end cards are drawn in the cut)."""
    return [r for r in b["rows"] if not r.get("card")]


def characters_in(b, r):
    """Named characters visible in the shot: named in the row, or standing in its blocking (unless off frame)."""
    text = " ".join(str(r.get(k, "")) for k in ("subject", "subject_motion", "scene"))
    staged = {x["name"] for x in (r.get("blocking") or {}).get("people", []) if not x.get("offscreen")}
    off = {x["name"] for x in (r.get("blocking") or {}).get("people", []) if x.get("offscreen")}
    return [c for c in b.get("looks", {}) if (c in text or c in staged) and c not in off]


def cap(t):
    return t[:1].upper() + t[1:]


def user():
    return os.environ.get("STUDIO_USER") or "you"


def need(cond, msg):
    if not cond:
        sys.exit(f"WAITING: {msg}\n  Open the review page: python3 studio.py review <project>")


def money(credits):
    return f"{credits:.0f} credits (${credits / 200:.2f})"


# ---------- setup ----------
def cmd_setup(a):
    env = ROOT / ".env"
    cur = {}
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cur[k.strip()] = v.strip().strip('"')
    print("AI Video Studio setup. Keys are stored only in .env on this computer (never in git).\n")

    def ask(key, label, secret=False, required=False, help_=""):
        if help_:
            print(f"  {help_}")
        shown = " [keep current]" if cur.get(key) else (" [optional, Enter to skip]" if not required else "")
        while True:
            v = (getpass.getpass if secret else input)(f"{label}{shown}: ").strip()
            if v:
                return v
            if cur.get(key):
                return cur[key]
            if not required:
                return ""
            print("  This one is required.")

    vals = {
        "STUDIO_USER": ask("STUDIO_USER", "Your name (shown on review pages and approvals)", required=True),
        "STUDIO_BRAND": ask("STUDIO_BRAND", "Your studio or company name"),
        "KIE_API_KEY": ask("KIE_API_KEY", "kie.ai API key", secret=True, required=True,
                           help_="Images, video and music run on kie.ai. Get a key at https://kie.ai/api-key (top up a few dollars first)."),
        "ELEVENLABS_API_KEY": ask("ELEVENLABS_API_KEY", "ElevenLabs API key", secret=True,
                                  help_="Narration runs on ElevenLabs (https://elevenlabs.io/app/settings/api-keys). "
                                        "Permissions needed: Text to Speech, Voices read. Skip it if your films have no voice."),
    }
    env.write_text("# AI Video Studio keys. Never commit or share this file.\n" +
                   "".join(f'{k}="{v}"\n' for k, v in vals.items() if v))
    os.chmod(env, 0o600)
    for k, v in vals.items():
        if v:
            os.environ[k] = v
    print(f"\nSaved {env} (readable only by you).")
    b = kie.balance()
    print(f"  kie.ai: {'OK, balance ' + money(b) if b is not None else 'could not read the balance: check the key'}")
    if vals["ELEVENLABS_API_KEY"]:
        import urllib.request
        try:
            req = urllib.request.Request("https://api.elevenlabs.io/v1/voices?page_size=1", headers={"xi-api-key": vals["ELEVENLABS_API_KEY"]})
            urllib.request.urlopen(req, timeout=20).read()
            print("  ElevenLabs: OK")
        except Exception as e:
            print(f"  ElevenLabs: key rejected or no 'Voices read' permission ({e})")
    print(f"\nWelcome, {vals['STUDIO_USER']}. Next: python3 studio.py new my-first-film --from examples/first-pour")


# ---------- new / check / blocking ----------
def cmd_new(a):
    dst = PROJECTS / a.project
    if dst.exists():
        sys.exit(f"{dst} exists")
    dst.mkdir(parents=True)
    if a.src:
        src = Path(a.src)
        shutil.copy(src / "beats.json", dst / "beats.json")
        for extra in ("look.json",):
            if (src / extra).exists():
                shutil.copy(src / extra, dst / extra)
    else:
        shutil.copy(ROOT / "templates" / TEMPLATES[a.format], dst / "beats.json")
    print(f"created {dst}\n  1. write the script in {dst / 'beats.json'} (guide: docs/SCRIPTS.md)\n"
          f"  2. python3 studio.py check {a.project}\n  3. python3 studio.py review {a.project}")


def cmd_check(a):
    p = pdir(a.project)
    r = subprocess.run([sys.executable, str(ROOT / "script_check.py"), str(p / "beats.json"), "--gate"], capture_output=True, text=True)
    out = r.stdout
    b = beats(p)
    lint = []
    for row in shot_rows(b):
        for kind, prompt in (("image", frame_prompt(b, row)), ("video", motion_prompt(b, row))):
            for lvl, rule, d in prompt_lint.lint(prompt, kind, allow=row.get("lint_allow", ()), characters=bool(characters_in(b, row))):
                lint.append(f"{'FAIL' if lvl == 'FAIL' else 'warn'} {row['id']} {kind} prompt, {rule}: {d}")
    out += "\n".join(lint) + ("\n" if lint else "")
    (p / "script_check.txt").write_text(out)
    print(out, end="")
    sys.exit(1 if r.returncode or any(l.startswith("FAIL") for l in lint) else 0)


def cmd_blocking(a):
    p = pdir(a.project)
    sys.exit(subprocess.run([sys.executable, str(ROOT / "blocking.py"), str(p)]).returncode)


# ---------- prompts ----------
def look_parts(p, b, row):
    lp = p / "look.json"
    if lp.exists():
        import look
        cfg = look.load(p)
        pre, tail = look.prefix(cfg, row.get("light", ""))
        if characters_in(b, row) or cfg.get("people", "none") != "none":
            tail = tail.replace(look.NO_PEOPLE, "")
        h = look.hero(cfg, row.get("light", ""))
        return pre, tail, ([p / h] if h and (p / h).exists() else [])
    return b.get("style", "Cinematic film still, photoreal, soft natural light, 16:9."), NO_TEXT, []


def frame_prompt(b, row, p=None, note=""):
    pre, tail, _ = look_parts(p, b, row) if p else (b.get("style", ""), NO_TEXT, [])
    chars = characters_in(b, row)
    who = " ".join(f"{c} is {b['looks'][c]}." for c in chars)
    num = ["no", "one", "two", "three", "four"][min(len(chars), 4)]
    people = (f"Exactly {num} {'person' if len(chars) == 1 else 'people'} in the frame, nobody else."
              if chars else "No people anywhere in the frame.")
    import blocking
    parts = [pre, f"The first frame of a shot. {cap(row['subject'])} {row['subject_motion']}: show the moment just before it starts.",
             f"Scene: {row['scene']}.", f"Framing: {row['framing']}.", who, people, blocking.describe(row),
             f"Props appear once each: {', '.join(row['props'])}." if row.get("props") else "", tail]
    if note:
        parts.append(f"Change from the last version: {note}.")
    return " ".join(x for x in parts if x)


def motion_prompt(b, row):
    chars = characters_in(b, row)
    parts = [f"{cap(row['subject'])} {row['subject_motion']}.", f"Camera: {row['camera']}.", f"{cap(row['scene'])}.",
             "Only one person moves." if len(chars) == 1 else (f"Only {row['subject']} moves; everyone else stays in place. Nobody else enters the frame."
                                                               if chars else "No people appear."),
             "Nothing appears, vanishes or changes shape. Natural, steady motion, real physics.", NO_TEXT]
    return " ".join(parts)


# ---------- frames ----------
def cmd_frames(a):
    p = pdir(a.project); b = beats(p); ap = approvals(p)
    need(approved(ap, "script"), "the script is not approved yet")
    if any(r.get("blocking") for r in b["rows"]):
        need(approved(ap, "blocking"), "the blocking drawings are not approved yet")
    todo = []
    for r in shot_rows(b):
        st = ap.get("frames", {}).get(r["id"], {})
        exists = (p / "frames" / f"{r['id']}.png").exists()
        if a.only and r["id"] not in a.only:
            continue
        if st.get("status") == "approved":
            continue
        if exists and st.get("status") != "changes" and not a.only:
            continue                                   # waiting for review, do not pay twice
        todo.append((r, st.get("note", "") if st.get("status") == "changes" else ""))
    if not todo:
        print("nothing to generate: every frame is approved or waiting for review"); return
    sampled = any(e.get("stage") == "frames" for e in ledger.entries(p, "sample_ok"))
    if len(todo) > ledger.SAMPLE_BATCH and not sampled:
        print(f"{len(todo)} frames to make. Making ONE sample first; approve it on the review page and run frames again.")
        todo = todo[:1]
    est = len(todo) * kie.IMAGE_CREDITS
    print(f"frames: {len(todo)} x {kie.IMAGE_CREDITS} = {money(est)}")
    try:
        b0 = ledger.gate(p, "frames", len(todo), est, items=[r["id"] for r, _ in todo])
    except ledger.LedgerError as e:
        sys.exit(f"STOPPED: {e}")
    for r, note in todo:
        prompt = frame_prompt(b, r, p, note)
        try:
            prompt_lint.gate(prompt, "image", allow=r.get("lint_allow", ()), characters=bool(characters_in(b, r)), label=r["id"])
        except prompt_lint.PromptError as e:
            print(f"  {r['id']}: BLOCKED by prompt check: {e}"); continue
        _, _, refs = look_parts(p, b, r)
        # character consistency: the first approved frame of each character goes in as a reference
        for c in characters_in(b, r):
            for rr in shot_rows(b):
                if rr["id"] != r["id"] and c in characters_in(b, rr) and approved(ap, "frames", rr["id"]):
                    refs.append(p / "frames" / f"{rr['id']}.png"); break
        urls = [kie.upload(kie.jpeg(x, p / "frames" / f"ref_{Path(x).stem}.jpg", 1600)) for x in refs]
        out = p / "frames" / f"{r['id']}.png"
        if out.exists():
            v = len(list((p / "frames" / "old").glob(f"{r['id']}_v*.png"))) + 1
            (p / "frames" / "old").mkdir(parents=True, exist_ok=True); out.rename(p / "frames" / "old" / f"{r['id']}_v{v}.png")
        model = kie.image(prompt, urls, out, label=r["id"])
        (p / "frames" / f"{r['id']}.json").write_text(json.dumps({"prompt": prompt, "model": model, "refs": [str(x) for x in refs],
                                                                   "t": time.strftime("%Y-%m-%d %H:%M")}, indent=1))
        ap.setdefault("frames", {})[r["id"]] = {"status": "review"}
        print(f"  {r['id']}: {'done' if model else 'FAILED'}")
    save_approvals(p, ap)
    ledger.spend(p, "frames", b0, kie.balance(), model="nano-banana-pro", items=len(todo))
    print("Next: approve or send back each frame on the review page.")


# ---------- takes ----------
def cmd_takes(a):
    import clip_qa
    p = pdir(a.project); b = beats(p); ap = approvals(p)
    rows = [r for r in shot_rows(b) if approved(ap, "frames", r["id"]) and (not a.only or r["id"] in a.only)]
    rows = [r for r in rows if not approved(ap, "clips", r["id"]) and
            (a.only or not list((p / "takes" / r["id"]).glob("t*.mp4")) or ap.get("clips", {}).get(r["id"], {}).get("status") == "changes")]
    need(rows or any(approved(ap, "frames", r["id"]) for r in shot_rows(b)), "no approved frames yet")
    if not rows:
        print("nothing to render: every approved frame has takes waiting for review"); return
    sampled = any(e.get("stage") == "takes" for e in ledger.entries(p, "sample_ok"))
    if len(rows) > ledger.SAMPLE_BATCH and not sampled:
        print(f"{len(rows)} shots to render. Rendering ONE shot first; approve a take on the review page and run takes again.")
        rows = rows[:1]
    secs = sum(min(12, max(4, round(r["seconds"] + 1))) for r in rows)
    est = secs * a.n * kie.RATE["480p"]
    print(f"takes: {len(rows)} shots x {a.n} takes, {secs} s each round = {money(est)}")
    try:
        b0 = ledger.gate(p, "takes", len(rows), est, items=[r["id"] for r in rows])
    except ledger.LedgerError as e:
        sys.exit(f"STOPPED: {e}")
    for r in rows:
        prompt = motion_prompt(b, r)
        note = ap.get("clips", {}).get(r["id"], {}).get("note") if ap.get("clips", {}).get(r["id"], {}).get("status") == "changes" else ""
        if note:
            prompt += f" {note}."
        try:
            prompt_lint.gate(prompt, "video", allow=r.get("lint_allow", ()), characters=bool(characters_in(b, r)), label=r["id"])
        except prompt_lint.PromptError as e:
            print(f"  {r['id']}: BLOCKED by prompt check: {e}"); continue
        d = p / "takes" / r["id"]; d.mkdir(parents=True, exist_ok=True)
        meta = json.loads((d / "takes.json").read_text()) if (d / "takes.json").exists() else {}
        start = len(meta)
        dur = min(12, max(4, round(r["seconds"] + 1)))
        for i in range(start + 1, start + a.n + 1):
            out = d / f"t{i}.mp4"
            tid = kie.take(p / "frames" / f"{r['id']}.png", prompt, out, seconds=dur, res="480p", draft=True)
            if not tid:
                print(f"  {r['id']} t{i}: FAILED"); continue
            qa = clip_qa.run(out, d, min_clean=min(r["seconds"], dur - 0.5))
            meta[f"t{i}"] = {"task_id": tid, "prompt": prompt, "qa": qa["verdict"], "clean": qa["clean"]}
            ledger.outcome(p, "seedance-2-5", f"{r['id']}_t{i}", qa["verdict"] != "FAIL", why=qa["verdict"])
        (d / "takes.json").write_text(json.dumps(meta, indent=1))
        ap.setdefault("clips", {})[r["id"]] = {"status": "review"}
    save_approvals(p, ap)
    ledger.spend(p, "takes", b0, kie.balance(), model="seedance-2-5", items=len(rows) * a.n)
    print("Next: watch every take at full speed on the review page, pick one per shot (docs/REVIEW_CHECKLIST.md).")


# ---------- voice ----------
def cmd_voices(a):
    import voice
    p = pdir(a.project); b = beats(p)
    need(approved(approvals(p), "script"), "the script is not approved yet")
    lines = [r["vo"] for r in b["rows"] if r.get("vo")]
    text = " ".join(lines[:2]) or "This is a short sample of how this narrator sounds."
    for k, v in b.get("pronounce", {}).items():
        text = text.replace(k, v)
    voices = b.get("voices") or voice.SUGGESTED
    out = voice.samples(p, text, voices)
    print(f"{len(out)} samples in {p / 'vo' / 'samples'}. Listen on the review page and pick one by ear.")


def cmd_vo(a):
    import voice
    p = pdir(a.project); b = beats(p); ap = approvals(p)
    need(approved(ap, "voice"), "no narrator chosen yet")
    voice.lines(p, b["rows"], ap["voice"]["voice_id"], b.get("pronounce"))
    print(f"narration in {p / 'vo' / 'lines'}")


def cmd_music(a):
    p = pdir(a.project); b = beats(p)
    m = b.get("music", {})
    total = sum(r["seconds"] for r in b["rows"])
    out = kie.music(m.get("style", "warm cinematic piano and soft strings, hopeful, gentle build"), m.get("title", b.get("title", "Score")),
                    p / "music", seconds=int(total) + 4)
    print("\n".join(f"  {x}" for x in out) or "music failed")


# ---------- cut ----------
def cmd_cut(a, final=False):
    import cut
    p = pdir(a.project); b = beats(p); ap = approvals(p)
    missing = [r["id"] for r in shot_rows(b) if not approved(ap, "clips", r["id"])]
    need(not missing, f"clips not approved yet: {', '.join(missing)}")
    narrated = [r["id"] for r in b["rows"] if (r.get("vo") or "").strip()]
    if narrated:
        need(approved(ap, "voice"), "the script has narration but no narrator is chosen (studio.py voices, then pick one)")
        lacking = [i for i in narrated if not (p / "vo" / "lines" / f"{i}.mp3").exists()]
        if lacking:
            sys.exit(f"narration missing for {', '.join(lacking)}: run `python3 studio.py vo {a.project}` first")
    if not list((p / "music").glob("score_*.mp3")):
        print("note: no score yet (studio.py music); cutting without music")
    out = cut.build(p, b, ap, final=final)
    print(f"cut: {out}")
    if not final:
        ap["cut"] = {"status": "review"}; save_approvals(p, ap)
        print("Next: watch the draft film on the review page. Finals are only rendered after it is approved.")


def cmd_final(a):
    p = pdir(a.project); b = beats(p); ap = approvals(p)
    need(approved(ap, "cut"), "the draft film is not approved yet (finals are only paid for after that)")
    rows = shot_rows(b)
    secs = 0; jobs = []
    for r in rows:
        take = ap["clips"][r["id"]]["take"]
        meta = json.loads((p / "takes" / r["id"] / "takes.json").read_text())[take]
        dur = min(12, max(4, round(r["seconds"] + 1)))
        if not (p / "finals" / f"{r['id']}.mp4").exists():
            jobs.append((r, take, meta, dur)); secs += dur
    est = secs * kie.RATE[a.res]
    print(f"finals: {len(jobs)} shots, {secs} s at {a.res} = {money(est)}")
    try:
        b0 = ledger.gate(p, "finals", min(len(jobs), ledger.SAMPLE_BATCH), est, items=[])
    except ledger.LedgerError as e:
        sys.exit(f"STOPPED: {e}")
    for r, take, meta, dur in jobs:
        tid = kie.take(p / "frames" / f"{r['id']}.png", meta["prompt"], p / "finals" / f"{r['id']}.mp4", seconds=dur,
                       res=a.res, draft=False, draft_task_id=meta["task_id"])
        print(f"  {r['id']} ({take}): {'done' if tid else 'FAILED'}")
    ledger.spend(p, "finals", b0, kie.balance(), model="seedance-2-5", items=len(jobs))
    cmd_cut(a, final=True)


# ---------- status ----------
def stage_state(p, b, ap):
    rows = shot_rows(b)
    n = len(rows)
    fr = sum(approved(ap, "frames", r["id"]) for r in rows)
    cl = sum(approved(ap, "clips", r["id"]) for r in rows)
    has_block = any(r.get("blocking") for r in b["rows"])
    return [
        ("script", "approved" if approved(ap, "script") else "waiting for approval", approved(ap, "script")),
        ("blocking", ("approved" if approved(ap, "blocking") else "waiting for approval") if has_block else "not used", approved(ap, "blocking") or not has_block),
        ("frames", f"{fr}/{n} approved", fr == n),
        ("clips", f"{cl}/{n} approved", cl == n),
        ("voice", ap.get("voice", {}).get("name", "not chosen") if approved(ap, "voice") else "not chosen", approved(ap, "voice")),
        ("draft film", ap.get("cut", {}).get("status", "not cut"), approved(ap, "cut")),
        ("final", "done" if (p / "cut" / "final.mp4").exists() else "not rendered", (p / "cut" / "final.mp4").exists()),
    ]


def cmd_status(a):
    p = pdir(a.project); b = beats(p); ap = approvals(p)
    print(f"{b.get('title')}  ({p})")
    for name, txt, ok in stage_state(p, b, ap):
        print(f"  {'[x]' if ok else '[ ]'} {name:11s} {txt}")
    spent = sum((e.get("credits") or 0) for e in ledger.entries(p, "spend"))
    bal = kie.balance()
    print(f"  spent so far: {money(spent)}" + (f", balance {money(bal)}" if bal is not None else ""))


def cmd_review(a):
    import review_server
    review_server.serve(pdir(a.project), a.port, export=a.export)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("setup")
    x = sp.add_parser("new"); x.add_argument("project"); x.add_argument("--format", default="brand_film", choices=TEMPLATES); x.add_argument("--from", dest="src")
    for c in ("check", "blocking", "voices", "vo", "music", "cut", "status"):
        sp.add_parser(c).add_argument("project")
    x = sp.add_parser("frames"); x.add_argument("project"); x.add_argument("--only", nargs="*")
    x = sp.add_parser("takes"); x.add_argument("project"); x.add_argument("--n", type=int, default=4); x.add_argument("--only", nargs="*")
    x = sp.add_parser("final"); x.add_argument("project"); x.add_argument("--res", default="720p", choices=["720p", "1080p"])
    x = sp.add_parser("review"); x.add_argument("project"); x.add_argument("--port", type=int, default=8765)
    x.add_argument("--export", help="write a static read-only copy of the page to this folder instead of serving it")
    a = ap.parse_args()
    {"setup": cmd_setup, "new": cmd_new, "check": cmd_check, "blocking": cmd_blocking, "frames": cmd_frames, "takes": cmd_takes,
     "voices": cmd_voices, "vo": cmd_vo, "music": cmd_music, "cut": cmd_cut, "final": cmd_final, "status": cmd_status,
     "review": cmd_review}[a.cmd](a)


if __name__ == "__main__":
    main()
