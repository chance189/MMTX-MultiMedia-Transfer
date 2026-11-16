"""Headless run-through of Easy mode from a fresh install. Usage: python tests/gui_easy.py <out_dir>"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import gui_smoke as G  # noqa: E402  (sets offscreen platform, temp config/cache dirs)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mmtx import planner  # noqa: E402
from mmtx.app import make_icon  # noqa: E402
from mmtx.transfer import JobResult  # noqa: E402
from mmtx.ui import easy_widgets, main_window  # noqa: E402
from mmtx.ui.theme import apply_theme  # noqa: E402
from mmtx.ui.volumes import Volume  # noqa: E402

OUT = G.OUT


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(make_icon())
    planner.same_device = lambda a, b: False
    shots = {"n": 0}

    def grab(widget, name):
        p = os.path.join(OUT, name)
        widget.grab().save(p)
        print("saved", p)

    def pump(n=15):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    dialogs_seen: list[tuple[str, str]] = []
    answers: list[int] = []

    def fake_exec(self):  # EasyDialog popups: screenshot, then answer
        self.resize(max(self.width(), 680), self.sizeHint().height())
        self.show()
        pump(3)
        shots["n"] += 1
        title = self.windowTitle()
        dialogs_seen.append((self.kind, title))
        grab(self, f"easy_popup_{shots['n']}_{title.split()[0].lower().strip('!.,')}.png")
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
    vols_no_card = [
        Volume(big, "BigDrive", 2_000_000_000_000, "ntfs", None, False, "SSD"),
        Volume(backup, "Backup", 4_000_000_000_000, "ntfs", None, False, "Hard drive"),
    ]
    vols = [Volume(card, "GOPRO", 128_000_000_000, "exfat", "GoPro HERO13 Black", False, "SD card")] + vols_no_card

    w = main_window.MainWindow()  # fresh settings: no folders chosen yet
    w.resize(1280, 940)
    w.show()
    pump()
    w.set_easy(True)
    w._route_volumes(vols_no_card)
    G.wait_until(app, lambda: getattr(w.easy, "_overlay", None) is not None and w.easy._overlay.isVisible())
    pump()
    grab(w, "easy_0_tour.png")
    w.easy._overlay.advance()
    pump()
    grab(w, "easy_0b_tour.png")
    w.easy._overlay.close_tour()
    pump()
    assert w.settings.easy_tour_done

    # 1. no card yet
    pump(30)
    grab(w, "easy_1_insert.png")
    assert w.easy.title.text() == "Put your camera card in"

    # 2. card goes in -> found, but no place picked yet -> suggestion
    w._route_volumes(vols)
    G.wait_until(app, lambda: w.easy.scan is not None and not w.easy._scanning)
    pump(20)
    grab(w, "easy_2_place.png")
    assert w.easy.pick.isVisible() and "Use this place" in w.easy.pick.btn.text(), w.easy.pick.btn.text()
    assert w.easy._suggestion and w.easy._suggestion[0].startswith(big), w.easy._suggestion

    # 3. press the glowing yellow button -> ready, big green GO
    w.easy.pick.btn.click()
    pump()
    G.wait_until(app, lambda: not w.loader.inflight)
    pump(20)
    grab(w, "easy_3_ready.png")
    assert w.easy.go.isVisible() and w.easy.go.isEnabled()
    assert w.easy.title.text().startswith("Found"), w.easy.title.text()

    # 4. GO
    got = {"run": False}

    def shot_running():
        if not got["run"] and w.busy and w.easy.ring.value > 0.12:
            grab(w, "easy_4_running.png")
            got["run"] = True

    t = QTimer()
    t.timeout.connect(shot_running)
    t.start(30)
    answers[:] = [0]
    w.easy.go.btn.click()
    G.wait_until(app, lambda: not w.busy and any(k == "ok" for k, _ in dialogs_seen))
    t.stop()
    G.wait_until(app, lambda: not w.easy._scanning)
    pump(20)
    grab(w, "easy_5_all_saved.png")
    assert w.easy.title.text() == "All your videos are already saved!", w.easy.title.text()
    dest = w.settings.offload_dest
    days = sorted(os.listdir(dest))
    print("saved day folders:", days)
    assert len(days) == 6 and all(d.endswith("_DailyRun") for d in days)
    assert os.path.exists(os.path.join(card, "DCIM", "100GOPRO", "GX010001.MP4")), "easy mode must never delete from card"

    # 5. Move old videos
    w.settings.older_than_days = 3
    w.easy.tab_arch.click()
    pump()
    G.wait_until(app, lambda: not (w.easy.arch_worker and w.easy.arch_worker.isRunning()))
    pump(10)
    grab(w, "easy_6_backup_pick.png")
    assert "Use this drive" in w.easy.pick.btn.text() and w.easy._suggestion[0].startswith(backup), w.easy._suggestion
    w.easy.pick.btn.click()
    pump()
    G.wait_until(app, lambda: w.easy.arch_scanned)
    G.wait_until(app, lambda: not w.loader.inflight)
    pump(20)
    grab(w, "easy_7_archive_ready.png")
    assert w.easy.go.isEnabled()
    n_before = len(dialogs_seen)
    answers[:] = [0, 0]  # "Yes, move them", then OK
    w.easy.go.btn.click()
    G.wait_until(app, lambda: not w.busy and len(dialogs_seen) >= n_before + 2)
    pump(20)
    G.wait_until(app, lambda: w.easy.arch_scanned)
    pump(10)
    grab(w, "easy_8_archive_after.png")
    moved = sorted(os.listdir(os.path.join(w.settings.archive_dest, "2026")))
    print("archived:", moved)
    assert moved and all(not os.path.exists(os.path.join(dest, m)) for m in moved)

    # 6. what a problem looks like
    w.easy._kind = "copy"
    w.easy._job_dest = dest
    w.easy._job_done(JobResult(kind="offload", title="x", aborted="Source disappeared (card or drive removed?): E:\\"))
    w.easy._job_done(JobResult(kind="offload", title="x", errors=["2026_11_14_DailyRun/GX010003.MP4: I/O error"],
                               files_failed=1))
    print("popups:", dialogs_seen)

    # 7. toggle back and forth
    w.set_easy(False)
    pump()
    assert not w.is_easy and not w.settings.easy_mode
    assert w.offload.dest.path() == w.settings.offload_dest, "Advanced must pick up the place chosen in Easy mode"
    assert w.archive.dst.path() == w.settings.archive_dest
    grab(w, "easy_9_advanced_with_switch.png")
    w.set_easy(True)
    pump()
    assert w.is_easy
    w.close()
    print("EASY MODE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
