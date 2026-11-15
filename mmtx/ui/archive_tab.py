"""Archive tab: working drive folders -> archive drive, sorted into year (/month) folders."""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Optional

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..planner import ARCHIVE_LAYOUTS, archive_dest, is_inside, plan_archive
from ..scanner import WorkFolder, scan_working
from ..transfer import human_bytes
from . import theme
from .widgets import Card, NumItem, PathPicker, Worker, button, label, ro_item

C_CHECK, C_NAME, C_DATE, C_FILES, C_SIZE, C_SUMS, C_DEST, C_STATUS = range(8)


class ArchiveTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.host = host
        self.s = host.settings
        self.folders: list[WorkFolder] = []
        self.worker: Optional[Worker] = None
        self._updating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)
        a = Card("1 · Working drive (source)")
        self.src = PathPicker("Folder holding your YYYY_MM_DD_<Title> folders", "Choose working folder")
        self.src.set_path(self.s.archive_source or self.s.offload_dest, emit=False)
        self.src.changed.connect(self._src_changed)
        a.body.addWidget(self.src)
        a.body.addWidget(label("Dated folders are found at the top level and inside YYYY or YYYY_MM folders.", "Hint", wrap=True))
        top.addWidget(a, 1)
        b = Card("2 · Archive drive (destination)")
        self.dst = PathPicker("e.g. F:\\Archive on the SATA drive", "Choose archive folder")
        self.dst.set_path(self.s.archive_dest, emit=False)
        self.dst.changed.connect(self._dst_changed)
        b.body.addWidget(self.dst)
        lrow = QHBoxLayout()
        lrow.addWidget(label("Sort into"))
        self.layout_combo = QComboBox()
        for k, v in ARCHIVE_LAYOUTS.items():
            self.layout_combo.addItem(v, k)
        self.layout_combo.setCurrentIndex(max(0, self.layout_combo.findData(self.s.archive_layout)))
        self.layout_combo.currentIndexChanged.connect(self._layout_changed)
        lrow.addWidget(self.layout_combo, 1)
        b.body.addLayout(lrow)
        top.addWidget(b, 1)
        root.addLayout(top)

        lst = Card("3 · Folders")
        f = QHBoxLayout()
        f.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by name…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.search.setMaximumWidth(220)
        f.addWidget(self.search)
        self.range_on = QCheckBox("Dates")
        self.range_on.toggled.connect(self._apply_filter)
        f.addWidget(self.range_on)
        today = QDate.currentDate()
        self.d_from = QDateEdit(today.addMonths(-3))
        self.d_to = QDateEdit(today)
        for d in (self.d_from, self.d_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
            d.dateChanged.connect(lambda _d: (self.range_on.setChecked(True), self._apply_filter()))
        f.addWidget(self.d_from)
        f.addWidget(label("to", "Muted"))
        f.addWidget(self.d_to)
        f.addSpacing(12)
        f.addWidget(button("All", "Chip", slot=lambda: self._select("all")))
        f.addWidget(button("None", "Chip", slot=lambda: self._select("none")))
        self.older_btn = button("Older than", "Chip", slot=lambda: self._select("older"))
        f.addWidget(self.older_btn)
        self.older = QSpinBox()
        self.older.setRange(0, 3650)
        self.older.setValue(self.s.older_than_days)
        self.older.setSuffix(" days")
        self.older.setButtonSymbols(QSpinBox.NoButtons)
        self.older.setFixedWidth(90)
        self.older.valueChanged.connect(self._older_changed)
        f.addWidget(self.older)
        f.addStretch(1)
        f.addWidget(button("Rescan", slot=self.rescan))
        lst.body.addLayout(f)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["", "Folder", "Date", "Files", "Size", "Checksums", "Archive location", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(C_CHECK, QHeaderView.Fixed)
        hh.setSectionResizeMode(C_DEST, QHeaderView.Stretch)
        for c, w in ((C_CHECK, 34), (C_NAME, 250), (C_DATE, 110), (C_FILES, 60), (C_SIZE, 90), (C_SUMS, 150), (C_STATUS, 170)):
            self.table.setColumnWidth(c, w)
        self.table.itemChanged.connect(self._item_changed)
        lst.body.addWidget(self.table, 1)
        self.empty = label("", "Muted")
        lst.body.addWidget(self.empty)
        root.addWidget(lst, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        self.mode_move = QRadioButton("Move  (delete from working drive after verify)")
        self.mode_copy = QRadioButton("Copy  (keep working copy)")
        grp = QButtonGroup(self)
        grp.addButton(self.mode_move)
        grp.addButton(self.mode_copy)
        (self.mode_move if self.s.archive_move else self.mode_copy).setChecked(True)
        self.mode_move.toggled.connect(self._mode_changed)
        bottom.addWidget(self.mode_move)
        bottom.addWidget(self.mode_copy)
        bottom.addStretch(1)
        self.go = button("Archive", "Primary", slot=self._start)
        self.go.setMinimumWidth(280)
        bottom.addWidget(self.go)
        root.addLayout(bottom)

        self.host.busy_changed.connect(lambda _b: self._update_go())
        self._update_go()

    def shutdown(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.wait(5000)

    # ================================================================ scanning
    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        if not self.folders and self.src.path() and os.path.isdir(self.src.path()):
            self.rescan()

    def rescan(self) -> None:
        root = self.src.path()
        if not root or not os.path.isdir(root):
            self.folders = []
            self.table.setRowCount(0)
            self._update_go()
            self.empty.setText("Choose the working drive folder to list its dated folders.")
            return
        if self.worker and self.worker.isRunning():
            return
        self.empty.setText(f"Scanning {root} …")
        self.folders = []
        self.table.setRowCount(0)
        self._update_go()
        arch = self.dst.path() or None

        w = Worker(lambda emit: scan_working(root, exclude=arch), self)
        w.bridge.finished.connect(self._scan_done)
        w.bridge.failed.connect(lambda tb: self.empty.setText("Scan failed: " + tb.splitlines()[-1]))
        self.worker = w
        w.start()

    def _scan_done(self, folders: list[WorkFolder]) -> None:
        self.folders = folders
        self._updating = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(folders))
        for r, wf in enumerate(folders):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, r)
            self.table.setItem(r, C_CHECK, chk)
            self.table.setItem(r, C_NAME, ro_item(wf.name, wf.path))
            d = ro_item(f"{wf.day:%Y-%m-%d}" if wf.day else "undated")
            if not wf.day:
                d.setForeground(QColor(theme.MUTED))
            self.table.setItem(r, C_DATE, d)
            self.table.setItem(r, C_FILES, NumItem(str(wf.files), wf.files))
            self.table.setItem(r, C_SIZE, NumItem(human_bytes(wf.size), wf.size))
            if wf.manifest_algo:
                s = ro_item(f"✓ {wf.manifest_algo}", "Checksums recorded at offload. Every file is checked against them.")
                s.setForeground(QColor(theme.SUCCESS))
            else:
                s = ro_item("none, hashed now", "No manifest yet. Files are hashed during the copy and a manifest is written to the archive.")
                s.setForeground(QColor(theme.MUTED))
            self.table.setItem(r, C_SUMS, s)
            self.table.setItem(r, C_DEST, ro_item(""))
            self.table.setItem(r, C_STATUS, ro_item(""))
        self._updating = False
        self.empty.setText("" if folders else "No folders found here.")
        self._refresh_dest()
        self._apply_filter()

    def _refresh_dest(self) -> None:
        arch = self.dst.path()
        layout = self.layout_combo.currentData()
        self._updating = True
        for r, wf in enumerate(self.folders):
            dest_item = self.table.item(r, C_DEST)
            st = self.table.item(r, C_STATUS)
            if not arch:
                dest_item.setText("")
                st.setText("Choose an archive drive")
                st.setForeground(QColor(theme.MUTED))
                continue
            dd = archive_dest(arch, wf, layout)
            dest_item.setText(os.path.relpath(dd, arch).replace("\\", "/"))
            dest_item.setToolTip(dd)
            if is_inside(arch, wf.path):
                st.setText("Archive is inside this folder")
                st.setForeground(QColor(theme.DANGER))
            elif os.path.isdir(dd):
                st.setText("Exists, will merge")
                st.setForeground(QColor(theme.WARN))
            else:
                st.setText("New")
                st.setForeground(QColor(theme.INFO))
        self._updating = False
        self._update_go()

    # ================================================================ filter/select
    def _visible(self, r: int) -> bool:
        wf = self.folders[r]
        q = self.search.text().strip().lower()
        if q and q not in wf.name.lower():
            return False
        if self.range_on.isChecked():
            if not wf.day:
                return False
            lo = self.d_from.date().toPython()
            hi = self.d_to.date().toPython()
            if not (lo <= wf.day <= hi):
                return False
        return True

    def _apply_filter(self) -> None:
        for r in range(len(self.folders)):
            self.table.setRowHidden(r, not self._visible(r))
        self._update_go()

    def _select(self, mode: str) -> None:
        cutoff = date.fromtimestamp(time.time() - self.older.value() * 86400)
        self._updating = True
        for r, wf in enumerate(self.folders):
            if self.table.isRowHidden(r):
                continue
            if mode == "all":
                on = True
            elif mode == "none":
                on = False
            else:
                on = wf.day is not None and wf.day < cutoff
            self.table.item(r, C_CHECK).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._updating = False
        self._update_go()

    def selected(self) -> list[WorkFolder]:
        out = []
        for r, wf in enumerate(self.folders):
            item = self.table.item(r, C_CHECK)
            if item is not None and not self.table.isRowHidden(r) and item.checkState() == Qt.Checked:
                out.append(wf)
        return out

    def _item_changed(self, item) -> None:
        if not self._updating and item.column() == C_CHECK:
            self._update_go()

    def _update_go(self) -> None:
        sel = self.selected()
        n = len(sel)
        size = sum(w.size for w in sel)
        verb = "Move" if self.mode_move.isChecked() else "Copy"
        self.go.setText(f"{verb} {n} folder{'s' if n != 1 else ''} to archive   ·   {human_bytes(size)}" if n else f"{verb} to archive")
        self.go.setEnabled(bool(n) and bool(self.dst.path()) and not self.host.busy)

    # ================================================================ settings plumbing
    def _src_changed(self, p: str) -> None:
        self.s.archive_source = p
        self.s.save()
        self.rescan()

    def _dst_changed(self, p: str) -> None:
        self.s.archive_dest = p
        self.s.save()
        self._refresh_dest()

    def _layout_changed(self) -> None:
        self.s.archive_layout = self.layout_combo.currentData()
        self.s.save()
        self._refresh_dest()

    def _mode_changed(self) -> None:
        self.s.archive_move = self.mode_move.isChecked()
        self.s.save()
        self._update_go()

    def _older_changed(self, v: int) -> None:
        self.s.older_than_days = v
        self.s.save()

    def settings_changed(self) -> None:
        self._update_go()

    # ================================================================ go
    def _start(self) -> None:
        sel = self.selected()
        arch = self.dst.path()
        if not sel or not arch:
            return
        bad = [w.name for w in sel if is_inside(arch, w.path)]
        if bad:
            QMessageBox.warning(self, "Archive inside a selected folder", "The archive folder sits inside:\n" + "\n".join(bad))
            return
        try:
            os.makedirs(arch, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Cannot use archive folder", f"{arch}\n\n{e}")
            return
        move = self.mode_move.isChecked()
        job = plan_archive(sel, arch, self.layout_combo.currentData(), move, self.s.algo,
                           verify_readback=self.s.verify_readback, uncached=self.s.uncached)
        if move:
            ans = QMessageBox.question(
                self, "Move to archive?",
                f"{len(sel)} folder(s) will be copied to the archive, every file verified by checksum, "
                "and only then removed from the working drive.\nFolders with any problem stay put.\n\nContinue?",
            )
            if ans != QMessageBox.Yes:
                return
        if not self.host.preflight(job, arch):
            return
        self.host.run_transfer(job, after=lambda _r: self.rescan())
