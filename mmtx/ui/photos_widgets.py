"""Widgets for the Google Photos views (Advanced and Easy)."""

from __future__ import annotations

import os
import time
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from ..takeout import media_kind
from .icons import draw_check


def pick_filter() -> str:
    """File dialog filter: takeout zips plus loose photos and videos (a .MOV saved from Google, say)."""
    from ..takeout import MEDIA_EXT

    media = " ".join(f"*{e}" for e in sorted(MEDIA_EXT))
    return f"Takeout zips, photos and videos (*.zip {media});;Takeout zips (*.zip);;All files (*)"


def drop_paths(event) -> list[str]:
    """Local zip files, loose photos and videos, and folders from a drag and drop."""
    md = event.mimeData()
    if not md.hasUrls():
        return []
    out = []
    for u in md.urls():
        if u.isLocalFile():
            p = u.toLocalFile()
            if os.path.isdir(p) or p.lower().endswith(".zip") or media_kind(p):
                out.append(os.path.normpath(p))
    return out


class YearBars(QWidget):
    """One column per year: how many photos and videos are coming, filling up as they land.

    colors() returns {track, fill, done, text, sub, line}; Advanced and Easy pass their own palettes."""

    def __init__(self, colors: Callable[[], dict], height: int = 150, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.setMinimumHeight(height)
        self.setMouseTracking(True)
        self.expected: dict[int, int] = {}
        self.done: dict[int, int] = {}
        self.active = False
        self._last_year = None
        self._last_at = 0.0
        self._t = QTimer(self)
        self._t.setInterval(60)
        self._t.timeout.connect(self._tick)

    def set_years(self, years: dict[int, int]) -> None:
        self.expected = {y: n for y, n in years.items() if n}
        self.done = {}
        self.active = False
        self.updateGeometry()
        self.update()

    def start(self) -> None:
        self.done = {}
        self.active = True
        self._t.start()
        self.update()

    def stop(self) -> None:
        self.active = False
        self._t.stop()
        self.update()

    def add(self, year: int, n: int = 1) -> None:
        self.done[year] = self.done.get(year, 0) + n
        if year not in self.expected:
            self.expected[year] = 0
        self._last_year = year
        self._last_at = time.monotonic()

    def _tick(self) -> None:
        self.update()

    def _cols(self) -> list[int]:
        ys = sorted(y for y in set(self.expected) | set(self.done) if y)
        if not ys:
            return [0] if (self.expected.get(0) or self.done.get(0)) else []
        out = list(range(ys[0], ys[-1] + 1)) if ys[-1] - ys[0] <= 40 else ys
        if self.expected.get(0) or self.done.get(0):
            out.append(0)
        return out

    def _geom(self):
        cols = self._cols()
        W, H = float(self.width()), float(self.height())
        n = max(1, len(cols))
        gap = 6.0 if n < 25 else 3.0
        cw = min(46.0, (W - gap * (n - 1)) / n)
        x0 = (W - (cw * n + gap * (n - 1))) / 2
        return cols, W, H, cw, gap, x0

    def paintEvent(self, _e):  # noqa: N802
        c = self.colors()
        cols, W, H, cw, gap, x0 = self._geom()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not cols:
            return
        f = QFont(self.font())
        f.setPointSizeF(8.5 if cw >= 30 else 7.5)
        f.setBold(True)
        fm = QFontMetrics(f)
        p.setFont(f)
        label_h = fm.height() + 6
        top = fm.height() + 6
        bottom = H - label_h
        avail = max(10.0, bottom - top)
        peak = max([max(self.expected.get(y, 0), self.done.get(y, 0)) for y in cols] + [1])
        now = time.monotonic()
        for i, y in enumerate(cols):
            x = x0 + i * (cw + gap)
            exp = self.expected.get(y, 0)
            got = self.done.get(y, 0)
            tot = max(exp, got)
            h = 0.0 if tot == 0 else max(4.0, avail * (tot / peak) ** 0.6)
            r = QRectF(x, bottom - h, cw, h)
            if h:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(c["track"]))
                p.drawRoundedRect(r, 4, 4)
                if got:
                    fh = h * min(1.0, got / tot)
                    fr = QRectF(x, bottom - fh, cw, fh)
                    col = QColor(c["done"] if got >= exp else c["fill"])
                    if self.active and y == self._last_year and now - self._last_at < 0.6:
                        col = col.lighter(130)
                    p.setBrush(col)
                    path = QPainterPath()
                    path.addRoundedRect(fr, 4, 4)
                    p.drawPath(path)
                    if got >= exp and exp and not self.active and cw >= 18:
                        draw_check(p, QPointF(r.center().x(), r.top() + min(12.0, h / 2)), min(8.0, cw / 3),
                                   c["done"], "#ffffff")
                num = got if self.active else tot
                if num and cw >= 20:
                    p.setPen(QColor(c["sub"]))
                    txt = f"{num / 1000:.1f}k" if num >= 10000 else str(num)
                    p.drawText(QRectF(x - 8, r.top() - fm.height() - 3, cw + 16, fm.height()),
                               Qt.AlignCenter, txt)
            p.setPen(QColor(c["text"] if tot else c["sub"]))
            lab = "Other" if y == 0 else (str(y) if cw >= 32 else f"’{y % 100:02d}")
            p.drawText(QRectF(x - 6, bottom + 4, cw + 12, fm.height()), Qt.AlignCenter, lab)
        p.setPen(QPen(QColor(c["line"]), 1))
        p.drawLine(QPointF(x0 - 4, bottom), QPointF(x0 + len(cols) * (cw + gap) - gap + 4, bottom))

    def mouseMoveEvent(self, e):  # noqa: N802
        cols, W, H, cw, gap, x0 = self._geom()
        i = int((e.position().x() - x0) // (cw + gap)) if cols else -1
        if 0 <= i < len(cols):
            y = cols[i]
            exp, got = self.expected.get(y, 0), self.done.get(y, 0)
            name = "Other (albums without a year folder, or no date)" if y == 0 else str(y)
            txt = f"{name}: {max(exp, got):,} file(s)"
            if self.active or got:
                txt += f", {got:,} in"
            QToolTip.showText(e.globalPosition().toPoint(), txt, self)
        else:
            QToolTip.hideText()


class ZipDropArea(QWidget):
    """A dashed drop target; clicking it opens the zip picker."""

    clicked = Signal()

    def __init__(self, colors: Callable[[], dict], text: str, sub: str = "", height: int = 96, parent=None):
        super().__init__(parent)
        self.colors = colors
        self.text = text
        self.sub = sub
        self.hot = False
        self.setMinimumHeight(height)
        self.setCursor(Qt.PointingHandCursor)

    def set_text(self, text: str, sub: str = "") -> None:
        self.text, self.sub = text, sub
        self.update()

    def set_hot(self, on: bool) -> None:
        self.hot = on
        self.update()

    def mouseReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def enterEvent(self, _e):  # noqa: N802
        self.set_hot(True)

    def leaveEvent(self, _e):  # noqa: N802
        self.set_hot(False)

    def paintEvent(self, _e):  # noqa: N802
        c = self.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        bg = QColor(c["fill"])
        bg.setAlpha(46 if self.hot else 22)
        p.setBrush(bg)
        pen = QPen(QColor(c["fill"] if self.hot else c["line"]), 2, Qt.DashLine)
        pen.setDashPattern([5, 4])
        p.setPen(pen)
        p.drawRoundedRect(r, 14, 14)
        f = QFont(self.font())
        f.setPointSizeF(11.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(c["text"]))
        if self.sub:
            p.drawText(r.adjusted(12, 10, -12, -r.height() / 2), Qt.AlignHCenter | Qt.AlignBottom, self.text)
            f.setBold(False)
            f.setPointSizeF(9.5)
            p.setFont(f)
            p.setPen(QColor(c["sub"]))
            p.drawText(r.adjusted(12, r.height() / 2 + 4, -12, -8), Qt.AlignHCenter | Qt.AlignTop, self.sub)
        else:
            p.drawText(r, Qt.AlignCenter, self.text)
