"""Full-size viewer for MMTX Library: photos (HEIC and RAW previews too), video playback, details."""

from __future__ import annotations

import html
import os
from datetime import datetime

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..catalog import DEVICE_LABEL
from ..media import format_duration
from ..transfer import human_bytes
from . import theme
from .lib_thumbs import load_image

try:  # Qt Multimedia ships with PySide6; guarded so a missing backend only disables playback
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    HAVE_VIDEO = True
except Exception:  # noqa: BLE001
    HAVE_VIDEO = False


def _fmt_ms(ms: int) -> str:
    s = max(0, ms // 1000)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class _Loaded(QObject):
    done = Signal(int, QImage)


class _LoadTask(QRunnable):
    def __init__(self, sig: _Loaded, token: int, path: str):
        super().__init__()
        self.sig, self.token, self.path = sig, token, path

    def run(self) -> None:  # noqa: D401
        img = load_image(self.path, 4096)
        self.sig.done.emit(self.token, img if img is not None else QImage())


class ImageView(QWidget):
    """Fits the picture; mouse wheel zooms around the cursor, drag pans, double-click toggles 100%."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pm: QPixmap | None = None
        self.note = ""
        self.zoom = 0.0  # 0 = fit
        self.center = QPointF(0.5, 0.5)
        self._drag = None
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_pixmap(self, pm: QPixmap | None, note: str = "") -> None:
        self.pm, self.note = pm, note
        self.zoom = 0.0
        self.center = QPointF(0.5, 0.5)
        self.update()

    def _fit(self) -> float:
        if not self.pm or self.pm.isNull():
            return 1.0
        return min(self.width() / self.pm.width(), self.height() / self.pm.height())

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#000000"))
        if self.pm and not self.pm.isNull():
            z = self.zoom or self._fit()
            w, h = self.pm.width() * z, self.pm.height() * z
            if self.zoom:
                x = self.width() / 2 - self.center.x() * w
                y = self.height() / 2 - self.center.y() * h
            else:
                x, y = (self.width() - w) / 2, (self.height() - h) / 2
            p.drawPixmap(QRectF(x, y, w, h), self.pm, QRectF(self.pm.rect()))
        if self.note:
            f = QFont(self.font())
            f.setPointSizeF(11)
            p.setFont(f)
            r = QRectF(0, self.height() - 64, self.width(), 40)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            tw = p.fontMetrics().horizontalAdvance(self.note) + 36
            p.drawRoundedRect(QRectF((self.width() - tw) / 2, r.top(), tw, 34), 8, 8)
            p.setPen(QColor("#ffffff"))
            p.drawText(r.adjusted(0, 0, 0, -6), Qt.AlignCenter, self.note)

    def wheelEvent(self, e):  # noqa: N802
        if not self.pm:
            return
        z = self.zoom or self._fit()
        z *= 1.25 if e.angleDelta().y() > 0 else 0.8
        fit = self._fit()
        self.zoom = 0.0 if z <= fit else min(z, 8.0)
        self.update()

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if not self.pm:
            return
        if self.zoom:
            self.zoom = 0.0
        else:
            self.zoom = 1.0
            self.center = QPointF(e.position().x() / max(1, self.width()), e.position().y() / max(1, self.height()))
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        self._drag = (e.position(), QPointF(self.center))

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag and self.zoom and self.pm:
            start, c = self._drag
            d = e.position() - start
            self.center = QPointF(c.x() - d.x() / (self.pm.width() * self.zoom),
                                  c.y() - d.y() / (self.pm.height() * self.zoom))
            self.update()

    def mouseReleaseEvent(self, _e):  # noqa: N802
        self._drag = None


class Viewer(QWidget):
    closed = Signal()
    fav_toggled = Signal(str, bool)
    export_requested = Signal(list)
    collect_requested = Signal(list)
    reveal_requested = Signal(str)
    index_changed = Signal(int)

    def __init__(self, thumbs, parent=None):
        super().__init__(parent)
        self.thumbs = thumbs
        self.rows: list = []
        self.i = -1
        self.cat = None
        self.root = ""
        self.online = True
        self._token = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._loaded = _Loaded()
        self._loaded.done.connect(self._image_loaded)
        self.player = None
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QColor("#000000"))
        self.setPalette(pal)
        self.setFocusPolicy(Qt.StrongFocus)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("ViewerBar")
        bar.setStyleSheet(f"#ViewerBar {{ background:#0b0d11; border-bottom:1px solid {theme.BORDER}; }}")
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(10, 8, 12, 8)
        hb.setSpacing(8)
        self.back = self._btn("‹  Back", "", self.close_viewer, "Back to the grid (Esc)")
        hb.addWidget(self.back)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:12pt; font-weight:700;")
        self.sub = QLabel()
        self.sub.setStyleSheet(f"color:{theme.MUTED};")
        titles.addWidget(self.title)
        titles.addWidget(self.sub)
        hb.addLayout(titles, 1)
        self.fav = self._btn("♡ Favorite", "", self._toggle_fav, "Favorite (F)")
        self.info_btn = self._btn("Info", "", self._toggle_info, "Details (I)")
        self.info_btn.setCheckable(True)
        self.info_btn.setChecked(True)
        self.folder_btn = self._btn("Show in folder", "", lambda: self.rows and self.reveal_requested.emit(self.rows[self.i].path))
        self.coll_btn = self._btn("Add to collection", "", lambda: self.collect_requested.emit([self.rows[self.i]]))
        self.export_btn = self._btn("Export…", "Primary", lambda: self.export_requested.emit([self.rows[self.i]]))
        for b in (self.fav, self.info_btn, self.folder_btn, self.coll_btn, self.export_btn):
            hb.addWidget(b)
        v.addWidget(bar)

        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.prev = self._nav("‹", -1)
        self.next = self._nav("›", 1)
        mid.addWidget(self.prev)
        self.stack = QStackedWidget()
        self.image = ImageView()
        self.stack.addWidget(self.image)
        self.video_page = QWidget()
        vv = QVBoxLayout(self.video_page)
        vv.setContentsMargins(0, 0, 0, 0)
        vv.setSpacing(0)
        self.video = QVideoWidget() if HAVE_VIDEO else QWidget()
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vv.addWidget(self.video, 1)
        ctl = QWidget()
        ctl.setStyleSheet("background:#0b0d11;")
        ch = QHBoxLayout(ctl)
        ch.setContentsMargins(12, 6, 12, 8)
        self.play_btn = self._btn("Pause", "", self._toggle_play, "Play / pause (Space)")
        self.play_btn.setFixedWidth(84)
        self.pos = QSlider(Qt.Horizontal)
        self.pos.setRange(0, 0)
        self.pos.sliderMoved.connect(lambda v_: self.player and self.player.setPosition(v_))
        self.time = QLabel("0:00 / 0:00")
        self.time.setStyleSheet(f"color:{theme.MUTED}; min-width:110px;")
        self.mute_btn = self._btn("Sound off", "", self._toggle_mute, "Videos start muted")
        self.mute_btn.setFixedWidth(96)
        self.proxy_btn = self._btn("Low-res copy", "", self._toggle_proxy,
                                   "Play the camera's low-res proxy (LRV/LRF): smooth on any computer")
        self.proxy_btn.setCheckable(True)
        for w in (self.play_btn, self.pos, self.time, self.mute_btn, self.proxy_btn):
            ch.addWidget(w, 1 if w is self.pos else 0)
        vv.addWidget(ctl)
        self.stack.addWidget(self.video_page)
        mid.addWidget(self.stack, 1)
        mid.addWidget(self.next)
        self.info = QScrollArea()
        self.info.setWidgetResizable(True)
        self.info.setFixedWidth(330)
        self.info.setStyleSheet(f"QScrollArea {{ background:{theme.SURFACE}; border:none; "
                                f"border-left:1px solid {theme.BORDER}; }}")
        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.RichText)
        self.info_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info_label.setOpenExternalLinks(True)
        self.info_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info_label.setContentsMargins(18, 16, 18, 16)
        self.info_label.setStyleSheet(f"background:{theme.SURFACE};")
        self.info.setWidget(self.info_label)
        mid.addWidget(self.info)
        v.addLayout(mid, 1)

    def _btn(self, text, obj, slot, tip="") -> QPushButton:
        b = QPushButton(text)
        if obj:
            b.setObjectName(obj)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(slot)
        return b

    def _nav(self, text, step) -> QPushButton:
        b = QPushButton(text)
        b.setFixedWidth(46)
        b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        b.setFocusPolicy(Qt.NoFocus)
        b.setStyleSheet("QPushButton { background:#000; color:#9aa3b5; border:none; font-size:30pt; }"
                        "QPushButton:hover { color:#ffffff; background:#0d1016; }"
                        "QPushButton:disabled { color:#222; }")
        b.clicked.connect(lambda: self.step(step))
        return b

    # ---------------------------------------------------------------- showing
    def open(self, rows: list, i: int, cat, root: str, online: bool) -> None:
        self.rows, self.cat, self.root, self.online = rows, cat, root, online
        self.show()
        self.raise_()
        self.setFocus()
        self._show(i)

    def step(self, d: int) -> None:
        if self.rows:
            self._show(max(0, min(len(self.rows) - 1, self.i + d)))

    def _abs(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def _show(self, i: int) -> None:
        self._stop_video()
        self.i = i
        r = self.rows[i]
        self.index_changed.emit(i)
        self.prev.setEnabled(i > 0)
        self.next.setEnabled(i < len(self.rows) - 1)
        self.title.setText(r.name)
        when = datetime.fromtimestamp(r.taken).strftime("%a, %b %d, %Y  ·  %I:%M %p").replace(" 0", " ") \
            if r.taken else "Unknown date"
        self.sub.setText("   ·   ".join(x for x in (when, r.title, r.model) if x) + f"   ·   {i + 1:,} of {len(self.rows):,}")
        self.fav.setText("♥ Favorite" if r.fav else "♡ Favorite")
        writable = self.cat is not None and not self.cat.read_only
        self.fav.setEnabled(writable)
        self.coll_btn.setEnabled(writable)
        self.folder_btn.setEnabled(self.online)
        self.export_btn.setEnabled(self.online)
        self._fill_info(r)
        poster = self.thumbs.thumb(r)
        if r.kind == "video":
            if self.online and HAVE_VIDEO:
                self.stack.setCurrentWidget(self.video_page)
                self.proxy_btn.setVisible(bool(r.proxy))
                self.proxy_btn.setChecked(False)
                self._play(self._abs(r.path))
                return
            self.stack.setCurrentWidget(self.image)
            self.image.set_pixmap(poster, "Connect the drive to play this video" if not self.online
                                  else "Video playback isn't available on this computer")
            return
        self.stack.setCurrentWidget(self.image)
        if not self.online:
            self.image.set_pixmap(poster, "Saved preview · connect the drive for the full photo")
            return
        self.image.set_pixmap(poster, "")
        self._token += 1
        self._pool.start(_LoadTask(self._loaded, self._token, self._abs(r.path)))

    def _image_loaded(self, token: int, img: QImage) -> None:
        if token != self._token or img.isNull():
            if token == self._token and self.image.pm is None:
                self.image.set_pixmap(None, "Can't show this file here; open it with Show in folder")
            return
        self.image.set_pixmap(QPixmap.fromImage(img))

    def _fill_info(self, r) -> None:
        d = self.cat.get(r.path) if self.cat else None
        meta = (d or {}).get("meta") or {}
        rows = []

        def add(k, v):
            if v:
                rows.append(f"<div style='color:{theme.MUTED}; font-size:8.5pt; font-weight:700; "
                            f"letter-spacing:1px; margin-top:12px;'>{k.upper()}</div>"
                            f"<div style='margin-top:2px;'>{v}</div>")

        esc = html.escape
        if r.taken:
            dt = datetime.fromtimestamp(r.taken)
            add("Taken", dt.strftime("%A, %B %d, %Y<br>") + dt.strftime("%I:%M:%S %p").lstrip("0"))
        if meta.get("date_from"):
            from ..takeout import DATE_LABEL

            add("Date from", esc(DATE_LABEL.get(meta["date_from"], meta["date_from"])))
        if r.title:
            add("Shoot", esc(r.title))
        add("Camera", esc(r.model or DEVICE_LABEL.get(r.device, "")))
        kind = "Video" if r.kind == "video" else "Photo"
        ext = os.path.splitext(r.name)[1].upper().lstrip(".")
        parts = [f"{ext} {kind.lower()}" + (" (360°)" if r.fmt == "360" else "")]
        if r.width and r.height:
            parts.append(f"{r.width} × {r.height}")
        if r.duration:
            parts.append(format_duration(r.duration))
        parts.append(human_bytes(r.size or 0))
        add("File", esc("  ·  ".join(parts)))
        if meta.get("description"):
            add("Caption", esc(meta["description"]))
        loc = meta.get("location")
        if loc and len(loc) == 2 and (loc[0] or loc[1]):
            url = f"https://www.google.com/maps/search/?api=1&query={loc[0]},{loc[1]}"
            add("Location", f"{loc[0]:.5f}, {loc[1]:.5f}<br><a style='color:{theme.ACCENT}' href='{url}'>Open in maps</a>")
        if meta.get("people"):
            add("People", esc(", ".join(meta["people"])))
        if meta.get("albums"):
            add("Albums", esc(", ".join(meta["albums"])))
        if d and d.get("collections"):
            add("Collections", esc(", ".join(d["collections"])))
        if meta.get("original_name"):
            add("Original name", esc(meta["original_name"]))
        if meta.get("google_url"):
            add("Google Photos", f"<a style='color:{theme.ACCENT}' href='{esc(meta['google_url'])}'>Open in Google Photos</a>")
        add("In the library", esc(r.path))
        if r.proxy:
            add("Low-res copy", esc(r.proxy.rsplit("/", 1)[-1]))
        if d and d.get("hash"):
            add("Checksum", f"<span style='font-family:monospace; font-size:8.5pt'>{esc(d['hash'])}</span>")
        self.info_label.setText("".join(rows))

    # ---------------------------------------------------------------- video
    def _ensure_player(self) -> None:
        if self.player is None and HAVE_VIDEO:
            self.player = QMediaPlayer(self)
            self.audio = None  # videos start muted: no audio device is opened until you turn sound on
            self.player.setVideoOutput(self.video)
            self.player.durationChanged.connect(lambda d: self.pos.setRange(0, int(d)))
            self.player.positionChanged.connect(self._pos_changed)
            self.player.playbackStateChanged.connect(
                lambda s: self.play_btn.setText("Pause" if s == QMediaPlayer.PlayingState else "Play"))

    def _play(self, path: str) -> None:
        self._ensure_player()
        if self.player is None:
            return
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()

    def _pos_changed(self, ms: int) -> None:
        if not self.pos.isSliderDown():
            self.pos.setValue(int(ms))
        self.time.setText(f"{_fmt_ms(int(ms))} / {_fmt_ms(int(self.player.duration()))}")

    def _toggle_play(self) -> None:
        if self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _toggle_mute(self) -> None:
        if self.player is None:
            return
        if self.audio is None:
            self.audio = QAudioOutput(self)
            self.audio.setMuted(True)
            self.player.setAudioOutput(self.audio)
        m = not self.audio.isMuted()
        self.audio.setMuted(m)
        self.mute_btn.setText("Sound off" if m else "Sound on")

    def _toggle_proxy(self) -> None:
        r = self.rows[self.i]
        if not r.proxy or self.player is None:
            return
        at = self.player.position()
        self.player.setSource(QUrl.fromLocalFile(self._abs(r.proxy if self.proxy_btn.isChecked() else r.path)))
        self.player.setPosition(at)
        self.player.play()

    def _stop_video(self) -> None:
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())

    # ---------------------------------------------------------------- actions
    def _toggle_fav(self) -> None:
        if not self.rows or self.cat is None or self.cat.read_only:
            return
        r = self.rows[self.i]
        on = not r.fav
        self.rows[self.i] = r._replace(fav=int(on))
        self.fav.setText("♥ Favorite" if on else "♡ Favorite")
        self.fav_toggled.emit(r.path, on)

    def _toggle_info(self) -> None:
        self.info.setVisible(self.info_btn.isChecked())

    def close_viewer(self) -> None:
        self._stop_video()
        self.hide()
        self.closed.emit()

    def keyPressEvent(self, e):  # noqa: N802
        k = e.key()
        if k == Qt.Key_Escape:
            self.close_viewer()
        elif k in (Qt.Key_Left, Qt.Key_Up):
            self.step(-1)
        elif k in (Qt.Key_Right, Qt.Key_Down):
            self.step(1)
        elif k == Qt.Key_Space:
            self._toggle_play()
        elif k == Qt.Key_F:
            self._toggle_fav()
        elif k == Qt.Key_I:
            self.info_btn.toggle()
            self._toggle_info()
        else:
            super().keyPressEvent(e)


def open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


__all__ = ["Viewer", "ImageView", "HAVE_VIDEO", "open_url", "QTimer"]
