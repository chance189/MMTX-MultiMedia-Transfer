"""Turn scans + UI choices into transfer Jobs."""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass
from datetime import date

from .cameras import gopro_sorted_name
from .manifest import load_folder_manifest
from .scanner import CardScan, DayGroup, WorkFolder, parse_dated_name
from .transfer import FileTask, Group, Job, JobOptions, list_folder_files, same_device

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(text: str, spaces_to_underscores: bool = True) -> str:
    s = _ILLEGAL.sub("", text).strip().strip(".")
    if spaces_to_underscores:
        s = re.sub(r"\s+", "_", s)
    return s


def day_folder_name(day: date, title: str, spaces_to_underscores: bool = True) -> str:
    t = sanitize(title, spaces_to_underscores)
    return f"{day:%Y_%m_%d}_{t}" if t else f"{day:%Y_%m_%d}"


def existing_day_folders(dest_root: str) -> dict[date, list[str]]:
    out: dict[date, list[str]] = {}
    try:
        names = sorted(os.listdir(dest_root))
    except OSError:
        return out
    for n in names:
        if not os.path.isdir(os.path.join(dest_root, n)):
            continue
        d, _ = parse_dated_name(n)
        if d:
            out.setdefault(d, []).append(n)
    return out


@dataclass
class OffloadOptions:
    algo: str
    camera_subfolders: bool = False
    skip_proxies: bool = False
    delete_source: bool = False
    keep_structure: bool = False
    rename_gopro: bool = False
    verify_readback: bool = True
    uncached: bool = True
    workers: int = 1
    workers_reason: str = ""


def _offload_rel(f, device_folders: dict[str, str], opts: OffloadOptions) -> str:
    if opts.keep_structure:
        name = f.rel
    elif opts.rename_gopro and f.profile == "gopro":
        name = gopro_sorted_name(f.name)
    else:
        name = f.name
    sub = device_folders.get(f.profile, "") if opts.camera_subfolders else ""
    return posixpath.join(sub, name) if sub else name


def offload_files(day: DayGroup, opts: OffloadOptions):
    return [f for f in day.files if not (opts.skip_proxies and f.role in ("proxy", "thumb"))]


def day_presence(day: DayGroup, dest_dir: str, device_folders: dict[str, str], opts: OffloadOptions) -> tuple[int, int, set]:
    """(files already at destination with matching size, files to transfer, set of present source paths)."""
    files = offload_files(day, opts)
    present: set[str] = set()
    if os.path.isdir(dest_dir):
        for f in files:
            dst = os.path.join(dest_dir, *_offload_rel(f, device_folders, opts).split("/"))
            try:
                if os.path.getsize(dst) == f.size:
                    present.add(f.path)
            except OSError:
                pass
    return len(present), len(files), present


def plan_offload(
    scan: CardScan,
    selections: list[tuple[DayGroup, str]],
    dest_root: str,
    device_folders: dict[str, str],
    opts: OffloadOptions,
) -> Job:
    groups = []
    for day, folder in selections:
        dest_dir = os.path.join(dest_root, folder)
        g = Group(
            name=folder,
            dest_dir=dest_dir,
            algo=opts.algo,
            source_root=scan.root,
            delete_source=opts.delete_source,
            prune_source_dir=False,
        )
        for f in offload_files(day, opts):
            rel = _offload_rel(f, device_folders, opts)
            g.tasks.append(FileTask(src=f.path, dst=os.path.join(dest_dir, *rel.split("/")), size=f.size, rel=rel))
        if g.tasks:
            groups.append(g)
    n = len(groups)
    title = f"Offload {n} day{'s' if n != 1 else ''} from {scan.root}"
    return Job("offload", title, groups, JobOptions(verify_readback=opts.verify_readback, uncached=opts.uncached,
                                                     workers=opts.workers, workers_reason=opts.workers_reason))


# ---------------------------------------------------------------- archive

ARCHIVE_LAYOUTS = {
    "year": "Year folders   (Archive/2026/2026_11_14_Title)",
    "year_month": "Year + month   (Archive/2026/2026_11/2026_11_14_Title)",
    "flat": "Flat           (Archive/2026_11_14_Title)",
}


def archive_dest(archive_root: str, wf: WorkFolder, layout: str) -> str:
    if layout == "flat":
        return os.path.join(archive_root, wf.name)
    if wf.day is None:
        return os.path.join(archive_root, "Undated", wf.name)
    y = f"{wf.day.year:04d}"
    if layout == "year_month":
        return os.path.join(archive_root, y, f"{wf.day.year:04d}_{wf.day.month:02d}", wf.name)
    return os.path.join(archive_root, y, wf.name)


def _existing_ancestor(p: str) -> str:
    p = os.path.abspath(p)
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p


def is_inside(child: str, parent: str) -> bool:
    c = os.path.normcase(os.path.abspath(child))
    p = os.path.normcase(os.path.abspath(parent))
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def plan_archive(
    folders: list[WorkFolder],
    archive_root: str,
    layout: str,
    move: bool,
    default_algo: str,
    verify_readback: bool = True,
    uncached: bool = True,
    workers: int = 1,
    workers_reason: str = "",
) -> Job:
    groups = []
    anchor = _existing_ancestor(archive_root)
    for wf in folders:
        if is_inside(archive_root, wf.path):
            continue  # never archive a folder into itself
        dest_dir = archive_dest(archive_root, wf, layout)
        algo, entries = load_folder_manifest(wf.path)
        g = Group(
            name=wf.name,
            dest_dir=dest_dir,
            algo=algo or default_algo,
            source_root=wf.path,
            source_dir=wf.path,
            source_manifest=entries,
            delete_source=move,
            prune_source_dir=True,
        )
        for p, rel, size in list_folder_files(wf.path):
            g.tasks.append(FileTask(src=p, dst=os.path.join(dest_dir, *rel.split("/")), size=size, rel=rel, src_rel=rel))
        g.fast_rename = move and not os.path.exists(dest_dir) and same_device(wf.path, anchor)
        groups.append(g)
    n = len(groups)
    verb = "Move" if move else "Copy"
    return Job("archive", f"{verb} {n} folder{'s' if n != 1 else ''} to archive {archive_root}", groups,
               JobOptions(verify_readback=verify_readback, uncached=uncached, workers=workers,
                          workers_reason=workers_reason))


def title_from_existing(name: str) -> str:
    _, t = parse_dated_name(name)
    return t
