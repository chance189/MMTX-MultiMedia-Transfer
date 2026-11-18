"""Google Photos page (Advanced): takeout zips -> a dated photo library."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..takeout import ALBUMS_FILE, UNKNOWN_DIR, _pretty_export
from ..transfer import free_bytes, human_bytes
from . import theme
from .archive_tab import LIVE_ROLE, LiveStatusDelegate
from .photos_widgets import YearBars, ZipDropArea, drop_paths, pick_filter
from .volumes import filesystem_type
from .widgets import Card, NumItem, PathPicker, button, label, open_path, page_header, ro_item

C_PART, C_FROM, C_FILES, C_SIZE, C_STATUS = range(5)
FAT_LIMIT = 4 * 1024**3 - 1


def theme_colors() -> dict:
    return {"track": "#262c3a", "fill": theme.ACCENT, "done": theme.SUCCESS, "text": theme.TEXT,
            "sub": theme.MUTED, "line": theme.BORDER}


class _Stat(QWidget):
    def __init__(self, caption: str, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.caption = label(caption.upper(), "CardTitle")
        v.addWidget(self.caption)
        self.value = label("–")
        self.value.setStyleSheet("font-size:15pt; font-weight:800; background:transparent;")
        v.addWidget(self.value)

    def set(self, text: str, tip: str = "", caption: str = "") -> None:
        self.value.setText(text)
        self.setToolTip(tip)
        if caption:
            self.caption.setText(caption.upper())


class PhotosTab(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.host = host
        self.s = host.settings
        self.session = host.takeout
        self.setAcceptDrops(True)
        self._live: dict[str, dict] = {}  # zip path -> {"done", "total", "failed", "done_at"}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        hrow, _t, _sub = page_header(
            "Google Photos",
            "Turn Google Takeout zips into a photo library: a folder for every day, each photo stored once, "
            "every file checked. The zips are never changed or deleted.")
        root.addLayout(hrow)

        top = QHBoxLayout()
        top.setSpacing(12)
        a = Card("1 · Takeout zips")
        self.drop = ZipDropArea(theme_colors, "Drop takeout zips here, or the folder they're in",
                                "Loose .MOV files and unpacked Takeout folders work too. All parts at once, or a few "
                                "now and the rest later.", height=84)
        self.drop.clicked.connect(self._add_zips)
        a.body.addWidget(self.drop)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(button("Add zips…", "Chip", slot=self._add_zips))
        tools.addWidget(button("Add a folder…", "Chip", slot=self._add_folder))
        self.rm_btn = button("Remove", "Chip", slot=self._remove_selected)
        tools.addWidget(self.rm_btn)
        self.clear_btn = button("Clear", "Chip", slot=self.session.clear)
        tools.addWidget(self.clear_btn)
        tools.addStretch(1)
        self.parts_note = label("", "Muted")
        tools.addWidget(self.parts_note)
        a.body.addLayout(tools)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Takeout part", "Exported", "Photos & videos", "Size", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setItemDelegateForColumn(C_STATUS, LiveStatusDelegate(self.table))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setSectionResizeMode(C_PART, QHeaderView.Stretch)
        for c, w in ((C_FROM, 128), (C_FILES, 112), (C_SIZE, 80), (C_STATUS, 150)):
            self.table.setColumnWidth(c, w)
        a.body.addWidget(self.table, 1)
        top.addWidget(a, 3)

        b = Card("2 · Photo library")
        self.lib = PathPicker("e.g. D:\\Photos  (a folder on a big drive)", "Choose the photo library folder")
        self.lib.set_path(self.s.takeout_library, emit=False)
        self.lib.changed.connect(self.session.set_library)
        b.body.addWidget(self.lib)
        self.lib_line = label("", "Muted", wrap=True)
        b.body.addWidget(self.lib_line)
        b.body.addWidget(label(
            "Inside: 2026 / 2026_01 / 2026_01_22 / IMG_6367.HEIC. Each day folder also gets "
            "_google_photos.json (captions, places, people, albums) and a checksum file. "
            f"Albums are listed in {ALBUMS_FILE} instead of copied. Photos with no date at all go in "
            f"'{UNKNOWN_DIR}'.", "Hint", wrap=True))
        self.open_lib = button("Open library", "Ghost", slot=lambda: open_path(self.s.takeout_library))
        b.body.addWidget(self.open_lib, 0, Qt.AlignLeft)
        b.body.addStretch(1)
        top.addWidget(b, 2)
        root.addLayout(top, 3)

        c = Card()
        self.inside_title = label("3 · WHAT'S INSIDE", "CardTitle")
        c.body.addWidget(self.inside_title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        self.st_photos = _Stat("Photos")
        self.st_videos = _Stat("Videos")
        self.st_new = _Stat("New for the library")
        self.st_have = _Stat("Already in the library")
        self.st_copies = _Stat("Album copies")
        self.st_size = _Stat("Space needed")
        for i, w in enumerate((self.st_photos, self.st_videos, self.st_new, self.st_have, self.st_copies, self.st_size)):
            grid.addWidget(w, 0, i)
        c.body.addLayout(grid)
        self.years = YearBars(theme_colors, height=140)
        c.body.addWidget(self.years)
        self.notes = label("", "Hint", wrap=True)
        self.notes.setTextFormat(Qt.RichText)
        c.body.addWidget(self.notes)
        root.addWidget(c, 2)

        bottom = QHBoxLayout()
        bottom.setSpacing(16)
        self.scratch = QCheckBox("Unpack through a scratch folder next to each zip")
        self.scratch.setToolTip(
            "Each photo is unpacked into _mmtx_unpack_<zip name> beside its zip, moved into the library "
            "(a rename on the same drive), and deleted from scratch right away. The folder goes when the zip is "
            "done. Off: photos stream straight from the zip into the library.")
        self.scratch.setChecked(self.s.takeout_scratch)
        self.scratch.toggled.connect(self._scratch_toggled)
        bottom.addWidget(self.scratch)
        self.trash = QCheckBox("Include the Trash")
        self.trash.setChecked(self.s.takeout_trash)
        self.trash.toggled.connect(self._trash_toggled)
        bottom.addWidget(self.trash)
        self.again = QCheckBox("Check again what already went in")
        self.again.setToolTip("Normally a zip (or loose file) that went in completely is skipped. Use this after "
                              "deleting photos from the library to bring them back.")
        self.again.toggled.connect(self._again_toggled)
        bottom.addWidget(self.again)
        bottom.addStretch(1)
        self.go = button("Import", "Primary", slot=self._start, icon_name="offload", icon_color="#ffffff")
        self.go.setMinimumWidth(300)
        bottom.addWidget(self.go)
        root.addLayout(bottom)

        self._anim = QTimer(self)
        self._anim.setInterval(40)
        self._anim.timeout.connect(self._animate)
        self.session.changed.connect(self._sync)
        self.session.photo.connect(self._photo)
        host.busy_changed.connect(lambda _b: self._update_go())
        host.job_finished.connect(self._job_finished)
        self._sync()

    # ---------------------------------------------------------------- settings hooks (MainWindow calls these)
    def settings_changed(self) -> None:
        self._sync()

    def sync_from_settings(self) -> None:
        self.lib.set_path(self.s.takeout_library, emit=False)
        self.scratch.setChecked(self.s.takeout_scratch)
        self.trash.setChecked(self.s.takeout_trash)
        self._sync()

    # ---------------------------------------------------------------- adding
    def _start_dir(self) -> str:
        d = self.s.takeout_last_dir
        if d and os.path.isdir(d):
            return d
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        return dl if os.path.isdir(dl) else os.path.expanduser("~")

    def _add_zips(self) -> None:
        files, _f = QFileDialog.getOpenFileNames(self, "Choose your Google Takeout zips (and loose videos)",
                                                 self._start_dir(), pick_filter())
        if files:
            self._add([os.path.normpath(f) for f in files])

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the folder with your takeout zips and loose files",
                                             self._start_dir())
        if d:
            self._add([os.path.normpath(d)])

    def _add(self, paths: list[str]) -> None:
        if self.host.busy:
            return
        if not self.session.add(paths):
            QMessageBox.information(self, "Nothing new to add",
                                    "No takeout zips (or unpacked Takeout folders) there that aren't already in the list.")

    def _remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        parts = self.session.parts()
        self.session.remove([parts[r].path for r in rows if r < len(parts)])

    def dragEnterEvent(self, e):  # noqa: N802
        if drop_paths(e) and not self.host.busy:
            e.acceptProposedAction()
            self.drop.set_hot(True)

    def dragLeaveEvent(self, _e):  # noqa: N802
        self.drop.set_hot(False)

    def dropEvent(self, e):  # noqa: N802
        self.drop.set_hot(False)
        paths = drop_paths(e)
        if paths:
            e.acceptProposedAction()
            self._add(paths)

    # ---------------------------------------------------------------- options
    def _scratch_toggled(self, on: bool) -> None:
        self.s.takeout_scratch = on
        self.s.save()

    def _trash_toggled(self, on: bool) -> None:
        self.s.takeout_trash = on
        self.s.save()
        self.session.replan()

    def _again_toggled(self, on: bool) -> None:
        self.session.reimport = on
        self.session.replan()

    # ---------------------------------------------------------------- showing the plan
    def _sync(self) -> None:
        ses = self.session
        plan = ses.plan
        if self.lib.path() != self.s.takeout_library:
            self.lib.set_path(self.s.takeout_library, emit=False)
        has = bool(ses.lib_stats and ses.lib_stats["files"])
        self.lib_line.setText(ses.library_line(free=False) if has else "")
        self.lib_line.setVisible(has)
        self.lib.refresh_free()
        self.open_lib.setEnabled(bool(self.s.takeout_library) and os.path.isdir(self.s.takeout_library))
        if self.again.isChecked() != ses.reimport:
            self.again.blockSignals(True)
            self.again.setChecked(ses.reimport)
            self.again.blockSignals(False)
        parts = ses.parts()
        fresh = not ses.running and ses.last_result is None  # keep the finished run's marks until something changes
        if fresh:
            self._live = {}
        self.table.setRowCount(len(parts))
        for r, a in enumerate(parts):
            it = ro_item(a.label, a.path)
            self.table.setItem(r, C_PART, it)
            exp = _pretty_export(a.export) if a.zipped else (
                "unpacked Takeout" if a.label.lower() == "takeout" else "loose files")
            self.table.setItem(r, C_FROM, ro_item(exp))
            self.table.setItem(r, C_FILES, NumItem(f"{a.media:,}", a.media))
            self.table.setItem(r, C_SIZE, NumItem(human_bytes(a.size), a.size))
            st = ro_item("")
            st.setData(LIVE_ROLE, self._status_of(a))
            tip = []
            if a.trash:
                tip.append(f"{a.trash} in the Trash")
            if a.other:
                tip.append(f"{a.other} file(s) from other Google products, left alone")
            if a.error:
                tip.append(a.error)
            st.setToolTip("\n".join(tip))
            self.table.setItem(r, C_STATUS, st)
        self.rm_btn.setEnabled(bool(parts) and not ses.running)
        self.clear_btn.setEnabled(bool(ses.paths) and not ses.running)
        if ses.planning:
            self.parts_note.setText("Reading the zips…")
        elif ses.plan_error:
            self.parts_note.setText(f"Couldn't read: {ses.plan_error}")
        else:
            self.parts_note.setText(ses.summary())
        if plan is None:
            for w in (self.st_photos, self.st_videos, self.st_new, self.st_have, self.st_copies, self.st_size):
                w.set("–")
            if fresh:
                self.years.set_years({})
            self.notes.setText("Get your takeout at takeout.google.com: deselect all, tick Google Photos, "
                               "keep the .zip format. Add every part it gives you.")
        elif ses.last_result is not None and not ses.running and not plan.items:
            res = ses.last_result  # just finished: show what happened until something changes
            self.inside_title.setText("3 · LAST IMPORT")
            self.st_photos.set(f"{res.new:,}", caption="Brought in")
            self.st_videos.set(f"{res.duplicates:,}", "Album copies and photos the library already had",
                               caption="Already there")
            self.st_new.set(f"{res.days:,}", caption="Day folders")
            self.st_have.set(f"{res.albums:,}", caption="Albums")
            self.st_copies.set(f"{res.files_failed:,}", caption="Failed")
            self.st_size.set(human_bytes(res.bytes_written), caption="Written")
            self.notes.setText(f"Library: {self.s.takeout_library}. Your zips weren't changed; delete them "
                               "whenever you like.")
        else:
            self.inside_title.setText("3 · WHAT'S INSIDE")
            for w, cap in ((self.st_photos, "Photos"), (self.st_videos, "Videos"), (self.st_new, "New for the library"),
                           (self.st_have, "Already in the library"), (self.st_copies, "Album copies"),
                           (self.st_size, "Space needed")):
                w.caption.setText(cap.upper())
            self.st_photos.set(f"{plan.photos:,}")
            self.st_videos.set(f"{plan.videos:,}")
            self.st_new.set(f"{ses.new_count():,}", "Estimated from names and sizes; the import checks every file's "
                                                  "content, so the real number can only be the same or smaller.")
            self.st_have.set(f"{plan.in_library:,}", "Same name and size as a photo already in the library")
            self.st_copies.set(f"{plan.copies:,}", "Takeout puts album photos in twice (year folder and album "
                                                  "folder). They're stored once and listed in the album file.")
            self.st_size.set(human_bytes(plan.new_bytes_estimate))
            if fresh:
                self.years.set_years({y: v[0] + v[1] for y, v in plan.years.items()})
            notes = []
            if plan.skipped_archives or plan.skipped_loose:
                done = []
                if plan.skipped_archives:
                    done.append(f"{plan.skipped_archives} zip(s)")
                if plan.skipped_loose:
                    done.append(f"{plan.skipped_loose:,} loose file(s)")
                notes.append(f"{' and '.join(done)} already went in completely and are skipped. "
                             "Tick “Check again” to look at them anyway.")
            if plan.trash:
                notes.append(f"{plan.trash:,} file(s) in the Trash are left out.")
            loose = sum(1 for it in plan.items if not it.m.zipped and it.json is None)
            zipped = plan.no_json - loose
            if loose:
                notes.append(f"{loose:,} loose file(s) without a sidecar: dated by the recording date inside "
                             "them (camera clock), else a date in the name, else the file's created date.")
            if zipped:
                notes.append(f"{zipped:,} file(s) in the zips have no sidecar by name; they're matched by title "
                             "while importing, or dated by their camera clock or file name.")
            notes += [f"<span style='color:{theme.WARN}'>{w}</span>" for w in plan.warnings]
            self.notes.setText("<br>".join(notes))
        self._update_go()

    def _status_of(self, a) -> tuple:
        live = self._live.get(a.path)
        if live is not None:
            if live["failed"]:
                return ("bad", f"{live['failed']} failed", 1.0, 0.0)
            if live["total"] and live["done"] >= live["total"]:
                return ("done", "In the library", 1.0, live.get("done_at", 0.0))
            if live["done"]:
                frac = live["done"] / live["total"] if live["total"] else 0.0
                return ("almost" if frac >= 0.8 else "busy", f"{live['done']:,} / {live['total']:,}", frac, 0.0)
            return ("wait", "Waiting", 0.0, 0.0)
        if a.error:
            return ("bad", "Can't open", 0.0, 0.0)
        if a.imported_before:
            return ("done", "Already in", 1.0, 0.0)
        if not a.media:
            return ("wait", "Sidecars only" if a.jsons else "No photos", 0.0, 0.0)
        return ("wait", "Ready", 0.0, 0.0)

    def _update_go(self) -> None:
        ses = self.session
        plan = ses.plan
        busy = self.host.busy
        n = len(plan.items) if plan else 0
        if ses.planning:
            text, ok = "Reading the zips…", False
        elif not ses.paths:
            text, ok = "Add takeout zips first", False
        elif not self.s.takeout_library:
            text, ok = "Choose a library folder", False
        elif not n:
            text, ok = "Nothing new in these zips", False
        else:
            new = ses.new_count()
            if new:
                text = f"Import {new:,} new photo{'s' if new != 1 else ''}" + (" and videos" if plan.videos else "")
            else:
                text = f"Check {n:,} file{'s' if n != 1 else ''} again"
            ok = True
        self.go.setText(text)
        self.go.setEnabled(ok and not busy and ses.library_ok())

    # ---------------------------------------------------------------- running
    def _start(self) -> None:
        ses = self.session
        plan = ses.plan
        lib = self.s.takeout_library
        if plan is None or not plan.items or self.host.busy:
            return
        if not ses.library_ok():
            QMessageBox.critical(self, "Library folder not found", f"{lib} isn't reachable. Connect the drive or choose "
                                 "another folder.")
            return
        root = lib if os.path.isdir(lib) else os.path.dirname(lib.rstrip("\\/"))
        need, free = plan.new_bytes_estimate, free_bytes(root)
        if free >= 0 and need > free:
            QMessageBox.critical(self, "Not enough space",
                                 f"This needs about {human_bytes(need)}, but the library drive has {human_bytes(free)} free.")
            return
        if filesystem_type(root) in ("vfat", "fat", "fat32", "msdos", "fat16"):
            big = [it for it in plan.items if it.m.size > FAT_LIMIT]
            if big:
                QMessageBox.critical(self, "Library drive is FAT32",
                                     f"{len(big)} video(s) are larger than 4 GB, which FAT32 can't store "
                                     f"(e.g. {big[0].name}). Use an exFAT or NTFS drive.")
                return
        self._live = {}
        per: dict[str, int] = {}
        for it in plan.items:
            per[it.archive.path] = per.get(it.archive.path, 0) + 1
        for a in plan.archives:
            if a.path in per:
                self._live[a.path] = {"done": 0, "total": per[a.path], "failed": 0}
        if ses.start(show_summary=True):
            self.years.start()
            self._anim.start()
            self._apply_live()

    def _photo(self, p: dict) -> None:
        live = self._live.get(p.get("archive", ""))
        if live is not None:
            if p["state"] == "failed":
                live["failed"] += 1
            live["done"] += 1
            if live["done"] >= live["total"]:
                live["done_at"] = time.monotonic()
        if p["state"] == "new" and self.years.active:
            self.years.add(p.get("year", 0))
        self._apply_live()

    def _apply_live(self) -> None:
        parts = self.session.parts()
        for r, a in enumerate(parts):
            it = self.table.item(r, C_STATUS)
            if it is not None:
                it.setData(LIVE_ROLE, self._status_of(a))
        self.table.viewport().update()

    def _animate(self) -> None:
        from .easy_clips import done_flashing

        moving = any((d["done"] < d["total"]) or done_flashing(d) for d in self._live.values())
        if moving or self.session.running:
            self.table.viewport().update()
        else:
            self._anim.stop()

    def _job_finished(self, res) -> None:
        if res.kind != "takeout":
            return
        self.years.stop()
        QTimer.singleShot(3200, lambda: self.session.running or self._sync())
