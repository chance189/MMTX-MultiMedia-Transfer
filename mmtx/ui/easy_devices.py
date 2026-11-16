"""Easy mode, step 1: "What are you copying from?" and how to connect each kind of device.

The steps come from each maker's own instructions (Sony FX3 help guide, GoPro, Insta360 online
manual, DJI, Apple). Only the steps for the computer MMTX is running on are shown.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .icons import draw_device, draw_glyph
from .palettes import P, ink

# kind, name, caption
DEVICES = [
    ("drone", "DJI drone", "Mini, Air, Mavic, Avata"),
    ("action", "GoPro", "HERO cameras"),
    ("360", "Insta360", "X series, Ace, GO"),
    ("cinema", "Sony camera", "FX3, FX30, Alpha"),
    ("phone", "Phone", "iPhone or Android, USB-C"),
    ("card", "Other memory card", "Any SD or microSD card"),
]
NAMES = {k: n for k, n, _c in DEVICES}

_READER = ("Put it in a card reader", "The slot on your computer, or a USB card reader.")
_FINDS = ("MMTX finds it", "This screen moves on by itself.")


def device_guide(kind: str, platform: str = sys.platform) -> dict:
    """{title, sub, routes: [(title, glyph, [(head, text)])], note}"""
    win, mac = platform == "win32", platform == "darwin"
    if kind == "drone":
        return dict(
            title="Connect your DJI drone",
            sub="Two ways in. The memory card is usually the fastest.",
            routes=[
                ("Memory card", "card", [("Take out the microSD card", "It's in the card slot on the drone."),
                                         _READER, _FINDS]),
                ("USB-C cable", "usb", [
                    ("Leave the drone switched off", "Newer drones like the Mini 4 Pro show their storage while off. "
                                                     "If nothing shows up, turn the drone on."),
                    ("Plug in a USB-C data cable", "Use the cable that came with the drone; some cables only charge."),
                    ("MMTX finds it", "The drone shows up like a memory card. Internal storage, if any, shows up "
                                      "as a second one.")]),
            ],
            note="")
    if kind == "action":
        usb = [("Set USB to MTP", "On the camera: Preferences > Connections > USB Connection > MTP."),
               ("Plug in and turn the camera on", "USB-C cable straight into the computer, not a hub."),
               ("MMTX finds it", "It shows up as a USB device. Over USB it copies one file at a time, so the "
                                 "card reader is quicker for big days.")]
        if mac:
            usb = [("Use the memory card", "A Mac can't read a GoPro over USB without extra software."),
                   ("Or copy it to a folder first", "Then pick Copy from a folder instead.")]
        return dict(
            title="Connect your GoPro",
            sub="The memory card is the fastest way.",
            routes=[
                ("Memory card", "card", [("Open the battery door", "The microSD card sits next to the battery."),
                                         _READER, _FINDS]),
                ("USB-C cable", "usb", usb),
            ],
            note="")
    if kind == "360":
        return dict(
            title="Connect your Insta360",
            sub="Both ways show the camera as a normal drive.",
            routes=[
                ("Memory card", "card", [("Open the battery door", "The microSD card is in the battery compartment."),
                                         _READER, _FINDS]),
                ("USB-C cable", "usb", [
                    ("Plug in and turn the camera on", "Use the USB-C cable that came with it."),
                    ("Pick File Transfer on the camera", "X4, X5, X6: File Transfer. X3: U-Disk Mode."),
                    ("MMTX finds it", "If Windows says \"USB device not recognized\", wait a few seconds: the camera "
                                      "restarts into transfer mode, then shows up.")]),
            ],
            note="")
    if kind == "cinema":
        return dict(
            title="Connect your Sony camera",
            sub="FX3 and most Sony cameras work both ways.",
            routes=[
                ("Memory card", "card", [
                    ("Open the card door", "The FX3 takes SD cards or CFexpress Type A cards."),
                    ("Use the right reader", "CFexpress Type A needs a CFexpress Type A reader. "
                                             "An SD reader only takes SD cards."),
                    ("MMTX finds it", "Two cards show up as two drives.")]),
                ("USB-C cable", "usb", [
                    ("Set USB to Mass Storage", "MENU > Setup > USB > USB Connection Mode > MassStorage(MSC)."),
                    ("Plug in the USB-C cable", "Camera on, cable straight into the computer."),
                    ("MMTX finds it", "With two cards in, each slot can show up as its own drive.")]),
            ],
            note="")
    if kind == "phone":
        if mac:
            android = [("Macs can't read Android over USB", "Not without extra software, such as OpenMTP."),
                       ("Copy the videos to a folder", "With that app, from DCIM/Camera."),
                       ("Then use Copy from a folder", "MMTX checks every file from there.")]
            iphone = [("Open Image Capture", "Connect the iPhone, unlock it and tap Trust."),
                      ("Import into a folder", "Pick the videos and a folder to import them to."),
                      ("Then use Copy from a folder", "MMTX checks every file from there.")]
        else:
            android = [("Unlock and plug in", "Use a USB-C data cable."),
                       ("Choose File transfer", "Tap the USB notification on the phone and pick File transfer."),
                       ("MMTX finds it", "It reads the camera folder (DCIM). " + (
                           "If it doesn't show up, open the phone once in your file manager." if not win else
                           "Over USB it copies one file at a time."))]
            iphone = ([("Install Apple Devices", "From the Microsoft Store. Windows needs it to see an iPhone.")]
                      if win else []) + [
                ("Unlock, plug in, tap Trust", "Enter your passcode when the iPhone asks."),
                ("Keep originals", "On the iPhone: Settings > Photos > Transfer to Mac or PC > Keep Originals, "
                                   "so videos come across unchanged."),
                _FINDS]
        return dict(
            title="Connect your phone",
            sub="MMTX reads the phone's camera folder over the cable. Only recent days are shown.",
            routes=[("Android", "phone", android), ("iPhone", "phone", iphone)],
            note="Videos that are only in iCloud or Google Photos (not stored on the phone) can't be copied over USB.")
    return dict(
        title="Insert your memory card",
        sub="Any camera that records to an SD or microSD card works.",
        routes=[("Memory card", "card", [("Take out the card", "Remove the memory card from your camera."),
                                         _READER, _FINDS])],
        note="")


# ------------------------------------------------------------------ widgets


class DeviceTile(QPushButton):
    def __init__(self, kind: str, name: str, caption: str, parent=None):
        super().__init__(parent)
        self.kind, self.name, self.caption = kind, name, caption
        self.last_used = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(232, 176)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.setAccessibleName(name)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        hover = self.underMouse()
        p.setPen(QPen(ink(150 if hover else 36), 2.5 if hover else 1.5))
        p.setBrush(ink(16 if hover else 8))
        p.drawRoundedRect(r, 20, 20)
        draw_device(p, self.kind, QRectF(r.center().x() - 40, r.y() + 18, 80, 80), P.text, width=1.6,
                    fill_alpha=40)
        f = QFont(self.font())
        f.setPointSizeF(14.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(P.text))
        p.drawText(QRectF(r.x(), r.y() + 104, r.width(), 28), Qt.AlignCenter, self.name)
        f.setPointSizeF(10.5)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(P.muted))
        p.drawText(QRectF(r.x() + 8, r.y() + 132, r.width() - 16, 22), Qt.AlignCenter, self.caption)
        if self.last_used:
            f.setPointSizeF(9)
            f.setBold(True)
            p.setFont(f)
            tag = QRectF(r.right() - 92, r.y() + 10, 82, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(ink(30))
            p.drawRoundedRect(tag, 11, 11)
            p.setPen(QColor(P.sub))
            p.drawText(tag, Qt.AlignCenter, "Last used")

    def enterEvent(self, e):  # noqa: N802
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        self.update()
        super().leaveEvent(e)


class DeviceGrid(QWidget):
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setHorizontalSpacing(14)
        lay.setVerticalSpacing(14)
        self.tiles: dict[str, DeviceTile] = {}
        for i, (kind, name, cap) in enumerate(DEVICES):
            t = DeviceTile(kind, name, cap)
            t.clicked.connect(lambda _c=False, k=kind: self.picked.emit(k))
            lay.addWidget(t, i // 3, i % 3)
            self.tiles[kind] = t

    def set_last(self, kind: str) -> None:
        for k, t in self.tiles.items():
            t.last_used = k == kind
            t.update()


class _RouteIcon(QWidget):
    def __init__(self, glyph: str):
        super().__init__()
        self.glyph = glyph
        self.setFixedSize(30, 30)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.glyph in ("card", "phone"):
            draw_device(p, self.glyph, QRectF(0, 0, 30, 30), P.text, width=1.3, fill_alpha=40)
        else:
            draw_glyph(p, self.glyph, QRectF(2, 2, 26, 26), P.text, 1.8)


class RouteCard(QFrame):
    """One way to connect (memory card, USB-C cable, Android, iPhone) as numbered steps."""

    def __init__(self, title: str, glyph: str, steps: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("HowTile")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(_RouteIcon(glyph))
        t = QLabel(title)
        t.setObjectName("PickDay")
        head.addWidget(t, 1)
        lay.addLayout(head)
        for i, (h, txt) in enumerate(steps, 1):
            row = QHBoxLayout()
            row.setSpacing(12)
            num = QLabel(str(i))
            num.setObjectName("HowNum")
            num.setFixedSize(30, 30)
            num.setAlignment(Qt.AlignCenter)
            row.addWidget(num, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            hl = QLabel(h)
            hl.setObjectName("HowHead")
            hl.setWordWrap(True)
            tl = QLabel(txt)
            tl.setObjectName("HowText")
            tl.setWordWrap(True)
            col.addWidget(hl)
            col.addWidget(tl)
            row.addLayout(col, 1)
            lay.addLayout(row)
        lay.addStretch(1)


class GuideView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 4, 0, 0)
        self.lay.setSpacing(10)
        self.row = QHBoxLayout()
        self.row.setSpacing(14)
        self.lay.addLayout(self.row)
        self.note = QLabel("")
        self.note.setObjectName("EasyFoot")
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignCenter)
        self.lay.addWidget(self.note)

    def set_guide(self, routes: list, note: str = "") -> None:
        while self.row.count():
            w = self.row.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        for title, glyph, steps in routes:
            self.row.addWidget(RouteCard(title, glyph, steps))
        self.note.setText(note)
        self.note.setVisible(bool(note))


class DeviceChip(QWidget):
    """Small pill above the title: which device MMTX is reading from."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.kind, self.text = "card", ""
        self.setFixedHeight(40)

    def set_device(self, kind: str, text: str) -> None:
        self.kind, self.text = kind or "card", text
        f = QFont(self.font())
        f.setPointSizeF(12.5)
        f.setBold(True)
        from PySide6.QtGui import QFontMetrics

        self.setFixedWidth(QFontMetrics(f).horizontalAdvance(text) + 70)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(ink(40), 1.2))
        p.setBrush(ink(12))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        draw_device(p, self.kind, QRectF(r.x() + 12, r.y() + 5, 28, 28), P.text, width=1.3, fill_alpha=40)
        f = QFont(self.font())
        f.setPointSizeF(12.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(P.text))
        p.drawText(r.adjusted(48, 0, -14, 0), Qt.AlignLeft | Qt.AlignVCenter, self.text)


class NameBox(QFrame):
    """'Name this shoot': one title for the new day folders, pre-filled with the default."""

    edited = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("NameBox")
        self.setFixedWidth(640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 14, 22, 14)
        lay.setSpacing(6)
        head = QLabel("Name this shoot")
        head.setObjectName("NameHead")
        lay.addWidget(head)
        self.edit = QLineEdit()
        self.edit.setObjectName("NameEdit")
        self.edit.setMaxLength(60)
        self.edit.setPlaceholderText("For example: Beach Run")
        self.edit.textEdited.connect(self.edited.emit)
        lay.addWidget(self.edit)
        self.preview = QLabel("")
        self.preview.setObjectName("NamePreview")
        self.preview.setTextFormat(Qt.RichText)
        self.preview.setWordWrap(True)
        lay.addWidget(self.preview)

    def set_text(self, t: str) -> None:
        if self.edit.text() != t:
            self.edit.setText(t)

    def focus_and_select(self) -> None:
        self.edit.setFocus(Qt.OtherFocusReason)
        self.edit.selectAll()


class _SwitchChip(QPushButton):
    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self.root = root
        self.kind, self.name, self.status, self.tone = "card", "", "", "idle"
        self.selected = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.setFixedHeight(64)

    def set_info(self, kind: str, name: str, status: str, tone: str, selected: bool) -> None:
        self.kind, self.name, self.status, self.tone, self.selected = kind, name, status, tone, selected
        f = QFont(self.font())
        f.setPointSizeF(11.5)
        f.setBold(True)
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(f)
        f.setPointSizeF(10)
        fm2 = QFontMetrics(f)
        self.setFixedWidth(min(300, max(fm.horizontalAdvance(name), fm2.horizontalAdvance(status) + 16) + 78))
        self.setAccessibleName(f"{name}: {status}")
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        hover = self.underMouse()
        p.setPen(QPen(QColor(P.text), 2.5) if self.selected else QPen(ink(110 if hover else 34), 1.5))
        p.setBrush(ink(22 if self.selected else (14 if hover else 6)))
        p.drawRoundedRect(r, 16, 16)
        if self.kind == "all":
            for dx, dy in ((8, -6), (0, 0)):
                draw_device(p, "card", QRectF(r.x() + 12 + dx, r.y() + 12 + dy, 32, 36), P.text, width=1.2,
                            fill_alpha=50)
        else:
            draw_device(p, self.kind, QRectF(r.x() + 10, r.y() + 12, 38, 38), P.text, width=1.2, fill_alpha=40)
        tone = {"busy": P.busy, "done": P.done_hi, "new": P.almost, "warn": P.bad}.get(self.tone, P.muted)
        f = QFont(self.font())
        f.setPointSizeF(11.5)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(P.text))
        tx = r.x() + 58
        p.drawText(QRectF(tx, r.y() + 8, r.width() - 66, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.name, Qt.ElideRight, int(r.width() - 66)))
        f.setPointSizeF(10)
        f.setBold(self.tone in ("busy", "new", "done"))
        p.setFont(f)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tone))
        p.drawEllipse(QRectF(tx, r.y() + 39, 8, 8))
        p.setPen(QColor(tone if self.tone != "idle" else P.muted))
        p.drawText(QRectF(tx + 14, r.y() + 32, r.width() - 80, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.status, Qt.ElideRight, int(r.width() - 80)))


class DeviceSwitcher(QWidget):
    """Shown when two or more cards/devices are connected: 'All devices' plus one chip per device with
    its live status. picked(root): '' means all devices."""

    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(10)
        self.lay.addStretch(1)
        self.lay.addStretch(1)
        self.chips: dict[str, _SwitchChip] = {}

    def set_devices(self, rows: list, selected: str) -> None:
        """rows: [(root, kind, name, status, tone)]; root '' is the 'All devices' chip."""
        want = [r[0] for r in rows]
        if want != list(self.chips):
            for c in self.chips.values():
                c.hide()
                c.setParent(None)
                c.deleteLater()
            self.chips = {}
            for root in want:
                c = _SwitchChip(root)
                c.clicked.connect(lambda _c=False, r=root: self.picked.emit(r))
                self.lay.insertWidget(self.lay.count() - 1, c)
                self.chips[root] = c
        for root, kind, name, status, tone in rows:
            self.chips[root].set_info(kind, name, status, tone, root == selected)
