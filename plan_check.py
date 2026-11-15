#!/usr/bin/env python3
# Copyright (c) 2026 Giovanni Brees. All rights reserved. Proprietary software: see LICENSE. Do not copy or redistribute.
"""Plan arithmetic before any credit is spent: cuts needed vs. unique clips vs. retakes vs. credits vs. balance.

Why: a plan that needs 150 cuts from 55 clips only works by showing every clip three times. Checking the promise
("every shot once") against the numbers before anything is generated saves a whole render. This check prints the table
the client signs and exits 1 when the plan cannot be made as promised.

Usage:
  python3 plan_check.py <plan.json> [--balance CREDITS]
plan.json:
{
  "title": "City music video",
  "audiomap": "audiomap.json",          cuts and durations from the song map (or give "cuts" and "duration_s")
  "cuts": null, "duration_s": null,
  "promise": {"no_repeat": true, "max_uses_per_clip": 1, "max_stretch": 1.3},
  "approved_clips": 54,                 clips already approved and usable in this film
  "usable_s_per_clip": 3.0,             clean seconds per generated clip after trims (Veo 4 s takes kept 2 to 3.9 s)
  "takes_per_shot": 1,                  first-round takes per new shot
  "video": {"model": "veo3_fast", "credits_per_take": 60, "reject_rate": 0.25},
  "frames": {"credits_each": 18, "remake_rate": 0.35},
  "reserve": 0.10,
  "budget_usd": 40
}
Defaults for reject rates come from the ledger when it has 5 or more outcomes for the model (ledger.reject_rate).
"""
import argparse
import json
import math
import sys
from pathlib import Path

import ledger


def check(plan, base, bal=None):
    am = None
    if plan.get("audiomap"):
        ap = base / plan["audiomap"]
        if not ap.exists():
            sys.exit(f"{ap} not found: run `python3 audiomap.py <song.mp3> {ap}` first")
        am = json.loads(ap.read_text())
    cuts = plan.get("cuts") or (am["plan"]["cuts"] if am else None)
    dur = plan.get("duration_s") or (am["duration"] if am else None)
    if not cuts or not dur:
        sys.exit("plan needs an audiomap or cuts + duration_s")
    pr = plan.get("promise", {})
    usable = plan.get("usable_s_per_clip", 3.0)
    max_uses = 1 if pr.get("no_repeat", True) else pr.get("max_uses_per_clip", 3)
    # longest cut lengths from the map: a cut longer than usable * max_stretch needs a clip that long or slow motion
    long_cuts = 0
    if am:
        bar = am["bar_s"]
        for s in am["sections"]:
            if s["cut_bars"] * bar > usable * pr.get("max_stretch", 1.3):
                long_cuts += s["cuts"]
    clips_needed = cuts if max_uses == 1 else math.ceil(cuts / min(max_uses, max(1, int(usable // (dur / cuts)))))
    have = plan.get("approved_clips", 0)
    new = max(0, clips_needed - have)
    v = plan.get("video", {}); f = plan.get("frames", {})
    rr = v.get("reject_rate") or ledger.reject_rate(base, v.get("model", ""), 0.25)
    rm = f.get("remake_rate", 0.35)
    tps = plan.get("takes_per_shot", 1)
    # expected takes: every new shot gets tps takes; a shot whose takes all fail gets one more round
    p_fail = rr ** tps
    takes = new * tps + new * p_fail * tps
    frames = new * (1 + rm)
    cr_video = takes * v.get("credits_per_take", 60)
    cr_frames = frames * f.get("credits_each", 18)
    total = (cr_video + cr_frames) * (1 + plan.get("reserve", 0.10))
    usd = total / ledger.CREDITS_PER_USD
    lost = new * p_fail ** 2          # shots expected to fail both rounds: the plan must survive losing them
    rows = [
        ("song", f"{dur:.0f} s" + (f", {am['tempo']} bpm, {len(am['bars'])} bars" if am else "")),
        ("cuts the music asks for", f"{cuts} (mean {dur / cuts:.2f} s per cut)"),
        ("rule", "every clip once" if max_uses == 1 else f"each clip at most {max_uses} times"),
        ("unique clips needed", str(clips_needed)),
        ("approved clips on hand", str(have)),
        ("new shots to make", str(new)),
        ("start frames (incl. remakes)", f"{frames:.0f} x {f.get('credits_each', 18)} = {cr_frames:.0f} credits"),
        ("video takes (incl. retakes)", f"{takes:.0f} x {v.get('credits_per_take', 60)} = {cr_video:.0f} credits (reject rate {rr:.0%})"),
        ("reserve", f"{plan.get('reserve', 0.10):.0%}"),
        ("total", f"{total:.0f} credits = ${usd:.2f}"),
        ("expected shots lost after 2 rounds", f"{lost:.1f}: the cut must allow merging {math.ceil(lost)} slot(s)"),
        ("cuts longer than one clip allows", f"{long_cuts} (need longer clips or a slower section)" if long_cuts else "0"),
    ]
    problems = []
    if bal is not None and bal < total:
        problems.append(f"balance {bal:.0f} credits < {total:.0f}: top up ${(total - bal) / ledger.CREDITS_PER_USD:.2f} or cut scope")
    if plan.get("budget_usd") and usd > plan["budget_usd"]:
        problems.append(f"cost ${usd:.2f} > budget ${plan['budget_usd']}: fewer cuts (slower sections) or fewer new shots")
    if long_cuts and pr.get("no_repeat", True):
        problems.append(f"{long_cuts} cuts are longer than {usable * pr.get('max_stretch', 1.3):.1f} s of usable footage")
    return rows, problems, {"cuts": cuts, "clips_needed": clips_needed, "new_shots": new, "credits": round(total), "usd": round(usd, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan"); ap.add_argument("--balance", type=float)
    a = ap.parse_args()
    p = Path(a.plan)
    plan = json.loads(p.read_text())
    bal = a.balance if a.balance is not None else ledger.balance()
    rows, problems, nums = check(plan, p.parent, bal)
    print(f"PLAN CHECK: {plan.get('title', p.stem)}")
    for k, v in rows:
        print(f"  {k:36s} {v}")
    if bal is not None:
        print(f"  {'balance':36s} {bal:.0f} credits (${bal / ledger.CREDITS_PER_USD:.2f})")
    (p.parent / "plan_check.json").write_text(json.dumps({"rows": rows, "problems": problems, **nums}, indent=1))
    if problems:
        print("NOT READY:"); [print("  -", x) for x in problems]; sys.exit(1)
    print("READY: show this table to the client and get a written yes before generating.")


if __name__ == "__main__":
    main()
