"""Checksum algorithms and cache-bypassing file readers.

Read-back verification is only meaningful if the destination is read from the
disk, not from the OS page cache that still holds the bytes we just wrote.
Each platform gets its own bypass:

* Linux:   fsync, then posix_fadvise(DONTNEED) to evict the clean pages.
* macOS:   F_NOCACHE on the write and read descriptors.
* Windows: FILE_FLAG_NO_BUFFERING read with a page-aligned VirtualAlloc buffer.

Any failure to set up the bypass falls back to a normal buffered read, so
verification never silently stops working.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
from typing import Callable, Iterator, Optional

try:
    import xxhash  # type: ignore

    HAVE_XXHASH = True
except Exception:  # pragma: no cover - xxhash is in requirements
    xxhash = None
    HAVE_XXHASH = False

CHUNK = 8 * 1024 * 1024  # 8 MiB; multiple of 4096 so it is valid for unbuffered I/O

# key -> (display name, manifest extension)
ALGORITHMS = {
    "xxh128": ("XXH3-128 (fastest, recommended)", "xxh128"),
    "xxh64": ("XXH64", "xxh64"),
    "sha256": ("SHA-256 (slow, cryptographic)", "sha256"),
    "md5": ("MD5 (legacy)", "md5"),
}


class Cancelled(Exception):
    """Raised when the user cancels a running job."""


def available_algorithms() -> list[str]:
    if HAVE_XXHASH:
        return list(ALGORITHMS)
    return ["sha256", "md5"]


def default_algorithm() -> str:
    return "xxh128" if HAVE_XXHASH else "sha256"


def new_hasher(algo: str):
    if algo == "xxh128":
        if not HAVE_XXHASH:
            raise RuntimeError("xxhash is not installed (pip install xxhash)")
        return xxhash.xxh3_128()
    if algo == "xxh64":
        if not HAVE_XXHASH:
            raise RuntimeError("xxhash is not installed (pip install xxhash)")
        return xxhash.xxh64()
    if algo == "sha256":
        return hashlib.sha256()
    if algo == "md5":
        return hashlib.md5()
    raise ValueError(f"Unknown checksum algorithm: {algo}")


def long_path(p: str) -> str:
    """Windows extended-length path so deep folder trees never hit MAX_PATH."""
    if sys.platform != "win32":
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def drop_cache_fd(fd: int) -> None:
    """Evict a file's clean pages from the page cache (Linux)."""
    if hasattr(os, "posix_fadvise"):
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
        except OSError:
            pass


def set_nocache_fd(fd: int) -> None:
    """Disable caching on a descriptor (macOS)."""
    if sys.platform == "darwin":
        try:
            import fcntl

            fcntl.fcntl(fd, getattr(fcntl, "F_NOCACHE", 48), 1)
        except Exception:
            pass


def _iter_buffered(path: str, chunk: int, uncached: bool) -> Iterator[memoryview]:
    buf = bytearray(chunk)
    mv = memoryview(buf)
    with open(long_path(path), "rb", buffering=0) as f:
        if uncached:
            set_nocache_fd(f.fileno())
            drop_cache_fd(f.fileno())
        while True:
            n = f.readinto(buf)
            if not n:
                break
            yield mv[:n]
        if uncached:
            drop_cache_fd(f.fileno())


def _iter_windows_unbuffered(path: str, chunk: int) -> Iterator[memoryview]:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateFileW = k32.CreateFileW
    CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    CreateFileW.restype = wintypes.HANDLE
    ReadFile = k32.ReadFile
    ReadFile.argtypes = [
        wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
    ]
    ReadFile.restype = wintypes.BOOL
    CloseHandle = k32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    VirtualAlloc = k32.VirtualAlloc
    VirtualAlloc.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
    VirtualAlloc.restype = wintypes.LPVOID
    VirtualFree = k32.VirtualFree
    VirtualFree.argtypes = [wintypes.LPVOID, ctypes.c_size_t, wintypes.DWORD]

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x1
    OPEN_EXISTING = 3
    FILE_FLAG_NO_BUFFERING = 0x20000000
    FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    MEM_COMMIT, MEM_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x1000, 0x2000, 0x8000, 0x04
    INVALID = ctypes.c_void_p(-1).value

    handle = CreateFileW(
        long_path(path), GENERIC_READ, FILE_SHARE_READ, None, OPEN_EXISTING,
        FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN, None,
    )
    if handle is None or handle == INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    buf = VirtualAlloc(None, chunk, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not buf:
        CloseHandle(handle)
        raise MemoryError("VirtualAlloc failed")
    arr = (ctypes.c_char * chunk).from_address(buf)
    mv = memoryview(arr).cast("B")
    try:
        got = wintypes.DWORD(0)
        while True:
            if not ReadFile(handle, buf, chunk, ctypes.byref(got), None):
                raise ctypes.WinError(ctypes.get_last_error())
            n = got.value
            if n == 0:
                break
            yield mv[:n]
            if n < chunk:
                break
    finally:
        try:
            mv.release()
        except Exception:
            pass
        VirtualFree(buf, 0, MEM_RELEASE)
        CloseHandle(handle)


def _chunk_for(path: str, chunk: int) -> int:
    """Don't allocate an 8 MiB buffer to read a 60 KB thumbnail. Stays a multiple of 4096."""
    try:
        size = os.path.getsize(long_path(path))
    except OSError:
        return chunk
    return min(chunk, ((size + 4096) // 4096 + 1) * 4096)


def iter_file(path: str, uncached: bool = True, chunk: int = CHUNK) -> Iterator[memoryview]:
    """Yield the file's bytes, bypassing the OS cache when asked and possible."""
    from .sources import is_portable, src_open

    if is_portable(path):  # a phone or GoPro over USB: stream it from the device
        with src_open(path) as f:
            while True:
                b = f.read(chunk)
                if not b:
                    return
                yield memoryview(b)
    chunk = _chunk_for(path, chunk)
    if uncached and sys.platform == "win32":
        try:
            gen = _iter_windows_unbuffered(path, chunk)
            first = next(gen, None)  # open + first read happen here
        except Exception:
            gen = None
        if gen is not None:
            if first is not None:
                yield first
                yield from gen
            return
    yield from _iter_buffered(path, chunk, uncached)


def hash_file(
    path: str,
    algo: str,
    uncached: bool = True,
    on_bytes: Optional[Callable[[int], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> str:
    h = new_hasher(algo)
    for block in iter_file(path, uncached=uncached):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        h.update(block)
        if on_bytes:
            on_bytes(len(block))
    return h.hexdigest()
