"""Headless GUI run-through: drives the real window through offload -> archive -> verify
and saves screenshots. Usage: python tests/gui_smoke.py <out_dir>
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp(prefix="mmtx_gui_")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from make_fake_cards import _write  # noqa: E402

from mmtx import planner  # noqa: E402
from mmtx.app import make_icon  # noqa: E402
from mmtx.ui import dialogs, main_window  # noqa: E402
from mmtx.ui.theme import apply_theme  # noqa: E402

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots")
os.makedirs(OUT, exist_ok=True)
MB = 1_000_000


def build_demo_card(root: str) -> None:
    os.makedirs(os.path.join(root, "MISC"), exist_ok=True)
    with open(os.path.join(root, "MISC", "version.txt"), "w") as f:
        f.write('{"camera type":"HERO13 Black"}')
    dc = os.path.join(root, "DCIM", "100GOPRO")
    base = datetime(2026, 11, 14, 6, 5)
    n = 1
    plan = [(0, [1400, 380, 220]), (1, [610, 95]), (2, [880]), (4, [320, 300, 260, 90]), (5, [512]), (6, [150, 75])]
    for back, sizes in plan:
        day = base - timedelta(days=back)
        for j, s in enumerate(sizes):
            t = day + timedelta(minutes=9 * j)
            size = s * MB if back == 0 and j == 0 else s * MB // 40  # one big clip for live progress
            _write(os.path.join(dc, f"GX01{n:04d}.MP4"), size, t, n)
            _write(os.path.join(dc, f"GL01{n:04d}.LRV"), size // 30 + 1000, t, n + 500)
            _write(os.path.join(dc, f"GX01{n:04d}.THM"), 18_000, t, n + 900)
            n += 1


def wait_until(app, cond, timeout=120.0):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError("condition not met")


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(make_icon())
    shots: list[str] = []

    def grab(widget, name):
        p = os.path.join(OUT, name)
        widget.grab().save(p)
        shots.append(p)
        print("saved", p)

    # auto-answer modal prompts, screenshot summaries
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    summaries = []

    class AutoSummary(dialogs.SummaryDialog):
        def exec(self):  # noqa: A003
            self.resize(760, self.sizeHint().height())
            self.show()
            app.processEvents()
            summaries.append(self.res)
            grab(self, f"summary_{len(summaries)}_{self.res.kind}.png")
            self.accept()
            return 1

    main_window.SummaryDialog = AutoSummary
    # pretend the archive is a different physical drive (NVMe -> SATA) so the real copy path runs
    planner.same_device = lambda a, b: False

    card = os.path.join(TMP, "GOPRO_CARD")
    work = os.path.join(TMP, "NVMe", "Footage")
    arch = os.path.join(TMP, "SATA", "Archive")
    build_demo_card(card)
    os.makedirs(work)
    # an older day already offloaded earlier under its own title
    os.makedirs(os.path.join(work, "2026_11_08_Trail_Loop"))

    w = main_window.MainWindow()
    w.resize(1360, 900)
    w.show()
    w.offload.dest.set_path(work)
    w.offload.default_title.setText("DailyRun")
    w.offload.set_source(card)
    wait_until(app, lambda: w.offload.scan is not None)
    app.processEvents()
    # give one day a custom title, like typing it into the table
    w.offload.table.item(1, 5).setText("Beach Sunrise")
    w.offload._select("all")
    w.offload.table.item(5, 0).setCheckState(Qt.Unchecked)
    app.processEvents()
    grab(w, "1_offload.png")

    # ---- run the offload, catch a live progress frame
    got_progress = {"done": False}

    def try_progress_shot():
        if got_progress["done"] or not w.busy:
            return
        v = w.progress.bar.value()
        if 120 < v < 900:
            w.progress._toggle_log()
            app.processEvents()
            grab(w, "2_offload_progress.png")
            w.progress._toggle_log()
            got_progress["done"] = True

    t = QTimer()
    t.timeout.connect(try_progress_shot)
    t.start(40)
    w.offload._start()
    wait_until(app, lambda: not w.busy and summaries)
    t.stop()
    res = summaries[-1]
    assert res.ok, res.errors
    print("offload:", res.files_copied, "copied", res.files_failed, "failed")

    # second pass: everything should show as already offloaded
    wait_until(app, lambda: w.offload.scan is not None and w.offload.table.rowCount() > 0)
    statuses = [w.offload.table.item(r, 7).text() for r in range(w.offload.table.rowCount())]
    print("statuses after offload:", statuses)

    # ---- archive
    w.tabs.setCurrentIndex(1)
    w.archive.dst.set_path(arch)
    w.archive.src.set_path(work)
    wait_until(app, lambda: len(w.archive.folders) > 0 and not (w.archive.worker and w.archive.worker.isRunning()))
    app.processEvents()
    w.archive.older.setValue(3)
    w.archive._select("all")
    for r, wf in enumerate(w.archive.folders):
        if wf.name.startswith("2026_11_14"):
            w.archive.table.item(r, 0).setCheckState(Qt.Unchecked)
    app.processEvents()
    grab(w, "3_archive.png")
    n_before = len(summaries)
    w.archive._start()
    wait_until(app, lambda: not w.busy and len(summaries) > n_before)
    res = summaries[-1]
    assert res.ok, res.errors
    print("archive:", res.files_copied, "copied+verified,", sum(g.source_deleted for g in res.groups), "folders removed from working drive")
    assert os.path.isdir(os.path.join(arch, "2026", "2026_11_13_Beach_Sunrise"))
    assert not os.path.exists(os.path.join(work, "2026_11_13_Beach_Sunrise"))
    assert os.path.isdir(os.path.join(work, "2026_11_14_DailyRun"))

    # ---- verify (with one rotted file)
    victim_dir = os.path.join(arch, "2026", "2026_11_10_DailyRun", "GoPro_HERO13_Black")
    victim = os.path.join(victim_dir, sorted(os.listdir(victim_dir))[0])
    with open(victim, "r+b") as f:
        f.seek(777)
        f.write(b"\xde\xad")
    w.tabs.setCurrentIndex(2)
    w.verify.path.set_path(arch)
    n_before = len(summaries)
    w.verify._verify()
    wait_until(app, lambda: not w.busy and len(summaries) > n_before)
    res = summaries[-1]
    assert res.mismatched == 1, res.mismatched
    app.processEvents()
    grab(w, "4_verify.png")
    print("verify:", res.verified_ok, "ok", res.mismatched, "mismatch")

    # settings dialog look
    sd = dialogs.SettingsDialog(w.settings, w)
    sd.show()
    app.processEvents()
    grab(sd, "5_settings.png")
    sd.close()
    w.close()
    print("ALL GUI CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
