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
    "phone": ("PH", "#34d399"),
    "generic": ("CAM", "#94a3b8"),
}


def camera_style(label_or_profile: str) -> tuple[str, str]:
    low = (label_or_profile or "").lower()
    if low.startswith(("phone", "pixel", "apple", "iphone", "samsung", "galaxy")):
        return CAMERA_STYLES["phone"]
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
    elif name == "camera":
        path.addRoundedRect(QRectF(P(2.5, 7), P(21.5, 19.5)), 2.5 * s, 2.5 * s)
        path.moveTo(P(8, 7)); path.lineTo(P(9.5, 4.5)); path.lineTo(P(14.5, 4.5)); path.lineTo(P(16, 7))
        path.addEllipse(P(12, 13.2), 3.6 * s, 3.6 * s)
        path.addEllipse(P(18.2, 9.6), 0.6 * s, 0.6 * s)
    elif name == "laptop":
        path.addRoundedRect(QRectF(P(4.5, 4.5), P(19.5, 15.5)), 1.5 * s, 1.5 * s)
        path.moveTo(P(2, 18.5)); path.lineTo(P(22, 18.5)); path.lineTo(P(20.5, 20)); path.lineTo(P(3.5, 20)); path.closeSubpath()
    elif name == "search":
        path.addEllipse(P(10.5, 10.5), 6 * s, 6 * s)
        path.moveTo(P(15, 15)); path.lineTo(P(20.5, 20.5))
    elif name == "usb":  # USB-C plug on a cable
        path.addRoundedRect(QRectF(P(8, 2.5), P(16, 12)), 2 * s, 2 * s)
        path.moveTo(P(10.5, 5.5))
        path.lineTo(P(13.5, 5.5))
        path.moveTo(P(12, 12))
        path.lineTo(P(12, 15))
        path.cubicTo(P(12, 19), P(17, 18), P(17, 21.5))
    elif name == "palette":
        path.addEllipse(P(12, 12), 8.5 * s, 8.5 * s)
        for x, y in ((8.3, 10.0), (11.2, 7.4), (15.0, 8.4)):
            path.addEllipse(P(x, y), 1.3 * s, 1.3 * s)
        path.addEllipse(P(14.6, 15.0), 2.1 * s, 2.1 * s)
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


# ---------------------------------------------------------------- device icons
# Original line drawings of device types (not brand logos): drone, action cam, 360 cam,
# cinema camera body, phone, memory card. Drawn on a 48-unit grid.

DEVICE_KINDS = ("drone", "action", "360", "cinema", "phone", "card")


def draw_device(p: QPainter, kind: str, r: QRectF, color: str, width: float = 2.2, fill_alpha: int = 34) -> None:
    s = min(r.width(), r.height()) / 48.0
    ox = r.x() + (r.width() - 48 * s) / 2
    oy = r.y() + (r.height() - 48 * s) / 2

    def P(x, y):
        return QPointF(ox + x * s, oy + y * s)

    def R(x1, y1, x2, y2):
        return QRectF(P(x1, y1), P(x2, y2))

    col = QColor(color)
    fill = QColor(col)
    fill.setAlpha(fill_alpha)
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    pen = _pen(color, max(1.2, width * s * 0.72))
    p.setPen(pen)
    p.setBrush(fill)
    if kind == "drone":
        for cx, cy in ((10, 11), (38, 11), (10, 37), (38, 37)):
            p.setBrush(Qt.NoBrush)
            p.drawLine(P(24 + (cx - 24) * 0.25, 24 + (cy - 24) * 0.25), P(cx, cy))
            p.setBrush(fill)
            p.drawEllipse(P(cx, cy), 7 * s, 7 * s)
            p.setBrush(col)
            p.drawEllipse(P(cx, cy), 1.6 * s, 1.6 * s)
        p.setBrush(fill)
        p.drawRoundedRect(R(17, 17, 31, 31), 4 * s, 4 * s)
        p.setBrush(col)
        p.drawEllipse(P(24, 28), 2.2 * s, 2.2 * s)
    elif kind == "action":
        p.drawRoundedRect(R(7, 13, 41, 38), 6 * s, 6 * s)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(R(12, 9, 19, 13), 1.2 * s, 1.2 * s)
        p.setBrush(fill)
        p.drawEllipse(P(29, 25.5), 8 * s, 8 * s)
        p.drawEllipse(P(29, 25.5), 3.6 * s, 3.6 * s)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(R(11.5, 18, 19.5, 25), 1.5 * s, 1.5 * s)
    elif kind == "360":
        p.drawRoundedRect(R(16, 5, 32, 43), 7 * s, 7 * s)
        p.drawEllipse(P(24, 14), 6.5 * s, 6.5 * s)
        p.setBrush(col)
        p.drawEllipse(P(24, 14), 2.4 * s, 2.4 * s)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(R(19.5, 24, 28.5, 33), 1.5 * s, 1.5 * s)
        p.setBrush(col)
        p.drawEllipse(P(24, 38), 1.5 * s, 1.5 * s)
    elif kind == "cinema":
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(R(19, 8, 29, 12), 1 * s, 1 * s)
        p.setBrush(fill)
        p.drawRoundedRect(R(5, 12, 43, 38), 4 * s, 4 * s)
        p.drawEllipse(P(25, 25), 10 * s, 10 * s)
        p.drawEllipse(P(25, 25), 5.5 * s, 5.5 * s)
        p.setBrush(col)
        p.drawEllipse(P(25, 25), 1.8 * s, 1.8 * s)
        p.setBrush(Qt.NoBrush)
        for x in (8.5, 11.5):
            p.drawLine(P(x, 17), P(x, 33))
        p.drawEllipse(P(39, 16.5), 1.4 * s, 1.4 * s)
    elif kind == "phone":
        p.drawRoundedRect(R(13, 3, 35, 45), 6 * s, 6 * s)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(R(16, 9, 32, 39), 2 * s, 2 * s)
        p.setBrush(col)
        p.drawEllipse(P(24, 6), 1.3 * s, 1.3 * s)
        p.drawLine(P(21, 42), P(27, 42))
    else:  # memory card
        path = QPainterPath()
        path.moveTo(P(14, 5))
        path.lineTo(P(29, 5))
        path.lineTo(P(36, 12))
        path.lineTo(P(36, 41))
        path.quadTo(P(36, 43), P(34, 43))
        path.lineTo(P(14, 43))
        path.quadTo(P(12, 43), P(12, 41))
        path.lineTo(P(12, 7))
        path.quadTo(P(12, 5), P(14, 5))
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        for x in (17, 21, 25, 29):
            p.drawLine(P(x, 9), P(x, 15))
        p.drawRoundedRect(R(16, 26, 32, 38), 1.5 * s, 1.5 * s)
    p.restore()
