"""Media helpers for thumbnails: clip duration, video keyframe grabs, RAW previews.

Thumbnail sources are picked cheapest-first:
  * Sony THMBNL/C0001T01.JPG and GoPro .THM are ready-made JPEGs
  * GoPro/Insta360 .LRV, DJI .LRF, Sony SUB/C0001S03.MP4 are low-res proxies:
    decoding one keyframe from them is ~10x cheaper than from 5.3K HEVC
  * the full clip, keyframe-only decode (-skip_frame nokey)
  * RAW (ARW/DNG/GPR/...): the JPEG preview embedded in the file
ffmpeg comes from PATH or the imageio-ffmpeg package. Without it, video tiles
fall back to THM/THMBNL images or a placeholder.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from functools import lru_cache
from typing import Optional

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".avi", ".mts", ".m2ts", ".mkv", ".360", ".insv", ".osv", ".lrv", ".lrf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".thm", ".insp", ".tif", ".tiff", ".bmp", ".webp"}
RAW_EXTS = {".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2", ".gpr"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac"}


def media_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in (".insv", ".360", ".osv"):
        return "360"
    if ext in VIDEO_EXTS:
        return "video"
    if ext == ".insp":
        return "360photo"
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in RAW_EXTS:
        return "raw"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


# ------------------------------------------------------------------ MP4 duration


def mp4_duration(path: str) -> Optional[float]:
    """Read moov/mvhd without decoding anything (a few small seeks, fine on SD cards)."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            pos = 0
            for _ in range(10000):
                if pos + 8 > size:
                    return None
                f.seek(pos)
                hdr = f.read(16)
                if len(hdr) < 8:
                    return None
                box_size, box_type = struct.unpack(">I4s", hdr[:8])
                header = 8
                if box_size == 1:
                    box_size = struct.unpack(">Q", hdr[8:16])[0]
                    header = 16
                elif box_size == 0:
                    box_size = size - pos
                if box_size < header:
                    return None
                if box_type == b"moov":
                    end = pos + box_size
                    child = pos + header
                    while child + 8 <= end:
                        f.seek(child)
                        ch = f.read(8)
                        csize, ctype = struct.unpack(">I4s", ch)
                        if ctype == b"mvhd":
                            body = f.read(32)
                            version = body[0]
                            if version == 1:
                                timescale, duration = struct.unpack(">IQ", body[20:32])
                            else:
                                timescale, duration = struct.unpack(">II", body[12:20])
                            return duration / timescale if timescale else None
                        if csize < 8:
                            return None
                        child += csize
                    return None
                pos += box_size
    except (OSError, struct.error):
        return None
    return None


def format_duration(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    s = int(round(sec))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ------------------------------------------------------------------ ffmpeg


@lru_cache(maxsize=1)
def find_ffmpeg() -> Optional[str]:
    env = os.environ.get("MMTX_FFMPEG")
    if env and os.path.isfile(env):
        return env
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:  # noqa: BLE001
        pass
    return None


def _run(cmd: list[str], timeout: float) -> bytes:
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flashing per thumbnail
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         timeout=timeout, **kw)
    return out.stdout


def video_frame_jpeg(path: str, at: float = 1.0, width: int = 360) -> Optional[bytes]:
    ff = find_ffmpeg()
    if not ff:
        return None
    for t in ([at, 0.0] if at > 0 else [0.0]):
        cmd = [ff, "-v", "error", "-nostdin", "-skip_frame", "nokey", "-ss", f"{t:.2f}", "-i", path,
               "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe",
               "-c:v", "mjpeg", "-q:v", "4", "pipe:1"]
        try:
            data = _run(cmd, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if data[:2] == b"\xff\xd8":
            return data
    return None


def _jpeg_end(data: bytes, start: int) -> Optional[int]:
    """Walk JPEG segments from an SOI; return the offset just past EOI, or None if not a JPEG."""
    n = len(data)
    i = start + 2
    while i + 2 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xD9:  # EOI
            return i + 2
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            i += 2
            continue
        if i + 4 > n:
            return None
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        if seg < 2:
            return None
        i += 2 + seg
        if marker == 0xDA:  # entropy-coded scan: runs until a real marker (FF not followed by 00/RSTn)
            while True:
                i = data.find(b"\xff", i)
                if i == -1 or i + 1 >= n:
                    return None
                nxt = data[i + 1]
                if nxt == 0x00 or 0xD0 <= nxt <= 0xD7:
                    i += 2
                    continue
                break
    return None


def embedded_jpeg(path: str, limit: int = 24 * 1024 * 1024) -> Optional[bytes]:
    """Largest valid JPEG embedded in the first part of a RAW file (the camera's preview)."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return None
    best: Optional[tuple[int, int]] = None
    i = data.find(b"\xff\xd8\xff")
    while i != -1:
        end = _jpeg_end(data, i)
        if end is not None:
            length = end - i
            if length > 20_000 and (best is None or length > best[1]):
                best = (i, length)
            i = data.find(b"\xff\xd8\xff", end)
        else:
            i = data.find(b"\xff\xd8\xff", i + 3)
    return data[best[0]: best[0] + best[1]] if best else None


# ------------------------------------------------------------------ candidate picking


def thumb_candidates(main: str, siblings: list[str]) -> list[tuple[str, str]]:
    """Ordered (path, method) to try for one clip/photo. method: image | video | raw."""
    kind = media_kind(main)
    jpgs = [p for p in siblings if os.path.splitext(p)[1].lower() in (".thm", ".jpg", ".jpeg")]
    proxies = [p for p in siblings if os.path.splitext(p)[1].lower() in (".lrv", ".lrf")
               or "/SUB/" in p.replace("\\", "/").upper()]
    sony_thumbs = [p for p in jpgs if "/THMBNL/" in p.replace("\\", "/").upper()]
    out: list[tuple[str, str]] = []
    if kind in ("video", "360"):
        out += [(p, "image") for p in sony_thumbs]  # Sony's are 640x360, perfect
        out += [(p, "video") for p in proxies]
        out += [(main, "video")]
        out += [(p, "image") for p in jpgs if p not in sony_thumbs]  # GoPro THM: small, last resort
    elif kind in ("photo", "360photo"):
        out.append((main, "image"))
    elif kind == "raw":
        out += [(p, "image") for p in jpgs]  # RAW+JPEG shooting
        out.append((main, "raw"))
    return out


def poster_candidates(files: list[str]) -> list[tuple[str, str]]:
    """Folder poster for the Archive list: a clip from the folder, cheapest source first."""
    by_ext: dict[str, list[str]] = {}
    for p in sorted(files):
        by_ext.setdefault(os.path.splitext(p)[1].lower(), []).append(p)
    out: list[tuple[str, str]] = []
    sony = [p for p in by_ext.get(".jpg", []) if "THMBNL" in p.upper() or p.upper().endswith("T01.JPG")]
    out += [(p, "image") for p in sony[:1]]
    for ext in (".lrv", ".lrf"):
        out += [(p, "video") for p in by_ext.get(ext, [])[:1]]
    for ext in (".mp4", ".mov", ".insv", ".mxf"):
        out += [(p, "video") for p in by_ext.get(ext, [])[:1]]
    out += [(p, "image") for p in by_ext.get(".thm", [])[:1]]
    for ext in (".jpg", ".jpeg", ".insp", ".png"):
        out += [(p, "image") for p in by_ext.get(ext, [])[:1]]
    for ext in RAW_EXTS:
        out += [(p, "raw") for p in by_ext.get(ext, [])[:1]]
    return out[:4]
