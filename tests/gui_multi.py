"""Several cards and devices at once: every one is read in the background, each shows its own status,
you can switch between them, "Copy all" copies them side by side (Easy), and "Offload all cards"
does the same from Advanced. Usage: python tests/gui_multi.py <out_dir>
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import gui_smoke as G  # noqa: E402  (sets up the temp dirs, env and demo-card helpers)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from make_fake_cards import make_cards  # noqa: E402
from mmtx import hashing, planner, sources  # noqa: E402
from mmtx.app import make_icon  # noqa: E402
from mmtx.manifest import find_manifest, read_manifest  # noqa: E402
from mmtx.ui import dialogs, easy_widgets, main_window  # noqa: E402
from mmtx.ui import volumes as volmod  # noqa: E402
from mmtx.ui.theme import apply_theme  # noqa: E402
from mmtx.ui.volumes import Volume  # noqa: E402

OUT = G.OUT


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(make_icon())
    planner.same_device = lambda a, b: False

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

    def fake_exec(self):
        self.show()
        pump(2)
        seen.append((getattr(self, "kind", "help"), self.windowTitle()))
        self.choice = answers.pop(0) if answers else 0
        self.accept()
        return 1

    easy_widgets.EasyDialog.exec = fake_exec
    summaries = []

    class AutoSummary(dialogs.SummaryDialog):
        def exec(self):  # noqa: A003
            summaries.append(self.res)
            self.accept()
            return 1

    main_window.SummaryDialog = AutoSummary

    # three sources: a GoPro card with real clips, a DJI card, a phone over USB (no drive letter)
    gopro = os.path.join(G.TMP, "GOPRO_CARD")
    G.build_demo_card(gopro)
    dji = make_cards(os.path.join(G.TMP, "cards"))["dji"]
    phone_dir = os.path.join(G.TMP, "phone")
    cam = os.path.join(phone_dir, "DCIM", "Camera")
    os.makedirs(cam)
    for i in range(3):
        p = os.path.join(cam, f"PXL_20261114_07{i:02d}00123.mp4")
        with open(p, "wb") as f:
            f.write(os.urandom(3_000_000 + i))
    be = sources.DirBackend()
    be.add("pix8", "Pixel 8", phone_dir, maker="Google")
    sources.register_backend(be)
    phone_root = "portable://pix8/Internal shared storage"

    work = os.path.join(G.TMP, "NVMe", "Footage")
    os.makedirs(work)
    big = os.path.join(G.TMP, "NVMe")
    drives = [Volume(big, "NVMe", 2_000_000_000_000, "ntfs", None, False, "SSD")]
    vols = drives + [
        Volume(gopro, "GOPRO", 128_000_000_000, "exfat", "GoPro HERO13 Black", False, "SD card", icon="action"),
        Volume(dji, "DJI", 64_000_000_000, "exfat", "DJI", False, "SD card", icon="drone"),
    ] + volmod._portable_volumes()

    w = main_window.MainWindow()
    w.watcher.changed.disconnect()  # this test plugs devices in and out by hand
    s = w.settings
    s.offload_dest = work
    s.easy_tour_done = True
    s.phone_recent_days = 0  # the fake phone clips are dated Nov 2026 whatever today is
    s.save()
    w.resize(1320, 1000)
    w.show()
    pump()

    # 1. plug all three in: each is read in the background, one after another
    w._on_volumes(vols)
    pump(5)
    assert len(w.cards) == 3, list(w.cards)
    assert w.card_root == gopro, "the first card is selected"
    G.wait_until(app, lambda: all(e.state == "ready" for e in w.cards.values()), timeout=300)
    for e in w.cards.values():
        text, tone = e.status()
        assert tone == "new" and "new" in text, (e.name, text)
    assert w.cards[phone_root].portable and w.cards[phone_root].icon == "phone"

    # 2. Easy: the switcher shows "All 3 devices" + one chip each; the All view offers Copy all
    w.set_easy(True)
    pump(20)
    G.wait_until(app, lambda: not w.loader.inflight)
    pump(10)
    ez = w.easy
    assert ez.switcher.isVisible() and list(ez.switcher.chips) == ["", gopro, dji, phone_root]
    assert ez._view_all and ez.switcher.chips[""].selected
    assert ez.title.text().endswith("on 3 devices"), ez.title.text()
    assert ez.start.btn.text() == "Copy all 3 devices", ez.start.btn.text()
    ez.start._t = 0.5
    grab(w, "multi_1_all_devices.png")

    # 3. switch to one device: instant (already read), its own screens
    ez.switcher.chips[dji].click()
    pump(10)
    assert w.card_root == dji and not ez._view_all and ez.switcher.chips[dji].selected
    assert "DJI" in ez.chip.text and "devices" not in ez.title.text(), ez.title.text()
    assert ez.start.btn.text() == "Copy everything"
    ez.start._t = 0.5
    grab(w, "multi_2_switched_to_dji.png")
    ez.switcher.chips[phone_root].click()
    pump(10)
    assert w.card_root == phone_root and "Pixel 8" in ez.chip.text
    ez.switcher.chips[""].click()
    pump(10)
    assert ez._view_all and ez.title.text().endswith("on 3 devices")

    # 4. one name for the shoot, then Copy all: one job, devices side by side, phone one file at a time
    ez.name_box.edit.setText("Multi Cam")
    ez.name_box.edited.emit("Multi Cam")
    assert "2026_11_14_Multi_Cam" in ez.name_box.preview.text(), ez.name_box.preview.text()
    shot = {"done": False}

    def mid():
        if shot["done"] or not w.busy or ez.ring.value < 0.15:
            return
        shot["done"] = True
        grab(w, "multi_3_copying_all.png")
        chips = {r: c.status for r, c in ez.switcher.chips.items()}
        print("switcher while copying:", chips)
        assert chips[""].startswith("Copying"), chips

    t = QTimer()
    t.timeout.connect(mid)
    t.start(30)
    answers[:] = [0]
    n_before = len(seen)
    ez.start.btn.click()
    G.wait_until(app, lambda: w.busy)
    job = w.current_job
    lim = job.options.source_limits
    assert set(lim) == {gopro, dji, phone_root} and lim[phone_root] == 1, lim
    assert job.options.workers >= 3, job.options.workers
    G.wait_until(app, lambda: not w.busy and len(seen) > n_before, timeout=600)
    t.stop()
    assert seen[n_before][0] == "ok", seen[n_before:]
    assert shot["done"], "mid-copy screenshot"

    # every day folder verifies independently; Nov 14 holds all three devices' files
    total = 0
    for name in sorted(os.listdir(work)):
        folder = os.path.join(work, name)
        mpath, algo = find_manifest(folder)
        entries = read_manifest(mpath)
        for rel, h in entries.items():
            assert hashing.hash_file(os.path.join(folder, *rel.split("/")), algo, uncached=False) == h, rel
        total += len(entries)
    assert total == job.total_files, (total, job.total_files)
    d14 = os.listdir(os.path.join(work, "2026_11_14_Multi_Cam"))
    assert any(n.startswith("GX") for n in d14) and any(n.startswith("DJI_") for n in d14) \
        and any(n.startswith("PXL_") for n in d14), d14
    print(f"copied {total} files from 3 devices into {len(os.listdir(work))} day folders")

    # 5. every device is re-read after the copy and says so
    G.wait_until(app, lambda: all(e.state == "ready" and e.status()[0] == "All saved" for e in w.cards.values()),
                 timeout=300)
    pump(20)
    assert ez.title.text() == "Everything on these 3 devices is saved", ez.title.text()
    grab(w, "multi_4_all_saved.png")

    # 6. Advanced: sidebar tiles carry each card's status; new footage on two devices -> Offload all cards
    w.set_easy(False)
    pump(10)
    tiles = {t.root: t.status.text() for t in w.sidebar.card_tiles}
    assert tiles == {gopro: "All saved", dji: "All saved", phone_root: "All saved"}, tiles
    newdji = os.path.join(dji, "DCIM", "DJI_001", "DJI_20261115071500_0009_D.MP4")
    os.makedirs(os.path.dirname(newdji), exist_ok=True)
    with open(newdji, "wb") as f:
        f.write(os.urandom(2_000_000))
    with open(os.path.join(cam, "PXL_20261115_080000123.mp4"), "wb") as f:
        f.write(os.urandom(1_500_000))
    w.rescan_roots([dji, phone_root])
    G.wait_until(app, lambda: all(e.state == "ready" for e in w.cards.values()) and w.cards[dji].new_files
                 and w.cards[phone_root].new_files)
    w.select_card(gopro)
    w.show_page(0)
    pump(20)
    off = w.offload
    assert off.go_all.isVisible() and off.go_all.text().strip().startswith("Offload all 2 cards"), off.go_all.text()
    tiles = {t.root: t.status.text() for t in w.sidebar.card_tiles}
    assert tiles[dji].startswith("1 new") and tiles[phone_root].startswith("1 new"), tiles
    grab(w, "multi_5_advanced_offload_all.png")
    off.go_all.click()
    G.wait_until(app, lambda: not w.busy and summaries, timeout=300)
    res = summaries[-1]
    assert res.ok and res.files_ok == 2, (res.errors, res.files_ok)
    G.wait_until(app, lambda: all(e.state == "ready" and e.status()[0] == "All saved" for e in w.cards.values()))
    pump(10)
    grab(w, "multi_6_advanced_after.png")

    # 7. unplug the selected card: another one takes over, nothing breaks
    w._on_volumes([v for v in vols if v.root != gopro])
    pump(10)
    assert gopro not in w.cards and w.card_root in (dji, phone_root), w.card_root
    sources.unregister_backend(be)
    w._on_volumes(drives)
    pump(10)
    assert not w.cards and w.card_root == ""

    w.close()
    print("MULTI DEVICE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
