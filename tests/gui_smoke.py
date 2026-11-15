"""Headless GUI run-through: drives the real window through offload -> archive -> verify
and saves screenshots. Needs ffmpeg for the demo clips. Usage: python tests/gui_smoke.py <out_dir>
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
TMP = tempfile.mkdtemp(prefix="mmtx_gui_")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "cache")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from mmtx import planner  # noqa: E402
from mmtx.app import make_icon  # noqa: E402
from mmtx.media import find_ffmpeg  # noqa: E402
from mmtx.ui import dialogs, main_window  # noqa: E402
from mmtx.ui.theme import apply_theme  # noqa: E402
from mmtx.ui.volumes import Volume  # noqa: E402

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots")
os.makedirs(OUT, exist_ok=True)
FF = find_ffmpeg()

LOOKS = [
    "gradients=c0=0x0b3d91:c1=0xf6a04d:c2=0x1b998b:n=3:speed=0.02",
    "mandelbrot",
    "testsrc2",
    "gradients=c0=0x2b0f54:c1=0xff6f3c:n=2:speed=0.03",
    "life=mold=10:ratio=0.1:death_color=#0d2b45:life_color=#3ec6c0",
    "cellauto=rule=110",
    "gradients=c0=0x113f2e:c1=0x9bd36a:c2=0xe8f1a0:n=3:speed=0.015",
    "smptehdbars",
    "gradients=c0=0x081c3a:c1=0x2f80ed:c2=0xf2f2f2:n=3:speed=0.02",
]


def lavfi(src: str, size: str) -> str:
    return f"{src}:size={size}:rate=10" if "=" in src else f"{src}=size={size}:rate=10"


def make_clip(path: str, look: str, seconds: float, size: str = "640x360") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", lavfi(look, size), "-t", str(seconds),
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-pix_fmt", "yuv420p", "-f", "mp4", path],
                   check=True)


def make_photo(path: str, look: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", lavfi(look, "1920x1080"), "-frames:v", "1",
                    "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", path], check=True)


def pad_mp4(path: str, extra: int) -> None:
    """Append a valid 'free' box so the clip is big enough to watch progress on."""
    with open(path, "ab") as f:
        f.write((8 + extra).to_bytes(4, "big") + b"free")
        block = b"\0" * (8 << 20)
        left = extra
        while left > 0:
            f.write(block[: min(left, len(block))])
            left -= len(block)


def stamp(path: str, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def build_demo_card(root: str) -> None:
    os.makedirs(os.path.join(root, "MISC"), exist_ok=True)
    with open(os.path.join(root, "MISC", "version.txt"), "w") as f:
        f.write('{"camera type":"HERO13 Black"}')
    dc = os.path.join(root, "DCIM", "100GOPRO")
    base = datetime(2026, 11, 14, 6, 5)
    plan = [(0, 7), (1, 4), (2, 3), (4, 5), (5, 2), (6, 3)]
    n, look = 1, 0
    for back, count in plan:
        day = base - timedelta(days=back)
        for j in range(count):
            t = day + timedelta(minutes=7 * j)
            if j == count - 1 and back in (0, 4):
                p = os.path.join(dc, f"GOPR{n:04d}.JPG")
                make_photo(p, LOOKS[look % len(LOOKS)])
                stamp(p, t)
            else:
                main = os.path.join(dc, f"GX01{n:04d}.MP4")
                lrv = os.path.join(dc, f"GL01{n:04d}.LRV")
                make_clip(lrv, LOOKS[look % len(LOOKS)], seconds=2 + (n % 5) * 1.5)
                shutil.copy(lrv, main)
                if back == 0 and j in (0, 1):
                    pad_mp4(main, (900 if j == 0 else 400) << 20)  # two big clips for live progress
                stamp(lrv, t)
                stamp(main, t)
            n += 1
            look += 1


def wait_until(app, cond, timeout=180.0):
    t0 = time.time()
    while not cond():
        app.processEvents()
        time.sleep(0.01)
        if time.time() - t0 > timeout:
            raise TimeoutError("condition not met")


def main() -> int:
    if not FF:
        print("ffmpeg needed for the demo clips")
        return 1
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(make_icon())
    shots: list[str] = []

    def grab(widget, name):
        p = os.path.join(OUT, name)
        widget.grab().save(p)
        shots.append(p)
        print("saved", p)

    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    summaries = []

    class AutoSummary(dialogs.SummaryDialog):
        def exec(self):  # noqa: A003
            self.resize(780, self.sizeHint().height())
            self.show()
            app.processEvents()
            summaries.append(self.res)
            grab(self, f"summary_{len(summaries)}_{self.res.kind}.png")
            self.accept()
            return 1

    main_window.SummaryDialog = AutoSummary
    planner.same_device = lambda a, b: False  # pretend NVMe -> SATA so the real copy path runs

    card = os.path.join(TMP, "GOPRO_CARD")
    work = os.path.join(TMP, "NVMe", "Footage")
    arch = os.path.join(TMP, "SATA", "Archive")
    t0 = time.time()
    build_demo_card(card)
    print(f"demo card built in {time.time() - t0:.1f}s")
    os.makedirs(os.path.join(work, "2026_11_08_Trail_Loop"))

    w = main_window.MainWindow()
    w.settings.workers = 2  # show parallel mode in the screenshots
    w._update_mode()
    w.resize(1480, 940)
    w.show()
    app.processEvents()
    # what the sidebar shows with real cards in two readers
    w.sidebar.set_volumes([
        Volume(card, "GOPRO", 128_000_000_000, "exfat", "GoPro HERO13 Black", False, "SD card"),
        Volume("/media/DJI_MINI4", "DJI", 256_000_000_000, "exfat", "DJI", False, "SD card"),
    ])
    w.offload.dest.set_path(work)
    w.offload.default_title.setText("DailyRun")
    w.offload.set_source(card)
    wait_until(app, lambda: w.offload.scan is not None and w.offload.sections)
    wait_until(app, lambda: not w.loader.inflight)
    secs = w.offload.sections
    secs[1].title.setText("Beach Sunrise")
    secs[1].edited = True
    w.offload._select("all")
    secs[-1].check.setChecked(False)
    secs[0].grid.set_expanded(True)
    for _ in range(20):
        app.processEvents()
    grab(w, "1_offload.png")

    got = {"done": False}

    def try_progress_shot():
        if got["done"] or not w.busy:
            return
        v = w.progress.bar.value()
        if 80 < v < 700 and w.progress.more.isVisible():
            app.processEvents()
            grab(w, "2_offload_progress.png")
            got["done"] = True

    t = QTimer()
    t.timeout.connect(try_progress_shot)
    t.start(30)
    w.offload._start()
    wait_until(app, lambda: not w.busy and summaries)
    t.stop()
    res = summaries[-1]
    assert res.ok, res.errors
    assert res.workers == 2, res.workers
    print(f"offload: {res.files_copied} copied, {res.files_failed} failed, workers={res.workers}, "
          f"copy {res.copy_rate / 1e6:.0f} MB/s, read-back {res.verify_rate / 1e6:.0f} MB/s")
    for _ in range(10):
        app.processEvents()
    statuses = [s.status.text() for s in w.offload.sections]
    print("statuses after offload:", statuses)
    assert sum(1 for x in statuses if x.startswith("✓")) == 5, statuses
    grab(w, "1b_offload_after.png")

    # ---- archive
    w.show_page(1)
    w.archive.dst.set_path(arch)
    w.archive.src.set_path(work)
    wait_until(app, lambda: len(w.archive.folders) > 0 and not (w.archive.worker and w.archive.worker.isRunning()))
    wait_until(app, lambda: not w.loader.inflight)
    app.processEvents()
    w.archive.older.setCurrentIndex(w.archive.older.findData(3))
    w.archive._select("all")
    for r, wf in enumerate(w.archive.folders):
        if wf.name.startswith("2026_11_14"):
            w.archive.table.item(r, 0).setCheckState(Qt.Unchecked)
    for _ in range(10):
        app.processEvents()
    grab(w, "3_archive.png")
    n_before = len(summaries)
    w.archive._start()
    wait_until(app, lambda: not w.busy and len(summaries) > n_before)
    res = summaries[-1]
    assert res.ok, res.errors
    print("archive:", res.files_copied, "copied+verified,", sum(g.source_deleted for g in res.groups), "folders removed")
    assert os.path.isdir(os.path.join(arch, "2026", "2026_11_13_Beach_Sunrise"))
    assert not os.path.exists(os.path.join(work, "2026_11_13_Beach_Sunrise"))
    assert os.path.isdir(os.path.join(work, "2026_11_14_DailyRun"))

    # ---- verify (with one rotted file)
    victim_dir = os.path.join(arch, "2026", "2026_11_10_DailyRun", "GoPro_HERO13_Black")
    victim = os.path.join(victim_dir, sorted(os.listdir(victim_dir))[0])
    with open(victim, "r+b") as f:
        f.seek(777)
        f.write(b"\xde\xad")
    w.show_page(2)
    w.verify.path.set_path(arch)
    n_before = len(summaries)
    w.verify._verify()
    wait_until(app, lambda: not w.busy and len(summaries) > n_before)
    res = summaries[-1]
    assert res.mismatched == 1, res.mismatched
    app.processEvents()
    grab(w, "4_verify.png")
    print("verify:", res.verified_ok, "ok", res.mismatched, "mismatch")

    sd = dialogs.SettingsDialog(w.settings, w)
    sd.resize(720, 640)
    sd.show()
    for _ in range(5):
        app.processEvents()
    grab(sd, "5_settings.png")
    sd.close()

    # empty state
    w.show_page(0)
    w.offload._clear_scan("Insert a camera card, or Browse to a folder.")
    app.processEvents()
    grab(w, "6_empty.png")
    w.close()
    print("ALL GUI CHECKS PASSED")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
