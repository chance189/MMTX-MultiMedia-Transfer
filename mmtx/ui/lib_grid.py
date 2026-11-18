"""The timeline grid of MMTX Library: one section per day (or per shoot folder), virtualized so a
100,000-item library scrolls smoothly. Video tiles scrub through 8 frames as the mouse moves across them."""

from __future__ import annotations

import bisect
import math
import os
from datetime import date

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QApplication

from ..catalog import DEVICE_KIND
from ..media import format_duration
from . import theme
from .icons import draw_check, draw_device

HEADER = 52
GAP = 4
PAD = 18
SCRUB_W = 58
SECTION_GAP = 10


def day_label(day: str) -> str:
    if not day:
        return "Unknown date"
    if len(day) == 7:  # a month section
        try:
            return date.fromisoformat(day + "-01").strftime("%B %Y")
        except ValueError:
            return day
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return day
    return d.strftime("%A, %B %-d, %Y") if os.name != "nt" else d.strftime("%A, %B %#d, %Y")


class _Section:
    __slots__ = ("day", "folder", "title", "start", "count", "y", "h", "videos", "seconds")

    def __init__(self, day, folder, title, start):
        self.day, self.folder, self.title, self.start = day, folder, title, start
        self.count = 0
        self.y = 0
        self.h = 0
        self.videos = 0
        self.seconds = 0.0


class TimelineGrid(QAbstractScrollArea):
    opened = Signal(int)  # row index
    selection_changed = Signal()
    context = Signal(int, QPoint)  # row index (-1 = empty space), global position

    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.rows: list = []
        self.sections: list[_Section] = []
        self.sec_y: list[int] = []
        self.tile = 168
        self.ts = 168.0  # actual tile size after stretching
        self.cols = 1
        self.total_h = 0
        self.selected: set[str] = set()
        self.anchor = -1
        self.hover = -1
        self.hover_x = 0.5
        self.hover_head = -1
        self.online = True
        self.root = ""
        self.group = "day"  # day | month
        self._press = None
        self._scrub_drag = False
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._scrolled())
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # the year scrubber replaces it
        self.setFrameShape(QAbstractScrollArea.NoFrame)
        self.viewport().setAutoFillBackground(False)
        thumbs.ready.connect(self._thumb_ready)
        thumbs.strip_ready.connect(lambda _k: self.viewport().update())
        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(50)
        self._idle.timeout.connect(self.viewport().update)

    # ---------------------------------------------------------------- data
    def set_rows(self, rows: list, root: str, online: bool, keep_scroll: bool = False) -> None:
        old = self.verticalScrollBar().value()
        self.rows = rows
        self.root = root
        self.online = online
        paths = {r.path for r in rows}
        self.selected &= paths
        self.sections = []
        cur = None
        for i, r in enumerate(rows):
            key = (r.day[:7], "") if self.group == "month" else (r.day, r.folder if r.title else "")
            if cur is None or (cur.day, cur.folder) != key:
                cur = _Section(key[0], key[1], r.title if self.group == "day" else "", i)
                self.sections.append(cur)
            cur.count += 1
            if r.kind == "video":
                cur.videos += 1
                cur.seconds += r.duration or 0.0
        self.hover = -1
        self._relayout()
        self.verticalScrollBar().setValue(old if keep_scroll else 0)
        self.viewport().update()
        self.selection_changed.emit()

    def set_tile(self, size: int) -> None:
        # keep the row under the top edge in view while zooming
        top = self._index_at_y(self.verticalScrollBar().value())
        self.tile = max(96, min(360, size))
        self._relayout()
        if top >= 0:
            self.verticalScrollBar().setValue(int(self._tile_rect(top).top() - PAD))
        self.viewport().update()

    def _relayout(self) -> None:
        w = max(200, self.viewport().width() - SCRUB_W - PAD)
        self.cols = max(1, int((w - PAD + GAP) // (self.tile + GAP)))
        self.ts = (w - PAD - GAP * (self.cols - 1)) / self.cols
        y = 8
        self.sec_y = []
        for s in self.sections:
            s.y = y
            rows = math.ceil(s.count / self.cols)
            s.h = HEADER + rows * (self.ts + GAP)
            self.sec_y.append(y)
            y += int(s.h) + SECTION_GAP
        self.total_h = y + PAD
        sb = self.verticalScrollBar()
        sb.setRange(0, max(0, self.total_h - self.viewport().height()))
        sb.setPageStep(self.viewport().height())
        sb.setSingleStep(int(self.ts / 2))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._relayout()

    # ---------------------------------------------------------------- geometry
    def _section_of(self, i: int) -> int:
        lo, hi = 0, len(self.sections) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.sections[mid].start <= i:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _tile_rect(self, i: int) -> QRectF:
        s = self.sections[self._section_of(i)]
        k = i - s.start
        r, c = divmod(k, self.cols)
        return QRectF(PAD + c * (self.ts + GAP), s.y + HEADER + r * (self.ts + GAP), self.ts, self.ts)

    def _index_at_y(self, y: int) -> int:
        if not self.sections:
            return -1
        si = max(0, bisect.bisect_right(self.sec_y, y) - 1)
        s = self.sections[si]
        r = max(0, int((y - s.y - HEADER) // (self.ts + GAP)))
        return min(s.start + r * self.cols, s.start + s.count - 1)

    def _hit(self, pos) -> tuple[int, int]:
        """(row index, section index of a header hit)."""
        y = pos.y() + self.verticalScrollBar().value()
        x = pos.x()
        if not self.sections or x > self.viewport().width() - SCRUB_W:
            return -1, -1
        si = bisect.bisect_right(self.sec_y, y) - 1
        if si < 0:
            return -1, -1
        s = self.sections[si]
        if y < s.y + HEADER:
            return -1, si
        r = int((y - s.y - HEADER) // (self.ts + GAP))
        c = int((x - PAD) // (self.ts + GAP))
        if c < 0 or c >= self.cols:
            return -1, -1
        k = r * self.cols + c
        if k >= s.count:
            return -1, -1
        rect = self._tile_rect(s.start + k)
        if not rect.contains(QPointF(x, y)):
            return -1, -1
        return s.start + k, -1

    def ensure_visible(self, i: int) -> None:
        if not (0 <= i < len(self.rows)):
            return
        r = self._tile_rect(i)
        sb = self.verticalScrollBar()
        if r.top() - HEADER < sb.value():
            sb.setValue(int(r.top() - HEADER - 8))
        elif r.bottom() > sb.value() + self.viewport().height():
            sb.setValue(int(r.bottom() - self.viewport().height() + 8))

    # ---------------------------------------------------------------- painting
    def _scrolled(self) -> None:
        self.viewport().update()

    def _thumb_ready(self, _key: str) -> None:
        if not self._idle.isActive():
            self._idle.start()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        vh = self.viewport().height()
        top = self.verticalScrollBar().value()
        p.fillRect(self.viewport().rect(), QColor(theme.BG))
        if not self.sections:
            return
        si = max(0, bisect.bisect_right(self.sec_y, top) - 1)
        fh = QFont(self.font())
        fh.setPointSizeF(12.5)
        fh.setBold(True)
        fs = QFont(self.font())
        fs.setPointSizeF(9.5)
        fb = QFont(self.font())
        fb.setPointSizeF(8.5)
        fb.setBold(True)
        self.thumbs.begin_frame()
        first_i = last_i = -1
        while si < len(self.sections) and self.sections[si].y - top < vh:
            s = self.sections[si]
            y0 = s.y - top
            self._paint_header(p, s, si, y0, fh, fs)
            first_row = max(0, int((top - s.y - HEADER) // (self.ts + GAP)))
            last_row = int((top + vh - s.y - HEADER) // (self.ts + GAP)) + 1
            for k in range(first_row * self.cols, min(s.count, (last_row + 1) * self.cols)):
                i = s.start + k
                r = self._tile_rect(i).translated(0, -top)
                if r.bottom() < 0 or r.top() > vh:
                    continue
                self._paint_tile(p, i, r, fb)
                first_i = i if first_i < 0 else first_i
                last_i = i
            si += 1
        self._paint_scrubber(p, top, vh)
        if last_i >= 0:  # a screen above and below, so scrolling finds them ready
            n = last_i - first_i + 1
            for i in range(last_i + 1, min(len(self.rows), last_i + 1 + n)):
                self.thumbs.prefetch(self.rows[i])
            for i in range(first_i - 1, max(-1, first_i - 1 - n), -1):
                self.thumbs.prefetch(self.rows[i])

    def _paint_header(self, p, s, si, y0, fh, fs) -> None:
        x = PAD
        if self.hover_head == si or any(r.path in self.selected for r in self.rows[s.start:s.start + s.count]) \
                and self.selected:
            on = all(r.path in self.selected for r in self.rows[s.start:s.start + s.count])
            draw_check(p, QPointF(x + 10, y0 + 24), 10, theme.ACCENT if on else "#00000000",
                       "#ffffff" if on else theme.MUTED)
            if not on:
                p.setPen(QPen(QColor(theme.MUTED), 1.5))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(x + 10, y0 + 24), 9, 9)
            x += 28
        p.setFont(fh)
        p.setPen(QColor(theme.TEXT))
        txt = day_label(s.day)
        p.drawText(QRectF(x, y0 + 8, 900, 22), Qt.AlignLeft | Qt.AlignVCenter, txt)
        tw = QFontMetrics(fh).horizontalAdvance(txt)
        bits = []
        if s.title:
            bits.append(s.title)
        photos = s.count - s.videos
        if photos:
            bits.append(f"{photos} photo{'s' if photos != 1 else ''}")
        if s.videos:
            v = f"{s.videos} video{'s' if s.videos != 1 else ''}"
            if s.seconds:
                v += f" · {format_duration(s.seconds)}"
            bits.append(v)
        p.setFont(fs)
        p.setPen(QColor(theme.MUTED))
        p.drawText(QRectF(x + tw + 14, y0 + 8, 900, 22), Qt.AlignLeft | Qt.AlignVCenter, "   ·   ".join(bits))

    def _paint_tile(self, p, i, r: QRectF, fb) -> None:
        row = self.rows[i]
        sel = row.path in self.selected
        hov = i == self.hover
        inner = r.adjusted(10, 10, -10, -10) if sel else r
        if sel:
            p.fillRect(r, QColor("#1c2840"))
        pm = None
        scrub = False
        if row.kind == "video" and hov and not sel:
            st = self.thumbs.strip(row)
            if st is not None:
                n = 8
                fw = st.width() / n
                k = min(n - 1, max(0, int(self.hover_x * n)))
                src = QRectF(k * fw, 0, fw, st.height())
                self._draw_cover(p, st, inner, src)
                scrub = True
        if not scrub:
            pm = self.thumbs.thumb(row)
            if pm is not None:
                self._draw_cover(p, pm, inner, QRectF(pm.rect()))
            else:
                path = QPainterPath()
                path.addRoundedRect(inner, 6, 6)
                p.fillPath(path, QColor("#1a1e27"))
                kind = DEVICE_KIND.get(row.device, "card")
                s = min(inner.width(), inner.height()) * 0.34
                draw_device(p, kind, QRectF(inner.center().x() - s / 2, inner.center().y() - s / 2, s, s),
                            "#4a5366", 1.6)
        if row.kind == "video":
            g = QLinearGradient(inner.bottomLeft() + QPointF(0, -34), inner.bottomLeft())
            g.setColorAt(0, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, 170))
            p.fillRect(QRectF(inner.left(), inner.bottom() - 34, inner.width(), 34), g)
            p.setFont(fb)
            p.setPen(QColor("#ffffff"))
            dur = format_duration(row.duration) if row.duration else ""
            p.drawText(inner.adjusted(6, 0, -8, -6), Qt.AlignRight | Qt.AlignBottom, dur)
            kind = DEVICE_KIND.get(row.device, "card")
            draw_device(p, kind, QRectF(inner.left() + 7, inner.bottom() - 22, 16, 16), "#ffffff", 1.2, 0)
            if scrub:  # progress line under the frame
                p.fillRect(QRectF(inner.left(), inner.bottom() - 3, inner.width() * self.hover_x, 3),
                           QColor(theme.ACCENT))
        badges = []
        if row.fmt == "360":
            badges.append("360°")
        if row.fmt == "raw":
            badges.append("RAW")
        if badges:
            p.setFont(fb)
            x = inner.right() - 6
            for b in badges:
                w = QFontMetrics(fb).horizontalAdvance(b) + 10
                br = QRectF(x - w, inner.top() + 6, w, 17)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 150))
                p.drawRoundedRect(br, 4, 4)
                p.setPen(QColor("#ffffff"))
                p.drawText(br, Qt.AlignCenter, b)
                x -= w + 4
        if row.fav:
            self._heart(p, QPointF(inner.right() - 14, inner.bottom() - (30 if row.kind == "video" else 14)), 7)
        if sel:
            draw_check(p, QPointF(r.left() + 16, r.top() + 16), 11, theme.ACCENT, "#ffffff")
        elif hov or self.selected:
            p.setPen(QPen(QColor(255, 255, 255, 230 if hov else 140), 1.8))
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawEllipse(QPointF(r.left() + 16, r.top() + 16), 10, 10)

    @staticmethod
    def _draw_cover(p, pm, r: QRectF, src: QRectF) -> None:
        """Fill r with the middle of src (center crop), rounded corners."""
        sw, sh = src.width(), src.height()
        if sw <= 0 or sh <= 0:
            return
        scale = max(r.width() / sw, r.height() / sh)
        cw, chh = r.width() / scale, r.height() / scale
        crop = QRectF(src.left() + (sw - cw) / 2, src.top() + (sh - chh) / 2, cw, chh)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.save()
        p.setClipPath(path)
        p.drawPixmap(r, pm, crop)
        p.restore()

    @staticmethod
    def _heart(p, c: QPointF, s: float) -> None:
        path = QPainterPath()
        path.moveTo(c.x(), c.y() + s * 0.9)
        path.cubicTo(c.x() - s * 1.6, c.y() - s * 0.2, c.x() - s * 0.6, c.y() - s * 1.3, c.x(), c.y() - s * 0.35)
        path.cubicTo(c.x() + s * 0.6, c.y() - s * 1.3, c.x() + s * 1.6, c.y() - s * 0.2, c.x(), c.y() + s * 0.9)
        p.setPen(QPen(QColor(0, 0, 0, 120), 1))
        p.setBrush(QColor("#ff5d7a"))
        p.drawPath(path)

    def _scrub_marks(self) -> list[tuple[float, str, bool]]:
        """(y in content, label, is year) for the right-hand date scrubber."""
        out = []
        last_y = last_m = None
        for s in self.sections:
            if not s.day:
                continue
            y, m = s.day[:4], s.day[:7]
            if y != last_y:
                out.append((s.y, y, True))
                last_y, last_m = y, m
            elif m != last_m:
                out.append((s.y, "", False))
                last_m = m
        return out

    def _paint_scrubber(self, p, top: int, vh: int) -> None:
        if self.total_h <= vh:
            return
        x0 = self.viewport().width() - SCRUB_W
        f = QFont(self.font())
        f.setPointSizeF(8.5)
        f.setBold(True)
        p.setFont(f)
        span = max(1, self.total_h)
        last_label_y = -100
        for y, lab, is_year in self._scrub_marks():
            sy = max(10.0, y / span * vh)
            if is_year and sy - last_label_y >= 16:
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(x0, sy - 8, SCRUB_W - 10, 16), Qt.AlignRight | Qt.AlignVCenter, lab)
                last_label_y = sy
            elif not is_year:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#3a4152"))
                p.drawEllipse(QPointF(x0 + SCRUB_W - 14, sy), 1.6, 1.6)
        # where you are
        sy = top / span * vh
        sh = max(18.0, vh / span * vh)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(91, 140, 255, 90 if self._scrub_drag else 60))
        p.drawRoundedRect(QRectF(x0 + SCRUB_W - 8, sy, 4, sh), 2, 2)

    # ---------------------------------------------------------------- mouse and keys
    def mouseMoveEvent(self, e):  # noqa: N802
        pos = e.position().toPoint()
        if self._scrub_drag:
            self._scrub_to(pos.y())
            return
        if self._press is not None and (pos - self._press[0]).manhattanLength() > QApplication.startDragDistance():
            i = self._press[1]
            self._press = None
            if i >= 0:
                self._start_drag(i)
                return
        i, hi = self._hit(pos)
        if i >= 0 and self.rows[i].kind == "video":
            r = self._tile_rect(i).translated(0, -self.verticalScrollBar().value())
            self.hover_x = max(0.0, min(0.999, (pos.x() - r.left()) / max(1.0, r.width())))
        if i != self.hover or hi != self.hover_head or (i >= 0 and self.rows[i].kind == "video"):
            self.hover, self.hover_head = i, hi
            self.viewport().update()
        self.viewport().setCursor(Qt.PointingHandCursor if (i >= 0 or hi >= 0) else Qt.ArrowCursor)

    def leaveEvent(self, _e):  # noqa: N802
        self.hover = self.hover_head = -1
        self.viewport().update()

    def mousePressEvent(self, e):  # noqa: N802
        pos = e.position().toPoint()
        self.setFocus()
        if pos.x() > self.viewport().width() - SCRUB_W and self.total_h > self.viewport().height():
            self._scrub_drag = True
            self._scrub_to(pos.y())
            return
        i, _hi = self._hit(pos)
        if e.button() == Qt.LeftButton:
            self._press = (pos, i)

    def mouseReleaseEvent(self, e):  # noqa: N802
        pos = e.position().toPoint()
        if self._scrub_drag:
            self._scrub_drag = False
            self.viewport().update()
            return
        press, self._press = self._press, None
        if e.button() == Qt.RightButton:
            i, _hi = self._hit(pos)
            if i >= 0 and self.rows[i].path not in self.selected:
                self.selected = {self.rows[i].path}
                self.anchor = i
                self.selection_changed.emit()
                self.viewport().update()
            self.context.emit(i, e.globalPosition().toPoint())
            return
        if press is None:
            return
        i, hi = self._hit(pos)
        mods = e.modifiers()
        if hi >= 0:
            s = self.sections[hi]
            paths = {r.path for r in self.rows[s.start:s.start + s.count]}
            if paths <= self.selected:
                self.selected -= paths
            else:
                self.selected |= paths
            self.selection_changed.emit()
            self.viewport().update()
            return
        if i < 0:
            return
        r = self._tile_rect(i).translated(0, -self.verticalScrollBar().value())
        on_check = (pos.x() - r.left()) < 32 and (pos.y() - r.top()) < 32
        if mods & Qt.ShiftModifier and self.anchor >= 0:
            a, b = sorted((self.anchor, i))
            self.selected |= {x.path for x in self.rows[a:b + 1]}
        elif on_check or mods & Qt.ControlModifier or self.selected:
            p = self.rows[i].path
            if p in self.selected:
                self.selected.discard(p)
            else:
                self.selected.add(p)
            self.anchor = i
        else:
            self.opened.emit(i)
            return
        self.selection_changed.emit()
        self.viewport().update()

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        i, _hi = self._hit(e.position().toPoint())
        if i >= 0:
            self.opened.emit(i)

    def wheelEvent(self, e):  # noqa: N802
        if e.modifiers() & Qt.ControlModifier:
            self.set_tile(self.tile + (16 if e.angleDelta().y() > 0 else -16))
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() == Qt.Key_Escape and self.selected:
            self.clear_selection()
        elif e.key() == Qt.Key_A and e.modifiers() & Qt.ControlModifier:
            self.selected = {r.path for r in self.rows}
            self.selection_changed.emit()
            self.viewport().update()
        else:
            super().keyPressEvent(e)

    def clear_selection(self) -> None:
        self.selected.clear()
        self.selection_changed.emit()
        self.viewport().update()

    def _scrub_to(self, y: int) -> None:
        vh = self.viewport().height()
        self.verticalScrollBar().setValue(int(y / max(1, vh) * self.total_h - vh / 2))

    def selected_rows(self) -> list:
        return [r for r in self.rows if r.path in self.selected]

    def _start_drag(self, i: int) -> None:
        """Drag files out to Explorer, Premiere, Resolve... (only while the drive is connected)."""
        if not self.online:
            return
        rows = self.selected_rows() if self.rows[i].path in self.selected else [self.rows[i]]
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(os.path.join(self.root, *r.path.split("/"))) for r in rows])
        drag = QDrag(self)
        drag.setMimeData(md)
        pm = self.thumbs.thumb(rows[0], keep=True)
        if pm is not None:
            drag.setPixmap(pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        drag.exec(Qt.CopyAction)
