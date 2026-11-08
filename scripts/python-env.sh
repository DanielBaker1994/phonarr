#!/usr/bin/env bash
# Creates backend/.venv with the gateway's dev dependencies (once), then prints the venv's python path.
# Used by ../dev.sh and ../test.sh.
set -euo pipefail
cd "$(dirname "$0")/../backend"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv >&2
  .venv/bin/pip install -q --upgrade pip >&2
fi
# Reinstall only when the requirements changed.
stamp=.venv/.requirements.sha
want=$(cat requirements.txt requirements-dev.txt | cksum)
if [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then
  .venv/bin/pip install -q -r requirements-dev.txt >&2
  echo "$want" > "$stamp"
fi
echo "$PWD/.venv/bin/python"
