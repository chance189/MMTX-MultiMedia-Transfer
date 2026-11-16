"""Building blocks for Easy mode.

Visual rules:
  * The button for the current step flashes red until you press it (about once a second,
    well under the 3 Hz photosensitivity limit). Pressed: it turns solid green with a check,
    the step tracker ticks that step off, and the next step opens. Nothing else flashes.
  * Green = done, amber = take care. Problems only appear in popups, with a cross icon.
  * Big type, calm navy background, illustrations instead of walls of text.
"""

from __future__ import annotations

import math
import random
from typing import Optional

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .icons import draw_check, draw_cross, draw_glyph

from .easy_clips import (
    done_flashing,
    draw_cover,
    draw_hourglass,
    flash,
    glow_border,
    grey_pixmap,
    hourglass_angle,
    pulse,
    sand_level,
)
from .palettes import P, ink


def paint_backdrop(p: QPainter, w: float, h: float) -> None:
    """The palette's background gradient with its soft glows. Static."""
    g = QLinearGradient(0, 0, 0, h)
    g.setColorAt(0, QColor(P.bg_top))
    g.setColorAt(1, QColor(P.bg_bottom))
    p.fillRect(QRectF(0, 0, w, h), g)
    for fx, fy, fr, rgb, alpha in P.glows:
        cx, cy, r = w * fx, h * fy, max(w, h) * fr
        col = QColor(*(int(v) for v in rgb.split(",")), alpha)
        rg = QRadialGradient(QPointF(cx, cy), r)
        rg.setColorAt(0, col)
        rg.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
        p.fillRect(QRectF(0, 0, w, h), rg)


def _mix(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    t = max(0.0, min(1.0, t))
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t), int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t))


def _rgba(text: str) -> QColor:
    """'rgba(r,g,b,a)' with a 0..1 -> QColor."""
    r, g, b, a = (x.strip() for x in text[text.index("(") + 1:text.rindex(")")].split(","))
    return QColor(int(r), int(g), int(b), int(float(a) * 255))


def _two(rect: QRectF, a: str, b: str, vertical: bool = False) -> QLinearGradient:
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft() if vertical else rect.topRight())
    g.setColorAt(0, QColor(a))
    g.setColorAt(1, QColor(b))
    return g


# ===================================================================== step tracker


class StepTracker(QWidget):
    """Three steps with labels. Done = green check, current = red, later = outline.
    Still, except for one short animation when a step completes: the check pops in,
    the connector fills green, and the next step turns red."""

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self.labels = labels
        self.states = ["todo"] * len(labels)
        self._prev = list(self.states)
        self._prog = 1.0
        self._skip = True
        self.setFixedHeight(92)
        self.setMinimumWidth(560)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(650)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._tick)

    def _tick(self, v) -> None:
        self._prog = float(v)
        self.update()

    def set_labels(self, labels: list[str]) -> None:
        if labels != self.labels:
            self._skip = True  # a different flow: jump straight to its states
        self.labels = labels
        self.update()

    def set_states(self, states: list[str]) -> None:
        states = list(states)
        if states == self.states:
            return
        rank = {"todo": 0, "active": 1, "done": 2}
        forward = any(rank[n] > rank[o] for o, n in zip(self.states, states))
        backward = any(rank[n] < rank[o] for o, n in zip(self.states, states))
        self._prev, self.states = self.states, states
        if self._skip or backward or not forward:
            self._skip = False
            self._anim.stop()
            self._prog = 1.0
            self._prev = list(states)
        else:
            self._anim.stop()
            self._prog = 0.0
            self._anim.start()
        self.update()

    def complete_current(self) -> None:
        """Tick off the current step and make the next one current (used the moment a button is pressed)."""
        st = list(self.states)
        if "active" in st:
            i = st.index("active")
            st[i] = "done"
            if i + 1 < len(st) and st[i + 1] == "todo":
                st[i + 1] = "active"
        self.set_states(st)

    def _circle(self, p: QPainter, i: int, st: str, c: QPointF, r: float, f: QFont) -> None:
        box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
        if st == "done":
            draw_check(p, c, r, P.done_hi, P.on_done)
            return
        if st == "active":
            g = QLinearGradient(box.topLeft(), box.bottomRight())
            g.setColorAt(0, QColor(P.todo_hi))
            g.setColorAt(1, QColor(P.todo))
            p.setPen(QPen(ink(50), 5))
            p.setBrush(g)
            p.drawEllipse(c, r, r)
            col = QColor("#ffffff")
        else:
            p.setPen(QPen(QColor(P.step_line), 2.5))
            p.setBrush(QColor(P.step_fill))
            p.drawEllipse(c, r - 1, r - 1)
            col = QColor(P.muted)
        f.setPointSizeF(16 if st == "active" else 15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(col)
        p.drawText(box, Qt.AlignCenter, str(i + 1))

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.labels)
        if len(self.states) != n:
            return
        w = self.width()
        xs = [w * (i + 0.5) / n for i in range(n)]
        cy, r = 28.0, 22.0
        k = self._prog
        for i in range(n - 1):
            x0, x1 = xs[i] + r + 10, xs[i + 1] - r - 10
            p.setPen(QPen(QColor(P.line), 4, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x0, cy), QPointF(x1, cy))
            was, now = self._prev[i] == "done", self.states[i] == "done"
            frac = 1.0 if (now and was) else (k if now else 0.0)
            if frac > 0:
                p.setPen(QPen(QColor(P.done_hi), 4, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(x0, cy), QPointF(x0 + (x1 - x0) * frac, cy))
        f = QFont(self.font())
        for i, (x, lab) in enumerate(zip(xs, self.labels)):
            c = QPointF(x, cy)
            old, new = self._prev[i], self.states[i]
            if old == new or k >= 1.0:
                self._circle(p, i, new, c, r, f)
            else:
                p.setOpacity(1.0 - k)
                self._circle(p, i, old, c, r, f)
                p.setOpacity(k)
                pop = 1.0 + 0.25 * math.sin(k * math.pi) if new == "done" else 1.0
                self._circle(p, i, new, c, r * pop, f)
                p.setOpacity(1.0)
            st = new
            f.setPointSizeF(12.5)
            f.setBold(st == "active")
            p.setFont(f)
            p.setPen(QColor({"done": P.done_hi, "active": P.text}.get(st, P.muted)))
            p.drawText(QRectF(x - 120, cy + r + 8, 240, 26), Qt.AlignCenter, lab)


# ===================================================================== illustrations


def _card_path(rect: QRectF) -> QPainterPath:
    """SD card outline with the clipped corner."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cut = w * 0.28
    path = QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w - 6, y)
    path.quadTo(x + w, y, x + w, y + 6)
    path.lineTo(x + w, y + h - 6)
    path.quadTo(x + w, y + h, x + w - 6, y + h)
    path.lineTo(x + 6, y + h)
    path.quadTo(x, y + h, x, y + h - 6)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


def draw_sd_card(p: QPainter, rect: QRectF, angle: float = 0.0) -> None:
    p.save()
    c = rect.center()
    p.translate(c)
    p.rotate(angle)
    p.translate(-c)
    path = _card_path(rect)
    g = QLinearGradient(rect.topLeft(), rect.bottomRight())
    g.setColorAt(0, QColor(P.art_card[0]))
    g.setColorAt(1, QColor(P.art_card[1]))
    p.setPen(QPen(ink(40), 1.2))
    p.setBrush(g)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 230))
    lab = QRectF(rect.x() + rect.width() * 0.14, rect.y() + rect.height() * 0.48, rect.width() * 0.72, rect.height() * 0.34)
    p.drawRoundedRect(lab, 3, 3)
    p.setBrush(QColor(255, 214, 102))
    for i in range(4):
        cx = rect.x() + rect.width() * (0.36 + i * 0.14)
        p.drawRoundedRect(QRectF(cx, rect.y() + 5, rect.width() * 0.08, rect.height() * 0.16), 1.5, 1.5)
    p.restore()


def draw_drive(p: QPainter, rect: QRectF, label: str, accent: str) -> None:
    p.setPen(QPen(QColor(P.art_line), 1.5))
    p.setBrush(_two(rect, P.art_body[0], P.art_body[1], vertical=True))
    p.drawRoundedRect(rect, 14, 14)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(accent))
    p.drawEllipse(QPointF(rect.right() - 20, rect.bottom() - 18), 5, 5)
    p.setBrush(ink(40))
    for i in range(3):
        p.drawRoundedRect(QRectF(rect.x() + 18, rect.bottom() - 26 + i * 0, rect.width() * 0.4 - i * 18, 5), 2.5, 2.5)
    f = QFont()
    f.setPointSizeF(10.5)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor(P.sub))
    p.drawText(QRectF(rect.x(), rect.y() + 14, rect.width(), 22), Qt.AlignCenter, label)


class Art(QWidget):
    """Illustration at the top of each Easy screen.

    insert   laptop with an SD card sliding into its slot (slow, smooth)
    search   card with a turning ring (only while reading a card)
    choose   a drive with auto-named day folders on it
    backup   working drive -> backup drive
    done     mint check; celebrate() adds a one-off confetti burst
    empty    card with a question mark
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(420, 210)
        self.kind = "insert"
        self.folder_names: list[str] = []
        self._t = 0.0
        self._confetti: list[list[float]] = []
        self._conf_t = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(2800)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)
        self._conf = QVariantAnimation(self)
        self._conf.setStartValue(0.0)
        self._conf.setEndValue(1.0)
        self._conf.setDuration(1800)
        self._conf.valueChanged.connect(self._conf_tick)

    def _tick(self, v) -> None:
        self._t = float(v)
        self.update()

    def _conf_tick(self, v) -> None:
        self._conf_t = float(v)
        self.update()

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        if kind in ("insert", "search"):
            if self._anim.state() != QVariantAnimation.Running:
                self._anim.start()
        else:
            self._anim.stop()
        self.update()

    def celebrate(self) -> None:
        rnd = random.Random()
        cols = [P.done_hi, P.busy, P.almost, P.todo_hi, "#facc15", "#f472b6"]
        self._confetti = [[rnd.uniform(-1, 1), rnd.uniform(-1.2, -0.3), rnd.uniform(0, 360),
                           rnd.uniform(-540, 540), cols[i % len(cols)], rnd.uniform(5, 9)] for i in range(70)]
        self._conf_t = 0.0
        self._conf.start()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = float(self.width()), float(self.height())
        k = self.kind
        if k == "insert":
            self._paint_insert(p, W, H)
        elif k == "search":
            draw_sd_card(p, QRectF(W / 2 - 36, H / 2 - 48, 72, 96))
            arc = QRectF(W / 2 - 82, H / 2 - 82, 164, 164)
            g = QConicalGradient(arc.center(), -self._t * 360)
            g.setColorAt(0, QColor(P.busy))
            g.setColorAt(0.25, QColor(P.busy_lo))
            g.setColorAt(0.26, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(QPen(g, 7, Qt.SolidLine, Qt.RoundCap))
            p.drawEllipse(arc)
        elif k == "choose":
            self._paint_choose(p, W, H)
        elif k == "backup":
            draw_drive(p, QRectF(W * 0.06, H * 0.25, W * 0.34, H * 0.55), "This computer", P.busy)
            draw_drive(p, QRectF(W * 0.60, H * 0.25, W * 0.34, H * 0.55), "Backup drive", P.done_hi)
            y = H * 0.52
            path = QPainterPath()
            path.moveTo(W * 0.43, y)
            path.lineTo(W * 0.57, y)
            path.moveTo(W * 0.53, y - 9)
            path.lineTo(W * 0.575, y)
            path.lineTo(W * 0.53, y + 9)
            p.setPen(QPen(QColor(P.sub), 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        elif k == "done":
            c = QPointF(W / 2, H / 2)
            rg = QRadialGradient(c, 110)
            glow = QColor(P.done_hi)
            glow.setAlpha(70)
            rg.setColorAt(0, glow)
            glow.setAlpha(0)
            rg.setColorAt(1, glow)
            p.fillRect(QRectF(0, 0, W, H), rg)
            draw_check(p, c, 62, P.done_hi, P.on_done)
        elif k == "empty":
            draw_sd_card(p, QRectF(W / 2 - 46, H / 2 - 60, 92, 120), -6)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(P.warn))
            p.drawEllipse(QPointF(W / 2 + 56, H / 2 - 50), 24, 24)
            f = QFont(self.font())
            f.setPointSizeF(20)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#1c1917"))
            p.drawText(QRectF(W / 2 + 32, H / 2 - 74, 48, 48), Qt.AlignCenter, "?")
        if self._confetti and self._conf_t < 1.0:
            t = self._conf_t
            ease = 1 - (1 - t) ** 2
            p.setPen(Qt.NoPen)
            for vx, vy, rot, spin, col, size in self._confetti:
                x = W / 2 + vx * 230 * ease
                y = H / 2 + vy * 160 * ease + 260 * t * t
                c = QColor(col)
                c.setAlpha(int(255 * (1 - t)))
                p.save()
                p.translate(x, y)
                p.rotate(rot + spin * t)
                p.setBrush(c)
                p.drawRoundedRect(QRectF(-size / 2, -size / 4, size, size / 2), 1.5, 1.5)
                p.restore()

    def _paint_insert(self, p: QPainter, W: float, H: float) -> None:
        scr = QRectF(W * 0.20, H * 0.08, W * 0.52, H * 0.60)
        p.setPen(QPen(QColor(P.art_line), 2))
        p.setBrush(_two(scr, P.art_body[0], P.art_body[1], vertical=True))
        p.drawRoundedRect(scr, 12, 12)
        inner = scr.adjusted(12, 12, -12, -12)
        p.setPen(Qt.NoPen)
        p.setBrush(ink(14))
        p.drawRoundedRect(inner, 6, 6)
        for i in range(3):
            p.setBrush(QColor(P.art_folders[2 - i]))
            p.drawRoundedRect(QRectF(inner.x() + 10 + i * (inner.width() - 20) / 3, inner.y() + 14,
                                     (inner.width() - 40) / 3, inner.height() * 0.45), 5, 5)
        base = QRectF(W * 0.12, scr.bottom() + 4, W * 0.68, 18)
        p.setBrush(_two(base, P.art_folders[1], P.art_body[0], vertical=True))
        p.setPen(QPen(QColor(P.art_line), 1.5))
        p.drawRoundedRect(base, 8, 8)
        slot = QRectF(base.right() - 2, base.y() + 5, 8, 8)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(P.art_slot))
        p.drawRoundedRect(slot, 2, 2)
        # card: glides toward the slot, pauses, fades, repeats
        t = self._t
        travel = min(1.0, t / 0.6)
        travel = 0.5 - 0.5 * math.cos(travel * math.pi)
        alpha = 1.0 if t < 0.8 else max(0.0, 1 - (t - 0.8) / 0.2)
        cw, ch = 52.0, 68.0
        x = W * 0.97 - cw - (W * 0.97 - cw - (slot.right() - 6)) * travel
        y = base.center().y() - ch * (1 - travel) * 0.9 - ch / 2 * travel - 6
        p.setOpacity(alpha)
        draw_sd_card(p, QRectF(x, y - ch / 2 + 6, cw, ch), angle=-90 * travel)
        p.setOpacity(1.0)

    def _paint_choose(self, p: QPainter, W: float, H: float) -> None:
        drive = QRectF(W * 0.08, H * 0.58, W * 0.84, H * 0.34)
        draw_drive(p, drive, "", P.done_hi)
        names = self.folder_names or ["2026_11_14_DailyRun", "2026_11_13_DailyRun", "2026_11_12_DailyRun"]
        f = QFont(self.font())
        f.setPointSizeF(9.5)
        f.setBold(True)
        for i, name in enumerate(names[:3][::-1]):
            j = 2 - i
            r = QRectF(W * 0.16 + j * 22, H * 0.06 + j * 26, W * 0.62, H * 0.40)
            p.setPen(QPen(ink(40), 1))
            col = QColor(P.art_folders[j])
            p.setBrush(col)
            tab = QPainterPath()
            tab.addRoundedRect(QRectF(r.x(), r.y() - 10, r.width() * 0.32, 20), 6, 6)
            tab.addRoundedRect(r, 10, 10)
            p.drawPath(tab.simplified())
            p.setFont(f)
            p.setPen(QColor("#ffffff" if P.dark or j == 0 else "#0f172a"))
            p.drawText(r.adjusted(14, 8, -10, 0), Qt.AlignLeft | Qt.AlignTop, name)


class ThumbFan(QWidget):
    """A fanned hand of thumbnails (cheap way to make 'here is what's new' feel exciting)."""

    def __init__(self, loader, parent=None):
        super().__init__(parent)
        self.loader = loader
        self.items: list[tuple[str, str]] = []  # (key, glyph)
        self.extra = 0
        self.setFixedSize(620, 210)

    def set_items(self, items: list[tuple[str, str]], extra: int) -> None:
        self.items = items[:5]
        self.extra = extra
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        n = len(self.items)
        if not n:
            return
        W, H = float(self.width()), float(self.height())
        cw, ch = 220.0, 124.0
        angles = {1: [0], 2: [-6, 6], 3: [-10, 0, 10], 4: [-14, -5, 5, 14], 5: [-16, -8, 0, 8, 16]}[n]
        offs = {1: [0], 2: [-70, 70], 3: [-120, 0, 120], 4: [-165, -55, 55, 165], 5: [-190, -95, 0, 95, 190]}[n]
        order = sorted(range(n), key=lambda i: -abs(i - (n - 1) / 2))  # middle card on top
        for i in order:
            (key, glyph), ang, dx = self.items[i], angles[i], offs[i]
            cx, cy = W / 2 + dx, H / 2 + abs(ang) * 1.6
            p.save()
            p.translate(cx, cy)
            p.rotate(ang)
            rect = QRectF(-cw / 2, -ch / 2, cw, ch)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRoundedRect(rect.translated(0, 8), 14, 14)
            path = QPainterPath()
            path.addRoundedRect(rect, 14, 14)
            p.setClipPath(path)
            pm = self.loader.pixmap(key) if key else None
            if pm is not None and not pm.isNull():
                pw, ph = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
                sc = max(cw / pw, ch / ph)
                sw, sh = cw / sc, ch / sc
                p.drawPixmap(rect, pm, QRectF((pw - sw) / 2, (ph - sh) / 2, sw, sh))
            else:
                p.fillRect(rect, _two(rect, P.placeholder[0], P.placeholder[1]))
                draw_glyph(p, glyph, QRectF(-20, -20, 40, 40), P.muted, 1.6)
            p.setClipping(False)
            p.setPen(QPen(ink(60), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(rect, 14, 14)
            p.restore()
        if self.extra > 0:
            r = QRectF(W - 96, H - 46, 84, 36)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(P.chip_bg) if not P.chip_bg.startswith("rgba") else _rgba(P.chip_bg))
            p.drawRoundedRect(r, 18, 18)
            f = QFont(self.font())
            f.setPointSizeF(12)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor("#ffffff"))
            p.drawText(r, Qt.AlignCenter, f"+{self.extra}")


# ===================================================================== the step button


class Beacon(QWidget):
    """The button for the current step.

    todo: flashes red (fill, halo and a ripple ring, about once a second) until pressed.
    done: confirm() turns it solid green with a check and one green burst; it no longer reacts to clicks.
    disabled: grey and still.
    The QPushButton itself is transparent (see theme.py); this widget paints its fill."""

    clicked = Signal()
    PERIOD_MS = 1100  # ~0.9 flashes per second

    def __init__(self, text: str, big: bool = False, parent=None):
        super().__init__(parent)
        self.state = "todo"
        self._t = 0.0
        self._pop = 1.0
        self.btn = QPushButton(text)
        self.btn.setObjectName("EasyGo" if big else "EasyPrimary")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._on_click)
        self.btn.installEventFilter(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 28, 36, 28)
        lay.addWidget(self.btn, 0, Qt.AlignHCenter)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(self.PERIOD_MS)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Linear)
        self._anim.valueChanged.connect(self._tick)
        self._anim.start()
        self._pop_anim = QVariantAnimation(self)
        self._pop_anim.setStartValue(0.0)
        self._pop_anim.setEndValue(1.0)
        self._pop_anim.setDuration(650)
        self._pop_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._pop_anim.valueChanged.connect(self._pop_tick)

    def _tick(self, v) -> None:
        self._t = float(v)
        self.update()

    def _pop_tick(self, v) -> None:
        self._pop = float(v)
        self.update()

    def eventFilter(self, obj, ev):  # noqa: N802 - repaint the fill on hover/press
        if obj is self.btn and ev.type() in (QEvent.Enter, QEvent.Leave, QEvent.MouseButtonPress,
                                             QEvent.MouseButtonRelease):
            self.update()
        return False

    def _on_click(self) -> None:
        if self.state == "todo":
            self.clicked.emit()

    def set_text(self, t: str) -> None:
        self.btn.setText(t)

    def reset(self) -> None:
        """Back to red and flashing (a fresh step)."""
        self.state = "todo"
        self._pop_anim.stop()
        self._pop = 1.0
        self.btn.setMinimumWidth(0)
        self.btn.setCursor(Qt.PointingHandCursor)
        if self.isEnabled() and self._anim.state() != QVariantAnimation.Running:
            self._anim.start()
        self.update()

    def confirm(self, text: str = "") -> None:
        """The step is done: solid green with a check, one green burst, no more flashing."""
        self.state = "done"
        self._anim.stop()
        self.btn.setMinimumWidth(self.btn.width())  # same size, so nothing jumps
        if text:
            self.btn.setText("\u2713  " + text)
        self.btn.setCursor(Qt.ArrowCursor)
        self._pop = 0.0
        self._pop_anim.start()
        self.update()

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        self.btn.setEnabled(on)
        if on and self.state == "todo" and self._anim.state() != QVariantAnimation.Running:
            self._anim.start()
        elif not on:
            self._anim.stop()
        self.update()

    def flash_level(self) -> float:
        """0 = dim, 1 = bright. A sharpened sine, so it reads as a flash rather than a slow breath."""
        s = 0.5 - 0.5 * math.cos(self._t * 2 * math.pi)
        for _ in range(2):
            s = s * s * (3 - 2 * s)
        return s

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QRectF(self.btn.geometry())
        rad = g.height() / 2
        p.setPen(Qt.NoPen)
        if not self.isEnabled():
            p.setBrush(ink(24))
            p.drawRoundedRect(g, rad, rad)
            return
        hover, down = self.btn.underMouse(), self.btn.isDown()
        if self.state == "done":
            q = self._pop
            if q < 1.0:  # one green ring bursting outward
                spread = 4 + 34 * q
                ring = QColor(P.done_hi)
                ring.setAlpha(int(220 * (1 - q) ** 1.4))
                p.setPen(QPen(ring, 3))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(g.adjusted(-spread, -spread, spread, spread), rad + spread, rad + spread)
            halo = QColor(P.done_hi)
            for k in range(12, 0, -1):
                halo.setAlpha(int(70 * (1 - k / 13) ** 2))
                p.setPen(QPen(halo, 2))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(g.adjusted(-k, -k, k, k), rad + k, rad + k)
            fill = QLinearGradient(g.topLeft(), g.topRight())
            fill.setColorAt(0, QColor(P.done_hi))
            fill.setColorAt(1, QColor(P.done))
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(g, rad, rad)
            return
        f = self.flash_level()
        if hover:
            f = max(f, 0.85)  # under the mouse it stays lit, so it's easy to see what you're about to press
        halo = QColor(P.todo_hi)
        for k in range(18, 0, -1):
            halo.setAlpha(int((15 + 120 * f) * (1 - k / 19) ** 2))
            p.setPen(QPen(halo, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(g.adjusted(-k, -k, k, k), rad + k, rad + k)
        ripple = self._t  # ring leaves the button at each flash and fades out
        spread = 4 + 26 * ripple
        ring = QColor(P.todo_hi)
        ring.setAlpha(int(170 * (1 - ripple) ** 1.6))
        p.setPen(QPen(ring, 2.4))
        p.drawRoundedRect(g.adjusted(-spread, -spread, spread, spread), rad + spread, rad + spread)
        top = _mix(P.todo_lo, P.todo_hi, f)
        bot = _mix(P.todo_lo, P.todo, f)
        if down:
            top, bot = top.darker(120), bot.darker(120)
        fill = QLinearGradient(g.topLeft(), g.bottomLeft())
        fill.setColorAt(0, top)
        fill.setColorAt(1, bot)
        p.setPen(QPen(QColor(255, 255, 255, int(40 + 60 * f)), 2))
        p.setBrush(fill)
        p.drawRoundedRect(g.adjusted(1, 1, -1, -1), rad - 1, rad - 1)


# ===================================================================== live views


class RingProgress(QWidget):
    """Overall progress. Blue while copying, orange from 90%, green when done. No flashing."""

    def __init__(self, size: int = 210, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.value = 0.0
        self.caption = ""
        self.done = False

    def set_value(self, v: float, caption: str = "") -> None:
        self.value = max(0.0, min(1.0, v))
        self.caption = caption
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = float(self.width())
        w = 16.0
        r = QRectF(w, w, s - 2 * w, s - 2 * w)
        p.setPen(QPen(QColor(P.ring_track), w, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(r)
        if self.value > 0:
            col = P.done_hi if self.done else (P.almost if self.value >= 0.9 else P.busy)
            pen = QPen(QColor(col), w)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 90 * 16, int(-self.value * 360 * 16))
        f = QFont(self.font())
        f.setPointSizeF(s * 0.15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(P.text))
        p.drawText(QRectF(0, s * 0.27, s, s * 0.30), Qt.AlignCenter, f"{int(self.value * 100)}%")
        f.setPointSizeF(12.5)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(P.sub))
        p.drawText(QRectF(0, s * 0.57, s, 26), Qt.AlignCenter, self.caption)


class FolderLiveList(QWidget):
    """Day folder rows for Easy "Move old videos".

    live: waiting (grey), copying (blue pulse + rotating hourglass), almost done (orange flash),
          done (big green check, green outline flashing for a few seconds), problem (red).
    pick: tap a row to add or remove it (chosen = bold outline + filled check, not chosen = grey).
    Rows: {key, name, meta, state, text, frac, done_at, picked}.
    """

    ROW = 82
    picked_changed = Signal()

    def __init__(self, loader, mode: str = "live", parent=None):
        super().__init__(parent)
        self.loader = loader
        self.mode = mode
        self.rows: list[dict] = []
        self._grey: dict = {}
        self._hover = -1
        self.setMinimumHeight(10)
        self.setMouseTracking(True)
        if mode == "pick":
            self.setCursor(Qt.PointingHandCursor)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._animate)

    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows
        self.setFixedHeight(max(10, self.ROW * len(rows)))
        self.refresh()

    def refresh(self) -> None:
        self.update()
        if self.mode == "live" and not self._timer.isActive():
            self._timer.start()

    def _moving(self, row: dict) -> bool:
        st = row.get("state", "")
        return st in ("busy", "almost") or (st == "done" and done_flashing(row))

    def _animate(self) -> None:
        if any(self._moving(r) for r in self.rows):
            self.update()
        else:
            self._timer.stop()

    def set_all(self, on: bool) -> None:
        for r in self.rows:
            r["picked"] = on
        self.update()
        self.picked_changed.emit()

    def mouseMoveEvent(self, e):  # noqa: N802
        i = int(e.position().y() // self.ROW)
        if i != self._hover:
            self._hover = i
            self.update()

    def leaveEvent(self, _e):  # noqa: N802
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if self.mode != "pick" or e.button() != Qt.LeftButton:
            return
        i = int(e.position().y() // self.ROW)
        if 0 <= i < len(self.rows):
            self.rows[i]["picked"] = not self.rows[i].get("picked")
            self.update()
            self.picked_changed.emit()

    def paintEvent(self, _e):  # noqa: N802, C901
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        f = QFont(self.font())
        pick = self.mode == "pick"
        for i, row in enumerate(self.rows):
            y = i * float(self.ROW)
            card = QRectF(2, y + 5, self.width() - 4, self.ROW - 10)
            st = ("pick_on" if row.get("picked") else "pick_off") if pick else row.get("state", "wait")
            grey = st in ("wait", "pick_off", "stopped")
            # card + outline
            p.setPen(Qt.NoPen)
            p.setBrush(ink(10))
            p.drawRoundedRect(card, 14, 14)
            if st == "busy":
                glow_border(p, card, 14, P.busy, pulse(), width=3)
            elif st == "almost":
                glow_border(p, card, 14, P.almost, flash(), width=4)
            elif st == "done":
                glow_border(p, card, 14, P.done_hi, flash(0.6) if done_flashing(row) else 0.4,
                            width=4 if done_flashing(row) else 3)
            elif st == "bad":
                glow_border(p, card, 14, P.bad, 0.3, width=3)
            elif st == "pick_on":
                p.setPen(QPen(QColor(P.text), 3.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(card.adjusted(1.5, 1.5, -1.5, -1.5), 13, 13)
            else:
                p.setPen(QPen(ink(150 if (pick and i == self._hover) else 28), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(card, 14, 14)
            x0 = card.x() + 12
            if pick:  # selection circle at the left
                cc = QPointF(x0 + 16, card.center().y())
                if st == "pick_on":
                    draw_check(p, cc, 16, P.text, P.bg_bottom)
                else:
                    p.setPen(QPen(ink(170), 2.5))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(cc, 14, 14)
                x0 += 44
            # thumbnail
            th = QRectF(x0, card.y() + 6, 106, card.height() - 12)
            path = QPainterPath()
            path.addRoundedRect(th, 8, 8)
            p.save()
            p.setClipPath(path)
            pm = self.loader.pixmap(row["key"]) if row.get("key") else None
            if pm is not None and not pm.isNull():
                draw_cover(p, th, grey_pixmap(pm, self._grey, row["key"]) if grey else pm)
            else:
                p.fillRect(th, QColor(P.placeholder[0]))
            if grey:
                p.fillRect(th, QColor(0, 0, 0, 90))
            elif st in ("busy", "almost", "done", "bad"):
                p.fillRect(th, QColor(0, 0, 0, 110))
            p.restore()
            tc = th.center()
            if st in ("busy", "almost"):
                draw_hourglass(p, tc, 34, P.busy if st == "busy" else P.almost, hourglass_angle(), sand_level())
            elif st == "done":
                draw_check(p, tc, 22, P.done_hi, P.on_done)
            elif st == "bad":
                draw_cross(p, tc, 20, P.bad)
            # name + meta
            tx = th.right() + 16
            f.setPointSizeF(14)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(P.muted if grey else P.text))
            p.drawText(QRectF(tx, card.y() + 8, card.width() * 0.5, 30), Qt.AlignLeft | Qt.AlignVCenter, row["name"])
            f.setPointSizeF(11.5)
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(P.muted))
            p.drawText(QRectF(tx, card.y() + 36, card.width() * 0.5, 24), Qt.AlignLeft | Qt.AlignVCenter,
                       row.get("meta", ""))
            # status pill on the right
            text = row.get("text", "")
            if not text:
                continue
            col = QColor({"busy": P.busy, "almost": P.almost, "done": P.done_hi, "bad": P.bad,
                          "kept": P.warn}.get(st, P.wait))
            f.setPointSizeF(12)
            f.setBold(True)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(text) + 32
            pill = QRectF(card.right() - tw - 14, card.center().y() - 17, tw, 34)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(pill, 17, 17)
            lum = col.red() * 0.299 + col.green() * 0.587 + col.blue() * 0.114
            p.setPen(QColor("#0b0b0c" if lum > 150 else "#ffffff"))
            p.drawText(pill, Qt.AlignCenter, text)


# ===================================================================== custom selection: delete toggle


class ToggleCard(QFrame):
    """A whole card that toggles an option when clicked, with a big switch on the left.
    On = red switch and red outline (it deletes things)."""

    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PickDelete")
        self.setCursor(Qt.PointingHandCursor)
        self._on = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(18)
        self.switch = _Switch(self)
        lay.addWidget(self.switch, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.head = QLabel("")
        self.head.setObjectName("PickDeleteHead")
        self.head.setWordWrap(True)
        self.text = QLabel("")
        self.text.setObjectName("PickDeleteText")
        self.text.setWordWrap(True)
        col.addWidget(self.head)
        col.addWidget(self.text)
        lay.addLayout(col, 1)

    def isChecked(self) -> bool:  # noqa: N802
        return self._on

    def setChecked(self, on: bool) -> None:  # noqa: N802
        self._on = bool(on)
        self.setProperty("on", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)
        self.switch.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self.isEnabled():
            self.setChecked(not self._on)
            self.toggled.emit(self._on)


class _Switch(QWidget):
    def __init__(self, card: ToggleCard):
        super().__init__(card)
        self.card = card
        self.setFixedSize(72, 40)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.card.isChecked()
        r = QRectF(1, 1, 70, 38)
        p.setPen(Qt.NoPen if on else QPen(ink(90), 2))
        p.setBrush(QColor(P.todo) if on else ink(20))
        p.drawRoundedRect(r, 19, 19)
        knob = QPointF(r.right() - 19 if on else r.left() + 19, r.center().y())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffffff") if on else ink(170))
        p.drawEllipse(knob, 14, 14)
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff") if on else QColor(P.muted))
        p.drawText(QRectF(r.left() + 6, r.top(), 30, r.height()) if on else QRectF(r.right() - 36, r.top(), 30, r.height()),
                   Qt.AlignCenter, "ON" if on else "OFF")


# ===================================================================== palettes


class PalettePreview(QWidget):
    """A miniature Easy screen in a given palette (not the current one)."""

    def __init__(self, pal, parent=None):
        super().__init__(parent)
        self.pal = pal
        self.setFixedSize(300, 150)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):  # noqa: N802
        pal = self.pal
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        R = QRectF(0, 0, 300, 150)
        clip = QPainterPath()
        clip.addRoundedRect(R, 12, 12)
        p.setClipPath(clip)
        p.fillRect(R, _two(R, pal.bg_top, pal.bg_bottom, vertical=True))
        hero = R.adjusted(14, 30, -14, -10)
        hb = pal.hero_bg
        p.setBrush(_rgba(hb) if hb.startswith("rgba") else QColor(hb))
        ir, ig, ib = (int(v) for v in pal.ink.split(","))
        p.setPen(QPen(QColor(ir, ig, ib, 60), 1.5))
        p.drawRoundedRect(hero, 10, 10)
        # tracker: done, current
        draw_check(p, QPointF(120, 15), 8, pal.done_hi, pal.on_done)
        p.setPen(QPen(QColor(pal.done_hi), 2.5))
        p.drawLine(QPointF(131, 15), QPointF(169, 15))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.todo))
        p.drawEllipse(QPointF(180, 15), 8, 8)
        # title + text lines
        p.setBrush(QColor(pal.text))
        p.drawRoundedRect(QRectF(hero.x() + 16, hero.y() + 12, 120, 9), 4, 4)
        p.setBrush(QColor(pal.muted))
        p.drawRoundedRect(QRectF(hero.x() + 16, hero.y() + 26, 84, 6), 3, 3)
        # four tiles: waiting, copying, almost, done
        x = hero.x() + 16
        for kind in ("wait", "busy", "almost", "done"):
            t = QRectF(x, hero.y() + 42, 54, 32)
            p.setBrush(QColor(pal.placeholder[0] if kind == "wait" else "#26262b"))
            col = {"wait": pal.wait, "busy": pal.busy, "almost": pal.almost, "done": pal.done_hi}[kind]
            p.setPen(QPen(QColor(col), 2.5 if kind != "wait" else 1.2))
            p.drawRoundedRect(t, 6, 6)
            if kind == "done":
                draw_check(p, t.center(), 9, pal.done_hi, pal.on_done)
            elif kind in ("busy", "almost"):
                draw_hourglass(p, t.center(), 16, col, 0.0, 0.5)
            x += 62
        # the red step button
        b = QRectF(hero.center().x() - 50, hero.bottom() - 26, 100, 20)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(pal.todo))
        p.drawRoundedRect(b, 10, 10)
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(b, Qt.AlignCenter, "Start")


class PaletteDialog(QDialog):
    """Pick the Easy colors. Each click applies right away; Done closes."""

    def __init__(self, parent, current: str, on_pick):
        from .palettes import PALETTES

        super().__init__(parent)
        self.setObjectName("EasyDialog")
        self.setWindowTitle("Colors")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(36, 28, 36, 24)
        lay.setSpacing(14)
        t = QLabel("Choose colors")
        t.setObjectName("EasyDialogTitle")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        m = QLabel("The colors of the screens change. What they mean stays the same: red still to do, "
                   "blue copying, orange almost done, green done.")
        m.setObjectName("EasyDialogText")
        m.setWordWrap(True)
        m.setAlignment(Qt.AlignCenter)
        lay.addWidget(m)
        from PySide6.QtWidgets import QButtonGroup, QGridLayout

        grid = QGridLayout()
        grid.setSpacing(16)
        self.group = QButtonGroup(self)
        self.cards: dict[str, QPushButton] = {}
        for i, (k, pal) in enumerate(PALETTES.items()):
            b = QPushButton()
            b.setObjectName("SwatchCard")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(332, 270)
            bl = QVBoxLayout(b)
            bl.setContentsMargins(14, 14, 14, 12)
            bl.setSpacing(6)
            bl.addWidget(PalettePreview(pal), 0, Qt.AlignHCenter)
            bl.addSpacing(6)
            n = QLabel(pal.name)
            n.setObjectName("HowHead")
            n.setAttribute(Qt.WA_TransparentForMouseEvents)
            d = QLabel(pal.blurb)
            d.setObjectName("HowText")
            d.setWordWrap(True)
            d.setAttribute(Qt.WA_TransparentForMouseEvents)
            bl.addWidget(n)
            bl.addWidget(d)
            bl.addStretch(1)
            b.setChecked(k == current)
            b.clicked.connect(lambda _c=False, key=k: on_pick(key))
            self.group.addButton(b)
            grid.addWidget(b, i // 2, i % 2)
            self.cards[k] = b
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.addStretch(1)
        done = QPushButton("Done")
        done.setObjectName("EasyDialogYes")
        done.setCursor(Qt.PointingHandCursor)
        done.setMinimumWidth(170)
        done.clicked.connect(self.accept)
        row.addWidget(done)
        row.addStretch(1)
        lay.addLayout(row)


# ===================================================================== dialogs


class _BigIcon(QWidget):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(96, 96)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(48, 48)
        if self.kind == "ok":
            draw_check(p, c, 42, P.done_hi, P.on_done)
            return
        if self.kind == "bad":
            draw_cross(p, c, 42, P.bad)
            return
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(P.warn) if self.kind == "warn" else QColor(P.btn_bg))
        p.drawEllipse(c, 42, 42)
        f = QFont(self.font())
        f.setPointSizeF(32)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#1c1917" if self.kind == "warn" else P.btn_fg))
        p.drawText(QRectF(0, 0, 96, 96), Qt.AlignCenter, {"question": "?", "warn": "!"}.get(self.kind, "i"))


class EasyDialog(QDialog):
    """Clear, plain-language popup. ask() returns the index of the clicked button (or -1)."""

    def __init__(self, parent, kind: str, title: str, message: str, buttons: list[str],
                 primary: int = 0, details: str = ""):
        super().__init__(parent)
        self.setObjectName("EasyDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(620)
        self.choice = -1
        self.kind = kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 32, 40, 28)
        lay.setSpacing(14)
        lay.addWidget(_BigIcon(kind), 0, Qt.AlignHCenter)
        t = QLabel(title)
        t.setObjectName("EasyDialogTitle")
        t.setProperty("tone", kind)
        t.setAlignment(Qt.AlignCenter)
        t.setWordWrap(True)
        lay.addWidget(t)
        m = QLabel(message)
        m.setObjectName("EasyDialogText")
        m.setAlignment(Qt.AlignCenter)
        m.setWordWrap(True)
        m.setTextFormat(Qt.RichText)
        lay.addWidget(m)
        self.details_box: Optional[QPlainTextEdit] = None
        if details:
            self.details_box = QPlainTextEdit(details)
            self.details_box.setObjectName("EasyDetailsText")
            self.details_box.setReadOnly(True)
            self.details_box.setMaximumHeight(150)
            self.details_box.setVisible(False)
            more = QPushButton("Show technical details")
            more.setObjectName("EasyLink")
            more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(lambda: (self.details_box.setVisible(not self.details_box.isVisible()),
                                          self.adjustSize()))
            lay.addWidget(more, 0, Qt.AlignHCenter)
            lay.addWidget(self.details_box)
        lay.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(14)
        row.addStretch(1)
        self.buttons: list[QPushButton] = []
        for i, text in enumerate(buttons):
            b = QPushButton(text)
            b.setObjectName("EasyDialogYes" if i == primary else "EasyDialogNo")
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(170)
            b.clicked.connect(lambda _c=False, idx=i: self._pick(idx))
            if i == primary:
                b.setDefault(True)
            row.addWidget(b)
            self.buttons.append(b)
        row.addStretch(1)
        lay.addLayout(row)

    def _pick(self, idx: int) -> None:
        self.choice = idx
        self.accept()

    def ask(self) -> int:
        self.exec()
        return self.choice


class HelpDialog(QDialog):
    """Questions people actually have. Click a question to open its answer."""

    def __init__(self, parent, items: list[tuple[str, str, list[tuple[str, object]]]], on_tour=None):
        super().__init__(parent)
        self.setObjectName("EasyDialog")
        self.setWindowTitle("Help")
        self.setMinimumSize(720, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 28, 34, 22)
        lay.setSpacing(10)
        t = QLabel("Help")
        t.setObjectName("EasyDialogTitle")
        lay.addWidget(t)
        intro = QLabel("Common questions. Click one to see the answer.")
        intro.setObjectName("EasyDialogText")
        lay.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setObjectName("HelpScroll")
        body = QWidget()
        body.setObjectName("HelpBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 6, 8, 6)
        bl.setSpacing(10)
        self.answers: list[QWidget] = []
        self.heads: list[QPushButton] = []
        for q, a, actions in items:
            card = QFrame()
            card.setObjectName("HelpItem")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(18, 10, 18, 12)
            cl.setSpacing(8)
            head = QPushButton(f"＋   {q}")
            head.setObjectName("HelpQ")
            head.setCursor(Qt.PointingHandCursor)
            cl.addWidget(head)
            ans = QWidget()
            al = QVBoxLayout(ans)
            al.setContentsMargins(36, 0, 0, 4)
            al.setSpacing(10)
            txt = QLabel(a)
            txt.setObjectName("HelpA")
            txt.setWordWrap(True)
            txt.setTextFormat(Qt.RichText)
            al.addWidget(txt)
            if actions:
                ar = QHBoxLayout()
                ar.setSpacing(10)
                for label_, fn in actions:
                    b = QPushButton(label_)
                    b.setObjectName("EasyDialogNo")
                    b.setCursor(Qt.PointingHandCursor)
                    b.clicked.connect(lambda _c=False, f=fn: f())
                    ar.addWidget(b)
                ar.addStretch(1)
                al.addLayout(ar)
            ans.setVisible(False)
            cl.addWidget(ans)
            head.clicked.connect(lambda _c=False, h=head, w=ans, qq=q: self._toggle(h, w, qq))
            self.answers.append(ans)
            self.heads.append(head)
            bl.addWidget(card)
        bl.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        row = QHBoxLayout()
        if on_tour:
            tour = QPushButton("Show me around the screen")
            tour.setObjectName("EasyDialogNo")
            tour.setCursor(Qt.PointingHandCursor)
            tour.clicked.connect(lambda: (self.accept(), on_tour()))
            row.addWidget(tour)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("EasyDialogYes")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _toggle(self, head: QPushButton, ans: QWidget, q: str) -> None:
        on = not ans.isVisible()
        ans.setVisible(on)
        head.setText(f"{'－' if on else '＋'}   {q}")

    def open_item(self, i: int) -> None:
        if 0 <= i < len(self.heads):
            self.heads[i].click()


class CoachOverlay(QWidget):
    """Dims the page except one widget and explains it in a card. Back / Next / Skip. Static highlight."""

    finished = Signal()

    def __init__(self, page: QWidget, steps: list[tuple[QWidget, str, str]]):
        super().__init__(page)
        self.page = page
        self.steps = steps
        self.i = 0
        self._hole = QRectF()
        self.setGeometry(page.rect())
        page.installEventFilter(self)

        self.bubble = QFrame(self)
        self.bubble.setObjectName("Bubble")
        b = QVBoxLayout(self.bubble)
        b.setContentsMargins(24, 20, 24, 18)
        b.setSpacing(8)
        self.count = QLabel()
        self.count.setObjectName("BubbleCount")
        b.addWidget(self.count)
        self.btitle = QLabel()
        self.btitle.setObjectName("BubbleTitle")
        self.btitle.setWordWrap(True)
        b.addWidget(self.btitle)
        self.btext = QLabel()
        self.btext.setObjectName("BubbleText")
        self.btext.setWordWrap(True)
        b.addWidget(self.btext)
        row = QHBoxLayout()
        self.skip = QPushButton("Skip tour")
        self.skip.setObjectName("BubbleLink")
        self.skip.setCursor(Qt.PointingHandCursor)
        self.skip.clicked.connect(self.close_tour)
        row.addWidget(self.skip)
        row.addStretch(1)
        self.back = QPushButton("Back")
        self.back.setObjectName("BubbleBack")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(self.go_back)
        row.addWidget(self.back)
        self.next = QPushButton("Next")
        self.next.setObjectName("BubbleNext")
        self.next.setCursor(Qt.PointingHandCursor)
        self.next.clicked.connect(self.advance)
        row.addWidget(self.next)
        b.addLayout(row)
        self._show_step()
        self.show()
        self.raise_()

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.page and ev.type() == QEvent.Resize:
            self.setGeometry(self.page.rect())
            QTimer.singleShot(0, self._place)
        return False

    def _show_step(self) -> None:
        _t, title, text = self.steps[self.i]
        self.count.setText(f"{self.i + 1} of {len(self.steps)}")
        self.btitle.setText(title)
        self.btext.setText(text)
        self.back.setVisible(self.i > 0)
        self.next.setText("Done" if self.i == len(self.steps) - 1 else "Next")
        self._place()

    def _place(self) -> None:
        target = self.steps[self.i][0]
        tl = target.mapTo(self.page, target.rect().topLeft())
        self._hole = QRectF(tl.x(), tl.y(), target.width(), target.height()).adjusted(-8, -8, 8, 8)
        bl = self.bubble.layout()
        bl.activate()
        bw = 440
        bh = bl.totalHeightForWidth(bw) if bl.hasHeightForWidth() else self.bubble.sizeHint().height()
        self.bubble.setFixedSize(bw, bh)
        x = min(max(16.0, self._hole.center().x() - bw / 2), self.width() - bw - 16)
        y = self._hole.bottom() + 18
        if y + bh > self.height() - 16:
            y = self._hole.top() - bh - 18
        if y < 16:
            y = max(16.0, self._hole.bottom() - bh - 24)
        self.bubble.move(int(x), int(y))
        self.update()

    def advance(self) -> None:
        if self.i < len(self.steps) - 1:
            self.i += 1
            self._show_step()
        else:
            self.close_tour()

    def go_back(self) -> None:
        if self.i > 0:
            self.i -= 1
            self._show_step()

    def close_tour(self) -> None:
        self.page.removeEventFilter(self)
        self.hide()
        self.deleteLater()
        self.finished.emit()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        path.addRect(QRectF(self.rect()))
        path.addRoundedRect(self._hole, 20, 20)
        p.fillPath(path, QColor(0, 0, 0, 200))
        p.setPen(QPen(QColor("#ffffff"), 3))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(self._hole, 20, 20)

    def mousePressEvent(self, e):  # noqa: N802 - the page underneath stays untouchable during the tour
        e.accept()

