#!/usr/bin/env python3
"""Cost ledger, decision log, retry caps and sample-before-batch, per project folder.

Why: spend reconstructed afterwards from balance readings is always wrong, retakes that are not planned blow the budget,
and a batch of 50 frames generated before one sample proved the prompt is 50 wasted frames. Every credit goes through here.

File: <project>/ledger.jsonl, one JSON object per line, append only:
  {"t", "kind": "estimate"|"spend"|"outcome"|"sample_ok"|"decision"|"note", "stage", "credits", "model", "item", ...}
Rules enforced by gate():
  1. Sample before batch: a batch of more than 3 items in a stage needs a "sample_ok" entry for that stage first
     (one sample generated, looked at, approved). `ledger.py sample-ok <dir> <stage>` records it.
  2. Retry cap: an item is submitted in at most 3 rounds (MAX_TRIES; a round may hold several takes). After that it is
     re-designed and the change goes back to the client in writing.
  3. Reserve: the balance must cover the estimate plus RESERVE (10%) for retakes, or nothing is submitted.
Commands:
  python3 ledger.py summary <dir>                 spend per stage, estimate vs actual, reject rate per model
  python3 ledger.py sample-ok <dir> <stage>       record that the sample for a stage was approved
  python3 ledger.py decision <dir> "<question>" "<chosen>" "<rejected option: why>" ...
  python3 ledger.py balance                       kie.ai balance in credits and dollars
In code:
  import ledger
  ledger.gate(fd, "frames", n_items=53, est_credits=954)   raises LedgerError if a rule is broken
  b0 = ledger.balance(); ...render...; ledger.spend(fd, "frames", b0, ledger.balance(), model="nano-banana-pro", items=53)
  ledger.outcome(fd, "veo3_fast", "C44_t1", ok=False, why="two taxis move")
  ledger.reject_rate(fd, "veo3_fast")   measured share of failed outputs, used by plan_check.py
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

MAX_TRIES = 3
RESERVE = 0.10
SAMPLE_BATCH = 3
CREDITS_PER_USD = 200


class LedgerError(RuntimeError):
    pass


def _file(fd):
    return Path(fd) / "ledger.jsonl"


def record(fd, kind, **kw):
    entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "kind": kind, **kw}
    with open(_file(fd), "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def entries(fd, kind=None):
    p = _file(fd)
    if not p.exists():
        return []
    out = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return [e for e in out if kind is None or e["kind"] == kind]


def balance():
    key = os.environ.get("KIE_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request("https://api.kie.ai/api/v1/chat/credit", headers={"Authorization": f"Bearer {key}"})
        return float(json.loads(urllib.request.urlopen(req, timeout=20).read())["data"])
    except Exception:
        return None


def tries(fd, item):
    """Rounds an item has been submitted in (one gate() call = one round, however many takes it renders)."""
    return sum(1 for e in entries(fd, "estimate") if item in e.get("round_items", []))


def gate(fd, stage, n_items, est_credits, items=()):
    """Raise LedgerError when a batch breaks sample-before-batch, the retry cap or the reserve. Records the estimate."""
    if n_items > SAMPLE_BATCH and not any(e.get("stage") == stage for e in entries(fd, "sample_ok")):
        raise LedgerError(f"{stage}: {n_items} items without an approved sample. Generate 1 to 3, check them, then "
                          f"`python3 ledger.py sample-ok {fd} {stage}`.")
    over = [i for i in items if tries(fd, i) >= MAX_TRIES]
    if over:
        raise LedgerError(f"{stage}: {', '.join(over)} already had {MAX_TRIES} tries. Re-design the shot and send the change to the client.")
    bal = balance()
    need = est_credits * (1 + RESERVE)
    if bal is not None and bal < need:
        raise LedgerError(f"{stage}: balance {bal:.0f} credits < estimate {est_credits:.0f} + {int(RESERVE * 100)}% reserve = {need:.0f}. "
                          f"Top up about ${(need - bal) / CREDITS_PER_USD:.2f} or cut scope.")
    record(fd, "estimate", stage=stage, credits=round(est_credits), items=n_items, balance=bal, round_items=list(items))
    return bal


def spend(fd, stage, before, after, model="", items=0, detail=""):
    """Record what a step really cost, measured from the balance before and after."""
    if before is None or after is None:
        return record(fd, "spend", stage=stage, credits=None, model=model, items=items, detail=detail + " (balance unavailable)")
    return record(fd, "spend", stage=stage, credits=round(before - after), usd=round((before - after) / CREDITS_PER_USD, 2),
                  model=model, items=items, detail=detail, balance=after)


def outcome(fd, model, item, ok, why=""):
    return record(fd, "outcome", model=model, item=item, ok=bool(ok), why=why)


def reject_rate(fd, model, default=None):
    o = [e for e in entries(fd, "outcome") if e.get("model") == model]
    if len(o) < 5:
        return default
    return round(sum(not e["ok"] for e in o) / len(o), 3)


def decision(fd, question, chosen, rejected=()):
    return record(fd, "decision", question=question, chosen=chosen,
                  rejected=[{"option": r.split(":")[0].strip(), "because": r.split(":", 1)[1].strip() if ":" in r else ""} for r in rejected])


def summary(fd):
    sp = entries(fd, "spend"); est = entries(fd, "estimate")
    stages = {}
    for e in est:
        stages.setdefault(e["stage"], [0, 0])[0] += e.get("credits") or 0
    for e in sp:
        stages.setdefault(e["stage"], [0, 0])[1] += e.get("credits") or 0
    tot = sum(v[1] for v in stages.values())
    print(f"{'stage':28s} {'estimate':>9s} {'spent':>9s}")
    for s, (a, b) in stages.items():
        print(f"{s:28s} {a:9.0f} {b:9.0f}   ${b / CREDITS_PER_USD:.2f}")
    print(f"{'total':28s} {'':9s} {tot:9.0f}   ${tot / CREDITS_PER_USD:.2f}")
    models = sorted({e.get("model") for e in entries(fd, "outcome")})
    for m in models:
        o = [e for e in entries(fd, "outcome") if e.get("model") == m]
        print(f"reject rate {m}: {sum(not e['ok'] for e in o)}/{len(o)} = {sum(not e['ok'] for e in o) / len(o):.0%}")
    for d in entries(fd, "decision")[-10:]:
        print(f"decision {d['t']}: {d['question']} -> {d['chosen']}" + "".join(f"; not {r['option']} ({r['because']})" for r in d["rejected"]))
    return stages


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "summary":
        summary(sys.argv[2])
    elif cmd == "sample-ok":
        print(record(sys.argv[2], "sample_ok", stage=sys.argv[3]))
    elif cmd == "decision":
        print(decision(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]))
    elif cmd == "balance":
        b = balance(); print(f"{b:.0f} credits (${b / CREDITS_PER_USD:.2f})" if b is not None else "KIE_API_KEY not set or API unreachable")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
