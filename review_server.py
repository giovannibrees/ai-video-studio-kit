# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Review page: one local web page per project where a person approves or sends back every step.

  python3 studio.py review <project>               serve it at http://localhost:8765 (approve buttons work)
  python3 studio.py review <project> --export DIR  write a static, read-only copy to share (no buttons)

Every click writes approvals.json in the project: who, when, approved or changes, and the note. A note on "changes" is
added to the next prompt for that frame or take, so the fix request travels with the regeneration.
"""
import http.server
import json
import mimetypes
import os
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import ledger  # noqa: E402


def state(p):
    import studio
    b = studio.beats(p); ap = studio.approvals(p)
    rel = lambda x: str(Path(x).relative_to(p))
    rows = []
    for r in b["rows"]:
        d = {"row": r, "frame": None, "takes": [], "blocking": None}
        f = p / "frames" / f"{r['id']}.png"
        if f.exists():
            meta = json.loads((p / "frames" / f"{r['id']}.json").read_text()) if (p / "frames" / f"{r['id']}.json").exists() else {}
            d["frame"] = {"src": rel(f) + f"?v={int(f.stat().st_mtime)}", "prompt": meta.get("prompt", ""),
                          "versions": len(list((p / "frames" / "old").glob(f"{r['id']}_v*.png"))) if (p / "frames" / "old").exists() else 0}
        bp = p / "blocking" / f"{r['id']}.png"
        if bp.exists():
            d["blocking"] = rel(bp)
        tj = p / "takes" / r["id"] / "takes.json"
        if tj.exists():
            for name, m in json.loads(tj.read_text()).items():
                v = p / "takes" / r["id"] / f"{name}.mp4"
                qa = p / "takes" / r["id"] / f"{name}.qa.json"
                flags = json.loads(qa.read_text()).get("flags", []) if qa.exists() else []
                d["takes"].append({"name": name, "src": rel(v), "qa": m.get("qa"), "clean": m.get("clean"),
                                   "strip": rel(p / "takes" / r["id"] / f"{name}.qa.jpg") if (p / "takes" / r["id"] / f"{name}.qa.jpg").exists() else None,
                                   "flags": sorted({f"{x['kind']} at {x['t']}s" for x in flags})[:6]})
        rows.append(d)
    samples = p / "vo" / "samples" / "samples.json"
    check = p / "blocking" / "check.json"
    spent = sum((e.get("credits") or 0) for e in ledger.entries(p, "spend"))
    return {
        "title": b.get("title"), "user": os.environ.get("STUDIO_USER", ""), "brand": os.environ.get("STUDIO_BRAND", ""),
        "beats": b, "approvals": ap, "rows": rows,
        "steps": [{"name": n, "text": t, "ok": ok} for n, t, ok in studio.stage_state(p, b, ap)],
        "script_check": (p / "script_check.txt").read_text() if (p / "script_check.txt").exists() else "",
        "blocking_sheet": rel(p / "blocking" / "sheet.png") if (p / "blocking" / "sheet.png").exists() else None,
        "blocking_check": json.loads(check.read_text()) if check.exists() else None,
        "voices": json.loads(samples.read_text()) if samples.exists() else [],
        "music": [rel(x) for x in sorted((p / "music").glob("score_*.mp3"))] if (p / "music").exists() else [],
        "draft": rel(p / "cut" / "draft.mp4") if (p / "cut" / "draft.mp4").exists() else None,
        "final": rel(p / "cut" / "final.mp4") if (p / "cut" / "final.mp4").exists() else None,
        "spent": spent,
    }


def approve(p, body):
    import studio
    ap = studio.approvals(p)
    stage, item, status = body["stage"], body.get("id"), body["status"]
    rec = {"status": status, "by": os.environ.get("STUDIO_USER", "reviewer"), "t": time.strftime("%Y-%m-%d %H:%M"),
           "note": (body.get("note") or "").strip()}
    if stage == "clips" and status == "approved":
        take = body.get("take")
        meta = json.loads((p / "takes" / item / "takes.json").read_text())
        if take not in meta:
            raise ValueError("pick a take")
        rec.update(take=take, clean=meta[take].get("clean"))
    if stage == "voice":
        rec.update(voice_id=body.get("voice_id"), name=body.get("name"))
    if stage == "music":
        rec.update(file=body.get("file"))
    if item:
        ap.setdefault(stage, {})[item] = rec
    else:
        ap[stage] = rec
    log = ap.setdefault("_log", []); log.append({"stage": stage, "id": item, **rec})
    studio.save_approvals(p, ap)
    # the first approved frame or take is the sample that unlocks the batch
    if status == "approved" and stage in ("frames", "clips"):
        st = "frames" if stage == "frames" else "takes"
        if not any(e.get("stage") == st for e in ledger.entries(p, "sample_ok")):
            ledger.record(p, "sample_ok", stage=st, item=item)
    return ap


PAGE = (ROOT / "review_page.html")


def serve(p, port=8765, export=None):
    p = Path(p).resolve()
    if export:
        out = Path(export); out.mkdir(parents=True, exist_ok=True)
        st = state(p); st["readonly"] = True
        html = PAGE.read_text().replace("/*STATE*/null", json.dumps(st))
        (out / "index.html").write_text(html)
        for sub in ("frames", "blocking", "takes", "vo", "music", "cut"):
            if (p / sub).exists():
                shutil.copytree(p / sub, out / sub, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*_parts", "*.first.jpg", "old"))
        print(f"static copy in {out}/index.html")
        return

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, data, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                return self.send(200, PAGE.read_text().encode(), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self.send(200, json.dumps(state(p)).encode())
            f = (p / urllib.parse.unquote(u.path.lstrip("/"))).resolve()
            if p not in f.parents or not f.is_file():
                return self.send(404, b"not found", "text/plain")
            data = f.read_bytes()
            rng = self.headers.get("Range")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if rng and rng.startswith("bytes="):                      # video seeking in the browser
                a, _, b = rng[6:].partition("-")
                a = int(a or 0); b = int(b) if b else len(data) - 1
                chunk = data[a:b + 1]
                self.send_response(206); self.send_header("Content-Type", ctype); self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {a}-{a + len(chunk) - 1}/{len(data)}")
                self.send_header("Content-Length", str(len(chunk))); self.end_headers(); self.wfile.write(chunk); return
            self.send(200, data, ctype)

        def do_POST(self):
            if self.path != "/api/approve":
                return self.send(404, b"{}")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                approve(p, body)
                self.send(200, json.dumps({"ok": True}).encode())
            except Exception as e:
                self.send(400, json.dumps({"ok": False, "error": str(e)}).encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Review page: http://localhost:{port}   (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
