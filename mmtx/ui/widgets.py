"""Shared widgets and the background worker."""

from __future__ import annotations

import os
import traceback
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..transfer import free_bytes, human_bytes


def open_path(path: str) -> None:
    if path and os.path.exists(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class Card(QFrame):
    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 12, 16, 14)
        self.body.setSpacing(8)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("CardTitle")
            self.body.addWidget(t)


def label(text: str = "", obj: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


def button(text: str, obj: str = "", tip: str = "", slot: Optional[Callable] = None) -> QPushButton:
    b = QPushButton(text)
    if obj:
        b.setObjectName(obj)
    if tip:
        b.setToolTip(tip)
    if slot:
        b.clicked.connect(slot)
    b.setCursor(Qt.PointingHandCursor)
    return b


class PathPicker(QWidget):
    """Line edit + Browse + free space readout."""

    changed = Signal(str)

    def __init__(self, placeholder: str, dialog_title: str, show_free: bool = True, parent=None):
        super().__init__(parent)
        self.dialog_title = dialog_title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.editingFinished.connect(self._emit)
        row.addWidget(self.edit, 1)
        row.addWidget(button("Browse…", slot=self._browse))
        self.open_btn = button("Open", "Ghost", "Open in file manager", lambda: open_path(self.path()))
        row.addWidget(self.open_btn)
        lay.addLayout(row)
        self.free = label("", "Hint")
        self.free.setVisible(show_free)
        lay.addWidget(self.free)
        self._last = ""

    def path(self) -> str:
        return self.edit.text().strip()

    def set_path(self, p: str, emit: bool = True) -> None:
        self.edit.setText(p)
        if emit:
            self._emit()
        else:
            self._last = p
            self.refresh_free()

    def _browse(self) -> None:
        start = self.path() if os.path.isdir(self.path()) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, self.dialog_title, start)
        if p:
            self.set_path(os.path.normpath(p))

    def _emit(self) -> None:
        self.refresh_free()
        p = self.path()
        if p != self._last:
            self._last = p
            self.changed.emit(p)

    def refresh_free(self) -> None:
        p = self.path()
        if not p:
            self.free.setText("")
            return
        fb = free_bytes(p)
        exists = os.path.isdir(p)
        txt = f"{human_bytes(fb)} free" if fb >= 0 else "drive not found"
        if not exists and fb >= 0:
            txt += "  ·  folder will be created"
        self.free.setText(txt)


class NumItem(QTableWidgetItem):
    """Table item that sorts by a numeric key but shows formatted text."""

    def __init__(self, text: str, key: float):
        super().__init__(text)
        self.key = key
        self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):  # noqa: D401
        if isinstance(other, NumItem):
            return self.key < other.key
        return super().__lt__(other)


def ro_item(text: str, tip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if tip:
        it.setToolTip(tip)
    return it


# ---------------------------------------------------------------- background work


class _Bridge(QObject):
    event = Signal(str, dict)
    finished = Signal(object)
    failed = Signal(str)


class Worker(QThread):
    """Runs ``fn(emit)`` off the UI thread; ``emit(kind, payload)`` arrives as a Qt signal."""

    def __init__(self, fn: Callable[[Callable[[str, dict], None]], Any], parent=None):
        super().__init__(parent)
        self.fn = fn
        self.bridge = _Bridge()

    def run(self) -> None:  # noqa: D401
        try:
            res = self.fn(lambda kind, payload: self.bridge.event.emit(kind, payload))
            self.bridge.finished.emit(res)
        except Exception:  # noqa: BLE001
            self.bridge.failed.emit(traceback.format_exc())
