"""Main window: owns the shared state (card scan, running transfer) and both UIs.

Easy mode and the Advanced pages are just two views of the same session:
  * the window detects cards and scans them once; every page renders that scan
    (card_scanning / card_changed signals)
  * every transfer is broadcast to every page (job_started / job_event / job_finished),
    so switching views mid-transfer shows the same live per-file / per-folder state
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from .. import APP_NAME
from ..drives import auto_workers, drive_info
from ..hashing import ALGORITHMS
from .. import sources
from ..scanner import CardScan, scan_card
from .session import CardEntry, source_limit, summarize
from ..settings import Settings, logs_dir
from ..transfer import Job, JobResult, TransferRunner, estimate_needed_bytes, free_bytes, human_bytes
from .archive_tab import ArchiveTab
from .dialogs import SettingsDialog, SummaryDialog
from .easy import EasyPage
from .easy_widgets import EasyDialog
from .offload_tab import OffloadPage
from .progress_panel import ProgressPanel
from .sidebar import Sidebar, VolumeWatcher
from .thumbs import ThumbLoader
from .verify_tab import VerifyTab
from .volumes import filesystem_type
from .widgets import Worker

FAT_LIMIT = 4 * 1024**3 - 1


class MainWindow(QMainWindow):
    busy_changed = Signal(bool)
    card_scanning = Signal(str)  # root being scanned
    card_changed = Signal(object)  # CardScan or None (the selected card)
    cards_changed = Signal()  # any card/device: added, read, removed, copying progress
    job_started = Signal(object)  # Job
    job_event = Signal(str, dict)  # progress / file / group / log ...
    job_finished = Signal(object)  # JobResult

    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.busy = False
        self._worker: Optional[Worker] = None
        self._cancel: Optional[Callable[[], None]] = None
        # shared session state
        self.volumes: list = []
        self.card_scan: Optional[CardScan] = None
        self.card_root = ""
        self.card_note = ""  # "", "empty", "unreadable", "removed"
        self.cards: dict[str, CardEntry] = {}  # every connected card / device, in the order they came in
        self.current_job: Optional[Job] = None
        self._scan_worker: Optional[Worker] = None
        self._job_roots: dict[str, str] = {}  # src path -> card root, while a job runs

        self.setWindowTitle(f"{APP_NAME}  ·  MultiMedia Transfer")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 700)

        self.loader = ThumbLoader(self)
        self.loader.enabled = self.settings.thumbnails
        self.watcher = VolumeWatcher(self)

        self.modes = QStackedWidget()
        self.setCentralWidget(self.modes)
        central = QWidget()
        central.setObjectName("Root")
        self.modes.addWidget(central)
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.sidebar = Sidebar()
        h.addWidget(self.sidebar)

        right = QWidget()
        right.setObjectName("Main")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(26, 20, 26, 18)
        rv.setSpacing(14)
        self.stack = QStackedWidget()
        self.offload = OffloadPage(self)
        self.archive = ArchiveTab(self)
        self.verify = VerifyTab(self)
        for page in (self.offload, self.archive, self.verify):
            self.stack.addWidget(page)
        rv.addWidget(self.stack, 1)
        self.progress = ProgressPanel(self.loader)
        self.progress.cancel_requested.connect(self.cancel_job)
        rv.addWidget(self.progress)
        h.addWidget(right, 1)

        self.easy = EasyPage(self)
        self.modes.addWidget(self.easy)

        self.sidebar.page_selected.connect(self.stack.setCurrentIndex)
        self.sidebar.settings_clicked.connect(self.open_settings)
        self.sidebar.card_clicked.connect(self._card_clicked)
        self.sidebar.easy_clicked.connect(lambda: self.set_easy(True))
        self.watcher.changed.connect(self._on_volumes)
        self.cards_changed.connect(lambda: self.sidebar.set_card_statuses(self.cards))
        self.job_event.connect(self._track_job_event)
        self.offload.dest_changed.connect(lambda _p: self.update_drives())

        self._update_mode()
        self.update_drives()
        if self.settings.easy_mode:
            self.set_easy(True)
        if self.settings.window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- easy / advanced
    @property
    def is_easy(self) -> bool:
        return self.modes.currentWidget() is self.easy

    def set_easy(self, on: bool) -> None:
        self.modes.setCurrentWidget(self.easy if on else self.modes.widget(0))
        self.settings.easy_mode = on
        self.settings.save()
        if on:
            self.easy.refresh()
            if not self.settings.easy_tour_done:
                QTimer.singleShot(400, self.easy.start_tour)
        else:
            self.offload.sync_from_settings()
            self.archive.sync_from_settings()
        self.update_drives()

    # ---------------------------------------------------------------- cards (shared by both views)
    @property
    def scanning(self) -> bool:
        """The selected card is still being read."""
        e = self.cards.get(self.card_root)
        return e is not None and e.state in ("waiting", "scanning")

    def _on_volumes(self, vols: list) -> None:
        self.volumes = list(vols)
        self.sidebar.set_volumes(vols)
        cams = {v.root: v for v in vols if v.camera}
        for r in list(self.cards):
            e = self.cards[r]
            if r in cams:
                e.vol = cams[r]
            elif not e.extra.get("folder"):  # a pulled card / unplugged device
                del self.cards[r]
            elif not os.path.isdir(r):
                del self.cards[r]
        new = [v for v in cams.values() if v.root not in self.cards]
        for v in new:
            self.cards[v.root] = CardEntry(v.root, v)
        if self.card_root and self.card_root not in self.cards:
            if self.cards:
                self.select_card(self._best_card())
            elif not self.busy:
                self.clear_card("removed")
        elif not self.card_root and self.cards:
            self.select_card(new[0].root if new else next(iter(self.cards)))
        self._kick_scans()
        self.cards_changed.emit()
        self.easy.on_volumes(vols)
        self.update_drives()

    def _best_card(self) -> str:
        """A card with something new, else the first one."""
        for e in self.cards.values():
            if e.new_files or e.state in ("waiting", "scanning"):
                return e.root
        return next(iter(self.cards))

    def _kick_scans(self) -> None:
        """Read the next card that hasn't been read yet (the selected one first). One at a time."""
        if self._scan_worker is not None and self._scan_worker.isRunning():
            return
        order = sorted(self.cards.values(), key=lambda e: e.root != self.card_root)
        e = next((x for x in order if x.state == "waiting"), None)
        if e is None:
            return
        e.state = "scanning"
        root, gen = e.root, e.extra.get("gen", 0)
        if root == self.card_root:
            self.card_scanning.emit(root)
        days = self.settings.phone_recent_days
        w = Worker(lambda emit: scan_card(root, phone_days=days), self)
        w.bridge.finished.connect(lambda scan, r=root, g=gen: self._entry_scanned(r, g, scan))
        w.bridge.failed.connect(lambda _tb, r=root, g=gen: self._entry_scanned(r, g, None))
        self._scan_worker = w
        w.start()
        self.cards_changed.emit()

    def _entry_scanned(self, root: str, gen: int, scan) -> None:
        self._scan_worker = None
        e = self.cards.get(root)
        if e is not None:
            if e.extra.get("gen", 0) != gen:
                e.state = "waiting"  # asked to read it again while this read ran
            else:
                if scan is None:
                    e.scan, e.state = None, "unreadable"
                elif not scan.files:
                    e.scan, e.state = None, "empty"
                else:
                    e.scan, e.state = scan, "ready"
                summarize(e, self.settings)
                if not self.busy:
                    e.job_total = e.job_done = e.job_failed = 0
                if root == self.card_root:
                    self._publish_selected()
        self.cards_changed.emit()
        QTimer.singleShot(0, self._kick_scans)

    def _publish_selected(self) -> None:
        e = self.cards.get(self.card_root)
        if e is None:
            return
        self.sidebar.set_active_card(e.root)
        if e.state in ("waiting", "scanning"):
            self.card_scan = None
            self.card_scanning.emit(e.root)
            return
        self.card_scan = e.scan
        self.card_note = {"empty": "empty", "unreadable": "unreadable"}.get(e.state, "")
        self.card_changed.emit(self.card_scan)

    def select_card(self, root: str) -> None:
        """Show this card/device (instant if it has been read already)."""
        if root not in self.cards:
            vol = next((v for v in self.volumes if v.root == root), None)
            self.cards[root] = CardEntry(root, vol)
            if vol is None:
                self.cards[root].extra["folder"] = True  # "Copy from a folder"
        self.card_root = root
        self.card_note = ""
        self._publish_selected()
        self._kick_scans()
        self.cards_changed.emit()

    def scan_card(self, root: str) -> None:
        """Select a card/device and read it (again)."""
        e = self.cards.get(root)
        if e is not None:
            e.extra["gen"] = e.extra.get("gen", 0) + 1
            e.state = "waiting"
        self.select_card(root)

    def rescan_roots(self, roots) -> None:
        for r in roots:
            e = self.cards.get(r)
            if e is not None and self._present(r):
                e.extra["gen"] = e.extra.get("gen", 0) + 1
                e.state = "waiting"
        if self.card_root in roots:
            self._publish_selected()
        self._kick_scans()
        self.cards_changed.emit()

    def resummarize(self) -> None:
        """The destination changed: recount what's new on every card."""
        for e in self.cards.values():
            summarize(e, self.settings)
        self.cards_changed.emit()

    @staticmethod
    def _present(root: str) -> bool:
        if sources.is_portable(root):
            return sources.src_exists(root)
        return os.path.isdir(root)

    def _card_present(self) -> bool:
        return self._present(self.card_root)

    def rescan_card(self) -> None:
        if self.card_root and self._card_present():
            self.scan_card(self.card_root)
        elif self.card_root:
            self.clear_card("removed")

    def clear_card(self, note: str = "") -> None:
        self.card_scan = None
        self.card_root = ""
        self.card_note = note
        self.sidebar.set_active_card("")
        self.card_changed.emit(None)

    def ready_cards(self) -> list:
        """Cards/devices that have been read and have files."""
        return [e for e in self.cards.values() if e.state == "ready" and e.scan is not None]

    def source_limits(self, roots, dest_root: str) -> dict:
        out = {}
        for r in roots:
            e = self.cards.get(r)
            out[r] = source_limit(e if e is not None else r, dest_root, self.settings)[0]
        return out

    # ---- per-device progress while a job runs
    def _job_started_cards(self, job: Job) -> None:
        self._job_roots = {}
        for e in self.cards.values():
            e.job_total = e.job_done = e.job_failed = 0
        if job.kind != "offload":
            return
        for g in job.groups:
            for t in g.tasks:
                root = t.src_root or g.source_root
                self._job_roots[t.src] = root
                e = self.cards.get(root)
                if e is not None:
                    e.job_total += 1
        self.cards_changed.emit()

    def _track_job_event(self, kind: str, p: dict) -> None:
        if kind != "file" or not self._job_roots:
            return
        e = self.cards.get(self._job_roots.get(p.get("src", ""), ""))
        if e is not None:
            e.job_done += 1
            if p.get("status") == "failed":
                e.job_failed += 1
            self.cards_changed.emit()

    # ---------------------------------------------------------------- navigation
    def show_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.sidebar.select_page(idx)

    def _card_clicked(self, root: str) -> None:
        self.show_page(0)
        self.select_card(root)

    def update_drives(self) -> None:
        s = self.settings
        self.sidebar.set_drives(s.offload_dest, s.archive_dest)

    # ---------------------------------------------------------------- settings
    def _update_mode(self) -> None:
        s = self.settings
        algo = ALGORITHMS.get(s.algo, (s.algo,))[0].split(" (")[0]
        mode = "read-back verify" if s.verify_readback else "size check only"
        par = "auto" if s.workers <= 0 else str(s.workers)
        self.sidebar.set_mode_text(f"{algo}  ·  {mode}  ·  files at once: {par}")

    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            self.loader.enabled = self.settings.thumbnails
            self._update_mode()
            for t in (self.offload, self.archive, self.verify):
                t.settings_changed()
            self.easy.apply_palette()

    # ---------------------------------------------------------------- jobs
    def resolve_workers(self, job: Job, src_root: str, dest_root: str) -> None:
        vol = next((v for v in self.volumes if v.root == src_root), None)
        if sources.is_portable(src_root) or (vol is not None and vol.portable):
            job.options.workers = 1
            job.options.workers_reason = "USB device (phone or camera): one file at a time"
            return
        if self.settings.workers > 0:
            n, why = self.settings.workers, "set in Settings"
        else:
            src, dst = drive_info(src_root), drive_info(dest_root)
            n, why = auto_workers(src, dst)
        job.options.workers = n
        job.options.workers_reason = why

    def preflight(self, job: Job, dest_root: str, easy: bool = False) -> bool:
        need = estimate_needed_bytes(job)
        free = free_bytes(dest_root)
        if free >= 0 and need > free:
            if easy:
                EasyDialog(self, "bad", "Not enough space on that drive",
                           f"This needs <b>{human_bytes(need)}</b>, but the drive only has "
                           f"<b>{human_bytes(free)}</b> free.<br><br>Free up some space, or choose a "
                           "folder on a bigger drive.", ["OK"]).ask()
            else:
                QMessageBox.critical(
                    self, "Not enough space",
                    f"This needs {human_bytes(need)} but {dest_root} only has {human_bytes(free)} free.")
            return False
        fs = filesystem_type(dest_root)
        if fs in ("vfat", "fat", "fat32", "msdos", "fat16"):
            big = [t for g in job.groups for t in g.tasks if t.size > FAT_LIMIT]
            if big:
                if easy:
                    EasyDialog(self, "bad", "That drive can't store large videos",
                               "It's formatted as FAT32, which can't hold files over 4 GB. "
                               "Choose a folder on a different drive (exFAT or NTFS).", ["OK"]).ask()
                else:
                    QMessageBox.critical(
                        self, "Destination is FAT32",
                        f"{len(big)} file(s) are larger than 4 GB, which FAT32 cannot store "
                        f"(e.g. {big[0].rel}). Use an exFAT or NTFS drive.")
                return False
        return True

    def _set_busy(self, b: bool) -> None:
        self.busy = b
        if b:
            self.loader.pause()
        else:
            self.loader.resume()
        self.busy_changed.emit(b)

    def run_transfer(self, job: Job, after: Optional[Callable[[JobResult], None]] = None, on_event=None,
                     show_summary: bool = True) -> None:
        if self.busy:
            return
        self.current_job = job
        self._job_started_cards(job)
        self.job_started.emit(job)
        self.run_runner(job.title, lambda emit, logs: TransferRunner(job, emit=emit, log_dir=logs),
                        after=after, on_event=on_event, show_summary=show_summary)

    def run_runner(self, title: str, factory, after=None, on_event=None, show_summary: bool = True) -> None:
        if self.busy:
            return
        holder: dict = {}

        def fn(emit):
            r = factory(emit, logs_dir())
            holder["r"] = r
            if holder.get("cancel"):
                r.cancel()
            return r.run()

        def cancel() -> None:
            holder["cancel"] = True
            if "r" in holder:
                holder["r"].cancel()

        w = Worker(fn, self)
        w.bridge.event.connect(self.progress.on_event)
        w.bridge.event.connect(self.job_event.emit)
        if on_event:
            w.bridge.event.connect(on_event)
        w.bridge.finished.connect(lambda res: self._done(res, after, show_summary))
        w.bridge.failed.connect(self._crashed)
        self._worker = w
        self._cancel = cancel
        self._set_busy(True)
        self.progress.start(title)
        w.start()

    def cancel_job(self) -> None:
        if self._cancel:
            self.progress.cancelling()
            self._cancel()

    def _done(self, res: JobResult, after, show_summary: bool = True) -> None:
        self._set_busy(False)
        self._cancel = None
        self.current_job = None
        if res.cancelled:
            head, ok = "Cancelled", False
        elif res.ok and not res.warnings:
            head, ok = "Done  ✓", True
        else:
            head, ok = "Finished with problems", False
        if res.kind == "verify":
            detail = f"{res.verified_ok} bit-perfect, {res.mismatched} mismatched, {res.missing} missing"
        elif res.kind == "checksum":
            detail = f"{res.files_ok} files hashed"
        else:
            detail = (f"{res.files_ok}/{res.files_total} files verified   ·   {human_bytes(res.bytes_written)} written"
                      f"   ·   {res.files_failed} failed")
            if res.throughput:
                detail += f"   ·   {human_bytes(res.throughput)}/s overall"
        self.progress.finish(ok, head, detail)
        self.update_drives()
        self.job_finished.emit(res)  # every view updates its live state first
        if show_summary:
            SummaryDialog(res, self).exec()
        if after:
            after(res)
        if res.kind == "offload":  # re-read every card the job touched: refreshes "already saved" everywhere
            roots = {r for r in self._job_roots.values() if r} or ({self.card_root} if self.card_root else set())
            self._job_roots = {}
            self.rescan_roots(roots)

    def _crashed(self, tb: str) -> None:
        self._set_busy(False)
        self._cancel = None
        self.current_job = None
        self.progress.finish(False, "Error", tb.strip().splitlines()[-1])
        self.progress.append_log("error", tb)
        QMessageBox.critical(self, "Unexpected error", tb[-3000:])

    def closeEvent(self, e):  # noqa: N802
        if self.busy:
            ans = QMessageBox.question(self, "Transfer running", "A transfer is running. Cancel it and quit?")
            if ans != QMessageBox.Yes:
                e.ignore()
                return
            self.cancel_job()
            if self._worker:
                self._worker.wait(15000)
        self.watcher.stop()
        self.archive.shutdown()
        for w in (self.easy.arch_worker, self._scan_worker):
            if w is not None and w.isRunning():
                w.wait(5000)
        self.loader.clear_queue()
        self.loader.pool.waitForDone(5000)
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        super().closeEvent(e)
