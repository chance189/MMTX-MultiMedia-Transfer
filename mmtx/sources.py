"""Where source files come from.

Normal sources are paths (a memory card, a camera in mass-storage mode, a folder). Phones and
GoPros over USB are MTP devices: Windows shows them in Explorer but they have no drive letter,
so nothing can open() them. Those are addressed as

    portable://<device key>/<storage>/<path inside the storage>

and served by a backend (Windows Portable Devices on Windows, see wpd.py). Everything that
reads a source goes through the few functions here, so the copy engine, scanner and hasher
treat both kinds the same: read once while hashing, write, read the copy back, compare.

On Linux the desktop (gvfs) already mounts phones as folders under /run/user/<uid>/gvfs, so
they are plain paths and need no backend.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

SCHEME = "portable://"
# On a phone only the camera folders matter (the rest is apps and caches, and walking it over
# USB takes ages). Sony in MTP mode exposes PRIVATE/ too.
PORTABLE_TOP_DIRS = {"DCIM", "PRIVATE"}


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


@dataclass
class PortableDevice:
    key: str  # short, stable id used in paths
    name: str  # what people call it: "Pixel 8", "GoPro HERO13 Black", "Apple iPhone"
    maker: str = ""
    storages: list[str] = field(default_factory=list)  # "Internal shared storage", ...

    @property
    def kind(self) -> str:
        return device_kind_for(f"{self.maker} {self.name}")


class Backend:
    """Interface a portable-device backend implements. All paths are storage-relative, '/'-separated."""

    name = "base"

    def devices(self) -> list[PortableDevice]:
        raise NotImplementedError

    def list_dir(self, key: str, rel: str) -> list[Entry]:
        raise NotImplementedError

    def open(self, key: str, rel: str):
        """Binary reader with read(n) and close()."""
        raise NotImplementedError

    def remove(self, key: str, rel: str) -> None:
        raise NotImplementedError


def device_kind_for(text: str) -> str:
    """Device icon family from a maker / model / camera label."""
    low = (text or "").lower()
    if "gopro" in low:
        return "action"
    if "dji" in low:
        return "drone"
    if "insta360" in low:
        return "360"
    if low.startswith("sony") or "ilme" in low or "ilce" in low or "fx3" in low:
        return "cinema"
    if any(k in low for k in ("phone", "apple", "pixel", "samsung", "galaxy", "oneplus", "xiaomi", "motorola",
                              "android", "huawei", "oppo", "nothing")):
        return "phone"
    return "card"


def icon_for_profile(profile: str, label: str = "") -> str:
    from .cameras import PROFILE_KIND

    if profile == "generic" and label:
        return device_kind_for(label)
    return PROFILE_KIND.get(profile, "card")


# ------------------------------------------------------------------ registry

_backends: list[Backend] = []
_devices: dict[str, tuple[Backend, PortableDevice]] = {}
_stat_cache: dict[str, Entry] = {}
_lock = threading.RLock()
_poller: Optional[threading.Thread] = None
_on_change: list[Callable[[], None]] = []


def register_backend(b: Backend) -> None:
    with _lock:
        _backends.append(b)
    refresh_devices()


def unregister_backend(b: Backend) -> None:
    with _lock:
        if b in _backends:
            _backends.remove(b)
    refresh_devices()


def refresh_devices() -> None:
    """Ask every backend what is plugged in. Cheap enough to poll every few seconds off the UI thread."""
    found: dict[str, tuple[Backend, PortableDevice]] = {}
    with _lock:
        backends = list(_backends)
    for b in backends:
        try:
            for d in b.devices():
                found[d.key] = (b, d)
        except Exception:  # noqa: BLE001 - a flaky USB device must never take the app down
            continue
    with _lock:
        _devices.clear()
        _devices.update(found)


def start_polling(interval: float = 3.0) -> None:
    """Keep the device list fresh in the background so the UI never waits on USB."""
    global _poller
    if _poller is not None:
        return

    def run() -> None:
        while True:
            refresh_devices()
            time.sleep(interval)

    _poller = threading.Thread(target=run, name="portable-poll", daemon=True)
    _poller.start()


def portable_devices() -> list[PortableDevice]:
    with _lock:
        return [d for _b, d in _devices.values()]


def portable_roots() -> list[tuple[str, PortableDevice, str]]:
    """(root path, device, storage name) for every storage on every connected device."""
    out = []
    for d in portable_devices():
        for s in d.storages:
            out.append((f"{SCHEME}{d.key}/{s}", d, s))
    return out


# ------------------------------------------------------------------ path helpers


def is_portable(path: str) -> bool:
    return isinstance(path, str) and path.startswith(SCHEME)


def _split(path: str) -> tuple[str, str]:
    """portable://key/storage/a/b -> (key, 'storage/a/b')"""
    rest = path[len(SCHEME):]
    key, _, rel = rest.partition("/")
    return key, rel.strip("/")


def _backend(key: str) -> tuple[Backend, PortableDevice]:
    with _lock:
        hit = _devices.get(key)
    if hit is None:
        raise FileNotFoundError(2, "Device not connected", key)
    return hit


def device_of(path: str) -> Optional[PortableDevice]:
    if not is_portable(path):
        return None
    with _lock:
        hit = _devices.get(_split(path)[0])
    return hit[1] if hit else None


def join(base: str, name: str) -> str:
    if is_portable(base):
        return base.rstrip("/") + "/" + name
    return os.path.join(base, name)


def relpath(path: str, root: str) -> str:
    """Relative path with the OS separator (what the rest of the app expects)."""
    if is_portable(path):
        rel = path[len(root):].strip("/") if path.startswith(root) else path
        return rel.replace("/", os.sep) if rel else "."
    return os.path.relpath(path, root)


def basename(path: str) -> str:
    if is_portable(path):
        return path.rstrip("/").rsplit("/", 1)[-1]
    return os.path.basename(path)


def dirname(path: str) -> str:
    if is_portable(path):
        return path.rstrip("/").rsplit("/", 1)[0]
    return os.path.dirname(path)


# ------------------------------------------------------------------ file operations


@dataclass
class SrcStat:
    st_size: int
    st_mtime: float

    @property
    def st_mtime_ns(self) -> int:
        return int(self.st_mtime * 1e9)


def listdir(path: str) -> list[str]:
    if not is_portable(path):
        return os.listdir(path)
    return [e.name for e in _list(path)]


def _list(path: str) -> list[Entry]:
    key, rel = _split(path)
    b, _d = _backend(key)
    entries = b.list_dir(key, rel)
    with _lock:
        for e in entries:
            _stat_cache[join(path, e.name)] = e
    return entries


def src_stat(path: str):
    if not is_portable(path):
        return os.stat(path)
    with _lock:
        e = _stat_cache.get(path)
    if e is None:
        parent = dirname(path)
        name = basename(path)
        e = next((x for x in _list(parent) if x.name == name), None)
        if e is None:
            raise FileNotFoundError(2, "No such file on the device", path)
    return SrcStat(e.size, e.mtime)


def src_exists(path: str) -> bool:
    if not is_portable(path):
        return os.path.exists(path)
    key, rel = _split(path)
    with _lock:
        if key not in _devices:
            return False
    if "/" not in rel:  # a storage root: connected is enough
        return True
    try:
        src_stat(path)
        return True
    except OSError:
        return False


def src_open(path: str):
    """Binary reader. Local files are opened unbuffered; portable ones stream from the device."""
    if not is_portable(path):
        from .hashing import long_path

        return open(long_path(path), "rb", buffering=0)
    key, rel = _split(path)
    b, _d = _backend(key)
    return b.open(key, rel)


def src_remove(path: str) -> None:
    key, rel = _split(path)
    b, _d = _backend(key)
    b.remove(key, rel)
    with _lock:
        _stat_cache.pop(path, None)


def walk(root: str) -> Iterator[tuple[str, list[str], list[str]]]:
    """os.walk for either kind of root (top-down; callers may prune dirnames in place)."""
    if not is_portable(root):
        yield from os.walk(root)
        return
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = _list(d)
        except OSError:
            continue
        dirs = [e.name for e in entries if e.is_dir]
        if d == root:
            dirs = [x for x in dirs if x.upper() in PORTABLE_TOP_DIRS]
        files = [e.name for e in entries if not e.is_dir]
        yield d, dirs, files
        for x in reversed(dirs):  # dirs may have been pruned by the caller
            stack.append(join(d, x))


# ------------------------------------------------------------------ a folder that behaves like an MTP device


class DirBackend(Backend):
    """Serves local folders as portable devices, only through the Backend interface.
    Used by the tests (and handy for trying the phone flow without a phone)."""

    name = "folder"

    def __init__(self) -> None:
        self.devs: dict[str, tuple[PortableDevice, str]] = {}

    def add(self, key: str, name: str, folder: str, maker: str = "", storage: str = "Internal shared storage") -> None:
        self.devs[key] = (PortableDevice(key=key, name=name, maker=maker, storages=[storage]), folder)

    def unplug(self, key: str) -> None:
        self.devs.pop(key, None)

    def devices(self) -> list[PortableDevice]:
        return [d for d, _f in self.devs.values()]

    def _local(self, key: str, rel: str) -> str:
        d, folder = self.devs[key]
        parts = rel.split("/")
        if not parts or parts[0] not in d.storages:
            raise FileNotFoundError(2, "No such storage", rel)
        return os.path.join(folder, *parts[1:])

    def list_dir(self, key: str, rel: str) -> list[Entry]:
        if key not in self.devs:
            raise FileNotFoundError(2, "Device not connected", key)
        out = []
        for e in os.scandir(self._local(key, rel)):
            st = e.stat()
            out.append(Entry(e.name, e.is_dir(), 0 if e.is_dir() else st.st_size, st.st_mtime))
        return out

    def open(self, key: str, rel: str):
        if key not in self.devs:
            raise FileNotFoundError(2, "Device not connected", key)
        return open(self._local(key, rel), "rb", buffering=0)

    def remove(self, key: str, rel: str) -> None:
        os.remove(self._local(key, rel))


def install_platform_backend(log: Callable[[str], None] = lambda m: None) -> None:
    """Windows: Windows Portable Devices (phones, GoPro over USB, cameras in MTP mode)."""
    if sys.platform != "win32":
        return
    try:
        from .wpd import WpdBackend

        register_backend(WpdBackend(log=log))
        start_polling()
    except Exception as e:  # noqa: BLE001 - no comtypes, or WPD missing (Windows N without Media Feature Pack)
        log(f"Portable devices unavailable: {e}")
