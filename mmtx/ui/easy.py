"""Easy mode: one step at a time, one flashing button, plain language.

Copy my card:     1. Insert card (detected automatically)  2. Choose folder (once, suggested)  3. Start
Move old videos:  1. Videos folder                          2. Backup drive (suggested)          3. Start

The button for the current step flashes red until it is pressed, then turns green, the tracker ticks the
step off and the next step opens. Nothing else flashes. Amber = take care; problems are popups.
MMTX names the folders itself: <your folder>/YYYY_MM_DD_<Title>/<files>, and Archive/YYYY/<day folder>.

The card scan and any running transfer belong to the main window, so this page and the Advanced
pages always show the same thing, whichever one started the transfer.

Safety without questions:
  * Copy everything never deletes from a card or phone; Choose which ones can, after each file checks out.
  * Only days that aren't saved yet are copied, so pressing Start twice is harmless.
  * Old videos leave this computer only after every file checked out on the backup drive.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..media import poster_candidates
from ..planner import (
    merge_offload_jobs,
    day_folder_name,
    day_presence,
    existing_day_folders,
    is_inside,
    plan_archive,
    plan_offload,
    title_from_existing,
)
from ..scanner import DayGroup, scan_working
from ..settings import logs_dir
from ..transfer import free_bytes, human_bytes
from .clip_grid import ROLE, clip_info
from .. import sources
from .easy_clips import ALMOST, EasyClipGrid, NowThumb
from .easy_photos import PHOTOS_STEPS, PHOTOS_TAB, PhotosFlow
from .easy_devices import DeviceChip, DeviceGrid, DeviceSwitcher, GuideView, NameBox, device_guide
from .easy_widgets import (
    Art,
    Beacon,
    CoachOverlay,
    EasyDialog,
    FolderLiveList,
    HelpDialog,
    PaletteDialog,
    RingProgress,
    StepTracker,
    ThumbFan,
    ToggleCard,
    paint_backdrop,
)
from .palettes import P, easy_qss, set_palette
from .icons import draw_glyph, icon
from .offload_tab import device_folders, offload_options
from .widgets import Worker, open_path

PHASE_WORDS = {"Copying": "Copying", "Verifying": "Checking", "Checking existing": "Checking",
               "Moving (same drive)": "Moving"}
PHASE_STATUS = {"Copying": "copying", "Verifying": "verifying", "Checking existing": "checking"}
FINAL_STATUS = {"copied": "copied", "skipped": "skipped", "failed": "failed", "cancelled": "cancelled"}


def friendly_eta(sec: float) -> str:
    if sec < 0:
        return "Estimating time left…"
    if sec < 60:
        return "Less than a minute left"
    if sec < 3600:
        m = max(1, round(sec / 60))
        return f"About {m} minute{'s' if m != 1 else ''} left"
    h, m = divmod(int(sec // 60), 60)
    return f"About {h} h {m} min left"


def _lab(text: str, obj: str, center: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(obj)
    lab.setWordWrap(True)
    if center:
        lab.setAlignment(Qt.AlignCenter)
    return lab


def _ghost(text: str, slot) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("EasyGhost")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _link(text: str, slot) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("EasyLink")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _tool(text: str, slot) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("PickTool")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _home_anchor() -> str:
    home = os.path.abspath(os.path.expanduser("~"))
    return os.path.splitdrive(home)[0] + "\\" if sys.platform == "win32" else "/"


def suggest_place(vols: list, folder_name: str, avoid: str = "") -> Optional[tuple[str, int]]:
    """The non-camera drive with the most free space -> (suggested folder, free bytes)."""
    best = None
    for v in vols:
        if v.camera or v.read_only or v.kind in ("Network", "SD card"):
            continue
        if avoid and (is_inside(avoid, v.root) or is_inside(v.root, avoid)):
            continue
        fb = free_bytes(v.root)
        if fb > 0 and (best is None or fb > best[1]):
            best = (v.root, fb)
    if best is None:
        home = os.path.expanduser("~")
        if avoid and (is_inside(avoid, home) or is_inside(home, avoid)):
            return None
        videos = os.path.join(home, "Videos")
        fb = free_bytes(videos)
        return (os.path.join(videos, folder_name), fb) if fb > 0 else None
    root, fb = best
    if os.path.normcase(os.path.abspath(root)) == os.path.normcase(_home_anchor()):
        return os.path.join(os.path.expanduser("~"), "Videos", folder_name), fb
    return os.path.join(root, folder_name), fb


class _Glyph(QWidget):
    def __init__(self, name: str, size: int = 40, color: str = ""):
        super().__init__()
        self.name, self.color = name, color
        self.setFixedSize(size, size)

    def paintEvent(self, _e):  # noqa: N802
        from PySide6.QtCore import QRectF

        p = QPainter(self)
        draw_glyph(p, self.name, QRectF(0, 0, self.width(), self.height()), self.color or P.eyebrow, 1.7)


class HowToTiles(QWidget):
    """Three short steps side by side with icons (static)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.tiles: list[tuple[QFrame, _Glyph, QLabel, QLabel]] = []
        for i in range(3):
            tile = QFrame()
            tile.setObjectName("HowTile")
            tl = QVBoxLayout(tile)
            tl.setContentsMargins(18, 16, 18, 16)
            tl.setSpacing(8)
            top = QHBoxLayout()
            num = QLabel(str(i + 1))
            num.setObjectName("HowNum")
            num.setFixedSize(30, 30)
            num.setAlignment(Qt.AlignCenter)
            top.addWidget(num)
            top.addStretch(1)
            glyph = _Glyph("card")
            top.addWidget(glyph)
            tl.addLayout(top)
            head = _lab("", "HowHead", center=False)
            body = _lab("", "HowText", center=False)
            tl.addWidget(head)
            tl.addWidget(body)
            tl.addStretch(1)
            lay.addWidget(tile, 1)
            self.tiles.append((tile, glyph, head, body))
            if i < 2:
                chev = QLabel("›")
                chev.setObjectName("HowChevron")
                lay.addWidget(chev)

    def set_steps(self, steps: list[tuple[str, str, str]]) -> None:
        for (tile, glyph, head, body), (g, h, b) in zip(self.tiles, steps):
            glyph.name = g
            glyph.update()
            head.setText(h)
            body.setText(b)


class EasyPage(PhotosFlow, QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("EasyPage")
        self.host = host
        self.s = host.settings
        self.loader = host.loader
        self.plan: list = []
        self.arch_folders: list = []
        self.arch_scanned = False
        self.arch_worker: Optional[Worker] = None
        self._vols: list = []
        self._suggestion: Optional[tuple[str, int]] = None
        self._primary_action = None
        self._second_action = None
        self._foot_actions: list = [None, None]
        self._started_here = False
        self._advancing = False  # a step button just turned green; the next step opens shortly
        self._device_pick = ""  # device whose connection steps are showing on step 1
        self._shoot_title = self.s.default_title  # "Name this shoot"
        self._day_titles: dict = {}  # per-day names from Choose which ones
        self._title_root = None  # titles reset once every card/device has been unplugged
        self._view_all = True  # several devices connected: show them together (the "All devices" chip)
        self._pick_scans: list = []
        self._plans: dict = {}  # root -> plan, for the "All devices" view
        self._cards_sig = None
        self._name_focus = True
        self._existing: dict = {}
        self._job_kind = ""
        self._job_dest = ""
        self._celebrate = False
        self._fan_keys: set = set()
        # live transfer state
        self._tiles: dict = {}
        self._folders: dict = {}
        self._src_to_folder: dict = {}
        self._name_to_folder: dict = {}
        self._readback = True
        self._job_delete = False
        # custom selection ("Choose which ones")
        self._pick_kind = ""
        self._pick_scan = None
        self.host.cards_changed.connect(self._cards_changed)
        self._pick_grids: list = []
        set_palette(self.s.easy_palette)
        self.setStyleSheet(easy_qss())
        self.loader.ready.connect(self._thumb_ready)

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 18, 30, 16)
        root.setSpacing(8)

        # ------------------------------------------------ top bar
        top = QHBoxLayout()
        top.setSpacing(12)
        from ..app import make_icon

        logo = QPushButton()
        logo.setObjectName("Logo")
        logo.setIcon(make_icon())
        logo.setIconSize(QSize(36, 36))
        logo.setFixedSize(40, 40)
        top.addWidget(logo)
        name = QLabel("MMTX")
        name.setObjectName("EasyBrand")
        top.addWidget(name)
        top.addStretch(1)
        self.tab_group = QButtonGroup(self)
        self.tab_copy = self._tab("Copy my card", "card", 0)
        self.tab_arch = self._tab("Move old videos", "archive", 1)
        self.tab_photos = self._tab("Google Photos", "photos", PHOTOS_TAB)
        top.addWidget(self.tab_copy)
        top.addWidget(self.tab_arch)
        top.addWidget(self.tab_photos)
        top.addStretch(1)
        self.lib_btn = QPushButton("  Library")
        self.lib_btn.setObjectName("EasyTop")
        self.lib_btn.setIconSize(QSize(18, 18))
        self.lib_btn.setCursor(Qt.PointingHandCursor)
        self.lib_btn.setToolTip("See everything you've saved: search, play clips, export to a project")
        self.lib_btn.clicked.connect(lambda: self.host.open_library())
        top.addWidget(self.lib_btn)
        self.colors_btn = QPushButton("  Colors")
        self.colors_btn.setObjectName("EasyTop")
        self.colors_btn.setIconSize(QSize(18, 18))
        self.colors_btn.setCursor(Qt.PointingHandCursor)
        self.colors_btn.clicked.connect(self.open_colors)
        top.addWidget(self.colors_btn)
        self.help_btn = QPushButton("  Help")
        self.help_btn.setObjectName("EasyTop")
        self.help_btn.setIconSize(QSize(18, 18))
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.clicked.connect(self.open_help)
        top.addWidget(self.help_btn)
        self.adv_btn = QPushButton("  Advanced")
        self.adv_btn.setObjectName("EasyTop")
        self.adv_btn.setIconSize(QSize(18, 18))
        self.adv_btn.setCursor(Qt.PointingHandCursor)
        self.adv_btn.setToolTip("Every option: pick days, rename folders, verify an archive, settings")
        self.adv_btn.clicked.connect(lambda: self.host.set_easy(False))
        top.addWidget(self.adv_btn)
        root.addLayout(top)

        self.tracker = StepTracker(["Connect", "Choose folder", "Start"])
        trow = QHBoxLayout()
        trow.addStretch(1)
        trow.addWidget(self.tracker, 3)
        trow.addStretch(1)
        root.addLayout(trow)
        # two or more cards/devices: "All devices" + one chip each, with live status
        self.switcher = DeviceSwitcher()
        self.switcher.setVisible(False)
        self.switcher.picked.connect(self._switch_device)
        root.addWidget(self.switcher)

        # ------------------------------------------------ hero panel with one screen at a time
        self.hero = QFrame()
        self.hero.setObjectName("EasyHero")
        hv = QVBoxLayout(self.hero)
        hv.setContentsMargins(36, 24, 36, 24)
        self.stage = QStackedWidget()
        self.stage.addWidget(self._build_screen())
        self.stage.addWidget(self._build_running())
        self.stage.addWidget(self._build_pick())
        self.stage.currentChanged.connect(self._stage_sizes)
        hv.addWidget(self.stage)
        holder = QWidget()
        holder.setObjectName("EasyHolder")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 4, 0, 8)
        hl.addStretch(1)
        self.hero.setMinimumWidth(820)
        self.hero.setMaximumWidth(1120)
        hl.addWidget(self.hero, 10)
        hl.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("EasyScroll")
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        host.busy_changed.connect(lambda _b: self.refresh())
        host.card_scanning.connect(lambda _r: self.refresh())
        host.card_changed.connect(lambda _s: self.refresh())
        host.job_started.connect(self._job_started)
        host.job_event.connect(self._job_event)
        host.job_finished.connect(self._job_finished)
        if self.s.easy_tab not in (0, 1, PHOTOS_TAB):
            self.s.easy_tab = 0
        self.tab_group.button(self.s.easy_tab).setChecked(True)
        self._photos_setup()
        self._paint_icons()
        self._tab_changed(self.s.easy_tab)
        self._stage_sizes()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        paint_backdrop(p, self.width(), self.height())

    # ================================================================ colors
    def _paint_icons(self) -> None:
        self.lib_btn.setIcon(icon("photos", P.text, 20))
        self.colors_btn.setIcon(icon("palette", P.text, 20))
        self.help_btn.setIcon(icon("search", P.text, 20))
        self.adv_btn.setIcon(icon("settings", P.text, 20))
        for b in (self.tab_copy, self.tab_arch, self.tab_photos):
            b.setIcon(icon(b.property("glyph"), P.text, 22))

    def apply_palette(self) -> None:
        set_palette(self.s.easy_palette)
        self.setStyleSheet(easy_qss())
        self._paint_icons()
        for w in self.findChildren(QWidget):
            w.update()
        self.update()

    def set_palette(self, key: str) -> None:
        self.s.easy_palette = key
        self.s.save()
        self.apply_palette()

    def open_colors(self) -> None:
        PaletteDialog(self, self.s.easy_palette, self.set_palette).exec()

    def _stage_sizes(self, _i: int = 0) -> None:
        """A stacked widget is as tall as its tallest page; let only the visible page count."""
        for i in range(self.stage.count()):
            w = self.stage.widget(i)
            pol = QSizePolicy.Preferred if w is self.stage.currentWidget() else QSizePolicy.Ignored
            w.setSizePolicy(pol, pol)
            w.updateGeometry()
        self.stage.adjustSize()

    def _tab(self, text: str, ic: str, idx: int) -> QPushButton:
        b = QPushButton(f"  {text}")
        b.setObjectName("EasyTab")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setProperty("glyph", ic)
        b.setIconSize(QSize(20, 20))
        b.clicked.connect(lambda _c=False: self._tab_changed(idx))
        self.tab_group.addButton(b, idx)
        return b

    def _tab_changed(self, idx: int) -> None:
        self.s.easy_tab = idx
        self.s.save()
        self.tab_group.button(idx).setChecked(True)
        if self.host.busy and self.stage.currentWidget() is self.run_view:
            return
        self.tracker.set_labels(["Connect", "Choose folder", "Start"] if idx == 0
                                else PHOTOS_STEPS if idx == PHOTOS_TAB
                                else ["Videos folder", "Backup drive", "Start"])
        if idx == 1:
            self._scan_archive()
        self.refresh()

    # ================================================================ building the two views
    def _build_screen(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.art = Art()
        lay.addWidget(self.art, 0, Qt.AlignHCenter)
        self.fan = ThumbFan(self.loader)
        lay.addWidget(self.fan, 0, Qt.AlignHCenter)
        self.chip = DeviceChip()
        lay.addWidget(self.chip, 0, Qt.AlignHCenter)
        self.eyebrow = _lab("", "EasyEyebrow")
        lay.addWidget(self.eyebrow)
        self.title = _lab("", "EasyTitle")
        lay.addWidget(self.title)
        self.sub = _lab("", "EasySub")
        self.sub.setTextFormat(Qt.RichText)
        lay.addWidget(self.sub)
        self.howto = HowToTiles()
        lay.addWidget(self.howto)
        self.devices = DeviceGrid()
        self.devices.picked.connect(self._pick_device)
        lay.addWidget(self.devices, 0, Qt.AlignHCenter)
        self.guide = GuideView()
        lay.addWidget(self.guide)
        self.suggest = _lab("", "EasySuggest")
        self.suggest.setWordWrap(False)  # a wrapped, centered label negotiates itself too narrow
        self.suggest.setTextFormat(Qt.RichText)
        lay.addWidget(self.suggest, 0, Qt.AlignHCenter)
        self.name_box = NameBox()
        self.name_box.edited.connect(self._title_edited)
        lay.addWidget(self.name_box, 0, Qt.AlignHCenter)
        self.primary = Beacon("Continue")
        self.primary.clicked.connect(lambda: self._primary_action and self._primary_action())
        lay.addWidget(self.primary, 0, Qt.AlignHCenter)
        self.start = Beacon("Start", big=True)
        self.start.clicked.connect(lambda: self._primary_action and self._primary_action())
        lay.addWidget(self.start, 0, Qt.AlignHCenter)
        self.second = _ghost("", lambda: self._second_action and self._second_action())
        lay.addWidget(self.second, 0, Qt.AlignHCenter)
        self.foot_rows: list[tuple[QWidget, QLabel, QPushButton]] = []
        for i in range(2):
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)
            rl.addStretch(1)
            lab = _lab("", "EasyFoot", center=False)
            lab.setWordWrap(False)
            lab.setTextFormat(Qt.RichText)
            rl.addWidget(lab)
            ch = _link("Change", lambda _c=False, k=i: self._foot_actions[k] and self._foot_actions[k]())
            ch.setProperty("default_text", "Change")
            rl.addWidget(ch)
            rl.addStretch(1)
            lay.addWidget(row)
            self.foot_rows.append((row, lab, ch))
        lay.addStretch(1)
        self.screen_view = w
        return w

    def _build_running(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        self.r_title = _lab("", "EasyTitle")
        lay.addWidget(self.r_title)
        self.r_note = QLabel("")
        self.r_note.setObjectName("EasyNote")
        self.r_note.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.r_note, 0, Qt.AlignHCenter)
        row = QHBoxLayout()
        row.setSpacing(28)
        row.addStretch(1)
        self.ring = RingProgress(200)
        row.addWidget(self.ring)
        cur = QFrame()
        cur.setObjectName("NowCard")
        cl = QVBoxLayout(cur)
        cl.setContentsMargins(16, 16, 16, 16)
        cl.setSpacing(8)
        cl.addWidget(_lab("NOW", "EasyEyebrow", center=False))
        self.r_thumb = NowThumb(288, 162)
        cl.addWidget(self.r_thumb)
        self.r_name = _lab("", "NowName", center=False)
        self.r_eta = _lab("", "NowEta", center=False)
        cl.addWidget(self.r_name)
        cl.addWidget(self.r_eta)
        row.addWidget(cur)
        row.addStretch(1)
        lay.addLayout(row)
        self.r_grid = EasyClipGrid(self.loader, "live")
        lay.addWidget(self.r_grid)
        self.r_folders = FolderLiveList(self.loader)
        lay.addWidget(self.r_folders)
        self.r_stop = _ghost("Stop", self._stop)
        self.r_stop.setObjectName("EasyStop")
        lay.addWidget(self.r_stop, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        self.run_view = w
        return w

    def _build_pick(self) -> QWidget:
        """'Choose which ones': tap tiles (copy) or rows (move) to choose, optional delete, then go."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(_lab("Step 3 of 3", "EasyEyebrow"))
        self.p_title = _lab("", "EasyTitle")
        lay.addWidget(self.p_title)
        self.p_sub = _lab("", "EasySub")
        lay.addWidget(self.p_sub)
        tools = QHBoxLayout()
        tools.setSpacing(10)
        self.p_all = _tool("Choose all", lambda: self._pick_all(True))
        self.p_none = _tool("Choose none", lambda: self._pick_all(False))
        self.p_new = _tool("Only new ones", self._pick_only_new)
        for b in (self.p_all, self.p_none, self.p_new):
            tools.addWidget(b)
        tools.addStretch(1)
        self.p_count = _lab("", "PickCount", center=False)
        self.p_count.setWordWrap(False)
        tools.addWidget(self.p_count)
        lay.addLayout(tools)
        self.p_days_box = QWidget()
        self.p_days = QVBoxLayout(self.p_days_box)
        self.p_days.setContentsMargins(0, 4, 0, 0)
        self.p_days.setSpacing(4)
        lay.addWidget(self.p_days_box)
        self.p_folders = FolderLiveList(self.loader, mode="pick")
        self.p_folders.picked_changed.connect(self._pick_changed)
        lay.addWidget(self.p_folders)
        self.p_del = ToggleCard()
        self.p_del.toggled.connect(lambda _on: self._pick_changed())
        lay.addWidget(self.p_del)
        self.p_go = Beacon("Copy", big=True)
        self.p_go.clicked.connect(self._pick_go)
        lay.addWidget(self.p_go, 0, Qt.AlignHCenter)
        self.p_back = _ghost("Back", self._pick_back)
        lay.addWidget(self.p_back, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        self.pick_view = w
        return w

    # ================================================================ one screen renderer
    def _show(self, *, art: str = "", fan: Optional[list] = None, eyebrow: str = "", title: str = "",
              tone: str = "", sub: str = "", howto: Optional[list] = None, suggest: str = "",
              primary: str = "", start: str = "", action=None, second: str = "", second_action=None,
              foot: Optional[list] = None, steps: Optional[list[str]] = None, celebrate: bool = False,
              chip: Optional[tuple] = None, devices: bool = False, guide: Optional[dict] = None,
              name: bool = False, art_device: str = "") -> None:
        if title != self.title.text() or self.stage.currentWidget() is not self.screen_view:
            self.scroll.verticalScrollBar().setValue(0)  # a new screen starts at the top
        self.stage.setCurrentWidget(self.screen_view)
        if hasattr(self, "years"):
            self.years.setVisible(False)  # only the Google Photos Start screen shows the year chart
        self.art.setVisible(bool(art))
        self.art.device = art_device
        self.chip.setVisible(bool(chip))
        if chip:
            self.chip.set_device(*chip)
        self.devices.setVisible(devices)
        if devices:
            self.devices.set_last(self.s.easy_device)
        self.guide.setVisible(bool(guide))
        if guide:
            self.guide.set_guide(guide["routes"], guide.get("note", ""))
        self.name_box.setVisible(name)
        if name:
            self.name_box.set_text(self._shoot_title)
            self._update_name_preview()
            if self._name_focus:  # first time this card's Start screen shows: typing replaces the default
                self._name_focus = False
                QTimer.singleShot(0, self.name_box.focus_and_select)
        if art:
            self.art.set_kind(art)
            if celebrate:
                self.art.celebrate()
        self.fan.setVisible(bool(fan))
        if fan:
            items = []
            self._fan_keys = set()
            for path, cands, glyph in fan[:5]:
                key = self.loader.key_for(path)
                self._fan_keys.add(key)
                self.loader.request(key, cands, path)
                items.append((key, glyph))
            self.fan.set_items(items, max(0, len(fan) - 5))
        self.eyebrow.setText(eyebrow)
        self.eyebrow.setVisible(bool(eyebrow))
        self.title.setText(title)
        self.title.setProperty("tone", tone)
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        self.howto.setVisible(bool(howto))
        if howto:
            self.howto.set_steps(howto)
        self.suggest.setText(suggest)
        self.suggest.setVisible(bool(suggest))
        busy = self.host.busy
        self._primary_action = action
        self.primary.setVisible(bool(primary))
        self.primary.reset()
        self.primary.set_text(primary)
        self.primary.setEnabled(bool(primary) and not busy)
        self.start.setVisible(bool(start))
        self.start.reset()
        self.start.set_text(start)
        self.start.setEnabled(bool(start) and not busy)
        self.second.setText(second)
        self.second.setVisible(bool(second))
        self.second.setEnabled(True)
        self._second_action = second_action
        foot = foot or []
        for i, (row, lab, ch) in enumerate(self.foot_rows):
            entry = foot[i] if i < len(foot) else ("", None)
            text, act = entry[0], entry[1]
            ch.setText(entry[2] if len(entry) > 2 else ch.property("default_text"))
            row.setVisible(bool(text) or act is not None)
            lab.setText(text)
            lab.setVisible(bool(text))
            ch.setVisible(act is not None)
            ch.setEnabled(True)
            self._foot_actions[i] = act
        if steps:
            self.tracker.set_states(steps)

    def _thumb_ready(self, key: str) -> None:
        if key in self._fan_keys:
            self.fan.update()
        for it in self._tiles.get("_by_key", {}).get(key, ()):
            self.r_grid.refresh(it)
        if any(f.get("key") == key for f in self._folders.values()):
            self.r_folders.update()
        if self.stage.currentWidget() is self.pick_view:
            for _d, _f, grid, _h, _s in self._pick_grids:
                grid.viewport().update()
            self.p_folders.update()

    def refresh(self) -> None:
        if self._advancing:
            return  # the green "done" moment; the next step renders right after it
        if self.stage.currentWidget() is self.pick_view:
            same_tab = self.s.easy_tab != PHOTOS_TAB and (self._pick_kind == "archive") == (self.s.easy_tab == 1)
            same_card = self._pick_kind == "archive" or all(
                (e := self.host.cards.get(sc.root)) is not None and e.scan is sc for sc in self._pick_scans)
            if same_tab and same_card and not self.host.busy:
                self._pick_changed()
                return
        if self.stage.currentWidget() is self.run_view and (self.host.busy or self._started_here):
            return
        self._update_switcher()
        if self.s.easy_tab == PHOTOS_TAB:
            self._render_photos()
        elif self.s.easy_tab == 1:
            self._render_archive()
        elif self._multi():
            self._render_copy_all()
        else:
            self._render_copy()

    def on_volumes(self, vols: list) -> None:
        self._vols = list(vols)
        if not self.host.cards:
            self._title_root = None  # everything unplugged: the next shoot starts from the default name
            self._view_all = True
        if not self.host.busy:
            self.refresh()

    # ================================================================ several cards / devices
    def _multi(self) -> bool:
        return self._view_all and len(self.host.cards) >= 2 and self.s.easy_tab == 0

    def _switch_device(self, root: str) -> None:
        if self.host.busy and self.stage.currentWidget() is self.run_view:
            return  # the running view already shows every device
        if root:
            self._view_all = False
            self.host.select_card(root)
        else:
            self._view_all = True
        if self.stage.currentWidget() is self.pick_view:
            self.stage.setCurrentWidget(self.screen_view)
            self._clear_pick_days()
        self.refresh()

    def _update_switcher(self) -> None:
        cards = list(self.host.cards.values())
        show = len(cards) >= 2 and self.s.easy_tab == 0
        self.switcher.setVisible(show)
        if not show:
            return
        busy = any(e.copying for e in cards)
        new = sum(e.new_files for e in cards)
        reading = sum(1 for e in cards if e.state in ("waiting", "scanning"))
        if busy:
            done = sum(e.job_done for e in cards)
            total = sum(e.job_total for e in cards)
            all_status, tone = f"Copying {done}/{total}", "busy"
        elif reading:
            all_status, tone = f"Reading {reading} of {len(cards)}…", "busy"
        elif new:
            all_status, tone = f"{new} new on {sum(1 for e in cards if e.new_files)}", "new"
        else:
            all_status, tone = "All saved", "done"
        rows = [("", "all", f"All {len(cards)} devices", all_status, tone)]
        for e in cards:
            rows.append((e.root, e.icon, e.name, *e.status()))
        self.switcher.set_devices(rows, "" if self._view_all else self.host.card_root)

    def _cards_changed(self) -> None:
        self._update_switcher()
        sig = tuple((e.root, e.state, e.new_files) for e in self.host.cards.values())
        if sig != self._cards_sig:
            self._cards_sig = sig
            if not self.host.busy and self.stage.currentWidget() is not self.run_view:
                QTimer.singleShot(0, self.refresh)

    def _render_copy_all(self) -> None:
        cards = list(self.host.cards.values())
        ready = self.host.ready_cards()
        reading = [e for e in cards if e.state in ("waiting", "scanning")]
        dest = self.s.offload_dest
        dest_ok = bool(dest) and free_bytes(dest) >= 0
        saving = [(f"Saving to  <b>{dest}</b>", self._pick_dest)] if dest else []
        celebrate, self._celebrate = self._celebrate, False
        n = len(cards)
        if not ready:
            if reading:
                self._show(art="search", art_device=reading[0].icon, eyebrow="Step 1 of 3",
                           title=f"Reading your {n} devices…",
                           sub=f"{n - len(reading)} of {n} read. Each one appears above as soon as it's ready.",
                           steps=["active", "todo", "todo"])
            else:
                self._show(art="empty", eyebrow="Step 1 of 3", title="No videos on these devices", tone="warn",
                           sub="None of the connected cards or devices has camera videos or photos on it.",
                           second="Copy from a folder instead", second_action=self._pick_source,
                           steps=["active", "todo", "todo"])
            return
        names = " + ".join(e.name for e in ready)
        if not dest_ok:
            self._render_dest(ready[0].scan, None, names, dest)
            return
        self._plans = {e.root: self._compute_plan(e.scan) for e in ready}
        with_new = [e for e in ready if self._plans[e.root]]
        days: dict = {}
        for e in with_new:
            for d, f in self._plans[e.root]:
                days.setdefault(d.day, (d, f))
        self.plan = sorted(days.values(), key=lambda df: df[0].day, reverse=True)
        still = (f" Still reading {', '.join(e.name for e in reading)}." if reading else "")
        if not with_new:
            self._show(art="done", eyebrow="All set", title=f"Everything on these {len(ready)} devices is saved",
                       tone="ok", sub="You can remove them. Next time, only new videos are copied." + still,
                       second="Choose videos to copy again or delete", second_action=self._open_pick,
                       foot=saving, steps=["done", "done", "done"], celebrate=celebrate)
            return
        media = self._media([d for e in with_new for d, _ in self._plans[e.root]])
        n_vid = sum(1 for _, _, g in media if g == "film")
        n_pic = len(media) - n_vid
        what = " and ".join(x for x in (_plural(n_vid, "new video") if n_vid else "",
                                        _plural(n_pic, "photo") if n_pic else "") if x)
        size = sum(f.size for e in with_new for d, _ in self._plans[e.root] for f in d.files)
        k = len(with_new)
        where = f"on {k} devices" if k > 1 else f"on {with_new[0].name}"
        self._show(fan=media, eyebrow="Step 3 of 3", title=f"{what[0].upper() + what[1:]} {where}",
                   name=any(d.day not in self._existing for d, _ in self.plan),
                   sub=f"{human_bytes(size)} from {_plural(len(self.plan), 'day')} of shooting. Same-day videos from "
                       "every device go into the same day folder, and each file is checked after it's copied."
                       + still,
                   start=f"Copy all {k} devices" if k > 1 else "Copy everything", action=self._go_copy,
                   second="Choose which ones", second_action=self._open_pick,
                   foot=saving, steps=["done", "done", "active"])

    # ================================================================ copy my card
    def _compute_plan(self, scan=None) -> list:
        scan = scan or self.host.card_scan
        if not scan:
            return []
        dest = self.s.offload_dest
        if self._title_root is None:  # first card/device of a session: start from the default name
            self._title_root = "session"
            self._shoot_title = self.s.default_title
            self._day_titles = {}
            self._name_focus = True
        existing = existing_day_folders(dest) if dest else {}
        self._existing = existing
        opts = offload_options(self.s, delete_source=False)  # Copy everything never deletes from a card
        dev = device_folders(self.s, scan)
        plan = []
        for day in scan.days:
            folder = self._folder_for(day)
            if dest:
                present, total, _ = day_presence(day, os.path.join(dest, folder), dev, opts)
                if present >= total:
                    continue
            plan.append((day, folder))
        return plan

    # ================================================================ shoot titles
    def _folder_for(self, day) -> str:
        """Existing folder for that date wins (new files join it); otherwise date + the shoot's name."""
        if day.day in self._existing:
            t = title_from_existing(self._existing[day.day][0])
        else:
            t = (self._day_titles.get(day.day) or self._shoot_title or "").strip() or self.s.default_title
        return day_folder_name(day.day, t, self.s.spaces_to_underscores)

    def _title_edited(self, text: str) -> None:
        self._shoot_title = text
        self.plan = [(d, self._folder_for(d)) for d, _f in self.plan]
        self._plans = {r: [(d, self._folder_for(d)) for d, _f in pl] for r, pl in self._plans.items()}
        self._update_name_preview()

    def _update_name_preview(self) -> None:
        new = [f for d, f in self.plan if d.day not in self._existing]
        joined = [(d, f) for d, f in self.plan if d.day in self._existing]
        bits = []
        if new:
            more = f" and {_plural(len(new) - 1, 'more day')}, each with its own date" if len(new) > 1 else ""
            bits.append(f"Saves as <b>{new[0]}</b>{more}.")
        if joined:
            d, f = joined[0]
            others = f" (+{len(joined) - 1} more)" if len(joined) > 1 else ""
            bits.append(f"{d.day:%b} {d.day.day} already has a folder, <b>{f}</b>{others}: new files go there.")
        if not (self._shoot_title or "").strip():
            bits.append(f"Empty name: <b>{self.s.default_title}</b> is used.")
        self.name_box.preview.setText("<br>".join(bits))

    def _scan_chip(self, scan) -> Optional[tuple]:
        if not scan or not scan.files:
            return None
        counts: dict = {}
        for f in scan.files:
            if f.role == "media":
                counts[f.profile] = counts.get(f.profile, 0) + 1
        prof = max(counts, key=counts.get) if counts else next(iter(scan.devices))
        label = scan.devices.get(prof, "Camera")
        kind = sources.icon_for_profile(prof, label)
        dev = sources.device_of(scan.root)
        vol = next((v for v in self._vols if v.root == scan.root), None)
        if dev is not None:
            kind = dev.kind if dev.kind != "card" else kind
        usb = dev is not None or (vol is not None and vol.portable)
        others = len(counts) - 1
        text = label + (f" + {others} more" if others > 0 else "") + ("  ·  over USB" if usb else "")
        return kind, text

    # ================================================================ step 1: device picker
    def _pick_device(self, kind: str) -> None:
        self._device_pick = kind
        self.s.easy_device = kind
        self.s.save()
        self.refresh()

    def _all_devices(self) -> None:
        self._device_pick = ""
        self.refresh()

    @staticmethod
    def _media(days) -> list:
        out = []
        for d in days:
            for f in d.files:
                if f.role == "media":
                    out.append((f.path, f.thumbs, "photo" if f.kind in ("photo", "raw", "360photo") else "film"))
        return out

    def _render_copy(self) -> None:
        scan = self.host.card_scan
        dest = self.s.offload_dest
        dest_ok = bool(dest) and free_bytes(dest) >= 0
        self.plan = self._compute_plan() if scan else []
        saving = [(f"Saving to  <b>{dest}</b>", self._pick_dest)] if dest else []
        celebrate, self._celebrate = self._celebrate, False

        if self.host.scanning:
            vol = next((v for v in self._vols if v.root == self.host.card_root), None)
            kind = ((vol.icon or sources.device_kind_for(vol.camera or "")) if vol else "") or "card"
            thing = {"phone": "phone", "drone": "drone", "action": "GoPro", "360": "Insta360",
                     "cinema": "camera"}.get(kind, "card")
            usb = vol is not None and vol.portable
            self._show(art="search", art_device=kind, eyebrow="Step 1 of 3", title=f"Reading your {thing}…",
                       chip=(kind, (vol.camera or vol.name) + ("  ·  over USB" if usb else "")) if vol else None,
                       sub=("Over USB this can take a little longer." if usb else "This takes a few seconds."),
                       steps=["active", "todo", "todo"])
            return
        if not scan:
            note = self.host.card_note
            if note == "empty":
                self._show(art="empty", eyebrow="Step 1 of 3", title="No videos on this card", tone="warn",
                           sub="This card doesn't have any camera videos or photos on it. Try another card.",
                           second="Copy from a folder instead", second_action=self._pick_source,
                           steps=["active", "todo", "todo"])
                return
            if note == "unreadable":
                self._show(art="empty", eyebrow="Step 1 of 3", title="That card couldn't be read", tone="warn",
                           sub="Take it out and put it back in. If it still doesn't appear, open Help.",
                           second="Copy from a folder instead", second_action=self._pick_source,
                           foot=saving, steps=["active", "todo", "todo"])
                return
            if self._device_pick:
                g = device_guide(self._device_pick)
                self._show(art="device", art_device=self._device_pick, eyebrow="Step 1 of 3", title=g["title"],
                           sub=g["sub"] + " MMTX finds it by itself.", guide=g,
                           second="Copy from a folder instead", second_action=self._pick_source,
                           foot=[("", self._all_devices, "Pick a different device")] + saving,
                           steps=["active", "todo", "todo"])
                return
            self._show(eyebrow="Step 1 of 3", title="What are you copying from?",
                       sub="Plug in its memory card and MMTX finds it by itself. "
                           "Tap your camera to see how to connect it.",
                       devices=True, second="Copy from a folder instead", second_action=self._pick_source,
                       foot=saving, steps=["active", "todo", "todo"])
            return
        cams = " + ".join(scan.devices.values())
        chip = self._scan_chip(scan)
        if not dest_ok:
            self._render_dest(scan, chip, cams, dest)
            return
        self._render_ready(scan, chip, saving, celebrate)

    def _render_dest(self, scan, chip, cams: str, dest: str) -> None:
        if True:
            self._suggestion = suggest_place(self._vols, "MMTX Videos", avoid=scan.root)
            example = day_folder_name(scan.days[0].day, self.s.default_title, self.s.spaces_to_underscores)
            self.art.folder_names = [day_folder_name(d.day, self.s.default_title, self.s.spaces_to_underscores)
                                     for d in scan.days[:3]]
            sub = (f"Found your <b>{cams}</b>. Pick the folder where your videos should live.<br>"
                   f"Inside it, MMTX makes one folder per shooting day, like <b>{example}</b>, "
                   "and puts the videos straight in. You only choose this once.")
            if dest:
                sub = (f"The folder you chose before (<b>{dest}</b>) isn't available right now.<br>"
                       "Connect that drive, or choose a new folder.")
            if self._suggestion:
                where, free = self._suggestion
                self._show(art="choose", eyebrow="Step 2 of 3", title="Choose where to save your videos", sub=sub,
                           chip=chip, suggest=f"Suggested:  <b>{where}</b>   ·   {human_bytes(free)} free",
                           primary="Use this folder", action=self._use_suggestion,
                           second="Choose a different folder", second_action=self._pick_dest,
                           steps=["done", "active", "todo"])
            else:
                self._show(art="choose", eyebrow="Step 2 of 3", title="Choose where to save your videos", sub=sub,
                           chip=chip, primary="Choose folder", action=self._pick_dest, steps=["done", "active", "todo"])

    def _render_ready(self, scan, chip, saving: list, celebrate: bool) -> None:
        if not self.plan:
            self._show(art="done", eyebrow="All set", title="Everything on this card is saved", tone="ok", chip=chip,
                       sub="You can remove the card. Next time you insert it, only new videos are copied.",
                       second="Choose videos to copy again or delete from the card", second_action=self._open_pick,
                       foot=saving, steps=["done", "done", "done"], celebrate=celebrate)
            return
        media = self._media([d for d, _ in self.plan])
        n_vid = sum(1 for _, _, g in media if g == "film")
        n_pic = len(media) - n_vid
        what = " and ".join(x for x in (_plural(n_vid, "new video") if n_vid else "",
                                        _plural(n_pic, "photo") if n_pic else "") if x)
        days = len(self.plan)
        size = sum(f.size for d, _ in self.plan for f in d.files)
        phone = any(f.profile == "phone" for d, _ in self.plan for f in d.files)
        recent = (f" Showing the last {_plural(self.s.phone_recent_days, 'day')} from the phone."
                  if phone and self.s.phone_recent_days > 0 else "")
        self._show(fan=media, eyebrow="Step 3 of 3", title=f"{what[0].upper() + what[1:]} ready to copy",
                   chip=chip, name=any(d.day not in self._existing for d, _ in self.plan),
                   sub=f"{human_bytes(size)} from {_plural(days, 'day')} of shooting. "
                       f"Each file is checked after it's copied.{recent}",
                   start="Copy everything", action=self._go_copy,
                   second="Choose which ones", second_action=self._open_pick,
                   foot=saving, steps=["done", "done", "active"])

    def _use_suggestion(self) -> None:
        if not self._suggestion:
            return
        where = self._suggestion[0]
        try:
            os.makedirs(where, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{where}<br><br>{e.strerror or e}<br><br>"
                       "Choose a different folder instead.", ["OK"]).ask()
            return
        self._finish_step(self.primary, "Folder chosen", lambda: self.set_dest(where))

    def _pick_source(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Choose the folder with your videos", os.path.expanduser("~"))
        if p:
            self.host.scan_card(os.path.normpath(p))

    def _pick_dest(self) -> None:
        start = self.s.offload_dest if os.path.isdir(self.s.offload_dest) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Choose where to save your videos", start)
        if p:
            p = os.path.normpath(p)
            self._finish_step(self.primary, "Folder chosen", lambda: self.set_dest(p))

    def set_dest(self, path: str) -> None:
        self.s.offload_dest = path
        self.s.save()
        self.host.update_drives()
        self.refresh()

    def _go_copy(self) -> None:
        if self._multi():
            self._start_copy({r: pl for r, pl in self._plans.items() if pl}, delete=False, beacon=self.start)
        elif self.host.card_scan is not None:
            self._start_copy({self.host.card_scan.root: self.plan}, delete=False, beacon=self.start)

    def _scan_for(self, root: str):
        e = self.host.cards.get(root)
        return e.scan if e is not None else None

    def _start_copy(self, selections: dict, delete: bool, beacon) -> None:
        """selections: card root -> [(day, folder)]. Several roots become one job, copied side by side."""
        selections = {r: sel for r, sel in selections.items() if sel and self._scan_for(r) is not None}
        if not selections or self.host.busy:
            return
        dest = self.s.offload_dest
        if any(is_inside(dest, r) for r in selections if not r.startswith("portable://")):
            EasyDialog(self, "warn", "That folder is on the card itself",
                       "Choose a folder on your computer's drive instead (click Change).", ["OK"]).ask()
            return
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{dest}<br><br>{e.strerror or e}", ["OK"]).ask()
            return
        jobs = []
        for root, sel in selections.items():
            sc = self._scan_for(root)
            j = plan_offload(sc, sel, dest, device_folders(self.s, sc), offload_options(self.s, delete))
            if j.groups:
                jobs.append(j)
        if not jobs:
            return
        if len(jobs) == 1:
            job = jobs[0]
            if not self.host.preflight(job, dest, easy=True):
                return
            self.host.resolve_workers(job, job.groups[0].source_root, dest)
        else:
            roots = [j.groups[0].source_root for j in jobs]
            job = merge_offload_jobs(jobs, self.host.source_limits(roots, dest))
            if not self.host.preflight(job, dest, easy=True):
                return
        self._job_delete = delete
        self._finish_step(beacon, "Started", lambda: self._launch(job, dest))

    # ================================================================ move old videos
    def _arch_src(self) -> str:
        return self.s.archive_source or self.s.offload_dest

    def _pick_arch_src(self) -> None:
        start = self._arch_src() if os.path.isdir(self._arch_src()) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Choose the folder where your videos are saved", start)
        if p:
            p = os.path.normpath(p)
            self._finish_step(self.primary, "Folder chosen", lambda: self.set_archive_source(p))

    def set_archive_source(self, path: str) -> None:
        self.s.archive_source = path
        self.s.save()
        self.host.update_drives()
        self._scan_archive()

    def _pick_arch_dst(self) -> None:
        start = self.s.archive_dest if os.path.isdir(self.s.archive_dest) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Choose your backup drive", start)
        if p:
            p = os.path.normpath(p)
            self._finish_step(self.primary, "Backup drive chosen", lambda: self.set_archive_dest(p))

    def _use_backup_suggestion(self) -> None:
        if not self._suggestion:
            return
        where = self._suggestion[0]
        try:
            os.makedirs(where, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{where}<br><br>{e.strerror or e}", ["OK"]).ask()
            return
        self._finish_step(self.primary, "Backup drive chosen", lambda: self.set_archive_dest(where))

    def set_archive_dest(self, path: str) -> None:
        self.s.archive_dest = path
        self.s.save()
        self.host.update_drives()
        self._scan_archive()

    def _scan_archive(self) -> None:
        src = self._arch_src()
        if not src or not os.path.isdir(src):
            self.arch_folders, self.arch_scanned = [], False
            self.refresh()
            return
        if self.arch_worker and self.arch_worker.isRunning():
            return
        self.arch_scanned = False
        arch = self.s.archive_dest or None
        w = Worker(lambda emit: scan_working(src, exclude=arch), self)
        w.bridge.finished.connect(self._arch_done_scan)
        w.bridge.failed.connect(lambda _tb: self._arch_done_scan([]))
        self.arch_worker = w
        w.start()
        self.refresh()

    def _arch_done_scan(self, folders: list) -> None:
        self.arch_folders = folders
        self.arch_scanned = True
        self.refresh()

    def _arch_candidates(self) -> list:
        cutoff = date.fromtimestamp(time.time() - int(self.s.older_than_days) * 86400)
        arch = self.s.archive_dest
        return [w for w in self.arch_folders
                if w.day is not None and w.day < cutoff and not (arch and is_inside(arch, w.path))]

    def _render_archive(self) -> None:
        src, dst = self._arch_src(), self.s.archive_dest
        src_ok = bool(src) and os.path.isdir(src)
        dst_ok = bool(dst) and free_bytes(dst) >= 0
        age = int(self.s.older_than_days)
        celebrate, self._celebrate = self._celebrate, False
        foot = []
        if src:
            foot.append((f"Videos folder  <b>{src}</b>", self._pick_arch_src))
        if dst:
            foot.append((f"Backup drive  <b>{dst}</b>", self._pick_arch_dst))
        if not src_ok:
            self._show(art="choose", eyebrow="Step 1 of 3", title="Where are your videos saved now?",
                       sub="Choose the folder MMTX copies your cards into. That's where the old day folders are.",
                       primary="Choose folder", action=self._pick_arch_src, steps=["active", "todo", "todo"])
            return
        if not dst_ok:
            self._suggestion = suggest_place(self._vols, "MMTX Backup", avoid=src)
            sub = ("Old day folders move here to free up space on this computer. "
                   "MMTX sorts them into a folder for each year.")
            if dst:
                sub = f"The backup drive you chose before (<b>{dst}</b>) isn't connected. Connect it, or choose another."
            if self._suggestion:
                where, free = self._suggestion
                self._show(art="backup", eyebrow="Step 2 of 3", title="Choose your backup drive", sub=sub,
                           suggest=f"Suggested:  <b>{where}</b>   ·   {human_bytes(free)} free",
                           primary="Use this drive", action=self._use_backup_suggestion,
                           second="Choose a different folder", second_action=self._pick_arch_dst,
                           foot=foot[:1], steps=["done", "active", "todo"])
            else:
                self._show(art="backup", eyebrow="Step 2 of 3", title="Choose your backup drive", sub=sub,
                           primary="Choose drive", action=self._pick_arch_dst, foot=foot[:1],
                           steps=["done", "active", "todo"])
            return
        if not self.arch_scanned:
            self._show(art="search", eyebrow="Step 3 of 3", title="Looking for old videos…",
                       sub="This takes a few seconds.", steps=["done", "done", "todo"])
            return
        cands = self._arch_candidates()
        if not cands:
            self._show(art="done", eyebrow="All set", title="Nothing to move right now", tone="ok",
                       sub=f"Every day folder is newer than {_plural(age, 'day')}. Folders move to the backup once "
                           "they're older than that.", foot=foot, steps=["done", "done", "done"], celebrate=celebrate,
                       second="Choose folders to move anyway" if self._pickable_folders() else "",
                       second_action=self._open_pick)
            return
        posters = [(w.poster[0][0], w.poster, "folder") for w in cands if w.poster]
        n = len(cands)
        size = human_bytes(sum(w.size for w in cands))
        verb = "move" if self.s.archive_move else "copy"
        self._show(fan=posters, eyebrow="Step 3 of 3", title=f"{_plural(n, 'day folder')} ready to {verb}",
                   sub=f"Older than {_plural(age, 'day')}, {size} in total. Each file is copied to the backup and checked"
                       + (" before it's removed from this computer." if self.s.archive_move else "."),
                   start=f"{'Move' if self.s.archive_move else 'Copy'} all {n}" if n > 1 else
                   ("Move it" if self.s.archive_move else "Copy it"), action=self._go_archive,
                   second="Choose which ones", second_action=self._open_pick,
                   foot=foot, steps=["done", "done", "active"])

    def _pickable_folders(self) -> list:
        arch = self.s.archive_dest
        return [w for w in self.arch_folders if w.day is not None and not (arch and is_inside(arch, w.path))]

    def _go_archive(self) -> None:
        self._start_archive(self._arch_candidates(), self.s.archive_move, beacon=self.start)

    def _start_archive(self, cands: list, move: bool, beacon) -> None:
        src, arch = self._arch_src(), self.s.archive_dest
        if not cands or not arch or self.host.busy:
            return
        try:
            os.makedirs(arch, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{arch}<br><br>{e.strerror or e}", ["OK"]).ask()
            return
        n = len(cands)
        if move:
            msg = (f"<b>{_plural(n, 'day folder')}</b> will be copied to <b>{arch}</b> and checked file by file. "
                   "Then they're removed from this computer to free up space.<br><br>"
                   "If any file doesn't check out, that folder stays here.")
            if EasyDialog(self, "question", "Move these folders?", msg, ["Move them", "Cancel"]).ask() != 0:
                return
        job = plan_archive(cands, arch, self.s.archive_layout, move, self.s.algo,
                           verify_readback=self.s.verify_readback, uncached=self.s.uncached)
        if not self.host.preflight(job, arch, easy=True):
            return
        self.host.resolve_workers(job, src, arch)
        self._job_delete = move
        self._finish_step(beacon, "Started", lambda: self._launch(job, arch))

    # ================================================================ choose which ones
    def _open_pick(self) -> None:
        if self.host.busy:
            return
        if self.s.easy_tab == 1:
            self._open_pick_archive()
        else:
            self._open_pick_copy()

    def _clear_pick_days(self) -> None:
        while self.p_days.count():
            w = self.p_days.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._pick_grids = []

    def _enter_pick(self, kind: str) -> None:
        self._pick_kind = kind
        for b in (self.p_back, self.p_all, self.p_none, self.p_new, self.p_del):
            b.setEnabled(True)
        self.p_go.reset()
        self.tracker.set_states(["done", "done", "active"])
        self.stage.setCurrentWidget(self.pick_view)
        self._pick_changed()

    def _open_pick_copy(self) -> None:
        scans = [e.scan for e in self.host.ready_cards()] if self._multi() else (
            [self.host.card_scan] if self.host.card_scan else [])
        if not scans:
            return
        dest = self.s.offload_dest
        self._existing = existing_day_folders(dest) if dest else {}
        opts = offload_options(self.s, False)
        self._clear_pick_days()
        self._pick_where = {}
        for scan in scans:
            self._pick_device_days(scan, dest, opts, header=len(scans) > 1)
        self._pick_scans = scans
        self._pick_scan = scans[0]
        self._finish_open_pick_copy()

    def _pick_device_days(self, scan, dest: str, opts, header: bool) -> None:
        dev = device_folders(self.s, scan)
        if header:
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 18, 0, 0)
            chip = DeviceChip()
            kind, text = self._scan_chip(scan) or ("card", scan.root)
            chip.set_device(kind, text)
            bl.addWidget(chip)
            bl.addStretch(1)
            self.p_days.addWidget(box)
        for day in scan.days:
            folder = self._folder_for(day)
            present = day_presence(day, os.path.join(dest, folder), dev, opts)[2] if dest else set()
            clips: dict = {}
            for f in day.files:
                k = (f.profile, f.key)
                if f.role == "media" and (k not in clips or f.size > clips[k].size):
                    clips[k] = f
            if not clips:
                continue
            grid = EasyClipGrid(self.loader, "pick")
            n_vid = n_pic = 0
            for k, f in sorted(clips.items(), key=lambda kv: (kv[1].capture, kv[1].name)):
                key = self.loader.key_for(f.path)
                self.loader.request(key, f.thumbs, f.path)
                info = clip_info(f, key)
                members = [m for m in day.files if (m.profile, m.key) == k]
                info["size"] = sum(m.size for m in members)
                info["present"] = f.path in present
                info["picked"] = not info["present"]
                info["clip"] = k
                grid.add_clip(info)
                if f.kind in ("video", "360"):
                    n_vid += 1
                else:
                    n_pic += 1
            grid.picked_changed.connect(self._pick_changed)
            head = QWidget()
            hl = QHBoxLayout(head)
            hl.setContentsMargins(4, 14, 4, 2)
            hl.setSpacing(12)
            col = QVBoxLayout()
            col.setSpacing(0)
            top = QHBoxLayout()
            top.setSpacing(12)
            top.addWidget(_lab(f"{day.day:%A, %B} {day.day.day}", "PickDay", center=False))
            what = ", ".join(x for x in (_plural(n_vid, "video") if n_vid else "",
                                         _plural(n_pic, "photo") if n_pic else "") if x)
            where = _lab("", "PickDaySub", center=False)
            if day.day not in self._existing:
                ed = QLineEdit(self._day_titles.get(day.day) or self._shoot_title)
                ed.setObjectName("PickTitle")
                ed.setMaxLength(60)
                ed.setFixedWidth(260)
                ed.setPlaceholderText(self.s.default_title)
                ed.setToolTip("Name for this day's folder")
                ed.textEdited.connect(lambda t, d=day, src=ed: self._day_title_edited(d, t, src))
                top.addWidget(ed)
            top.addStretch(1)
            col.addLayout(top)
            where.setText(f"{what}  ·  {human_bytes(day.size)}  ·  saves to {folder}"
                          + ("  (already there)" if day.day in self._existing else ""))
            self._pick_where.setdefault(day.day, []).append(
                (where, what, day, ed if day.day not in self._existing else None))
            col.addWidget(where)
            hl.addLayout(col, 1)
            hl.addWidget(_tool("All", lambda _c=False, g=grid: g.set_all(True)))
            hl.addWidget(_tool("None", lambda _c=False, g=grid: g.set_all(False)))
            self.p_days.addWidget(head)
            self.p_days.addWidget(grid)
            grid.fit()
            self._pick_grids.append((day, folder, grid, head, scan))

    def _finish_open_pick_copy(self) -> None:
        self.p_title.setText("Choose what to copy")
        self.p_sub.setText("Tap a video to add it or take it out. New videos are already chosen; "
                           "videos tagged Saved are already on your computer.")
        self.p_new.setVisible(True)
        self.p_days_box.setVisible(True)
        self.p_folders.setVisible(False)
        self.p_del.head.setText("Delete them from the card after copying")
        self.p_del.text.setText("Each chosen video is deleted from the card only after its copy is checked. "
                                "If anything from a day doesn't check out, nothing from that day is deleted. "
                                "Videos you don't choose stay on the card.")
        self.p_del.setChecked(False)
        self._enter_pick("copy")

    def _open_pick_archive(self) -> None:
        folders = self._pickable_folders()
        if not folders:
            return
        old = {w.path for w in self._arch_candidates()}
        rows = []
        for w in folders:
            key = ""
            if w.poster:
                key = self.loader.key_for(w.poster[0][0])
                self.loader.request(key, w.poster)
            rows.append({"key": key, "name": w.name, "picked": w.path in old, "wf": w,
                         "meta": f"{_plural(w.files, 'file')}  ·  {human_bytes(w.size)}"})
        self._clear_pick_days()
        self.p_folders.set_rows(rows)
        age = int(self.s.older_than_days)
        self.p_title.setText("Choose which folders to move")
        self.p_sub.setText(f"Tap a folder to add it or take it out. Folders older than {_plural(age, 'day')} "
                           "are already chosen.")
        self.p_new.setVisible(False)
        self.p_days_box.setVisible(False)
        self.p_folders.setVisible(True)
        self.p_del.head.setText("Remove them from this computer after they're checked")
        self.p_del.text.setText("Each folder is copied to the backup drive and checked file by file first. "
                                "If anything doesn't check out, that folder stays here. "
                                "Off: the folders are copied and also kept on this computer.")
        self.p_del.setChecked(self.s.archive_move)
        self._enter_pick("archive")

    def _day_title_edited(self, day, text: str, src) -> None:
        """One name per date: every device's section for that day follows."""
        self._day_titles[day.day] = text
        for lab, what, d, ed in self._pick_where.get(day.day, []):
            lab.setText(f"{what}  ·  {human_bytes(d.size)}  ·  saves to {self._folder_for(d)}")
            if ed is not None and ed is not src and ed.text() != text:
                ed.setText(text)

    def _pick_all(self, on: bool) -> None:
        if self._pick_kind == "archive":
            self.p_folders.set_all(on)
        else:
            for _d, _f, grid, _h, _s in self._pick_grids:
                grid.set_all(on)

    def _pick_only_new(self) -> None:
        for _d, _f, grid, _h, _s in self._pick_grids:
            grid.set_all(True, only_new=True)

    def _picked_clips(self) -> list:
        return [i for _d, _f, grid, _h, _s in self._pick_grids for i in grid.infos() if i.get("picked")]

    def _pick_changed(self) -> None:
        if self.stage.currentWidget() is not self.pick_view:
            return
        delete = self.p_del.isChecked()
        if self._pick_kind == "archive":
            chosen = [r for r in self.p_folders.rows if r.get("picked")]
            n = len(chosen)
            size = sum(r["wf"].size for r in chosen)
            self.p_count.setText(f"{n} of {len(self.p_folders.rows)} chosen  ·  {human_bytes(size)}")
            label = f"{'Move' if delete else 'Copy'} {_plural(n, 'folder')}" if n else "Choose at least one folder"
        else:
            chosen = self._picked_clips()
            total = sum(grid.count() for _d, _f, grid, _h, _s in self._pick_grids)
            n = len(chosen)
            n_vid = sum(1 for i in chosen if i["kind"] in ("video", "360"))
            what = " and ".join(x for x in (_plural(n_vid, "video") if n_vid else "",
                                            _plural(n - n_vid, "photo") if n - n_vid else "") if x)
            self.p_count.setText(f"{n} of {total} chosen  ·  {human_bytes(sum(i['size'] for i in chosen))}")
            label = (f"Copy and delete {what}" if delete else f"Copy {what}") if n else "Choose at least one video"
        self.p_go.set_text(label)
        self.p_go.setEnabled(n > 0 and not self.host.busy and not self._advancing)

    def _pick_back(self) -> None:
        self.stage.setCurrentWidget(self.screen_view)
        self._clear_pick_days()
        self.refresh()

    def _pick_go(self) -> None:
        delete = self.p_del.isChecked()
        if self._pick_kind == "archive":
            chosen = [r["wf"] for r in self.p_folders.rows if r.get("picked")]
            self._start_archive(chosen, delete, beacon=self.p_go)
            return
        if not all((e := self.host.cards.get(sc.root)) is not None and e.scan is sc for sc in self._pick_scans):
            self._pick_back()  # a card was pulled or re-read meanwhile
            return
        selections: dict = {}
        for day, _folder, grid, _h, sc in self._pick_grids:
            keys = {i["clip"] for i in grid.infos() if i.get("picked")}
            if keys:
                selections.setdefault(sc.root, []).append(
                    (DayGroup(day.day, [f for f in day.files if (f.profile, f.key) in keys]), self._folder_for(day)))
        if not selections:
            return
        if delete:
            n = len(self._picked_clips())
            many = len(selections) > 1
            msg = (f"<b>{_plural(n, 'file')}</b> will be copied to <b>{self.s.offload_dest}</b> and checked. "
                   f"Then they're deleted from {'each card or device' if many else 'the card'}.<br><br>"
                   "If anything from a day doesn't check out, nothing from that day is deleted.")
            if EasyDialog(self, "warn", "Delete from the card after copying?", msg,
                          ["Copy and delete", "Cancel"]).ask() != 0:
                return
        self._start_copy(selections, delete=delete, beacon=self.p_go)

    # ================================================================ step completion
    HOLD_MS = 800

    def _finish_step(self, beacon, done_text: str, then) -> None:
        """The step's button turns green with a check and the tracker ticks the step off. After a short
        beat the next step opens. Only called once the action has succeeded, so green always means done.
        A Change link used on a later step (step button hidden) applies straight away."""
        if not beacon.isVisible():
            then()
            return
        if self._advancing:
            return
        self._advancing = True
        beacon.confirm(done_text)
        self.tracker.complete_current()
        self.second.setEnabled(False)  # nothing else to press during the green moment
        for _row, _lab, ch in self.foot_rows:
            ch.setEnabled(False)
        for b in (self.p_back, self.p_all, self.p_none, self.p_new, self.p_del):
            b.setEnabled(False)

        def go():
            self._advancing = False
            then()

        QTimer.singleShot(self.HOLD_MS, go)

    def _launch(self, job, dest: str) -> None:
        if self.host.busy:  # something else started during the green moment
            self.refresh()
            return
        self._started_here = True
        self._job_dest = dest
        self.host.run_transfer(job, after=self._job_done, show_summary=False)

    # ================================================================ live transfer view (any transfer, either mode)
    def _job_started(self, job) -> None:
        if job.kind not in ("offload", "archive"):
            return
        self._job_kind = job.kind
        self.r_years.setVisible(False)
        self._readback = bool(getattr(job.options, "verify_readback", True))
        n_files = job.total_files
        if job.kind == "offload":
            self._build_tiles(job)
            infos = [it.data(ROLE) for k, it in self._tiles.items() if k != "_by_key"]
            n_vid = sum(1 for i in infos if i["kind"] in ("video", "360"))
            n_pic = len(infos) - n_vid
            what = " and ".join(x for x in (_plural(n_vid, "video") if n_vid else "",
                                            _plural(n_pic, "photo") if n_pic else "") if x)
            roots = {t.src_root or g.source_root for g in job.groups for t in g.tasks}
            self._job_devices = len(roots)
            frm = f" from {len(roots)} devices" if len(roots) > 1 else ""
            self.r_title.setText(f"Copying {what or _plural(n_files, 'file')}{frm}")
            self.r_note.setText("Keep every card and device connected until this finishes" if len(roots) > 1
                                else "Keep the card in until this finishes")
            self.tracker.set_states(["done", "done", "done"])
            self.r_grid.setVisible(True)
            self.r_folders.setVisible(False)
        else:
            verb = "Moving" if any(g.delete_source for g in job.groups) else "Copying"
            self.r_title.setText(f"{verb} {_plural(len(job.groups), 'day folder')} to the backup")
            self.r_note.setText("Keep both drives connected until this finishes")
            self.tracker.set_states(["done", "done", "done"])
            self._build_folders(job)
            self.r_grid.setVisible(False)
            self.r_folders.setVisible(True)
        self.ring.done = False
        self.ring.set_value(0.0, "Starting")
        self.r_name.setText("")
        self.r_eta.setText("")
        self.r_thumb.set_pixmap(None)
        self.r_thumb.set_state("")
        self.r_stop.setEnabled(True)
        self.stage.setCurrentWidget(self.run_view)

    def _build_tiles(self, job) -> None:
        self.r_grid.clear()
        self._tiles = {"_by_key": {}}
        by_path = {}
        for e in self.host.cards.values():
            if e.scan is not None:
                by_path.update({f.path: f for f in e.scan.files})
        for g in job.groups:
            for t in g.tasks:
                f = by_path.get(t.src)
                if f is None or f.role != "media":
                    continue
                key = self.loader.key_for(f.path)
                info = clip_info(f, key)
                info["status"] = "queued"
                info["frac"] = 0.0
                it = self.r_grid.add_clip(info)
                self._tiles[t.src] = it
                self._tiles["_by_key"].setdefault(key, []).append(it)
        self.r_grid.fit()

    def _build_folders(self, job) -> None:
        self._folders, self._src_to_folder, self._name_to_folder = {}, {}, {}
        for g in job.groups:
            poster = poster_candidates([t.src for t in g.tasks])
            key = self.loader.key_for(poster[0][0]) if poster else ""
            if poster:
                self.loader.request(key, poster)
            self._folders[g.name] = {
                "key": key, "name": g.name, "state": "wait", "text": "Waiting", "frac": 0.0,
                "meta": f"{_plural(len(g.tasks), 'file')}  ·  {human_bytes(sum(t.size for t in g.tasks))}",
                "done": 0, "total": len(g.tasks), "verb": "Copying",
                "done_word": "Moved" if g.delete_source else "Copied"}
            self._name_to_folder[g.name] = g.name
            for t in g.tasks:
                self._src_to_folder[t.src] = g.name
        self.r_folders.set_rows(list(self._folders.values()))

    def _file_frac(self, phase: str, part: float) -> float:
        """One number for copy + read-back check, so 'almost done' means the real last stretch."""
        if phase == "Copying":
            return part * 0.5 if self._readback else part
        if phase == "Verifying":
            return 0.5 + 0.5 * part
        return part

    @staticmethod
    def _folder_busy(f: dict) -> None:
        frac = f["done"] / f["total"] if f["total"] else 0.0
        almost = frac >= ALMOST
        f.update(state="almost" if almost else "busy", frac=frac,
                 text=f"{'Almost done' if almost else f['verb']}  {f['done']}/{f['total']}")

    def _job_event(self, kind: str, p: dict) -> None:
        if self.stage.currentWidget() is not self.run_view:
            return
        if kind == "progress":
            total = p.get("total") or 0
            self.ring.set_value(p["done"] / total if total else 0.0, PHASE_WORDS.get(p.get("phase", ""), ""))
            if p.get("name"):
                self.r_name.setText(p["name"])
            self.r_eta.setText(f"{PHASE_WORDS.get(p.get('phase', ''), 'Working')}   ·   {friendly_eta(p.get('eta', -1))}")
            src = p.get("src", "")
            key = self.loader.known_key(src) if src else None
            if key:
                pm = self.loader.pixmap(key)
                if pm is not None:
                    self.r_thumb.set_pixmap(pm)
            ft = p.get("file_total") or 0
            nf = self._file_frac(p.get("phase", ""), p.get("file_done", 0) / ft if ft else 0.0)
            if src:
                self.r_thumb.set_state("almost" if nf >= ALMOST else "busy", nf)
            for slot in p.get("active", ()):
                s_src = slot.get("src", "")
                phase = slot.get("phase", "")
                tot = slot.get("file_total") or 0
                frac = self._file_frac(phase, slot.get("file_done", 0) / tot if tot else 0.0)
                it = self._tiles.get(s_src)
                if it is not None:
                    info = it.data(ROLE)
                    if info["status"] not in ("copied", "skipped", "failed"):
                        info["status"] = PHASE_STATUS.get(phase, "copying")
                        info["frac"] = max(info.get("frac", 0.0), frac)
                        self.r_grid.refresh(it)
                name = self._src_to_folder.get(s_src) or self._name_to_folder.get(slot.get("name", ""))
                f = self._folders.get(name)
                if f is not None and f["state"] in ("wait", "busy", "almost"):
                    f["verb"] = PHASE_WORDS.get(phase, "Copying")
                    self._folder_busy(f)
            if self._folders:
                self.r_folders.refresh()
        elif kind == "file":
            it = self._tiles.get(p.get("src", ""))
            if it is not None:
                info = it.data(ROLE)
                info["status"] = FINAL_STATUS.get(p.get("status", ""), "")
                info["frac"] = 1.0
                if info["status"] in ("copied", "skipped"):
                    info["done_at"] = time.monotonic()
                self.r_grid.refresh(it)
            f = self._folders.get(self._src_to_folder.get(p.get("src", "")))
            if f is not None:
                f["done"] += 1
                if f["state"] in ("wait", "busy", "almost"):
                    self._folder_busy(f)
                self.r_folders.refresh()
        elif kind == "group" and p.get("name") in self._folders:
            f = self._folders[p["name"]]
            state = p.get("state", "")
            if state in ("verified", "moved"):
                f.update(state="done", text=f["done_word"], frac=1.0, done_at=time.monotonic())
            elif state == "errors":
                f.update(state="bad", text="Problem")
            elif state == "warnings":
                f.update(state="kept", text="Kept here")
            elif state == "cancelled":
                f.update(state="stopped", text="Stopped")
            else:
                self._folder_busy(f)
            self.r_folders.refresh()

    def _job_finished(self, res) -> None:
        if res.kind not in ("offload", "archive"):
            return
        self.ring.done = res.ok
        self.ring.set_value(1.0 if res.ok else self.ring.value, "Done" if res.ok else "Stopped")
        self.r_thumb.set_state("done" if res.ok else "")
        self.r_stop.setEnabled(False)
        if not self._started_here:  # started in Advanced: just go back to the normal screen
            self._leave_running(res)

    def _leave_running(self, res) -> None:
        self.stage.setCurrentWidget(self.screen_view)
        self._celebrate = bool(res.ok and not res.warnings)
        if res.kind == "archive":
            self._scan_archive()
        self.refresh()

    def _stop(self) -> None:
        ans = EasyDialog(self, "warn", "Stop now?",
                         "Files that already finished are saved and checked. Press Start later to copy the rest. "
                         "Nothing is copied twice.", ["Keep going", "Stop"], primary=0).ask()
        if ans == 1:
            self.r_stop.setEnabled(False)
            self.host.cancel_job()

    def _job_done(self, res) -> None:
        """Result popup for transfers started from Easy mode."""
        self._started_here = False
        copy = res.kind == "offload"
        where = self._job_dest
        first_dir = next((g.dest_dir for g in res.groups if os.path.isdir(g.dest_dir)), where)
        details = "\n".join(([res.aborted] if res.aborted else []) + res.errors + res.warnings)
        if not self._job_delete:
            safe = "Nothing was deleted from your card." if copy else "Nothing was removed from this computer."
        else:
            safe = ("Days where every file checked out were deleted from the card; everything else is still on it."
                    if copy else "Folders that didn't fully check out are still on this computer.")
        if res.cancelled:
            EasyDialog(self, "info", "Stopped",
                       "Files that finished are saved and checked. Press Start to copy the rest.", ["OK"]).ask()
        elif res.aborted.startswith("Destination is full"):
            EasyDialog(self, "bad", "The drive is full",
                       f"There's no space left in <b>{where}</b>. {safe}<br><br>"
                       "Free up some space, or click Change and choose a bigger drive, then press Start again.",
                       ["OK"], details=details).ask()
        elif res.aborted.startswith("Source disappeared"):
            EasyDialog(self, "bad", "The card was removed too early" if copy else "A drive was disconnected",
                       "Put it back in and press Start again. Files that already finished won't be copied twice.",
                       ["OK"], details=details).ask()
        elif res.errors or res.aborted:
            n = res.files_failed or len(res.errors)
            EasyDialog(self, "bad", f"{_plural(n, 'file')} couldn't be copied",
                       f"{safe} Press Start to try again; only the missing files are copied.<br><br>"
                       "If it keeps failing, the card or drive may have a problem. Help has more on this.",
                       ["OK"], details=details).ask()
        elif res.warnings:
            EasyDialog(self, "warn", "Done, with one thing to check",
                       "Everything is on the backup drive. A few files had changed since they were first copied, "
                       "so MMTX kept those folders on this computer as well.", ["OK"], details=details).ask()
        else:
            if copy:
                n = res.files_ok
                gone = sum(1 for g in res.groups if g.source_deleted)
                msg = (f"{_plural(n, 'file')} saved to <b>{where}</b> and checked."
                       + ("<br>They've been deleted from the card." if self._job_delete and gone else "")
                       + ("<br><br><b>You can remove the cards now.</b>" if getattr(self, "_job_devices", 1) > 1
                          else "<br><br><b>You can remove the card now.</b>"))
            else:
                moved = sum(1 for g in res.groups if g.source_deleted)
                msg = (f"{_plural(len(res.groups), 'day folder')} saved to <b>{where}</b> and checked."
                       + (f"<br>{_plural(moved, 'folder')} removed from this computer to free up space." if moved else ""))
            if EasyDialog(self, "ok", "All done", msg, ["OK", "Open the folder"]).ask() == 1:
                open_path(first_dir)
        self._leave_running(res)

    # ================================================================ help
    def open_help(self) -> None:
        dest = self.s.offload_dest
        example = day_folder_name(date.today(), self.s.default_title, self.s.spaces_to_underscores)
        age = int(self.s.older_than_days)
        items = [
            ("My card or camera isn't showing up",
             "• Push the card all the way in until it clicks, or try a USB card reader.<br>"
             "• Check that it shows up in File Explorer (Finder on a Mac). If the computer can't see it, "
             "MMTX can't either.<br>"
             "• Using a USB-C cable? Each camera has a setting for it (GoPro: MTP, Sony: Mass Storage, "
             "Insta360: File Transfer). On step 1, tap your camera for the exact steps.<br>"
             "• Phones: unlock the phone and allow file access (Android: File transfer, iPhone: Trust).<br>"
             "• If the videos are already in a folder, use <b>Copy from a folder instead</b>.",
             [("Copy from a folder instead", self._pick_source)]),
            ("Where are my videos saved?",
             (f"In <b>{dest}</b>." if dest else "In the folder you choose in step 2.")
             + f" MMTX makes one folder per shooting day, named with the date and a title, like <b>{example}</b>, "
               "and puts that day's files directly inside it.",
             [("Open the folder", lambda: open_path(dest))] if dest and os.path.isdir(dest) else []),
            ("When is it safe to remove the card?",
             "When the <b>All done</b> message appears. Easy mode only deletes from the card if you switch on "
             "<b>Delete them from the card</b> under <b>Choose which ones</b>, and then only files whose copy "
             "checked out. Otherwise the card keeps every file until you format it in your camera.", []),
            ("What does \"checked\" mean?",
             "After each file is copied, MMTX reads it back from the drive and compares a fingerprint "
             "(a checksum) with the original. If they don't match, it copies the file again. <b>All done</b> means "
             "every file matched.", []),
            ("What happens if I press Start twice?",
             "Nothing gets copied twice. Files that are already saved and match the card are skipped, so it's "
             "always safe to run it again, for example after a card was removed too early.", []),
            ("My drive is getting full",
             f"Use <b>Move old videos</b>. It moves day folders older than {_plural(age, 'day')} to a backup drive, checks every "
             "file, and only then removes them from this computer. You can change the age in Advanced, on the "
             "Archive page.", [("Go to Move old videos", lambda: (self.tab_arch.click()))]),
            ("Something went wrong",
             "When something fails, nothing is deleted. Press Start again and only the missing files are copied. "
             "If the same file fails every time, the card or drive may be faulty. The log has the details.",
             [("Open the log folder", lambda: (os.makedirs(logs_dir(), exist_ok=True), open_path(logs_dir())))]),
            ("How do I bring in my Google Photos?",
             "On <b>takeout.google.com</b>, deselect everything, tick <b>Google Photos</b>, keep the .zip format and "
             "download every part. Then open the <b>Google Photos</b> tab here and add the zips (or drag them onto "
             "this window). Each photo goes in a folder for its day, like <b>2026 › 2026_01 › 2026_01_22</b>. "
             "Album copies and photos you already have are skipped, captions and places are kept next to the "
             "photos, and the zips themselves are never changed or deleted. Next year, do the same with a new "
             "takeout: only the new photos are added.",
             [("Go to Google Photos", lambda: self.tab_photos.click())]),
            ("Can I change the folder names?",
             f"Yes. The title part (\"{self.s.default_title}\") can be changed for each day in Advanced, on the "
             "Offload page, before copying.", []),
        ]
        self._help = HelpDialog(self, items, on_tour=self.start_tour)
        self._help.exec()

    def start_tour(self) -> None:
        if self.host.busy:
            return

        def run():
            age = int(self.s.older_than_days)
            steps = [
                (self.tracker, "Your progress",
                 "Three steps: connect a card, camera or phone, choose where videos are saved (only once), then start. "
                 "The current step is red. Finished steps turn green."),
                (self.hero, "What to do next",
                 "This panel always shows the next step. When there's something to click, its button flashes "
                 "red. Press it, it turns green, and the next step opens."),
                (self.tab_copy, "Copy my card",
                 "Copies new videos and photos from a camera card into one folder per shooting day. "
                 "Anything already copied is skipped."),
                (self.tab_arch, "Move old videos",
                 f"Frees up space by moving day folders older than {_plural(age, 'day')} to a backup drive. "
                 "Every file is checked before the original is removed."),
                (self.help_btn, "Help",
                 "Answers to common questions, like what to do if your card doesn't show up."),
                (self.adv_btn, "Advanced",
                 "Every option: pick individual days, rename folders, verify an archive drive, change settings."),
            ]
            ov = CoachOverlay(self, steps)
            ov.finished.connect(self._tour_done)
            self._overlay = ov

        QTimer.singleShot(60, run)

    def _tour_done(self) -> None:
        self.s.easy_tour_done = True
        self.s.save()
