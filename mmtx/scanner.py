"""Scan camera cards (grouped by shooting day) and working drives (grouped by folder)."""

from __future__ import annotations

import os
import re
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from . import cameras, sources
from .manifest import find_manifest, is_manifest
from .media import media_kind, poster_candidates, thumb_candidates
from .transfer import is_junk, list_folder_files

SKIP_DIRS = {
    "SYSTEM VOLUME INFORMATION", "$RECYCLE.BIN", "LOST.DIR", "MISC", "ANDROID",
    ".TRASHES", ".SPOTLIGHT-V100", ".FSEVENTSD", ".TEMPORARYITEMS",
}
SONY_M4_KEEP = {"CLIP", "SUB", "THMBNL"}


@dataclass
class MediaFile:
    path: str
    rel: str
    name: str
    size: int
    mtime: float
    profile: str
    role: str
    key: str
    capture: datetime
    day: date = None  # type: ignore[assignment]
    kind: str = "other"  # video | 360 | photo | 360photo | raw | audio | other
    thumbs: list = field(default_factory=list)  # [(path, method)] cheapest first


@dataclass
class DayGroup:
    day: date
    files: list[MediaFile] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def profiles(self) -> list[str]:
        return sorted({f.profile for f in self.files})

    @property
    def first_time(self) -> datetime:
        return min(f.capture for f in self.files)

    @property
    def last_time(self) -> datetime:
        return max(f.capture for f in self.files)


@dataclass
class CardScan:
    root: str
    devices: dict[str, str]  # profile -> detected label
    files: list[MediaFile]
    days: list[DayGroup]  # newest first

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def scan_card(
    root: str,
    cancel: Optional[threading.Event] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    phone_days: int = 0,
) -> CardScan:
    """phone_days > 0: from phones keep only files from that many recent days (a phone holds years)."""
    raw: list[tuple[str, str, os.stat_result]] = []
    for dirpath, dirnames, filenames in sources.walk(root):
        if cancel is not None and cancel.is_set():
            break
        rel_dir = sources.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        up = [p.upper() for p in rel_dir.split("/")] if rel_dir else []
        keep = []
        for d in dirnames:
            u = d.upper()
            if d.startswith(".") or u in SKIP_DIRS:
                continue
            if len(up) == 2 and up[0] == "PRIVATE" and up[1] == "M4ROOT" and u not in SONY_M4_KEEP:
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        in_dcim_sub = len(up) >= 2 and up[0] == "DCIM"
        in_m4 = len(up) >= 3 and up[0] == "PRIVATE" and up[1] == "M4ROOT"
        for fn in filenames:
            if fn.startswith(".") or is_junk(fn) or is_manifest(fn):
                continue
            ext = os.path.splitext(fn)[1].lower()
            if not (in_dcim_sub or in_m4) and ext not in cameras.MEDIA_EXTS:
                continue
            p = sources.join(dirpath, fn)
            try:
                st = sources.src_stat(p)
            except OSError:
                continue
            raw.append((p, f"{rel_dir}/{fn}" if rel_dir else fn, st))
        if on_progress:
            on_progress(len(raw))

    files: list[MediaFile] = []
    for p, rel, st in raw:
        prof = cameras.classify(rel)
        files.append(
            MediaFile(
                path=p,
                rel=rel,
                name=os.path.basename(rel),
                size=st.st_size,
                mtime=st.st_mtime,
                profile=prof,
                role=cameras.role_of(prof, rel),
                key=cameras.clip_key(prof, rel),
                capture=cameras.capture_time(prof, p, rel, st.st_mtime),
            )
        )

    if phone_days > 0:
        cutoff = datetime.now() - timedelta(days=phone_days)
        old_keys = {(f.profile, f.key) for f in files if f.profile == "phone" and f.capture < cutoff}
        files = [f for f in files if (f.profile, f.key) not in old_keys]

    # every file of a shot (clip, proxy, thumb, sidecar) lands on the main clip's day
    by_key: dict[tuple[str, str], list[MediaFile]] = defaultdict(list)
    for f in files:
        by_key[(f.profile, f.key)].append(f)
    for members in by_key.values():
        primaries = [m for m in members if m.role == "media"] or members
        primary = max(primaries, key=lambda m: m.size)
        for m in members:
            m.day = primary.capture.date()
            m.kind = media_kind(m.name)
            if m.role == "media":
                m.thumbs = thumb_candidates(m.path, [o.path for o in members if o is not m])

    days_map: dict[date, DayGroup] = {}
    for f in files:
        days_map.setdefault(f.day, DayGroup(f.day)).files.append(f)
    for dg in days_map.values():
        dg.files.sort(key=lambda f: (f.capture, f.name))
    days = sorted(days_map.values(), key=lambda d: d.day, reverse=True)

    devices = detect_devices(root, files)
    return CardScan(root=root, devices=devices, files=files, days=days)


def detect_devices(root: str, files: list[MediaFile]) -> dict[str, str]:
    present = {f.profile for f in files}
    out: dict[str, str] = {}
    for prof in sorted(present):
        label = None
        if prof == "gopro":
            label = cameras.gopro_model(root)
        elif prof == "insta360":
            insv = next((f.path for f in files if f.profile == prof and f.name.lower().endswith(".insv")), None)
            label = cameras.insta360_model(insv) if insv else None
        elif prof == "sony":
            xml = next((f.path for f in files if f.profile == prof and f.name.upper().endswith("M01.XML")), None)
            label = cameras.sony_model(xml) if xml else None
        elif prof in ("generic", "phone"):
            dev = sources.device_of(root)
            label = dev.name if dev else volume_label(root)
        out[prof] = label or cameras.PROFILE_NAMES[prof]
    return out


def volume_label(root: str) -> Optional[str]:
    if sources.is_portable(root):
        dev = sources.device_of(root)
        return dev.name if dev else None
    name = os.path.basename(os.path.normpath(root))
    if not name or name.upper() in ("NO NAME", "UNTITLED", "") or re.match(r"^[A-Z]:?$", name, re.I):
        return None
    return name


# ---------------------------------------------------------------- working drive

DATED_RE = re.compile(r"^(\d{4})[_\-.]?(\d{2})[_\-.]?(\d{2})(?:[_\- ]+(.*))?$")
YEAR_RE = re.compile(r"^\d{4}$")
YEAR_MONTH_RE = re.compile(r"^\d{4}[_\-.]\d{2}(?:[_\- ].*)?$")


def parse_dated_name(name: str) -> tuple[Optional[date], str]:
    m = DATED_RE.match(name)
    if not m:
        return None, name
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, name
    return d, (m.group(4) or "").strip()


@dataclass
class WorkFolder:
    path: str
    name: str
    day: Optional[date]
    title: str
    files: int = 0
    size: int = 0
    manifest_algo: Optional[str] = None
    newest_mtime: float = 0.0
    poster: list = field(default_factory=list)  # [(path, method)] for the folder thumbnail


def _is_system_dir(name: str) -> bool:
    return name.startswith(".") or name.startswith("$") or name.upper() in SKIP_DIRS


def scan_working(
    root: str,
    cancel: Optional[threading.Event] = None,
    exclude: Optional[str] = None,
) -> list[WorkFolder]:
    """Find YYYY_MM_DD_<TITLE> folders (also inside YYYY / YYYY_MM containers)."""
    exclude_abs = os.path.normcase(os.path.abspath(exclude)) if exclude else None
    found: list[WorkFolder] = []

    def visit(d: str, depth: int) -> None:
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            if cancel is not None and cancel.is_set():
                return
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if _is_system_dir(e.name):
                continue
            if exclude_abs and os.path.normcase(os.path.abspath(e.path)) == exclude_abs:
                continue
            day, title = parse_dated_name(e.name)
            if day:
                found.append(WorkFolder(e.path, e.name, day, title))
            elif depth < 2 and (YEAR_RE.match(e.name) or YEAR_MONTH_RE.match(e.name)):
                visit(e.path, depth + 1)
            elif depth == 0:
                found.append(WorkFolder(e.path, e.name, None, e.name))

    visit(root, 0)
    for wf in found:
        if cancel is not None and cancel.is_set():
            break
        files = list_folder_files(wf.path)
        wf.poster = poster_candidates([p for p, _, _ in files])
        wf.files = len(files)
        wf.size = sum(s for _, _, s in files)
        m = find_manifest(wf.path)
        wf.manifest_algo = m[1] if m else None
        try:
            wf.newest_mtime = max((os.path.getmtime(p) for p, _, _ in files), default=os.path.getmtime(wf.path))
        except OSError:
            wf.newest_mtime = 0.0
    found.sort(key=lambda w: (w.day or date.min, w.name), reverse=True)
    return found
