"""Library catalog: a searchable index of one library folder, for MMTX Library. No Qt in here.

Works on any folder MMTX fills:
  * a photo library from the Google Photos import:  2026/2026_01/2026_01_22/IMG_6367.HEIC
  * a working drive from Offload:                     2026_11_14_DailyRun/GX010001.MP4
  * an archive drive:                                 2026/2026_11_14_DailyRun/...  (or 2026/2026_11/...)
Only files inside dated day folders (and 'Unknown date') are catalogued, so a takeout folder or
anything else parked in the library is left alone.

The catalog lives in the library folder itself (_mmtx_library.db, table ``media``; the Google Photos
import index shares the file), so a drive carries its own catalog. MMTX Library keeps a copy of every
catalog on the computer, which is what lets you browse and search an archive drive that isn't plugged in.

Indexing is incremental: a file whose size and modified time haven't changed is not opened again.
For footage, checksums come from the day folder's _mmtx_checksums file; nothing is re-hashed.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import struct
import threading
import time
import traceback
import uuid
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Optional

from . import cameras
from .hashing import CHUNK, Cancelled, hash_file, iter_file, long_path as lp, new_hasher
from .manifest import is_manifest, load_folder_manifest, manifest_name, read_manifest, write_manifest
from .settings import cache_dir, config_dir
from .takeout import (
    HEAD,
    LIBRARY_DB,
    PHOTO_EXT,
    UNKNOWN_DIR,
    VIDEO_EXT,
    exif_datetime,
    filename_datetime,
    place_no_overwrite,
    set_file_times,
    video_datetime,
)
from .transfer import PARTIAL_SUFFIX, JobResult, RunnerBase, _remove, human_bytes

CATALOG_VERSION = 1

RAW_EXT = {".dng", ".arw", ".cr2", ".cr3", ".nef", ".nrw", ".raf", ".orf", ".rw2", ".gpr", ".srw", ".pef", ".x3f"}
PHOTO_ALL = (PHOTO_EXT | RAW_EXT | {".insp"}) - {".ico"}
VIDEO_ALL = (VIDEO_EXT | {".insv", ".360", ".osv", ".mxf"}) - {".lrv", ".lrf"}
SKIP_EXT = {".lrv", ".lrf", ".thm", ".srt", ".xml", ".json", ".txt", ".db", ".lrprev", ".xmp", ".ini", ".bin",
            ".dat", ".log", ".html", ".csv"}

DEVICE_LABEL = {"gopro": "GoPro", "dji": "DJI", "insta360": "Insta360", "sony": "Sony", "phone": "Phone",
                "generic": "Camera"}
DEVICE_KIND = {"gopro": "action", "dji": "drone", "insta360": "360", "sony": "cinema", "phone": "phone",
               "generic": "card"}

_DAY_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})(?:_(.+))?$")
_GOPRO_NAME = re.compile(r"^(G[HXLSP]\d{6}|GOPR\d{4}|GPR?\d{4,6}|GP\d{6})\.", re.I)
_SONY_NAME = re.compile(r"^C\d{4}\.(MP4|MXF)$", re.I)
_PHONE_NAME = re.compile(r"^(IMG|VID|PXL|MVIMG|LMC|Screenshot|Screenrecorder)[_-]", re.I)
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS media(
    path TEXT PRIMARY KEY, folder TEXT, name TEXT, kind TEXT, fmt TEXT, size INTEGER, mtime INTEGER,
    taken REAL, day TEXT, title TEXT, device TEXT, model TEXT, duration REAL, width INTEGER,
    height INTEGER, hash TEXT, fav INTEGER DEFAULT 0, text TEXT, meta TEXT, proxy TEXT, added REAL);
CREATE INDEX IF NOT EXISTS media_day ON media(day);
CREATE INDEX IF NOT EXISTS media_taken ON media(taken);
CREATE INDEX IF NOT EXISTS media_folder ON media(folder);
CREATE TABLE IF NOT EXISTS collections(name TEXT, path TEXT, added REAL, PRIMARY KEY(name, path));
"""

Row = namedtuple("Row", "path day taken kind fmt device model duration title fav size width height proxy "
                        "folder name mtime")
_ROW_COLS = "path, day, taken, kind, fmt, device, model, duration, title, fav, size, width, height, proxy, " \
            "folder, name, mtime"


# ----------------------------------------------------------------------------
# What a file is
# ----------------------------------------------------------------------------


def item_kind(name: str) -> tuple[str, str]:
    """('photo' | 'video' | '', fmt) where fmt is 'raw', '360' or ''."""
    ext = os.path.splitext(name)[1].lower()
    if ext in SKIP_EXT or name.startswith(("_mmtx", "._")) or name.endswith(PARTIAL_SUFFIX):
        return "", ""
    if ext in (".insv", ".360", ".osv"):
        return "video", "360"
    if ext in VIDEO_ALL:
        return "video", ""
    if ext == ".insp":
        return "photo", "360"
    if ext in RAW_EXT:
        return "photo", "raw"
    if ext in PHOTO_ALL:
        return "photo", ""
    return "", ""


def device_for(rel_in_day: str) -> str:
    """gopro | dji | insta360 | sony | phone | generic, from the path inside the day folder."""
    prof = cameras.classify(rel_in_day)
    if prof != "generic":
        return prof
    name = rel_in_day.rsplit("/", 1)[-1]
    if _GOPRO_NAME.match(name):
        return "gopro"
    if _SONY_NAME.match(name):
        return "sony"
    if _PHONE_NAME.match(name) or name.lower().endswith((".heic", ".heif")):
        return "phone"
    return "generic"


def parse_day_folder(name: str) -> Optional[tuple[str, str]]:
    """'2026_11_14_DailyRun' -> ('2026-11-14', 'DailyRun'); '2026_01_22' -> ('2026-01-22', '')."""
    m = _DAY_RE.match(name)
    if not m:
        return None
    try:
        datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", (m.group(4) or "")


def nice_title(title: str) -> str:
    return title.replace("_", " ").strip()


# ----------------------------------------------------------------------------
# Probing files
# ----------------------------------------------------------------------------


def probe_video(f, size: int) -> tuple[Optional[float], int, int]:
    """(duration seconds, width, height) from a MOV/MP4's moov box. ``f`` must be seekable."""

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

    dur, w, h = None, 0, 0
    try:
        moov = next(((a, b) for t, a, b in boxes(0, size) if t == b"moov"), None)
        if moov is None:
            return None, 0, 0
        for typ, a, b in boxes(*moov):
            if typ == b"mvhd":
                f.seek(a)
                d = f.read(32)
                if d[0] == 1:
                    ts, du = struct.unpack(">IQ", d[20:32])
                else:
                    ts, du = struct.unpack(">II", d[12:20])
                if ts:
                    dur = du / ts
            elif typ == b"trak" and not w:
                for t2, a2, b2 in boxes(a, b):
                    if t2 == b"tkhd" and b2 - a2 >= 84:
                        f.seek(b2 - 8)
                        ww, hh = struct.unpack(">II", f.read(8))
                        ww, hh = ww >> 16, hh >> 16
                        if ww and hh:
                            w, h = ww, hh
                        break
    except (struct.error, OSError, ValueError, IndexError):
        pass
    return dur, w, h


def exif_model(head: bytes) -> str:
    """'Apple iPhone 15 Pro' from a photo's EXIF Make/Model."""
    i = head.find(b"Exif\x00\x00")
    t = i + 6 if i != -1 else (0 if head[:4] in (b"II*\x00", b"MM\x00*") else -1)
    if t < 0:
        return ""
    try:
        e = "<" if head[t:t + 2] == b"II" else ">"
        off = struct.unpack_from(e + "I", head, t + 4)[0]
        n = struct.unpack_from(e + "H", head, t + off)[0]
        vals = {}
        for k in range(min(n, 400)):
            p = t + off + 2 + 12 * k
            tag, typ, cnt = struct.unpack_from(e + "HHI", head, p)
            if tag in (0x010F, 0x0110) and typ == 2 and cnt < 128:
                q = p + 8 if cnt <= 4 else t + struct.unpack_from(e + "I", head, p + 8)[0]
                vals[tag] = head[q:q + cnt].split(b"\x00")[0].decode("ascii", "ignore").strip()
        make, model = vals.get(0x010F, ""), vals.get(0x0110, "")
        if make and model.lower().startswith(make.split()[0].lower()):
            return model
        return f"{make} {model}".strip()
    except (struct.error, IndexError):
        return ""


def _search_text(*parts) -> str:
    out = []
    for p in parts:
        if isinstance(p, (list, tuple)):
            out.extend(str(x) for x in p if x)
        elif p:
            out.append(str(p))
    return " ".join(out).lower()


def _date_words(day: str) -> str:
    if not day:
        return "unknown date"
    y, m, d = day.split("-")
    mo = _MONTHS[int(m) - 1]
    return f"{day} {y} {mo} {mo[:3]} {y}_{m}_{d}"


# ----------------------------------------------------------------------------
# Finding the day folders
# ----------------------------------------------------------------------------

_SKIP_DIRS = {"system volume information", "$recycle.bin", ".trashes", ".spotlight-v100", ".fseventsd",
              ".thumbnails", ".ds_store"}


def find_day_folders(root: str, max_depth: int = 4) -> list[tuple[str, str, str]]:
    """[(relative folder, day 'YYYY-MM-DD' or '', title)] for every day folder in the library."""
    out = []
    for dirpath, dirnames, _files in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        depth = 0 if not rel else rel.count("/") + 1
        keep = []
        for d in sorted(dirnames):
            low = d.lower()
            if low in _SKIP_DIRS or d.startswith((".", "$", "_mmtx")):
                continue
            sub = f"{rel}/{d}" if rel else d
            parsed = parse_day_folder(d)
            if parsed:
                out.append((sub, parsed[0], parsed[1]))
            elif d == UNKNOWN_DIR and depth == 0:
                out.append((sub, "", ""))
            elif depth + 1 < max_depth:
                keep.append(d)
        dirnames[:] = keep
    return out


# ----------------------------------------------------------------------------
# The catalog
# ----------------------------------------------------------------------------


@dataclass
class IndexResult:
    folders: int = 0
    files: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: list = field(default_factory=list)
    seconds: float = 0.0


class Catalog:
    """One library's catalog. ``Catalog(root)`` opens (or creates) <root>/_mmtx_library.db;
    ``Catalog.open_file(path, root)`` opens a saved copy read-only (drive not connected)."""

    def __init__(self, root: str, db_path: Optional[str] = None, read_only: bool = False):
        self.root = root
        self.read_only = read_only
        self.db_path = db_path or os.path.join(root, LIBRARY_DB)
        if read_only:
            self.db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        else:
            self.db = sqlite3.connect(self.db_path, check_same_thread=False)
            self.db.executescript(_SCHEMA)
            self.db.commit()
        self.lock = threading.RLock()
        self._has_takeout = self._table("items")

    @classmethod
    def open_file(cls, path: str, root: str) -> "Catalog":
        return cls(root, db_path=path, read_only=True)

    def _table(self, name: str) -> bool:
        return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

    def close(self) -> None:
        with self.lock:
            try:
                if not self.read_only:
                    self.db.commit()
            finally:
                self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------------------------------------------------------- meta
    def get_meta(self, k: str, default: str = "") -> str:
        try:
            row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        except sqlite3.Error:
            return default
        return row[0] if row else default

    def set_meta(self, k: str, v: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (k, v))
            self.db.commit()

    @property
    def library_id(self) -> str:
        lid = self.get_meta("library_id")
        if not lid and not self.read_only:
            lid = uuid.uuid4().hex
            self.set_meta("library_id", lid)
        return lid

    @property
    def name(self) -> str:
        return self.get_meta("library_name") or default_name(self.root, self.kind)

    @property
    def kind(self) -> str:
        """photos | footage | mixed (decides the icon and wording)."""
        k = self.get_meta("library_kind")
        if k:
            return k
        try:
            n_v = self.db.execute("SELECT COUNT(*) FROM media WHERE kind='video' AND device!='phone'").fetchone()[0]
            n_all = self.db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
        except sqlite3.Error:
            return "mixed"
        if self._has_takeout or (n_all and n_v < n_all / 4):
            return "photos"
        return "footage" if n_all else "mixed"

    # ---------------------------------------------------------------- indexing
    def index(self, on_progress: Optional[Callable[[int, int, str], None]] = None,
              cancel: Optional[threading.Event] = None, folders: Optional[Iterable[str]] = None) -> IndexResult:
        """Bring the catalog up to date with the files on disk. ``folders``: only these day folders
        (relative), e.g. the ones a job just wrote; default = the whole library."""
        if self.read_only:
            raise RuntimeError("this catalog is a read-only copy")
        t0 = time.time()
        res = IndexResult()
        all_days = find_day_folders(self.root)
        if folders is not None:
            want = {f.replace("\\", "/").strip("/") for f in folders}
            days = [d for d in all_days if d[0] in want]
        else:
            days = all_days
        res.folders = len(days)
        takeout = self._takeout_rows() if self._has_takeout else {}
        albums = self._takeout_albums() if self._has_takeout else {}
        algo_t = self.get_meta("algo") if self._has_takeout else ""
        with self.lock:
            known = {p: (sz, mt) for p, sz, mt in self.db.execute("SELECT path, size, mtime FROM media")}
        seen: set[str] = set()
        batch = 0
        for i, (folder, day, title) in enumerate(days):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if on_progress:
                on_progress(i, len(days), folder)
            absdir = os.path.join(self.root, *folder.split("/"))
            files = []
            for dp, dns, fns in os.walk(absdir):
                dns[:] = sorted(d for d in dns if not d.startswith((".", "_mmtx")))
                for fn in fns:
                    full = os.path.join(dp, fn)
                    files.append((os.path.relpath(full, absdir).replace(os.sep, "/"), full))
            algo, man = load_folder_manifest(absdir)
            proxies = self._proxies(files)
            for rel_in, full in files:
                kind, fmt = item_kind(rel_in.rsplit("/", 1)[-1])
                if not kind:
                    continue
                dev = device_for(rel_in)
                if cameras.role_of(dev, rel_in) != "media":
                    continue
                path = f"{folder}/{rel_in}"
                seen.add(path)
                res.files += 1
                try:
                    st = os.stat(lp(full))
                except OSError:
                    continue
                old = known.get(path)
                if old and old == (st.st_size, st.st_mtime_ns):
                    res.unchanged += 1
                    continue
                try:
                    row = self._describe(path, full, rel_in, folder, day, title, kind, fmt, dev, st,
                                         takeout.get(path), albums, algo, man, algo_t, proxies)
                except Exception as e:  # noqa: BLE001
                    res.errors.append(f"{path}: {e}")
                    continue
                with self.lock:
                    if old:
                        fav = self.db.execute("SELECT fav FROM media WHERE path=?", (path,)).fetchone()
                        row["fav"] = fav[0] if fav else 0
                        res.updated += 1
                    else:
                        res.added += 1
                    cols = ", ".join(row)
                    self.db.execute(f"INSERT OR REPLACE INTO media({cols}) VALUES({', '.join('?' * len(row))})",
                                    tuple(row.values()))
                    batch += 1
                    if batch >= 300:
                        self.db.commit()
                        batch = 0
        # files that are gone (a folder asked for that no longer exists is gone entirely: archived with Move)
        scope = set(want) if folders is not None else {d[0] for d in days}
        with self.lock:
            for p in list(known):
                in_scope = any(p.startswith(s + "/") for s in scope) if folders is not None else True
                if in_scope and p not in seen:
                    self.db.execute("DELETE FROM media WHERE path=?", (p,))
                    res.removed += 1
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('catalog_version', ?)", (str(CATALOG_VERSION),))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('indexed_at', ?)", (str(time.time()),))
            self.db.commit()
        _ = self.library_id
        if on_progress:
            on_progress(len(days), len(days), "")
        res.seconds = time.time() - t0
        return res

    def _takeout_rows(self) -> dict[str, tuple]:
        try:
            return {r[0]: r[1:] for r in self.db.execute(
                "SELECT path, hash, taken, day, date_source, name, edited, meta FROM items")}
        except sqlite3.Error:
            return {}

    def _takeout_albums(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        try:
            for hx, a in self.db.execute("SELECT hash, album FROM albums"):
                out.setdefault(hx, []).append(a)
        except sqlite3.Error:
            pass
        return out

    @staticmethod
    def _proxies(files: list[tuple[str, str]]) -> dict[str, str]:
        """clip key -> relative path of its low-res proxy (GoPro/Insta360 .LRV, DJI .LRF, Sony SUB)."""
        out: dict[str, str] = {}
        for rel_in, _full in files:
            low = rel_in.lower()
            if low.endswith((".lrv", ".lrf")) or "/sub/" in f"/{low}":
                dev = device_for(rel_in)
                key = _proxy_key(dev, rel_in)
                out.setdefault(key, rel_in)
        return out

    def _describe(self, path, full, rel_in, folder, day, title, kind, fmt, dev, st, trow, albums, algo, man,
                  algo_t, proxies) -> dict:
        name = rel_in.rsplit("/", 1)[-1]
        taken = None
        model = ""
        dur, w, h = None, 0, 0
        meta: dict = {}
        hx = ""
        if trow:  # imported from Google Photos: the import already knows the date and Google's details
            t_hash, t_taken, t_day, t_src, t_name, t_edited, t_meta = trow
            taken = t_taken
            hx = f"{algo_t}:{t_hash}" if algo_t else t_hash
            try:
                g = json.loads(t_meta or "{}")
            except ValueError:
                g = {}
            dev_type = ((g.get("googlePhotosOrigin") or {}).get("mobileUpload") or {}).get("deviceType", "")
            if dev_type:
                model = {"IOS_PHONE": "iPhone", "ANDROID_PHONE": "Android phone",
                         "IOS_TABLET": "iPad"}.get(dev_type, dev_type.replace("_", " ").title())
                dev = "phone"
            if g.get("description"):
                meta["description"] = g["description"]
            people = [p.get("name") for p in (g.get("people") or []) if isinstance(p, dict) and p.get("name")]
            if people:
                meta["people"] = people
            geo = g.get("geoData") or {}
            if not (geo.get("latitude") or geo.get("longitude")):
                geo = g.get("geoDataExif") or {}
            if geo.get("latitude") or geo.get("longitude"):
                meta["location"] = [geo.get("latitude"), geo.get("longitude")]
            if g.get("url"):
                meta["google_url"] = g["url"]
            if g.get("favorited"):
                meta["google_favorite"] = True
            if albums.get(t_hash):
                meta["albums"] = sorted(albums[t_hash])
            meta["date_from"] = t_src
            if t_edited:
                meta["edited"] = True
            if t_name and t_name != name:
                meta["original_name"] = t_name
        elif man:
            v = man.get(rel_in)
            if v:
                hx = f"{algo}:{v}"
        need_video = kind == "video"
        if need_video or (not trow and kind == "photo"):
            try:
                with open(lp(full), "rb") as f:
                    if need_video:
                        dur, w, h = probe_video(f, st.st_size)
                        if taken is None:
                            vd = video_datetime(f, st.st_size)
                            if vd:
                                wall, off = vd
                                taken = (wall.timestamp() if off is None else
                                         (wall - _td(off)).replace(tzinfo=_utc()).timestamp())
                    else:
                        head = f.read(HEAD)
                        ex = exif_datetime(head)
                        if ex and taken is None:
                            wall, off = ex
                            taken = wall.timestamp() if off is None else \
                                (wall - _td(off)).replace(tzinfo=_utc()).timestamp()
                        model = model or exif_model(head)
            except OSError:
                pass
        if taken is None and dev in ("dji", "insta360", "sony", "phone"):
            try:
                taken = cameras.capture_time(dev, full, rel_in, 0).timestamp()
            except (OSError, ValueError, OverflowError):
                taken = None
            if taken is not None and taken <= 0:
                taken = None
        if taken is None:
            fn = filename_datetime(name)
            taken = fn.timestamp() if fn else st.st_mtime
        if dev == "sony" and not model:
            xml = os.path.join(os.path.dirname(full), os.path.splitext(name)[0] + "M01.XML")
            if os.path.isfile(xml):
                model = cameras.sony_model(xml) or ""
        if not model:
            model = DEVICE_LABEL.get(dev, "")
        proxy = proxies.get(_proxy_key(dev, rel_in), "")
        if proxy:
            proxy = f"{folder}/{proxy}"
        text = _search_text(name, nice_title(title), title, folder, model, DEVICE_LABEL.get(dev), dev,
                            meta.get("description"), meta.get("people"), meta.get("albums"),
                            meta.get("original_name"), _date_words(day), kind, "360" if fmt == "360" else "",
                            "raw" if fmt == "raw" else "")
        return {"path": path, "folder": folder, "name": name, "kind": kind, "fmt": fmt, "size": st.st_size,
                "mtime": st.st_mtime_ns, "taken": taken, "day": day, "title": nice_title(title), "device": dev,
                "model": model, "duration": dur, "width": w, "height": h, "hash": hx, "fav": 0, "text": text,
                "meta": json.dumps(meta, ensure_ascii=False), "proxy": proxy, "added": time.time()}

    # ---------------------------------------------------------------- reading
    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    def query(self, text: str = "", kind: str = "", device: str = "", model: str = "", fav: bool = False,
              collection: str = "", album: str = "", folder: str = "", day_from: str = "",
              day_to: str = "") -> list[Row]:
        sql = f"SELECT {_ROW_COLS} FROM media WHERE 1=1"
        args: list = []
        for term in (text or "").lower().split():
            if term.startswith("-") and len(term) > 1:
                sql += " AND text NOT LIKE ?"
                args.append(f"%{term[1:]}%")
            else:
                sql += " AND text LIKE ?"
                args.append(f"%{term}%")
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if device:
            sql += " AND device=?"
            args.append(device)
        if model:
            sql += " AND model=?"
            args.append(model)
        if fav:
            sql += " AND fav=1"
        if collection:
            sql += " AND path IN (SELECT path FROM collections WHERE name=?)"
            args.append(collection)
        if album:
            sql += " AND text LIKE ? AND meta LIKE ?"
            args += [f"%{album.lower()}%", f"%{json.dumps(album, ensure_ascii=False)}%"]
        if folder:
            sql += " AND folder=?"
            args.append(folder)
        if day_from:
            sql += " AND day>=?"
            args.append(day_from)
        if day_to:
            sql += " AND day<=?"
            args.append(day_to)
        sql += " ORDER BY day DESC, folder DESC, taken DESC, name DESC"
        with self.lock:
            return [Row(*r) for r in self.db.execute(sql, args)]

    def get(self, path: str) -> Optional[dict]:
        with self.lock:
            cur = self.db.execute("SELECT * FROM media WHERE path=?", (path,))
            r = cur.fetchone()
            if not r:
                return None
            d = dict(zip([c[0] for c in cur.description], r))
            try:
                d["meta"] = json.loads(d.get("meta") or "{}")
            except ValueError:
                d["meta"] = {}
            d["collections"] = [c[0] for c in self.db.execute(
                "SELECT name FROM collections WHERE path=? ORDER BY name", (path,))]
        return d

    def facets(self) -> dict:
        """Counts for the sidebar: kinds, cameras, albums, collections, years."""
        with self.lock:
            db = self.db
            kinds = dict(db.execute("SELECT kind, COUNT(*) FROM media GROUP BY kind").fetchall())
            models = db.execute("SELECT device, model, COUNT(*) FROM media GROUP BY device, model "
                                "ORDER BY COUNT(*) DESC").fetchall()
            favs = db.execute("SELECT COUNT(*) FROM media WHERE fav=1").fetchone()[0]
            cols = db.execute("SELECT name, COUNT(*) FROM collections GROUP BY name ORDER BY name").fetchall()
            years = db.execute("SELECT substr(day,1,4), COUNT(*) FROM media WHERE day!='' GROUP BY 1 "
                               "ORDER BY 1 DESC").fetchall()
            alb: dict[str, int] = {}
            for (m,) in db.execute("SELECT meta FROM media WHERE meta LIKE '%\"albums\"%'"):
                try:
                    for a in json.loads(m).get("albums", []):
                        alb[a] = alb.get(a, 0) + 1
                except ValueError:
                    pass
            total, size = db.execute("SELECT COUNT(*), COALESCE(SUM(size),0) FROM media").fetchone()
        return {"kinds": kinds, "models": models, "favs": favs, "collections": cols, "years": years,
                "albums": sorted(alb.items(), key=lambda kv: kv[0].lower()), "total": total, "size": size}

    # ---------------------------------------------------------------- your marks
    def set_fav(self, paths: Iterable[str], on: bool) -> None:
        with self.lock:
            self.db.executemany("UPDATE media SET fav=? WHERE path=?", [(int(on), p) for p in paths])
            self.db.commit()

    def add_to_collection(self, name: str, paths: Iterable[str]) -> None:
        now = time.time()
        with self.lock:
            self.db.executemany("INSERT OR IGNORE INTO collections VALUES(?,?,?)", [(name, p, now) for p in paths])
            self.db.commit()

    def remove_from_collection(self, name: str, paths: Iterable[str]) -> None:
        with self.lock:
            self.db.executemany("DELETE FROM collections WHERE name=? AND path=?", [(name, p) for p in paths])
            self.db.commit()

    def delete_collection(self, name: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM collections WHERE name=?", (name,))
            self.db.commit()

    # ---------------------------------------------------------------- the copy on this computer
    def save_copy(self, dest: str) -> None:
        """Snapshot this catalog (SQLite backup, safe while open) for browsing when the drive is away."""
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        with self.lock:
            out = sqlite3.connect(tmp)
            try:
                self.db.backup(out)
            finally:
                out.close()
        os.replace(tmp, dest)


def _td(minutes: int):
    from datetime import timedelta

    return timedelta(minutes=minutes)


def _utc():
    from datetime import timezone

    return timezone.utc


def _proxy_key(dev: str, rel_in: str) -> str:
    """GX010001.MP4 and GL010001.LRV, VID_..._00_000.insv and LRV_..._11_000.lrv,
    DJI_..._D.MP4 and DJI_..._D.LRF, C0001.MP4 and SUB/C0001S03.MP4 share a key."""
    name = rel_in.rsplit("/", 1)[-1]
    stem = os.path.splitext(name)[0].upper()
    if dev == "gopro":
        m = re.match(r"^G[HXLS](\d{2})(\d{4})$", stem)
        if m:
            return f"gopro|{m.group(1)}{m.group(2)}"
    if dev == "insta360":
        m = re.match(r"^(?:PRO_)?(?:VID|LRV)_(\d{8})_(\d{6})_\d{2}_(\d{3})$", stem)
        if m:
            return f"insta|{m.group(1)}{m.group(2)}{m.group(3)}"
    if dev == "sony":
        m = re.match(r"^(C\d{4})", stem)
        if m:
            return f"sony|{m.group(1)}"
    return f"{dev}|{stem}"


def default_name(root: str, kind: str) -> str:
    base = os.path.basename(root.rstrip("\\/")) or root
    drive = os.path.splitdrive(root)[0]
    return f"{base} ({drive})" if drive else base


# ----------------------------------------------------------------------------
# Libraries this computer knows about, and their saved copies
# ----------------------------------------------------------------------------


def registry_path() -> str:
    return os.path.join(config_dir(), "libraries.json")


def saved_copy_path(library_id: str) -> str:
    return os.path.join(config_dir(), "libraries", f"{library_id}.db")


def thumbs_dir(library_id: str) -> str:
    return os.path.join(cache_dir(), "library", library_id or "unknown")


def known_libraries() -> list[dict]:
    try:
        with open(registry_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return [d for d in data.get("libraries", []) if d.get("id") and d.get("root")]
    except (OSError, ValueError):
        return []


def _save_registry(libs: list[dict]) -> None:
    os.makedirs(os.path.dirname(registry_path()), exist_ok=True)
    tmp = registry_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"libraries": libs}, fh, indent=1)
    os.replace(tmp, registry_path())


def remember_library(cat: Catalog, count: Optional[int] = None) -> dict:
    libs = known_libraries()
    lid = cat.library_id
    entry = next((d for d in libs if d["id"] == lid), None)
    if entry is None:
        entry = {"id": lid}
        libs.append(entry)
    entry.update({"root": os.path.abspath(cat.root), "name": cat.name, "kind": cat.kind,
                  "count": cat.count() if count is None else count, "seen": time.time()})
    _save_registry(libs)
    return entry


def forget_library(library_id: str) -> None:
    _save_registry([d for d in known_libraries() if d["id"] != library_id])
    try:
        os.remove(saved_copy_path(library_id))
    except OSError:
        pass


def rename_library(library_id: str, name: str, cat: Optional[Catalog] = None) -> None:
    libs = known_libraries()
    for d in libs:
        if d["id"] == library_id:
            d["name"] = name
    _save_registry(libs)
    if cat is not None and not cat.read_only:
        cat.set_meta("library_name", name)


def locate(entry: dict) -> Optional[str]:
    """The library's folder if its drive is connected; on Windows also under another drive letter."""
    root = entry["root"]
    if os.path.isfile(os.path.join(root, LIBRARY_DB)):
        return root
    drive, rest = os.path.splitdrive(root)
    if drive and len(drive) == 2 and drive[1] == ":":
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZC":
            cand = f"{letter}:{rest}"
            db = os.path.join(cand, LIBRARY_DB)
            if cand != root and os.path.isfile(db):
                try:
                    with Catalog(cand, read_only=True) as c:
                        if c.get_meta("library_id") == entry["id"]:
                            return cand
                except sqlite3.Error:
                    pass
    return None


def index_and_remember(root: str, folders: Optional[Iterable[str]] = None, on_progress=None,
                       cancel=None) -> tuple[IndexResult, dict]:
    """Index a library (or some of its day folders), save the copy on this computer, register it."""
    with Catalog(root) as cat:
        res = cat.index(on_progress=on_progress, cancel=cancel, folders=folders)
        entry = remember_library(cat)
        try:
            cat.save_copy(saved_copy_path(entry["id"]))
        except (OSError, sqlite3.Error):
            pass
    return res, entry


# ----------------------------------------------------------------------------
# Export: copy picked files into a project folder, checked, never overwriting
# ----------------------------------------------------------------------------


@dataclass
class ExportOptions:
    dest: str
    layout: str = "flat"  # flat | days (YYYY_MM_DD_Title folders)
    rename: str = "keep"  # keep | date (2026_01_22_IMG_6367.HEIC)
    verify: bool = True
    uncached: bool = True
    algo: str = "xxh128"


class ExportRunner(RunnerBase):
    """Copies library files out. Same rules as everywhere in MMTX: read once while hashing, write to
    .mmtx-partial, read back and compare, then rename; an existing file of the same name is compared by
    checksum (same: skipped; different: the copy gets _1). Writes a checksum file in each folder."""

    def __init__(self, root: str, rows: list, opts: ExportOptions, emit=None, log_dir=None):
        super().__init__(emit, log_dir)
        self.root = root
        self.rows = rows
        self.opt = opts

    def _dest_for(self, r) -> tuple[str, str]:
        d = self.opt.dest
        day = (r.day or "").replace("-", "_")
        if self.opt.layout == "days":
            sub = day or "Unknown_date"
            if r.title:
                sub += "_" + r.title.replace(" ", "_")
            d = os.path.join(d, sub)
        name = r.name
        if self.opt.rename == "date" and day and not name.startswith(day):
            name = f"{day}_{name}"
        return d, name

    def run(self) -> JobResult:
        res = JobResult(kind="export", title=f"Export {len(self.rows)} file(s)", algo=self.opt.algo)
        res.started = time.time()
        res.log_path = self.log_path
        res.files_total = len(self.rows)
        sizes = 0
        for r in self.rows:
            sizes += r.size or 0
        res.bytes_total = sizes
        self.total = sizes * (2 if self.opt.verify else 1)
        self.files_total = len(self.rows)
        self.phase = "Copying"
        manifests: dict[str, dict[str, str]] = {}
        taken_names: set[str] = set()
        try:
            for r in self.rows:
                if self._stopping():
                    break
                src = os.path.join(self.root, *r.path.split("/"))
                ddir, name = self._dest_for(r)
                os.makedirs(lp(ddir), exist_ok=True)
                res.dest_dirs = sorted(set(res.dest_dirs) | {ddir})
                try:
                    hx, final, skipped = self._one(src, ddir, name, r, taken_names)
                except Cancelled:
                    break
                except OSError as e:
                    res.files_failed += 1
                    res.errors.append(f"{r.path}: {e}")
                    self.log("error", f"{r.path}: {e}")
                    continue
                manifests.setdefault(ddir, {})[os.path.basename(final)] = hx
                res.files_ok += 1
                if skipped:
                    res.files_skipped += 1
                else:
                    res.files_copied += 1
                self.files_done += 1
                self.emit("file", {"path": r.path, "dest": final, "skipped": skipped})
            for d, entries in manifests.items():
                write_manifest(d, self.opt.algo, entries, merge=True)
        except Exception as e:  # noqa: BLE001
            res.aborted = f"{e.__class__.__name__}: {e}"
            self.log("error", traceback.format_exc())
        finally:
            res.cancelled = self.cancel_event.is_set()
            self.finish_progress()
            res.finished = time.time()
            self.close_log()
        return res

    def _one(self, src: str, ddir: str, name: str, r, taken_names: set) -> tuple[str, str, bool]:
        algo = self.opt.algo
        known = (r.hash if hasattr(r, "hash") else "") or ""
        stem, ext = os.path.splitext(name)
        i = 0
        # an existing file of that name: compare checksums, never overwrite
        while True:
            cand = os.path.join(ddir, name if i == 0 else f"{stem}_{i}{ext}")
            i += 1
            key = os.path.normcase(os.path.abspath(cand))
            if key in taken_names:
                continue
            if not os.path.exists(lp(cand)):
                break
            if os.path.getsize(lp(cand)) == (r.size or -1):
                src_hash = known.split(":", 1)[1] if known.startswith(algo + ":") else hash_file(src, algo, uncached=False)
                if hash_file(cand, algo, uncached=False) == src_hash:
                    self.tick(r.size * (2 if self.opt.verify else 1))
                    taken_names.add(key)
                    return src_hash, cand, True
        taken_names.add(key)
        self.set_file("Copying", os.path.basename(src), r.size, src=src)
        partial = cand + PARTIAL_SUFFIX
        h = new_hasher(algo)
        try:
            fd = os.open(lp(partial), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o666)
            with os.fdopen(fd, "wb", buffering=0) as out:
                for chunk in iter_file(src, uncached=self.opt.uncached, chunk=CHUNK):
                    h.update(chunk)
                    mv = memoryview(chunk)
                    while mv:
                        n = out.write(mv)
                        mv = mv[n:]
                    self.tick(len(chunk))
                out.flush()
                os.fsync(out.fileno())
            hx = h.hexdigest()
            if self.opt.verify:
                self.set_file("Verifying", os.path.basename(src), r.size, src=src)
                back = new_hasher(algo)
                for chunk in iter_file(partial, uncached=self.opt.uncached, chunk=CHUNK):
                    back.update(chunk)
                    self.tick(len(chunk))
                if back.hexdigest() != hx:
                    raise OSError("read-back checksum mismatch; the destination drive may be failing")
            while True:
                try:
                    place_no_overwrite(partial, cand)
                    break
                except FileExistsError:
                    cand = os.path.join(ddir, f"{stem}_{i}{ext}")
                    i += 1
            ts = r.taken or os.stat(lp(src)).st_mtime
            set_file_times(cand, ts)
            return hx, cand, False
        finally:
            if os.path.exists(lp(partial)):
                _remove(lp(partial))
            self.clear_file()


__all__ = ["Catalog", "Row", "IndexResult", "ExportOptions", "ExportRunner", "item_kind", "device_for",
           "parse_day_folder", "find_day_folders", "probe_video", "known_libraries", "remember_library",
           "forget_library", "locate", "saved_copy_path", "thumbs_dir", "index_and_remember", "DEVICE_LABEL",
           "DEVICE_KIND", "human_bytes", "is_manifest", "manifest_name", "read_manifest", "rename_library"]
