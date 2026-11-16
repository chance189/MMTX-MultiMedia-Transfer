"""Left sidebar: navigation, inserted camera cards, working/archive drive status."""

from __future__ import annotations

import os
import shutil
from typing import Optional

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout

from ..drives import drive_info
from ..transfer import human_bytes
from . import theme
from .icons import icon
from .volumes import Volume, list_volumes
from .widgets import CamBadge, Worker, label, open_path


class VolumeWatcher(QObject):
    """Polls mounted volumes off the UI thread; emits when the set changes."""

    changed = Signal(list)

    def __init__(self, parent=None, interval_ms: int = 3000):
        super().__init__(parent)
        self.volumes: list[Volume] = []
        self._sig: tuple = ()
        self._worker: Optional[Worker] = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(interval_ms)
        QTimer.singleShot(0, self.poll)

    def poll(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        w = Worker(lambda emit: list_volumes(), self)
        w.bridge.finished.connect(self._apply)
        self._worker = w
        w.start()

    def _apply(self, vols: list) -> None:
        sig = tuple((v.root, v.camera, v.read_only) for v in vols)
        if sig != self._sig:
            self._sig = sig
            self.volumes = vols
            self.changed.emit(vols)

    def stop(self) -> None:
        self.timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(5000)


class CardTile(QFrame):
    clicked = Signal(str)

    def __init__(self, vol: Volume, parent=None):
        super().__init__(parent)
        self.root = vol.root
        self.setObjectName("CardTile")
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)
        lay.addWidget(CamBadge(vol.camera or "", 32))
        col = QVBoxLayout()
        col.setSpacing(1)
        name = label(vol.camera or "Camera", "TileTitle")
        where = vol.name if getattr(vol, "portable", False) else vol.root
        size = f"  ·  {human_bytes(vol.total)}" if vol.total else "  ·  over USB"
        sub = f"{where}{size}" + ("  ·  locked" if vol.read_only else "")
        col.addWidget(name)
        col.addWidget(label(sub, "TileSub"))
        self.status = label("", "TileStatus")
        self.status.setVisible(False)
        col.addWidget(self.status)
        lay.addLayout(col, 1)
        self.setToolTip(f"{vol.camera} on {vol.root}\nClick to show it in Offload")

    def set_status(self, text: str, tone: str) -> None:
        colors = {"busy": theme.ACCENT, "done": theme.SUCCESS, "new": "#fbbf24", "warn": theme.DANGER}
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colors.get(tone, theme.MUTED)}; font-weight: 700;")
        self.status.setVisible(bool(text))

    def set_active(self, on: bool) -> None:
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.root)


class DriveTile(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DriveTile")
        self.setCursor(Qt.PointingHandCursor)
        self.path = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 9, 12, 10)
        lay.setSpacing(4)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = label(title.upper(), "TileCaps")
        top.addWidget(self.title)
        top.addStretch(1)
        self.kind = label("", "TileKind")
        top.addWidget(self.kind)
        lay.addLayout(top)
        self.where = label("Not set", "TileTitle")
        lay.addWidget(self.where)
        self.bar = QProgressBar()
        self.bar.setObjectName("Cap")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.free = label("", "TileSub")
        lay.addWidget(self.free)

    def set_path(self, path: str) -> None:
        self.path = path
        if not path:
            self.where.setText("Not set")
            self.kind.setText("")
            self.free.setText("Choose it on the page")
            self.bar.setValue(0)
            return
        anchor = path
        while anchor and not os.path.exists(anchor) and os.path.dirname(anchor) != anchor:
            anchor = os.path.dirname(anchor)
        self.where.setText(os.path.basename(os.path.normpath(path)) or path)
        self.setToolTip(f"{path}\nClick to open")
        try:
            du = shutil.disk_usage(anchor)
            self.bar.setValue(int(1000 * du.used / du.total) if du.total else 0)
            self.free.setText(f"{human_bytes(du.free)} free of {human_bytes(du.total)}")
            self.bar.setProperty("state", "bad" if du.free < 0.08 * du.total else "")
            self.bar.style().unpolish(self.bar)
            self.bar.style().polish(self.bar)
        except OSError:
            self.bar.setValue(0)
            self.free.setText("Drive not connected")
        self.kind.setText(drive_info(anchor).label if os.path.exists(anchor) else "")

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self.path:
            open_path(self.path)


class Sidebar(QFrame):
    page_selected = Signal(int)
    card_clicked = Signal(str)
    settings_clicked = Signal()
    easy_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(250)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(6)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        from ..app import make_icon  # local import: avoid a cycle at module load

        logo = QPushButton()
        logo.setObjectName("Logo")
        logo.setIcon(make_icon())
        logo.setIconSize(QSize(30, 30))
        logo.setFixedSize(34, 34)
        brand.addWidget(logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        bcol.addWidget(label("MMTX", "Brand"))
        bcol.addWidget(label("MultiMedia Transfer", "BrandSub"))
        brand.addLayout(bcol, 1)
        lay.addLayout(brand)
        lay.addSpacing(14)

        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        for i, (text, ic) in enumerate((("Offload", "offload"), ("Archive", "archive"), ("Verify", "verify"))):
            b = QPushButton(f"  {text}")
            b.setObjectName("Nav")
            b.setCheckable(True)
            b.setIcon(icon(ic, "#c3cad8", 20))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _c=False, idx=i: self.page_selected.emit(idx))
            self.nav.addButton(b, i)
            lay.addWidget(b)
        self.nav.button(0).setChecked(True)

        lay.addSpacing(16)
        lay.addWidget(label("CAMERA CARDS", "SideCaps"))
        self.cards_box = QVBoxLayout()
        self.cards_box.setSpacing(6)
        lay.addLayout(self.cards_box)
        self.no_cards = label("No card inserted", "SideHint")
        self.cards_box.addWidget(self.no_cards)
        self.card_tiles: list[CardTile] = []

        lay.addSpacing(16)
        lay.addWidget(label("DRIVES", "SideCaps"))
        self.work_tile = DriveTile("Working drive")
        self.arch_tile = DriveTile("Archive drive")
        lay.addWidget(self.work_tile)
        lay.addWidget(self.arch_tile)
        lay.addStretch(1)

        self.mode = label("", "SideHint")
        self.mode.setWordWrap(True)
        lay.addWidget(self.mode)
        eb = QPushButton("  Easy mode")
        eb.setObjectName("EasySwitch")
        eb.setIcon(icon("offload", "#ffffff", 20))
        eb.setIconSize(QSize(18, 18))
        eb.setCursor(Qt.PointingHandCursor)
        eb.setToolTip("Big buttons, three steps, plain language")
        eb.clicked.connect(self.easy_clicked.emit)
        lay.addWidget(eb)
        self.easy_btn = eb
        sb = QPushButton("  Settings")
        sb.setObjectName("Nav")
        sb.setIcon(icon("settings", "#c3cad8", 20))
        sb.setIconSize(QSize(18, 18))
        sb.setCursor(Qt.PointingHandCursor)
        sb.clicked.connect(self.settings_clicked.emit)
        lay.addWidget(sb)
        self._active_root = ""

    def select_page(self, idx: int) -> None:
        self.nav.button(idx).setChecked(True)

    def set_volumes(self, vols: list) -> None:
        for t in self.card_tiles:
            t.setParent(None)
            t.deleteLater()
        self.card_tiles = []
        cams = [v for v in vols if v.camera]
        self.no_cards.setVisible(not cams)
        for v in cams:
            t = CardTile(v)
            t.clicked.connect(self.card_clicked.emit)
            t.set_active(v.root == self._active_root)
            self.cards_box.addWidget(t)
            self.card_tiles.append(t)

    def set_card_statuses(self, cards: dict) -> None:
        for t in self.card_tiles:
            e = cards.get(t.root)
            if e is None:
                t.set_status("", "idle")
            else:
                t.set_status(*e.status())

    def set_active_card(self, root: str) -> None:
        self._active_root = root
        for t in self.card_tiles:
            t.set_active(t.root == root)

    def set_drives(self, work: str, archive: str) -> None:
        self.work_tile.set_path(work)
        self.arch_tile.set_path(archive)

    def set_mode_text(self, text: str) -> None:
        self.mode.setText(text)


