"""Offload page: camera card -> working drive, one card per shooting day with clip thumbnails."""

from __future__ import annotations

import os
from collections import Counter
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
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
from ..scanner import CardScan, DayGroup, scan_card
from ..transfer import human_bytes
from .clip_grid import ROLE, ClipGrid, clip_info
from .icons import draw_glyph
from .widgets import Card, CamBadge, DateTile, PathPicker, Worker, button, label, page_header, pill, set_pill

PHASE_STATUS = {"Copying": "copying", "Verifying": "verifying", "Checking existing": "checking"}
FINAL_STATUS = {"copied": "copied", "skipped": "skipped", "failed": "failed", "cancelled": "cancelled"}


class DaySection(QFrame):
    """One shooting day: checkbox, calendar tile, title, folder preview, status, clip grid."""

    changed = Signal()

    def __init__(self, day: DayGroup, devices: dict, loader, default_title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DayCard")
        self.day = day
        self.loader = loader
        self.edited = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 10)
        lay.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(14)
        self.check = QCheckBox()
        self.check.setToolTip("Include this day")
        self.check.toggled.connect(self._toggled)
        head.addWidget(self.check)
        head.addWidget(DateTile(day.day))
        mid = QVBoxLayout()
        mid.setSpacing(5)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.title = QLineEdit(default_title)
        self.title.setObjectName("TitleEdit")
        self.title.setPlaceholderText("Title")
        self.title.setFixedWidth(250)
        self.title.setToolTip("Folder title for this day")
        self.title.textEdited.connect(self._edited)
        self.title.textChanged.connect(lambda _t: self.changed.emit())
        row.addWidget(self.title)
        row.addWidget(label("→", "Arrow"))
        self.folder = label("", "Folder")
        self.folder.setTextFormat(Qt.PlainText)
        row.addWidget(self.folder, 1)
        mid.addLayout(row)
        media = [f for f in day.files if f.role == "media"] or list(day.files)
        cams = ", ".join(devices[p] for p in day.profiles)
        span = f"{day.first_time:%H:%M}–{day.last_time:%H:%M}"
        n_vid = sum(1 for f in media if f.kind in ("video", "360"))
        n_pic = len(media) - n_vid
        parts = []
        if n_vid:
            parts.append(f"{n_vid} clip{'s' if n_vid != 1 else ''}")
        if n_pic:
            parts.append(f"{n_pic} photo{'s' if n_pic != 1 else ''}")
        self.meta = label(f"{cams}   ·   {', '.join(parts)}   ·   {human_bytes(day.size)}   ·   {span}", "Muted")
        mid.addWidget(self.meta)
        head.addLayout(mid, 1)
        self.status = pill("", "muted")
        head.addWidget(self.status, 0, Qt.AlignVCenter)
        lay.addLayout(head)

        self.grid = ClipGrid(loader)
        lay.addWidget(self.grid)
        self.more = button("", "Chip", slot=self._toggle_more)
        lay.addWidget(self.more, 0, Qt.AlignLeft)
        self.grid.layout_changed.connect(self._update_more)

        extras = Counter((f.profile, f.key) for f in day.files if f.role != "media")
        self.items = {}
        for f in media:
            key = loader.key_for(f.path)
            info = clip_info(f, key)
            info["extra"] = extras.get((f.profile, f.key), 0)
            self.items[f.path] = self.grid.add_clip(info)
            loader.request(key, f.thumbs, f.path)
        self._update_more()

    # ---------------------------------------------------------------- state
    def _edited(self, _t: str) -> None:
        self.edited = True

    def _toggled(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self)
        self.style().polish(self)
        self.changed.emit()

    def _toggle_more(self) -> None:
        self.grid.set_expanded(not self.grid.expanded)

    def _update_more(self) -> None:
        if self.grid.expanded:
            self.more.setText("Show less")
            self.more.setVisible(self.grid.total_rows() > self.grid.collapsed_rows)
        else:
            hidden = self.grid.overflow()
            self.more.setText(f"Show all {self.grid.count()}  ({hidden} more)")
            self.more.setVisible(hidden > 0)

    def set_present(self, present: set) -> None:
        for path, it in self.items.items():
            info = it.data(ROLE)
            info["present"] = path in present
        self.grid.viewport().update()

    def set_all_status(self, status: str) -> None:
        for it in self.items.values():
            info = it.data(ROLE)
            info["status"] = status
            info["progress"] = 0.0
        self.grid.viewport().update()


class OffloadPage(QWidget):
    source_changed = Signal(str)
    dest_changed = Signal(str)

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.host = host
        self.s = host.settings
        self.loader = host.loader
        self.scan: Optional[CardScan] = None
        self.scan_worker: Optional[Worker] = None
        self.sections: list[DaySection] = []
        self.src_index: dict = {}
        self.key_index: dict = {}
        self.device_edits: dict[str, QLineEdit] = {}
        self._seen_roots: set = set()
        self._first_volumes = True
        self._job_deletes = False
        self.loader.ready.connect(self._thumb_ready)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        hrow, self.h_title, self.h_sub = page_header("Offload", "Insert a camera card, or Browse to a folder.")
        self.badge = CamBadge("", 46)
        self.badge.setVisible(False)
        hrow.insertWidget(0, self.badge)
        hrow.addWidget(button("Rescan", slot=self.rescan, icon_name="refresh"))
        hrow.addWidget(button("Browse…", tip="Offload from any folder", slot=self._browse_source, icon_name="folder"))
        root.addLayout(hrow)

        # ---------------------------------------------------- destination strip
        strip = Card()
        g = QGridLayout()
        g.setHorizontalSpacing(18)
        g.setVerticalSpacing(4)
        g.addWidget(label("WORKING DRIVE", "CardTitle"), 0, 0)
        g.addWidget(label("DEFAULT TITLE", "CardTitle"), 0, 1)
        self.dev_caption = label("CAMERA FOLDER", "CardTitle")
        g.addWidget(self.dev_caption, 0, 2)
        self.dest = PathPicker("Where daily footage lives, e.g. D:\\Footage", "Choose working folder")
        self.dest.set_path(self.s.offload_dest, emit=False)
        self.dest.changed.connect(self._dest_changed)
        g.addWidget(self.dest, 1, 0, Qt.AlignTop)
        tcol = QVBoxLayout()
        tcol.setSpacing(4)
        self.default_title = QLineEdit(self.s.default_title)
        self.default_title.setPlaceholderText("e.g. DailyRun")
        self.default_title.textChanged.connect(self._default_title_changed)
        tcol.addWidget(self.default_title)
        hint = label("Day folders: YYYY_MM_DD_<Title>", "Hint")
        hint.setTextFormat(Qt.PlainText)
        tcol.addWidget(hint)
        g.addLayout(tcol, 1, 1, Qt.AlignTop)
        self.dev_box = QVBoxLayout()
        self.dev_box.setSpacing(4)
        g.addLayout(self.dev_box, 1, 2, Qt.AlignTop)
        g.setColumnStretch(0, 5)
        g.setColumnStretch(1, 3)
        g.setColumnStretch(2, 3)
        strip.body.addLayout(g)
        root.addWidget(strip)

        # ---------------------------------------------------- empty state / day list
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_empty())
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(10)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(button("Select all", "Chip", slot=lambda: self._select("all")))
        bar.addWidget(button("Select none", "Chip", slot=lambda: self._select("none")))
        bar.addWidget(button("Not yet offloaded", "Chip", slot=lambda: self._select("new")))
        bar.addStretch(1)
        self.sel_info = label("", "Muted")
        bar.addWidget(self.sel_info)
        cl.addLayout(bar)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("Days")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("DaysInner")
        self.days_lay = QVBoxLayout(inner)
        self.days_lay.setContentsMargins(0, 0, 6, 0)
        self.days_lay.setSpacing(12)
        self.days_lay.addStretch(1)
        self.scroll.setWidget(inner)
        cl.addWidget(self.scroll, 1)
        self.stack.addWidget(content)
        root.addWidget(self.stack, 1)

        # ---------------------------------------------------- options + go
        bottom = QHBoxLayout()
        bottom.setSpacing(18)
        self.opt_subfolders = QCheckBox("Camera subfolders")
        self.opt_subfolders.setToolTip("2026_11_14_DailyRun/GoPro_HERO13_Black/… so several cameras on one day stay apart")
        self.opt_subfolders.setChecked(self.s.camera_subfolders)
        self.opt_proxies = QCheckBox("Skip proxies && thumbnails")
        self.opt_proxies.setToolTip("Leave .LRV, DJI .LRF, .THM and Sony SUB/THMBNL files on the card")
        self.opt_proxies.setChecked(self.s.skip_proxies)
        self.opt_delete = QCheckBox("Delete from card after verify")
        self.opt_delete.setToolTip(
            "Only files whose checksum verified are deleted, and only if the whole day verified.\n"
            "Formatting in camera is still cleanest (Sony cameras keep a clip database).")
        self.opt_delete.setChecked(self.s.delete_from_card)
        for cb in (self.opt_subfolders, self.opt_proxies, self.opt_delete):
            cb.toggled.connect(self._options_changed)
            bottom.addWidget(cb)
        bottom.addStretch(1)
        self.go = button("Offload", "Primary", slot=self._start, icon_name="offload", icon_color="#ffffff")
        self.go.setMinimumWidth(270)
        bottom.addWidget(self.go)
        root.addLayout(bottom)

        self.host.busy_changed.connect(lambda _b: self._update_go())
        self._update_go()

    def _build_empty(self) -> QWidget:
        w = QFrame()
        w.setObjectName("Empty")
        lay = QVBoxLayout(w)
        lay.addStretch(1)

        class _Art(QWidget):
            def __init__(self):
                super().__init__()
                self.setFixedSize(96, 96)

            def paintEvent(self, _e):  # noqa: N802
                from PySide6.QtCore import QRectF
                from PySide6.QtGui import QColor, QPainter

                p = QPainter(self)
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#161b26"))
                p.drawRoundedRect(QRectF(0, 0, 96, 96), 26, 26)
                draw_glyph(p, "card", QRectF(22, 20, 52, 56), "#5b8cff", 1.6)

        art = _Art()
        lay.addWidget(art, 0, Qt.AlignHCenter)
        lay.addSpacing(10)
        t = label("Insert a camera card", "EmptyTitle")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        self.empty_sub = label("GoPro, DJI, Insta360 and Sony cards are picked up automatically.\n"
                               "Shooting days show up here with thumbnails of every clip.", "Muted")
        self.empty_sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.empty_sub)
        lay.addSpacing(8)
        b = button("Browse for a folder…", slot=self._browse_source, icon_name="folder")
        lay.addWidget(b, 0, Qt.AlignHCenter)
        lay.addStretch(2)
        return w

    # ================================================================ volumes
    def on_volumes(self, vols: list) -> None:
        cams = [v for v in vols if v.camera]
        roots = {v.root for v in cams}
        new = [v for v in cams if v.root not in self._seen_roots]
        first, self._first_volumes = self._first_volumes, False
        self._seen_roots = roots
        cur = self.scan.root if self.scan else ""
        if cur and not os.path.isdir(cur):
            self._clear_scan("Card removed.")
            cur = ""
        if new and not self.host.busy and (first or not cur or cur not in roots):
            self.set_source(new[0].root)

    def _browse_source(self) -> None:
        start = self.scan.root if self.scan else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Choose card or folder", start)
        if p:
            self.set_source(os.path.normpath(p))

    def set_source(self, root: str) -> None:
        self._scan(root)

    def rescan(self) -> None:
        if self.scan:
            self._scan(self.scan.root)

    # ================================================================ scanning
    def _scan(self, root: str) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self.h_sub.setText(f"Scanning {root} …")
        self.source_changed.emit(root)
        w = Worker(lambda emit: scan_card(root), self)
        w.bridge.finished.connect(self._scan_done)
        w.bridge.failed.connect(lambda tb: self._clear_scan(f"Scan failed: {tb.splitlines()[-1]}"))
        self.scan_worker = w
        w.start()

    def _clear_scan(self, msg: str) -> None:
        self.scan = None
        self._clear_sections()
        self.badge.setVisible(False)
        self.h_title.setText("Offload")
        self.h_sub.setText(msg)
        self.stack.setCurrentIndex(0)
        self._rebuild_devices()
        self.source_changed.emit("")
        self._update_go()

    def _clear_sections(self) -> None:
        self.loader.clear_queue()
        for sec in self.sections:
            sec.setParent(None)
            sec.deleteLater()
        self.sections = []
        self.src_index.clear()
        self.key_index.clear()

    def _scan_done(self, scan: CardScan) -> None:
        if not scan.files:
            self._clear_scan(f"No camera media found on {scan.root}.")
            return
        self.scan = scan
        first = next(iter(scan.devices.values()))
        self.badge.set_camera(first)
        self.badge.setVisible(True)
        self.h_title.setText("  +  ".join(scan.devices.values()))
        n = len(scan.days)
        self.h_sub.setText(f"{scan.root}   ·   {len(scan.files)} files   ·   {human_bytes(scan.size)}   ·   "
                           f"{n} shooting day{'s' if n != 1 else ''}")
        self.stack.setCurrentIndex(1)
        self._rebuild_devices()
        self._populate()
        self.source_changed.emit(scan.root)

    # ================================================================ device folders
    def _rebuild_devices(self) -> None:
        def drop(w):
            w.hide()
            w.setParent(None)
            w.deleteLater()

        while self.dev_box.count():
            it = self.dev_box.takeAt(0)
            lay = it.layout()
            if lay is not None:
                while lay.count():
                    w = lay.takeAt(0).widget()
                    if w:
                        drop(w)
                lay.deleteLater()
            elif it.widget():
                drop(it.widget())
        self.device_edits.clear()
        if not self.scan:
            self.dev_box.addWidget(label("Set per camera once a card is in", "Hint"))
            return
        for prof, detected in self.scan.devices.items():
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(CamBadge(detected, 26))
            edit = QLineEdit(self.s.device_aliases.get(detected) or sanitize(detected, self.s.spaces_to_underscores))
            edit.setToolTip(f"Subfolder for {detected}. Remembered for next time.")
            edit.editingFinished.connect(lambda d=detected, e=edit: self._alias_changed(d, e))
            row.addWidget(edit, 1)
            self.dev_box.addLayout(row)
            self.device_edits[prof] = edit
        self._sync_device_visibility()

    def _sync_device_visibility(self) -> None:
        vis = self.opt_subfolders.isChecked()
        self.dev_caption.setVisible(vis)
        for e in self.device_edits.values():
            e.setEnabled(vis)

    def _alias_changed(self, detected: str, edit: QLineEdit) -> None:
        val = sanitize(edit.text(), self.s.spaces_to_underscores) or sanitize(detected, self.s.spaces_to_underscores)
        edit.setText(val)
        self.s.device_aliases[detected] = val
        self.s.save()
        self.refresh_all()

    def device_folders(self) -> dict[str, str]:
        return {p: (sanitize(e.text(), self.s.spaces_to_underscores) or p) for p, e in self.device_edits.items()}

    # ================================================================ days
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
        self._clear_sections()
        existing = existing_day_folders(self.dest.path()) if self.dest.path() else {}
        for day in self.scan.days:
            title = title_from_existing(existing[day.day][0]) if day.day in existing else self.default_title.text()
            sec = DaySection(day, self.scan.devices, self.loader, title)
            sec.changed.connect(self._section_changed)
            self.days_lay.insertWidget(self.days_lay.count() - 1, sec)
            self.sections.append(sec)
            for path, it in sec.items.items():
                self.src_index[path] = (sec, it)
                self.key_index.setdefault(it.data(ROLE)["key"], []).append((sec, it))
        self.refresh_all(set_checks=True)

    def _folder(self, sec: DaySection) -> str:
        return day_folder_name(sec.day.day, sec.title.text(), self.s.spaces_to_underscores)

    def refresh_all(self, set_checks: bool = False) -> None:
        if not self.scan:
            return
        opts = self._opts()
        dev = self.device_folders()
        dest = self.dest.path()
        for sec in self.sections:
            folder = self._folder(sec)
            sec.folder.setText(folder)
            sec.folder.setToolTip(os.path.join(dest, folder) if dest else folder)
            if not dest:
                set_pill(sec.status, "Choose a working drive", "muted")
                new = True
                sec.set_present(set())
            else:
                dest_dir = os.path.join(dest, folder)
                present, total, present_set = day_presence(sec.day, dest_dir, dev, opts)
                sec.set_present(present_set)
                new = present < total
                if not os.path.isdir(dest_dir):
                    set_pill(sec.status, "New folder", "info")
                elif present >= total:
                    set_pill(sec.status, "✓  Already offloaded", "ok")
                elif present:
                    set_pill(sec.status, f"Partly there  {present}/{total}", "warn")
                else:
                    set_pill(sec.status, "Adds to existing folder", "info")
            if set_checks:
                sec.check.blockSignals(True)
                sec.check.setChecked(new)
                sec.check.blockSignals(False)
                sec._toggled(new)
        self._update_go()

    def _section_changed(self) -> None:
        sec = self.sender()
        if isinstance(sec, DaySection) and self.scan:
            folder = self._folder(sec)
            if sec.folder.text() != folder:
                self.refresh_all()
                return
        self._update_go()

    def _thumb_ready(self, key: str) -> None:
        for sec, it in self.key_index.get(key, ()):
            sec.grid.refresh(it)

    def _default_title_changed(self, text: str) -> None:
        self.s.default_title = text
        self.s.save()
        if not self.scan:
            return
        existing = existing_day_folders(self.dest.path()) if self.dest.path() else {}
        for sec in self.sections:
            if not sec.edited and sec.day.day not in existing:
                sec.title.blockSignals(True)
                sec.title.setText(text)
                sec.title.blockSignals(False)
        self.refresh_all()

    def _dest_changed(self, path: str) -> None:
        self.s.offload_dest = path
        self.s.save()
        self.dest_changed.emit(path)
        if self.scan:
            self._populate()
        self._update_go()

    def _options_changed(self) -> None:
        self.s.camera_subfolders = self.opt_subfolders.isChecked()
        self.s.skip_proxies = self.opt_proxies.isChecked()
        self.s.delete_from_card = self.opt_delete.isChecked()
        self.s.save()
        self._sync_device_visibility()
        self.refresh_all()

    def settings_changed(self) -> None:
        if self.scan:
            self._rebuild_devices()
            self.refresh_all()

    def sync_from_settings(self) -> None:
        """Easy mode edits the same settings; pick up its choices when switching back."""
        if self.dest.path() != self.s.offload_dest:
            self.dest.set_path(self.s.offload_dest, emit=False)
            if self.scan:
                self._populate()
        if self.default_title.text() != self.s.default_title:
            self.default_title.blockSignals(True)
            self.default_title.setText(self.s.default_title)
            self.default_title.blockSignals(False)
        self.refresh_all()
        self._update_go()

    def _select(self, mode: str) -> None:
        for sec in self.sections:
            on = mode == "all" or (mode == "new" and not sec.status.text().startswith("✓"))
            sec.check.setChecked(on)
        self._update_go()

    def selected(self) -> list[DaySection]:
        return [s for s in self.sections if s.check.isChecked()]

    def _update_go(self) -> None:
        sel = self.selected()
        opts = self._opts()
        size = sum(f.size for s in sel for f in s.day.files if not (opts.skip_proxies and f.role in ("proxy", "thumb")))
        n = len(sel)
        self.go.setText(f"  Offload {n} day{'s' if n != 1 else ''}   ·   {human_bytes(size)}" if n else "  Offload")
        self.sel_info.setText(f"{n} of {len(self.sections)} days selected" if self.sections else "")
        self.go.setEnabled(bool(n) and bool(self.dest.path()) and not self.host.busy)

    # ================================================================ transfer
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
        sel = self.selected()
        opts = self._opts()
        job = plan_offload(self.scan, [(s.day, self._folder(s)) for s in sel], dest, self.device_folders(), opts)
        if not job.groups:
            return
        if opts.delete_source:
            ans = QMessageBox.question(
                self, "Delete from card after verify?",
                "Files will be deleted from the card after their checksums verify on the working drive.\n"
                "Days with any problem are left untouched.\n\nContinue?")
            if ans != QMessageBox.Yes:
                return
        if not self.host.preflight(job, dest):
            return
        self.host.resolve_workers(job, self.scan.root, dest)
        self._job_deletes = opts.delete_source
        for s in sel:
            s.set_all_status("queued")
        self.host.run_transfer(job, after=self._after_job, on_event=self.on_job_event)

    def on_job_event(self, kind: str, p: dict) -> None:
        if kind == "progress":
            for slot in p.get("active", ()):
                hit = self.src_index.get(slot.get("src", ""))
                if not hit:
                    continue
                sec, it = hit
                info = it.data(ROLE)
                info["status"] = PHASE_STATUS.get(slot.get("phase", ""), "copying")
                tot = slot.get("file_total") or 0
                info["progress"] = slot.get("file_done", 0) / tot if tot else 0.0
                sec.grid.refresh(it)
        elif kind == "file":
            hit = self.src_index.get(p.get("src", ""))
            if hit:
                sec, it = hit
                info = it.data(ROLE)
                info["status"] = FINAL_STATUS.get(p.get("status", ""), "")
                info["progress"] = 1.0
                sec.grid.refresh(it)

    def _after_job(self, _result) -> None:
        if not self.scan:
            return
        if self._job_deletes or not os.path.isdir(self.scan.root):
            if os.path.isdir(self.scan.root):
                self._scan(self.scan.root)
            return
        for sec in self.sections:
            sec.set_all_status("")
        self.refresh_all(set_checks=True)
