#!/usr/bin/env bash
# MMTX Library launcher for Linux/macOS. Optional argument: a library folder to open.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Setting up MMTX for the first time..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip >/dev/null
    .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m mmtx.library "$@"
