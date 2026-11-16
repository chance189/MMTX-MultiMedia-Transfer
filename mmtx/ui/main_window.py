"""Main window: sidebar + pages + transfer bar, and job orchestration."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QByteArray, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from .. import APP_NAME
from ..drives import auto_workers, drive_info
from ..hashing import ALGORITHMS
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

    def __init__(self, settings: Optional[Settings] = None):
        super().__init__()
        self.settings = settings or Settings.load()
        self.busy = False
        self._worker: Optional[Worker] = None
        self._cancel: Optional[Callable[[], None]] = None
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
        self.watcher.changed.connect(self._route_volumes)
        self.offload.source_changed.connect(self.sidebar.set_active_card)
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
        # whichever view is showing reacts to cards; give it the current picture right away
        self._route_volumes(self.watcher.volumes)
        if on:
            self.easy.refresh()
            if not self.settings.easy_tour_done:
                QTimer.singleShot(400, self.easy.start_tour)
        else:
            self.offload.sync_from_settings()
            self.archive.sync_from_settings()
            self.update_drives()

    def _route_volumes(self, vols: list) -> None:
        self.sidebar.set_volumes(vols)
        (self.easy if self.is_easy else self.offload).on_volumes(vols)
        self.update_drives()

    # ---------------------------------------------------------------- navigation
    def show_page(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.sidebar.select_page(idx)

    def _card_clicked(self, root: str) -> None:
        self.show_page(0)
        if not self.busy:
            self.offload.set_source(root)

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

    # ---------------------------------------------------------------- jobs
    def resolve_workers(self, job: Job, src_root: str, dest_root: str) -> None:
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
                EasyDialog(self, "bad", "Not enough room on that drive",
                           f"This needs <b>{human_bytes(need)}</b> but the drive only has "
                           f"<b>{human_bytes(free)}</b> free.<br><br>Free up some space or choose a bigger drive.",
                           ["OK"]).ask()
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
                    EasyDialog(self, "bad", "That drive can't hold big video files",
                               "It's formatted as FAT32, which can't store files over 4 GB. "
                               "Choose a different drive (exFAT or NTFS).", ["OK"]).ask()
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
        if show_summary:
            SummaryDialog(res, self).exec()
        if after:
            after(res)

    def _crashed(self, tb: str) -> None:
        self._set_busy(False)
        self._cancel = None
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
        for w in (self.easy.scan_worker, self.easy.arch_worker):
            if w is not None and w.isRunning():
                w.wait(5000)
        if self.offload.scan_worker and self.offload.scan_worker.isRunning():
            self.offload.scan_worker.wait(5000)
        self.loader.clear_queue()
        self.loader.pool.waitForDone(5000)
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        super().closeEvent(e)
