#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Blocking drawings: a top-view plan per shot showing the set, the camera and its field of view, where every person
stands and the path they walk. Drawn before any image is generated, approved with the script.

Why: when shots are generated one by one, the model re-invents the room, people swap seats, a group walks through a
wall. A drawn plan fixes positions and paths first; every frame prompt is then written from it.

beats.json:
  "sets": {"studio": {"size": [10, 7], "items": [
      {"name": "desk", "x": 2, "y": 2, "w": 1.6, "d": 0.8},
      {"name": "window", "x": 0, "y": 0, "w": 10, "d": 0.15, "kind": "window"},
      {"name": "door", "x": 9.2, "y": 6.85, "w": 0.8, "d": 0.15, "kind": "door"}]}},
  row "blocking": {"set": "studio", "camera": {"x": 5, "y": 6.5, "aim": [3, 2.5], "lens": 35, "move": "push-in"},
                   (a pan adds "pan_to": [x, y], the point the camera ends on)
                   "people": [{"name": "Lena", "at": [2.8, 3.2], "facing": [5, 6.5]},
                              {"name": "Sam", "from": [6, 4], "to": [9.4, 6.4], "via": [[8, 5.5]]}]}
Coordinates are metres from the top-left corner of the set; y grows toward the camera side. "facing" draws where a person
looks; "offscreen": true marks someone deliberately out of frame (over the shoulder).
Usage:
  python3 blocking.py <project_dir>            one PNG per row in blocking/, plus blocking/sheet.png
Checks (printed, FAIL also stored in blocking/check.json):
  FAIL  a walking path goes through furniture or a wall
  FAIL  a person stands inside furniture or outside the set
  WARN  more than one person walks in the same shot (video models drift people through each other)
  WARN  a person is outside the camera's field of view
  WARN  a walking person and the camera move on the same axis toward each other (hard for the model)
"""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

COLORS = [(220, 80, 60), (40, 120, 200), (40, 160, 90), (200, 140, 20), (140, 70, 180), (20, 160, 170)]
PX = 80  # pixels per metre


def font(size, bold=False):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def fov(lens):
    """Horizontal field of view in degrees for a full-frame lens (35 mm -> 54 deg)."""
    return math.degrees(2 * math.atan(36 / (2 * lens)))


def seg_hits_rect(p, q, r, pad=0.15):
    """True when segment p-q passes through rectangle r (x, y, w, d) grown by pad."""
    x0, y0, x1, y1 = r["x"] - pad, r["y"] - pad, r["x"] + r["w"] + pad, r["y"] + r["d"] + pad
    for k in range(41):
        t = k / 40
        x, y = p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t
        if x0 < x < x1 and y0 < y < y1:
            return True
    return False


def inside(pt, r):
    return r["x"] < pt[0] < r["x"] + r["w"] and r["y"] < pt[1] < r["y"] + r["d"]


def check(row, st):
    b = row.get("blocking", {}); issues = []
    W, D = st["size"]
    solid = [i for i in st.get("items", []) if i.get("kind") not in ("door", "rug", "light")]
    movers = [p for p in b.get("people", []) if "from" in p]
    for p in b.get("people", []):
        pts = [p.get("at")] if "at" in p else [p["from"], *p.get("via", []), p["to"]]
        for pt in pts:
            if not (0 <= pt[0] <= W and 0 <= pt[1] <= D):
                issues.append(("FAIL", f"{p['name']} at {pt} is outside the set"))
            for it in solid:
                if inside(pt, it):
                    issues.append(("FAIL", f"{p['name']} stands inside the {it['name']}"))
        for a, c in zip(pts, pts[1:]):
            for it in solid:
                if seg_hits_rect(a, c, it):
                    issues.append(("FAIL", f"{p['name']} walks through the {it['name']}: add a 'via' point around it"))
    if len(movers) > 1:
        issues.append(("WARN", f"{len(movers)} people walk in one shot ({', '.join(p['name'] for p in movers)}): video models drift people through each other; let one walk, others act in place"))
    cam = b.get("camera")
    if cam:
        half = fov(cam.get("lens", 35)) / 2
        aims = [math.atan2(a[1] - cam["y"], a[0] - cam["x"]) for a in ([cam["aim"]] + ([cam["pan_to"]] if cam.get("pan_to") else []))]
        def seen(pt, which):
            return any(abs(math.degrees((math.atan2(pt[1] - cam["y"], pt[0] - cam["x"]) - aims[k] + math.pi) % (2 * math.pi) - math.pi)) <= half for k in which)
        for p in b.get("people", []):
            if p.get("offscreen"):          # deliberately out of frame (over-the-shoulder, a hand entering)
                continue
            checks = [(p["at"], range(len(aims)))] if "at" in p else [(p["from"], [0]), (p["to"], [len(aims) - 1])]
            for pt, which in checks:
                if not seen(pt, which):
                    issues.append(("WARN", f"{p['name']} at {pt} is outside the camera's view ({cam.get('lens', 35)} mm, {2 * half:.0f} deg)"))
        for p in movers:
            v = (p["to"][0] - p["from"][0], p["to"][1] - p["from"][1])
            c = (cam["aim"][0] - cam["x"], cam["aim"][1] - cam["y"])
            n = math.hypot(*v) * math.hypot(*c)
            if n and (v[0] * c[0] + v[1] * c[1]) / n < -0.9 and cam.get("move") in ("push-in", "dolly-in"):
                issues.append(("WARN", f"{p['name']} walks straight at a pushing camera: pick one of the two moves"))
    return issues


def draw(row, st, chars, out):
    b = row.get("blocking", {}); W, D = st["size"]
    M = 60; im = Image.new("RGB", (int(W * PX) + 2 * M, int(D * PX) + 2 * M + 150), (250, 248, 244))
    d = ImageDraw.Draw(im)
    X = lambda x: M + x * PX; Y = lambda y: M + 110 + y * PX
    d.text((M, 18), f"{row.get('id', '')}  {row.get('seconds', '')} s  {row.get('framing', '')}", fill=(30, 30, 30), font=font(30, True))
    d.text((M, 60), (row.get("subject_motion") or "")[:90], fill=(80, 80, 80), font=font(22))
    for gx in range(int(W) + 1):
        d.line([X(gx), Y(0), X(gx), Y(D)], fill=(232, 228, 220))
    for gy in range(int(D) + 1):
        d.line([X(0), Y(gy), X(W), Y(gy)], fill=(232, 228, 220))
    d.rectangle([X(0), Y(0), X(W), Y(D)], outline=(60, 60, 60), width=4)
    for it in st.get("items", []):
        kind = it.get("kind", "furniture")
        fill = {"window": (170, 205, 235), "door": (250, 248, 244), "rug": (238, 230, 214), "light": (255, 236, 170)}.get(kind, (205, 196, 182))
        d.rectangle([X(it["x"]), Y(it["y"]), X(it["x"] + it["w"]), Y(it["y"] + it["d"])], fill=fill, outline=(120, 110, 100), width=2)
        d.text((X(it["x"]) + 4, Y(it["y"]) + 2), it["name"], fill=(70, 60, 50), font=font(16))
    cam = b.get("camera")
    if cam:
        half = math.radians(fov(cam.get("lens", 35)) / 2); R = max(W, D) * 1.2
        ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
        for k, a in enumerate([cam["aim"]] + ([cam["pan_to"]] if cam.get("pan_to") else [])):
            aim = math.atan2(a[1] - cam["y"], a[0] - cam["x"])
            cone = [(X(cam["x"]), Y(cam["y"])), (X(cam["x"] + R * math.cos(aim - half)), Y(cam["y"] + R * math.sin(aim - half))),
                    (X(cam["x"] + R * math.cos(aim + half)), Y(cam["y"] + R * math.sin(aim + half)))]
            ImageDraw.Draw(ov).polygon(cone, fill=(255, 200, 60, 70) if k == 0 else (90, 160, 255, 55))
        clip = Image.new("L", im.size, 0); ImageDraw.Draw(clip).rectangle([X(0), Y(0), X(W), Y(D)], fill=255)
        ov.putalpha(Image.composite(ov.getchannel("A"), Image.new("L", im.size, 0), clip))
        im.paste(ov, (0, 0), ov); d = ImageDraw.Draw(im)
        aim = math.atan2(cam["aim"][1] - cam["y"], cam["aim"][0] - cam["x"])
        cx, cy = X(cam["x"]), Y(cam["y"])
        d.polygon([(cx + 22 * math.cos(aim), cy + 22 * math.sin(aim)), (cx + 16 * math.cos(aim + 2.4), cy + 16 * math.sin(aim + 2.4)),
                   (cx + 16 * math.cos(aim - 2.4), cy + 16 * math.sin(aim - 2.4))], fill=(30, 30, 30))
        lbl = f"CAM {cam.get('lens', 35)} mm" + (f", {cam['move']}" if cam.get("move") else "") + (" (blue: end of pan)" if cam.get("pan_to") else "")
        d.text((cx + 18, cy + 12), lbl, fill=(30, 30, 30), font=font(18, True))
        if cam.get("move") in ("push-in", "dolly-in", "pull-out", "dolly-out"):
            sgn = 1 if "in" in cam["move"] else -1
            ex, ey = cx + sgn * 70 * math.cos(aim), cy + sgn * 70 * math.sin(aim)
            d.line([cx, cy, ex, ey], fill=(30, 30, 30), width=3)
    for i, p in enumerate(b.get("people", [])):
        col = COLORS[chars.index(p["name"]) % len(COLORS)] if p["name"] in chars else COLORS[i % len(COLORS)]
        if "at" in p:
            pts = [p["at"]]
        else:
            pts = [p["from"], *p.get("via", []), p["to"]]
            for a, c in zip(pts, pts[1:]):
                n = max(2, int(math.hypot(c[0] - a[0], c[1] - a[1]) / 0.35))
                for k in range(n):
                    t = k / n; x, y = a[0] + (c[0] - a[0]) * t, a[1] + (c[1] - a[1]) * t
                    d.ellipse([X(x) - 4, Y(y) - 4, X(x) + 4, Y(y) + 4], fill=col)
            a, c = pts[-2], pts[-1]; ang = math.atan2(c[1] - a[1], c[0] - a[0])
            tip = (X(c[0]), Y(c[1]))
            d.polygon([tip, (tip[0] - 22 * math.cos(ang - 0.4), tip[1] - 22 * math.sin(ang - 0.4)),
                       (tip[0] - 22 * math.cos(ang + 0.4), tip[1] - 22 * math.sin(ang + 0.4))], fill=col)
        x0, y0 = X(pts[0][0]), Y(pts[0][1])
        if p.get("facing"):
            fp = pts[-1]; fa = math.atan2(p["facing"][1] - fp[1], p["facing"][0] - fp[0]); fx, fy = X(fp[0]), Y(fp[1])
            d.line([fx, fy, fx + 34 * math.cos(fa), fy + 34 * math.sin(fa)], fill=col, width=4)
        d.ellipse([x0 - 16, y0 - 16, x0 + 16, y0 + 16], fill=col, outline="white", width=3)
        lbl = p["name"] + (" walks" if "from" in p else "") + (" (off frame)" if p.get("offscreen") else "")
        tw = d.textlength(lbl, font=font(20, True))
        tx = x0 + 20 if x0 + 20 + tw < im.width - 8 else x0 - 20 - tw
        d.text((tx, y0 - 14), lbl, fill=col, font=font(20, True))
    issues = check(row, st)
    y = Y(D) + 18
    for lvl, msg in sorted(issues, key=lambda i: i[0] != "FAIL")[:3]:
        txt = f"{lvl}: {msg}"
        while d.textlength(txt, font=font(17)) > im.width - 2 * M and len(txt) > 20:
            txt = txt[:-2]
        txt = txt if txt == f"{lvl}: {msg}" else txt.rstrip(" ,:;") + "..."
        d.text((M, y), txt, fill=(200, 40, 40) if lvl == "FAIL" else (180, 120, 0), font=font(17)); y += 24
    if not issues:
        d.text((M, y), "blocking OK", fill=(40, 140, 70), font=font(18, True))
    im.save(out)
    return issues


def describe(row):
    """Plain words for where each visible person is in the frame, from the camera geometry, for the frame prompt."""
    bl = row.get("blocking") or {}
    cam = bl.get("camera")
    if not cam:
        return ""
    fx, fy = cam["aim"][0] - cam["x"], cam["aim"][1] - cam["y"]
    n = math.hypot(fx, fy) or 1; fx, fy = fx / n, fy / n
    rx, ry = -fy, fx                                   # map y points down, so this is the frame's right
    t = math.tan(math.radians(fov(cam.get("lens", 35)) / 2))
    out = []
    for p in bl.get("people", []):
        if p.get("offscreen"):
            continue
        pt = p.get("at") or p["from"]
        vx, vy = pt[0] - cam["x"], pt[1] - cam["y"]
        depth = vx * fx + vy * fy
        if depth <= 0:
            continue
        lat = (vx * rx + vy * ry) / depth / t
        side = "on the left of the frame" if lat < -0.33 else ("on the right of the frame" if lat > 0.33 else "in the center of the frame")
        dist = "in the foreground" if depth < 2.5 else ("in the middle distance" if depth < 5 else "in the background")
        out.append(f"{p['name']} {side}, {dist}")
    return ("Positions: " + "; ".join(out) + ".") if out else ""


def main():
    proj = Path(sys.argv[1]); b = json.loads((proj / "beats.json").read_text())
    sets = b.get("sets", {}); chars = list(b.get("characters", []))
    out = proj / "blocking"; out.mkdir(exist_ok=True)
    files, report = [], {}
    for row in b.get("rows", []):
        bl = row.get("blocking")
        if not bl or bl.get("set") not in sets:
            continue
        f = out / f"{row['id']}.png"
        iss = draw(row, sets[bl["set"]], chars, f)
        files.append(f); report[row["id"]] = iss
        for lvl, msg in iss:
            print(f"  {lvl:4s} {row['id']}: {msg}")
    if files:
        ims = [Image.open(f) for f in files]; w = max(i.width for i in ims); h = max(i.height for i in ims)
        cols = 3; rows = (len(ims) + cols - 1) // cols; sc = 0.5
        sheet = Image.new("RGB", (int(w * sc) * cols, int(h * sc) * rows), "white")
        for k, i in enumerate(ims):
            sheet.paste(i.resize((int(i.width * sc), int(i.height * sc))), ((k % cols) * int(w * sc), (k // cols) * int(h * sc)))
        sheet.save(out / "sheet.png")
    (out / "check.json").write_text(json.dumps(report, indent=1))
    fails = sum(1 for v in report.values() for l, _ in v if l == "FAIL")
    print(f"{len(files)} blocking drawings in {out}, {fails} FAIL")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
