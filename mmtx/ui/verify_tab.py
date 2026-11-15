"""Verify tab: re-check any folder (or a whole archive drive) against its manifests."""

from __future__ import annotations

import os

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..manifest import find_manifest
from ..scanner import scan_working
from ..transfer import ChecksumRunner, VerifyRunner, find_manifest_folders
from . import theme
from .widgets import Card, NumItem, PathPicker, PillDelegate, button, label, page_header, ro_item

COLS = ["Folder", "Algorithm", "Verified", "Mismatch", "Missing", "Not in manifest", "Result"]


class VerifyTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.host = host
        self.s = host.settings
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        hrow, _t, _s = page_header("Verify", "Re-read footage from disk and prove it still matches the checksums recorded when it was copied.")
        root.addLayout(hrow)

        c = Card("Check footage against its recorded checksums")
        c.body.addWidget(label(
            "Every offloaded day and archived folder carries a <b>_mmtx_checksums</b> file. Verifying re-reads every "
            "file from disk and compares it, so you can prove an archive drive is still bit-perfect years later. "
            "Point it at a single folder or at the whole archive drive.", "Muted", wrap=True))
        self.path = PathPicker("Folder or whole archive drive", "Choose folder to verify")
        self.path.set_path(self.s.verify_path, emit=False)
        self.path.changed.connect(self._path_changed)
        c.body.addWidget(self.path)
        row = QHBoxLayout()
        self.recursive = QCheckBox("Include all subfolders")
        self.recursive.setChecked(self.s.verify_recursive)
        self.recursive.toggled.connect(self._rec_changed)
        row.addWidget(self.recursive)
        row.addStretch(1)
        self.create_btn = button("Create missing checksums", tip="Hash dated folders that have no manifest yet",
                                 slot=self._create)
        row.addWidget(self.create_btn)
        self.go = button("Verify checksums", "Primary", slot=self._verify, icon_name="verify", icon_color="#ffffff")
        self.go.setMinimumWidth(220)
        row.addWidget(self.go)
        c.body.addLayout(row)
        root.addWidget(c)

        r = Card("Results")
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setItemDelegateForColumn(6, PillDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate([0, 90, 80, 80, 80, 120, 140]):
            if w:
                self.table.setColumnWidth(i, w)
        r.body.addWidget(self.table, 1)
        root.addWidget(r, 1)

        self.host.busy_changed.connect(self._busy)
        self._busy(False)

    def _busy(self, busy: bool) -> None:
        self.go.setEnabled(not busy)
        self.create_btn.setEnabled(not busy)

    def _path_changed(self, p: str) -> None:
        self.s.verify_path = p
        self.s.save()

    def _rec_changed(self, v: bool) -> None:
        self.s.verify_recursive = v
        self.s.save()

    def settings_changed(self) -> None:
        pass

    # ---------------------------------------------------------------- verify
    def _verify(self) -> None:
        root = self.path.path()
        if not root or not os.path.isdir(root):
            QMessageBox.information(self, "Verify", "Choose a folder first.")
            return
        self.table.setRowCount(0)
        uncached = self.s.uncached
        recursive = self.recursive.isChecked()
        self.host.run_runner(
            f"Verify {root}",
            lambda emit, logs: VerifyRunner(lambda: find_manifest_folders(root, recursive),
                                            uncached=uncached, emit=emit, log_dir=logs),
            on_event=self._event,
        )

    def _event(self, kind: str, row: dict) -> None:
        if kind != "verify_row":
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        root = self.path.path()
        rel = os.path.relpath(row["folder"], root) if row["folder"] != root else os.path.basename(root) or root
        self.table.setItem(r, 0, ro_item(rel, row["folder"]))
        self.table.setItem(r, 1, ro_item(row["algo"]))
        vals = [row["ok"], len(row["mismatch"]), len(row["missing"]), len(row["extra"])]
        for i, v in enumerate(vals):
            it = NumItem(str(v), v)
            if i in (1, 2) and v:
                it.setForeground(QColor(theme.DANGER))
                it.setToolTip("\n".join((row["mismatch"] if i == 1 else row["missing"])[:40]))
            if i == 3 and v:
                it.setForeground(QColor(theme.MUTED))
                it.setToolTip("Files added after the manifest was written (e.g. project files):\n" + "\n".join(row["extra"][:40]))
            self.table.setItem(r, 2 + i, it)
        bad = row["mismatch"] or row["missing"]
        res = ro_item("✕  Problems" if bad else "✓  Bit-perfect")
        res.setForeground(QColor(theme.DANGER if bad else theme.SUCCESS))
        self.table.setItem(r, 6, res)
        self.table.scrollToBottom()

    # ---------------------------------------------------------------- create
    def _create(self) -> None:
        """Record checksums for dated folders that have none (non-destructive: only adds manifest files)."""
        root = self.path.path()
        if not root or not os.path.isdir(root):
            QMessageBox.information(self, "Create checksums", "Choose a folder first.")
            return
        recursive = self.recursive.isChecked()

        def targets() -> list[str]:
            if recursive:
                found = [w.path for w in scan_working(root) if w.day and not w.manifest_algo]
                if not found and not find_manifest(root) and not find_manifest_folders(root):
                    found = [root]
                return found
            return [] if find_manifest(root) else [root]

        algo = self.s.algo
        self.host.run_runner(
            f"Create checksums in {root}",
            lambda emit, logs: ChecksumRunner(targets, algo, emit=emit, log_dir=logs),
        )
