#!/usr/bin/env bash
# Builds a standalone dist/MMTX/ app for Linux or macOS.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name MMTX \
    --icon assets/mmtx.png --add-data "assets:assets" \
    --exclude-module tkinter --collect-submodules mmtx launcher.py
echo "Built: dist/MMTX/"
