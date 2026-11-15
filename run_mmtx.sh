#!/usr/bin/env bash
# MMTX launcher for Linux/macOS. First run creates a local .venv and installs dependencies.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Setting up MMTX for the first time..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --upgrade pip >/dev/null
    .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m mmtx "$@"
