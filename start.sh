#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8768

