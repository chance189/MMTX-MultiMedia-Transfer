"""Google Photos Takeout -> a dated personal library. No Qt in here.

Layout of the library::

    <library>/2019/2019_06/2019_06_23/IMG_1234.jpg
                                     /_google_photos.json     descriptions, places, people, albums
                                     /_mmtx_checksums.xxh128  checksums, same as every MMTX folder
    <library>/Unknown date/...                                 nothing said when it was taken
    <library>/_albums.json                                     album -> files, no extra copies
    <library>/_mmtx_library.db                                 index: content hash -> file

How a takeout goes in:

1. ``plan_takeout`` reads only the file list of every zip (no unpacking) plus any
   folders you already unpacked. Each entry gets a path *inside* the export, so a
   photo in part 001 still finds its sidecar JSON in part 002.
2. Photos are paired with Google's sidecars by name. Takeout mangles those names
   (``.supplemental-metadata`` cut at random lengths, the 46/51 character limit,
   ``(1)`` moved behind the extension, ``-edited`` copies without their own JSON,
   Live Photo videos without one). Anything still unpaired is matched on the
   ``title`` inside the JSON while importing.
3. The shooting date: Google's ``photoTakenTime`` is what Google Photos shows,
   including dates you fixed there, but it is UTC only. When the camera's own
   clock (EXIF) describes the same moment, its wall-clock date decides the day
   folder, so a photo taken at 11 pm in Tokyo stays on that day. When they
   disagree, you changed the date in Google Photos, and Google wins. No JSON:
   camera clock, then a date in the file name, then upload date.
4. ``TakeoutRunner`` reads each photo out of its zip once. Content already in
   the library (album copies, the next takeout of the same account) is only
   hashed, never written. New files are unpacked into a scratch folder next to
   their zip (``_mmtx_unpack_<zip name>``), moved into the library (a rename
   when it's the same drive), read back from disk, compared, then renamed into
   place. Each scratch file is gone the moment it's moved; the scratch folder
   goes when its zip is done. The zips themselves are never touched.
   Originals stay byte-identical: nothing is rewritten inside the photos;
   Google's metadata goes in ``_google_photos.json``.
5. Takeouts get re-downloaded and parts trickle in: a sidecar whose photo came in
   an earlier run (or the other way round) is kept and matched later, and the
   photo moves to its proper day if it was filed by a weaker date.
"""

from __future__ import annotations

import errno
import json
import os
import posixpath
import re
import shutil
import sqlite3
import struct
import threading
import time
import traceback
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .hashing import (
    CHUNK,
    Cancelled,
    default_algorithm,
    drop_cache_fd,
    hash_file,
    long_path as lp,
    new_hasher,
    set_nocache_fd,
)
from .manifest import manifest_name, read_manifest, write_manifest
from .transfer import PARTIAL_SUFFIX, JobResult, RunnerBase, _remove, _retry, human_bytes, same_device

PHOTO_EXT = {
    ".jpg", ".jpeg", ".jpe", ".heic", ".heif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".avif", ".jxl", ".dng", ".raw", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".orf",
    ".rw2", ".raf", ".srw", ".pef", ".x3f", ".ico",
}
VIDEO_EXT = {
    ".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".mpg",
    ".mpeg", ".wmv", ".flv", ".mp", ".ts", ".vob", ".divx",
}
MEDIA_EXT = PHOTO_EXT | VIDEO_EXT

LIBRARY_DB = "_mmtx_library.db"
DAY_META = "_google_photos.json"
ALBUMS_FILE = "_albums.json"
UNKNOWN_DIR = "Unknown date"
HEAD = 256 * 1024  # read this much first to find the camera's clock (EXIF)

# How sure a date is. A photo filed by a weaker source moves when a stronger one turns up.
DATE_RANK = {"unknown": 0, "filedate": 1, "uploaded": 1, "filename": 2, "camera": 3, "google": 4,
             "google+camera": 4}
DATE_LABEL = {
    "google+camera": "Google Photos date, camera clock",
    "google": "Google Photos date",
    "camera": "camera clock",
    "filename": "date in the file name",
    "uploaded": "upload date",
    "filedate": "file's created date",
    "unknown": "unknown",
}

_EXPORT_RE = re.compile(r"^takeout-(\d{8}T\d{6}Z)(?:-(\d+))?-(\d+)$", re.I)
_PRODUCT_RE = re.compile(r"photo|foto|フォト|照片|相片|사진|фото", re.I)
_YEAR_KNOWN = re.compile(
    r"^(photos from|fotos von|photos de|fotos de|foto del|foto dal|foto's uit|foto’s uit|zdjęcia z|"
    r"фото за|fotky z|fotók|fotos|foto) (18|19|20)\d\d\b", re.I)
_YEAR_ANY = re.compile(r"^\D{0,40}?(?<!\d)(18|19|20)\d\d(?!\d)\D{0,12}$")
_TRASH = {"trash", "bin", "papierkorb", "corbeille", "papelera", "cestino", "prullenbak", "lixeira",
          "lixo", "kosz", "корзина", "ゴミ箱", "휴지통", "垃圾桶", "回收站", "papperskorg", "papirkurv",
          "roskakori", "çöp kutusu", "koš"}
_DUP = re.compile(r"^(.*)\((\d+)\)$")
_EDITED = ("-edited", "-bearbeitet", "-modifié", "-modifie", "-modificato", "-editada", "-editado",
           "-bewerkt", "-redigerad", "-redigeret", "-redigert", "-muokattu", "-edytowane", "-upraveno",
           "-upravené", "-szerkesztett", "-editat", "-編集済み", "-편집됨", "-изменено", "-düzenlendi",
           "-已编辑", "-已編輯")
_CUT_EDIT = re.compile(r"^(.*)-(e|ed|edi|edit|edite)$")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FN_DT = re.compile(
    r"(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
    r"(?:[-_ T.]?([01]\d|2[0-3])[-_.:]?([0-5]\d)[-_.:]?([0-5]\d))?")


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------


def media_kind(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return ""


def _split_ext(name: str) -> tuple[str, str]:
    i = name.rfind(".")
    return (name[:i], name[i:]) if i > 0 else (name, "")


def safe_name(name: str) -> str:
    name = _BAD_CHARS.sub("_", name).strip().rstrip(" .")
    return name or "unnamed"


def day_folder(dt: Optional[datetime]) -> str:
    """'2019/2019_06/2019_06_23' (always '/', joined onto the library later)."""
    if dt is None:
        return UNKNOWN_DIR
    y, m, d = dt.year, dt.month, dt.day
    return f"{y:04d}/{y:04d}_{m:02d}/{y:04d}_{m:02d}_{d:02d}"


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def is_year_folder(folder: str) -> bool:
    name = folder.split("/")[0]
    return bool(_YEAR_KNOWN.match(name) or _YEAR_ANY.match(name))


def year_of_folder(folder: str) -> int:
    m = re.search(r"(?<!\d)((?:18|19|20)\d\d)(?!\d)", folder.split("/")[0])
    return int(m.group(1)) if m else 0


def is_trash_folder(folder: str) -> bool:
    return folder.split("/")[0].strip().lower() in _TRASH


# ----------------------------------------------------------------------------
# Dates: EXIF, file name, Google JSON
# ----------------------------------------------------------------------------


def _parse_exif_dt(s: Optional[str]) -> Optional[datetime]:
    if not s or len(s) < 19 or s.startswith("0000"):
        return None
    try:
        dt = datetime.strptime(s[:19].replace("-", ":"), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    return dt if 1900 < dt.year < 2100 else None


def _parse_offset(s: Optional[str]) -> Optional[int]:
    m = re.match(r"^([+-])(\d\d):?(\d\d)$", (s or "").strip())
    if not m:
        return None
    mins = int(m.group(2)) * 60 + int(m.group(3))
    return -mins if m.group(1) == "-" else mins


def _tiff_dates(b: bytes, t: int):
    try:
        bo = b[t:t + 2]
        if bo == b"II":
            e = "<"
        elif bo == b"MM":
            e = ">"
        else:
            return None
        if struct.unpack_from(e + "H", b, t + 2)[0] != 42:
            return None

        def ifd(off: int) -> dict:
            n = struct.unpack_from(e + "H", b, t + off)[0]
            if n > 600:
                return {}
            out = {}
            for k in range(n):
                p = t + off + 2 + 12 * k
                tag, typ, cnt = struct.unpack_from(e + "HHI", b, p)
                out[tag] = (typ, cnt, p + 8)
            return out

        def text(ent) -> Optional[str]:
            typ, cnt, p = ent
            if typ != 2 or cnt > 64:
                return None
            if cnt > 4:
                p = t + struct.unpack_from(e + "I", b, p)[0]
            raw = b[p:p + cnt]
            if len(raw) < cnt:
                return None
            return raw.split(b"\x00")[0].decode("ascii", "ignore").strip()

        d0 = ifd(struct.unpack_from(e + "I", b, t + 4)[0])
        if 0x8769 not in d0:
            return None
        ex = ifd(struct.unpack_from(e + "I", b, d0[0x8769][2])[0])
        for tag, otag in ((0x9003, 0x9011), (0x9004, 0x9012)):
            if tag in ex:
                dt = _parse_exif_dt(text(ex[tag]))
                if dt:
                    off = _parse_offset(text(ex[otag])) if otag in ex else None
                    return dt, off
    except (struct.error, IndexError, KeyError, ValueError):
        return None
    return None


def exif_datetime(head: bytes) -> Optional[tuple[datetime, Optional[int]]]:
    """(camera wall-clock time, UTC offset in minutes or None) from a photo's first bytes.
    Works for JPEG, HEIC/AVIF (the Exif item) and TIFF-based raws (DNG, ARW, NEF...)."""
    starts = [0] if head[:4] in (b"II*\x00", b"MM\x00*") else []
    i = head.find(b"Exif\x00\x00")
    while i != -1 and len(starts) < 6:
        starts.append(i + 6)
        i = head.find(b"Exif\x00\x00", i + 6)
    for t in starts:
        r = _tiff_dates(head, t)
        if r:
            return r
    return None


def filename_datetime(name: str) -> Optional[datetime]:
    """IMG_20190623_172854, PXL_20240902_141512531, Screenshot_2019-06-23-17-28-54, IMG-20190623-WA0001..."""
    stem = os.path.splitext(name)[0]
    for m in _FN_DT.finditer(stem):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4) is None:
            nxt = stem[m.end():m.end() + 1]
            if nxt.isdigit():
                continue
            h = mi = s = 0
        else:
            h, mi, s = int(m.group(4)), int(m.group(5)), int(m.group(6))
        try:
            return datetime(y, mo, d, h, mi, s)
        except ValueError:
            continue
    return None


_QT_EPOCH = 2082844800  # seconds from 1904-01-01 (QuickTime) to 1970-01-01
_ISO_DT = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?")
_CREATIONDATE_KEY = b"com.apple.quicktime.creationdate"


def _parse_iso(s: str) -> Optional[tuple[datetime, Optional[int]]]:
    m = _ISO_DT.match((s or "").strip())
    if not m:
        return None
    try:
        dt = datetime(*(int(m.group(i)) for i in range(1, 7)))
    except ValueError:
        return None
    if not 1971 < dt.year < 2100:
        return None
    z = m.group(7)
    off = 0 if z == "Z" else _parse_offset(z) if z else None
    return dt, off


def video_datetime(f, size: int) -> Optional[tuple[datetime, Optional[int]]]:
    """When a MOV/MP4 was recorded, from inside the file (what Explorer calls "Media created").
    Apple's creationdate carries the local time and zone; otherwise mvhd's UTC time is shown in this
    computer's zone. ``f`` must be seekable: the moov box is usually at the end, after the video data."""

    def boxes(start: int, end: int):
        pos = start
        while pos + 8 <= end:
            f.seek(pos)
            h = f.read(16)
            if len(h) < 8:
                return
            sz, typ = struct.unpack(">I4s", h[:8])
            hdr = 8
            if sz == 1:
                if len(h) < 16:
                    return
                sz, hdr = struct.unpack(">Q", h[8:16])[0], 16
            elif sz == 0:
                sz = end - pos
            if sz < hdr:
                return
            yield typ, pos + hdr, min(pos + sz, end)
            pos += sz

    try:
        moov = next(((a, b) for t, a, b in boxes(0, size) if t == b"moov"), None)
        if moov is None:
            return None
        apple = mvhd = None
        for typ, a, b in boxes(*moov):
            if typ == b"mvhd":
                f.seek(a)
                h = f.read(12)
                secs = struct.unpack(">Q", h[4:12])[0] if h[0] == 1 else struct.unpack(">I", h[4:8])[0]
                if secs > _QT_EPOCH + 86400 * 366:
                    mvhd = secs - _QT_EPOCH
            elif typ == b"meta" and b - a < 1 << 20:  # QuickTime metadata: keys + ilst
                keys: list[bytes] = []
                items: dict[int, bytes] = {}
                for t2, a2, b2 in boxes(a, b):
                    f.seek(a2)
                    body = f.read(b2 - a2)
                    if t2 == b"keys" and len(body) >= 8:
                        n, p = struct.unpack(">I", body[4:8])[0], 8
                        for _ in range(min(n, 256)):
                            ks = struct.unpack(">I", body[p:p + 4])[0]
                            keys.append(body[p + 8:p + ks])
                            p += ks
                    elif t2 == b"ilst":
                        p = 0
                        while p + 8 <= len(body):
                            isz, idx = struct.unpack(">II", body[p:p + 8])
                            if isz < 8:
                                break
                            d = body[p + 8:p + isz]
                            if d[4:8] == b"data":
                                items[idx] = d[16:struct.unpack(">I", d[:4])[0]]
                            p += isz
                for i, k in enumerate(keys, 1):
                    if k == _CREATIONDATE_KEY and i in items:
                        apple = _parse_iso(items[i].decode("utf-8", "ignore"))
            elif typ == b"udta" and b - a < 1 << 20 and apple is None:
                for t2, a2, b2 in boxes(a, b):
                    if t2 == b"\xa9day":
                        f.seek(a2)
                        body = f.read(b2 - a2)
                        apple = _parse_iso(body[4:].decode("utf-8", "ignore")) or apple
        if apple:
            return apple
        if mvhd:
            local = datetime.fromtimestamp(mvhd)
            off = round((local - datetime(1970, 1, 1) - timedelta(seconds=mvhd)).total_seconds() / 60)
            return local, off
    except (struct.error, OSError, ValueError, IndexError, EOFError, zipfile.BadZipFile, RuntimeError):
        return None
    return None


def file_datetime(path: str) -> Optional[tuple[datetime, float]]:
    """The file's own date: the earlier of created and modified. Copying a file gives the copy a new
    created date but keeps the modified one, so the earlier of the two is the closer to the truth."""
    try:
        st = os.stat(lp(path))
    except OSError:
        return None
    cands = [st.st_mtime]
    bt = getattr(st, "st_birthtime", None)  # Windows (3.12+), macOS
    if bt:
        cands.append(bt)
    elif os.name == "nt":
        cands.append(st.st_ctime)  # creation time on Windows
    good = [c for c in cands if c and c > 86400 * 366]
    if not good:
        return None
    ts = min(good)
    return datetime.fromtimestamp(ts), ts


def set_file_times(path: str, ts: float) -> None:
    """Modified (and on Windows also created) = when the photo was taken, so Explorer and Finder sort
    the library by shooting date. macOS moves the created date back by itself when modified is earlier."""
    try:
        os.utime(lp(path), (ts, ts))
    except OSError:
        return
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        h = k32.CreateFileW(lp(path), 0x100, 0x7, None, 3, 0x02000000, None)  # FILE_WRITE_ATTRIBUTES
        if not h or h == wintypes.HANDLE(-1).value:
            return
        try:
            ft = int(ts * 10_000_000) + 116444736000000000
            created = wintypes.FILETIME(ft & 0xFFFFFFFF, ft >> 32)
            k32.SetFileTime(h, ctypes.byref(created), None, None)
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        pass


def place_no_overwrite(src: str, dst: str) -> None:
    """Rename src to dst, failing with FileExistsError instead of replacing anything already at dst."""
    if os.name == "nt":
        _retry(os.rename, lp(src), lp(dst))  # Windows rename never replaces
        return
    try:
        os.link(lp(src), lp(dst))  # atomic; refuses an existing name
    except FileExistsError:
        raise
    except OSError:  # no hard links here (exFAT, some NAS shares)
        if os.path.lexists(lp(dst)):
            raise FileExistsError(errno.EEXIST, "already exists", dst) from None
        os.rename(lp(src), lp(dst))
        return
    os.unlink(lp(src))


def _json_ts(data: dict, key: str) -> int:
    try:
        v = int((data.get(key) or {}).get("timestamp") or 0)
    except (TypeError, ValueError, AttributeError):
        return 0
    return v if v > 0 else 0


def is_sidecar(data) -> bool:
    return isinstance(data, dict) and ("photoTakenTime" in data or ("creationTime" in data and "url" in data))


@dataclass
class Taken:
    local: Optional[datetime]  # wall clock that decides the day folder
    ts: Optional[float]  # the instant, for file times
    source: str  # key of DATE_RANK

    @property
    def rank(self) -> int:
        return DATE_RANK.get(self.source, 0)


def resolve_date(data: Optional[dict], camera, name: str, fdate: Optional[tuple[datetime, float]] = None) -> Taken:
    """Pick the day a photo or video belongs to. ``camera`` is (wall clock, UTC offset or None) from
    the photo's EXIF or the date recorded inside a video; ``fdate`` is the file's own created date
    (loose files only). See the module docstring for the reasoning."""
    jts = _json_ts(data, "photoTakenTime") if data else 0
    if jts:
        if camera:
            wall, off = camera
            utc = datetime(1970, 1, 1) + timedelta(seconds=jts)
            if off is not None:
                same = abs((wall - timedelta(minutes=off) - utc).total_seconds()) <= 120
            else:  # unknown zone: same moment if it differs by whole quarter hours, within ±14 h
                diff = (wall - utc).total_seconds()
                rem = abs(diff) % 900
                same = abs(diff) <= 14 * 3600 + 120 and (rem <= 90 or rem >= 810)
            if same:
                return Taken(wall, float(jts), "google+camera")
        return Taken(datetime.fromtimestamp(jts), float(jts), "google")
    if camera:
        wall, off = camera
        if off is not None:
            ts = (wall - timedelta(minutes=off)).replace(tzinfo=timezone.utc).timestamp()
        else:
            ts = time.mktime(wall.timetuple())
        return Taken(wall, ts, "camera")
    fn = filename_datetime(name)
    if fn:
        return Taken(fn, time.mktime(fn.timetuple()), "filename")
    cts = _json_ts(data, "creationTime") if data else 0
    if cts:
        return Taken(datetime.fromtimestamp(cts), float(cts), "uploaded")
    if fdate:
        return Taken(fdate[0], fdate[1], "filedate")
    return Taken(None, None, "unknown")


# ----------------------------------------------------------------------------
# Sidecar name matching
# ----------------------------------------------------------------------------


def _json_key(jname: str) -> Optional[tuple[str, int]]:
    """'IMG.jpg.supplemental-metada(1).json' -> ('img.jpg.supplemental-metada', 1)."""
    low = jname.lower()
    if not low.endswith(".json"):
        return None
    base = low[:-5]
    m = _DUP.match(base)
    if m:
        return m.group(1), int(m.group(2))
    return base, 0


def _targets(name: str) -> list[tuple[str, int, bool]]:
    """Names a media file's sidecar could be built from: (name, duplicate number, is edited copy)."""
    low = name.lower()
    stem, ext = _split_ext(low)
    out: list[tuple[str, int, bool]] = []
    cores: list[tuple[str, int]] = []
    m = _DUP.match(stem)
    if m:
        out.append((m.group(1) + ext, int(m.group(2)), False))  # IMG(1).jpg -> IMG.jpg(1).json
        cores.append((m.group(1), int(m.group(2))))
    out.append((low, 0, False))
    cores.append((stem, 0))
    for core, n in cores:
        hit = next((s for s in _EDITED if core.endswith(s)), None)
        if hit:
            out.append((core[:-len(hit)] + ext, n, True))
        elif len(core) >= 40:  # name cut at Takeout's limit, the edit suffix with it
            mm = _CUT_EDIT.match(core)
            if mm:
                out.append((mm.group(1) + ext, n, True))
    return out


def _fits(jbase: str, target: str) -> int:
    if jbase == target or jbase.startswith(target + "."):
        return 3  # IMG.jpg.json, IMG.jpg.supplemental-metadata.json, IMG.jpg.suppl.json ...
    stem = _split_ext(target)[0]
    if jbase == stem:
        return 1  # very old exports: IMG.json
    if len(jbase) >= 40 and (target + ".supplemental-metadata").startswith(jbase):
        return 2  # whole name cut at the 46/51 character limit
    return 0


def match_folder(media: list[str], jsons: list[str]) -> dict[str, tuple[str, str]]:
    """Pair media names with sidecar JSON names that sit in the same folder.
    Returns media name -> (json name, how) with how in name | edited | pair."""
    idx: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for j in jsons:
        k = _json_key(j)
        if k:
            idx[k[1]].append((k[0], j))
    out: dict[str, tuple[str, str]] = {}
    for name in media:
        best = None
        for rank, (target, n, edited) in enumerate(_targets(name)):
            for jbase, jname in idx.get(n, ()):
                s = _fits(jbase, target)
                if s:
                    score = (s, -rank, len(jbase))
                    if best is None or score > best[0]:
                        best = (score, jname, "edited" if edited else "name")
        if best:
            out[name] = (best[1], best[2])
    # Live Photos / motion photos: IMG_1234.MOV shares IMG_1234.HEIC's sidecar
    stems: dict[str, str] = {}
    for name in media:
        if name in out and out[name][1] == "name":
            stems.setdefault(name.lower().split(".")[0], out[name][0])
    for name in media:
        if name not in out:
            j = stems.get(name.lower().split(".")[0])
            if j:
                out[name] = (j, "pair")
    return out


def title_matches(title: str, name: str) -> bool:
    """The JSON's title is the original upload name; the file on disk may be cut or numbered."""
    t = (title or "").lower()
    if not t:
        return False
    for target, _n, _e in _targets(name):
        if t == target:
            return True
        ts, te = _split_ext(t)
        gs, ge = _split_ext(target)
        if te == ge and len(gs) >= 30 and ts.startswith(gs):
            return True
    return False


# ----------------------------------------------------------------------------
# Indexing the takeouts
# ----------------------------------------------------------------------------


@dataclass
class Member:
    source: str  # the zip, or the folder that was added
    name: str  # entry name inside the zip, or the file's absolute path
    vpath: str  # path below the Google Photos folder, '/' separated
    size: int
    zipped: bool
    offset: int = 0

    @property
    def folder(self) -> str:
        return posixpath.dirname(self.vpath)

    @property
    def base(self) -> str:
        return posixpath.basename(self.vpath)


@dataclass
class Archive:
    path: str
    size: int
    zipped: bool
    export: str
    part: int = 0
    key: str = ""
    members: list = field(default_factory=list)
    media: int = 0
    media_bytes: int = 0
    jsons: int = 0
    other: int = 0  # files from other Google products (Drive, Mail...) or stray files
    trash: int = 0
    error: str = ""
    imported_before: bool = False

    @property
    def label(self) -> str:
        return os.path.basename(self.path.rstrip("\\/")) or self.path


@dataclass
class Item:
    m: Member
    archive: Archive
    kind: str
    export: str
    json: Optional[Member] = None
    how: str = ""  # name | edited | pair | title | orphan
    album: str = ""
    year_hint: int = 0
    edited: bool = False
    trash: bool = False
    predated: bool = False  # camera/fdate below were read while planning (loose files without a sidecar)
    camera: Optional[tuple] = None  # (wall clock, UTC offset) from EXIF or inside the video
    fdate: Optional[tuple] = None  # the file's own created date

    @property
    def name(self) -> str:
        return self.m.base

    @property
    def folder(self) -> str:
        return self.m.folder


@dataclass
class TakeoutPlan:
    library: str
    archives: list = field(default_factory=list)
    items: list = field(default_factory=list)  # to import
    unclaimed: dict = field(default_factory=dict)  # (export, folder) -> [Member] sidecars nobody claimed by name
    exports: dict = field(default_factory=dict)  # export -> sorted part numbers
    years: dict = field(default_factory=dict)  # year (0 = albums only) -> [photos, videos, bytes]
    photos: int = 0
    videos: int = 0
    bytes: int = 0
    copies: int = 0  # album copies of photos that are in a year folder too
    copies_bytes: int = 0
    in_library: int = 0  # look like they're in the library already (same name and size)
    in_library_bytes: int = 0
    no_json: int = 0
    trash: int = 0
    other: int = 0
    skipped_archives: int = 0
    skipped_loose: int = 0  # loose files that went in before and haven't changed since
    warnings: list = field(default_factory=list)

    @property
    def new_bytes_estimate(self) -> int:
        return max(0, self.bytes - self.copies_bytes - self.in_library_bytes)


def _is_photos_product(part: str) -> bool:
    return bool(_PRODUCT_RE.search(part))


def _takeout_at(parts: list[str]) -> int:
    """Index of the 'Takeout' folder near the top ('takeout-2026.../Takeout/...' works too), else -1."""
    for k, p in enumerate(parts[:3]):
        if p.lower() == "takeout":
            return k
    return -1


def _vpath(parts: list[str], takeout_root: bool) -> Optional[str]:
    """Path below 'Takeout/Google Photos/'. None = belongs to another Google product."""
    k = _takeout_at(parts)
    if k >= 0:
        parts = parts[k + 1:]
        if len(parts) >= 2 and _is_photos_product(parts[0]):
            return "/".join(parts[1:])
        return None
    if takeout_root:
        return None
    if len(parts) >= 2 and _is_photos_product(parts[0]):
        return "/".join(parts[1:])
    return "/".join(parts)


def archive_key(path: str, size: int) -> str:
    return f"{os.path.basename(path).lower()}|{size}"


def _export_of(path: str) -> tuple[str, int]:
    stem = os.path.splitext(os.path.basename(path.rstrip("\\/")))[0]
    m = _EXPORT_RE.match(stem)
    if m:
        exp = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
        return exp, int(m.group(3))
    return stem, 0


def _skip_dir(parent: str, name: str, library: str) -> bool:
    """Folders a source walk never enters: MMTX's own scratch folders, and the library's year folders
    (the takeout folder may well sit inside the library, or next to it)."""
    if name.startswith("_mmtx_unpack_"):
        return True
    if library and _norm(parent) == _norm(library):
        return bool(re.fullmatch(r"(18|19|20)\d\d", name)) or name == UNKNOWN_DIR
    return False


def find_takeouts(paths: list[str], library: str = "") -> list[str]:
    """Expand what the user added: zip files; a folder holding zips and/or loose photos and videos
    (a .MOV saved straight from Google, a hand-unpacked part); already unpacked Takeout folders."""
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        k = _norm(p)
        if k not in seen:
            seen.add(k)
            out.append(p)

    for p in paths:
        if os.path.isfile(p):
            if p.lower().endswith(".zip"):
                add(p)
            elif media_kind(p):
                add(p)  # a single loose photo or video
            continue
        if not os.path.isdir(p):
            continue
        if os.path.basename(p.rstrip("\\/")).lower() == "takeout":
            add(p)
            continue
        base_depth = p.rstrip("\\/").count(os.sep)
        loose = False
        for dirpath, dirnames, filenames in os.walk(p):
            depth = dirpath.rstrip("\\/").count(os.sep) - base_depth
            dirnames[:] = [d for d in sorted(dirnames) if not _skip_dir(dirpath, d, library)]
            for fn in sorted(filenames):
                if fn.lower().endswith(".zip"):
                    add(os.path.join(dirpath, fn))
            for d in list(dirnames):
                if d.lower() == "takeout":
                    add(os.path.join(dirpath, d))
                    dirnames.remove(d)
            if depth >= 3:
                dirnames[:] = []
            if any(media_kind(f) for f in filenames):
                loose = True
        if loose:
            add(p)  # the loose photos and videos in it (zips and Takeout folders are read on their own)
    return out


def index_archive(path: str, library: str = "") -> Archive:
    if os.path.isfile(path) and not path.lower().endswith(".zip"):  # one loose file
        size = os.path.getsize(path)
        a = Archive(path=path, size=size, zipped=False, export=_norm(os.path.dirname(path)), key="")
        a.members.append(Member(path, path, os.path.basename(path), size, False))
    elif os.path.isfile(path):
        size = os.path.getsize(path)
        export, part = _export_of(path)
        a = Archive(path=path, size=size, zipped=True, export=export, part=part, key=archive_key(path, size))
        try:
            with zipfile.ZipFile(lp(path)) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
        except (zipfile.BadZipFile, OSError) as e:
            a.error = f"can't open: {e}"
            return a
        split = [[x for x in i.filename.replace("\\", "/").split("/") if x] for i in infos]
        root = any(_takeout_at(p[:-1]) >= 0 for p in split)
        for i, parts in zip(infos, split):
            vp = _vpath(parts, root)
            if vp is None or not vp:
                if not i.filename.lower().endswith("archive_browser.html"):
                    a.other += 1
                continue
            a.members.append(Member(path, i.filename, vp, i.file_size, True, i.header_offset))
    else:
        a = Archive(path=path, size=0, zipped=False, export=_norm(path), key="")
        root_is_takeout = os.path.basename(path.rstrip("\\/")).lower() == "takeout"
        for dirpath, dirnames, filenames in os.walk(path):
            # Takeout folders found inside are read as sources of their own; zips are skipped below
            dirnames[:] = [d for d in sorted(dirnames) if not _skip_dir(dirpath, d, library)
                           and (root_is_takeout or d.lower() != "takeout")]
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, path).replace(os.sep, "/")
                parts = rel.split("/")
                if root_is_takeout:
                    parts = ["Takeout"] + parts
                vp = _vpath(parts, root_is_takeout)
                if vp is None or fn.lower().endswith(".zip"):
                    if not fn.lower().endswith((".zip", "archive_browser.html")):
                        a.other += 1
                    continue
                try:
                    sz = os.path.getsize(full)
                except OSError:
                    continue
                a.members.append(Member(path, full, vp, sz, False))
                a.size += sz
    for m in a.members:
        k = media_kind(m.base)
        if k:
            a.media += 1
            a.media_bytes += m.size
            if is_trash_folder(m.folder):
                a.trash += 1
        elif m.base.lower().endswith(".json"):
            a.jsons += 1
        else:
            a.other += 1
    return a


def plan_takeout(
    paths: list[str],
    library: str,
    include_trash: bool = False,
    reimport: bool = False,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> TakeoutPlan:
    plan = TakeoutPlan(library=library)
    sources = find_takeouts(paths, library)
    done_keys: set[str] = set()
    done_loose: dict[str, tuple[int, int]] = {}
    lib_names: dict[tuple[str, int], int] = {}
    if library and os.path.isfile(os.path.join(library, LIBRARY_DB)):
        try:
            with Library(library, read_only=True) as lib:
                done_keys = lib.archive_keys()
                done_loose = lib.loose_index()
                lib_names = lib.name_size_index()
        except (sqlite3.Error, OSError):
            pass
    for i, src in enumerate(sources):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if on_progress:
            on_progress(i, len(sources), os.path.basename(src))
        a = index_archive(src, library)
        if a.error:
            plan.warnings.append(f"{a.label}: {a.error}")
        a.imported_before = bool(a.key) and a.key in done_keys and not reimport
        plan.archives.append(a)
    plan.archives.sort(key=lambda a: (a.export, a.part, a.path.lower()))

    # exports and missing parts
    for a in plan.archives:
        if a.zipped and a.part:
            plan.exports.setdefault(a.export, []).append(a.part)
    for exp, parts in plan.exports.items():
        parts.sort()
        missing = [n for n in range(1, parts[-1] + 1) if n not in parts]
        if missing:
            have = ", ".join(str(n) for n in parts)
            plan.warnings.append(
                f"Export {_pretty_export(exp)}: part {', '.join(f'{n:03d}' for n in missing)} missing "
                f"(have {have}). Photos whose sidecar is in a missing part still go in; their details "
                "are filled in when you add that part later.")

    # group by (export, folder) across parts
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {"media": [], "json": {}})
    for a in plan.archives:
        if a.error:
            continue
        for m in a.members:
            g = groups[(a.export, m.folder)]
            if media_kind(m.base):
                g["media"].append((m, a))
            elif m.base.lower().endswith(".json"):
                g["json"].setdefault(m.base, m)
    year_names: dict[tuple[str, str, int], int] = {}  # (export, name, size) seen in a year folder
    for (export, folder), g in groups.items():
        if is_year_folder(folder) or not folder:
            for m, _a in g["media"]:
                year_names[(export, m.base.lower(), m.size)] = year_of_folder(folder)

    for (export, folder), g in sorted(groups.items()):
        names = [m.base for m, _a in g["media"]]
        pairs = match_folder(names, list(g["json"]))
        claimed = {j for j, _how in pairs.values()}
        un = [m for jn, m in g["json"].items() if jn not in claimed]
        if un:
            plan.unclaimed[(export, folder)] = un
        trash = is_trash_folder(folder)
        album = "" if (is_year_folder(folder) or not folder or trash) else folder.split("/")[0]
        yr = year_of_folder(folder) if not album else 0
        for m, a in g["media"]:
            if trash and not include_trash:
                plan.trash += 1
                continue
            if a.imported_before:
                continue
            if not a.zipped and not reimport and done_loose:
                fp = done_loose.get(_norm(m.name))
                if fp is not None:
                    try:
                        st = os.stat(lp(m.name))
                        if fp == (st.st_size, st.st_mtime_ns):
                            plan.skipped_loose += 1
                            continue
                    except OSError:
                        pass
            jn, how = pairs.get(m.base, (None, ""))
            it = Item(m=m, archive=a, kind=media_kind(m.base), export=export,
                      json=g["json"].get(jn) if jn else None, how=how, album=album, year_hint=yr,
                      edited=how == "edited", trash=trash)
            plan.items.append(it)
            if it.json is None:
                plan.no_json += 1
                if not a.zipped:  # a loose file: date it now (cheap, it's on disk) for the preview
                    _predate(it)
                    t = resolve_date(None, it.camera, it.name, it.fdate)
                    if t.local and not yr:
                        it.year_hint = t.local.year
            if it.kind == "video":
                plan.videos += 1
            else:
                plan.photos += 1
            plan.bytes += m.size
            key = (export, m.base.lower(), m.size)
            if album and key in year_names:
                plan.copies += 1
                plan.copies_bytes += m.size
            elif (m.base.lower(), m.size) in lib_names:
                plan.in_library += 1
                plan.in_library_bytes += m.size
            else:
                y = yr or year_names.get(key, 0) or it.year_hint
                row = plan.years.setdefault(y, [0, 0, 0])
                row[0 if it.kind == "photo" else 1] += 1
                row[2] += m.size
    plan.other = sum(a.other for a in plan.archives)
    plan.skipped_archives = sum(1 for a in plan.archives if a.imported_before)
    # read order: zip by zip, front to back
    plan.items.sort(key=lambda it: (it.archive.path.lower(), it.m.offset, it.m.name))
    return plan


def _predate(it: "Item") -> None:
    """The dates a loose file carries itself: EXIF or the recording date inside a video, and the file's
    own created date."""
    try:
        with open(lp(it.m.name), "rb") as f:
            if it.kind == "photo":
                it.camera = exif_datetime(f.read(HEAD))
            else:
                it.camera = video_datetime(f, it.m.size)
    except OSError:
        it.camera = None
    it.fdate = file_datetime(it.m.name)
    it.predated = True


def _pretty_export(exp: str) -> str:
    m = re.match(r"^(\d{4})(\d\d)(\d\d)T(\d\d)(\d\d)", exp)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}" if m else exp


# ----------------------------------------------------------------------------
# The library index
# ----------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS items(
    hash TEXT PRIMARY KEY, size INTEGER, path TEXT, taken REAL, day TEXT, date_source TEXT,
    name TEXT, edited INTEGER, meta TEXT, export TEXT, vpath TEXT, added REAL);
CREATE INDEX IF NOT EXISTS items_size ON items(size);
CREATE INDEX IF NOT EXISTS items_path ON items(path);
CREATE TABLE IF NOT EXISTS albums(hash TEXT, album TEXT, PRIMARY KEY(hash, album));
CREATE TABLE IF NOT EXISTS archives(key TEXT PRIMARY KEY, path TEXT, size INTEGER, export TEXT,
    part INTEGER, imported REAL, media INTEGER, new INTEGER);
CREATE TABLE IF NOT EXISTS orphans(export TEXT, folder TEXT, jname TEXT, data TEXT,
    PRIMARY KEY(export, folder, jname));
CREATE TABLE IF NOT EXISTS pending(hash TEXT PRIMARY KEY, export TEXT, folder TEXT, name TEXT);
CREATE TABLE IF NOT EXISTS loose(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, hash TEXT);
"""


class Library:
    """SQLite index at the library root. The files and the per-day JSON are the archive;
    this is what makes "is this photo already in there?" instant."""

    def __init__(self, root: str, algo: Optional[str] = None, read_only: bool = False):
        self.root = root
        path = os.path.join(root, LIBRARY_DB)
        if read_only:
            self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        else:
            os.makedirs(lp(root), exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.executescript(_SCHEMA)
        row = self.db.execute("SELECT v FROM meta WHERE k='algo'").fetchone() if self._has_meta() else None
        if row:
            self.algo = row[0]
        else:
            self.algo = algo or default_algorithm()
            if not read_only:
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('algo', ?)", (self.algo,))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('layout', 'YYYY/YYYY_MM/YYYY_MM_DD')")
                self.db.commit()

    def _has_meta(self) -> bool:
        return bool(self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone())

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        try:
            self.db.commit()
        except sqlite3.Error:
            pass
        self.db.close()

    def archive_keys(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT key FROM archives")}

    def loose_index(self) -> dict[str, tuple[int, int]]:
        """Loose files (not in a zip) that went in: path -> (size, modified ns)."""
        try:
            return {p: (sz, mt) for p, sz, mt in self.db.execute("SELECT path, size, mtime FROM loose")}
        except sqlite3.Error:  # a library from before loose files were remembered
            return {}

    def name_size_index(self) -> dict[tuple[str, int], int]:
        out: dict[tuple[str, int], int] = {}
        for vpath, name, size in self.db.execute("SELECT vpath, name, size FROM items"):
            for n in {posixpath.basename(vpath or "").lower(), (name or "").lower()}:
                if n:
                    out[(n, size)] = 1
        return out

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def stats(self) -> dict:
        n, b = self.db.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM items").fetchone()
        days = self.db.execute("SELECT COUNT(DISTINCT day) FROM items WHERE day != ''").fetchone()[0]
        lo, hi = self.db.execute("SELECT MIN(day), MAX(day) FROM items WHERE day != ''").fetchone()
        albums = self.db.execute("SELECT COUNT(DISTINCT album) FROM albums").fetchone()[0]
        zips = self.db.execute("SELECT COUNT(*) FROM archives").fetchone()[0]
        return {"files": n, "bytes": b, "days": days, "first": lo or "", "last": hi or "",
                "albums": albums, "zips": zips}


def library_stats(root: str) -> Optional[dict]:
    if not root or not os.path.isfile(os.path.join(root, LIBRARY_DB)):
        return None
    try:
        with Library(root, read_only=True) as lib:
            return lib.stats()
    except sqlite3.Error:
        return None


# ----------------------------------------------------------------------------
# The import runner
# ----------------------------------------------------------------------------


@dataclass
class TakeoutOptions:
    algo: str = ""  # "" = the library's own (or the default for a new library)
    verify_readback: bool = True
    uncached: bool = True
    workers: int = 2  # zips read side by side
    # Unpack each zip through a scratch folder next to it (_mmtx_unpack_<zip name>). Every file is
    # deleted from scratch the moment it's in the library; the folder goes when that zip is done.
    # Off: stream straight from the zip into the library. The zips themselves are never deleted.
    scratch: bool = True


@dataclass
class TakeoutResult(JobResult):
    library: str = ""
    new: int = 0
    duplicates: int = 0
    restored: int = 0
    refiled: int = 0  # moved to a better day once their sidecar turned up
    details_added: int = 0
    no_date: int = 0
    days: int = 0
    albums: int = 0
    date_sources: dict = field(default_factory=dict)


_KEEP_META = ("title", "description", "photoTakenTime", "creationTime", "geoData", "geoDataExif",
              "people", "url", "googlePhotosOrigin", "favorited", "archived", "trashed", "imageViews")


def _meta_of(data: Optional[dict]) -> dict:
    if not data:
        return {}
    return {k: data[k] for k in _KEEP_META if k in data}


def _empty(v) -> bool:
    if isinstance(v, dict) and ("latitude" in v or "longitude" in v):
        return not (v.get("latitude") or v.get("longitude"))
    return v in (None, "", [], {}, 0, "0")


def merge_meta(old: dict, new: dict, newer: bool) -> dict:
    """Album copies and later takeouts carry the same photo's details again: keep the fullest set.
    A newer takeout wins outright (you may have edited a caption since)."""
    if newer:
        out = dict(old)
        out.update({k: v for k, v in new.items() if not _empty(v) or k not in out})
        return out
    out = dict(old)
    for k, v in new.items():
        if k not in out or (_empty(out[k]) and not _empty(v)):
            out[k] = v
    return out


def scratch_dir_for(zip_path: str) -> str:
    stem = os.path.splitext(os.path.basename(zip_path))[0]
    return os.path.join(os.path.dirname(os.path.abspath(zip_path)), f"_mmtx_unpack_{stem}")


class TakeoutRunner(RunnerBase):
    def __init__(self, plan: TakeoutPlan, opts: Optional[TakeoutOptions] = None, emit=None, log_dir=None):
        super().__init__(emit, log_dir)
        self.plan = plan
        self.opt = opts or TakeoutOptions()
        self.root = plan.library
        self.lib: Optional[Library] = None
        self.algo = ""
        self.by_hash: dict[str, str] = {}  # hash -> library-relative path
        self.sizes: set[int] = set()
        self.reserved: dict[str, threading.Event] = {}
        self.claimed: set[str] = set()  # final paths taken during this run
        self.used_json: set[tuple[str, str]] = set()
        self.used_orphans: set[tuple[str, str, str]] = set()
        self.json_cache: dict[tuple[str, str], dict] = {}
        self.touched: set[str] = set()  # day folders (relative) whose manifest/metadata changed
        self.manifest_add: dict[str, dict[str, str]] = defaultdict(dict)
        self.manifest_drop: dict[str, set] = defaultdict(set)
        self.made_dirs: set[str] = set()
        self.arch_fail: dict[str, int] = defaultdict(int)
        self.arch_new: dict[str, int] = defaultdict(int)
        self.arch_done: dict[str, int] = defaultdict(int)
        self.scratch: dict[str, Optional[str]] = {}  # zip -> its scratch folder (None = stream directly)
        self.scratch_same: dict[str, bool] = {}  # scratch folder on the library's drive?
        self._seq = 0
        self._db_lock = threading.Lock()
        self._zips_tl = threading.local()
        self._pending_writes = 0
        self.result = TakeoutResult(kind="takeout", title="Google Photos takeout", library=self.root)

    # ---------------------------------------------------------------- zip access
    def _zip(self, path: str) -> zipfile.ZipFile:
        cache = getattr(self._zips_tl, "zips", None)
        if cache is None:
            cache = self._zips_tl.zips = {}
        zf = cache.get(path)
        if zf is None:
            zf = cache[path] = zipfile.ZipFile(lp(path))
        return zf

    def _close_zips(self) -> None:
        for zf in (getattr(self._zips_tl, "zips", None) or {}).values():
            try:
                zf.close()
            except Exception:  # noqa: BLE001
                pass
        self._zips_tl.zips = {}

    def _open(self, m: Member):
        if m.zipped:
            return self._zip(m.source).open(m.name)
        return open(lp(m.name), "rb")

    def _read_json(self, m: Member) -> dict:
        key = (m.source, m.name)
        with self._lock:
            if key in self.json_cache:
                return self.json_cache[key]
        try:
            with self._open(m) as f:
                data = json.loads(f.read(4 * 1024 * 1024).decode("utf-8", "replace"))
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        with self._lock:
            self.json_cache[key] = data
        return data

    # ---------------------------------------------------------------- scratch folder per zip
    def _scratch_for(self, m: Member) -> Optional[str]:
        if not self.opt.scratch or not m.zipped:
            return None
        with self._lock:
            if m.source in self.scratch:
                return self.scratch[m.source]
        d: Optional[str] = scratch_dir_for(m.source)
        try:
            if os.path.isdir(lp(d)):  # left over from an interrupted run
                shutil.rmtree(lp(d), ignore_errors=True)
            os.makedirs(lp(d), exist_ok=True)
            same = same_device(d, self.root)
        except OSError as e:
            self.log("warn", f"Can't make a scratch folder next to {m.source} ({e}); "
                             "unpacking straight into the library instead")
            d, same = None, False
        with self._lock:
            self.scratch[m.source] = d
            if d:
                self.scratch_same[d] = same
        if d:
            self.log("info", f"Scratch folder for {os.path.basename(m.source)}: {d}"
                             + ("" if same else " (other drive: each photo is copied over, then deleted here)"))
        return d

    def _drop_scratch(self, zip_path: str) -> None:
        with self._lock:
            d = self.scratch.get(zip_path)
        if d and os.path.isdir(lp(d)):
            shutil.rmtree(lp(d), ignore_errors=True)

    # ---------------------------------------------------------------- sidecar for one item
    def _sidecar(self, it: Item) -> tuple[Optional[dict], str]:
        if it.json is not None:
            data = self._read_json(it.json)
            if is_sidecar(data):
                with self._lock:
                    self.used_json.add((it.export, it.json.vpath))
                return data, it.how
        # the title inside sidecars nobody claimed by name (same folder, any part)
        for jm in self.plan.unclaimed.get((it.export, it.folder), ()):
            data = self._read_json(jm)
            if is_sidecar(data) and title_matches(data.get("title", ""), it.name):
                with self._lock:
                    self.used_json.add((it.export, jm.vpath))
                return data, "title"
        # a sidecar that came in an earlier run without its photo
        with self._db_lock:
            rows = self.lib.db.execute(
                "SELECT jname, data FROM orphans WHERE export=? AND folder=?", (it.export, it.folder)).fetchall()
        if rows:
            pairs = match_folder([it.name], [r[0] for r in rows])
            if it.name in pairs:
                jn = pairs[it.name][0]
                try:
                    data = json.loads(next(r[1] for r in rows if r[0] == jn))
                except (ValueError, StopIteration):
                    data = {}
                if is_sidecar(data):
                    with self._lock:
                        self.used_orphans.add((it.export, it.folder, jn))
                    return data, "orphan"
        return None, ""

    def _video_date(self, it: Item):
        try:
            with self._open(it.m) as vf:  # zip entries seek too (forward, by unpacking)
                return video_datetime(vf, it.m.size)
        except (OSError, zipfile.BadZipFile, RuntimeError, EOFError, ValueError):
            return None

    # ---------------------------------------------------------------- paths
    def _abs(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def _final_name(self, it: Item, data: Optional[dict]) -> str:
        disk = it.name
        title = (data or {}).get("title") or ""
        ds, de = _split_ext(disk)
        core = _DUP.match(ds)
        core_s = core.group(1) if core else ds
        if title and not it.edited and it.how in ("name", "title", "orphan"):
            ts, te = _split_ext(title)
            if te.lower() == de.lower() and ts.lower().startswith(core_s.lower()) and len(ts) > len(core_s):
                return safe_name(ts + de)  # Takeout cut the name; the JSON has the whole thing
        if core and it.how in ("name", "title", "orphan", "edited"):
            ds = core_s  # Takeout's own "(1)" numbering; clashes get _1 like everywhere in MMTX
        return safe_name(ds + de)

    def _claim(self, day_rel: str, name: str, size: int, avoid: Optional[set] = None) -> tuple[str, Optional[str]]:
        """Pick a free final name in the day folder. A file already there with that name and size
        (an interrupted run) comes back as the second value, to be compared, never overwritten."""
        d = self._abs(day_rel)
        stem, ext = _split_ext(name)
        i = 0
        while True:
            cand = name if i == 0 else f"{stem}_{i}{ext}"
            i += 1
            full = os.path.join(d, cand)
            key = _norm(full)
            if avoid and key in avoid:
                continue
            with self._lock:
                if key in self.claimed:
                    continue
                if not os.path.exists(lp(full)):
                    self.claimed.add(key)
                    return full, None
                if size >= 0 and f"{day_rel}/{cand}" not in self._known_paths:
                    try:
                        same = os.path.getsize(lp(full)) == size
                    except OSError:
                        same = False
                    if same:
                        self.claimed.add(key)
                        return full, full

    def _unclaim(self, path: str) -> None:
        with self._lock:
            self.claimed.discard(_norm(path))

    def _ensure_dir(self, day_rel: str) -> str:
        d = self._abs(day_rel)
        if not os.path.isdir(lp(d)):
            os.makedirs(lp(d), exist_ok=True)
            with self._lock:
                self.made_dirs.add(day_rel)
        return d

    # ---------------------------------------------------------------- streaming
    def _pump(self, f, head: bytes, h, out=None, tick: bool = True) -> int:
        n_total = 0

        def put(b) -> None:
            nonlocal n_total
            h.update(b)
            if out is not None:
                mv = memoryview(b)
                while mv:
                    n = out.write(mv)
                    mv = mv[n:]
            n_total += len(b)
            if tick:
                self.tick(len(b))
            elif self._stopping():
                raise Cancelled()

        if head:
            put(head)
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            put(b)
        return n_total

    def _hash_stream(self, f, head: bytes) -> str:
        h = new_hasher(self.algo)
        self._pump(f, head, h)
        return h.hexdigest()

    def _write_stream(self, f, head: bytes, dst: str, tick: bool = True) -> str:
        h = new_hasher(self.algo)
        fd = os.open(lp(dst), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o666)
        with os.fdopen(fd, "wb", buffering=0) as out:
            if self.opt.uncached:
                set_nocache_fd(out.fileno())
            n = self._pump(f, head, h, out, tick)
            out.flush()
            os.fsync(out.fileno())
            if self.opt.uncached:
                drop_cache_fd(out.fileno())
        with self._lock:
            self.result.bytes_written += n
        return h.hexdigest()

    def _land(self, it: Item, f, head: bytes, partial: str, tick: bool = True) -> str:
        """Get the photo's bytes to ``partial`` inside the library; returns their checksum.
        With scratch on, the bytes go zip -> scratch folder next to the zip -> library."""
        scratch = self._scratch_for(it.m)
        if not scratch:
            return self._write_stream(f, head, partial, tick)
        with self._lock:
            self._seq += 1
            tmp = os.path.join(scratch, f"{self._seq:07d}_{safe_name(it.name)}{PARTIAL_SUFFIX}")
        try:
            hx = self._write_stream(f, head, tmp, tick)
            if self.scratch_same.get(scratch):
                _retry(os.replace, lp(tmp), lp(partial))  # same drive: a rename, nothing copied
            else:
                h = new_hasher(self.algo)
                with open(lp(tmp), "rb") as src:
                    fd = os.open(lp(partial), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o666)
                    with os.fdopen(fd, "wb", buffering=0) as out:
                        self._pump(src, b"", h, out, tick=False)
                        out.flush()
                        os.fsync(out.fileno())
                        if self.opt.uncached:
                            drop_cache_fd(out.fileno())
                if h.hexdigest() != hx:
                    raise IOError("the copy out of the scratch folder came out different")
            return hx
        finally:
            if os.path.exists(lp(tmp)):
                try:
                    _remove(lp(tmp))
                except OSError:
                    pass

    # ---------------------------------------------------------------- one photo
    def _process(self, it: Item) -> None:
        self.set_file("Copying", it.name, it.m.size, src=it.m.vpath)
        data, how = self._sidecar(it)
        if how:
            it.how = how
        f = self._open(it.m)
        partial = ""
        target: Optional[str] = None
        try:
            head = f.read(HEAD) if it.kind == "photo" else f.read(64 * 1024)
            if it.predated:
                camera, fdate = it.camera, it.fdate
            else:
                fdate = None if it.m.zipped else file_datetime(it.m.name)
                if it.kind == "photo":
                    camera = exif_datetime(head)
                elif not it.m.zipped or not _json_ts(data or {}, "photoTakenTime"):
                    camera = self._video_date(it)  # recorded date inside the video
                else:
                    camera = None
            taken = resolve_date(data, camera, it.name, fdate)
            day_rel = day_folder(taken.local)
            final_name = self._final_name(it, data)
            avoid: set[str] = set()
            with self._lock:
                size_known = it.m.size in self.sizes
            existing = None
            if not size_known:
                target, existing = self._claim(day_rel, final_name, it.m.size)
            if size_known or existing:
                # Same size as something already here: hash first, write only if it's really new.
                hx = self._hash_stream(f, head)
                f.close()
                f = None
                if existing:
                    if hash_file(existing, self.algo, uncached=False) == hx:
                        self._adopt(it, data, taken, hx, existing)
                        return
                    self._unclaim(existing)
                    avoid.add(_norm(existing))
                    target = None
                state = self._known(it, data, taken, hx)
                if state == "dup":
                    return
                if self._reserve(hx) is None:  # another zip is saving this exact content right now
                    self._dup_after_wait(it, data, taken, hx)
                    return
                try:
                    while target is None:
                        t, ex = self._claim(day_rel, final_name, it.m.size, avoid)
                        if ex:
                            if hash_file(ex, self.algo, uncached=False) == hx:
                                self._adopt(it, data, taken, hx, ex)
                                return
                            self._unclaim(ex)
                            avoid.add(_norm(ex))
                            continue
                        target = t
                    self._ensure_dir(day_rel)
                    partial = target + PARTIAL_SUFFIX
                    with self._open(it.m) as f2:
                        got = self._land(it, f2, b"", partial, tick=False)
                    if got != hx:
                        raise IOError("the zip gave different bytes on the second read")
                    self._commit(it, data, taken, hx, partial, target, restoring=state == "missing")
                    partial = ""
                finally:
                    self._release(hx)
                return
            # A size nothing else has: unpack while hashing, one pass.
            self._ensure_dir(day_rel)
            partial = target + PARTIAL_SUFFIX
            hx = self._land(it, f, head, partial)
            f.close()
            f = None
            state = self._known(it, data, taken, hx)
            if state == "dup":  # a same-size twin landed meanwhile
                return
            if self._reserve(hx) is None:
                _remove(lp(partial))
                partial = ""
                self._unclaim(target)
                self._dup_after_wait(it, data, taken, hx)
                return
            try:
                self._commit(it, data, taken, hx, partial, target, restoring=state == "missing")
                partial = ""
            finally:
                self._release(hx)
        except Cancelled:
            raise
        except OSError as e:
            if e.errno == errno.ENOSPC or getattr(e, "winerror", None) in (39, 112):
                self.result.aborted = "A drive is full (the library or the scratch folder next to the zips)."
                self.abort_event.set()
                raise Cancelled() from e
            self._fail(it, str(e))
        except (zipfile.BadZipFile, RuntimeError, EOFError) as e:
            self._fail(it, f"damaged inside the zip ({e}); download that part again")
        finally:
            if f is not None:
                try:
                    f.close()
                except Exception:  # noqa: BLE001
                    pass
            if partial:
                try:
                    _remove(lp(partial))
                except OSError:
                    pass
                if target:
                    self._unclaim(target)
            self.clear_file()

    def _place(self, partial: str, target: str, hx: str) -> tuple[Optional[str], Optional[str]]:
        """Rename the checked file into place without ever replacing anything. If something got to
        that name first, compare checksums: the same file means there's nothing to do (returns
        (None, that file)); a different one means this one takes the next free name."""
        day_rel = os.path.relpath(os.path.dirname(target), self.root).replace(os.sep, "/")
        name = os.path.basename(target)
        avoid: set[str] = set()
        while True:
            try:
                place_no_overwrite(partial, target)
                return target, None
            except FileExistsError:
                pass
            self._unclaim(target)
            avoid.add(_norm(target))
            try:
                if os.path.getsize(lp(target)) == os.path.getsize(lp(partial)) and \
                        hash_file(target, self.algo, uncached=False) == hx:
                    return None, target
            except OSError:
                pass
            target, twin = self._claim(day_rel, name, -1, avoid)

    def _fail(self, it: Item, why: str) -> None:
        with self._lock:
            self.result.files_failed += 1
            self.result.errors.append(f"{it.m.vpath}  ({it.archive.label}): {why}")
            self.arch_fail[it.archive.path] += 1
            self.files_done += 1
        self.log("error", f"{it.m.vpath}: {why}")
        self.emit("photo", {"name": it.name, "state": "failed", "kind": it.kind, "size": it.m.size,
                            "day": "", "path": "", "archive": it.archive.path})

    def _reserve(self, hx: str) -> Optional[threading.Event]:
        with self._lock:
            if hx in self.reserved:
                return None
            ev = self.reserved[hx] = threading.Event()
            return ev

    def _release(self, hx: str) -> None:
        with self._lock:
            ev = self.reserved.pop(hx, None)
        if ev is not None:
            ev.set()

    def _dup_after_wait(self, it: Item, data, taken: Taken, hx: str) -> None:
        while True:
            with self._lock:
                ev = self.reserved.get(hx)
            if ev is None or ev.wait(0.2):
                break
            if self._stopping():
                raise Cancelled()
        if self._known(it, data, taken, hx) != "dup":
            self._fail(it, "an identical copy from another zip failed to save; run the import again")

    # ---------------------------------------------------------------- bookkeeping
    @property
    def _known_paths(self) -> set:
        return self._paths

    def _remember_loose(self, it: Item, hx: str) -> None:
        if it.m.zipped:
            return
        try:
            st = os.stat(lp(it.m.name))
        except OSError:
            return
        with self._db_lock:
            self.lib.db.execute("INSERT OR REPLACE INTO loose VALUES(?,?,?,?)",
                                (_norm(it.m.name), st.st_size, st.st_mtime_ns, hx))

    def _known(self, it: Item, data, taken: Taken, hx: str) -> Optional[str]:
        """'dup': content already in the library (albums and better details recorded, nothing written).
        'missing': it was, but the file is gone from the library, so it goes back in. None: new."""
        with self._lock:
            rel = self.by_hash.get(hx)
        if rel is None:
            return None
        if not os.path.isfile(lp(self._abs(rel))):
            with self._lock:
                self.by_hash.pop(hx, None)
                self._paths.discard(rel)
            with self._db_lock:
                self.lib.db.execute("DELETE FROM items WHERE hash=?", (hx,))
            return "missing"
        self._merge_existing(it, data, taken, hx, rel)
        self._remember_loose(it, hx)
        with self._lock:
            self.result.duplicates += 1
            self.result.files_skipped += 1
            self.result.files_ok += 1
            self.files_done += 1
            self.arch_done[it.archive.path] += 1
        self.emit("photo", {"name": it.name, "state": "dup", "kind": it.kind, "size": it.m.size,
                            "day": rel.rsplit("/", 1)[0], "path": self._abs(rel), "archive": it.archive.path})
        return "dup"

    def _merge_existing(self, it: Item, data, taken: Taken, hx: str, rel: str) -> None:
        day_rel = rel.rsplit("/", 1)[0]
        upgrade = False
        with self._db_lock:
            db = self.lib.db
            row = db.execute("SELECT date_source, meta, export FROM items WHERE hash=?", (hx,)).fetchone()
            if it.album:
                cur = db.execute("INSERT OR IGNORE INTO albums VALUES(?, ?)", (hx, it.album))
                if cur.rowcount:
                    with self._lock:
                        self.touched.add(day_rel)
            if row is None:
                return
            old_src, old_meta, old_export = row
            if data:
                try:
                    cur_meta = json.loads(old_meta or "{}")
                except ValueError:
                    cur_meta = {}
                newer = it.export > (old_export or "") and bool(cur_meta)
                merged = merge_meta(cur_meta, _meta_of(data), newer)
                if merged != cur_meta:
                    db.execute("UPDATE items SET meta=?, export=? WHERE hash=?",
                               (json.dumps(merged, ensure_ascii=False), max(it.export, old_export or ""), hx))
                    db.execute("DELETE FROM pending WHERE hash=?", (hx,))
                    with self._lock:
                        self.touched.add(day_rel)
                        if not cur_meta:
                            self.result.details_added += 1
            upgrade = taken.rank > DATE_RANK.get(old_src, 0) and taken.rank >= DATE_RANK["google"]
        if upgrade:
            self._refile(hx, taken)

    def _refile(self, hx: str, taken: Taken) -> None:
        """A better date turned up (its sidecar arrived): file the photo under the right day."""
        with self._db_lock:
            row = self.lib.db.execute("SELECT path FROM items WHERE hash=?", (hx,)).fetchone()
        if not row:
            return
        rel = row[0]
        new_day = day_folder(taken.local)
        old_dir, name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
        day_str = taken.local.strftime("%Y-%m-%d") if taken.local else ""
        if old_dir == new_day:
            with self._db_lock:
                self.lib.db.execute("UPDATE items SET date_source=?, taken=?, day=? WHERE hash=?",
                                    (taken.source, taken.ts, day_str, hx))
            with self._lock:
                self.touched.add(old_dir)
            return
        src = self._abs(rel)
        if not os.path.isfile(lp(src)):
            return
        self._ensure_dir(new_day)
        dst, _ = self._claim(new_day, name, -1)
        while True:
            try:
                place_no_overwrite(src, dst)
                break
            except FileExistsError:
                self._unclaim(dst)
                dst, _ = self._claim(new_day, name, -1, {_norm(dst)})
        new_rel = f"{new_day}/{os.path.basename(dst)}"
        if taken.ts:
            set_file_times(dst, taken.ts)
        with self._db_lock:
            self.lib.db.execute("UPDATE items SET path=?, day=?, date_source=?, taken=? WHERE hash=?",
                                (new_rel, day_str, taken.source, taken.ts, hx))
        with self._lock:
            self.by_hash[hx] = new_rel
            self._paths.discard(rel)
            self._paths.add(new_rel)
            self.manifest_add[old_dir].pop(name, None)
            self.manifest_add[new_day][os.path.basename(dst)] = hx
            self.manifest_drop[old_dir].add(name)
            self.touched.update({old_dir, new_day})
            self.result.refiled += 1
        self.log("info", f"Moved {rel} -> {new_rel} (its Google Photos date turned up)")

    def _adopt(self, it: Item, data, taken: Taken, hx: str, path: str) -> None:
        """An earlier run wrote this exact file but never recorded it (crash, power cut)."""
        rel = os.path.relpath(path, self.root).replace(os.sep, "/")
        self._record(it, data, taken, hx, rel)
        with self._lock:
            self.result.files_skipped += 1
            self.result.duplicates += 1
            self.result.files_ok += 1
            self.files_done += 1
        self.emit("photo", {"name": it.name, "state": "dup", "kind": it.kind, "size": it.m.size,
                            "day": rel.rsplit("/", 1)[0], "path": path, "archive": it.archive.path})

    def _commit(self, it: Item, data, taken: Taken, hx: str, partial: str, target: str,
                restoring: bool = False) -> None:
        if self.opt.verify_readback:
            self.set_file("Verifying", it.name, it.m.size, src=it.m.vpath)
            if hash_file(partial, self.algo, uncached=self.opt.uncached) != hx:
                self.log("warn", f"{it.m.vpath}: read-back didn't match, unpacking it again")
                with self._open(it.m) as f2:
                    again = self._land(it, f2, b"", partial, tick=False)
                if again != hx or hash_file(partial, self.algo, uncached=self.opt.uncached) != hx:
                    raise IOError("read-back checksum mismatch twice; the library drive may be failing")
        target, twin = self._place(partial, target, hx)
        if twin:  # an identical file turned up at that name meanwhile: nothing to add
            _remove(lp(partial))
            self._adopt(it, data, taken, hx, twin)
            return
        if taken.ts:
            set_file_times(target, taken.ts)
        rel = os.path.relpath(target, self.root).replace(os.sep, "/")
        self._record(it, data, taken, hx, rel)
        with self._lock:
            self.result.new += 1
            self.result.files_copied += 1
            self.result.files_ok += 1
            self.files_done += 1
            self.arch_new[it.archive.path] += 1
            if restoring:
                self.result.restored += 1
        self.emit("photo", {"name": it.name, "state": "new", "kind": it.kind, "size": it.m.size,
                            "day": rel.rsplit("/", 1)[0], "path": target, "archive": it.archive.path,
                            "year": taken.local.year if taken.local else 0})

    def _record(self, it: Item, data, taken: Taken, hx: str, rel: str) -> None:
        day_rel = rel.rsplit("/", 1)[0]
        day_str = taken.local.strftime("%Y-%m-%d") if taken.local else ""
        title = (data or {}).get("title") or it.name
        with self._db_lock:
            db = self.lib.db
            db.execute(
                "INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (hx, it.m.size, rel, taken.ts, day_str, taken.source, title, int(it.edited),
                 json.dumps(_meta_of(data), ensure_ascii=False), it.export, it.m.vpath, time.time()))
            if it.album:
                db.execute("INSERT OR IGNORE INTO albums VALUES(?, ?)", (hx, it.album))
            if not data:
                db.execute("INSERT OR REPLACE INTO pending VALUES(?,?,?,?)", (hx, it.export, it.folder, it.name))
            if not it.m.zipped:
                try:
                    st = os.stat(lp(it.m.name))
                    db.execute("INSERT OR REPLACE INTO loose VALUES(?,?,?,?)",
                               (_norm(it.m.name), st.st_size, st.st_mtime_ns, hx))
                except OSError:
                    pass
            self._pending_writes += 1
            if self._pending_writes >= 200:
                db.commit()
                self._pending_writes = 0
        with self._lock:
            self.by_hash[hx] = rel
            self._paths.add(rel)
            self.sizes.add(it.m.size)
            self.manifest_add[day_rel][rel.rsplit("/", 1)[-1]] = hx
            self.touched.add(day_rel)
            self.arch_done[it.archive.path] += 1
            self.result.date_sources[taken.source] = self.result.date_sources.get(taken.source, 0) + 1
            if taken.local is None:
                self.result.no_date += 1

    # ---------------------------------------------------------------- after the photos
    def _late_sidecars(self) -> None:
        """Sidecars nobody used: match them to photos imported earlier without one, else keep for later."""
        by_group: dict[tuple[str, str], list[tuple[str, dict]]] = defaultdict(list)
        for (export, folder), members in self.plan.unclaimed.items():
            for m in members:
                if (export, m.vpath) in self.used_json:
                    continue
                data = self._read_json(m)
                if is_sidecar(data):
                    by_group[(export, folder)].append((m.base, data))
        for (export, folder), sidecars in by_group.items():
            with self._db_lock:
                pend = self.lib.db.execute(
                    "SELECT hash, name FROM pending WHERE export=? AND folder=?", (export, folder)).fetchall()
            pairs = match_folder([p[1] for p in pend], [s[0] for s in sidecars]) if pend else {}
            used: set[str] = set()
            for hx, pname in pend:
                if pname not in pairs:
                    continue
                jn = pairs[pname][0]
                data = next(d for n, d in sidecars if n == jn)
                used.add(jn)
                with self._db_lock:
                    row = self.lib.db.execute("SELECT path, date_source FROM items WHERE hash=?", (hx,)).fetchone()
                    if not row:
                        continue
                    self.lib.db.execute("UPDATE items SET meta=? WHERE hash=?",
                                        (json.dumps(_meta_of(data), ensure_ascii=False), hx))
                    self.lib.db.execute("DELETE FROM pending WHERE hash=?", (hx,))
                with self._lock:
                    self.touched.add(row[0].rsplit("/", 1)[0])
                    self.result.details_added += 1
                exif = None
                try:
                    with open(lp(self._abs(row[0])), "rb") as fh:
                        exif = exif_datetime(fh.read(HEAD))
                except OSError:
                    pass
                taken = resolve_date(data, exif, pname)
                if taken.rank > DATE_RANK.get(row[1], 0):
                    self._refile(hx, taken)
            with self._db_lock:
                for jn, data in sidecars:
                    if jn not in used:
                        self.lib.db.execute("INSERT OR REPLACE INTO orphans VALUES(?,?,?,?)",
                                            (export, folder, jn, json.dumps(data, ensure_ascii=False)))
        with self._db_lock:
            for export, folder, jn in self.used_orphans:
                self.lib.db.execute("DELETE FROM orphans WHERE export=? AND folder=? AND jname=?",
                                    (export, folder, jn))

    def _write_folder_files(self) -> None:
        """Checksums and Google's details per day folder, the album list at the root."""
        for day_rel in sorted(self.touched):
            d = self._abs(day_rel)
            if not os.path.isdir(lp(d)):
                continue
            self._write_day_meta(day_rel)
            adds = dict(self.manifest_add.get(day_rel, {}))
            drops = set(self.manifest_drop.get(day_rel, ()))
            meta_path = os.path.join(d, DAY_META)
            if os.path.isfile(lp(meta_path)):  # the details file is checksummed like the photos
                adds[DAY_META] = hash_file(meta_path, self.algo, uncached=False)
            else:
                drops.add(DAY_META)
            mpath = os.path.join(d, manifest_name(self.algo))
            if os.path.isfile(lp(mpath)) and drops & set(read_manifest(mpath)):
                cur = read_manifest(mpath)
                for n in drops:
                    cur.pop(n, None)
                cur.update(adds)
                write_manifest(d, self.algo, cur, merge=False)
            elif adds:
                write_manifest(d, self.algo, adds, merge=True)
            self._prune(day_rel)
        self._write_albums()

    def _write_day_meta(self, day_rel: str) -> None:
        d = self._abs(day_rel)
        like = day_rel.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"
        with self._db_lock:
            rows = self.lib.db.execute(
                "SELECT hash, path, name, taken, day, date_source, edited, meta FROM items "
                "WHERE path LIKE ? ESCAPE '\\'", (like,)).fetchall()
            alb: dict[str, list[str]] = defaultdict(list)
            for hx, a in self.lib.db.execute(
                    "SELECT hash, album FROM albums WHERE hash IN "
                    "(SELECT hash FROM items WHERE path LIKE ? ESCAPE '\\')", (like,)):
                alb[hx].append(a)
        files: dict[str, dict] = {}
        for hx, rel, name, taken, _day, src, edited, meta in rows:
            if rel.rsplit("/", 1)[0] != day_rel:
                continue
            try:
                g = json.loads(meta or "{}")
            except ValueError:
                g = {}
            e: dict = {"original_name": name}
            if taken:
                e["taken_utc"] = datetime.fromtimestamp(taken, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            e["date_from"] = DATE_LABEL.get(src, src)
            if g.get("description"):
                e["description"] = g["description"]
            geo = g.get("geoData") or {}
            if not (geo.get("latitude") or geo.get("longitude")):
                geo = g.get("geoDataExif") or {}
            if geo.get("latitude") or geo.get("longitude"):
                e["location"] = {k: geo.get(k) for k in ("latitude", "longitude", "altitude") if k in geo}
            people = [p.get("name") for p in (g.get("people") or []) if isinstance(p, dict) and p.get("name")]
            if people:
                e["people"] = people
            if alb.get(hx):
                e["albums"] = sorted(alb[hx])
            if g.get("favorited"):
                e["favorite"] = True
            if g.get("archived"):
                e["archived"] = True
            if edited:
                e["edited_copy"] = True
            if g.get("url"):
                e["google_photos_url"] = g["url"]
            e["checksum"] = f"{self.algo}:{hx}"
            files[rel.rsplit("/", 1)[-1]] = e
        path = os.path.join(d, DAY_META)
        if not files:
            if os.path.isfile(lp(path)):
                _remove(lp(path))
            return
        doc = {"note": "Written by MMTX from your Google Photos takeout. The photos themselves are untouched.",
               "files": dict(sorted(files.items(), key=lambda kv: kv[0].lower()))}
        tmp = path + ".tmp"
        with open(lp(tmp), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=1)
        os.replace(lp(tmp), lp(path))

    def _prune(self, day_rel: str) -> None:
        """A day folder left without photos (moved away, or everything was a duplicate):
        remove our files in it, then the empty month and year folders above it."""
        d = self._abs(day_rel)
        try:
            names = os.listdir(lp(d))
        except OSError:
            return
        ours = {manifest_name(self.algo), DAY_META}
        if any(n not in ours for n in names):
            return
        for n in names:
            _remove(lp(os.path.join(d, n)))
        parts = day_rel.split("/")
        while parts:
            try:
                os.rmdir(lp(self._abs("/".join(parts))))
            except OSError:
                break
            parts.pop()

    def _write_albums(self) -> None:
        with self._db_lock:
            rows = self.lib.db.execute(
                "SELECT a.album, i.path FROM albums a JOIN items i ON i.hash=a.hash "
                "ORDER BY a.album, i.path").fetchall()
        albums: dict[str, list[str]] = defaultdict(list)
        for a, p in rows:
            albums[a].append(p)
        self.result.albums = len(albums)
        if not albums:
            return
        path = os.path.join(self.root, ALBUMS_FILE)
        tmp = path + ".tmp"
        with open(lp(tmp), "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"note": "Your Google Photos albums. Paths are relative to this folder; "
                               "each photo is stored once, in its day folder.",
                       "albums": albums}, fh, ensure_ascii=False, indent=1)
        os.replace(lp(tmp), lp(path))

    def _record_archives(self, cancelled: bool) -> None:
        """Remember zips that went in completely, so adding them again is instant. Never deletes them."""
        for a in self.plan.archives:
            if a.imported_before or a.error or not a.zipped or cancelled:
                continue
            n_items = sum(1 for it in self.plan.items if it.archive is a)
            if self.arch_fail.get(a.path, 0) == 0 and self.arch_done.get(a.path, 0) >= n_items:
                with self._db_lock:
                    self.lib.db.execute(
                        "INSERT OR REPLACE INTO archives VALUES(?,?,?,?,?,?,?,?)",
                        (a.key, a.path, a.size, a.export, a.part, time.time(), a.media,
                         self.arch_new.get(a.path, 0)))

    # ---------------------------------------------------------------- main
    def run(self) -> TakeoutResult:
        res = self.result
        res.started = time.time()
        res.log_path = self.log_path
        res.dest_dirs = [self.root]
        items = list(self.plan.items)
        res.files_total = len(items)
        res.bytes_total = sum(it.m.size for it in items)
        self.total = res.bytes_total
        self.files_total = len(items)
        self.phase = "Copying"
        self._paths: set[str] = set()
        cancelled = False
        try:
            self.lib = Library(self.root, algo=self.opt.algo or None)
            self.algo = self.lib.algo
            res.algo = self.algo
            for hx, rel, size in self.lib.db.execute("SELECT hash, path, size FROM items"):
                self.by_hash[hx] = rel
                self._paths.add(rel)
                self.sizes.add(size)
            self.log("info", f"Library {self.root}: {len(self.by_hash)} files already in it, checksum {self.algo}")
            self.log("info", f"{len(items)} photos and videos to bring in from {len(self.plan.archives)} takeout part(s)")
            by_arch: dict[str, list[Item]] = defaultdict(list)
            for it in items:
                by_arch[it.archive.path].append(it)
            groups = list(by_arch.items())
            n = max(1, min(self.opt.workers or 1, len(groups)))
            res.workers = n

            def run_group(path: str, g: list[Item]) -> None:
                try:
                    for it in g:
                        if self._stopping():
                            return
                        try:
                            self._process(it)
                        except Cancelled:
                            return
                        except Exception as e:  # noqa: BLE001
                            self._fail(it, f"{e.__class__.__name__}: {e}")
                            self.log("error", traceback.format_exc())
                finally:
                    self._close_zips()
                    self._drop_scratch(path)

            if n == 1:
                for path, g in groups:
                    run_group(path, g)
            else:
                with ThreadPoolExecutor(max_workers=n) as ex:
                    for fut in [ex.submit(run_group, p, g) for p, g in groups]:
                        fut.result()
            cancelled = self._stopping()
            res.cancelled = self.cancel_event.is_set()
            if not cancelled:
                self.phase = "Finishing"
                self._late_sidecars()
            self.lib.db.commit()
            self._write_folder_files()
            for day_rel in sorted(self.made_dirs):
                self._prune(day_rel)
            self._record_archives(cancelled)
            self.lib.db.commit()
            res.days = len({p.rsplit("/", 1)[0] for p in self.by_hash.values()})
        except Cancelled:
            res.cancelled = self.cancel_event.is_set()
        except Exception as e:  # noqa: BLE001
            res.aborted = f"{e.__class__.__name__}: {e}"
            self.log("error", traceback.format_exc())
        finally:
            if self.lib is not None:
                try:
                    self.lib.close()
                except sqlite3.Error:
                    pass
            for zp in list(self.scratch):
                self._drop_scratch(zp)
            self.finish_progress()
            res.finished = time.time()
            self.log("info", f"Done: {res.new} new, {res.duplicates} already in the library, "
                             f"{res.files_failed} failed, {human_bytes(res.bytes_written)} written")
            self.close_log()
        return res
