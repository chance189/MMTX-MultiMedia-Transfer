"""Big thumbnail tiles for Easy mode.

live mode (while copying), one look per state:
    waiting      grey picture, "Waiting"
    copying      blue border that pulses, a rotating hourglass inside a blue progress ring
    almost done  orange border that flashes, hourglass in an orange ring   (80% or more of copy + check)
    done         big green check, green outline that flashes for a few seconds, then stays solid
    problem      red tint, big cross

pick mode (choosing what to copy): tap a tile to add or remove it.
    chosen       full color, bold outline, filled check in the corner
    not chosen   grey picture, empty circle
    already saved on the computer: a small green "Saved" tag either way
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from ..media import format_duration
from ..transfer import human_bytes
from .clip_grid import KIND_BADGE, KIND_GLYPH, ROLE, ClipInfo
from .icons import draw_check, draw_cross, draw_glyph
from .palettes import P, ink
from .widgets import ThumbView

CELL = QSize(232, 196)
TW, TH = 214, 120
ALMOST = 0.8

LIVE_STATE = {"queued": "wait", "copying": "busy", "verifying": "busy", "checking": "busy",
              "copied": "done", "skipped": "done", "failed": "bad", "cancelled": "stopped"}


def pulse(period: float = 1.4) -> float:
    """Smooth 0..1..0 (copying)."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * (time.monotonic() % period) / period)


def flash(period: float = 1.0) -> float:
    """Sharpened 0..1..0, reads as a flash (almost done). ~1 per second, far under 3 Hz."""
    s = pulse(period)
    for _ in range(2):
        s = s * s * (3 - 2 * s)
    return s


DONE_FLASH_S = 3.0  # finished tiles flash green this long, then stay solid (a full grid of blinking is noise)


def hourglass_angle(period: float = 1.8) -> float:
    """Sits still, then flips 180 degrees, like a real hourglass being turned over."""
    t = (time.monotonic() % period) / period
    if t < 0.7:
        return 0.0
    k = (t - 0.7) / 0.3
    return 180.0 * (0.5 - 0.5 * math.cos(math.pi * k))


def sand_level(period: float = 1.8) -> float:
    """1 = top full, 0 = drained; drains while the glass sits still."""
    t = (time.monotonic() % period) / period
    return max(0.0, 1.0 - t / 0.7)


def draw_hourglass(p: QPainter, c: QPointF, h: float, color: str, angle: float, sand: float) -> None:
    """Vector hourglass centered on c, h tall."""
    w = h * 0.62
    p.save()
    p.translate(c)
    p.rotate(angle)
    top, bot = -h / 2, h / 2
    neck = h * 0.07
    glass = QPainterPath()
    glass.moveTo(-w / 2, top + h * 0.08)
    glass.lineTo(w / 2, top + h * 0.08)
    glass.cubicTo(w / 2, top + h * 0.32, neck, -neck * 1.2, neck, 0)
    glass.cubicTo(neck, neck * 1.2, w / 2, bot - h * 0.32, w / 2, bot - h * 0.08)
    glass.lineTo(-w / 2, bot - h * 0.08)
    glass.cubicTo(-w / 2, bot - h * 0.32, -neck, neck * 1.2, -neck, 0)
    glass.cubicTo(-neck, -neck * 1.2, -w / 2, top + h * 0.32, -w / 2, top + h * 0.08)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 40))
    p.drawPath(glass)
    # sand: top bulb drains into the bottom bulb (clipped to the glass)
    p.save()
    p.setClipPath(glass)
    sand_col = QColor(color)
    upper = (h / 2 - h * 0.08) * 0.85 * sand
    if upper > 0.5:
        p.fillRect(QRectF(-w / 2, -upper, w, upper), sand_col)
    lower = (h / 2 - h * 0.08) * 0.85 * (1 - sand)
    if lower > 0.5:
        p.fillRect(QRectF(-w / 2, bot - h * 0.08 - lower, w, lower), sand_col)
    if 0.02 < sand < 0.98:
        p.fillRect(QRectF(-1.2, -2, 2.4, h / 2 - h * 0.08), sand_col)
    p.restore()
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#ffffff"), max(2.0, h * 0.06), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(glass)
    cap = max(3.0, h * 0.09)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(-w / 2 - h * 0.08, top, w + h * 0.16, cap), cap / 2, cap / 2)
    p.drawRoundedRect(QRectF(-w / 2 - h * 0.08, bot - cap, w + h * 0.16, cap), cap / 2, cap / 2)
    p.restore()


def done_flashing(info) -> bool:
    at = info.get("done_at")
    return bool(at) and time.monotonic() - at < DONE_FLASH_S


def tile_state(info) -> str:
    st = LIVE_STATE.get(info.get("status", ""), "")
    if st == "busy" and info.get("frac", 0.0) >= ALMOST:
        return "almost"
    return st


def state_color(state: str) -> str:
    return {"busy": P.busy, "almost": P.almost, "done": P.done_hi, "bad": P.bad}.get(state, P.wait)


def grey_pixmap(pm: QPixmap, cache: dict, key: str) -> QPixmap:
    hit = cache.get(key)
    if hit is not None and hit[0] == pm.cacheKey():
        return hit[1]
    img = pm.toImage().convertToFormat(QImage.Format_Grayscale8)
    g = QPixmap.fromImage(img)
    g.setDevicePixelRatio(pm.devicePixelRatio())
    cache[key] = (pm.cacheKey(), g)
    return g


def draw_cover(p: QPainter, rect: QRectF, pm: QPixmap) -> None:
    pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
    sc = max(rect.width() / pw, rect.height() / ph)
    sw, sh = rect.width() / sc, rect.height() / sc
    p.drawPixmap(rect, pm, QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh))


def draw_ring(p: QPainter, c: QPointF, rad: float, frac: float, color: str, width: float = 4.5) -> None:
    p.setPen(QPen(QColor(255, 255, 255, 60), width))
    p.setBrush(QColor(0, 0, 0, 110))
    p.drawEllipse(c, rad, rad)
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(c.x() - rad, c.y() - rad, 2 * rad, 2 * rad), 90 * 16, int(-max(0.0, min(1.0, frac)) * 5760))


def glow_border(p: QPainter, rect: QRectF, radius: float, color: str, level: float, width: float = 3.5) -> None:
    """Solid border plus a soft outer glow whose strength follows `level` (0..1)."""
    c = QColor(color)
    for k in range(9, 0, -1):
        c.setAlpha(int((20 + 150 * level) * (1 - k / 10) ** 2))
        p.setPen(QPen(c, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect.adjusted(-k, -k, k, k), radius + k, radius + k)
    c.setAlpha(int(140 + 115 * level))
    p.setPen(QPen(c, width))
    p.drawRoundedRect(rect.adjusted(width / 2, width / 2, -width / 2, -width / 2), radius, radius)


class EasyClipDelegate(QStyledItemDelegate):
    def __init__(self, loader, grid: "EasyClipGrid"):
        super().__init__(grid)
        self.loader = loader
        self.grid = grid
        self._grey: dict = {}
        self.f_name = QFont()
        self.f_name.setPointSizeF(10.5)
        self.f_name.setBold(True)
        self.f_meta = QFont()
        self.f_meta.setPointSizeF(9.5)
        self.f_pill = QFont()
        self.f_pill.setPointSizeF(10)
        self.f_pill.setBold(True)
        self.f_pct = QFont()
        self.f_pct.setPointSizeF(10.5)
        self.f_pct.setBold(True)

    def sizeHint(self, option, index):  # noqa: N802
        return CELL

    def _pill(self, p: QPainter, text: str, rect_anchor: QPointF, color: str, solid: bool = True,
              align_right: bool = False) -> None:
        fm = QFontMetrics(self.f_pill)
        w, h = fm.horizontalAdvance(text) + 18, fm.height() + 8
        x = rect_anchor.x() - w if align_right else rect_anchor.x()
        r = QRectF(x, rect_anchor.y() - h, w, h)
        p.setPen(Qt.NoPen)
        c = QColor(color)
        if not solid:
            c.setAlpha(225)
        p.setBrush(c)
        p.drawRoundedRect(r, h / 2, h / 2)
        p.setFont(self.f_pill)
        lum = c.red() * 0.299 + c.green() * 0.587 + c.blue() * 0.114
        p.setPen(QColor("#0b0b0c" if lum > 150 else "#ffffff"))
        p.drawText(r, Qt.AlignCenter, text)

    def paint(self, p: QPainter, opt, index) -> None:  # noqa: C901
        info = index.data(ROLE) or ClipInfo()
        r = opt.rect
        x = r.x() + (r.width() - TW) / 2
        thumb = QRectF(x, r.y() + 10, TW, TH)
        hover = bool(opt.state & QStyle.State_MouseOver)
        pick = self.grid.mode == "pick"
        state = "pick_on" if pick and info.get("picked") else "pick_off" if pick else tile_state(info)
        grey = state in ("wait", "pick_off", "stopped")

        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(thumb, 12, 12)
        p.setClipPath(clip)
        key = info.get("key", "")
        pm = self.loader.pixmap(key)
        if pm is not None and not pm.isNull():
            draw_cover(p, thumb, grey_pixmap(pm, self._grey, key) if grey else pm)
        else:
            p.fillRect(thumb, QColor(P.placeholder[0]))
            gl = 36
            draw_glyph(p, KIND_GLYPH.get(info.get("kind", ""), "film"),
                       QRectF(thumb.center().x() - gl / 2, thumb.center().y() - gl / 2, gl, gl), P.muted, 1.6)
        if grey:
            p.fillRect(thumb, QColor(0, 0, 0, 105 if P.dark else 70))
        elif state in ("busy", "almost"):
            p.fillRect(thumb, QColor(0, 0, 0, 95))
        elif state == "done":
            t = QColor(P.done_hi)
            t.setAlpha(80)
            p.fillRect(thumb, t)
        elif state == "bad":
            t = QColor(P.bad)
            t.setAlpha(90)
            p.fillRect(thumb, t)
        p.setClipping(False)

        c = thumb.center()
        mid = c - QPointF(0, 12)
        frac = info.get("frac", 0.0)
        if state in ("busy", "almost"):
            col = P.busy if state == "busy" else P.almost
            if state == "busy":
                glow_border(p, thumb, 12, col, pulse())
            else:
                glow_border(p, thumb, 12, col, flash(), width=4.5)
            draw_ring(p, mid, 36, frac, col, width=6)
            draw_hourglass(p, mid, 42, col, hourglass_angle(), sand_level())
            word = "Almost done" if state == "almost" else (
                "Checking" if info.get("status") in ("verifying", "checking") else "Copying")
            self._center_pill(p, thumb, f"{word}  {int(frac * 100)}%", col)
        elif state == "done":
            lvl = flash(0.6) if done_flashing(info) else 0.45
            glow_border(p, thumb, 12, P.done_hi, lvl, width=4.5 if done_flashing(info) else 3.5)
            draw_check(p, mid, 40, P.done_hi, P.on_done)
            self._center_pill(p, thumb, "Saved", P.done_hi)
        elif state == "bad":
            glow_border(p, thumb, 12, P.bad, 0.3, width=3.5)
            draw_cross(p, c, 30, P.bad)
            self._center_pill(p, thumb, "Problem", P.bad)
        elif state == "pick_on":
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(P.text), 4))
            p.drawRoundedRect(thumb.adjusted(2, 2, -2, -2), 11, 11)
            draw_check(p, QPointF(thumb.right() - 22, thumb.top() + 22), 16, P.text, P.bg_bottom)
        elif state == "pick_off":
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ink(150 if hover else 40), 2 if hover else 1.5))
            p.drawRoundedRect(thumb.adjusted(1, 1, -1, -1), 12, 12)
            p.setPen(QPen(QColor(255, 255, 255, 230), 2.5))
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawEllipse(QPointF(thumb.right() - 22, thumb.top() + 22), 14, 14)
        else:  # waiting / stopped
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ink(40), 1.5))
            p.drawRoundedRect(thumb.adjusted(1, 1, -1, -1), 12, 12)
            self._center_pill(p, thumb, "Stopped" if state == "stopped" else "Waiting", P.wait)

        kind = info.get("kind", "")
        if state in ("pick_on", "pick_off", "wait"):
            dur = self.loader.duration(key) if kind in ("video", "360") else None
            badge = format_duration(dur) if dur is not None else KIND_BADGE.get(kind, "")
            if badge and state != "wait":
                self._pill(p, badge, QPointF(thumb.right() - 8, thumb.bottom() - 8), "#0b0b0c", solid=False,
                           align_right=True)
            if pick and info.get("present"):
                self._pill(p, "✓ Saved", QPointF(thumb.left() + 8, thumb.bottom() - 8), P.done_hi)

        ty = thumb.bottom() + 9
        fm = QFontMetrics(self.f_name)
        p.setFont(self.f_name)
        p.setPen(QColor(P.text if not grey else P.muted))
        p.drawText(QRectF(x, ty, TW, fm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                   fm.elidedText(info.get("name", ""), Qt.ElideMiddle, TW))
        p.setFont(self.f_meta)
        p.setPen(QColor(P.muted))
        p.drawText(QRectF(x, ty + fm.height() + 2, TW, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{info.get('time', '')}  ·  {human_bytes(info.get('size', 0))}")
        p.restore()

    def _center_pill(self, p: QPainter, thumb: QRectF, text: str, color: str) -> None:
        fm = QFontMetrics(self.f_pill)
        w = fm.horizontalAdvance(text) + 18
        self._pill(p, text, QPointF(thumb.center().x() - w / 2, thumb.bottom() - 8), color)


class EasyClipGrid(QListWidget):
    """Wrapping grid sized to its content (the page scrolls). mode: 'live' or 'pick'."""

    picked_changed = Signal()

    def __init__(self, loader, mode: str = "live", parent=None):
        super().__init__(parent)
        self.mode = mode
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
        self.setObjectName("EasyClipGrid")
        self.setStyleSheet("QListWidget#EasyClipGrid { background: transparent; border: none; }")
        self.viewport().setAutoFillBackground(False)
        self.setItemDelegate(EasyClipDelegate(loader, self))
        if mode == "pick":
            self.viewport().setCursor(Qt.PointingHandCursor)
            self.itemClicked.connect(self._toggle)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._animate)

    # ---- content
    def add_clip(self, info: ClipInfo) -> QListWidgetItem:
        it = QListWidgetItem()
        it.setData(ROLE, info)
        it.setSizeHint(CELL)
        it.setToolTip(info.get("name", ""))
        self.addItem(it)
        return it

    def infos(self) -> list:
        return [self.item(i).data(ROLE) for i in range(self.count())]

    def refresh(self, it: QListWidgetItem) -> None:
        self.viewport().update(self.visualItemRect(it))
        self._kick()

    def refresh_all(self) -> None:
        self.viewport().update()
        self._kick()

    # ---- pick mode
    def _toggle(self, it: QListWidgetItem) -> None:
        info = it.data(ROLE)
        info["picked"] = not info.get("picked")
        self.refresh(it)
        self.picked_changed.emit()

    def set_all(self, on: bool, only_new: bool = False) -> None:
        for info in self.infos():
            info["picked"] = (not info.get("present")) if only_new else on
        self.viewport().update()
        self.picked_changed.emit()

    # ---- animation: runs only while a tile is copying, almost done, or just finished
    def _kick(self) -> None:
        if self.mode == "live" and not self._timer.isActive():
            self._timer.start()

    def _animate(self) -> None:
        moving = False
        for i in range(self.count()):
            it = self.item(i)
            info = it.data(ROLE)
            if tile_state(info) in ("busy", "almost") or (tile_state(info) == "done" and done_flashing(info)):
                moving = True
                self.viewport().update(self.visualItemRect(it))
        if not moving:
            self._timer.stop()

    # ---- layout
    def wheelEvent(self, e):  # noqa: N802 - let the page scroll
        e.ignore()

    def columns(self) -> int:
        return max(1, self.viewport().width() // CELL.width())

    def fit(self) -> None:
        rows = math.ceil(self.count() / self.columns()) if self.count() else 0
        h = rows * CELL.height() + 6
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self.fit()




class NowThumb(ThumbView):
    """The big 'Now' picture: the clip being copied, with a large rotating hourglass in a progress ring.
    Turns into a big green check when the transfer is done."""

    def __init__(self, w: int, h: int, parent=None):
        super().__init__(w, h, parent)
        self.state = ""  # "" | busy | almost | done
        self.frac = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self.update)

    def set_state(self, state: str, frac: float = 0.0) -> None:
        self.state, self.frac = state, frac
        if state in ("busy", "almost"):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if not self.state:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.setClipPath(path)
        p.fillRect(r, QColor(0, 0, 0, 100))
        p.setClipping(False)
        c = r.center()
        if self.state == "done":
            draw_check(p, c, 46, P.done_hi, P.on_done)
            return
        col = P.busy if self.state == "busy" else P.almost
        draw_ring(p, c, 50, self.frac, col, width=7)
        draw_hourglass(p, c, 58, col, hourglass_angle(), sand_level())
