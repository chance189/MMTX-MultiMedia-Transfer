"""Thumbnail grid for one shooting day, with live per-clip transfer state."""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from ..media import format_duration
from ..transfer import human_bytes
from . import theme
from .icons import draw_check, draw_cross, draw_glyph
from .widgets import open_path

ROLE = Qt.UserRole + 1


class ClipInfo:
    """Mutable per-tile state. Deliberately not a dict: PySide copies dicts stored on items
    (QVariantMap round-trip), which would silently drop every status update."""

    __slots__ = ("_d",)

    def __init__(self, **kw):
        self._d = dict(kw)

    def __getitem__(self, k):
        return self._d[k]

    def __setitem__(self, k, v):
        self._d[k] = v

    def get(self, k, default=None):
        return self._d.get(k, default)
CELL = QSize(184, 150)
THUMB_W, THUMB_H = 168, 95

KIND_BADGE = {"photo": "PHOTO", "raw": "RAW", "360photo": "360 PHOTO", "audio": "AUDIO", "other": "FILE"}
KIND_GLYPH = {"video": "film", "360": "film", "photo": "photo", "360photo": "photo", "raw": "photo", "audio": "audio"}
ACTIVE = {"copying": ("Copying", theme.ACCENT), "verifying": ("Verifying", "#3ec6c0"), "checking": ("Checking", "#3ec6c0")}


class ClipDelegate(QStyledItemDelegate):
    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.f_name = QFont()
        self.f_name.setPointSizeF(8.8)
        self.f_meta = QFont()
        self.f_meta.setPointSizeF(8.0)
        self.f_badge = QFont()
        self.f_badge.setPointSizeF(7.4)
        self.f_badge.setBold(True)

    def sizeHint(self, option, index):  # noqa: N802
        return CELL

    def _pill(self, p: QPainter, text: str, anchor: QPointF, align_right: bool, bg: QColor, fg: str = "#ffffff") -> None:
        fm = QFontMetrics(self.f_badge)
        w = fm.horizontalAdvance(text) + 10
        h = fm.height() + 2
        x = anchor.x() - w if align_right else anchor.x()
        rect = QRectF(x, anchor.y() - h, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(QColor(fg))
        p.setFont(self.f_badge)
        p.drawText(rect, Qt.AlignCenter, text)

    def paint(self, p: QPainter, opt, index) -> None:  # noqa: C901
        info = index.data(ROLE) or ClipInfo()
        r = opt.rect
        x = r.x() + (r.width() - THUMB_W) / 2
        y = r.y() + 6
        thumb = QRectF(x, y, THUMB_W, THUMB_H)
        hover = bool(opt.state & QStyle.State_MouseOver)
        status = info.get("status", "")

        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 9, 9)
        p.setClipPath(clip)
        pm = self.loader.pixmap(info.get("key", ""))
        if pm is not None and not pm.isNull():
            pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
            scale = max(THUMB_W / pw, THUMB_H / ph)
            sw, sh = THUMB_W / scale, THUMB_H / scale
            src = QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh)
            p.drawPixmap(thumb, pm, src)
        else:
            g = QLinearGradient(thumb.topLeft(), thumb.bottomRight())
            g.setColorAt(0, QColor("#1c2331"))
            g.setColorAt(1, QColor("#131823"))
            p.fillRect(thumb, g)
            gl = 30
            draw_glyph(p, KIND_GLYPH.get(info.get("kind", ""), "film"),
                       QRectF(thumb.center().x() - gl / 2, thumb.center().y() - gl / 2, gl, gl), "#3d4659", 1.5)

        if status in ACTIVE:
            label, color = ACTIVE[status]
            p.fillRect(thumb, QColor(8, 10, 14, 165))
            c = thumb.center() - QPointF(0, 6)
            rad = 17.0
            p.setPen(QPen(QColor(255, 255, 255, 45), 3.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rad, rad)
            pen = QPen(QColor(color), 3.2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            frac = max(0.0, min(1.0, info.get("progress", 0.0)))
            p.drawArc(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), 90 * 16, int(-frac * 360 * 16))
            p.setPen(QColor("#ffffff"))
            p.setFont(self.f_badge)
            p.drawText(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), Qt.AlignCenter, f"{int(frac * 100)}%")
            p.setPen(QColor("#d6dbe6"))
            p.drawText(QRectF(thumb.x(), c.y() + rad + 2, THUMB_W, 14), Qt.AlignHCenter, label)
        elif status == "queued":
            p.fillRect(thumb, QColor(8, 10, 14, 110))
        elif status in ("failed", "cancelled"):
            p.fillRect(thumb, QColor(240, 82, 90, 60) if status == "failed" else QColor(8, 10, 14, 120))
        p.setClipping(False)

        p.setBrush(Qt.NoBrush)
        if hover:
            p.setPen(QPen(QColor(theme.ACCENT), 1.6))
        else:
            p.setPen(QPen(QColor(255, 255, 255, 22), 1.0))
        p.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)

        kind = info.get("kind", "")
        if kind in ("360", "360photo"):
            self._pill(p, "360°", QPointF(thumb.left() + 6, thumb.top() + 20), False, QColor(242, 194, 48, 230), "#1a1400")
        dur = self.loader.duration(info.get("key", "")) if kind in ("video", "360") else None
        badge = format_duration(dur) if dur is not None else KIND_BADGE.get(kind, "")
        if badge and status not in ACTIVE:
            self._pill(p, badge, QPointF(thumb.right() - 6, thumb.bottom() - 6), True, QColor(6, 8, 12, 190))

        mark = QPointF(thumb.right() - 13, thumb.top() + 13)
        if status in ("copied", "skipped"):
            draw_check(p, mark, 9.5, theme.SUCCESS)
        elif status == "failed":
            draw_cross(p, mark, 9.5, theme.DANGER)
        elif info.get("present") and status not in ACTIVE and status != "queued":
            draw_check(p, mark, 9, theme.SUCCESS, outline_only=True)

        ty = thumb.bottom() + 7
        p.setFont(self.f_name)
        p.setPen(QColor(theme.TEXT))
        fm = QFontMetrics(self.f_name)
        p.drawText(QRectF(x, ty, THUMB_W, fm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(info.get("name", ""), Qt.ElideMiddle, THUMB_W))
        p.setFont(self.f_meta)
        p.setPen(QColor(theme.MUTED))
        meta = f"{info.get('time', '')}  ·  {human_bytes(info.get('size', 0))}"
        if info.get("extra"):
            meta += f"  ·  +{info['extra']}"
        p.drawText(QRectF(x, ty + fm.height() + 1, THUMB_W, 14), Qt.AlignLeft | Qt.AlignVCenter, meta)
        p.restore()


class ClipGrid(QListWidget):
    """Wrapping grid that sizes itself to its content (page scrolls, not the grid)."""

    layout_changed = Signal()

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setFlow(QListWidget.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setUniformItemSizes(True)
        self.setGridSize(CELL)
        self.setSpacing(0)
        self.setSelectionMode(QListWidget.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QListWidget.NoFrame)
        self.setObjectName("ClipGrid")
        self.setItemDelegate(ClipDelegate(loader, self))
        self.collapsed_rows = 2
        self.expanded = False
        self.itemDoubleClicked.connect(lambda it: open_path((it.data(ROLE) or ClipInfo()).get("src", "")))

    def add_clip(self, info: "ClipInfo") -> QListWidgetItem:
        it = QListWidgetItem()
        it.setData(ROLE, info)
        it.setSizeHint(CELL)
        tip = info.get("name", "")
        if info.get("extra"):
            tip += f"\n+ {info['extra']} proxy/sidecar file(s)"
        it.setToolTip(tip + "\nDouble-click to open")
        self.addItem(it)
        return it

    def refresh(self, it: QListWidgetItem) -> None:
        self.viewport().update(self.visualItemRect(it))

    def wheelEvent(self, e):  # noqa: N802 - let the page scroll
        e.ignore()

    def columns(self) -> int:
        return max(1, self.viewport().width() // CELL.width())

    def total_rows(self) -> int:
        return math.ceil(self.count() / self.columns()) if self.count() else 0

    def overflow(self) -> int:
        """How many clips are hidden while collapsed."""
        if self.expanded:
            return 0
        return max(0, self.count() - self.collapsed_rows * self.columns())

    def fit(self) -> None:
        rows = self.total_rows() if self.expanded else min(self.total_rows(), self.collapsed_rows)
        h = rows * CELL.height() + 4
        if self.height() != h:
            self.setFixedHeight(h)
        self.layout_changed.emit()

    def set_expanded(self, on: bool) -> None:
        self.expanded = on
        self.fit()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self.fit()


def clip_info(f, key: str) -> ClipInfo:
    return ClipInfo(
        key=key,
        src=f.path,
        name=f.name,
        size=f.size,
        kind=f.kind,
        time=f.capture.strftime("%H:%M"),
        status="",
        progress=0.0,
        present=False,
        extra=0,
        ext=os.path.splitext(f.name)[1].lower(),
    )
