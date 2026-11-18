"""Thumbnails and hover filmstrips for MMTX Library.

Every thumbnail is cached on this computer per library (keyed by path + size + modified time), so a
library whose drive is unplugged still shows the pictures you've already scrolled past.
Video thumbnails come from the low-res proxy (GoPro/Insta360 .LRV, DJI .LRF, Sony SUB) when there is
one: ~10x cheaper than decoding 5.3K HEVC. HEIC photos need pillow-heif (in requirements).
"""

from __future__ import annotations

import hashlib
import io
import os
from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter, QPixmap

from .. import media

THUMB = 360  # long side of a cached thumbnail
STRIP_FRAMES = 8
STRIP_W = 240
HEIF_EXT = (".heic", ".heif", ".avif", ".hif")

_heif_ready: Optional[bool] = None


def _heif() -> bool:
    global _heif_ready
    if _heif_ready is None:
        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
            _heif_ready = True
        except Exception:  # noqa: BLE001
            _heif_ready = False
    return _heif_ready


def load_image(path: str, max_side: int = 0) -> Optional[QImage]:
    """Any photo as a QImage, upright, optionally scaled down so its long side is max_side."""
    low = path.lower()
    if low.endswith(HEIF_EXT) or low.endswith((".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf", ".orf", ".rw2", ".gpr")):
        if low.endswith(HEIF_EXT):
            img = _pil_image(path, max_side)
            if img is not None:
                return img
        else:
            data = media.embedded_jpeg(path)
            if data:
                img = QImage.fromData(data)
                if not img.isNull():
                    return img.scaled(max_side, max_side, Qt.KeepAspectRatio, Qt.SmoothTransformation) \
                        if max_side and max(img.width(), img.height()) > max_side else img
    r = QImageReader(path)
    r.setAutoTransform(True)
    if max_side:
        sz = r.size()
        if sz.isValid() and max(sz.width(), sz.height()) > max_side:
            r.setScaledSize(sz.scaled(QSize(max_side, max_side), Qt.KeepAspectRatio))
    img = r.read()
    if img.isNull():
        return _pil_image(path, max_side)
    return img


def _pil_image(path: str, max_side: int) -> Optional[QImage]:
    if not _heif() and path.lower().endswith(HEIF_EXT):
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if max_side:
                im.thumbnail((max_side, max_side))
            im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90)
        img = QImage.fromData(buf.getvalue())
        return None if img.isNull() else img
    except Exception:  # noqa: BLE001
        return None


def video_source(root: str, row) -> str:
    """The cheapest file to pull frames from: the proxy if it's there, else the clip."""
    if row.proxy:
        p = os.path.join(root, *row.proxy.split("/"))
        if os.path.isfile(p):
            return p
    return os.path.join(root, *row.path.split("/"))


class _Emitter(QObject):
    done = Signal(str, str, QImage)  # kind ('t' or 's'), key, image (null = failed)


class _Task(QRunnable):
    def __init__(self, emitter: _Emitter, kind: str, key: str, cache_file: str, root: str, row, online: bool):
        super().__init__()
        self.e, self.kind, self.key, self.cache_file = emitter, kind, key, cache_file
        self.root, self.row, self.online = root, row, online

    def run(self) -> None:  # noqa: D401
        img = QImage()
        try:
            if os.path.isfile(self.cache_file):
                img = QImage(self.cache_file)
            elif self.online:
                img = self._make()
                if img is not None and not img.isNull():
                    os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
                    tmp = self.cache_file + ".tmp"
                    if img.save(tmp, "JPG", 82):
                        os.replace(tmp, self.cache_file)
        except Exception:  # noqa: BLE001
            img = QImage()
        self.e.done.emit(self.kind, self.key, img if img is not None else QImage())

    def _make(self) -> Optional[QImage]:
        r = self.row
        if self.kind == "s":
            return self._strip()
        if r.kind == "video":
            src = video_source(self.root, r)
            at = 1.0 if not r.duration else min(max(0.5, r.duration * 0.15), 8.0)
            data = media.video_frame_jpeg(src, at=at, width=THUMB)
            if data:
                img = QImage.fromData(data)
                return img if not img.isNull() else None
            return None
        return load_image(os.path.join(self.root, *r.path.split("/")), THUMB)

    def _strip(self) -> Optional[QImage]:
        r = self.row
        src = video_source(self.root, r)
        dur = r.duration or 0.0
        frames = []
        for i in range(STRIP_FRAMES):
            t = (i + 0.5) / STRIP_FRAMES * dur if dur else i * 1.0
            data = media.video_frame_jpeg(src, at=t, width=STRIP_W)
            img = QImage.fromData(data) if data else QImage()
            frames.append(img)
        good = [f for f in frames if not f.isNull()]
        if not good:
            return None
        h = good[0].height()
        out = QImage(STRIP_W * STRIP_FRAMES, h, QImage.Format_RGB32)
        out.fill(Qt.black)
        p = QPainter(out)
        last = good[0]
        for i, f in enumerate(frames):
            last = f if not f.isNull() else last
            p.drawImage(i * STRIP_W, 0, last.scaled(STRIP_W, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        p.end()
        return out


class ThumbCache(QObject):
    ready = Signal(str)  # thumbnail key
    strip_ready = Signal(str)

    def __init__(self, parent=None, mem_items: int = 1200):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(6, (os.cpu_count() or 4))))
        self.mem: OrderedDict[str, QPixmap] = OrderedDict()
        self.strips: OrderedDict[str, QPixmap] = OrderedDict()
        self.missing: set[str] = set()
        self.pending: set[str] = set()
        self.mem_items = mem_items
        self.dir = ""
        self.root = ""
        self.online = False
        self._em = _Emitter()
        self._em.done.connect(self._done)

    def set_library(self, cache_dir: str, root: str, online: bool) -> None:
        self.pool.clear()
        self.mem.clear()
        self.strips.clear()
        self.missing.clear()
        self.pending.clear()
        self.dir, self.root, self.online = cache_dir, root, online

    @staticmethod
    def key(row) -> str:
        return hashlib.sha1(f"{row.path}|{row.size}|{row.mtime}".encode("utf-8", "surrogatepass")).hexdigest()[:24]

    def _file(self, key: str, kind: str) -> str:
        return os.path.join(self.dir, key[:2], f"{key}{'_s' if kind == 's' else ''}.jpg")

    def thumb(self, row) -> Optional[QPixmap]:
        k = self.key(row)
        pm = self.mem.get(k)
        if pm is not None:
            self.mem.move_to_end(k)
            return pm
        self._request("t", k, row)
        return None

    def strip(self, row) -> Optional[QPixmap]:
        k = self.key(row)
        pm = self.strips.get(k)
        if pm is not None:
            return pm
        self._request("s", k, row)
        return None

    def is_missing(self, row, kind: str = "t") -> bool:
        return f"{kind}{self.key(row)}" in self.missing

    def _request(self, kind: str, key: str, row) -> None:
        tag = f"{kind}{key}"
        if tag in self.pending or tag in self.missing or not self.dir:
            return
        self.pending.add(tag)
        self.pool.start(_Task(self._em, kind, key, self._file(key, kind), self.root, row, self.online),
                        1 if kind == "t" else 0)

    def _done(self, kind: str, key: str, img: QImage) -> None:
        tag = f"{kind}{key}"
        self.pending.discard(tag)
        if img.isNull():
            self.missing.add(tag)
            if kind == "t":
                self.ready.emit(key)
            return
        pm = QPixmap.fromImage(img)
        if kind == "s":
            self.strips[key] = pm
            while len(self.strips) > 60:
                self.strips.popitem(last=False)
            self.strip_ready.emit(key)
        else:
            self.mem[key] = pm
            while len(self.mem) > self.mem_items:
                self.mem.popitem(last=False)
            self.ready.emit(key)

    def forget_queue(self) -> None:
        """Scrolled far away: drop requests that haven't started."""
        self.pool.clear()
        self.pending.clear()
