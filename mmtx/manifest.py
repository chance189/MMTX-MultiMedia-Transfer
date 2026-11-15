"""Checksum manifests.

Each offloaded day folder and each archived folder gets one manifest file at
its root, e.g. ``_mmtx_checksums.xxh128``. The format is the plain
``<hash>  <relative/path>`` layout used by xxhsum / sha256sum / md5sum, so the
folder can also be checked with standard tools (``xxh128sum -c``).

The manifest written at offload time travels with the folder. When the folder
is archived, every file is re-hashed and compared to it, so corruption on the
working drive between offload and archive is caught too.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from .hashing import ALGORITHMS, long_path

MANIFEST_PREFIX = "_mmtx_checksums."
_LINE = re.compile(r"^([0-9a-fA-F]{8,128}) [ *](.+)$")


def manifest_name(algo: str) -> str:
    return MANIFEST_PREFIX + ALGORITHMS[algo][1]


def is_manifest(filename: str) -> bool:
    if not filename.startswith(MANIFEST_PREFIX):
        return False
    ext = filename[len(MANIFEST_PREFIX):]
    return any(ext == v[1] for v in ALGORITHMS.values())


def find_manifest(folder: str) -> Optional[tuple[str, str]]:
    """Return (path, algo) of the manifest in ``folder``, if any."""
    for algo, (_, ext) in ALGORITHMS.items():
        p = os.path.join(folder, MANIFEST_PREFIX + ext)
        if os.path.isfile(long_path(p)):
            return p, algo
    return None


def read_manifest(path: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    with open(long_path(path), "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            m = _LINE.match(line)
            if m:
                entries[m.group(2).replace("\\", "/")] = m.group(1).lower()
    return entries


def load_folder_manifest(folder: str) -> tuple[Optional[str], dict[str, str]]:
    found = find_manifest(folder)
    if not found:
        return None, {}
    path, algo = found
    try:
        return algo, read_manifest(path)
    except OSError:
        return algo, {}


def write_manifest(folder: str, algo: str, entries: dict[str, str], merge: bool = True) -> str:
    """Atomically write (and by default merge into) the folder's manifest."""
    path = os.path.join(folder, manifest_name(algo))
    data = {}
    if merge and os.path.isfile(long_path(path)):
        try:
            data = read_manifest(path)
        except OSError:
            data = {}
    data.update(entries)
    tmp = path + ".tmp"
    with open(long_path(tmp), "w", encoding="utf-8", newline="\n") as f:
        for rel in sorted(data, key=str.lower):
            f.write(f"{data[rel]}  {rel}\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(long_path(tmp), long_path(path))
    return path
