#!/usr/bin/env bash
# AI Video Studio installer: system tools, Python packages, then your name and keys.
set -euo pipefail
cd "$(dirname "$0")"
say() { printf "\n\033[1m%s\033[0m\n" "$*"; }

say "1/4 System tools (ffmpeg; tesseract is optional, for spotting garbled text in clips)"
if ! command -v ffmpeg >/dev/null; then
  if command -v brew >/dev/null; then brew install ffmpeg tesseract
  elif command -v apt-get >/dev/null; then sudo apt-get update && sudo apt-get install -y ffmpeg tesseract-ocr
  else echo "Install ffmpeg yourself (https://ffmpeg.org/download.html), then run this again."; exit 1; fi
else echo "ffmpeg found"; fi

say "2/4 Python 3.10 or newer"
PY=$(command -v python3 || true)
[ -z "$PY" ] && { echo "Install Python 3.10+ first (https://www.python.org/downloads/)."; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ needed"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "packages installed in .venv"

say "3/4 Your name and API keys"
python3 studio.py setup

say "4/4 Free test run (demo mode, no credits)"
python3 studio.py new hello --from examples/first-pour >/dev/null 2>&1 || true
python3 studio.py check hello | tail -1
python3 studio.py blocking hello | tail -1
echo
echo "Done. Next:"
echo "  source .venv/bin/activate"
echo "  python3 studio.py review hello      # open http://localhost:8765 and approve the script"
echo "  python3 studio.py frames hello --demo"
