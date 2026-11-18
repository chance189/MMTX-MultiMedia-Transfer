"""Previews for MMTX Library: a thumbnail for every photo and clip, plus an 8-frame hover strip for every
clip, made once and kept in one file on this computer (``previews.db`` in the cache folder).

Previews are keyed by the file's content checksum (the one MMTX recorded when it copied the file), not by
its path, so a preview made while a shoot sits on the fast working drive is still the right preview after
the folder moves to the archive drive, and an unplugged drive still shows every picture.

A clip costs one ffmpeg run: eight inputs, each seeking straight to a keyframe, decoded single-threaded
(cheapest for one frame), producing the thumbnail and the strip together. Clips with a camera proxy
(.LRV/.LRF/SUB) are read from the proxy. iPhone HEICs use the preview Apple embeds in the file (9 ms
instead of ~1 s for a full 12 MP decode).
"""

from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from . import media
from .settings import cache_dir

FORMAT = "2"  # bump when sizes change: old previews are dropped
THUMB = 440  # long side of a thumbnail
CELL = 240  # one square frame of the hover strip
FRAMES = 8
QUALITY = 82
HEIF_EXT = (".heic", ".heif", ".hif", ".avif")
RAW_EXT = (".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2", ".gpr", ".srw", ".pef")


class PreviewError(Exception):
    """The file can't be previewed (damaged, no video stream...): recorded so it isn't retried."""


class NoTool(Exception):
    """ffmpeg isn't available: not the file's fault, nothing is recorded."""


class Cancelled(Exception):
    pass


def store_path() -> str:
    return os.path.join(cache_dir(), "previews.db")


def key_for(row) -> str:
    """Content checksum when MMTX recorded one (survives moves and renames of the folder), else
    name + size + day (also survives moves)."""
    h = getattr(row, "hash", "") or ""
    if h:
        return h
    raw = f"{row.name}|{row.size}|{row.day}".encode("utf-8", "surrogatepass")
    return "f:" + hashlib.sha1(raw).hexdigest()[:32]


def abs_path(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def video_source(root: str, row) -> str:
    """The cheapest file to take frames from: the camera's proxy if it's there, else the clip."""
    if getattr(row, "proxy", ""):
        p = abs_path(root, row.proxy)
        if os.path.isfile(p):
            return p
    return abs_path(root, row.path)


# ----------------------------------------------------------------------------
# The store
# ----------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS previews(key TEXT PRIMARY KEY, thumb BLOB, strip BLOB, made REAL);
CREATE TABLE IF NOT EXISTS failed(key TEXT PRIMARY KEY, why TEXT, made REAL);
"""


class PreviewStore:
    """One SQLite file. Safe to use from many threads (one connection per thread)."""

    def __init__(self, path: str = ""):
        self.path = path or store_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._gen = 0  # bumped by clear(): threads reconnect
        self.demand = 0  # previews being made for what's on screen right now; background work waits
        self._open()

    def _open(self) -> None:
        try:
            c = self._connect()
            if (c.execute("SELECT v FROM meta WHERE k='format'").fetchone() or [""])[0] != FORMAT:
                c.execute("DELETE FROM previews")
                c.execute("DELETE FROM failed")
                c.execute("INSERT OR REPLACE INTO meta VALUES('format', ?)", (FORMAT,))
                c.commit()
        except sqlite3.DatabaseError:  # damaged: start over, it's only a cache
            self._drop_files()
            self._gen += 1
            c = self._connect()
            c.execute("INSERT OR REPLACE INTO meta VALUES('format', ?)", (FORMAT,))
            c.commit()

    def _connect(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is not None and getattr(self._local, "gen", -1) == self._gen:
            return c
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.executescript(_SCHEMA)
        self._local.c, self._local.gen = c, self._gen
        return c

    def _drop_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass

    # reads
    def thumb(self, key: str) -> Optional[bytes]:
        r = self._connect().execute("SELECT thumb FROM previews WHERE key=?", (key,)).fetchone()
        return r[0] if r and r[0] else None

    def strip(self, key: str) -> Optional[bytes]:
        r = self._connect().execute("SELECT strip FROM previews WHERE key=?", (key,)).fetchone()
        return r[0] if r and r[0] else None

    def is_failed(self, key: str) -> bool:
        return self._connect().execute("SELECT 1 FROM failed WHERE key=?", (key,)).fetchone() is not None

    def done(self) -> dict[str, bool]:
        """key -> has a strip, for every stored preview."""
        return {k: bool(s) for k, s in self._connect().execute("SELECT key, strip IS NOT NULL FROM previews")}

    def failed_keys(self) -> set[str]:
        return {k for (k,) in self._connect().execute("SELECT key FROM failed")}

    def count(self) -> int:
        return self._connect().execute("SELECT COUNT(*) FROM previews").fetchone()[0]

    def size_bytes(self) -> int:
        n = 0
        for suffix in ("", "-wal"):
            try:
                n += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return n

    # writes
    def put(self, key: str, thumb: bytes, strip: Optional[bytes] = None) -> None:
        c = self._connect()
        with self._lock:
            if strip is None:  # keep a strip made earlier
                c.execute("INSERT INTO previews(key, thumb, strip, made) VALUES(?,?,NULL,?) "
                          "ON CONFLICT(key) DO UPDATE SET thumb=excluded.thumb, made=excluded.made",
                          (key, thumb, time.time()))
            else:
                c.execute("INSERT OR REPLACE INTO previews VALUES(?,?,?,?)", (key, thumb, strip, time.time()))
            c.execute("DELETE FROM failed WHERE key=?", (key,))
            c.commit()

    def fail(self, key: str, why: str) -> None:
        c = self._connect()
        with self._lock:
            c.execute("INSERT OR REPLACE INTO failed VALUES(?,?,?)", (key, why[:500], time.time()))
            c.commit()

    def clear(self) -> None:
        with self._lock:
            c = getattr(self._local, "c", None)
            if c is not None:
                c.close()
                self._local.c = None
            self._gen += 1
            self._drop_files()
        self._open()


_shared: Optional[PreviewStore] = None
_shared_lock = threading.Lock()


def shared_store() -> PreviewStore:
    """The one store everything in this process uses (the Library window and MMTX's own jobs)."""
    global _shared
    with _shared_lock:
        if _shared is None or _shared.path != store_path():
            _shared = PreviewStore()
        return _shared


# ----------------------------------------------------------------------------
# Making previews
# ----------------------------------------------------------------------------


def _run_ff(cmd: list[str], cancel: Optional[threading.Event], timeout: float) -> tuple[int, str]:
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **kw)
    t0 = time.time()
    while True:
        try:
            _out, err = p.communicate(timeout=0.1)
            return p.returncode, err.decode("utf-8", "replace")[-400:]
        except subprocess.TimeoutExpired:
            if (cancel is not None and cancel.is_set()) or time.time() - t0 > timeout:
                p.kill()
                p.communicate()
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                return -1, "ffmpeg took too long"


def _tmp_dir() -> str:
    d = os.path.join(cache_dir(), "tmp")
    os.makedirs(d, exist_ok=True)
    return d


def video_preview(src: str, duration: float, cancel: Optional[threading.Event] = None) -> tuple[bytes, bytes]:
    """(thumbnail JPEG, strip JPEG) from one ffmpeg run."""
    ff = media.find_ffmpeg()
    if not ff:
        raise NoTool("ffmpeg isn't available")
    n = FRAMES
    times = [(i + 0.5) / n * duration for i in range(n)] if duration and duration > 0 else [float(i) for i in range(n)]
    with tempfile.TemporaryDirectory(dir=_tmp_dir()) as td:
        t_out, s_out = os.path.join(td, "t.jpg"), os.path.join(td, "s.jpg")
        cmd = [ff, "-v", "error", "-nostdin"]
        for t in times:  # each input seeks to the keyframe at or before t and decodes only that one
            cmd += ["-threads", "1", "-skip_frame", "nokey", "-noaccurate_seek", "-ss", f"{t:.3f}", "-i", src]
        cell = f"scale={CELL}:{CELL}:force_original_aspect_ratio=increase,crop={CELL}:{CELL},setsar=1"
        thumb = f"scale='if(gte(iw,ih),{THUMB},-2)':'if(gte(iw,ih),-2,{THUMB})',setsar=1"
        fc = [f"[1:v]trim=end_frame=1,split[tsrc][s1];[tsrc]{thumb}[thumb];[s1]{cell}[c1]"]
        fc += [f"[{i}:v]trim=end_frame=1,{cell}[c{i}]" for i in range(n) if i != 1]
        fc.append("".join(f"[c{i}]" for i in range(n)) + f"hstack=inputs={n}[strip]")
        cmd += ["-filter_complex", ";".join(fc),
                "-map", "[thumb]", "-frames:v", "1", "-q:v", "3", "-update", "1", "-y", t_out,
                "-map", "[strip]", "-frames:v", "1", "-q:v", "4", "-update", "1", "-y", s_out]
        rc, err = _run_ff(cmd, cancel, timeout=90)
        if rc == 0 and os.path.isfile(t_out) and os.path.isfile(s_out):
            with open(t_out, "rb") as f1, open(s_out, "rb") as f2:
                return f1.read(), f2.read()
        # one frame from the start, repeated: short or odd files
        cmd = [ff, "-v", "error", "-nostdin", "-threads", "1", "-skip_frame", "nokey", "-i", src,
               "-map", "0:v:0", "-frames:v", "1", "-vf", thumb, "-q:v", "3", "-update", "1", "-y", t_out]
        rc2, err2 = _run_ff(cmd, cancel, timeout=60)
        if rc2 == 0 and os.path.isfile(t_out):
            with open(t_out, "rb") as f1:
                tdata = f1.read()
            return tdata, _repeat_strip(tdata)
        raise PreviewError((err2 or err or "no video frames").strip().splitlines()[-1] if (err2 or err) else
                           "no video frames")


def _repeat_strip(jpeg: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as im:
        cell = _square(im.convert("RGB"), CELL)
    out = Image.new("RGB", (CELL * FRAMES, CELL))
    for i in range(FRAMES):
        out.paste(cell, (i * CELL, 0))
    return _jpeg(out)


def _square(im, size: int):
    w, h = im.size
    s = min(w, h)
    return im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((size, size))


def _jpeg(im, quality: int = QUALITY) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


_heif_ok: Optional[bool] = None


def _heif() -> bool:
    global _heif_ok
    if _heif_ok is None:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
            _heif_ok = True
        except Exception:  # noqa: BLE001
            _heif_ok = False
    return _heif_ok


_ROT = {3: 180, 6: 270, 8: 90}  # EXIF orientation -> PIL rotate() degrees (counter-clockwise)


def _heif_embedded(path: str):
    """The preview the camera embedded in a HEIC (iPhone: 320x240), upright, or None."""
    import pillow_heif

    hf = pillow_heif.open_heif(path, convert_hdr_to_8bit=True)
    img = hf[getattr(hf, "primary_index", 0)]
    if hasattr(img, "get_thumbnail"):  # pillow-heif 0.18+
        if not img.info.get("thumbnails"):
            return None
        th = img.get_thumbnail(0).to_pillow()
    elif getattr(img, "thumbnails", None):  # older pillow-heif
        th = img.thumbnails[0].to_pillow()
    else:
        return None
    if min(th.size) < 200:
        return None
    full_portrait = img.size[1] > img.size[0]
    if (th.size[1] > th.size[0]) != full_portrait and th.size[0] != th.size[1]:
        # the thumbnail wasn't turned with the photo: turn it the way EXIF says, or give up
        orient = 0
        try:
            from PIL import Image

            exif = img.info.get("exif")
            if exif:
                ex = Image.Exif()
                ex.load(exif[6:] if exif[:6] == b"Exif\x00\x00" else exif)
                orient = ex.get(0x0112, 0)
        except Exception:  # noqa: BLE001
            orient = 0
        if orient not in (6, 8):
            return None
        th = th.rotate(_ROT[orient], expand=True)
    return th


def photo_preview(path: str) -> bytes:
    from PIL import Image, ImageOps

    low = path.lower()
    try:
        if low.endswith(HEIF_EXT):
            if not _heif():
                raise NoTool("pillow-heif isn't installed")
            try:
                th = _heif_embedded(path)
            except Exception:  # noqa: BLE001
                th = None
            if th is not None:
                th = th.convert("RGB")
                th.thumbnail((THUMB, THUMB))
                return _jpeg(th)
        if low.endswith(RAW_EXT):
            data = media.embedded_jpeg(path)
            if not data:
                raise PreviewError("no preview inside this RAW file")
            src = io.BytesIO(data)
        else:
            src = path
        with Image.open(src) as im:
            im.draft("RGB", (THUMB, THUMB))  # JPEG: decode at 1/2, 1/4 or 1/8 size straight away
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB, THUMB))
            return _jpeg(im.convert("RGB"))
    except (NoTool, PreviewError):
        raise
    except Exception as e:  # noqa: BLE001
        raise PreviewError(f"{type(e).__name__}: {e}") from None


def make(row, root: str, cancel: Optional[threading.Event] = None) -> tuple[bytes, Optional[bytes]]:
    """(thumbnail, strip or None for photos). Raises PreviewError, NoTool or Cancelled."""
    if row.kind == "video":
        return video_preview(video_source(root, row), row.duration or 0.0, cancel)
    return photo_preview(abs_path(root, row.path)), None


def complete(row, done: dict[str, bool], failed: set[str]) -> bool:
    k = key_for(row)
    if k in failed:
        return True
    has = done.get(k)
    return has is not None and (has or row.kind != "video")


# ----------------------------------------------------------------------------
# Preparing a whole library in the background
# ----------------------------------------------------------------------------


@dataclass
class PrepareResult:
    total: int = 0  # previews this run had to make
    made: int = 0
    failed: int = 0
    missing: int = 0  # file not there (drive unplugged mid-run, moved...)
    skipped_no_ffmpeg: int = 0
    cancelled: bool = False
    seconds: float = 0.0


def default_workers(root: str) -> int:
    cores = os.cpu_count() or 4
    try:
        from .drives import drive_info

        kind = drive_info(root).kind
    except Exception:  # noqa: BLE001
        kind = "unknown"
    if kind in ("hdd", "network", "usb", "sd"):
        return 2  # more just makes a spinning disk seek
    return max(2, min(4, cores // 2))  # leave cores for the window and for playback


def todo(rows: Iterable, store: PreviewStore) -> list:
    """Rows still needing a preview, newest first (the order they come in), one per distinct file."""
    done, failed = store.done(), store.failed_keys()
    seen: set[str] = set()
    out = []
    for r in rows:
        k = key_for(r)
        if k in seen or complete(r, done, failed):
            continue
        seen.add(k)
        out.append(r)
    return out


def prepare(root: str, rows: Iterable, store: Optional[PreviewStore] = None, workers: int = 0,
            on_progress: Optional[Callable[[int, int, float], None]] = None,
            cancel: Optional[threading.Event] = None, pause: Optional[threading.Event] = None) -> PrepareResult:
    """Make every missing preview for ``rows`` (catalog rows of the library at ``root``).

    Resumable (finished previews are kept), yields to on-screen requests (``store.demand``), waits while
    ``pause`` is set, stops promptly on ``cancel`` (running ffmpeg processes are killed).
    on_progress(done, total, seconds_left_estimate)."""
    store = store or shared_store()
    cancel = cancel or threading.Event()
    res = PrepareResult()
    t0 = time.time()
    items = todo(rows, store)
    res.total = len(items)
    if not items:
        return res
    have_ff = media.find_ffmpeg() is not None
    workers = workers or default_workers(root)
    finished = 0
    busy_time = [0.0]
    lock = threading.Lock()

    def one(row) -> str:
        while (pause is not None and pause.is_set()) or store.demand > 0:
            if cancel.is_set():
                return "cancel"
            time.sleep(0.1)
        if cancel.is_set():
            return "cancel"
        if row.kind == "video" and not have_ff:
            return "noff"
        k = key_for(row)
        if (store.strip(k) if row.kind == "video" else store.thumb(k)) is not None:
            return "made"  # the grid made it for the screen meanwhile
        if not os.path.exists(abs_path(root, row.path)):
            return "missing"
        t = time.time()
        try:
            thumb, strip = make(row, root, cancel)
        except Cancelled:
            return "cancel"
        except NoTool:
            return "noff"
        except Exception as e:  # noqa: BLE001  (PreviewError, or anything a damaged file trips)
            if os.path.exists(abs_path(root, row.path)):  # still there: it's the file
                store.fail(key_for(row), str(e) or type(e).__name__)
                return "failed"
            return "missing"
        store.put(key_for(row), thumb, strip)
        with lock:
            busy_time[0] += time.time() - t
        return "made"

    last = 0.0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mmtx-preview") as ex:
        it = iter(items)
        running = set()
        exhausted = False
        while True:
            while not exhausted and not cancel.is_set() and len(running) < workers * 2:
                r = next(it, None)
                if r is None:
                    exhausted = True
                    break
                running.add(ex.submit(one, r))
            if not running:
                break
            fin, running = wait(running, timeout=0.25, return_when=FIRST_COMPLETED)
            running = set(running)
            for f in fin:
                finished += 1
                out = f.result()
                if out == "made":
                    res.made += 1
                elif out == "failed":
                    res.failed += 1
                elif out == "missing":
                    res.missing += 1
                elif out == "noff":
                    res.skipped_no_ffmpeg += 1
                elif out == "cancel":
                    res.cancelled = True
            now = time.time()
            if on_progress and (now - last > 0.3 or not running):
                last = now
                left = res.total - finished
                rate = res.made / max(0.001, now - t0) if res.made else 0.0
                on_progress(finished, res.total, left / rate if rate else 0.0)
            if cancel.is_set():
                res.cancelled = True
    res.seconds = time.time() - t0
    return res
