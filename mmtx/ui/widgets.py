"""Shared widgets and the background worker."""

from __future__ import annotations

import os
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QRectF, QSize, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..drives import drive_info
from ..transfer import free_bytes, human_bytes
from . import theme
from .icons import camera_style, icon


def open_path(path: str) -> None:
    if path and os.path.exists(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class Card(QFrame):
    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 14)
        self.body.setSpacing(8)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("CardTitle")
            self.body.addWidget(t)


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


def button(text: str, obj: str = "", tip: str = "", slot: Optional[Callable] = None,
           icon_name: str = "", icon_color: str = "#c3cad8") -> QPushButton:
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, icon_color, 18))
        b.setIconSize(QSize(16, 16))
    if obj:
        b.setObjectName(obj)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


class PathPicker(QWidget):
    """Line edit + Browse + free space readout."""

    changed = Signal(str)

    def __init__(self, placeholder: str, dialog_title: str, show_free: bool = True, parent=None):
        super().__init__(parent)
        self.dialog_title = dialog_title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(self._emit)
        row.addWidget(self.edit, 1)
        row.addWidget(button("Browse…", slot=self._browse, icon_name="folder"))
        self.open_btn = button("Open", "Ghost", "Open in file manager", lambda: open_path(self.path()))
        row.addWidget(self.open_btn)
        lay.addLayout(row)
        self.free = label("", "Hint")
        self.free.setVisible(show_free)
        lay.addWidget(self.free)
        self._last = ""

    def path(self) -> str:
        return self.edit.text().strip()

    def set_path(self, p: str, emit: bool = True) -> None:
        self.edit.setText(p)
        if emit:
            self._emit()
        else:
            self._last = p
            self.refresh_free()

    def _browse(self) -> None:
        start = self.path() if os.path.isdir(self.path()) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, self.dialog_title, start)
        if p:
            self.set_path(os.path.normpath(p))

    def _emit(self) -> None:
        self.refresh_free()
        p = self.path()
        if p != self._last:
            self._last = p
            self.changed.emit(p)

    def refresh_free(self) -> None:
        p = self.path()
        if not p:
            self.free.setText("")
            return
        fb = free_bytes(p)
        exists = os.path.isdir(p)
        txt = f"{human_bytes(fb)} free" if fb >= 0 else "drive not found"
        if fb >= 0:
            kind = drive_info(p).label
            if kind:
                txt += f"  ·  {kind}"
        if not exists and fb >= 0:
            txt += "  ·  folder will be created"
        self.free.setText(txt)


class NumItem(QTableWidgetItem):
    """Table item that sorts by a numeric key but shows formatted text."""

    def __init__(self, text: str, key: float):
        super().__init__(text)
        self.key = key
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):  # noqa: D401
        if isinstance(other, NumItem):
            return self.key < other.key
        return super().__lt__(other)


def ro_item(text: str, tip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if tip:
        it.setToolTip(tip)
    return it


# ---------------------------------------------------------------- background work


class _Bridge(QObject):
    event = Signal(str, dict)
    finished = Signal(object)
    failed = Signal(str)


class Worker(QThread):
    """Runs ``fn(emit)`` off the UI thread; ``emit(kind, payload)`` arrives as a Qt signal."""

    def __init__(self, fn: Callable[[Callable[[str, dict], None]], Any], parent=None):
        super().__init__(parent)
        self.fn = fn
        self.bridge = _Bridge()

    def run(self) -> None:  # noqa: D401
        try:
            res = self.fn(lambda kind, payload: self.bridge.event.emit(kind, payload))
            self.bridge.finished.emit(res)
        except Exception:  # noqa: BLE001
            self.bridge.failed.emit(traceback.format_exc())


# ---------------------------------------------------------------- visual bits


def set_pill(lab: QLabel, text: str, tone: str) -> None:
    """tone: info | ok | warn | bad | muted (styled in theme via QLabel#Pill[tone=...])."""
    lab.setText(text)
    lab.setProperty("tone", tone)
    lab.style().unpolish(lab)
    lab.style().polish(lab)


def pill(text: str = "", tone: str = "muted") -> QLabel:
    lab = QLabel()
    lab.setObjectName("Pill")
    set_pill(lab, text, tone)
    return lab


def page_header(title: str, subtitle: str = "") -> tuple[QHBoxLayout, QLabel, QLabel]:
    row = QHBoxLayout()
    row.setSpacing(12)
    col = QVBoxLayout()
    col.setSpacing(2)
    t = label(title, "PageTitle")
    sub = label(subtitle, "Muted", wrap=True)
    col.addWidget(t)
    col.addWidget(sub)
    row.addLayout(col, 1)
    return row, t, sub


class CamBadge(QWidget):
    """Rounded colored square with the camera's device icon (drone, action cam, 360, cinema, phone, card)."""

    def __init__(self, label_or_profile: str = "", size: int = 34, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.set_camera(label_or_profile)

    def set_camera(self, label_or_profile: str) -> None:
        self.label = {"gopro": "GoPro", "dji": "DJI", "insta360": "Insta360", "sony": "Sony",
                      "phone": "Phone"}.get(label_or_profile, label_or_profile)
        self.text, self.color = camera_style(label_or_profile)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        c = QColor(self.color)
        bg = QColor(c)
        bg.setAlpha(40)
        p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 120), 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, r.width() * 0.28, r.width() * 0.28)
        from ..sources import device_kind_for
        from .icons import draw_device

        pad = r.width() * 0.14
        draw_device(p, device_kind_for(self.label) if self.label else "card", r.adjusted(pad, pad, -pad, -pad),
                    self.color, width=1.5, fill_alpha=50)


class DateTile(QWidget):
    """Little calendar page: NOV / 14 / SAT."""

    def __init__(self, day, parent=None):
        super().__init__(parent)
        self.day = day
        self.setFixedSize(54, 60)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        p.fillPath(path, QColor("#1a2030"))
        p.setPen(QPen(QColor("#283044"), 1))
        p.drawPath(path)
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(7.6)
        p.setFont(f)
        p.setPen(QColor(theme.ACCENT))
        p.drawText(QRectF(r.x(), r.y() + 5, r.width(), 12), Qt.AlignCenter, self.day.strftime("%b").upper())
        f.setPointSizeF(17)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(r.x(), r.y() + 15, r.width(), 28), Qt.AlignCenter, str(self.day.day))
        f.setPointSizeF(7.2)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(r.x(), r.y() + 42, r.width(), 12), Qt.AlignCenter, self.day.strftime("%a").upper())


class PillDelegate(QStyledItemDelegate):
    """Draws a table cell's text as a tinted pill using the item's foreground color."""

    def paint(self, p, opt, index):  # noqa: D401
        self.initStyleOption(opt, index)
        text = index.data(Qt.DisplayRole) or ""
        if not text:
            return super().paint(p, opt, index)
        brush = index.data(Qt.ForegroundRole)
        color = brush.color() if brush is not None else QColor(theme.MUTED)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        if opt.state & QStyle.State_Selected:  # keep row highlight
            p.fillRect(opt.rect, opt.palette.highlight())
        f = QFont(opt.font)
        f.setPointSizeF(8.6)
        f.setBold(True)
        fm = QFontMetrics(f)
        w = min(opt.rect.width() - 8, fm.horizontalAdvance(text) + 18)
        h = fm.height() + 6
        rect = QRectF(opt.rect.x() + 4, opt.rect.center().y() - h / 2 + 1, w, h)
        bg = QColor(color)
        bg.setAlpha(36)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setPen(color)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(w) - 12))
        p.restore()


class ThumbView(QWidget):
    """Rounded pixmap display (progress bar thumbnail, archive posters)."""

    def __init__(self, w: int, h: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.pm = None
        self.glyph = "film"

    def set_pixmap(self, pm) -> None:
        self.pm = pm
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        from .icons import draw_glyph

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 8, 8)
        p.setClipPath(path)
        if self.pm is not None and not self.pm.isNull():
            pw, ph = self.pm.width() / self.pm.devicePixelRatio(), self.pm.height() / self.pm.devicePixelRatio()
            sc = max(r.width() / pw, r.height() / ph)
            sw, sh = r.width() / sc, r.height() / sc
            p.drawPixmap(r, self.pm, QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh))
        else:
            p.fillRect(r, QColor("#1a2030"))
            g = min(r.width(), r.height()) * 0.45
            draw_glyph(p, self.glyph, QRectF(r.center().x() - g / 2, r.center().y() - g / 2, g, g), "#3d4659", 1.5)
        p.setClipping(False)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 8, 8)
