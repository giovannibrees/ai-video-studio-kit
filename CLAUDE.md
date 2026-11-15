# AI Video Studio: guide for Claude Code

This repo makes short AI films (brand films, UGC ads, music videos) with a person approving every step.
Read `README.md` for the flow and `docs/SCRIPTS.md` before writing any script.

## The line (each step is a gate; never start a step before the previous one is approved)
1. Script: `projects/<name>/beats.json` (format in `docs/SCRIPTS.md`, templates in `templates/`).
   Run `python3 studio.py check <name>` and fix every FAIL before showing it.
2. Blocking: add a `sets` floor plan and a `blocking` block per row with people, then `python3 studio.py blocking <name>`.
3. The user approves the script and blocking on the review page (`python3 studio.py review <name>`).
4. `studio.py frames`: one sample first, then the batch after the sample is approved.
5. `studio.py takes`: several 480p draft takes per approved frame, each checked by `clip_qa.py`. A person picks one per shot.
6. `studio.py voices`, the user picks by ear, then `studio.py vo` and `studio.py music`.
7. `studio.py cut`: the draft film. Finals (`studio.py final`) only after the user approves the draft film.

## Rules
- Never spend credits on a step the user has not approved. `ledger.py` enforces sample-before-batch, three rounds per
  shot and a 10% reserve; never work around it.
- Show the cost before every paid step (`plan_check.py` for music videos, the estimate printed by each command).
- Keys live in `.env` only (written by `studio.py setup`). Never print, log or commit them.
- One subject, one action, one camera move per shot. No crowds, signs or writing in any prompt (`prompt_lint.py`).
- Describe every character's look explicitly (age, hair, skin tone, outfit) in `looks`, so the model does not drift.
- One cup per person, every prop listed once, people counted in every frame.
- Watch every take at full speed with `docs/REVIEW_CHECKLIST.md`; `clip_qa.py` catches about two thirds of defects.
- A "changes" note from the review page is added to the next prompt for that item; read it before regenerating.
- Use `--demo` to test any flow for free.
