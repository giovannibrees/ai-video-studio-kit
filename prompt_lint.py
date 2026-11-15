#!/usr/bin/env python3
"""Prompt checker: blocks a generation before it spends credits when the prompt asks the model for what it does badly.

Built from real failures: 50 of 53 start frames once failed review in one round because the prompt template itself
asked for "motion-blurred crowds", and city prompts produced garbled pseudo-text on every sign. Video takes failed on
traffic crossing itself, roads vanishing, a foot hitching and a smoke blob growing from nothing.

Usage:
  python3 prompt_lint.py image "prompt text"           check one start-frame / keyframe prompt
  python3 prompt_lint.py video "prompt text"           check one video (motion) prompt
  python3 prompt_lint.py file <shots.json|film.json>   check every prompt in a shot file (exit 1 on any FAIL)
In code:
  import prompt_lint; prompt_lint.gate(prompt, "image")   raises PromptError on a FAIL (film.py and takes.py call this)

Rules (FAIL blocks the call, WARN is printed):
  image  FAIL  crowds or background people (crowd, pedestrians, passers-by, tourists, commuters, busy, bustling ...)
         FAIL  signs or writing asked for (sign, signage, billboard, menu, poster, lettering, licence plate ...)
         FAIL  no explicit "no readable text" clause
         WARN  no explicit "no people" clause in a shot without named characters
  video  FAIL  any of the image FAILs above
         FAIL  groups moving (people walking, crowd moving), vehicles in two directions, crossing traffic
         FAIL  more than one camera move named (push-in AND pan, orbit AND tilt ...)
         FAIL  object physics the model breaks (papers flying, book closing, things bursting, smoke billowing ...)
         WARN  traffic mentioned without "one direction" / "same direction" / "no traffic"
         WARN  no camera move named at all (a shot without a move tends to come back frozen) unless "locked-off" or "time-lapse" is stated
A phrase is allowed when it is negated in the same clause ("no people", "without signs", "blank signboards",
"no readable text"). Override a single rule per shot with "lint_allow": ["crowd"] in the shot spec, only after the
client has asked for that content in writing.
"""
import json
import re
import sys

NEG = r"(?:\bno\b|\bnot\b|\bwithout\b|\bnever\b|\bnothing\b|\bzero\b|\bblank\b|\bunlit\b|\bempty of\b|\bfree of\b|\bnone\b|\bnobody\b|\bno one\b|\bunreadable\b|\bplain\b)"

CROWD = r"\b(crowds?|crowded|pedestrians?|passers?-?by|bystanders?|onlookers?|tourists?|commuters?|shoppers?|throngs?|busy|bustling|teeming|packed with people|people|worshippers?|vendors?|diners?|passengers?|silhouettes? of people|figures)\b"
TEXT = r"\b(signs?|signage|signboards?|billboards?|neon signs?|neon text|lettering|letters|writing|words|text|captions?|subtitles?|menus?|posters?|banners?|labels?|stickers?|licen[cs]e plates?|number plates?|plates?|logos?|brand names?|shop names?|numbers?|characters? on)\b"
GROUP_MOVE = r"\b((people|crowds?|group|team|colleagues|pedestrians|tourists)\s+(walk|walking|run|running|cross|crossing|move|moving|stream|streaming|leave|leaving))\b"
TWO_WAY = r"\b(both directions|two-way|oncoming|cross(ing)? traffic|intersection|junction|criss-?cross|weav(e|ing)|swarm)\b"
TRAFFIC = r"\b(traffic|cars?|taxis?|tuk-?tuks?|motorbikes?|scooters?|buses|trucks|vehicles?)\b"
ONE_WAY = r"\b(one direction|same direction|one-way|one way|single lane|no traffic|parked|empty (road|street|highway))\b"
PHYSICS = r"\b(papers? (fly|flying|burst)|book (closes|closing)|bursts?|explod(e|es|ing)|shatter(s|ing)?|smoke (billows|billowing|blob)|billows|pours? out|splash(es|ing)? everywhere|liquid pour)\b"
ADV = r"(?:(?:very |slowly |gently |fast |quickly |smoothly |steadily |straight |low |high |and )*)"
CAMERA = {"push-in": r"\bpush(es|ing)?[- ]in\b|\bdoll(y|ies)[- ]in\b|\b(camera|drone) " + ADV + r"(moves|pushes|drifts) " + ADV + r"(toward|forward|in)",
          "pull-out": r"\bpull(s|ing)?[- ](out|back)\b|\bdoll(y|ies)[- ](out|back)\b",
          "pan": r"\b(camera|drone) " + ADV + r"pan(s|ning)?\b|(^|[.;:] *)(slow |slowly )?pan(s|ning)? (left|right|across)\b|\bwhip[- ]pan\b", "tilt": r"\b(camera|drone|lens) " + ADV + r"tilt(s|ing)?\b|(^|[.;:] *)(slow |slowly )?tilt(s|ing)? (up|down)\b|\b(pans?|moves?|pushes)[^.]{0,40}\band tilt(s|ing)? (up|down)\b", "orbit": r"\borbits?\b|\borbiting\b|\barcs? around\b",
          "track": r"\btracks? " + ADV + r"(along|with|beside|past)\b|\btracking\b|\btrucks?\b|\bslides? " + ADV + r"(sideways|along|past|left|right)\b|\bdoll(y|ies) " + ADV + r"(along|past|sideways)\b",
          "crane": r"\bcrane\b|\brises? " + ADV + r"up\b|\b(drone|camera) " + ADV + r"rises\b|\bdescends?\b",
          "glide": r"\bglides? " + ADV + r"(forward|along|over|through|toward)\b|\bflies? " + ADV + r"(forward|over|through|between)\b|\bhyperlapse forward\b|\bpoint of view " + ADV + r"moving\b",
          "zoom": r"\bzoom(s|ing)?\b", "handheld": r"\bhandheld\b"}
STATIC_OK = r"\b(locked-?off|static( close)? camera|time-?lapse|camera holds( steady)?|fixed to the|rides with)\b"
NO_TEXT_CLAUSE = r"\bno (readable |visible )?(text|letters|lettering|writing|words)\b|\bno readable\b"
NO_PEOPLE_CLAUSE = r"\b(no people|no person|nobody|no one|empty of people|completely empty|no pedestrians|no humans|no figures)\b"


class PromptError(ValueError):
    pass


PIVOT = r"\b(but|except|only|while|whereas|though|apart from|other than|as)\b"


def _clauses(p):
    return [c.strip() for c in re.split(r"[.;:!?]", p) if c.strip()]


def _hits(pattern, prompt, allow=()):
    """Matches of `pattern` that are not negated inside their own clause."""
    out = []
    for c in _clauses(prompt):
        low = c.lower()
        for m in re.finditer(pattern, low):
            word = m.group(0)
            if any(a in word for a in allow):
                continue
            before = low[:m.start()]
            neg = list(re.finditer(NEG, before))
            # negated when a negation word comes earlier in the clause and no pivot ("but", "only as", "except") follows it:
            # "no pedestrians, crowds, figures" and "no readable letters, numbers or logos" pass; "people only as blurred crowds" fails
            if neg and not re.search(PIVOT, before[neg[-1].end():]):
                continue
            out.append((word, c))
    return out


def lint(prompt, kind="image", allow=(), characters=False):
    """Return a list of (level, rule, detail). kind is 'image' or 'video'. characters=True when the shot names a person."""
    issues = []
    for w, c in _hits(CROWD, prompt, allow):
        if w == "people" and characters and re.search(r"\b(two|both|the) people\b", c.lower()):
            continue
        issues.append(("FAIL", "crowd", f"asks for '{w}' in: {c[:90]}"))
    for w, c in _hits(TEXT, prompt, allow):
        issues.append(("FAIL", "text", f"asks for '{w}' in: {c[:90]}"))
    if not re.search(NO_TEXT_CLAUSE, prompt.lower()):
        issues.append(("FAIL" if kind == "image" else "WARN", "no-text-clause", "add 'no readable text, letters or logos anywhere'"))
    if not characters and not re.search(NO_PEOPLE_CLAUSE, prompt.lower()):
        issues.append(("WARN", "no-people-clause", "no named character: add 'no people anywhere'"))
    if kind == "video":
        low = prompt.lower()
        for w, c in _hits(GROUP_MOVE, prompt, allow):
            issues.append(("FAIL", "group-move", f"group motion '{w}': groups moving break the model"))
        for w, c in _hits(TWO_WAY, prompt, allow):
            issues.append(("FAIL", "two-way", f"'{w}': vehicles crossing or two-way traffic break the model"))
        for w, c in _hits(PHYSICS, prompt, allow):
            issues.append(("FAIL", "physics", f"'{w}': object physics the model breaks"))
        if _hits(TRAFFIC, prompt, allow) and not re.search(ONE_WAY, low):
            issues.append(("WARN", "traffic", "traffic without 'one direction', 'parked' or 'no traffic'"))
        moves = [k for k, pat in CAMERA.items() if re.search(pat, low)]
        if len(moves) > 1:
            issues.append(("FAIL", "camera", f"{len(moves)} camera moves named ({', '.join(moves)}): one move per shot"))
        if not moves and not re.search(STATIC_OK, low):
            issues.append(("WARN", "camera", "no camera move named (frozen takes); say the move, or 'locked-off time-lapse'"))
    return issues


def gate(prompt, kind="image", allow=(), characters=False, label=""):
    issues = lint(prompt, kind, allow, characters)
    for lvl, rule, d in issues:
        if lvl == "WARN":
            print(f"  lint WARN {label} {rule}: {d}", file=sys.stderr)
    fails = [i for i in issues if i[0] == "FAIL"]
    if fails:
        raise PromptError(f"{label} prompt blocked by prompt_lint: " + "; ".join(f"{r}: {d}" for _, r, d in fails))
    return issues


def _walk(obj, path=""):
    """Yield (path, prompt, kind, allow, characters) for every prompt-like string in a shot or film file."""
    if isinstance(obj, dict):
        allow = tuple(obj.get("lint_allow", ()))
        chars = bool(obj.get("characters"))
        for k, v in obj.items():
            if isinstance(v, str) and k in ("prompt", "action", "motion", "frame", "frame_prompt", "video_prompt"):
                kind = "video" if k in ("prompt", "motion", "video_prompt") else "image"
                yield f"{path}/{k}", v, kind, allow, chars
            else:
                yield from _walk(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    mode = sys.argv[1]
    if mode in ("image", "video"):
        items = [("arg", " ".join(sys.argv[2:]), mode, (), False)]
    else:
        items = list(_walk(json.loads(open(sys.argv[2]).read())))
    n_fail = 0
    for path, p, kind, allow, chars in items:
        issues = lint(p, kind, allow, chars)
        fails = [i for i in issues if i[0] == "FAIL"]
        n_fail += bool(fails)
        tag = "FAIL" if fails else ("warn" if issues else "ok  ")
        print(f"{tag} {path} ({kind})")
        for lvl, rule, d in issues:
            print(f"     {lvl:4s} {rule}: {d}")
    print(f"{len(items)} prompts, {n_fail} blocked")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
