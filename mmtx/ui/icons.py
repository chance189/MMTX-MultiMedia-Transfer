"""Small vector icons drawn with QPainter (crisp at any DPI, no asset files)."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

CAMERA_STYLES = {
    # profile/prefix -> (badge text, color). Plain text badges, not logos.
    "gopro": ("GP", "#2ea7e0"),
    "dji": ("DJI", "#9aa6bd"),
    "insta360": ("360", "#f2c230"),
    "sony": ("SNY", "#f07b3f"),
    "generic": ("CAM", "#8b7cf6"),
}


def camera_style(label_or_profile: str) -> tuple[str, str]:
    low = (label_or_profile or "").lower()
    for key in ("gopro", "dji", "insta360", "sony"):
        if low.startswith(key):
            return CAMERA_STYLES[key]
    return CAMERA_STYLES["generic"]


def _pen(color: str, w: float) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def draw_glyph(p: QPainter, name: str, r: QRectF, color: str, width: float = 1.7) -> None:
    """Draw a glyph into rect r (assumed roughly square)."""
    s = min(r.width(), r.height()) / 24.0
    ox = r.x() + (r.width() - 24 * s) / 2
    oy = r.y() + (r.height() - 24 * s) / 2

    def P(x, y):
        return QPointF(ox + x * s, oy + y * s)

    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(_pen(color, width * max(1.0, s * 0.9)))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    if name == "offload":  # arrow into tray
        path.moveTo(P(12, 3.5)); path.lineTo(P(12, 14))
        path.moveTo(P(7.5, 9.5)); path.lineTo(P(12, 14)); path.lineTo(P(16.5, 9.5))
        path.moveTo(P(4, 14.5)); path.lineTo(P(4, 19)); path.lineTo(P(20, 19)); path.lineTo(P(20, 14.5))
    elif name == "archive":  # box with lid
        path.addRoundedRect(QRectF(P(3.5, 4.5), P(20.5, 9)), 1.5 * s, 1.5 * s)
        path.moveTo(P(5, 9)); path.lineTo(P(5, 19.5)); path.lineTo(P(19, 19.5)); path.lineTo(P(19, 9))
        path.moveTo(P(10, 13)); path.lineTo(P(14, 13))
    elif name == "verify":  # shield + check
        path.moveTo(P(12, 3)); path.lineTo(P(19.5, 6)); path.lineTo(P(19.5, 11.5))
        path.cubicTo(P(19.5, 16), P(16, 19.5), P(12, 21)); path.cubicTo(P(8, 19.5), P(4.5, 16), P(4.5, 11.5))
        path.lineTo(P(4.5, 6)); path.closeSubpath()
        path.moveTo(P(8.5, 12)); path.lineTo(P(11, 14.5)); path.lineTo(P(15.5, 9.5))
    elif name == "settings":  # sliders
        for y, kx in ((6, 15), (12, 8), (18, 13)):
            path.moveTo(P(4, y)); path.lineTo(P(20, y))
            path.addEllipse(P(kx, y), 2.2 * s, 2.2 * s)
    elif name == "card":  # SD card
        path.moveTo(P(7, 2.5)); path.lineTo(P(18, 2.5)); path.lineTo(P(19.5, 4)); path.lineTo(P(19.5, 21.5))
        path.lineTo(P(4.5, 21.5)); path.lineTo(P(4.5, 5)); path.closeSubpath()
        for x in (9, 12, 15):
            path.moveTo(P(x, 5.5)); path.lineTo(P(x, 8.5))
    elif name == "drive":
        path.addRoundedRect(QRectF(P(3, 7), P(21, 17)), 2.5 * s, 2.5 * s)
        path.moveTo(P(6.5, 12)); path.lineTo(P(12, 12))
        path.addEllipse(P(17, 12), 1.0 * s, 1.0 * s)
    elif name == "folder":
        path.moveTo(P(3, 6)); path.lineTo(P(9, 6)); path.lineTo(P(11, 8)); path.lineTo(P(21, 8))
        path.lineTo(P(21, 19)); path.lineTo(P(3, 19)); path.closeSubpath()
    elif name == "film":
        path.addRoundedRect(QRectF(P(3, 5), P(21, 19)), 2 * s, 2 * s)
        path.moveTo(P(7, 5)); path.lineTo(P(7, 19)); path.moveTo(P(17, 5)); path.lineTo(P(17, 19))
        for y in (8.5, 12, 15.5):
            path.moveTo(P(3, y)); path.lineTo(P(7, y)); path.moveTo(P(17, y)); path.lineTo(P(21, y))
    elif name == "photo":
        path.addRoundedRect(QRectF(P(3, 5), P(21, 19)), 2 * s, 2 * s)
        path.moveTo(P(3.5, 17)); path.lineTo(P(9, 11.5)); path.lineTo(P(13, 15.5)); path.lineTo(P(15.5, 13)); path.lineTo(P(20.5, 18))
        path.addEllipse(P(16, 9), 1.5 * s, 1.5 * s)
    elif name == "audio":
        for i, h in enumerate((4, 9, 14, 7, 11, 5)):
            x = 5 + i * 2.8
            path.moveTo(P(x, 12 - h / 2)); path.lineTo(P(x, 12 + h / 2))
    elif name == "refresh":
        path.arcMoveTo(QRectF(P(4.5, 4.5), P(19.5, 19.5)), 40)
        path.arcTo(QRectF(P(4.5, 4.5), P(19.5, 19.5)), 40, 290)
        path.moveTo(P(18.5, 3.5)); path.lineTo(P(18.2, 8.2)); path.lineTo(P(13.6, 7.6))
    p.drawPath(path)
    p.restore()


@lru_cache(maxsize=64)
def icon(name: str, color: str = "#c3cad8", size: int = 20) -> QIcon:
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.setDevicePixelRatio(scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        draw_glyph(p, name, QRectF(0, 0, size, size), color)
        p.end()
        ic.addPixmap(pm)
    return ic


def draw_check(p: QPainter, center: QPointF, radius: float, fill: str, mark: str = "#ffffff",
               outline_only: bool = False) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    if outline_only:
        p.setPen(_pen(fill, 1.6))
        p.setBrush(QColor(10, 12, 16, 190))
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(fill))
    p.drawEllipse(center, radius, radius)
    p.setPen(_pen(fill if outline_only else mark, max(1.6, radius * 0.22)))
    path = QPainterPath()
    path.moveTo(center + QPointF(-radius * 0.42, 0.02 * radius))
    path.lineTo(center + QPointF(-radius * 0.1, radius * 0.34))
    path.lineTo(center + QPointF(radius * 0.45, -radius * 0.32))
    p.drawPath(path)
    p.restore()


def draw_cross(p: QPainter, center: QPointF, radius: float, fill: str) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(fill))
    p.drawEllipse(center, radius, radius)
    p.setPen(_pen("#ffffff", max(1.6, radius * 0.22)))
    d = radius * 0.36
    p.drawLine(center + QPointF(-d, -d), center + QPointF(d, d))
    p.drawLine(center + QPointF(-d, d), center + QPointF(d, -d))
    p.restore()
