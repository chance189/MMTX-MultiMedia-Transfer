#!/usr/bin/env bash
# Builds a standalone dist/MMTX/ app for Linux or macOS.
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --name MMTX \
    --icon assets/mmtx.png --add-data "assets:assets" \
    --exclude-module tkinter --collect-submodules mmtx --collect-all imageio_ffmpeg --collect-all pillow_heif launcher.py
printf '#!/bin/sh\nexec "$(dirname "$0")/MMTX" --library "$@"\n' > "dist/MMTX/MMTX Library"
chmod +x "dist/MMTX/MMTX Library"
echo "Built: dist/MMTX/  (Library: dist/MMTX/MMTX Library, or MMTX --library)"
