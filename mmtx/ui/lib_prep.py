"""Making every preview ahead of time, in the background (MMTX Library and MMTX itself).

The cost is paid once per file: previews are keyed by content, kept on this computer, and survive the
folder moving from the working drive to the archive. On-screen requests always go first: the
background work waits while the grid is making something you're looking at, while you've paused it,
and while MMTX is copying.
"""

from __future__ import annotations

import os
import shutil
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal

from .. import catalog as C
from .. import previews as PV
from .widgets import Worker


def eta_text(seconds: float) -> str:
    if seconds <= 0:
        return ""
    if seconds < 60:
        return "under a minute left"
    m = int(seconds // 60)
    if m < 60:
        return f"about {m} min left"
    h, m = divmod(m, 60)
    return f"about {h} h {m:02d} min left"


class PreviewManager(QObject):
    """Runs previews.prepare() over a list of libraries, one worker thread at a time.

    targets: [(root, folders or None, name, library_id)]; folders limits it to those day folders."""

    progress = Signal(dict)  # {name, done, total, left, paused}
    finished = Signal(dict)  # {made, failed, total, cancelled}

    def __init__(self, parent=None, store: Optional[PV.PreviewStore] = None):
        super().__init__(parent)
        self.store = store or PV.shared_store()
        self.worker: Optional[Worker] = None
        self.cancel = threading.Event()
        self.pause = threading.Event()
        self.user_paused = False
        self.job_paused = False
        self._again: list = []
        self.current = ""

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def set_paused(self, user: Optional[bool] = None, job: Optional[bool] = None) -> None:
        if user is not None:
            self.user_paused = user
        if job is not None:
            self.job_paused = job
        if self.user_paused or self.job_paused:
            self.pause.set()
        else:
            self.pause.clear()

    def start(self, targets: list, restart: bool = False) -> None:
        """Prepare these libraries. Already running: ``restart`` cancels and begins with these (a different
        library was opened); otherwise they're done right after the current pass."""
        targets = [t for t in targets if t and t[0]]
        if not targets:
            return
        if self.busy:
            if restart:
                self._again = targets
                self.cancel.set()
            else:
                seen = {(t[0], tuple(t[1] or ())) for t in self._again}
                self._again += [t for t in targets if (t[0], tuple(t[1] or ())) not in seen]
            return
        self._run(targets)

    def _run(self, targets: list) -> None:
        self.cancel = threading.Event()
        cancel, pause, store = self.cancel, self.pause, self.store

        def work(emit):
            tot = {"made": 0, "failed": 0, "total": 0, "cancelled": False}
            for root, folders, name, lib_id in targets:
                if cancel.is_set():
                    break
                if not os.path.isfile(os.path.join(root, C.LIBRARY_DB)):
                    continue
                try:
                    with C.Catalog(root, read_only=True) as cat:
                        rows = cat.query()
                except Exception:  # noqa: BLE001
                    continue
                if folders:
                    want = set(folders)
                    rows = [r for r in rows if r.folder in want]
                emit("start", {"name": name, "total": 0})
                res = PV.prepare(root, rows, store, cancel=cancel, pause=pause,
                                 on_progress=lambda d, t, left, n=name: emit(
                                     "p", {"name": n, "done": d, "total": t, "left": left}))
                tot["made"] += res.made
                tot["failed"] += res.failed
                tot["total"] += res.total
                tot["cancelled"] = tot["cancelled"] or res.cancelled
                if not res.cancelled and folders is None and lib_id and not res.missing:
                    legacy = C.thumbs_dir(lib_id)  # MMTX 2.2's per-library thumbnails: all taken over now
                    if os.path.isdir(legacy):
                        shutil.rmtree(legacy, ignore_errors=True)
            return tot

        w = Worker(work, self)
        w.bridge.event.connect(self._event)
        w.bridge.finished.connect(self._done)
        w.bridge.failed.connect(lambda _tb: self._done({"made": 0, "failed": 0, "total": 0, "cancelled": True}))
        self.worker = w
        w.start()

    def _event(self, kind: str, p: dict) -> None:
        if kind == "start":
            self.current = p.get("name", "")
            return
        p["paused"] = self.pause.is_set()
        self.progress.emit(p)

    def _done(self, tot: dict) -> None:
        self.worker = None
        again, self._again = self._again, []
        if again:
            self._run(again)
            return
        self.current = ""
        self.finished.emit(tot)

    def stop(self, wait_ms: int = 8000) -> None:
        self._again = []
        self.cancel.set()
        if self.worker is not None:
            self.worker.wait(wait_ms)
