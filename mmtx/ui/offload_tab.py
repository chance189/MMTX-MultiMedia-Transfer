"""Offload tab: camera card -> working drive, grouped into YYYY_MM_DD_<Title> folders."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..planner import (
    OffloadOptions,
    day_folder_name,
    day_presence,
    existing_day_folders,
    is_inside,
    plan_offload,
    sanitize,
    title_from_existing,
)
from ..scanner import CardScan, scan_card
from ..transfer import human_bytes
from . import theme
from .volumes import list_volumes
from .widgets import Card, NumItem, PathPicker, Worker, button, label, ro_item

C_CHECK, C_DATE, C_CAMS, C_FILES, C_SIZE, C_TITLE, C_FOLDER, C_STATUS = range(8)


class OffloadTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.host = host
        self.s = host.settings
        self.scan: Optional[CardScan] = None
        self.scan_worker: Optional[Worker] = None
        self.edited_titles: set[int] = set()
        self.device_edits: dict[str, QLineEdit] = {}
        self._updating = False
        self._known_vols: tuple = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)

        # ---------------------------------------------------- source + destination
        top = QHBoxLayout()
        top.setSpacing(12)

        src = Card("1 · Camera card")
        row = QHBoxLayout()
        self.vol_combo = QComboBox()
        self.vol_combo.setMinimumWidth(300)
        self.vol_combo.activated.connect(self._combo_picked)
        row.addWidget(self.vol_combo, 1)
        row.addWidget(button("Browse…", tip="Pick a card or folder manually", slot=self._browse_source))
        row.addWidget(button("Rescan", tip="Scan the card again", slot=self.rescan))
        src.body.addLayout(row)
        self.card_info = label("No card selected. Insert an SD card (or plug in a camera in USB storage mode).", "Muted", wrap=True)
        src.body.addWidget(self.card_info)
        self.dev_grid = QGridLayout()
        self.dev_grid.setHorizontalSpacing(10)
        self.dev_grid.setVerticalSpacing(4)
        src.body.addLayout(self.dev_grid)
        src.body.addStretch(1)
        top.addWidget(src, 1)

        dst = Card("2 · Working drive")
        self.dest = PathPicker("Where daily footage lives, e.g. D:\\Footage", "Choose working folder")
        self.dest.set_path(self.s.offload_dest, emit=False)
        self.dest.changed.connect(self._dest_changed)
        dst.body.addWidget(self.dest)
        trow = QHBoxLayout()
        trow.addWidget(label("Default title"))
        self.default_title = QLineEdit(self.s.default_title)
        self.default_title.setPlaceholderText("e.g. DailyRun")
        self.default_title.textChanged.connect(self._default_title_changed)
        trow.addWidget(self.default_title, 1)
        dst.body.addLayout(trow)
        hint = label("Each shooting day becomes  YYYY_MM_DD_<Title>. Double-click a title in the table to change it for one day.", "Hint", wrap=True)
        hint.setTextFormat(Qt.PlainText)
        dst.body.addWidget(hint)
        dst.body.addStretch(1)
        top.addWidget(dst, 1)
        root.addLayout(top)

        # ---------------------------------------------------- day table
        days = Card("3 · Shooting days on the card")
        chips = QHBoxLayout()
        chips.addWidget(button("Select all", "Chip", slot=lambda: self._select("all")))
        chips.addWidget(button("Select none", "Chip", slot=lambda: self._select("none")))
        chips.addWidget(button("Not yet offloaded", "Chip", slot=lambda: self._select("new")))
        chips.addStretch(1)
        self.sel_info = label("", "Muted")
        chips.addWidget(self.sel_info)
        days.body.addLayout(chips)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["", "Date", "Cameras", "Files", "Size", "Title  ✎", "Destination folder", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(C_CHECK, QHeaderView.Fixed)
        hh.setSectionResizeMode(C_FOLDER, QHeaderView.Stretch)
        self.table.setColumnWidth(C_CHECK, 34)
        self.table.setColumnWidth(C_DATE, 130)
        self.table.setColumnWidth(C_CAMS, 190)
        self.table.setColumnWidth(C_FILES, 70)
        self.table.setColumnWidth(C_SIZE, 90)
        self.table.setColumnWidth(C_TITLE, 170)
        self.table.setColumnWidth(C_STATUS, 230)
        self.table.itemChanged.connect(self._item_changed)
        self.table.setEditTriggers(QTableWidget.DoubleClicked | QTableWidget.SelectedClicked | QTableWidget.EditKeyPressed)
        days.body.addWidget(self.table, 1)
        root.addWidget(days, 1)

        # ---------------------------------------------------- options + go
        bottom = QHBoxLayout()
        bottom.setSpacing(18)
        self.opt_subfolders = QCheckBox("Camera subfolders")
        self.opt_subfolders.setToolTip("2026_11_14_DailyRun/GoPro_HERO12_Black/…  so multiple cameras on one day stay apart")
        self.opt_subfolders.setChecked(self.s.camera_subfolders)
        self.opt_proxies = QCheckBox("Skip proxies && thumbnails")
        self.opt_proxies.setToolTip("Leave GoPro/Insta360 .LRV, DJI .LRF, .THM and Sony SUB/THMBNL files on the card")
        self.opt_proxies.setChecked(self.s.skip_proxies)
        self.opt_delete = QCheckBox("Delete from card after verify")
        self.opt_delete.setToolTip(
            "Only files whose checksum verified are deleted, and only if the whole day verified.\n"
            "Formatting the card in the camera is still the cleanest option (Sony cameras keep a clip database)."
        )
        self.opt_delete.setChecked(self.s.delete_from_card)
        for cb in (self.opt_subfolders, self.opt_proxies, self.opt_delete):
            cb.toggled.connect(self._options_changed)
            bottom.addWidget(cb)
        bottom.addStretch(1)
        self.go = button("Offload", "Primary", slot=self._start)
        self.go.setMinimumWidth(260)
        bottom.addWidget(self.go)
        root.addLayout(bottom)

        self.host.busy_changed.connect(lambda _b: self._update_go())
        self._vol_worker: Optional[Worker] = None
        self._first_poll = True
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_volumes)
        self.timer.start(3000)
        self.poll_volumes()
        self._update_go()

    def shutdown(self) -> None:
        self.timer.stop()
        for w in (self._vol_worker, self.scan_worker):
            if w is not None and w.isRunning():
                w.wait(5000)

    # ================================================================ volumes
    def poll_volumes(self) -> None:
        """List drives off the UI thread so a sleeping network drive can never freeze the window."""
        if self._vol_worker and self._vol_worker.isRunning():
            return
        w = Worker(lambda emit: list_volumes(), self)
        w.bridge.finished.connect(lambda vols: self.refresh_volumes(vols, initial=self._first_poll))
        self._vol_worker = w
        w.start()

    def refresh_volumes(self, vols, initial: bool = False) -> None:
        self._first_poll = False
        sig = tuple((v.root, v.camera) for v in vols)
        if sig == self._known_vols and not initial:
            return
        old_roots = {r for r, _ in self._known_vols}
        self._known_vols = sig
        current = self.current_source()
        self.vol_combo.blockSignals(True)
        self.vol_combo.clear()
        cams = [v for v in vols if v.camera]
        others = [v for v in vols if not v.camera]
        if not cams:
            self.vol_combo.addItem("No camera card detected. Insert one or Browse…", "")
        for v in cams:
            ro = "  ·  locked" if v.read_only else ""
            self.vol_combo.addItem(f"{v.camera}   ·   {v.root}   ·   {human_bytes(v.total)}{ro}", v.root)
        if others:
            self.vol_combo.insertSeparator(self.vol_combo.count())
            for v in others:
                nm = f"{v.name}  " if v.name else ""
                self.vol_combo.addItem(f"{nm}{v.root}   ·   {human_bytes(v.total)}", v.root)
        if current and self.vol_combo.findData(current) < 0:
            self.vol_combo.addItem(f"Folder   ·   {current}", current)
        idx = self.vol_combo.findData(current) if current else -1
        self.vol_combo.setCurrentIndex(max(idx, 0))
        self.vol_combo.blockSignals(False)

        # auto-pick a newly inserted camera card when idle
        new_cams = [v for v in cams if v.root not in old_roots]
        if new_cams and not self.host.busy and (initial or not current or current not in {v.root for v in cams}):
            self.set_source(new_cams[0].root)
        elif current and not os.path.isdir(current):
            self._clear_scan("Card removed.")

    def current_source(self) -> str:
        if self.scan:
            return self.scan.root
        return self.vol_combo.currentData() or ""

    def _combo_picked(self, _idx: int) -> None:
        root = self.vol_combo.currentData()
        if root:
            self.set_source(root)

    def _browse_source(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Choose card or folder", self.current_source() or os.path.expanduser("~"))
        if p:
            self.set_source(os.path.normpath(p))

    def set_source(self, root: str) -> None:
        if self.vol_combo.findData(root) < 0:
            self.vol_combo.addItem(f"Folder   ·   {root}", root)
        self.vol_combo.setCurrentIndex(self.vol_combo.findData(root))
        self._scan(root)

    def rescan(self) -> None:
        root = self.current_source()
        if root:
            self._scan(root)

    # ================================================================ scanning
    def _scan(self, root: str) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self.card_info.setText(f"Scanning {root} …")
        self.table.setRowCount(0)
        self.scan = None
        self._update_go()

        def fn(emit):
            return scan_card(root)

        w = Worker(fn, self)
        w.bridge.finished.connect(self._scan_done)
        w.bridge.failed.connect(lambda tb: self._clear_scan(f"Scan failed: {tb.splitlines()[-1]}"))
        self.scan_worker = w
        w.start()

    def _clear_scan(self, msg: str) -> None:
        self.scan = None
        self.table.setRowCount(0)
        self.card_info.setText(msg)
        self._rebuild_devices()
        self._update_go()

    def _scan_done(self, scan: CardScan) -> None:
        self.scan = scan
        self.edited_titles.clear()
        if not scan.files:
            self._clear_scan(f"No camera media found on {scan.root}.")
            self.scan = None
            return
        cams = ", ".join(scan.devices.values())
        n = len(scan.days)
        self.card_info.setText(
            f"<b>{cams}</b><br><span style='color:{theme.MUTED}'>{len(scan.files)} files  ·  "
            f"{human_bytes(scan.size)}  ·  {n} shooting day{'s' if n != 1 else ''}  ·  {scan.root}</span>"
        )
        self._rebuild_devices()
        self._populate()

    # ================================================================ device folders
    def _rebuild_devices(self) -> None:
        while self.dev_grid.count():
            it = self.dev_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.device_edits.clear()
        if not self.scan:
            return
        r = 0
        self.dev_grid.addWidget(label("Camera folder names", "Hint"), r, 0, 1, 2)
        for prof, detected in self.scan.devices.items():
            r += 1
            edit = QLineEdit(self.s.device_aliases.get(detected) or sanitize(detected, self.s.spaces_to_underscores))
            edit.setToolTip(f"Subfolder used for {detected}. Remembered for next time.")
            edit.editingFinished.connect(lambda d=detected, e=edit: self._alias_changed(d, e))
            self.dev_grid.addWidget(label(detected, "Muted"), r, 0)
            self.dev_grid.addWidget(edit, r, 1)
            self.device_edits[prof] = edit
        self._sync_device_visibility()

    def _sync_device_visibility(self) -> None:
        vis = self.opt_subfolders.isChecked()
        for i in range(self.dev_grid.count()):
            w = self.dev_grid.itemAt(i).widget()
            if w:
                w.setVisible(vis)

    def _alias_changed(self, detected: str, edit: QLineEdit) -> None:
        val = sanitize(edit.text(), self.s.spaces_to_underscores) or sanitize(detected, self.s.spaces_to_underscores)
        edit.setText(val)
        self.s.device_aliases[detected] = val
        self.s.save()
        self._refresh_all_rows()

    def device_folders(self) -> dict[str, str]:
        return {p: (sanitize(e.text(), self.s.spaces_to_underscores) or p) for p, e in self.device_edits.items()}

    # ================================================================ table
    def _opts(self) -> OffloadOptions:
        return OffloadOptions(
            algo=self.s.algo,
            camera_subfolders=self.opt_subfolders.isChecked(),
            skip_proxies=self.opt_proxies.isChecked(),
            delete_source=self.opt_delete.isChecked(),
            keep_structure=self.s.keep_structure,
            rename_gopro=self.s.rename_gopro,
            verify_readback=self.s.verify_readback,
            uncached=self.s.uncached,
        )

    def _populate(self) -> None:
        assert self.scan
        self._updating = True
        self.table.setRowCount(len(self.scan.days))
        existing = existing_day_folders(self.dest.path()) if self.dest.path() else {}
        for r, day in enumerate(self.scan.days):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setData(Qt.UserRole, r)
            self.table.setItem(r, C_CHECK, chk)
            span = f"{day.first_time:%H:%M}–{day.last_time:%H:%M}"
            self.table.setItem(r, C_DATE, ro_item(f"{day.day:%Y-%m-%d}  {day.day:%a}", f"Shot {span}"))
            cams = ", ".join(self.scan.devices[p] for p in day.profiles)
            self.table.setItem(r, C_CAMS, ro_item(cams, cams))
            media = sum(1 for f in day.files if f.role == "media")
            self.table.setItem(r, C_FILES, NumItem(str(day.count), day.count))
            self.table.item(r, C_FILES).setToolTip(f"{media} clips/photos + {day.count - media} proxies/sidecars")
            self.table.setItem(r, C_SIZE, NumItem(human_bytes(day.size), day.size))
            title = self.default_title.text()
            if day.day in existing:
                title = title_from_existing(existing[day.day][0])
            t_item = QTableWidgetItem(title)
            t_item.setToolTip("Double-click to edit the title for this day")
            self.table.setItem(r, C_TITLE, t_item)
            self.table.setItem(r, C_FOLDER, ro_item(""))
            self.table.setItem(r, C_STATUS, ro_item(""))
        self._updating = False
        self._refresh_all_rows(set_checks=True)

    def _folder_for_row(self, r: int) -> str:
        assert self.scan
        day = self.scan.days[r]
        return day_folder_name(day.day, self.table.item(r, C_TITLE).text(), self.s.spaces_to_underscores)

    def _refresh_all_rows(self, set_checks: bool = False) -> None:
        if not self.scan:
            return
        self._updating = True
        opts = self._opts()
        dev = self.device_folders()
        dest = self.dest.path()
        for r, day in enumerate(self.scan.days):
            folder = self._folder_for_row(r)
            self.table.item(r, C_FOLDER).setText(folder)
            self.table.item(r, C_FOLDER).setToolTip(os.path.join(dest, folder) if dest else folder)
            st = self.table.item(r, C_STATUS)
            if not dest:
                st.setText("Choose a working drive")
                st.setForeground(QColor(theme.MUTED))
                new = True
            else:
                dest_dir = os.path.join(dest, folder)
                present, total = day_presence(day, dest_dir, dev, opts)
                new = present < total
                if not os.path.isdir(dest_dir):
                    st.setText("New folder")
                    st.setForeground(QColor(theme.INFO))
                elif present >= total:
                    st.setText("✓ Already offloaded")
                    st.setForeground(QColor(theme.SUCCESS))
                elif present:
                    st.setText(f"Partly there ({present}/{total}), resumes")
                    st.setForeground(QColor(theme.WARN))
                else:
                    st.setText("Adds to existing folder")
                    st.setForeground(QColor(theme.INFO))
            if set_checks:
                self.table.item(r, C_CHECK).setCheckState(Qt.Checked if new else Qt.Unchecked)
        self._updating = False
        self._update_go()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        if item.column() == C_TITLE:
            self.edited_titles.add(item.row())
            self._refresh_all_rows()
        elif item.column() == C_CHECK:
            self._update_go()

    def _default_title_changed(self, text: str) -> None:
        self.s.default_title = text
        self.s.save()
        if not self.scan:
            return
        existing = existing_day_folders(self.dest.path()) if self.dest.path() else {}
        self._updating = True
        for r, day in enumerate(self.scan.days):
            if r not in self.edited_titles and day.day not in existing:
                self.table.item(r, C_TITLE).setText(text)
        self._updating = False
        self._refresh_all_rows()

    def _dest_changed(self, path: str) -> None:
        self.s.offload_dest = path
        self.s.save()
        if self.scan:
            self._populate()
        self._update_go()

    def _options_changed(self) -> None:
        self.s.camera_subfolders = self.opt_subfolders.isChecked()
        self.s.skip_proxies = self.opt_proxies.isChecked()
        self.s.delete_from_card = self.opt_delete.isChecked()
        self.s.save()
        self._sync_device_visibility()
        self._refresh_all_rows()

    def settings_changed(self) -> None:
        if self.scan:
            self._rebuild_devices()
            self._refresh_all_rows()

    def _select(self, mode: str) -> None:
        if not self.scan:
            return
        self._updating = True
        for r in range(self.table.rowCount()):
            st = self.table.item(r, C_STATUS).text()
            on = mode == "all" or (mode == "new" and not st.startswith("✓"))
            self.table.item(r, C_CHECK).setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._updating = False
        self._update_go()

    def selected_rows(self) -> list[int]:
        return [r for r in range(self.table.rowCount()) if self.table.item(r, C_CHECK).checkState() == Qt.Checked]

    def _update_go(self) -> None:
        rows = self.selected_rows() if self.scan else []
        opts = self._opts()
        size = 0
        if self.scan:
            for r in rows:
                day = self.scan.days[r]
                size += sum(f.size for f in day.files if not (opts.skip_proxies and f.role in ("proxy", "thumb")))
        n = len(rows)
        self.go.setText(f"Offload {n} day{'s' if n != 1 else ''}   ·   {human_bytes(size)}" if n else "Offload")
        self.sel_info.setText(f"{n} of {self.table.rowCount()} days selected" if self.scan else "")
        self.go.setEnabled(bool(n) and bool(self.dest.path()) and not self.host.busy)

    # ================================================================ go
    def _start(self) -> None:
        if not self.scan:
            return
        dest = self.dest.path()
        if is_inside(dest, self.scan.root):
            QMessageBox.warning(self, "Destination is on the card", "Pick a working drive that is not the card itself.")
            return
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Cannot use destination", f"{dest}\n\n{e}")
            return
        sel = [(self.scan.days[r], self._folder_for_row(r)) for r in self.selected_rows()]
        opts = self._opts()
        job = plan_offload(self.scan, sel, dest, self.device_folders(), opts)
        if not job.groups:
            return
        if opts.delete_source:
            ans = QMessageBox.question(
                self, "Delete from card after verify?",
                "Files will be deleted from the card after their checksums verify on the working drive.\n"
                "Days with any problem are left untouched.\n\nContinue?",
            )
            if ans != QMessageBox.Yes:
                return
        if not self.host.preflight(job, dest):
            return
        self.host.run_transfer(job, after=self._after_job)

    def _after_job(self, _result) -> None:
        if self.scan and os.path.isdir(self.scan.root):
            self._scan(self.scan.root)
