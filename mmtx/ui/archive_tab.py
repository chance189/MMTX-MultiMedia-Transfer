"""Archive tab: working drive folders -> archive drive, sorted into year (/month) folders."""

from __future__ import annotations

import os
import time
from datetime import date
from typing import Optional

from PySide6.QtCore import QDate, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics
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
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ..planner import ARCHIVE_LAYOUTS, archive_dest, is_inside, plan_archive
from ..scanner import WorkFolder, scan_working
from ..transfer import human_bytes
from . import theme
from .icons import draw_check, draw_cross, draw_glyph
from .widgets import Card, NumItem, PathPicker, PillDelegate, Worker, button, label, page_header, ro_item

C_CHECK, C_THUMB, C_NAME, C_DATE, C_FILES, C_SIZE, C_SUMS, C_DEST, C_STATUS = range(9)
KEY_ROLE = Qt.UserRole + 7
LIVE_ROLE = Qt.UserRole + 8  # (visual state, text, fraction, done_at) while an archive job runs


def _visual(d: dict) -> str:
    """Live folder state -> wait | busy | almost | done | bad | kept | stopped (same looks as Easy mode)."""
    from .easy_clips import ALMOST

    st = d["state"]
    if st == "copying":
        return "almost" if d["total"] and d["done"] / d["total"] >= ALMOST else "busy"
    return {"waiting": "wait", "verified": "done", "moved": "done", "errors": "bad", "warnings": "kept",
            "cancelled": "stopped"}.get(st, "wait")


class LiveStatusDelegate(PillDelegate):
    """Status column while a job runs: colored pill with a rotating hourglass (copying), a check (done),
    a cross (problem). Falls back to the plain pill otherwise."""

    def paint(self, p, opt, index):  # noqa: D401
        live = index.data(LIVE_ROLE)
        if not live:
            return super().paint(p, opt, index)
        from . import easy_clips as ec

        vis, text, _frac, done_at = live
        col = {"busy": ec.P.busy, "almost": ec.P.almost, "done": ec.P.done_hi, "bad": ec.P.bad,
               "kept": ec.P.warn}.get(vis, ec.P.wait)
        level = {"busy": ec.pulse(), "almost": ec.flash()}.get(vis, 1.0)
        if vis == "done" and done_at and ec.done_flashing({"done_at": done_at}):
            level = ec.flash(0.6)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if opt.state & QStyle.State_Selected:
            p.fillRect(opt.rect, opt.palette.highlight())
        f = QFont(opt.font)
        f.setPointSizeF(8.8)
        f.setBold(True)
        fm = QFontMetrics(f)
        h = fm.height() + 10
        icon = 18 if vis in ("busy", "almost", "done", "bad") else 0
        gap = 8 if icon else 0
        w = min(opt.rect.width() - 8, fm.horizontalAdvance(text) + 22 + icon + gap)
        rect = QRectF(opt.rect.x() + 4, opt.rect.center().y() - h / 2 + 1, w, h)
        bg = QColor(col)
        bg.setAlpha(int(60 + 150 * level) if vis in ("busy", "almost", "done") else 60)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, h / 2, h / 2)
        ic = QPointF(rect.x() + 8 + icon / 2, rect.center().y())
        if vis in ("busy", "almost"):
            ec.draw_hourglass(p, ic, icon, "#ffffff", ec.hourglass_angle(), ec.sand_level())
        elif vis == "done":
            draw_check(p, ic, icon / 2, "#ffffff", col)
        elif vis == "bad":
            draw_cross(p, ic, icon / 2, "#ffffff")
        p.setPen(QColor("#ffffff") if vis in ("busy", "almost", "done", "bad") else QColor(col))
        p.setFont(f)
        p.drawText(rect.adjusted(10 + icon + gap, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(text, Qt.ElideRight, int(w) - 18 - icon - gap))
        p.restore()


class PosterDelegate(QStyledItemDelegate):
    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self._grey: dict = {}

    def paint(self, p, opt, index):  # noqa: D401
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(opt.rect).adjusted(4, 5, -4, -5)
        h = r.height()
        w = min(r.width(), h * 16 / 9)
        r = QRectF(r.x(), r.y(), w, h)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.setClipPath(path)
        key = index.data(KEY_ROLE)
        live = index.data(LIVE_ROLE)
        vis = live[0] if live else ""
        from . import easy_clips as ec

        pm = self.loader.pixmap(key) if key else None
        if pm is not None and not pm.isNull():
            if vis in ("wait", "stopped"):
                pm = ec.grey_pixmap(pm, self._grey, key)
            pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            sc = max(r.width() / pw, r.height() / ph)
            sw, sh = r.width() / sc, r.height() / sc
            p.drawPixmap(r, pm, QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh))
        else:
            p.fillRect(r, QColor("#1a2030"))
            g = h * 0.5
            draw_glyph(p, "folder", QRectF(r.center().x() - g / 2, r.center().y() - g / 2, g, g), "#3d4659", 1.4)
        if vis in ("wait", "stopped", "busy", "almost", "bad"):
            p.fillRect(r, QColor(8, 10, 14, 120))
        elif vis == "done":
            t = QColor(ec.P.done_hi)
            t.setAlpha(70)
            p.fillRect(r, t)
        p.setClipping(False)
        c = r.center()
        if vis in ("busy", "almost"):
            col = ec.P.busy if vis == "busy" else ec.P.almost
            ec.glow_border(p, r, 6, col, ec.pulse() if vis == "busy" else ec.flash(), width=2.2)
            ec.draw_hourglass(p, c, h * 0.62, col, ec.hourglass_angle(), ec.sand_level())
        elif vis == "done":
            flashing = ec.done_flashing({"done_at": live[3]})
            ec.glow_border(p, r, 6, ec.P.done_hi, ec.flash(0.6) if flashing else 0.3, width=2.2)
            draw_check(p, c, h * 0.3, ec.P.done_hi, ec.P.on_done)
        elif vis == "bad":
            draw_cross(p, c, h * 0.28, ec.P.bad)
        else:
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 6, 6)
        p.restore()


class ArchiveTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.host = host
        self.s = host.settings
        self.folders: list[WorkFolder] = []
        self.worker: Optional[Worker] = None
        self._updating = False
        self.loader = host.loader
        self.key_rows: dict[str, list[int]] = {}
        self.loader.ready.connect(self._thumb_ready)
        # live state of the running archive job, keyed by normalized source folder path
        self._live: dict[str, dict] = {}
        self._src_to_folder: dict[str, str] = {}
        self._name_to_folder: dict[str, str] = {}
        self._job_move = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        hrow, _t, self.h_sub = page_header(
            "Archive", "Move finished day folders from the working drive to the archive drive, verified file by file.")
        root.addLayout(hrow)

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
        self.older = QComboBox()
        for d in (3, 7, 14, 30, 60, 90, 180, 365):
            self.older.addItem(f"{d} days", d)
        idx = self.older.findData(self.s.older_than_days)
        if idx < 0:
            self.older.addItem(f"{self.s.older_than_days} days", self.s.older_than_days)
            idx = self.older.count() - 1
        self.older.setCurrentIndex(idx)
        self.older.currentIndexChanged.connect(lambda _i: self._older_changed(self.older.currentData()))
        f.addWidget(self.older)
        f.addStretch(1)
        f.addWidget(button("Rescan", slot=self.rescan))
        lst.body.addLayout(f)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["", "", "Folder", "Date", "Files", "Size", "Checksums", "Archive location", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(60)
        self.table.setItemDelegateForColumn(C_THUMB, PosterDelegate(self.loader, self.table))
        self.table.setItemDelegateForColumn(C_SUMS, PillDelegate(self.table))
        self.table.setItemDelegateForColumn(C_STATUS, LiveStatusDelegate(self.table))
        self._anim = QTimer(self)  # repaints the hourglasses / flashes only while a job is moving
        self._anim.setInterval(40)
        self._anim.timeout.connect(self._animate)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(C_CHECK, QHeaderView.Fixed)
        hh.setSectionResizeMode(C_THUMB, QHeaderView.Fixed)
        hh.setSectionResizeMode(C_DEST, QHeaderView.Stretch)
        for c, w in ((C_CHECK, 34), (C_THUMB, 100), (C_NAME, 210), (C_DATE, 96), (C_FILES, 52), (C_SIZE, 80),
                     (C_SUMS, 118), (C_STATUS, 160)):
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
        self.go = button("Archive", "Primary", slot=self._start, icon_name="archive", icon_color="#ffffff")
        self.go.setMinimumWidth(280)
        bottom.addWidget(self.go)
        root.addLayout(bottom)

        self.host.busy_changed.connect(lambda _b: self._update_go())
        host.job_started.connect(self._job_started)
        host.job_event.connect(self._job_event)
        host.job_finished.connect(self._job_finished)
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

    def _thumb_ready(self, key: str) -> None:
        for r in self.key_rows.get(key, ()):
            if r < self.table.rowCount():
                self.table.viewport().update(self.table.visualRect(self.table.model().index(r, C_THUMB)))

    def _scan_done(self, folders: list[WorkFolder]) -> None:
        self.folders = folders
        self.key_rows = {}
        self._updating = True
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(folders))
        for r, wf in enumerate(folders):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, r)
            self.table.setItem(r, C_CHECK, chk)
            th = ro_item("")
            if wf.poster:
                key = self.loader.key_for(wf.poster[0][0])
                th.setData(KEY_ROLE, key)
                self.key_rows.setdefault(key, []).append(r)
                self.loader.request(key, wf.poster)
            self.table.setItem(r, C_THUMB, th)
            self.table.setItem(r, C_NAME, ro_item(wf.name, wf.path))
            d = ro_item(f"{wf.day:%Y-%m-%d}" if wf.day else "undated")
            if not wf.day:
                d.setForeground(QColor(theme.MUTED))
            self.table.setItem(r, C_DATE, d)
            self.table.setItem(r, C_FILES, NumItem(str(wf.files), wf.files))
            self.table.setItem(r, C_SIZE, NumItem(human_bytes(wf.size), wf.size))
            if wf.manifest_algo:
                s = ro_item(f"✓  {wf.manifest_algo}", "Checksums recorded at offload. Every file is checked against them.")
                s.setForeground(QColor(theme.SUCCESS))
            else:
                s = ro_item("hashed during copy", "No manifest yet. Files are hashed during the copy and a manifest is written to the archive.")
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
        self._apply_live()
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
        cutoff = date.fromtimestamp(time.time() - int(self.older.currentData()) * 86400)
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
        self.host.update_drives()
        self.rescan()

    def _dst_changed(self, p: str) -> None:
        self.s.archive_dest = p
        self.s.save()
        self.host.update_drives()
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

    def sync_from_settings(self) -> None:
        """Easy mode edits the same settings; pick up its choices when switching back."""
        src = self.s.archive_source or self.s.offload_dest
        if self.src.path() != src:
            self.src.set_path(src, emit=False)
            self.folders = []
            self.table.setRowCount(0)
        if self.dst.path() != self.s.archive_dest:
            self.dst.set_path(self.s.archive_dest, emit=False)
            if self.folders:
                self._refresh_dest()
        i = self.older.findData(self.s.older_than_days)
        if i >= 0 and i != self.older.currentIndex():
            self.older.blockSignals(True)
            self.older.setCurrentIndex(i)
            self.older.blockSignals(False)
        if self.isVisible():
            self.rescan()
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
        self.host.resolve_workers(job, self.src.path(), arch)
        self.host.run_transfer(job)

    # ================================================================ live state (any archive job, either mode)
    @staticmethod
    def _norm(p: str) -> str:
        return os.path.normcase(os.path.abspath(p))

    def _job_started(self, job) -> None:
        if job.kind != "archive":
            return
        self._live, self._src_to_folder, self._name_to_folder = {}, {}, {}
        self._job_move = any(g.delete_source for g in job.groups)
        for g in job.groups:
            key = self._norm(g.source_dir or g.source_root)
            self._live[key] = {"done": 0, "total": len(g.tasks), "state": "waiting", "phase": ""}
            self._name_to_folder[g.name] = key
            for t in g.tasks:
                self._src_to_folder[t.src] = key
        self._apply_live()

    def _job_event(self, kind: str, p: dict) -> None:
        if not self._live:
            return
        if kind == "progress":
            for slot in p.get("active", ()):
                key = self._src_to_folder.get(slot.get("src", ""))
                if key is None and str(slot.get("phase", "")).startswith("Moving"):
                    key = self._name_to_folder.get(slot.get("name", ""))
                if key and self._live[key]["state"] in ("waiting", "copying"):
                    self._live[key]["state"] = "copying"
                    self._live[key]["phase"] = slot.get("phase", "")
        elif kind == "file":
            key = self._src_to_folder.get(p.get("src", ""))
            if key:
                self._live[key]["done"] += 1
        elif kind == "group":
            key = self._name_to_folder.get(p.get("name", ""))
            if key:
                state = p.get("state", "")
                self._live[key]["state"] = "copying" if state == "start" else state
                if state in ("verified", "moved"):
                    self._live[key]["done_at"] = time.monotonic()
        else:
            return
        self._apply_live()

    def _live_text(self, d: dict) -> tuple[str, str]:
        st = d["state"]
        if st == "waiting":
            return "Waiting", theme.MUTED
        if st == "copying":
            ph = d.get("phase", "")
            verb = "Checking" if ph in ("Verifying", "Checking existing") else ("Moving" if ph.startswith("Moving") else "Copying")
            return f"{verb}  {d['done']}/{d['total']}", theme.ACCENT
        return {
            "verified": ("✓  Moved" if self._job_move else "✓  Archived", theme.SUCCESS),
            "moved": ("✓  Moved", theme.SUCCESS),
            "warnings": ("!  Kept, needs a look", theme.WARN),
            "errors": ("✕  Problem", theme.DANGER),
            "cancelled": ("Stopped", theme.MUTED),
        }.get(st, (st, theme.MUTED))

    def _apply_live(self) -> None:
        if not self._live:
            return
        self._updating = True
        for r, wf in enumerate(self.folders):
            d = self._live.get(self._norm(wf.path))
            st = self.table.item(r, C_STATUS)
            name = self.table.item(r, C_NAME)
            if d is None or st is None or name is None:
                continue
            text, color = self._live_text(d)
            st.setText(text)
            st.setForeground(QColor(color))
            vis = _visual(d)
            clean = text.lstrip("✓!✕ ").strip()
            if vis == "almost":
                clean = f"Almost done  {d['done']}/{d['total']}"
            live = (vis, clean, d["done"] / d["total"] if d["total"] else 0.0, d.get("done_at"))
            st.setData(LIVE_ROLE, live)
            th = self.table.item(r, C_THUMB)
            if th is not None:
                th.setData(LIVE_ROLE, live)
            f = name.font()
            f.setBold(d["state"] == "copying")
            name.setFont(f)
            name.setForeground(QColor(theme.ACCENT if d["state"] == "copying" else theme.TEXT))
        self._updating = False
        if not self._anim.isActive():
            self._anim.start()

    def _animate(self) -> None:
        from .easy_clips import done_flashing

        moving = any(_visual(d) in ("busy", "almost") or (_visual(d) == "done" and done_flashing(d))
                     for d in self._live.values())
        if moving:
            self.table.viewport().update()
        else:
            self._anim.stop()

    def _job_finished(self, res) -> None:
        if res.kind != "archive":
            return
        # final marks stay on any folder that is still here (copy mode, problems); moved ones leave the list
        if self.src.path() and os.path.isdir(self.src.path()):
            self.rescan()
