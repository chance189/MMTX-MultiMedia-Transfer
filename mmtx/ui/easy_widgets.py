"""Building blocks for Easy mode.

Design rules (for poor eyesight and zero computer knowledge):
  * one instruction on screen at a time, in big plain words
  * color tells you what to do: YELLOW = do this now, GREEN = go / done, RED = stop / problem
  * the one thing to press glows and has a bouncing arrow pointing at it
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from .icons import draw_check, draw_cross, draw_glyph

YELLOW = "#FFD400"
GREEN = "#22C55E"
RED = "#EF4444"
INK = "#0b0d12"
GREY = "#3a4152"


def _loop_anim(owner, ms: int, on_value) -> QVariantAnimation:
    a = QVariantAnimation(owner)
    a.setStartValue(0.0)
    a.setEndValue(1.0)
    a.setDuration(ms)
    a.setLoopCount(-1)
    a.valueChanged.connect(on_value)
    return a


class StepTracker(QWidget):
    """1 -- 2 -- 3 with labels. Green check = done, yellow pulsing = now, grey = later."""

    def __init__(self, labels: list[str], parent=None):
        super().__init__(parent)
        self.labels = labels
        self.states = ["todo"] * len(labels)
        self.setFixedHeight(104)
        self.setMinimumWidth(520)
        self._phase = 0.0
        self._anim = _loop_anim(self, 1400, self._tick)
        self._anim.start()

    def _tick(self, v) -> None:
        self._phase = float(v)
        self.update()

    def set_labels(self, labels: list[str]) -> None:
        self.labels = labels
        self.update()

    def set_states(self, states: list[str]) -> None:
        self.states = states
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = len(self.labels)
        w = self.width()
        xs = [w * (i + 0.5) / n for i in range(n)]
        cy, r = 34.0, 26.0
        for i in range(n - 1):
            col = GREEN if self.states[i] == "done" else GREY
            p.setPen(QPen(QColor(col), 6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(xs[i] + r + 8, cy), QPointF(xs[i + 1] - r - 8, cy))
        f = QFont(self.font())
        for i, (x, lab, st) in enumerate(zip(xs, self.labels, self.states)):
            c = QPointF(x, cy)
            if st == "done":
                draw_check(p, c, r, GREEN, INK)
            elif st == "active":
                pulse = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
                for k in range(10, 0, -1):
                    p.setPen(QPen(QColor(255, 212, 0, int((60 + 120 * pulse) * (1 - k / 11) ** 2)), 2))
                    p.setBrush(Qt.NoBrush)
                    p.drawEllipse(c, r + k, r + k)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(YELLOW))
                p.drawEllipse(c, r, r)
                f.setPointSizeF(19)
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor(INK))
                p.drawText(QRectF(x - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, str(i + 1))
            else:
                p.setPen(QPen(QColor(GREY), 3))
                p.setBrush(QColor("#141821"))
                p.drawEllipse(c, r - 1, r - 1)
                f.setPointSizeF(18)
                f.setBold(True)
                p.setFont(f)
                p.setPen(QColor("#7d879b"))
                p.drawText(QRectF(x - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, str(i + 1))
            f.setPointSizeF(14)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor({"done": GREEN, "active": YELLOW}.get(st, "#7d879b")))
            p.drawText(QRectF(x - 110, cy + r + 8, 220, 30), Qt.AlignCenter, lab)


class Art(QWidget):
    """Big picture at the top of each screen. card_in is animated (card sliding into a slot)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 170)
        self.kind = "card_in"
        self._phase = 0.0
        self._anim = _loop_anim(self, 1600, self._tick)
        self._anim.start()

    def _tick(self, v) -> None:
        self._phase = float(v)
        if self.kind in ("card_in", "search"):
            self.update()

    def set_kind(self, kind: str) -> None:
        self.kind = kind
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        if self.kind == "card_in":
            # slot
            slot = QRectF(W / 2 - 70, H - 46, 140, 22)
            p.setPen(QPen(QColor("#cfd6e4"), 4))
            p.setBrush(QColor("#141821"))
            p.drawRoundedRect(slot, 8, 8)
            # card moving down into the slot
            t = self._phase
            y = 6 + 40 * (t if t < 0.75 else 0.75)
            p.setOpacity(1.0 if t < 0.85 else max(0.0, 1 - (t - 0.85) / 0.15))
            draw_glyph(p, "card", QRectF(W / 2 - 42, y, 84, 92), YELLOW, 2.2)
            p.setOpacity(1.0)
            # arrow
            ax = W / 2 + 74
            ay = 30 + 10 * math.sin(t * 2 * math.pi)
            path = QPainterPath()
            path.moveTo(ax, ay)
            path.lineTo(ax, ay + 50)
            path.moveTo(ax - 14, ay + 36)
            path.lineTo(ax, ay + 52)
            path.lineTo(ax + 14, ay + 36)
            p.setPen(QPen(QColor(YELLOW), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        elif self.kind == "search":
            draw_glyph(p, "card", QRectF(W / 2 - 40, H / 2 - 50, 80, 90), "#cfd6e4", 2.0)
            arc = QRectF(W / 2 - 70, H / 2 - 70, 140, 140)
            p.setPen(QPen(QColor(YELLOW), 8, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(arc, int(-self._phase * 360 * 16), 100 * 16)
        elif self.kind == "check":
            draw_check(p, QPointF(W / 2, H / 2), 70, GREEN, INK)
        elif self.kind == "problem":
            draw_cross(p, QPointF(W / 2, H / 2), 70, RED)
        else:  # folder / drive / archive glyphs, big and yellow because they come with a "pick" button
            draw_glyph(p, self.kind, QRectF(W / 2 - 75, H / 2 - 75, 150, 150), YELLOW, 2.0)


class PulseButton(QWidget):
    """The one button to press: glowing ring plus a bouncing arrow pointing at it."""

    clicked = Signal()

    def __init__(self, text: str, color: str = YELLOW, big: bool = False, parent=None):
        super().__init__(parent)
        self.color = color
        self._phase = 0.0
        self.btn = QPushButton(text)
        self.btn.setObjectName("EasyGo" if big else "EasyPick")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self.clicked.emit)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 58, 26, 22)
        lay.addWidget(self.btn, 0, Qt.AlignHCenter)
        self._anim = _loop_anim(self, 1300, self._tick)
        self._anim.start()

    def _tick(self, v) -> None:
        self._phase = float(v)
        self.update()

    def set_text(self, t: str) -> None:
        self.btn.setText(t)

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        self.btn.setEnabled(on)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        if not self.isEnabled():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        g = QRectF(self.btn.geometry())
        rad = g.height() / 2
        pulse = 0.5 + 0.5 * math.sin(self._phase * 2 * math.pi)
        c = QColor(self.color)
        for k in range(18, 0, -1):
            c.setAlpha(int((40 + 110 * pulse) * (1 - k / 19) ** 2))
            p.setPen(QPen(c, 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(g.adjusted(-k, -k, k, k), rad + k, rad + k)
        x = g.center().x()
        y = g.top() - 50 + 9 * math.sin(self._phase * 2 * math.pi)
        path = QPainterPath()
        path.moveTo(x, y)
        path.lineTo(x, y + 30)
        path.moveTo(x - 13, y + 18)
        path.lineTo(x, y + 32)
        path.lineTo(x + 13, y + 18)
        p.setPen(QPen(QColor(YELLOW), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)


class RingProgress(QWidget):
    """Big percentage ring for the 'saving' screen."""

    def __init__(self, size: int = 260, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.value = 0.0
        self.caption = ""
        self.color_state = ""

    def set_value(self, v: float, caption: str = "") -> None:
        self.value = max(0.0, min(1.0, v))
        self.caption = caption
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self.width()
        w = 20.0
        r = QRectF(w, w, s - 2 * w, s - 2 * w)
        p.setPen(QPen(QColor("#222838"), w, Qt.SolidLine, Qt.RoundCap))
        p.drawEllipse(r)
        if self.value > 0:
            g = QConicalGradient(r.center(), 90)
            g.setColorAt(0, QColor(GREEN))
            g.setColorAt(1, QColor("#3ec6c0" if self.color_state != "ok" else GREEN))
            pen = QPen(g, w)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(r, 90 * 16, int(-self.value * 360 * 16))
        f = QFont(self.font())
        f.setPointSizeF(s * 0.15)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, s * 0.28, s, s * 0.30), Qt.AlignCenter, f"{int(self.value * 100)}%")
        f.setPointSizeF(14)
        p.setFont(f)
        p.setPen(QColor("#cfd6e4"))
        p.drawText(QRectF(0, s * 0.58, s, 30), Qt.AlignCenter, self.caption)


class _BigIcon(QWidget):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(110, 110)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(55, 55)
        if self.kind == "ok":
            draw_check(p, c, 50, GREEN, INK)
            return
        if self.kind == "bad":
            draw_cross(p, c, 50, RED)
            return
        col = YELLOW if self.kind in ("warn", "question") else "#4fb3d9"
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(col))
        p.drawEllipse(c, 50, 50)
        f = QFont(self.font())
        f.setPointSizeF(40)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(INK))
        mark = {"question": "?", "warn": "!"}.get(self.kind, "i")
        p.drawText(QRectF(0, 0, 110, 110), Qt.AlignCenter, mark)


class EasyDialog(QDialog):
    """Big, plain-language popup. ask() returns the index of the clicked button (or -1)."""

    def __init__(self, parent, kind: str, title: str, message: str, buttons: list[str],
                 primary: int = 0, details: str = ""):
        super().__init__(parent)
        self.setObjectName("EasyDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(640)
        self.choice = -1
        self.kind = kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 34, 40, 30)
        lay.setSpacing(16)
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
            self.details_box.setObjectName("Log")
            self.details_box.setReadOnly(True)
            self.details_box.setMaximumHeight(150)
            self.details_box.setVisible(False)
            more = QPushButton("Show details")
            more.setObjectName("EasyLink")
            more.setCursor(Qt.PointingHandCursor)
            more.clicked.connect(lambda: (self.details_box.setVisible(not self.details_box.isVisible()),
                                          self.adjustSize()))
            lay.addWidget(more, 0, Qt.AlignHCenter)
            lay.addWidget(self.details_box)
        lay.addSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addStretch(1)
        self.buttons: list[QPushButton] = []
        for i, text in enumerate(buttons):
            b = QPushButton(text)
            b.setObjectName("EasyDialogYes" if i == primary else "EasyDialogNo")
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumWidth(180)
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


class CoachOverlay(QWidget):
    """Dims the page except one highlighted widget and explains it in a speech bubble."""

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
        b.setContentsMargins(26, 22, 26, 20)
        b.setSpacing(10)
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
        self.skip = QPushButton("Skip")
        self.skip.setObjectName("EasyLink")
        self.skip.setCursor(Qt.PointingHandCursor)
        self.skip.clicked.connect(self.close_tour)
        row.addWidget(self.skip)
        row.addStretch(1)
        self.next = QPushButton("Next")
        self.next.setObjectName("EasyDialogYes")
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
        _target, title, text = self.steps[self.i]
        self.count.setText(f"{self.i + 1} of {len(self.steps)}")
        self.btitle.setText(title)
        self.btext.setText(text)
        self.next.setText("Got it!" if self.i == len(self.steps) - 1 else "Next")
        self._place()

    def _place(self) -> None:
        target = self.steps[self.i][0]
        tl = target.mapTo(self.page, target.rect().topLeft())
        self._hole = QRectF(tl.x(), tl.y(), target.width(), target.height()).adjusted(-8, -8, 8, 8)
        bl = self.bubble.layout()
        bl.activate()
        bw = 460
        bh = bl.totalHeightForWidth(bw) if bl.hasHeightForWidth() else self.bubble.sizeHint().height()
        self.bubble.setFixedSize(bw, bh)
        x = min(max(16.0, self._hole.center().x() - bw / 2), self.width() - bw - 16)
        y = self._hole.bottom() + 20
        if y + bh > self.height() - 16:
            y = self._hole.top() - bh - 20
        if y < 16:  # target fills the screen: put the bubble inside it, near the bottom
            y = max(16.0, self._hole.bottom() - bh - 24)
        self.bubble.move(int(x), int(y))
        self.update()

    def advance(self) -> None:
        if self.i < len(self.steps) - 1:
            self.i += 1
            self._show_step()
        else:
            self.close_tour()

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
        path.addRoundedRect(self._hole, 22, 22)
        p.fillPath(path, QColor(4, 6, 10, 215))
        for k in range(10, 0, -1):
            p.setPen(QPen(QColor(255, 212, 0, int(150 * (1 - k / 11) ** 2)), 2))
            p.drawRoundedRect(self._hole.adjusted(-k, -k, k, k), 22 + k, 22 + k)
        p.setPen(QPen(QColor(YELLOW), 3))
        p.drawRoundedRect(self._hole, 22, 22)

    def mousePressEvent(self, e):  # noqa: N802 - the page underneath stays untouchable during the tour
        e.accept()
