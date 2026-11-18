"""Easy mode, third tab: Google Photos takeouts -> the photo library.

Mixed into EasyPage. Three steps like the other tabs: add the takeout zips, pick the library folder, Start.
Zips can be dropped anywhere on the Easy window; that also opens this tab.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImageReader, QPixmap
from PySide6.QtWidgets import QFileDialog

from ..transfer import free_bytes, human_bytes
from .easy_widgets import EasyDialog
from .palettes import P
from .photos_widgets import YearBars, drop_paths, pick_filter
from .widgets import open_path

PHOTOS_TAB = 2
PHOTOS_STEPS = ["Add takeouts", "Photo library", "Start"]


def easy_colors() -> dict:
    track = QColor(P.sub)
    track.setAlpha(80)
    return {"track": track, "fill": P.busy, "done": P.done_hi, "text": P.text, "sub": P.sub, "line": P.line}


def _n(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _are(n: int) -> str:
    return "is" if n == 1 else "are"


def _were(n: int) -> str:
    return "was" if n == 1 else "were"


class PhotosFlow:
    """Methods EasyPage gets for the Google Photos tab."""

    # ---------------------------------------------------------------- setup
    def _photos_setup(self) -> None:
        self.ses = self.host.takeout
        self._photos_running = False
        self._photos_suggestion: Optional[tuple[str, int]] = None
        self._photos_thumb_at = 0.0
        # chart on the Start screen
        self.years = YearBars(easy_colors, height=150)
        self.years.setVisible(False)
        lay = self.screen_view.layout()
        lay.insertWidget(lay.indexOf(self.sub) + 1, self.years)
        # chart on the running screen
        self.r_years = YearBars(easy_colors, height=130)
        self.r_years.setVisible(False)
        rl = self.run_view.layout()
        rl.insertWidget(rl.indexOf(self.r_grid), self.r_years)
        self.setAcceptDrops(True)
        self.ses.changed.connect(self._photos_changed)
        self.ses.photo.connect(self._photos_event)

    def _photos_changed(self) -> None:
        if self.s.easy_tab != PHOTOS_TAB or self._advancing or self._photos_running:
            return
        if self.stage.currentWidget() is self.run_view:
            return
        self.refresh()

    # ---------------------------------------------------------------- drop zips anywhere
    def dragEnterEvent(self, e):  # noqa: N802
        if drop_paths(e) and not self.host.busy:
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        paths = drop_paths(e)
        if not paths or self.host.busy:
            return
        e.acceptProposedAction()
        if self.s.easy_tab != PHOTOS_TAB:
            self._tab_changed(PHOTOS_TAB)
        self._photos_add(paths)

    # ---------------------------------------------------------------- adding
    def _photos_start_dir(self) -> str:
        d = self.s.takeout_last_dir
        if d and os.path.isdir(d):
            return d
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        return dl if os.path.isdir(dl) else os.path.expanduser("~")

    def _photos_add_zips(self) -> None:
        files, _f = QFileDialog.getOpenFileNames(self, "Choose your Google Takeout zip files (and loose videos)",
                                                 self._photos_start_dir(), pick_filter())
        if files:
            self._photos_add([os.path.normpath(f) for f in files])

    def _photos_add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose the folder your takeout zips and videos are in",
                                             self._photos_start_dir())
        if d:
            self._photos_add([os.path.normpath(d)])

    def _photos_add(self, paths: list[str]) -> None:
        had = bool(self.ses.paths)
        n = self.ses.add(paths)
        if not n:
            EasyDialog(self, "warn", "Nothing new to add there",
                       "MMTX looks for the zip files Google Takeout gave you (takeout-…-001.zip and so on), "
                       "loose photos and videos, or a Takeout folder you already unzipped. Those may already "
                       "be in the list.",
                       ["OK"]).ask()
            return
        if not had:
            self._finish_step(self.primary, "Takeouts added", self.refresh)
        else:
            self.refresh()

    # ---------------------------------------------------------------- the library
    def _photos_pick_lib(self) -> None:
        cur = self.s.takeout_library
        start = cur if cur and os.path.isdir(cur) else os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "Choose the folder for your photo library", start)
        if p:
            p = os.path.normpath(p)
            self._finish_step(self.primary, "Library chosen", lambda: self.ses.set_library(p))

    def _photos_use_suggestion(self) -> None:
        if not self._photos_suggestion:
            return
        where = self._photos_suggestion[0]
        try:
            os.makedirs(where, exist_ok=True)
        except OSError as e:
            EasyDialog(self, "bad", "MMTX can't save there", f"{where}<br><br>{e.strerror or e}", ["OK"]).ask()
            return
        self._finish_step(self.primary, "Library chosen", lambda: self.ses.set_library(where))

    # ---------------------------------------------------------------- the screens
    def _render_photos(self) -> None:
        from .easy import suggest_place

        ses = self.ses
        plan = ses.plan
        lib = self.s.takeout_library
        celebrate, self._celebrate = self._celebrate, False
        foot = []
        if ses.paths:
            foot.append((f"Takeouts  <b>{ses.summary() or 'reading…'}</b>", self._photos_add_zips, "Add more"))
        if lib:
            foot.append((f"Photo library  <b>{lib}</b>", self._photos_pick_lib))
        self.years.setVisible(False)
        if not ses.paths:
            self._show(art="photos", eyebrow="Step 1 of 3", title="Bring in your Google Photos",
                       sub="Choose the folder with your Google Takeout zips (loose .MOV files in it come along), "
                           "or drag them onto this window. MMTX unpacks them, files every photo and video in a "
                           "folder for its day and skips anything you already have. Your zips stay exactly as they are.",
                       howto=[("laptop", "Get your takeout",
                               "On takeout.google.com, pick only Google Photos and keep the .zip format."),
                              ("offload", "Add the folder", "Zips and loose videos together. All parts at once, or "
                                                            "a few now and the rest later."),
                              ("photos", "That's it", "Captions, places and albums are kept. Run it again for your "
                                                      "next takeout: only new photos are added.")],
                       primary="Choose the takeout folder", action=self._photos_add_folder,
                       second="Choose zip files", second_action=self._photos_add_zips,
                       foot=foot, steps=["active", "todo", "todo"])
            return
        if ses.planning:
            self._show(art="search", art_device="", eyebrow="Step 1 of 3", title="Reading your takeouts…",
                       sub="Only the list of what's inside each zip is read, so this takes seconds.",
                       foot=foot, steps=["active", "todo", "todo"])
            return
        if plan is None or not (plan.items or plan.skipped_archives):
            why = ses.plan_error or ("There are no photos or videos in there. Zips from Google Takeout are named "
                                     "like takeout-20261117T…-001.zip and hold a Takeout/Google Photos folder.")
            self._show(art="empty", eyebrow="Step 1 of 3", title="No photos or videos found", tone="",
                       sub=why, primary="Choose another folder", action=self._photos_restart_with_zips,
                       second="Start over", second_action=ses.clear, steps=["active", "todo", "todo"])
            return
        if not ses.library_ok():
            self._photos_suggestion = suggest_place(self._vols, "Google Photos")
            need = human_bytes(plan.new_bytes_estimate)
            sub = (f"MMTX makes a folder for every year, month and day in it, like <b>2026 › 2026_01 › 2026_01_22</b>. "
                   f"These takeouts need about <b>{need}</b>.")
            if lib:
                sub = f"The library you chose before (<b>{lib}</b>) isn't connected. Connect that drive, or choose another."
            self.art.folder_names = ["2026_01_22", "2026_01", "2026"]
            if self._photos_suggestion:
                where, free = self._photos_suggestion
                self._show(art="choose", eyebrow="Step 2 of 3", title="Where should your photo library live?",
                           sub=sub, suggest=f"Suggested:  <b>{where}</b>   ·   {human_bytes(free)} free",
                           primary="Use this folder", action=self._photos_use_suggestion,
                           second="Choose a different folder", second_action=self._photos_pick_lib,
                           foot=foot[:1], steps=["done", "active", "todo"])
            else:
                self._show(art="choose", eyebrow="Step 2 of 3", title="Where should your photo library live?",
                           sub=sub, primary="Choose folder", action=self._photos_pick_lib, foot=foot[:1],
                           steps=["done", "active", "todo"])
            return
        if not plan.items:
            res = ses.last_result
            what = (f"{_n(res.new, 'new file')} went in." if res is not None and res.new else "")
            self._show(art="done", eyebrow="All set", title="Everything in these takeouts is in your library",
                       tone="ok", celebrate=celebrate,
                       sub=(what + " " if what else "") + "Next time, download a new takeout and add it here. "
                           "Only photos you don't have yet are copied. You can delete the zips whenever you like.",
                       second="Open the library" if os.path.isdir(lib) else "",
                       second_action=lambda: open_path(lib), foot=foot, steps=["done", "done", "done"])
            return
        items = plan.items
        n_new = ses.new_count()
        vids = sum(1 for it in items if it.kind == "video")
        pics = len(items) - vids
        what = " and ".join(x for x in (_n(pics, "photo") if pics else "", _n(vids, "video") if vids else "") if x)
        bits = [f"About <b>{human_bytes(plan.new_bytes_estimate)}</b> of new photos."]
        skip = []
        if plan.in_library:
            skip.append(f"{plan.in_library:,} {_are(plan.in_library)} already in your library")
        if plan.copies:
            skip.append(f"{plan.copies:,} {'is an album copy' if plan.copies == 1 else 'are album copies'}")
        if skip:
            bits.append(" and ".join(skip) + "; each photo is stored once.")
        if plan.skipped_archives:
            bits.append(f"{_n(plan.skipped_archives, 'zip')} already went in and {_are(plan.skipped_archives)} skipped.")
        if plan.skipped_loose:
            bits.append(f"{_n(plan.skipped_loose, 'loose file')} already went in and {_are(plan.skipped_loose)} skipped.")
        warn = "".join(f"<br><span style='color:{P.warn_text}'>{w}</span>" for w in plan.warnings)
        title = (f"{n_new:,} new photos ready" if n_new and n_new < len(items) and not vids
                 else f"Ready to bring in {what}")
        self._show(art="", eyebrow="Step 3 of 3", title=title, sub=" ".join(bits) + warn,
                   start="Start", action=self._photos_go, foot=foot, steps=["done", "done", "active"])
        self.years.set_years({y: v[0] + v[1] for y, v in plan.years.items()})
        self.years.setVisible(bool(plan.years))

    def _photos_restart_with_zips(self) -> None:
        self.ses.clear()
        self._photos_add_folder()

    # ---------------------------------------------------------------- running
    def _photos_go(self) -> None:
        ses = self.ses
        plan = ses.plan
        lib = self.s.takeout_library
        if plan is None or not plan.items or self.host.busy:
            return
        root = lib if os.path.isdir(lib) else os.path.dirname(lib.rstrip("\\/"))
        need, free = plan.new_bytes_estimate, free_bytes(root)
        if free >= 0 and need > free:
            EasyDialog(self, "bad", "Not enough space on that drive",
                       f"This needs about <b>{human_bytes(need)}</b>, but the drive only has "
                       f"<b>{human_bytes(free)}</b> free.<br><br>Free up some space, or click Change next to "
                       "Photo library and choose a bigger drive.", ["OK"]).ask()
            return
        self._finish_step(self.start, "Started", self._photos_launch)

    def _photos_launch(self) -> None:
        ses = self.ses
        plan = ses.plan
        if plan is None or self.host.busy:
            self.refresh()
            return
        n = len(plan.items)
        self._started_here = True
        self._photos_running = True
        self._job_kind = "takeout"
        self.r_title.setText(f"Bringing in {_n(n, 'photo')} and videos" if any(it.kind == "video" for it in plan.items)
                             else f"Bringing in {_n(n, 'photo')}")
        self.r_note.setText("Keep the drive with the zips and the library drive connected until this finishes")
        self.tracker.set_states(["done", "done", "done"])
        self.r_grid.setVisible(False)
        self.r_folders.setVisible(False)
        self.r_years.set_years({y: v[0] + v[1] for y, v in plan.years.items()})
        self.r_years.setVisible(True)
        self.r_years.start()
        self.ring.done = False
        self.ring.set_value(0.0, "Starting")
        self.r_name.setText("")
        self.r_eta.setText("")
        self.r_thumb.set_pixmap(None)
        self.r_thumb.set_state("busy", 0.0)
        self.r_stop.setEnabled(True)
        self.stage.setCurrentWidget(self.run_view)
        if not ses.start(after=self._photos_done, show_summary=False):
            self._photos_running = False
            self._started_here = False
            self.stage.setCurrentWidget(self.screen_view)
            self.refresh()

    def _photos_event(self, p: dict) -> None:
        if not self._photos_running:
            return
        if p.get("state") == "new":
            self.r_years.add(p.get("year", 0))
            path = p.get("path", "")
            now = time.monotonic()
            # thumbnails are paused during jobs; a scaled JPEG decode is a few ms, a few times a second is fine
            if (path and now - self._photos_thumb_at > 0.35
                    and path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))):
                self._photos_thumb_at = now
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                sz = reader.size()
                if sz.isValid():
                    reader.setScaledSize(sz.scaled(QSize(576, 324), Qt.KeepAspectRatioByExpanding))
                img = reader.read()
                if not img.isNull():
                    self.r_thumb.set_pixmap(QPixmap.fromImage(img))
                    self.r_thumb.set_state("done", 1.0)

    def _photos_done(self, res) -> None:
        self._photos_running = False
        self._started_here = False
        self.r_years.stop()
        self.ring.done = res.ok
        self.ring.set_value(1.0 if res.ok else self.ring.value, "Done" if res.ok else "Stopped")
        self.r_stop.setEnabled(False)
        lib = res.library
        details = "\n".join(([res.aborted] if res.aborted else []) + res.errors + res.warnings)
        safe = "Your zips weren't changed or deleted."
        if res.cancelled:
            EasyDialog(self, "info", "Stopped",
                       f"Photos that finished are in your library and checked. Press Start to bring in the rest; "
                       f"nothing is copied twice. {safe}", ["OK"]).ask()
        elif res.aborted:
            EasyDialog(self, "bad", "The import stopped", f"{res.aborted}<br><br>{safe} Free up space or fix the "
                       "problem, then press Start again.", ["OK"], details=details).ask()
        elif res.errors:
            EasyDialog(self, "bad", f"{_n(res.files_failed, 'file')} couldn't be brought in",
                       f"Everything else is in your library and checked. {safe}<br><br>A damaged zip part can "
                       "be downloaded again from Google Takeout; then add it here.", ["OK"], details=details).ask()
        else:
            bits = [f"{_n(res.new, 'new file')} in <b>{lib}</b>, each one checked."]
            if res.duplicates:
                bits.append(f"{res.duplicates:,} {_were(res.duplicates)} already there and "
                            f"{'wasn' if res.duplicates == 1 else 'weren'}'t copied again.")
            fd = res.date_sources.get("filedate", 0)
            if fd:
                bits.append(f"{fd:,} had no date inside {'it' if fd == 1 else 'them'}, so the file's created date "
                            "was used.")
            if res.no_date:
                bits.append(f"{res.no_date:,} had no date anywhere and {_are(res.no_date)} in <b>Unknown date</b>.")
            bits.append(f"<br><br>{safe} You can delete them whenever you like.")
            if EasyDialog(self, "ok", "All done", " ".join(bits), ["OK", "Open the library"]).ask() == 1:
                open_path(lib)
        self._celebrate = bool(res.ok and not res.errors)
        self.r_years.setVisible(False)
        self.stage.setCurrentWidget(self.screen_view)
        QTimer.singleShot(0, self.refresh)

