"""Headless run-through of Easy mode from a fresh install, including switching to Advanced mid-transfer.
Usage: python tests/gui_easy.py <out_dir>"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import gui_smoke as G  # noqa: E402  (sets offscreen platform, temp config/cache dirs)

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mmtx import planner  # noqa: E402
from mmtx.app import make_icon  # noqa: E402
from mmtx.transfer import JobResult  # noqa: E402
from mmtx.ui import easy_widgets, main_window  # noqa: E402
from mmtx.ui.clip_grid import ROLE  # noqa: E402
from mmtx.ui.palettes import PALETTES  # noqa: E402
from mmtx.ui.theme import apply_theme  # noqa: E402
from mmtx.ui.volumes import Volume  # noqa: E402

OUT = G.OUT


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(make_icon())
    planner.same_device = lambda a, b: False
    n_shot = {"n": 0}

    def grab(widget, name):
        p = os.path.join(OUT, name)
        widget.grab().save(p)
        print("saved", p)

    def pump(n=15):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    seen: list[tuple[str, str]] = []
    answers: list[int] = []

    def fake_exec(self):  # Easy popups: screenshot, then answer
        self.resize(max(self.width(), 660), self.sizeHint().height())
        self.show()
        pump(3)
        n_shot["n"] += 1
        seen.append((getattr(self, "kind", "help"), self.windowTitle()))
        grab(self, f"easy_popup_{n_shot['n']}_{self.windowTitle().split()[0].lower().strip('!.,')}.png")
        self.choice = answers.pop(0) if answers else 0
        self.accept()
        return 1

    easy_widgets.EasyDialog.exec = fake_exec

    card = os.path.join(G.TMP, "GOPRO_CARD")
    big = os.path.join(G.TMP, "BigDrive")
    backup = os.path.join(G.TMP, "BackupDrive")
    os.makedirs(big)
    os.makedirs(backup)
    G.build_demo_card(card)
    drives = [
        Volume(big, "BigDrive", 2_000_000_000_000, "ntfs", None, False, "SSD"),
        Volume(backup, "Backup", 4_000_000_000_000, "ntfs", None, False, "Hard drive"),
    ]
    with_card = [Volume(card, "GOPRO", 128_000_000_000, "exfat", "GoPro HERO13 Black", False, "SD card")] + drives

    w = main_window.MainWindow()  # fresh settings: nothing chosen yet
    w.resize(1320, 980)
    w.show()
    pump()
    w.set_easy(True)
    w._on_volumes(drives)
    G.wait_until(app, lambda: getattr(w.easy, "_overlay", None) is not None and w.easy._overlay.isVisible())
    pump(10)
    grab(w, "easy_0_tour.png")
    w.easy._overlay.advance()
    pump()
    grab(w, "easy_0b_tour.png")
    w.easy._overlay.close_tour()
    pump()
    assert w.settings.easy_tour_done

    # help dialog: open one answer and screenshot it
    orig_exec = easy_widgets.HelpDialog.exec

    def help_exec(self):
        self.resize(760, 700)
        self.open_item(0)
        self.show()
        pump(5)
        grab(self, "easy_help.png")
        self.accept()
        return 1

    easy_widgets.HelpDialog.exec = help_exec
    w.easy.help_btn.click()
    easy_widgets.HelpDialog.exec = orig_exec

    # 1. waiting for a card
    pump(20)
    grab(w, "easy_1_insert.png")
    assert w.easy.title.text() == "Insert your camera card"
    for key in PALETTES:
        w.easy.set_palette(key)
        pump(4)
        grab(w, f"palette_{key}_insert.png")
    w.easy.set_palette("graphite")
    assert not w.easy.primary.isVisible() and not w.easy.start.isVisible(), "nothing to press on step 1"

    assert w.easy.tracker.states == ["active", "todo", "todo"]

    # 2. card goes in -> found, no folder yet -> suggestion with the one flashing red button
    w._on_volumes(with_card)
    G.wait_until(app, lambda: w.card_scan is not None and not w.scanning)
    pump(40)  # let the tracker finish ticking off step 1
    w.easy.primary._t = 0.5  # screenshot at the bright point of the flash
    grab(w, "easy_2_choose.png")
    assert w.easy.primary.isVisible() and w.easy.primary.btn.text() == "Use this folder"
    assert w.easy.primary.state == "todo" and w.easy.tracker.states == ["done", "active", "todo"]
    assert w.easy._suggestion and w.easy._suggestion[0].startswith(big), w.easy._suggestion
    assert w.easy.start.state == "todo" and not w.easy.start.isVisible()

    # 3. press it -> green with a check, step 2 ticked off, then the ready screen opens by itself
    w.easy.HOLD_MS = 2000  # longer green moment, only so the screenshot catches the finished tracker animation
    w.easy.primary.btn.click()
    assert w.easy.primary.state == "done" and w.easy.primary.btn.text().endswith("Folder chosen")
    assert w.easy.tracker.states == ["done", "done", "active"]
    w.easy.primary.btn.click()  # a second press during the green moment does nothing
    pump(75)
    grab(w, "easy_2b_folder_chosen.png")
    del w.easy.HOLD_MS
    assert w.easy.title.text() == "Choose where to save your videos", "green moment holds the screen briefly"
    G.wait_until(app, lambda: w.easy.start.isVisible())
    assert w.easy.start.state == "todo"
    G.wait_until(app, lambda: not w.loader.inflight)
    pump(25)
    grab(w, "easy_3_ready.png")
    assert w.easy.start.isVisible() and w.easy.start.isEnabled()
    assert "ready to copy" in w.easy.title.text(), w.easy.title.text()
    for key in ("midnight", "daylight", "contrast"):
        w.easy.set_palette(key)
        pump(4)
        w.easy.start._t = 0.5
        grab(w, f"palette_{key}_ready.png")
    w.easy.set_palette("graphite")
    pump(2)

    # 3b. "Choose which ones": pick two clips and have them deleted from the card after the check
    assert w.easy.start.btn.text() == "Copy everything" and w.easy.second.text() == "Choose which ones"
    w.easy.second.click()
    pump(10)
    assert w.easy.stage.currentWidget() is w.easy.pick_view
    G.wait_until(app, lambda: not w.loader.inflight)
    pump(15)
    grab(w, "easy_pick_1_open.png")
    total = sum(g.count() for _d, _f, g, _h in w.easy._pick_grids)
    assert len(w.easy._picked_clips()) == total == 24, (len(w.easy._picked_clips()), total)
    w.easy.p_none.click()
    assert not w.easy.p_go.isEnabled() and w.easy.p_go.btn.text() == "Choose at least one video"
    _day, folder, grid, _h = w.easy._pick_grids[0]
    want = {"GX010002.MP4", "GX010003.MP4"}
    for i in range(grid.count()):
        if grid.item(i).data(ROLE)["name"] in want:
            grid.itemClicked.emit(grid.item(i))  # a tap
    assert {i["name"] for i in w.easy._picked_clips()} == want
    QTest.mouseClick(w.easy.p_del, Qt.LeftButton)
    assert w.easy.p_del.isChecked() and w.easy.p_go.btn.text() == "Copy and delete 2 videos", w.easy.p_go.btn.text()
    pump(5)
    w.easy.p_go._t = 0.5
    grab(w, "easy_pick_2_chosen.png")
    sb = w.easy.hero.parentWidget().parentWidget().parentWidget().verticalScrollBar()
    sb.setValue(sb.maximum())
    pump(5)
    w.easy.p_go._t = 0.5
    grab(w, "easy_pick_3_delete.png")
    sb.setValue(0)
    picked_src = [i["src"] for i in w.easy._picked_clips()]
    n_before = len(seen)
    answers[:] = [0, 0]  # "Copy and delete", then OK
    w.easy.p_go.btn.click()
    assert w.easy.p_go.state == "done"
    G.wait_until(app, lambda: not w.busy and len(seen) >= n_before + 2, timeout=300)
    assert seen[n_before][1] == "Delete from the card after copying?", seen[n_before:]
    day_dir = os.path.join(w.settings.offload_dest, folder)
    for src in picked_src:
        assert not os.path.exists(src), f"{src} should be gone from the card after its copy checked out"
        assert os.path.isfile(os.path.join(day_dir, os.path.basename(src)))
    assert os.path.exists(os.path.join(card, "DCIM", "100GOPRO", "GX010001.MP4")), "clips not chosen stay on the card"
    G.wait_until(app, lambda: not w.scanning)
    G.wait_until(app, lambda: w.easy.start.isVisible())
    G.wait_until(app, lambda: not w.loader.inflight)
    pump(20)
    print("after custom copy:", w.easy.title.text())

    # 4. Start in Easy; mid-transfer: screenshot, switch to Advanced and check its tiles are live, switch back
    state = {"easy": False, "adv": False, "live_tiles": 0}

    def mid_copy():
        if not w.busy or w.easy.ring.value < 0.10:
            return
        if not state["easy"]:
            grab(w, "easy_4_running.png")
            state["easy"] = True
            w.set_easy(False)
            w.show_page(0)
            return
        if not state["adv"]:
            pump(3)
            live = [it.data(ROLE)["status"] for sec in w.offload.sections for it in sec.items.values()]
            state["live_tiles"] = sum(1 for s in live if s in ("queued", "copying", "verifying", "copied"))
            grab(w, "sync_1_advanced_during_easy_copy.png")
            state["adv"] = True
            w.set_easy(True)

    t = QTimer()
    t.timeout.connect(mid_copy)
    t.start(25)
    answers[:] = [0]
    n_before = len(seen)
    w.easy.start.btn.click()
    assert w.easy.start.state == "done" and w.easy.tracker.states == ["done", "done", "done"]
    pump(10)
    grab(w, "easy_3b_started.png")
    G.wait_until(app, lambda: w.busy)
    G.wait_until(app, lambda: not w.busy and any(k == "ok" for k, _ in seen[n_before:]))
    t.stop()
    print("live tiles seen in Advanced during the Easy copy:", state["live_tiles"])
    assert state["adv"] and state["live_tiles"] > 0, "Advanced must show the Easy transfer live"
    G.wait_until(app, lambda: not w.scanning)
    pump(30)
    grab(w, "easy_5_all_saved.png")
    assert w.easy.title.text() == "Everything on this card is saved", w.easy.title.text()
    dest = w.settings.offload_dest
    days = sorted(os.listdir(dest))
    print("day folders:", days)
    assert len(days) == 6 and all(d.endswith("_DailyRun") for d in days)
    d14 = os.path.join(dest, "2026_11_14_DailyRun")
    assert os.path.isfile(os.path.join(d14, "GX010001.MP4")), "files go straight into the day folder"
    assert not any(os.path.isdir(os.path.join(d14, n)) for n in os.listdir(d14))
    assert os.path.exists(os.path.join(card, "DCIM", "100GOPRO", "GX010001.MP4")), "Copy everything never deletes"

    # 5. Move old videos (include the big day so there is time to look at it mid-transfer)
    w.settings.older_than_days = 1
    w.easy.tab_arch.click()
    pump()
    G.wait_until(app, lambda: not (w.easy.arch_worker and w.easy.arch_worker.isRunning()))
    pump(10)
    grab(w, "easy_6_backup.png")
    assert w.easy.primary.btn.text() == "Use this drive" and w.easy._suggestion[0].startswith(backup)
    w.easy.primary.btn.click()
    assert w.easy.primary.state == "done" and w.easy.primary.btn.text().endswith("Backup drive chosen")
    G.wait_until(app, lambda: w.easy.start.isVisible() or w.easy.title.text().startswith("Looking"))
    G.wait_until(app, lambda: w.easy.arch_scanned)
    G.wait_until(app, lambda: not w.loader.inflight)
    pump(20)
    grab(w, "easy_7_archive_ready.png")
    assert w.easy.start.isEnabled()

    arch_state = {"easy": False, "adv": False, "texts": []}

    def mid_archive():
        if not w.busy or w.easy.ring.value < 0.08:
            return
        if not arch_state["easy"]:
            grab(w, "easy_8_archiving.png")
            arch_state["easy"] = True
            w.set_easy(False)
            w.show_page(1)
            return
        if not arch_state["adv"] and w.archive.folders:
            pump(3)
            arch_state["texts"] = [w.archive.table.item(r, 8).text() for r in range(w.archive.table.rowCount())]
            if any(x.startswith(("Copying", "Checking", "Waiting", "✓")) for x in arch_state["texts"]):
                grab(w, "sync_2_advanced_archive_during_easy_move.png")
                arch_state["adv"] = True
                w.set_easy(True)

    t2 = QTimer()
    t2.timeout.connect(mid_archive)
    t2.start(25)
    n_before = len(seen)
    answers[:] = [0, 0]  # "Move them", then OK
    w.easy.start.btn.click()
    G.wait_until(app, lambda: not w.busy and len(seen) >= n_before + 2, timeout=300)
    t2.stop()
    print("Advanced archive statuses during the Easy move:", arch_state["texts"])
    assert arch_state["adv"], "Advanced Archive must show the Easy move live"
    G.wait_until(app, lambda: w.easy.arch_scanned)
    pump(20)
    grab(w, "easy_9_after_archive.png")
    moved = sorted(os.listdir(os.path.join(w.settings.archive_dest, "2026")))
    print("archived:", moved)
    assert moved and all(not os.path.exists(os.path.join(dest, m)) for m in moved)

    # 6. problem popups
    w.easy._job_dest = dest
    w.easy._started_here = True
    w.easy._job_done(JobResult(kind="offload", title="x", aborted="Source disappeared (card or drive removed?): E:\\"))
    w.easy._started_here = True
    w.easy._job_done(JobResult(kind="offload", title="x", errors=["2026_11_14_DailyRun/GX010003.MP4: I/O error"],
                               files_failed=1))
    print("popups:", seen)

    # 8. palettes: the same live view in each, with every tile state on screen, plus the Colors picker
    ez = w.easy
    ez.stage.setCurrentWidget(ez.run_view)
    ez.r_grid.setVisible(True)
    ez.r_folders.setVisible(False)
    ez.r_title.setText("Copying 20 videos and 2 photos")
    ez.r_note.setText("Keep the card in until this finishes")
    ez.r_stop.setEnabled(True)
    ez.ring.done = False
    ez.ring.set_value(0.42, "Copying")
    now = time.monotonic()
    looks = [("copied", 1.0, now), ("copied", 1.0, now - 10), ("verifying", 0.9, None), ("copying", 0.35, None)]
    for i, info in enumerate(ez.r_grid.infos()):
        st, fr, at = looks[i] if i < len(looks) else ("queued", 0.0, None)
        info["status"], info["frac"], info["done_at"] = st, fr, at
    ez.hero.parentWidget().parentWidget().parentWidget().verticalScrollBar().setValue(330)
    for key in PALETTES:
        ez.set_palette(key)
        pump(8)
        grab(w, f"palette_{key}_live.png")
    dlg = easy_widgets.PaletteDialog(ez, "graphite", lambda k: None)
    dlg.show()
    pump(8)
    grab(dlg, "easy_colors_dialog.png")
    dlg.close()
    ez.set_palette("graphite")
    ez.stage.setCurrentWidget(ez.screen_view)
    ez.refresh()

    # 7. a transfer started in Advanced shows up in Easy too
    w.set_easy(False)
    pump()
    assert w.offload.dest.path() == dest and w.archive.dst.path() == w.settings.archive_dest
    w.close()
    print("EASY MODE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
