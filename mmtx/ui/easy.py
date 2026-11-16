"""Easy mode: one big instruction at a time, one glowing button, plain words.

Copy my card:     1. Card (found by itself)  ->  2. Place (picked once, suggested for you)  ->  3. GO
Move old videos:  1. Videos (where they are) ->  2. Backup drive (suggested)                 ->  3. GO

Color tells you what to do: YELLOW = do this now, GREEN = go / done, RED = stop / problem.
MMTX names and sorts the folders by itself (a folder per day, a folder per year in the backup).

Safety without questions:
  * Easy mode never deletes anything from a camera card.
  * Only days that aren't saved yet are copied, so pressing GO twice is harmless.
  * Old videos leave this computer only after every file checked out on the backup drive.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..planner import (
    OffloadOptions,
    day_folder_name,
    day_presence,
    existing_day_folders,
    is_inside,
    plan_archive,
    plan_offload,
    sanitize,
    title_from_existing,
)
from ..scanner import CardScan, scan_card, scan_working
from ..transfer import free_bytes, human_bytes
from .easy_widgets import GREEN, YELLOW, Art, CoachOverlay, EasyDialog, PulseButton, RingProgress, StepTracker
from .icons import icon
from .widgets import ThumbView, Worker, open_path

PHASE_WORDS = {
    "Copying": "Copying",
    "Verifying": "Checking",
    "Checking existing": "Checking",
    "Moving (same drive)": "Moving",
}

CARD_HOWTO = [
    "Take the small memory card out of your camera.",
    "Push it into the card slot on your computer, or into the card reader. The sticker side faces up.",
    "Wait a few seconds. This screen changes by itself when it finds the card.",
]


def friendly_eta(sec: float) -> str:
    if sec < 0:
        return "Working out how long it will take…"
    if sec < 60:
        return "Less than 1 minute left"
    if sec < 3600:
        m = max(1, round(sec / 60))
        return f"About {m} minute{'s' if m != 1 else ''} left"
    h, m = divmod(int(sec // 60), 60)
    return f"About {h} hour{'s' if h != 1 else ''} {m} minutes left"


def _lab(text: str, obj: str, center: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(obj)
    lab.setWordWrap(True)
    if center:
        lab.setAlignment(Qt.AlignCenter)
    return lab


def _link(text: str, slot) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("EasyLink")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(slot)
    return b


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _home_anchor() -> str:
    home = os.path.abspath(os.path.expanduser("~"))
    return os.path.splitdrive(home)[0] + "\\" if sys.platform == "win32" else "/"


def suggest_place(vols: list, folder_name: str, avoid: str = "") -> Optional[tuple[str, int]]:
    """Best place for footage: the non-camera drive with the most free space.
    Returns (suggested folder, free bytes) or None."""
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
        home_videos = os.path.join(home, "Videos")
        fb = free_bytes(home_videos)
        return (os.path.join(home_videos, folder_name), fb) if fb > 0 else None
    root, fb = best
    if os.path.normcase(os.path.abspath(root)) == os.path.normcase(_home_anchor()):
        return os.path.join(os.path.expanduser("~"), "Videos", folder_name), fb
    return os.path.join(root, folder_name), fb


class EasyPage(QWidget):
    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.setObjectName("EasyPage")
        self.host = host
        self.s = host.settings
        self.loader = host.loader
        self.scan: Optional[CardScan] = None
        self.scan_worker: Optional[Worker] = None
        self.arch_worker: Optional[Worker] = None
        self.plan: list = []
        self.arch_folders: list = []
        self.arch_scanned = False
        self._scanning = False
        self._note = ""
        self._note_root = ""
        self._vols: list = []
        self._seen_roots: set = set()
        self._first = True
        self._kind = ""
        self._job_dest = ""
        self._suggestion: Optional[tuple[str, int]] = None
        self._primary_action = None
        self._second_action = None
        self._foot_action = None
        self._foot_action2 = None
        self._strip_keys: dict[str, ThumbView] = {}
        self.loader.ready.connect(self._thumb_ready)

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 18, 30, 16)
        root.setSpacing(10)

        # ------------------------------------------------ top bar
        top = QHBoxLayout()
        top.setSpacing(12)
        from ..app import make_icon

        logo = QPushButton()
        logo.setObjectName("Logo")
        logo.setIcon(make_icon())
        logo.setIconSize(QSize(38, 38))
        logo.setFixedSize(42, 42)
        top.addWidget(logo)
        name = QLabel("MMTX")
        name.setObjectName("EasyBrand")
        top.addWidget(name)
        top.addStretch(1)
        self.help_btn = QPushButton("?   Help")
        self.help_btn.setObjectName("EasyTop")
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.clicked.connect(self.start_tour)
        top.addWidget(self.help_btn)
        self.adv_btn = QPushButton("  Advanced")
        self.adv_btn.setObjectName("EasyTop")
        self.adv_btn.setIcon(icon("settings", "#ffffff", 22))
        self.adv_btn.setIconSize(QSize(20, 20))
        self.adv_btn.setCursor(Qt.PointingHandCursor)
        self.adv_btn.setToolTip("The full app with every option")
        self.adv_btn.clicked.connect(lambda: self.host.set_easy(False))
        top.addWidget(self.adv_btn)
        root.addLayout(top)

        # ------------------------------------------------ the two jobs
        tabs = QHBoxLayout()
        tabs.setSpacing(14)
        tabs.addStretch(1)
        self.tab_group = QButtonGroup(self)
        self.tab_copy = self._tab("Copy my card", "card", 0)
        self.tab_arch = self._tab("Move old videos", "archive", 1)
        tabs.addWidget(self.tab_copy)
        tabs.addWidget(self.tab_arch)
        tabs.addStretch(1)
        root.addLayout(tabs)

        self.tracker = StepTracker(["Card", "Place", "GO"])
        trow = QHBoxLayout()
        trow.addStretch(1)
        trow.addWidget(self.tracker, 3)
        trow.addStretch(1)
        root.addLayout(trow)

        # ------------------------------------------------ the stage: one screen at a time
        self.stage = QStackedWidget()
        self.stage.addWidget(self._build_screen())
        self.stage.addWidget(self._build_running())
        holder = QWidget()
        holder.setObjectName("EasyHolder")
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        self.stage.setMinimumWidth(720)
        self.stage.setMaximumWidth(1000)
        hl.addWidget(self.stage, 8)
        hl.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("EasyScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        self.host.busy_changed.connect(lambda _b: self.refresh())
        (self.tab_arch if self.s.easy_tab == 1 else self.tab_copy).setChecked(True)
        self._tab_changed(self.s.easy_tab)

    def _tab(self, text: str, ic: str, idx: int) -> QPushButton:
        b = QPushButton(f"  {text}")
        b.setObjectName("EasyTab")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setIcon(icon(ic, "#ffffff", 26))
        b.setIconSize(QSize(24, 24))
        b.clicked.connect(lambda _c=False: self._tab_changed(idx))
        self.tab_group.addButton(b, idx)
        return b

    def _tab_changed(self, idx: int) -> None:
        self.s.easy_tab = idx
        self.s.save()
        if self.host.busy and self.stage.currentWidget() is self.run_view:
            return
        self.tracker.set_labels(["Card", "Place", "GO"] if idx == 0 else ["Videos", "Backup", "GO"])
        if idx == 1:
            self._scan_archive()
        self.refresh()

    # ================================================================ widgets
    def _build_screen(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(12)
        self.art = Art()
        lay.addWidget(self.art, 0, Qt.AlignHCenter)
        strip = QHBoxLayout()
        strip.setSpacing(10)
        strip.addStretch(1)
        self.thumbs = [ThumbView(150, 84) for _ in range(5)]
        for t in self.thumbs:
            strip.addWidget(t)
        self.more = QLabel("")
        self.more.setObjectName("EasyMore")
        strip.addWidget(self.more)
        strip.addStretch(1)
        self.strip_box = QWidget()
        self.strip_box.setLayout(strip)
        lay.addWidget(self.strip_box)
        self.title = _lab("", "EasyTitle")
        lay.addWidget(self.title)
        self.sub = _lab("", "EasySub")
        self.sub.setTextFormat(Qt.RichText)
        lay.addWidget(self.sub)

        self.howto = QFrame()
        self.howto.setObjectName("HowTo")
        hv = QVBoxLayout(self.howto)
        hv.setContentsMargins(26, 18, 26, 18)
        hv.setSpacing(12)
        self.howto_lines: list[tuple[QLabel, QLabel]] = []
        for _ in range(3):
            row = QHBoxLayout()
            row.setSpacing(16)
            num = QLabel("")
            num.setObjectName("HowNum")
            num.setFixedSize(40, 40)
            num.setAlignment(Qt.AlignCenter)
            txt = _lab("", "HowText", center=False)
            txt.setTextFormat(Qt.RichText)
            row.addWidget(num, 0, Qt.AlignTop)
            row.addWidget(txt, 1)
            hv.addLayout(row)
            self.howto_lines.append((num, txt))
        lay.addWidget(self.howto)

        self.pick = PulseButton("Pick", YELLOW)
        self.pick.clicked.connect(lambda: self._primary_action and self._primary_action())
        lay.addWidget(self.pick, 0, Qt.AlignHCenter)
        self.go = PulseButton("GO", GREEN, big=True)
        self.go.clicked.connect(lambda: self._primary_action and self._primary_action())
        lay.addWidget(self.go, 0, Qt.AlignHCenter)
        self.second = _link("", lambda: self._second_action and self._second_action())
        lay.addWidget(self.second, 0, Qt.AlignHCenter)

        self.foot = _lab("", "EasyFoot")
        self.foot.setTextFormat(Qt.RichText)
        lay.addWidget(self.foot)
        self.foot_link = _link("Change", lambda: self._foot_action and self._foot_action())
        lay.addWidget(self.foot_link, 0, Qt.AlignHCenter)
        self.foot2 = _lab("", "EasyFoot")
        self.foot2.setTextFormat(Qt.RichText)
        lay.addWidget(self.foot2)
        self.foot_link2 = _link("Change", lambda: self._foot_action2 and self._foot_action2())
        lay.addWidget(self.foot_link2, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        self.screen_view = w
        return w

    def _build_running(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(16)
        self.r_title = _lab("Saving your videos…", "EasyTitle")
        lay.addWidget(self.r_title)
        self.r_warn = QLabel("Do NOT take the card out yet!")
        self.r_warn.setObjectName("EasyWarn")
        self.r_warn.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.r_warn, 0, Qt.AlignHCenter)
        self.ring = RingProgress(260)
        lay.addWidget(self.ring, 0, Qt.AlignHCenter)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addStretch(1)
        self.r_thumb = ThumbView(176, 99)
        row.addWidget(self.r_thumb)
        col = QVBoxLayout()
        col.addStretch(1)
        self.r_name = _lab("", "EasySub", center=False)
        self.r_eta = _lab("", "EasyEta", center=False)
        col.addWidget(self.r_name)
        col.addWidget(self.r_eta)
        col.addStretch(1)
        row.addLayout(col)
        row.addStretch(1)
        lay.addLayout(row)
        self.r_stop = QPushButton("Stop")
        self.r_stop.setObjectName("EasyStop")
        self.r_stop.setCursor(Qt.PointingHandCursor)
        self.r_stop.clicked.connect(self._stop)
        lay.addWidget(self.r_stop, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        self.run_view = w
        return w

    # ================================================================ one screen renderer
    def _show(self, *, art: str = "", thumbs: Optional[list] = None, title: str = "", tone: str = "",
              sub: str = "", howto: Optional[list[str]] = None, pick: str = "", go: str = "",
              primary=None, second: str = "", second_action=None,
              foot: str = "", foot_action=None, foot2: str = "", foot_action2=None,
              steps: Optional[list[str]] = None) -> None:
        self.stage.setCurrentWidget(self.screen_view)
        self.art.setVisible(bool(art))
        if art:
            self.art.set_kind(art)
        self._fill_strip(thumbs or [])
        self.title.setText(title)
        self.title.setProperty("tone", tone)
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        self.howto.setVisible(bool(howto))
        for i, (num, txt) in enumerate(self.howto_lines):
            line = howto[i] if howto and i < len(howto) else ""
            num.setVisible(bool(line) and len(howto or []) > 1)
            txt.setVisible(bool(line))
            num.setText(str(i + 1))
            txt.setText(line)
        busy = self.host.busy
        self._primary_action = primary
        self.pick.setVisible(bool(pick))
        self.pick.set_text(pick)
        self.pick.setEnabled(bool(pick) and not busy)
        self.go.setVisible(bool(go))
        self.go.set_text(go)
        self.go.setEnabled(bool(go) and not busy)
        self.second.setText(second)
        self.second.setVisible(bool(second))
        self._second_action = second_action
        self.foot.setText(foot)
        self.foot.setVisible(bool(foot))
        self.foot_link.setVisible(bool(foot and foot_action))
        self._foot_action = foot_action
        self.foot2.setText(foot2)
        self.foot2.setVisible(bool(foot2))
        self.foot_link2.setVisible(bool(foot2 and foot_action2))
        self._foot_action2 = foot_action2
        if steps:
            self.tracker.set_states(steps)

    def _fill_strip(self, files: list) -> None:
        self._strip_keys = {}
        self.strip_box.setVisible(bool(files))
        for i, tv in enumerate(self.thumbs):
            if i < len(files):
                path, cands, glyph = files[i]
                key = self.loader.key_for(path)
                self._strip_keys[key] = tv
                tv.glyph = glyph
                tv.set_pixmap(self.loader.pixmap(key))
                self.loader.request(key, cands, path)
                tv.setVisible(True)
            else:
                tv.setVisible(False)
        extra = len(files) - len(self.thumbs)
        self.more.setText(f"+{extra} more" if extra > 0 else "")
        self.more.setVisible(extra > 0)

    def _thumb_ready(self, key: str) -> None:
        tv = self._strip_keys.get(key)
        if tv is not None:
            tv.set_pixmap(self.loader.pixmap(key))

    def refresh(self) -> None:
        if self.stage.currentWidget() is self.run_view and self.host.busy:
            return
        if self.s.easy_tab == 1:
            self._render_archive()
        else:
            self._render_copy()

    # ================================================================ cards (volumes)
    def on_volumes(self, vols: list) -> None:
        self._vols = list(vols)
        cams = [v for v in vols if v.camera]
        roots = {v.root for v in cams}
        new = [v for v in cams if v.root not in self._seen_roots]
        first, self._first = self._first, False
        self._seen_roots = roots
        cur = self.scan.root if self.scan else ""
        if cur and not os.path.isdir(cur):
            self.scan, self.plan, cur = None, [], ""
        if self._note_root and not os.path.isdir(self._note_root):
            self._note = self._note_root = ""
        if new and not self.host.busy and (first or not cur or cur not in roots):
            self.tab_copy.setChecked(True)
            self._tab_changed(0)
            self._scan(new[0].root)
        else:
            self.refresh()

    def _pick_source(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "Pick the folder with your videos", os.path.expanduser("~"))
        if p:
            self._scan(os.path.normpath(p))

    def _scan(self, root: str) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self._scanning = True
        self._note = self._note_root = ""
        self.refresh()
        w = Worker(lambda emit: scan_card(root), self)
        w.bridge.finished.connect(self._scan_done)
        w.bridge.failed.connect(lambda _tb: self._scan_done(None))
        self.scan_worker = w
        w.start()

    def _scan_done(self, scan) -> None:
        self._scanning = False
        self.scan = scan if scan is not None and scan.files else None
        if scan is not None and not scan.files:
            self._note, self._note_root = "empty", scan.root
        elif scan is None:
            self._note = "unreadable"
        self.refresh()

    # ================================================================ copy my card
    def _opts(self) -> OffloadOptions:
        return OffloadOptions(
            algo=self.s.algo,
            camera_subfolders=self.s.camera_subfolders,
            skip_proxies=self.s.skip_proxies,
            delete_source=False,  # Easy mode never deletes from a card
            keep_structure=self.s.keep_structure,
            rename_gopro=self.s.rename_gopro,
            verify_readback=self.s.verify_readback,
            uncached=self.s.uncached,
        )

    def _device_folders(self) -> dict[str, str]:
        assert self.scan
        sp = self.s.spaces_to_underscores
        return {p: (self.s.device_aliases.get(lbl) or sanitize(lbl, sp)) for p, lbl in self.scan.devices.items()}

    def _compute_plan(self) -> list:
        if not self.scan:
            return []
        dest = self.s.offload_dest
        existing = existing_day_folders(dest) if dest else {}
        opts = self._opts()
        dev = self._device_folders()
        plan = []
        for day in self.scan.days:
            t = title_from_existing(existing[day.day][0]) if day.day in existing else self.s.default_title
            folder = day_folder_name(day.day, t, self.s.spaces_to_underscores)
            if dest:
                present, total, _ = day_presence(day, os.path.join(dest, folder), dev, opts)
                if present >= total:
                    continue
            plan.append((day, folder))
        return plan

    @staticmethod
    def _media(days) -> list:
        out = []
        for d in days:
            for f in d.files:
                if f.role == "media":
                    out.append((f.path, f.thumbs, "photo" if f.kind in ("photo", "raw", "360photo") else "film"))
        return out

    def _render_copy(self) -> None:
        dest = self.s.offload_dest
        dest_ok = bool(dest) and free_bytes(dest) >= 0
        self.plan = self._compute_plan() if self.scan else []
        saving = f"Your videos go to:  <b>{dest}</b>" if dest else ""
        change = self._pick_dest if dest else None

        if self._scanning:
            self._show(art="search", title="Looking at your card…", sub="One second.",
                       steps=["active", "todo", "todo"])
            return
        if not self.scan:
            if self._note == "empty":
                self._show(art="card", title="This card has no videos on it", tone="warn",
                           sub="Take it out and try a different card.", steps=["active", "todo", "todo"])
                return
            title = "Put your camera card in"
            sub = "MMTX finds it by itself. You don't have to click anything."
            if self._note == "unreadable":
                title, sub = "MMTX couldn't read that card", "Take it out, then push it back in."
            self._show(art="card_in", title=title, tone="do", sub=sub, howto=CARD_HOWTO,
                       second="My videos are in a folder instead", second_action=self._pick_source,
                       foot=saving, foot_action=change, steps=["active", "todo", "todo"])
            return
        cams = " + ".join(self.scan.devices.values())
        if not dest_ok:
            self._suggestion = suggest_place(self._vols, "MMTX Videos", avoid=self.scan.root)
            sub = (f"Found your <b>{cams}</b>. Now pick where its videos should live.<br>"
                   "MMTX makes a new folder for each day and names it for you. You never have to sort anything.")
            if dest:
                sub = (f"The place you picked before (<b>{dest}</b>) isn't plugged in.<br>"
                       "Plug that drive in, or pick a new place.")
            if self._suggestion:
                where, free = self._suggestion
                self._show(art="folder", title="Pick where to save your videos", tone="do", sub=sub,
                           howto=[f"MMTX suggests:  <b>{where}</b>  ({human_bytes(free)} free)"],
                           pick="Use this place", primary=self._use_suggestion,
                           second="Pick a different place", second_action=self._pick_dest,
                           steps=["done", "active", "todo"])
            else:
                self._show(art="folder", title="Pick where to save your videos", tone="do", sub=sub,
                           pick="Pick a place", primary=self._pick_dest, steps=["done", "active", "todo"])
            return
        if not self.plan:
            self._show(art="check", title="All your videos are already saved!", tone="ok",
                       sub="You can take the card out now.", foot=saving, foot_action=change,
                       steps=["done", "done", "done"])
            return
        media = self._media([d for d, _ in self.plan])
        n_vid = sum(1 for _, _, g in media if g == "film")
        n_pic = len(media) - n_vid
        found = " and ".join(x for x in (_plural(n_vid, "new video") if n_vid else "",
                                         _plural(n_pic, "new photo") if n_pic else "") if x)
        self._show(thumbs=media, title=f"Found {found}!", tone="ok",
                   sub="Press the green <b>GO</b> button to save them.",
                   go="GO", primary=self._go_copy,
                   foot=saving + "<br><span style='font-size:12pt'>MMTX makes a folder for each day by itself.</span>",
                   foot_action=change, steps=["done", "done", "active"])

    def _use_suggestion(self) -> None:
        if not self._suggestion:
            return
        where = self._suggestion[0]
        try:
            os.makedirs(where, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{where}<br><br>{e.strerror or e}<br><br>"
                       "Press <b>Pick a different place</b> instead.", ["OK"]).ask()
            return
        self.set_dest(where)

    def _pick_dest(self) -> None:
        start = self.s.offload_dest if os.path.isdir(self.s.offload_dest) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Pick where to save your videos", start)
        if p:
            self.set_dest(os.path.normpath(p))

    def set_dest(self, path: str) -> None:
        self.s.offload_dest = path
        self.s.save()
        self.host.update_drives()
        self.refresh()

    def _go_copy(self) -> None:
        if not (self.scan and self.plan) or self.host.busy:
            return
        dest = self.s.offload_dest
        if is_inside(dest, self.scan.root):
            EasyDialog(self, "warn", "That place is on the card itself",
                       "Press <b>Change</b> and pick a place on your computer instead.", ["OK"]).ask()
            return
        try:
            os.makedirs(dest, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{dest}<br><br>{e.strerror or e}", ["OK"]).ask()
            return
        job = plan_offload(self.scan, self.plan, dest, self._device_folders(), self._opts())
        if not self.host.preflight(job, dest, easy=True):
            return
        self.host.resolve_workers(job, self.scan.root, dest)
        self._kind = "copy"
        self._job_dest = dest
        self._show_running("Saving your videos…", "Do NOT take the card out yet!")
        self.host.run_transfer(job, after=self._job_done, on_event=self._on_event, show_summary=False)

    # ================================================================ move old videos
    def _arch_src(self) -> str:
        return self.s.archive_source or self.s.offload_dest

    def _pick_arch_src(self) -> None:
        start = self._arch_src() if os.path.isdir(self._arch_src()) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Pick the folder where your videos are", start)
        if p:
            self.s.archive_source = os.path.normpath(p)
            self.s.save()
            self.host.update_drives()
            self._scan_archive()

    def _pick_arch_dst(self) -> None:
        start = self.s.archive_dest if os.path.isdir(self.s.archive_dest) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Pick your backup drive", start)
        if p:
            self.set_archive_dest(os.path.normpath(p))

    def _use_backup_suggestion(self) -> None:
        if not self._suggestion:
            return
        where = self._suggestion[0]
        try:
            os.makedirs(where, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{where}<br><br>{e.strerror or e}", ["OK"]).ask()
            return
        self.set_archive_dest(where)

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
        f_from = f"Videos are in:  <b>{src}</b>" if src else ""
        f_to = f"Backup drive:  <b>{dst}</b>" if dst else ""
        if not src_ok:
            self._show(art="folder", title="Where are your videos now?", tone="do",
                       sub="Pick the folder where MMTX saves the videos from your card.",
                       pick="Pick the folder", primary=self._pick_arch_src, steps=["active", "todo", "todo"])
            return
        if not dst_ok:
            self._suggestion = suggest_place(self._vols, "MMTX Backup", avoid=src)
            sub = ("This is the big drive that keeps your old videos.<br>"
                   "MMTX sorts them into a folder for each year by itself.")
            if dst:
                sub = f"Your backup drive (<b>{dst}</b>) isn't plugged in.<br>Plug it in, or pick a new one."
            if self._suggestion:
                where, free = self._suggestion
                self._show(art="drive", title="Pick your backup drive", tone="do", sub=sub,
                           howto=[f"MMTX suggests:  <b>{where}</b>  ({human_bytes(free)} free)"],
                           pick="Use this drive", primary=self._use_backup_suggestion,
                           second="Pick a different place", second_action=self._pick_arch_dst,
                           foot=f_from, foot_action=self._pick_arch_src, steps=["done", "active", "todo"])
            else:
                self._show(art="drive", title="Pick your backup drive", tone="do", sub=sub,
                           pick="Pick the drive", primary=self._pick_arch_dst,
                           foot=f_from, foot_action=self._pick_arch_src, steps=["done", "active", "todo"])
            return
        if not self.arch_scanned:
            self._show(art="search", title="Looking for old videos…", sub="One second.",
                       steps=["done", "done", "todo"])
            return
        cands = self._arch_candidates()
        if not cands:
            self._show(art="check", title="Nothing old to move!", tone="ok",
                       sub=f"All your videos are newer than {age} days.",
                       foot=f_from, foot_action=self._pick_arch_src, foot2=f_to, foot_action2=self._pick_arch_dst,
                       steps=["done", "done", "done"])
            return
        posters = [(w.poster[0][0], w.poster, "folder") for w in cands if w.poster]
        n = len(cands)
        self._show(thumbs=posters, title=f"Found {_plural(n, 'day')} of old videos", tone="ok",
                   sub=f"They are older than {age} days ({human_bytes(sum(w.size for w in cands))}).<br>"
                       "Press the green <b>GO</b> button to move them to your backup drive.",
                   go="GO", primary=self._go_archive,
                   foot=f_from, foot_action=self._pick_arch_src, foot2=f_to, foot_action2=self._pick_arch_dst,
                   steps=["done", "done", "active"])

    def _go_archive(self) -> None:
        cands = self._arch_candidates()
        src, arch = self._arch_src(), self.s.archive_dest
        if not cands or not arch or self.host.busy:
            return
        try:
            os.makedirs(arch, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{arch}<br><br>{e.strerror or e}", ["OK"]).ask()
            return
        move = self.s.archive_move
        n = len(cands)
        msg = (f"MMTX copies <b>{_plural(n, 'day')} of videos</b> to your backup drive and checks every one.<br>"
               + ("Then it takes them off this computer to make room." if move else "They also stay on this computer."))
        if EasyDialog(self, "question", "Move these videos?", msg, ["Yes, move them", "No"]).ask() != 0:
            return
        job = plan_archive(cands, arch, self.s.archive_layout, move, self.s.algo,
                           verify_readback=self.s.verify_readback, uncached=self.s.uncached)
        if not self.host.preflight(job, arch, easy=True):
            return
        self.host.resolve_workers(job, src, arch)
        self._kind = "archive"
        self._job_dest = arch
        self._show_running("Moving your old videos…", "Do NOT unplug any drive yet!")
        self.host.run_transfer(job, after=self._job_done, on_event=self._on_event, show_summary=False)

    # ================================================================ running
    def _show_running(self, title: str, warn: str) -> None:
        self.r_title.setText(title)
        self.r_warn.setText(warn)
        self.ring.color_state = ""
        self.ring.set_value(0.0, "Starting")
        self.r_name.setText("")
        self.r_eta.setText("")
        self.r_thumb.set_pixmap(None)
        self.r_stop.setEnabled(True)
        self.tracker.set_states(["done", "done", "active"])
        self.stage.setCurrentWidget(self.run_view)

    def _on_event(self, kind: str, p: dict) -> None:
        if kind != "progress":
            return
        total = p.get("total") or 0
        self.ring.set_value(p["done"] / total if total else 0.0, PHASE_WORDS.get(p.get("phase", ""), ""))
        if p.get("name"):
            self.r_name.setText(p["name"])
        self.r_eta.setText(friendly_eta(p.get("eta", -1)))
        src = p.get("src", "")
        key = self.loader.known_key(src) if src else None
        if key:
            pm = self.loader.pixmap(key)
            if pm is not None:
                self.r_thumb.set_pixmap(pm)

    def _stop(self) -> None:
        ans = EasyDialog(self, "warn", "Stop now?",
                         "Videos that already finished are safe.<br>Press <b>GO</b> later to finish the rest.",
                         ["Keep going", "Stop"], primary=0).ask()
        if ans == 1:
            self.r_stop.setEnabled(False)
            self.host.cancel_job()

    def _job_done(self, res) -> None:
        copy = self._kind == "copy"
        where = self._job_dest
        first_dir = next((g.dest_dir for g in res.groups if os.path.isdir(g.dest_dir)), where)
        details = "\n".join(([res.aborted] if res.aborted else []) + res.errors + res.warnings)
        card_safe = "Your card is safe. Nothing was deleted from it." if copy else "Nothing was removed from this computer."
        if res.cancelled:
            EasyDialog(self, "info", "Stopped",
                       "Videos that finished are safe.<br>Press <b>GO</b> to finish the rest.", ["OK"]).ask()
        elif res.aborted.startswith("Destination is full"):
            EasyDialog(self, "bad", "There is no more room",
                       f"The place you picked is full.<br>{card_safe}<br><br>"
                       "Make some room, or press <b>Change</b> and pick a bigger drive.", ["OK"], details=details).ask()
        elif res.aborted.startswith("Source disappeared"):
            EasyDialog(self, "bad", "The card came out too early" if copy else "A drive came unplugged",
                       "Put it back in and press <b>GO</b> again.<br>Videos that finished won't be copied twice.",
                       ["OK"], details=details).ask()
        elif res.errors or res.aborted:
            EasyDialog(self, "bad", "Uh oh. Some videos did not save.",
                       f"{card_safe}<br><br>Press <b>GO</b> to try again.<br>"
                       "If it keeps happening, ask someone to look at <b>Advanced</b>.", ["OK"], details=details).ask()
        elif res.warnings:
            EasyDialog(self, "warn", "Done, but some videos were kept",
                       "Everything was saved to your backup drive. A few videos had changed since they came off "
                       "the card, so MMTX kept them on this computer too, just in case.", ["OK"], details=details).ask()
        else:
            if copy:
                title, msg = "All done!", ("Your videos are saved and checked.<br><br>"
                                           "<b>You can take the card out now.</b>")
            else:
                title, msg = "All done!", ("Your old videos are on the backup drive and checked.<br>"
                                           "This computer has more room now.")
            if EasyDialog(self, "ok", title, msg, ["OK", "Show me the videos"]).ask() == 1:
                open_path(first_dir)
        self.ring.color_state = ""
        self.stage.setCurrentWidget(self.screen_view)
        if copy and self.scan and os.path.isdir(self.scan.root):
            self._scan(self.scan.root)
        elif not copy:
            self._scan_archive()
        self.refresh()

    # ================================================================ help
    def start_tour(self) -> None:
        if self.host.busy:
            return

        def run():
            steps = [
                (self.tracker, "These are the steps",
                 "Green means done. Yellow means do this now. Then press GO."),
                (self.stage, "Read the big words",
                 "They tell you what to do. When there is a button to press, it glows and an arrow points at it."),
                (self.tab_copy, "Copy my card",
                 "Saves new videos from your camera card. MMTX makes and names the folders for you."),
                (self.tab_arch, "Move old videos",
                 "Moves old videos to your backup drive to make room on this computer."),
                (self.adv_btn, "Advanced",
                 "All the options, for people who like settings. You can come back here any time."),
            ]
            ov = CoachOverlay(self, steps)
            ov.finished.connect(self._tour_done)
            self._overlay = ov

        QTimer.singleShot(60, run)

    def _tour_done(self) -> None:
        self.s.easy_tour_done = True
        self.s.save()
