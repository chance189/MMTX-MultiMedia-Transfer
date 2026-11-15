"""Mounted volume discovery (cross-platform via QStorageInfo)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QStorageInfo

from ..cameras import card_signature
from ..drives import drive_info

_LINUX_SKIP_PREFIX = ("/boot", "/snap", "/proc", "/sys", "/dev", "/var", "/usr", "/etc", "/tmp", "/opt", "/run/user")
_SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "efivarfs", "fuse.snapfuse", "autofs", "cgroup2"}
_NETWORK_FS = {"cifs", "smbfs", "smb3", "nfs", "nfs4", "fuse.sshfs", "fuse.rclone", "9p", "afpfs", "webdav", "davfs", "fuse.davfs2"}
_camera_cache: dict[str, Optional[str]] = {}


def _windows_drive_type(root: str) -> int:
    """GetDriveTypeW: 2 removable, 3 fixed, 4 network, 5 CD-ROM."""
    try:
        import ctypes

        p = os.path.normpath(root)
        if not p.endswith("\\"):
            p += "\\"
        return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(p)))
    except Exception:  # noqa: BLE001
        return 0


@dataclass
class Volume:
    root: str
    name: str
    total: int
    fs: str
    camera: Optional[str]
    read_only: bool
    kind: str = ""


def filesystem_type(path: str) -> str:
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    info = QStorageInfo(p)
    try:
        return bytes(info.fileSystemType()).decode(errors="replace").lower()
    except Exception:  # noqa: BLE001
        return ""


def list_volumes() -> list[Volume]:
    out: list[Volume] = []
    seen = set()
    for v in QStorageInfo.mountedVolumes():
        root = v.rootPath()
        if sys.platform == "win32" and _windows_drive_type(root) in (4, 5):  # network, CD-ROM
            continue
        if not v.isValid() or not v.isReady():
            continue
        fs = bytes(v.fileSystemType()).decode(errors="replace").lower()
        if fs in _SKIP_FS or fs in _NETWORK_FS:
            continue
        if sys.platform.startswith("linux"):
            if root == "/" or (root.startswith(_LINUX_SKIP_PREFIX) and not root.startswith("/run/media")):
                continue
        elif sys.platform == "darwin":
            if root == "/" or root.startswith("/System/") or root.startswith("/private/"):
                continue
        seen.add(root)
        if root not in _camera_cache:
            try:
                _camera_cache[root] = card_signature(root)
            except OSError:
                _camera_cache[root] = None
        cam = _camera_cache[root]
        out.append(Volume(root=os.path.normpath(root), name=v.name() or v.displayName(), total=v.bytesTotal(),
                          fs=fs, camera=cam, read_only=v.isReadOnly(), kind=drive_info(root).label))
    for gone in set(_camera_cache) - seen:  # card pulled: forget it so a new card re-probes
        _camera_cache.pop(gone, None)
    out.sort(key=lambda x: (x.camera is None, x.root.lower()))
    return out
