"""Google Photos takeouts: one shared session for Advanced and Easy mode.

Zips you add in either view show up in both. Reading them (file lists only, a few seconds even
for a 50 GB part) runs in the background, again whenever the zips, the library or the options change.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from ..takeout import (
    TakeoutOptions,
    TakeoutPlan,
    TakeoutRunner,
    find_takeouts,
    library_stats,
    plan_takeout,
)
from ..transfer import free_bytes, human_bytes
from .widgets import Worker


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


class TakeoutSession(QObject):
    changed = Signal()  # zips, plan, library or running state changed
    photo = Signal(dict)  # one photo landed (or was a duplicate / failed) during an import

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.s = host.settings
        self.paths: list[str] = []  # what the user added: zips and folders
        self.plan: Optional[TakeoutPlan] = None
        self.planning = False
        self.plan_error = ""
        self.reimport = False
        self.running = False
        self.last_result = None
        self.lib_stats: Optional[dict] = None
        self._gen = 0
        self._worker: Optional[Worker] = None
        self._again = False
        self._refresh_stats()

    # ---------------------------------------------------------------- zips
    def add(self, paths: list[str]) -> int:
        """Add zips / folders. Returns how many new takeout parts that found."""
        found = find_takeouts([p for p in paths if p], self.library)
        have = {_norm(p) for p in self.paths}
        new = [p for p in found if _norm(p) not in have]
        if not new:
            return 0
        self.paths.extend(new)
        self.last_result = None
        zips = [p for p in new if p.lower().endswith(".zip")]
        if zips:
            self.s.takeout_last_dir = os.path.dirname(zips[0])
            self.s.save()
        self.replan()
        return len(new)

    def remove(self, paths: list[str]) -> None:
        drop = {_norm(p) for p in paths}
        self.paths = [p for p in self.paths if _norm(p) not in drop]
        self.last_result = None
        self.replan()

    def clear(self) -> None:
        self.paths = []
        self.last_result = None
        self.plan = None
        self.plan_error = ""
        self.reimport = False
        self._gen += 1
        self.planning = False
        self.changed.emit()

    # ---------------------------------------------------------------- library
    @property
    def library(self) -> str:
        return self.s.takeout_library

    def library_ok(self) -> bool:
        lib = self.library
        if not lib:
            return False
        if os.path.isdir(lib):
            return True
        parent = os.path.dirname(lib.rstrip("\\/"))  # a new folder on a connected drive is fine
        return bool(parent) and os.path.isdir(parent)

    def set_library(self, path: str) -> None:
        self.s.takeout_library = os.path.normpath(path) if path else ""
        self.s.save()
        self.last_result = None
        self._refresh_stats()
        self.replan()

    def _refresh_stats(self) -> None:
        self.lib_stats = library_stats(self.library) if self.library else None

    def library_line(self, free: bool = True) -> str:
        """'12,034 files · 2009 to 2026 · 1.2 TB free' for the views."""
        lib = self.library
        if not lib:
            return ""
        fb = free_bytes(lib if os.path.isdir(lib) else os.path.dirname(lib.rstrip("\\/")))
        parts = []
        st = self.lib_stats
        if st and st["files"]:
            parts.append(f"{st['files']:,} files")
            if st["first"] and st["last"]:
                parts.append(f"{st['first'][:4]} to {st['last'][:4]}" if st["first"][:4] != st["last"][:4]
                             else st["first"][:4])
        elif os.path.isdir(lib):
            parts.append("empty, ready for photos")
        else:
            parts.append("new folder")
        if free and fb >= 0:
            parts.append(f"{human_bytes(fb)} free")
        return "  ·  ".join(parts)

    # ---------------------------------------------------------------- reading the zips
    def replan(self) -> None:
        if not self.paths:
            self.plan = None
            self.planning = False
            self.changed.emit()
            return
        if self._worker is not None and self._worker.isRunning():
            self._again = True
            self.planning = True
            self.changed.emit()
            return
        self._gen += 1
        gen = self._gen
        paths = list(self.paths)
        lib = self.library
        trash, reimport = self.s.takeout_trash, self.reimport
        self.planning = True
        self.plan_error = ""
        w = Worker(lambda emit: plan_takeout(paths, lib, include_trash=trash, reimport=reimport), self.host)
        w.bridge.finished.connect(lambda plan: self._planned(gen, plan))
        w.bridge.failed.connect(lambda tb: self._plan_failed(gen, tb))
        self._worker = w
        w.start()
        self.changed.emit()

    def _planned(self, gen: int, plan: TakeoutPlan) -> None:
        if self._again:
            self._again = False
            self.replan()
            return
        if gen != self._gen:
            return
        self.plan = plan
        self.planning = False
        self.changed.emit()

    def _plan_failed(self, gen: int, tb: str) -> None:
        if self._again:
            self._again = False
            self.replan()
            return
        if gen != self._gen:
            return
        self.planning = False
        self.plan = None
        self.plan_error = tb.strip().splitlines()[-1] if tb.strip() else "couldn't read the zips"
        self.changed.emit()

    # ---------------------------------------------------------------- summaries for the views
    def parts(self) -> list:
        return list(self.plan.archives) if self.plan else []

    def summary(self) -> str:
        p = self.plan
        if not p:
            return ""
        bits = []
        n = sum(1 for a in p.archives if a.zipped)
        unpacked = sum(1 for a in p.archives if not a.zipped and a.label.lower() == "takeout")
        loose = sum(a.media for a in p.archives if not a.zipped and a.label.lower() != "takeout")
        if n:
            bits.append(f"{n} zip{'s' if n != 1 else ''}")
        if unpacked:
            bits.append(f"{unpacked} unpacked folder{'s' if unpacked != 1 else ''}")
        if loose:
            bits.append(f"{loose:,} loose file{'s' if loose != 1 else ''}")
        what = []
        if p.photos:
            what.append(f"{p.photos:,} photo{'s' if p.photos != 1 else ''}")
        if p.videos:
            what.append(f"{p.videos:,} video{'s' if p.videos != 1 else ''}")
        if what:
            bits.append(" and ".join(what))
        years = sorted(y for y in p.years if y)
        if years:
            bits.append(f"{years[0]} to {years[-1]}" if years[0] != years[-1] else str(years[0]))
        return "  ·  ".join(bits)

    def new_count(self) -> int:
        p = self.plan
        if not p:
            return 0
        return max(0, len(p.items) - p.copies - p.in_library)

    # ---------------------------------------------------------------- running
    def start(self, after: Optional[Callable] = None, show_summary: bool = True, on_event=None) -> bool:
        plan = self.plan
        if plan is None or not plan.items or self.host.busy or not self.library_ok():
            return False
        plan.library = self.library
        os.makedirs(self.library, exist_ok=True)
        s = self.s
        opts = TakeoutOptions(verify_readback=s.verify_readback, uncached=s.uncached,
                              workers=max(1, min(3, s.workers)) if s.workers > 0 else 2,
                              scratch=s.takeout_scratch)
        self.running = True
        self.changed.emit()

        def event(kind: str, p: dict) -> None:
            if kind == "photo":
                self.photo.emit(p)
            if on_event:
                on_event(kind, p)

        def done(res) -> None:
            self.running = False
            self.last_result = res
            self.reimport = False
            self._refresh_stats()
            self.replan()  # the zips that went in now read as "already in the library"
            if after:
                after(res)

        n = len(plan.items)
        title = f"Google Photos: {n:,} file{'s' if n != 1 else ''} into {os.path.basename(self.library) or self.library}"
        self.host.run_runner(title, lambda emit, logs: TakeoutRunner(plan, opts, emit=emit, log_dir=logs),
                             after=done, on_event=event, show_summary=show_summary)
        return True
