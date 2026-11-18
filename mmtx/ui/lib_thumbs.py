"""Thumbnails and hover strips for MMTX Library, served from the preview store (see mmtx/previews.py).

Two thread pools, so a preview that's already made never waits behind one being made:
- load: read the stored JPEG and decode it (about a millisecond);
- make: build a missing preview with ffmpeg (or from the photo) and store it.
Both run the newest request first, and a make that's no longer on screen (you scrolled on) is dropped
before it starts. Decoded pictures stay in memory up to a byte budget, so scrolling back is free.
"""

from __future__ import annotations

import hashlib
import io
import os
import threading
from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from .. import media
from .. import previews as PV
from ..previews import video_source

__all__ = ["ThumbCache", "load_image", "video_source", "legacy_key"]

HEIF_EXT = PV.HEIF_EXT
THUMB_BUDGET = 256 * 1024 * 1024  # decoded thumbnails kept in memory
STRIP_KEEP = 24  # decoded hover strips kept in memory (1.8 MB each)

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
    """Any photo as a QImage, upright, optionally scaled down so its long side is max_side (the viewer)."""
    low = path.lower()
    if low.endswith(HEIF_EXT) or low.endswith(PV.RAW_EXT):
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


def legacy_key(row) -> str:
    """How MMTX 2.2 named its per-library thumbnail files."""
    return hashlib.sha1(f"{row.path}|{row.size}|{row.mtime}".encode("utf-8", "surrogatepass")).hexdigest()[:24]


class _Sig(QObject):
    loaded = Signal(str, str, QImage)  # kind ('t' thumbnail / 's' strip), key, picture
    absent = Signal(str, str)  # not made yet
    failed = Signal(str, str)  # can't be made (damaged file, drive offline, no ffmpeg)
    dropped = Signal(str, str)  # a make nobody's looking at any more


class _Load(QRunnable):
    def __init__(self, cache: "ThumbCache", kind: str, key: str, legacy: str):
        super().__init__()
        self.c, self.kind, self.key, self.legacy = cache, kind, key, legacy

    def run(self) -> None:  # noqa: D401
        s, store = self.c.sig, self.c.store
        try:
            data = store.thumb(self.key) if self.kind == "t" else store.strip(self.key)
            if data is None and self.kind == "t" and self.legacy and os.path.isfile(self.legacy):
                with open(self.legacy, "rb") as f:  # a thumbnail MMTX 2.2 made: take it over
                    data = f.read()
                store.put(self.key, data)
            if data:
                img = QImage.fromData(data)
                if not img.isNull():
                    s.loaded.emit(self.kind, self.key, img)
                    return
            if store.is_failed(self.key):
                s.failed.emit(self.kind, self.key)
            else:
                s.absent.emit(self.kind, self.key)
        except Exception:  # noqa: BLE001
            s.absent.emit(self.kind, self.key)


class _Make(QRunnable):
    def __init__(self, cache: "ThumbCache", kind: str, key: str, row, root: str):
        super().__init__()
        self.c, self.kind, self.key, self.row, self.root = cache, kind, key, row, root

    def run(self) -> None:  # noqa: D401
        c, s = self.c, self.c.sig
        if not c.wanted(self.kind, self.key):
            s.dropped.emit(self.kind, self.key)
            return
        c.store_demand(+1)
        try:
            if not os.path.exists(PV.abs_path(self.root, self.row.path)):
                s.failed.emit(self.kind, self.key)
                return
            thumb, strip = PV.make(self.row, self.root, c.cancel)
            c.store.put(self.key, thumb, strip)
            for kind, data in (("t", thumb), ("s", strip)):
                if data:
                    img = QImage.fromData(data)
                    if not img.isNull():
                        s.loaded.emit(kind, self.key, img)
            if self.kind == "s" and not strip:
                s.failed.emit("s", self.key)
        except PV.Cancelled:
            s.dropped.emit(self.kind, self.key)
        except PV.NoTool:
            s.failed.emit(self.kind, self.key)
        except Exception as e:  # noqa: BLE001
            if os.path.exists(PV.abs_path(self.root, self.row.path)):
                c.store.fail(self.key, str(e) or type(e).__name__)
            s.failed.emit(self.kind, self.key)
        finally:
            c.store_demand(-1)


class ThumbCache(QObject):
    ready = Signal(str)  # thumbnail key
    strip_ready = Signal(str)

    def __init__(self, parent=None, store: Optional[PV.PreviewStore] = None):
        super().__init__(parent)
        self.store = store or PV.shared_store()
        self.load_pool = QThreadPool(self)
        self.load_pool.setMaxThreadCount(2)
        self.make_pool = QThreadPool(self)
        self.make_pool.setMaxThreadCount(max(1, min(3, (os.cpu_count() or 4) // 2)))
        self.pool = self.make_pool  # older name
        self.mem: OrderedDict[str, QPixmap] = OrderedDict()
        self.mem_bytes = 0
        self.strips: OrderedDict[str, QPixmap] = OrderedDict()
        self.missing: set[str] = set()  # tags ('t'/'s' + key) with nothing to show this session
        self.pending: set[str] = set()
        self.rows: dict[str, object] = {}  # tag -> row, for making
        self.frame = 0
        self.last_req: dict[str, int] = {}
        self.seq = 0
        self.root = ""
        self.online = False
        self.legacy_dir = ""
        self.cancel = threading.Event()
        self._dlock = threading.Lock()
        self.sig = _Sig()
        self.sig.loaded.connect(self._loaded)
        self.sig.absent.connect(self._absent)
        self.sig.failed.connect(self._failed)
        self.sig.dropped.connect(self._dropped)
        self.made_on_demand = 0

    # ------------------------------------------------------------ setup
    def set_library(self, root: str, online: bool, legacy_dir: str = "") -> None:
        """Previews are keyed by content, so what's in memory stays useful across libraries."""
        self.load_pool.clear()
        self.make_pool.clear()
        self.pending.clear()
        self.missing.clear()
        self.rows.clear()
        self.last_req.clear()
        self.root, self.online, self.legacy_dir = root, online, legacy_dir

    def reset(self) -> None:
        """The store was cleared."""
        self.set_library(self.root, self.online, "")
        self.mem.clear()
        self.mem_bytes = 0
        self.strips.clear()

    def shutdown(self) -> None:
        """Stop making (running ffmpeg is killed) and wait; usable again afterwards."""
        self.cancel.set()
        self.load_pool.clear()
        self.make_pool.clear()
        self.make_pool.waitForDone(5000)
        self.load_pool.waitForDone(2000)
        self.pending.clear()
        self.rows.clear()
        self.cancel.clear()

    def idle(self) -> bool:
        return not self.pending and self.make_pool.activeThreadCount() == 0 and self.load_pool.activeThreadCount() == 0

    def store_demand(self, d: int) -> None:
        with self._dlock:
            self.store.demand = max(0, self.store.demand + d)

    # ------------------------------------------------------------ asking
    @staticmethod
    def key(row) -> str:
        return PV.key_for(row)

    def begin_frame(self) -> None:
        """The grid starts painting: requests from earlier paints that aren't repeated are stale."""
        self.frame += 1
        if len(self.last_req) > 20000:
            cut = self.frame - 3
            self.last_req = {t: f for t, f in self.last_req.items() if f >= cut or t in self.pending}

    def wanted(self, kind: str, key: str) -> bool:
        return self.frame - self.last_req.get(kind + key, -10) <= 1

    def thumb(self, row, keep: bool = False) -> Optional[QPixmap]:
        """``keep``: asked for outside the grid (viewer poster, drag): never counts as scrolled away."""
        k = self.key(row)
        pm = self.mem.get(k)
        if pm is not None:
            self.mem.move_to_end(k)
            return pm
        self._request("t", k, row, True)
        if keep:
            self.last_req["t" + k] = 1 << 60
        return None

    def strip(self, row) -> Optional[QPixmap]:
        k = self.key(row)
        pm = self.strips.get(k)
        if pm is not None:
            self.strips.move_to_end(k)
            return pm
        self._request("s", k, row, True)
        return None

    def prefetch(self, row) -> None:
        """Just off screen: load (or make) at lower priority than what's visible."""
        k = self.key(row)
        if k not in self.mem:
            self._request("t", k, row, False)

    def is_missing(self, row, kind: str = "t") -> bool:
        return f"{kind}{self.key(row)}" in self.missing

    def _request(self, kind: str, key: str, row, visible: bool) -> None:
        tag = kind + key
        self.last_req[tag] = self.frame
        if tag in self.pending or tag in self.missing:
            return
        self.pending.add(tag)
        self.rows[tag] = row
        self.seq += 1
        legacy = ""
        if kind == "t" and self.legacy_dir:
            lk = legacy_key(row)
            legacy = os.path.join(self.legacy_dir, lk[:2], lk + ".jpg")
        self.load_pool.start(_Load(self, kind, key, legacy), self._prio(visible))

    def _prio(self, visible: bool) -> int:
        return self.seq if visible else self.seq - 1_000_000  # newest first; off-screen after on-screen

    # ------------------------------------------------------------ answers (GUI thread)
    def _loaded(self, kind: str, key: str, img: QImage) -> None:
        tag = kind + key
        self.pending.discard(tag)
        self.rows.pop(tag, None)
        self.missing.discard(tag)
        pm = QPixmap.fromImage(img)
        if kind == "s":
            self.strips[key] = pm
            self.strips.move_to_end(key)
            while len(self.strips) > STRIP_KEEP:
                self.strips.popitem(last=False)
            self.strip_ready.emit(key)
            return
        old = self.mem.pop(key, None)
        if old is not None:
            self.mem_bytes -= old.width() * old.height() * 4
        self.mem[key] = pm
        self.mem_bytes += pm.width() * pm.height() * 4
        while self.mem_bytes > THUMB_BUDGET and len(self.mem) > 1:
            _k, gone = self.mem.popitem(last=False)
            self.mem_bytes -= gone.width() * gone.height() * 4
        self.ready.emit(key)

    def _absent(self, kind: str, key: str) -> None:
        tag = kind + key
        row = self.rows.get(tag)
        if row is None or not self.online:
            self._failed(kind, key)
            return
        if not self.wanted(kind, key):
            self._dropped(kind, key)
            return
        self.seq += 1
        visible = self.last_req.get(tag, -10) == self.frame
        self.make_pool.start(_Make(self, kind, key, row, self.root), self._prio(visible))
        self.made_on_demand += 1

    def _failed(self, kind: str, key: str) -> None:
        tag = kind + key
        self.pending.discard(tag)
        self.rows.pop(tag, None)
        self.missing.add(tag)
        if kind == "t":
            self.ready.emit(key)

    def _dropped(self, kind: str, key: str) -> None:
        tag = kind + key
        self.pending.discard(tag)
        self.rows.pop(tag, None)  # asked again when it's back on screen
