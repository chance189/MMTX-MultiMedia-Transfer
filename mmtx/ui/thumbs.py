"""Background thumbnail loading with an in-memory + on-disk cache."""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader, QPixmap

from ..media import embedded_jpeg, media_kind, mp4_duration, video_frame_jpeg
from ..settings import cache_dir

THUMB_BOX = 400  # stored thumbnails fit inside 400x400 (tiles are ~170 px wide, 2x for HiDPI)


def _load_image(path: str) -> QImage:
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if not reader.canRead():
        reader = QImageReader(path, b"jpg")  # .THM / .insp are JPEGs with odd extensions
        reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid():
        reader.setScaledSize(size.scaled(QSize(THUMB_BOX, THUMB_BOX), Qt.KeepAspectRatio))  # DCT-scaled decode
    return reader.read()


class _Task(QRunnable):
    def __init__(self, loader: "ThumbLoader", key: str, candidates, duration_path: str):
        super().__init__()
        self.loader, self.key, self.candidates, self.duration_path = loader, key, candidates, duration_path
        self.setAutoDelete(True)

    def run(self) -> None:  # noqa: D401 - worker thread
        img = QImage()
        dur = -1.0
        try:
            if self.duration_path and media_kind(self.duration_path) in ("video", "360"):
                d = mp4_duration(self.duration_path)
                dur = d if d is not None else -1.0
            cache_file = os.path.join(self.loader.disk_dir, self.key + ".jpg")
            if os.path.exists(cache_file):
                img = QImage(cache_file)
            if img.isNull():
                at = min(max(dur * 0.25, 0.5), 8.0) if dur > 0 else 1.0
                for path, method in self.candidates:
                    if method == "image":
                        img = _load_image(path)
                    else:
                        data = video_frame_jpeg(path, at=at) if method == "video" else embedded_jpeg(path)
                        img = QImage.fromData(data) if data else QImage()
                    if not img.isNull():
                        break
                if not img.isNull():
                    if img.width() > THUMB_BOX or img.height() > THUMB_BOX:
                        img = img.scaled(THUMB_BOX, THUMB_BOX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    try:
                        img.save(cache_file, "JPG", 85)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 - a broken file just gets a placeholder
            img = QImage()
        self.loader._done.emit(self.key, img, dur)


class ThumbLoader(QObject):
    """request(key, candidates) -> later `ready(key)`; then pixmap(key) / duration(key)."""

    ready = Signal(str)
    _done = Signal(str, QImage, float)

    def __init__(self, parent=None, threads: int = 3):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(threads)
        self.disk_dir = os.path.join(cache_dir(), "thumbs")
        os.makedirs(self.disk_dir, exist_ok=True)
        self.mem: OrderedDict[str, QPixmap] = OrderedDict()
        self.durations: dict[str, float] = {}
        self.inflight: set[str] = set()
        self.failed: set[str] = set()
        self.paused = False
        self.enabled = True
        self._parked: dict[str, tuple] = {}
        self._keys: dict[str, str] = {}
        self._done.connect(self._on_done, Qt.QueuedConnection)
        self.pool.start(_PruneTask(self.disk_dir))

    # -------------------------------------------------------------- keys
    def key_for(self, path: str) -> str:
        k = self._keys.get(path)
        if k:
            return k
        try:
            st = os.stat(path)
            raw = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}"
        except OSError:
            raw = os.path.abspath(path)
        k = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:24]
        self._keys[path] = k
        return k

    def known_key(self, path: str) -> Optional[str]:
        return self._keys.get(path)

    # -------------------------------------------------------------- lookups
    def pixmap(self, key: str) -> Optional[QPixmap]:
        pm = self.mem.get(key)
        if pm is not None:
            self.mem.move_to_end(key)
        return pm

    def duration(self, key: str) -> Optional[float]:
        d = self.durations.get(key)
        return d if d is not None and d >= 0 else None

    # -------------------------------------------------------------- requests
    def request(self, key: str, candidates, duration_path: str = "") -> None:
        if not self.enabled or not candidates:
            return
        if key in self.mem or key in self.inflight or key in self.failed:
            return
        if self.paused:
            self._parked[key] = (candidates, duration_path)
            return
        self.inflight.add(key)
        self.pool.start(_Task(self, key, candidates, duration_path))

    def pause(self) -> None:
        """Keep the card free for the transfer: drop queued work, park new requests."""
        self.paused = True
        self.pool.clear()
        self.inflight.clear()

    def resume(self) -> None:
        self.paused = False
        parked, self._parked = self._parked, {}
        for key, (cands, dpath) in parked.items():
            self.request(key, cands, dpath)

    def clear_queue(self) -> None:
        self.pool.clear()
        self.inflight.clear()

    def _on_done(self, key: str, img: QImage, dur: float) -> None:
        self.inflight.discard(key)
        if dur >= 0:
            self.durations[key] = dur
        if img.isNull():
            self.failed.add(key)
        else:
            self.mem[key] = QPixmap.fromImage(img)
            while len(self.mem) > 1500:
                self.mem.popitem(last=False)
        self.ready.emit(key)


class _PruneTask(QRunnable):
    """Keep the disk cache bounded (oldest thumbnails go first)."""

    def __init__(self, folder: str, keep: int = 6000):
        super().__init__()
        self.folder, self.keep = folder, keep

    def run(self) -> None:
        try:
            entries = [e for e in os.scandir(self.folder) if e.name.endswith(".jpg")]
            if len(entries) <= self.keep:
                return
            entries.sort(key=lambda e: e.stat().st_mtime)
            for e in entries[: len(entries) - self.keep]:
                os.remove(e.path)
        except OSError:
            pass
