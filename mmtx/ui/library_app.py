"""MMTX Library: browse, search, play and export everything MMTX has put in your libraries.

Runs on its own (``python -m mmtx.library``) or as a window of MMTX. A library is any folder MMTX fills:
the Google Photos library, a working drive, an archive drive. Each has its catalog in the folder itself;
a copy is kept on this computer so a drive that isn't plugged in can still be browsed and searched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import catalog as C
from ..settings import config_dir, logs_dir
from ..transfer import free_bytes, human_bytes
from . import theme
from .icons import draw_device, draw_glyph
from .lib_grid import TimelineGrid
from .lib_thumbs import ThumbCache
from .lib_viewer import Viewer
from .widgets import PathPicker, Worker, open_path

EXTRA_QSS = f"""
#LibSide {{ background:{theme.SURFACE}; border-right:1px solid {theme.BORDER}; }}
#LibSection {{ color:{theme.MUTED}; font-size:8.5pt; font-weight:700; letter-spacing:1px; padding:14px 12px 4px 12px; }}
#LibTitle {{ font-size:15pt; font-weight:800; }}
#LibBanner {{ background:#2a2213; border:1px solid #5a4520; border-radius:10px; color:#f3d9a6; padding:8px 12px; }}
#LibSelBar {{ background:#1c2840; border-radius:10px; }}
#LibEmptyTitle {{ font-size:20pt; font-weight:800; }}
QLineEdit#LibSearch {{ padding:8px 12px; border-radius:10px; font-size:10.5pt; }}
"""


def app_state_path() -> str:
    return os.path.join(config_dir(), "library_app.json")


def load_state() -> dict:
    try:
        with open(app_state_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(d: dict) -> None:
    try:
        os.makedirs(config_dir(), exist_ok=True)
        with open(app_state_path(), "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=1)
    except OSError:
        pass


def reveal(path: str) -> None:
    """Show a file selected in Explorer / Finder (Linux: open its folder)."""
    try:
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
            return
    except OSError:
        pass
    open_path(os.path.dirname(path))


# ----------------------------------------------------------------------------
# Sidebar pieces
# ----------------------------------------------------------------------------


class NavItem(QAbstractButton):
    """A sidebar row: glyph, label, count on the right."""

    def __init__(self, text: str, count: str = "", glyph: str = "", sub: str = "", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.text_, self.count, self.glyph, self.sub = text, count, glyph, sub
        self.dim = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(46 if sub else 32)
        self.setToolTip(text)

    def sizeHint(self):  # noqa: N802
        return QSize(220, self.height())

    def enterEvent(self, _e):  # noqa: N802
        self.update()

    def leaveEvent(self, _e):  # noqa: N802
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 1, -6, -1)
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1c2840"))
            p.drawRoundedRect(r, 8, 8)
        elif self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#1a1f2a"))
            p.drawRoundedRect(r, 8, 8)
        x = r.left() + 10
        col = theme.TEXT if not self.dim else theme.MUTED
        if self.glyph:
            gc = theme.ACCENT if self.isChecked() else ("#9aa3b5" if not self.dim else "#5d6577")
            gr = QRectF(x, r.center().y() - 9, 18, 18) if not self.sub else QRectF(x, r.top() + 8, 18, 18)
            if self.glyph.startswith("device:"):
                draw_device(p, self.glyph[7:], gr.adjusted(-1, -1, 1, 1), gc, 1.5, 0)
            elif self.glyph == "heart":
                c = gr.center()
                path = QPainterPath()
                path.moveTo(c.x(), c.y() + 6.5)
                path.cubicTo(c.x() - 11, c.y() - 1, c.x() - 4, c.y() - 9.5, c.x(), c.y() - 3)
                path.cubicTo(c.x() + 4, c.y() - 9.5, c.x() + 11, c.y() - 1, c.x(), c.y() + 6.5)
                p.setPen(QPen(QColor(gc), 1.6))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            else:
                draw_glyph(p, self.glyph, gr, gc, 1.6)
            x += 28
        f = QFont(self.font())
        f.setPointSizeF(10)
        f.setBold(self.isChecked())
        p.setFont(f)
        fm = p.fontMetrics()
        right = r.right() - 10
        if self.count:
            p.setPen(QColor(theme.MUTED))
            cw = fm.horizontalAdvance(self.count)
            p.drawText(QRectF(right - cw, r.top(), cw, r.height() if not self.sub else 26), Qt.AlignVCenter, self.count)
            right -= cw + 8
        p.setPen(QColor(col))
        top_h = r.height() if not self.sub else 26
        p.drawText(QRectF(x, r.top() + (0 if not self.sub else 3), right - x, top_h),
                   Qt.AlignVCenter | Qt.AlignLeft, fm.elidedText(self.text_, Qt.ElideRight, int(right - x)))
        if self.sub:
            f.setBold(False)
            f.setPointSizeF(8.5)
            p.setFont(f)
            p.setPen(QColor(theme.WARN if "offline" in self.sub else theme.MUTED))
            p.drawText(QRectF(x, r.top() + 24, r.right() - x - 10, 18), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(r.right() - x - 10)))


def section(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("LibSection")
    return lab


# ----------------------------------------------------------------------------
# Dialogs
# ----------------------------------------------------------------------------


class ExportDialog(QDialog):
    """Copy the picked files into a project folder, checked, never overwriting anything there."""

    def __init__(self, parent, root: str, rows: list, state: dict):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.root, self.rows, self.state = root, rows, state
        self.worker: Optional[Worker] = None
        self.runner = None
        self.result = None
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setSpacing(12)
        v.setContentsMargins(22, 20, 22, 20)
        n, size = len(rows), sum(r.size or 0 for r in rows)
        head = QLabel(f"Export {n:,} file{'s' if n != 1 else ''}  ·  {human_bytes(size)}")
        head.setStyleSheet("font-size:14pt; font-weight:800;")
        v.addWidget(head)
        note = QLabel("Copies go into the folder you pick. Each one is read back and compared, nothing "
                      "already there is overwritten, and a checksum file is left next to them.")
        note.setWordWrap(True)
        v.addWidget(note)
        self.dest = PathPicker("Folder for the copies, e.g. your edit project's footage folder",
                               "Choose where the copies go")
        self.dest.set_path(state.get("export_dir", ""), emit=False)
        v.addWidget(self.dest)
        self.flat = QRadioButton("All in that folder")
        self.days = QRadioButton("A folder per day inside it  (2026_01_22_Title)")
        (self.days if state.get("export_layout") == "days" else self.flat).setChecked(True)
        self.keep = QRadioButton("Keep the file names")
        self.dated = QRadioButton("Put the date first  (2026_01_22_IMG_6367.HEIC)")
        (self.dated if state.get("export_rename") == "date" else self.keep).setChecked(True)
        g1, g2 = QButtonGroup(self), QButtonGroup(self)
        for b in (self.flat, self.days):
            g1.addButton(b)
            v.addWidget(b)
        for b in (self.keep, self.dated):
            g2.addButton(b)
            v.addWidget(b)
        self.verify = QCheckBox("Check every copy (read it back and compare)")
        self.verify.setChecked(state.get("export_verify", True))
        v.addWidget(self.verify)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        v.addWidget(self.bar)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        self.go = QPushButton("Export")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self._go)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.go)
        v.addLayout(row)

    def _go(self) -> None:
        if self.result is not None:
            open_path(self.dest.path())
            self.accept()
            return
        dest = self.dest.path()
        if not dest:
            self.status.setText("Choose a folder first.")
            return
        need = sum(r.size or 0 for r in self.rows)
        free = free_bytes(dest if os.path.isdir(dest) else os.path.dirname(dest.rstrip("\\/")) or dest)
        if free >= 0 and need > free:
            self.status.setText(f"Not enough space there: needs {human_bytes(need)}, {human_bytes(free)} free.")
            return
        self.state.update(export_dir=dest, export_layout="days" if self.days.isChecked() else "flat",
                          export_rename="date" if self.dated.isChecked() else "keep",
                          export_verify=self.verify.isChecked())
        save_state(self.state)
        opts = C.ExportOptions(dest=dest, layout=self.state["export_layout"], rename=self.state["export_rename"],
                               verify=self.verify.isChecked())
        for w in (self.dest, self.flat, self.days, self.keep, self.dated, self.verify, self.go):
            w.setEnabled(False)
        self.bar.setVisible(True)
        self.status.setText("Copying…")

        def work(emit):
            self.runner = C.ExportRunner(self.root, self.rows, opts, emit=emit, log_dir=logs_dir())
            return self.runner.run()

        self.worker = Worker(work, self)
        self.worker.bridge.event.connect(self._event)
        self.worker.bridge.finished.connect(self._done)
        self.worker.bridge.failed.connect(lambda tb: self._done(None, tb))
        self.worker.start()

    def _event(self, kind: str, p: dict) -> None:
        if kind == "progress" and p.get("total"):
            self.bar.setValue(int(1000 * p.get("done", 0) / max(1, p["total"])))
            if p.get("name"):
                self.status.setText(f"{p.get('phase', 'Copying')} {p['name']}…")

    def _done(self, res, tb: str = "") -> None:
        self.result = res
        self.bar.setValue(1000)
        self.cancel_btn.setText("Close")
        if res is None:
            self.status.setText(f"Export stopped: {tb.strip().splitlines()[-1] if tb else 'unknown error'}")
            return
        bits = [f"{res.files_copied:,} copied"]
        if res.files_skipped:
            bits.append(f"{res.files_skipped:,} were already there (same checksum)")
        if res.files_failed:
            bits.append(f"{res.files_failed:,} failed")
        checked = " Every copy was read back and matched." if self.verify.isChecked() and not res.files_failed else ""
        self.status.setText(("Stopped. " if res.cancelled else "Done. ") + ", ".join(bits) + "." + checked +
                            ("\n" + "\n".join(res.errors[:5]) if res.errors else ""))
        self.go.setEnabled(True)
        self.go.setText("Open folder")

    def _cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning() and self.runner is not None:
            self.runner.cancel()
            return
        self.reject()


# ----------------------------------------------------------------------------
# The window
# ----------------------------------------------------------------------------


class LibraryWindow(QMainWindow):
    library_opened = Signal(str)

    def __init__(self, mmtx_settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MMTX Library")
        self.mmtx_settings = mmtx_settings
        self.state = load_state()
        self.cat: Optional[C.Catalog] = None
        self.entry: Optional[dict] = None
        self.root = ""
        self.online = False
        self.filter: dict = {}
        self.rows: list = []
        self.index_worker: Optional[Worker] = None
        self._index_queue: list = []
        self.thumbs = ThumbCache(self)
        self.setStyleSheet(EXTRA_QSS)
        self.resize(1480, 940)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.side = QWidget()
        self.side.setObjectName("LibSide")
        self.side.setFixedWidth(262)
        sv = QVBoxLayout(self.side)
        sv.setContentsMargins(8, 14, 8, 10)
        sv.setSpacing(0)
        title = QLabel("  MMTX Library")
        title.setObjectName("LibTitle")
        sv.addWidget(title)
        self.side_scroll = QScrollArea()
        self.side_scroll.setWidgetResizable(True)
        self.side_scroll.setFrameShape(QScrollArea.NoFrame)
        self.side_inner = QWidget()
        self.side_box = QVBoxLayout(self.side_inner)
        self.side_box.setContentsMargins(0, 0, 0, 0)
        self.side_box.setSpacing(1)
        self.side_scroll.setWidget(self.side_inner)
        sv.addWidget(self.side_scroll, 1)
        add = QPushButton("+  Add a library folder")
        add.clicked.connect(self.add_library)
        sv.addWidget(add)
        h.addWidget(self.side)

        self.main = QWidget()
        mv = QVBoxLayout(self.main)
        mv.setContentsMargins(18, 14, 0, 0)
        mv.setSpacing(10)
        tb = QHBoxLayout()
        tb.setContentsMargins(0, 0, 18, 0)
        self.search = QLineEdit()
        self.search.setObjectName("LibSearch")
        self.search.setPlaceholderText("Search: beach, GoPro, Zulu, january 2026, IMG_6367, DailyRun…  (-word leaves out)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        tb.addWidget(self.search, 1)
        self.group = QComboBox()
        self.group.addItems(["By day", "By month"])
        self.group.setToolTip("One section per day (and shoot), or per month for libraries with a few photos a day")
        self.group.setCurrentIndex(1 if self.state.get("group") == "month" else 0)
        self.group.currentIndexChanged.connect(self._group_changed)
        tb.addWidget(self.group)
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(96, 340)
        self.zoom.setFixedWidth(130)
        self.zoom.setToolTip("Tile size (Ctrl + mouse wheel)")
        self.zoom.valueChanged.connect(lambda v: self.grid.set_tile(v))
        tb.addWidget(QLabel("Size"))
        tb.addWidget(self.zoom)
        self.rescan = QPushButton("Rescan")
        self.rescan.setToolTip("Look for new, changed and removed files")
        self.rescan.clicked.connect(lambda: self.root and self._index(self.root))
        tb.addWidget(self.rescan)
        mv.addLayout(tb)
        self.banner = QLabel()
        self.banner.setObjectName("LibBanner")
        self.banner.setWordWrap(True)
        self.banner.setVisible(False)
        bwrap = QHBoxLayout()
        bwrap.setContentsMargins(0, 0, 18, 0)
        bwrap.addWidget(self.banner)
        mv.addLayout(bwrap)
        # status / selection row
        self.status_row = QStackedWidget()
        self.status_row.setFixedHeight(40)
        info = QWidget()
        ih = QHBoxLayout(info)
        ih.setContentsMargins(0, 0, 18, 0)
        self.count_label = QLabel()
        self.count_label.setStyleSheet(f"color:{theme.MUTED};")
        ih.addWidget(self.count_label)
        ih.addStretch(1)
        self.index_label = QLabel()
        self.index_label.setStyleSheet(f"color:{theme.MUTED};")
        self.index_bar = QProgressBar()
        self.index_bar.setFixedWidth(160)
        self.index_bar.setFixedHeight(6)
        self.index_bar.setTextVisible(False)
        self.index_bar.setVisible(False)
        ih.addWidget(self.index_label)
        ih.addWidget(self.index_bar)
        self.status_row.addWidget(info)
        sel = QWidget()
        sel.setObjectName("LibSelBar")
        sh = QHBoxLayout(sel)
        sh.setContentsMargins(12, 4, 8, 4)
        self.sel_label = QLabel()
        self.sel_label.setStyleSheet("font-weight:700;")
        sh.addWidget(self.sel_label)
        sh.addStretch(1)
        self.sel_export = QPushButton("Export…")
        self.sel_export.setObjectName("Primary")
        self.sel_export.clicked.connect(lambda: self.export(self.grid.selected_rows()))
        self.sel_collect = QPushButton("Add to collection…")
        self.sel_collect.clicked.connect(lambda: self.collect(self.grid.selected_rows()))
        self.sel_fav = QPushButton("♥ Favorite")
        self.sel_fav.clicked.connect(lambda: self.set_fav(self.grid.selected_rows(), True))
        self.sel_copy = QPushButton("Copy paths")
        self.sel_copy.clicked.connect(lambda: self.copy_paths(self.grid.selected_rows()))
        clear = QPushButton("✕")
        clear.setToolTip("Clear the selection (Esc)")
        clear.clicked.connect(lambda: self.grid.clear_selection())
        for b in (self.sel_export, self.sel_collect, self.sel_fav, self.sel_copy, clear):
            sh.addWidget(b)
        self.status_row.addWidget(sel)
        srw = QHBoxLayout()
        srw.setContentsMargins(0, 0, 18, 0)
        srw.addWidget(self.status_row)
        mv.addLayout(srw)
        self.pages = QStackedWidget()
        self.grid = TimelineGrid(self.thumbs)
        self.grid.opened.connect(self.open_viewer)
        self.grid.selection_changed.connect(self._selection_changed)
        self.grid.context.connect(self._context)
        self.pages.addWidget(self.grid)
        self.empty = QWidget()
        ev = QVBoxLayout(self.empty)
        ev.addStretch(1)
        self.empty_title = QLabel()
        self.empty_title.setObjectName("LibEmptyTitle")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_text = QLabel()
        self.empty_text.setAlignment(Qt.AlignCenter)
        self.empty_text.setWordWrap(True)
        self.empty_text.setStyleSheet(f"color:{theme.MUTED}; font-size:11pt;")
        ev.addWidget(self.empty_title)
        ev.addWidget(self.empty_text)
        self.empty_buttons = QVBoxLayout()
        self.empty_buttons.setAlignment(Qt.AlignHCenter)
        ev.addSpacing(16)
        ev.addLayout(self.empty_buttons)
        ev.addStretch(2)
        self.pages.addWidget(self.empty)
        mv.addWidget(self.pages, 1)
        h.addWidget(self.main, 1)
        self.setCentralWidget(central)

        self.viewer = Viewer(self.thumbs, self.main)
        self.viewer.hide()
        self.viewer.closed.connect(self._viewer_closed)
        self.viewer.fav_toggled.connect(lambda p, on: self._fav_paths([p], on))
        self.viewer.export_requested.connect(self.export)
        self.viewer.collect_requested.connect(self.collect)
        self.viewer.reveal_requested.connect(lambda rel: reveal(os.path.join(self.root, *rel.split("/"))))
        self.viewer.index_changed.connect(lambda i: self.grid.ensure_visible(i))

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self.load_rows)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1500)
        self._save_timer.timeout.connect(self._save_copy)
        self.grid.group = "month" if self.state.get("group") == "month" else "day"
        self.zoom.setValue(int(self.state.get("tile", 168)))
        self.refresh_side()
        last = self.state.get("last")
        libs = C.known_libraries()
        entry = next((d for d in libs if d["id"] == last), libs[0] if libs else None)
        if entry:
            QTimer.singleShot(0, lambda: self.open_entry(entry))
        else:
            self._show_empty()

    # ---------------------------------------------------------------- sidebar
    def _clear_side(self) -> None:
        while self.side_box.count():
            it = self.side_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.setParent(None)  # gone now, not at the next event loop turn
                w.deleteLater()

    def refresh_side(self) -> None:
        self._clear_side()
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        libs = C.known_libraries()
        self.side_box.addWidget(section("Libraries"))
        for d in libs:
            online = C.locate(d) is not None
            sub = f"{d.get('count', 0):,} items" + ("" if online else "  ·  offline")
            glyph = "photos" if d.get("kind") == "photos" else "film"
            b = NavItem(d.get("name") or os.path.basename(d["root"]), "", glyph, sub)
            b.dim = not online
            b.setChecked(self.entry is not None and d["id"] == self.entry["id"])
            b.clicked.connect(lambda _c=False, e=d: self.open_entry(e))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, e=d, w=b: self._lib_menu(e, w.mapToGlobal(pos)))
            self.side_box.addWidget(b)
        if self.cat is not None:
            f = self._facets
            k = f.get("kinds", {})
            self.side_box.addWidget(section("Show"))
            self._nav("Everything", f"{f.get('total', 0):,}", "photos", {})
            if k.get("photo"):
                self._nav("Photos", f"{k['photo']:,}", "photo", {"kind": "photo"})
            if k.get("video"):
                self._nav("Videos", f"{k['video']:,}", "film", {"kind": "video"})
            if f.get("favs") or not self.cat.read_only:
                self._nav("Favorites", f"{f.get('favs', 0):,}", "heart", {"fav": True})
            models = f.get("models", [])
            if models:
                self.side_box.addWidget(section("Cameras"))
                for dev, model, n in models[:14]:
                    self._nav(model or C.DEVICE_LABEL.get(dev, dev), f"{n:,}", "device:" + C.DEVICE_KIND.get(dev, "card"),
                              {"device": dev, "model": model})
            if f.get("albums"):
                self.side_box.addWidget(section("Albums"))
                for a, n in f["albums"][:40]:
                    self._nav(a, f"{n:,}", "folder", {"album": a})
            self.side_box.addWidget(section("Collections"))
            for name, n in f.get("collections", []):
                b = self._nav(name, f"{n:,}", "archive", {"collection": name})
                b.setContextMenuPolicy(Qt.CustomContextMenu)
                b.customContextMenuRequested.connect(
                    lambda pos, nm=name, w=b: self._collection_menu(nm, w.mapToGlobal(pos)))
            if not f.get("collections"):
                hint = QLabel("Select photos or clips and choose\nAdd to collection to start one.")
                hint.setStyleSheet(f"color:{theme.MUTED}; padding:2px 14px; font-size:9pt;")
                self.side_box.addWidget(hint)
        self.side_box.addStretch(1)

    def _nav(self, text, count, glyph, flt) -> NavItem:
        b = NavItem(text, count, glyph)
        b.setChecked(flt == self.filter)
        b.clicked.connect(lambda _c=False, f=flt: self.set_filter(f))
        self.nav_group.addButton(b)
        self.side_box.addWidget(b)
        return b

    def set_filter(self, flt: dict) -> None:
        self.filter = dict(flt)
        self.load_rows()
        for b in self.nav_group.buttons():
            b.update()

    def _lib_menu(self, entry: dict, pos) -> None:
        m = QMenu(self)
        online = C.locate(entry)
        a_open = m.addAction("Open")
        a_scan = m.addAction("Rescan")
        a_scan.setEnabled(bool(online))
        a_show = m.addAction("Show in folder")
        a_show.setEnabled(bool(online))
        a_ren = m.addAction("Rename…")
        m.addSeparator()
        a_rm = m.addAction("Remove from this list")
        act = m.exec(pos)
        if act is a_open:
            self.open_entry(entry)
        elif act is a_scan and online:
            self._index(online)
        elif act is a_show and online:
            open_path(online)
        elif act is a_ren:
            name, ok = QInputDialog.getText(self, "Rename library", "Name:", text=entry.get("name", ""))
            if ok and name.strip():
                cat = self.cat if self.entry and self.entry["id"] == entry["id"] else None
                C.rename_library(entry["id"], name.strip(), cat)
                self.refresh_side()
        elif act is a_rm:
            if QMessageBox.question(self, "Remove library", f"Remove {entry.get('name')} from this list?\n\n"
                                    "Nothing on the drive is touched; add the folder again any time.") \
                    == QMessageBox.Yes:
                C.forget_library(entry["id"])
                if self.entry and self.entry["id"] == entry["id"]:
                    self._close_catalog()
                    self.entry = None
                self.refresh_side()
                libs = C.known_libraries()
                if libs:
                    self.open_entry(libs[0])
                else:
                    self._show_empty()

    def _collection_menu(self, name: str, pos) -> None:
        m = QMenu(self)
        a_exp = m.addAction("Export this collection…")
        a_exp.setEnabled(self.online)
        a_del = m.addAction("Delete collection")
        a_del.setEnabled(self.cat is not None and not self.cat.read_only)
        act = m.exec(pos)
        if act is a_exp:
            self.export(self.cat.query(collection=name))
        elif act is a_del:
            self.cat.delete_collection(name)
            self._marks_changed()
            if self.filter.get("collection") == name:
                self.filter = {}
            self._reload()

    # ---------------------------------------------------------------- libraries
    def add_library(self, path: str = "") -> None:
        start = path or self.state.get("add_dir") or os.path.expanduser("~")
        if not path:
            path = QFileDialog.getExistingDirectory(
                self, "Choose a library folder (your photo library, working drive or archive)", start)
        if not path:
            return
        path = os.path.normpath(path)
        self.state["add_dir"] = os.path.dirname(path)
        save_state(self.state)
        if not C.find_day_folders(path):
            QMessageBox.information(
                self, "No day folders there",
                "MMTX Library looks for day folders: 2026_11_14_Title (Offload and Archive) or "
                "2026/2026_01/2026_01_22 (Google Photos). Pick the folder that holds them.")
            return
        self._index(path, open_after=True)

    def open_root(self, root: str) -> None:
        """Open (indexing if needed) the library at root; used by MMTX's Library buttons."""
        root = os.path.normpath(root)
        for d in C.known_libraries():
            if os.path.normcase(os.path.abspath(d["root"])) == os.path.normcase(os.path.abspath(root)):
                self.open_entry(d)
                return
        self.add_library(root)

    def _close_catalog(self) -> None:
        if self.cat is not None:
            try:
                self.cat.close()
            except Exception:  # noqa: BLE001
                pass
        self.cat = None

    def open_entry(self, entry: dict) -> None:
        self.viewer.close_viewer() if self.viewer.isVisible() else None
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_copy()
        self._close_catalog()
        self.entry = entry
        root = C.locate(entry)
        self.online = root is not None
        self.root = root or entry["root"]
        try:
            if self.online:
                self.cat = C.Catalog(self.root)
            else:
                copy = C.saved_copy_path(entry["id"])
                if not os.path.isfile(copy):
                    self.cat = None
                    self._show_empty(f"{entry.get('name')} isn't connected",
                                     "There's no saved copy of its catalog on this computer yet. Connect the "
                                     "drive once and it can be browsed without it from then on.")
                    self.refresh_side()
                    return
                self.cat = C.Catalog.open_file(copy, self.root)
        except Exception as e:  # noqa: BLE001
            self._show_empty("Can't open this library", str(e))
            return
        self.state["last"] = entry["id"]
        save_state(self.state)
        self.thumbs.set_library(C.thumbs_dir(entry["id"]), self.root, self.online)
        self.filter = {}
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.banner.setVisible(not self.online)
        if not self.online:
            self.banner.setText(f"<b>{entry.get('name')}</b> isn't connected ({entry['root']}). You're browsing its "
                                "saved catalog: search works, and thumbnails you've seen before still show. "
                                "Connect the drive to play, open or export files.")
        self.rescan.setEnabled(self.online)
        self._reload()
        self.library_opened.emit(self.root)
        if self.online:  # pick up anything new since last time, quietly
            QTimer.singleShot(400, lambda r=self.root: self._index(r, quiet=True))

    def _reload(self, keep_scroll: bool = False) -> None:
        self._facets = self.cat.facets() if self.cat is not None else {}
        self.refresh_side()
        self.load_rows(keep_scroll=keep_scroll)

    def load_rows(self, keep_scroll: bool = False) -> None:
        if self.cat is None:
            return
        f = self.filter
        self.rows = self.cat.query(text=self.search.text(), kind=f.get("kind", ""), device=f.get("device", ""),
                                   model=f.get("model", ""), fav=f.get("fav", False),
                                   collection=f.get("collection", ""), album=f.get("album", ""))
        self.grid.set_rows(self.rows, self.root, self.online, keep_scroll=keep_scroll)
        total = self._facets.get("total", 0)
        if not total:
            self._show_empty("Nothing catalogued here yet",
                             "MMTX Library looks for day folders: 2026_11_14_Title (Offload and Archive) or "
                             "2026/2026_01/2026_01_22 (Google Photos). Press Rescan after adding some.")
        elif not self.rows:
            q = self.search.text().strip()
            self._show_empty("Nothing matches" + (f" “{q}”" if q else ""),
                             "Try fewer words, a camera (GoPro, DJI, iPhone), a month (january 2026), "
                             "a shoot title or a file name.")
        else:
            self.pages.setCurrentWidget(self.grid)
        self._update_count()

    def _update_count(self) -> None:
        n = len(self.rows)
        vids = sum(1 for r in self.rows if r.kind == "video")
        days = sorted({r.day for r in self.rows if r.day})
        bits = [f"{n:,} item{'s' if n != 1 else ''}"]
        if vids:
            bits.append(f"{vids:,} video{'s' if vids != 1 else ''}")
        if days:
            bits.append(days[0][:4] if days[0][:4] == days[-1][:4] else f"{days[0][:4]} to {days[-1][:4]}")
        size = sum(r.size or 0 for r in self.rows)
        bits.append(human_bytes(size))
        self.count_label.setText("   ·   ".join(bits))

    def _show_empty(self, title: str = "", text: str = "") -> None:
        while self.empty_buttons.count():
            it = self.empty_buttons.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not title:
            title = "Your photos and footage, all in one place"
            text = ("Add a folder MMTX fills: your Google Photos library, a working drive or an archive drive. "
                    "Everything in its day folders becomes searchable, with previews of every clip.")
            known = {os.path.normcase(os.path.abspath(d["root"])) for d in C.known_libraries()}
            s = self.mmtx_settings
            for label, path in ((("Google Photos library", getattr(s, "takeout_library", "")),
                                 ("Working drive", getattr(s, "offload_dest", "")),
                                 ("Archive drive", getattr(s, "archive_dest", ""))) if s else ()):
                if path and os.path.isdir(path) and os.path.normcase(os.path.abspath(path)) not in known:
                    b = QPushButton(f"Add your {label}:  {path}")
                    b.clicked.connect(lambda _c=False, p=path: self.add_library(p))
                    self.empty_buttons.addWidget(b)
            b = QPushButton("Choose a library folder…")
            b.setObjectName("Primary")
            b.clicked.connect(lambda: self.add_library())
            self.empty_buttons.addWidget(b)
        self.empty_title.setText(title)
        self.empty_text.setText(text)
        self.pages.setCurrentWidget(self.empty)

    def _group_changed(self, i: int) -> None:
        self.state["group"] = "month" if i == 1 else "day"
        save_state(self.state)
        self.grid.group = self.state["group"]
        self.grid.set_rows(self.rows, self.root, self.online)

    def _marks_changed(self) -> None:
        """Favorites and collections changed: refresh the saved copy soon, so they show offline too."""
        self._save_timer.start()

    def _save_copy(self) -> None:
        if self.cat is not None and not self.cat.read_only and self.entry:
            try:
                self.cat.save_copy(C.saved_copy_path(self.entry["id"]))
            except Exception:  # noqa: BLE001
                pass

    # ---------------------------------------------------------------- indexing
    def _index(self, root: str, folders=None, open_after: bool = False, quiet: bool = False) -> None:
        if self.index_worker is not None and self.index_worker.isRunning():
            self._index_queue.append((root, folders, open_after, quiet))
            return
        self.index_bar.setVisible(True)
        self.index_bar.setRange(0, 0)
        self.index_label.setText("Checking for new files…" if quiet else "Reading the library…")

        def work(emit):
            return C.index_and_remember(root, folders, on_progress=lambda i, n, f: emit("p", {"i": i, "n": n}))

        w = Worker(work, self)
        w.bridge.event.connect(self._index_progress)
        w.bridge.finished.connect(lambda res, r=root, o=open_after, q=quiet: self._indexed(r, res, o, q))
        w.bridge.failed.connect(lambda tb, r=root: self._index_failed(r, tb))
        self.index_worker = w
        w.start()

    def _index_progress(self, _kind: str, p: dict) -> None:
        if p.get("n"):
            self.index_bar.setRange(0, p["n"])
            self.index_bar.setValue(p["i"])
            self.index_label.setText(f"Reading folder {min(p['i'] + 1, p['n']):,} of {p['n']:,}")

    def _indexed(self, root: str, res_entry, open_after: bool, quiet: bool) -> None:
        res, entry = res_entry
        self.index_bar.setVisible(False)
        changed = res.added + res.updated + res.removed
        self.index_label.setText("" if quiet and not changed else
                                 f"{res.added:,} new, {res.updated:,} changed, {res.removed:,} gone")
        if open_after or self.entry is None:
            self.open_entry(entry)
        elif self.entry and entry["id"] == self.entry["id"]:
            self.entry = entry
            if changed:
                self._reload(keep_scroll=True)
            else:
                self.refresh_side()
        else:
            self.refresh_side()
        self._next_index()

    def _index_failed(self, root: str, tb: str) -> None:
        self.index_bar.setVisible(False)
        self.index_label.setText(f"Couldn't read {root}: {tb.strip().splitlines()[-1] if tb.strip() else ''}")
        self._next_index()

    def _next_index(self) -> None:
        self.index_worker = None
        if self._index_queue:
            r, f, o, q = self._index_queue.pop(0)
            self._index(r, f, o, q)

    def index_folders(self, root: str, folders: list[str]) -> None:
        """MMTX calls this after a job: update just the day folders it wrote."""
        self._index(root, folders or None, quiet=True)

    # ---------------------------------------------------------------- grid actions
    def _selection_changed(self) -> None:
        sel = self.grid.selected_rows()
        if not sel:
            self.status_row.setCurrentIndex(0)
            return
        self.status_row.setCurrentIndex(1)
        self.sel_label.setText(f"{len(sel):,} selected  ·  {human_bytes(sum(r.size or 0 for r in sel))}")
        writable = self.cat is not None and not self.cat.read_only
        self.sel_export.setEnabled(self.online)
        self.sel_collect.setEnabled(writable)
        self.sel_fav.setEnabled(writable)
        allfav = all(r.fav for r in sel)
        self.sel_fav.setText("♡ Unfavorite" if allfav else "♥ Favorite")
        try:
            self.sel_fav.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.sel_fav.clicked.connect(lambda: self.set_fav(self.grid.selected_rows(), not allfav))

    def _context(self, i: int, pos) -> None:
        if i < 0:
            return
        rows = self.grid.selected_rows() or [self.rows[i]]
        writable = self.cat is not None and not self.cat.read_only
        m = QMenu(self)
        a_open = m.addAction("Open")
        a_show = m.addAction("Show in folder")
        a_show.setEnabled(self.online)
        m.addSeparator()
        a_exp = m.addAction(f"Export {len(rows)} file{'s' if len(rows) != 1 else ''}…")
        a_exp.setEnabled(self.online)
        a_col = m.addAction("Add to collection…")
        a_col.setEnabled(writable)
        allfav = all(r.fav for r in rows)
        a_fav = m.addAction("Unfavorite" if allfav else "Favorite")
        a_fav.setEnabled(writable)
        a_rm = None
        if self.filter.get("collection") and writable:
            a_rm = m.addAction(f"Remove from {self.filter['collection']}")
        m.addSeparator()
        a_copy = m.addAction("Copy path" if len(rows) == 1 else "Copy paths")
        act = m.exec(pos)
        if act is a_open:
            self.open_viewer(i)
        elif act is a_show:
            reveal(os.path.join(self.root, *rows[0].path.split("/")))
        elif act is a_exp:
            self.export(rows)
        elif act is a_col:
            self.collect(rows)
        elif act is a_fav:
            self.set_fav(rows, not allfav)
        elif a_rm is not None and act is a_rm:
            self.cat.remove_from_collection(self.filter["collection"], [r.path for r in rows])
            self._marks_changed()
            self._reload(keep_scroll=True)
        elif act is a_copy:
            self.copy_paths(rows)

    def open_viewer(self, i: int) -> None:
        if not (0 <= i < len(self.rows)):
            return
        self.viewer.setGeometry(self.main.rect())
        self.viewer.open(self.rows, i, self.cat, self.root, self.online)

    def _viewer_closed(self) -> None:
        if self.viewer.rows is self.rows or True:
            changed = [r for r in self.viewer.rows if r.fav != next((x.fav for x in self.rows if x.path == r.path), r.fav)]
            if changed:
                self._reload(keep_scroll=True)
        self.grid.setFocus()

    def export(self, rows: list) -> None:
        if not rows or not self.online:
            return
        ExportDialog(self, self.root, rows, self.state).exec()

    def collect(self, rows: list) -> None:
        if not rows or self.cat is None or self.cat.read_only:
            return
        names = [n for n, _c in self._facets.get("collections", [])]
        d = QDialog(self)
        d.setWindowTitle("Add to collection")
        v = QVBoxLayout(d)
        v.setContentsMargins(20, 18, 20, 18)
        v.addWidget(QLabel(f"Add {len(rows):,} item{'s' if len(rows) != 1 else ''} to a collection. "
                           "Collections only list files; nothing is copied or moved."))
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(names)
        combo.setCurrentText(self.state.get("last_collection", names[0] if names else ""))
        combo.lineEdit().setPlaceholderText("New collection name, e.g. YouTube: Beach Run edit")
        v.addWidget(combo)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel, ok = QPushButton("Cancel"), QPushButton("Add")
        ok.setObjectName("Primary")
        cancel.clicked.connect(d.reject)
        ok.clicked.connect(d.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        v.addLayout(row)
        if d.exec() != QDialog.Accepted or not combo.currentText().strip():
            return
        name = combo.currentText().strip()
        self.cat.add_to_collection(name, [r.path for r in rows])
        self._marks_changed()
        self.state["last_collection"] = name
        save_state(self.state)
        self._reload(keep_scroll=True)

    def set_fav(self, rows: list, on: bool) -> None:
        self._fav_paths([r.path for r in rows], on)

    def _fav_paths(self, paths: list[str], on: bool) -> None:
        if self.cat is None or self.cat.read_only:
            return
        self.cat.set_fav(paths, on)
        self._marks_changed()
        if not self.viewer.isVisible():
            self._reload(keep_scroll=True)

    def copy_paths(self, rows: list) -> None:
        QGuiApplication.clipboard().setText("\n".join(os.path.join(self.root, *r.path.split("/")) for r in rows))
        self.index_label.setText(f"Copied {len(rows)} path{'s' if len(rows) != 1 else ''}")

    # ---------------------------------------------------------------- window
    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.viewer.isVisible():
            self.viewer.setGeometry(self.main.rect())

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key_F and e.modifiers() & Qt.ControlModifier:
            self.search.setFocus()
            self.search.selectAll()
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):  # noqa: N802
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save_copy()
        self.state["tile"] = self.zoom.value()
        save_state(self.state)
        if self.index_worker is not None:
            self.index_worker.wait(3000)
        self.viewer.close_viewer() if self.viewer.isVisible() else None
        super().closeEvent(e)


def main(argv: Optional[list[str]] = None) -> int:
    from PySide6.QtWidgets import QApplication

    from ..app import make_icon
    from .theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MMTX Library")
    apply_theme(app)
    app.setWindowIcon(make_icon())
    try:
        from ..settings import Settings

        s = Settings.load()
    except Exception:  # noqa: BLE001
        s = None
    w = LibraryWindow(s)
    w.show()
    args = argv if argv is not None else sys.argv[1:]
    if args and os.path.isdir(args[0]):
        QTimer.singleShot(0, lambda: w.open_root(args[0]))
    return app.exec()


__all__ = ["LibraryWindow", "main", "reveal"]
