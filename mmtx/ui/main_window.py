"""Main window: header, tabs, shared progress panel, job orchestration."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QByteArray, Signal
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from .. import APP_NAME
from ..hashing import ALGORITHMS
from ..settings import Settings, logs_dir
from ..transfer import Job, JobResult, TransferRunner, estimate_needed_bytes, free_bytes, human_bytes
from .archive_tab import ArchiveTab
from .dialogs import SettingsDialog, SummaryDialog
from .offload_tab import OffloadTab
from .progress_panel import ProgressPanel
from .verify_tab import VerifyTab
from .volumes import filesystem_type
from .widgets import Worker, button, label

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
        self.resize(1280, 860)
        self.setMinimumSize(1020, 680)

        central = QWidget()
        central.setObjectName("Page")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(label("MMTX", "Brand"))
        header.addSpacing(6)
        header.addWidget(label("MultiMedia Transfer  ·  offload, archive, verify", "BrandSub"))
        header.addStretch(1)
        self.badge = label("", "Badge")
        self.badge.setToolTip("Checksum and verification mode (change in Settings)")
        header.addWidget(self.badge)
        header.addWidget(button("Settings", "Ghost", slot=self.open_settings))
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.offload = OffloadTab(self)
        self.archive = ArchiveTab(self)
        self.verify = VerifyTab(self)
        self.tabs.addTab(self.offload, "Offload card")
        self.tabs.addTab(self.archive, "Archive")
        self.tabs.addTab(self.verify, "Verify")
        root.addWidget(self.tabs, 1)

        self.progress = ProgressPanel()
        self.progress.cancel_requested.connect(self.cancel_job)
        root.addWidget(self.progress)

        self._update_badge()
        if self.settings.window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- settings
    def _update_badge(self) -> None:
        s = self.settings
        algo = ALGORITHMS.get(s.algo, (s.algo,))[0].split(" (")[0]
        mode = "read-back verify" if s.verify_readback else "size check only"
        self.badge.setText(f"{algo}  ·  {mode}")

    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            self._update_badge()
            for t in (self.offload, self.archive, self.verify):
                t.settings_changed()

    # ---------------------------------------------------------------- jobs
    def preflight(self, job: Job, dest_root: str) -> bool:
        need = estimate_needed_bytes(job)
        free = free_bytes(dest_root)
        if free >= 0 and need > free:
            QMessageBox.critical(
                self, "Not enough space",
                f"This needs {human_bytes(need)} but {dest_root} only has {human_bytes(free)} free.")
            return False
        fs = filesystem_type(dest_root)
        if fs in ("vfat", "fat", "fat32", "msdos", "fat16"):
            big = [t for g in job.groups for t in g.tasks if t.size > FAT_LIMIT]
            if big:
                QMessageBox.critical(
                    self, "Destination is FAT32",
                    f"{len(big)} file(s) are larger than 4 GB, which FAT32 cannot store "
                    f"(e.g. {big[0].rel}). Use an exFAT or NTFS drive.")
                return False
        return True

    def _set_busy(self, b: bool) -> None:
        self.busy = b
        self.tabs.tabBar().setEnabled(not b)
        self.busy_changed.emit(b)

    def run_transfer(self, job: Job, after: Optional[Callable[[JobResult], None]] = None) -> None:
        self.run_runner(job.title, lambda emit, logs: TransferRunner(job, emit=emit, log_dir=logs), after=after)

    def run_runner(self, title: str, factory, after=None, on_event=None) -> None:
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
        w.bridge.finished.connect(lambda res: self._done(res, after))
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

    def _done(self, res: JobResult, after) -> None:
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
            detail = (f"{res.files_ok}/{res.files_total} files verified  ·  {human_bytes(res.bytes_written)} written"
                      f"  ·  {res.files_failed} failed")
        self.progress.finish(ok, head, detail)
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
        self.offload.shutdown()
        self.archive.shutdown()
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        super().closeEvent(e)
