"""Headless run-through of MMTX Library with real clips and photos. Saves screenshots.
Usage: python tests/gui_library.py <out_dir>"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

os.environ["TZ"] = "America/New_York"
if hasattr(time, "tzset"):
    time.tzset()
TMP = tempfile.mkdtemp(prefix="mmtx_lib_gui_")
os.environ["XDG_CONFIG_HOME"] = os.path.join(TMP, "cfg")
os.environ["XDG_CACHE_HOME"] = os.path.join(TMP, "cache")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from make_fake_takeout import make_takeout  # noqa: E402
from mmtx import takeout as T  # noqa: E402
from mmtx.app import make_icon  # noqa: E402
from mmtx.media import find_ffmpeg  # noqa: E402
from mmtx.planner import OffloadOptions, day_folder_name, plan_offload  # noqa: E402
from mmtx.scanner import scan_card  # noqa: E402
from mmtx.transfer import TransferRunner  # noqa: E402
from mmtx.ui import library_app  # noqa: E402
from mmtx.ui.theme import apply_theme  # noqa: E402

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "shots")
os.makedirs(OUT, exist_ok=True)
FF = find_ffmpeg()
LOOKS = ["gradients=c0=0x0b3d91:c1=0xf6a04d:c2=0x1b998b:n=3:speed=0.02", "mandelbrot", "testsrc2",
         "gradients=c0=0x2b0f54:c1=0xff6f3c:n=2:speed=0.03", "life=mold=10:ratio=0.1:death_color=#0d2b45:life_color=#3ec6c0",
         "gradients=c0=0x113f2e:c1=0x9bd36a:c2=0xe8f1a0:n=3:speed=0.015", "smptehdbars",
         "gradients=c0=0x081c3a:c1=0x2f80ed:c2=0xf2f2f2:n=3:speed=0.02", "cellauto=rule=110"]


def lavfi(src, size):
    return f"{src}:size={size}:rate=10" if "=" in src else f"{src}=size={size}:rate=10"


def clip(path, look, seconds, size="640x360"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", lavfi(look, size), "-t", str(seconds), "-c:v",
                    "libx264", "-preset", "ultrafast", "-g", "10", "-pix_fmt", "yuv420p", "-f", "mp4", path], check=True)


def photo(path, look):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", lavfi(look, "1600x1200"), "-frames:v", "1",
                    "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", path], check=True)


def stamp(p, when):
    os.utime(p, (when.timestamp(), when.timestamp()))


def build_cards(root):
    """A GoPro, a DJI drone, an Insta360 and a Sony FX3 card spread over two years."""
    days = [datetime(2026, 11, 14, 6, 5), datetime(2026, 11, 9, 7, 0), datetime(2026, 8, 2, 18, 30),
            datetime(2026, 3, 21, 9, 10), datetime(2025, 12, 25, 10, 0), datetime(2025, 7, 4, 20, 15)]
    n = 1
    k = 0
    g = os.path.join(root, "GOPRO")
    os.makedirs(os.path.join(g, "MISC"))
    open(os.path.join(g, "MISC", "version.txt"), "w").write('{"camera type":"HERO13 Black"}')
    for d in days[:4]:
        for j in range(4 if d.month == 11 else 3):
            t = d + timedelta(minutes=9 * j)
            lrv = os.path.join(g, "DCIM", "100GOPRO", f"GL01{n:04d}.LRV")
            main = lrv.replace("GL01", "GX01").replace(".LRV", ".MP4")
            clip(lrv, LOOKS[k % len(LOOKS)], 3 + (n % 4) * 1.5)
            shutil.copy(lrv, main)
            stamp(lrv, t)
            stamp(main, t)
            n += 1
            k += 1
    dj = os.path.join(root, "DJI", "DCIM", "100MEDIA")
    for d in (days[0], days[2], days[5]):
        for j in range(3):
            t = d + timedelta(minutes=5 * j + 40)
            name = f"DJI_{t:%Y%m%d%H%M%S}_{j + 1:04d}_D"
            clip(os.path.join(dj, name + ".MP4"), LOOKS[k % len(LOOKS)], 4)
            shutil.copy(os.path.join(dj, name + ".MP4"), os.path.join(dj, name + ".LRF"))
            stamp(os.path.join(dj, name + ".MP4"), t)
            k += 1
    ins = os.path.join(root, "INSTA", "DCIM", "Camera01")
    for d in (days[1], days[4]):
        for j in range(2):
            t = d + timedelta(minutes=12 * j + 5)
            name = f"VID_{t:%Y%m%d_%H%M%S}_00_{j + 1:03d}.insv"
            clip(os.path.join(ins, name), LOOKS[k % len(LOOKS)], 5, "960x480")
            stamp(os.path.join(ins, name), t)
            k += 1
    so = os.path.join(root, "FX3", "PRIVATE", "M4ROOT", "CLIP")
    for j, d in enumerate((days[3], days[3], days[4])):
        t = d + timedelta(hours=2, minutes=20 * j)
        p = os.path.join(so, f"C{j + 1:04d}.MP4")
        clip(p, LOOKS[k % len(LOOKS)], 6, "1280x720")
        open(os.path.join(so, f"C{j + 1:04d}M01.XML"), "w").write(
            f'<?xml version="1.0"?><NonRealTimeMeta><CreationDate value="{t:%Y-%m-%dT%H:%M:%S}-05:00"/>'
            '<Device manufacturer="Sony" modelName="ILME-FX3"/></NonRealTimeMeta>')
        stamp(p, t)
        k += 1
    gp = os.path.join(root, "GOPRO", "DCIM", "100GOPRO")
    for j, d in enumerate(days[:2]):
        p = os.path.join(gp, f"GOPR{9000 + j}.JPG")
        photo(p, LOOKS[(k + j) % len(LOOKS)])
        stamp(p, d + timedelta(minutes=50))


def offload(cards_root, work):
    titles = {"2026-11-14": "DailyRun", "2026-11-09": "Trail Loop", "2026-08-02": "Beach Sunset",
              "2026-03-21": "Spring Ride", "2025-12-25": "Christmas", "2025-07-04": "Fireworks"}
    for card in sorted(os.listdir(cards_root)):
        scan = scan_card(os.path.join(cards_root, card))
        dev = {p: lbl.replace(" ", "_") for p, lbl in scan.devices.items()}
        sel = [(d, day_folder_name(d.day, titles.get(d.day.isoformat(), "Run"))) for d in scan.days]
        res = TransferRunner(plan_offload(scan, sel, work, dev, OffloadOptions(algo="xxh128"))).run()
        assert res.ok, res.errors


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(make_icon())

    def pump(n=10):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    def wait(cond, secs=60):
        t0 = time.time()
        while not cond():
            pump(2)
            if time.time() - t0 > secs:
                raise TimeoutError
        pump(5)

    def grab(widget, name):
        p = os.path.join(OUT, name)
        widget.grab().save(p)
        print("saved", p)

    def thumbs_idle():
        return w.thumbs.idle()

    def visible_rows(win):
        g = win.grid
        top, vh = g.verticalScrollBar().value(), g.viewport().height()
        out = []
        for i, r in enumerate(g.rows):
            rc = g._tile_rect(i)
            if rc.bottom() > top and rc.top() < top + vh:
                out.append(r)
        return out

    # -------- data
    build_cards(os.path.join(TMP, "cards"))
    work = os.path.join(TMP, "Footage")
    os.makedirs(work)
    offload(os.path.join(TMP, "cards"), work)
    photos = os.path.join(TMP, "Photos")
    src = os.path.join(TMP, "Takeouts")
    make_takeout(src, n_extra=40)
    try:  # a real HEIC, the format of every iPhone photo
        from PIL import Image
        from pillow_heif import register_heif_opener

        register_heif_opener()
        im = Image.new("RGB", (1200, 900), (30, 90, 160))
        for x in range(0, 1200, 60):
            im.paste((240, 170, 60), (x, 0, x + 30, 900))
        im.save(os.path.join(src, "IMG_8800.HEIC"))
        stamp(os.path.join(src, "IMG_8800.HEIC"), datetime(2026, 1, 22, 15, 0))
    except Exception as e:  # noqa: BLE001
        print("no HEIC:", e)
    T.TakeoutRunner(T.plan_takeout([src], photos), T.TakeoutOptions()).run()

    class S:  # what MMTX's settings would say
        offload_dest = work
        archive_dest = ""
        takeout_library = photos

    w = library_app.LibraryWindow(S())
    w.resize(1480, 940)
    w.show()
    pump(20)
    assert w.pages.currentWidget() is w.empty
    grab(w, "lib_0_welcome.png")

    # -------- footage drive
    w.previews.set_paused(user=True)  # hold the background run to see its card
    w.add_library(work)
    wait(lambda: w.cat is not None and w.rows and w.index_worker is None, 120)
    wait(thumbs_idle, 120)
    wait(lambda: w.prep_box.isVisible(), 30)
    assert "paused" in w.prep_title.text().lower(), w.prep_title.text()
    grab(w.side, "lib_9_previews_paused.png")
    w._toggle_prep_pause()  # resume: everything gets its preview once
    wait(lambda: not w.previews.busy, 240)
    assert not w.prep_box.isVisible()
    from mmtx import previews as PV
    left = PV.todo(w.cat.query(), w.thumbs.store)
    assert not left, [r.name for r in left]
    assert not w.state.get("previews_paused")
    w.grid.viewport().update()
    pump(20)
    devs = {r.device for r in w.rows}
    assert {"gopro", "dji", "insta360", "sony"} <= devs, devs
    assert not any(r.name.upper().endswith((".LRV", ".LRF", ".XML")) for r in w.rows)
    grab(w, "lib_1_footage.png")
    # hover-scrub a GoPro clip
    gi = next(i for i, r in enumerate(w.rows) if r.device == "gopro" and r.kind == "video")
    rect = w.grid._tile_rect(gi).translated(0, -w.grid.verticalScrollBar().value())
    pos = QPointF(rect.left() + rect.width() * 0.7, rect.center().y())
    w.grid.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, pos, pos, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    wait(lambda: w.thumbs.strips, 60)
    w.grid.mouseMoveEvent(QMouseEvent(QMouseEvent.MouseMove, pos, pos, Qt.NoButton, Qt.NoButton, Qt.NoModifier))
    pump(10)
    grab(w.grid, "lib_2_hover_scrub.png")
    # cameras filter + search
    w.set_filter({"device": "sony", "model": "Sony FX3"})
    pump(10)
    assert w.rows and all(r.model == "Sony FX3" for r in w.rows), [r.model for r in w.rows]
    w.set_filter({})
    w.search.setText("beach")
    wait(lambda: not w._search_timer.isActive(), 5)
    assert w.rows and all(r.title == "Beach Sunset" for r in w.rows), {r.title for r in w.rows}
    w.search.setText("")
    wait(lambda: not w._search_timer.isActive(), 5)
    # select a shoot, collect, export
    w.grid.selected = {r.path for r in w.rows if r.title == "DailyRun"}
    w.grid.selection_changed.emit()
    pump(10)
    assert w.status_row.currentIndex() == 1
    grab(w, "lib_3_selected.png")
    sel = w.grid.selected_rows()
    w.cat.add_to_collection("Beach Run edit", [r.path for r in sel])
    w._reload(keep_scroll=True)
    assert any(n == "Beach Run edit" for n, _c in w._facets["collections"])
    proj = os.path.join(TMP, "EditProject", "footage")
    dlg = library_app.ExportDialog(w, w.root, sel, w.state)
    dlg.dest.set_path(proj, emit=False)
    dlg.days.setChecked(True)
    dlg.show()
    dlg._go()
    wait(lambda: dlg.result is not None, 60)
    grab(dlg, "lib_5_export_done.png")
    assert dlg.result.ok and dlg.result.files_copied == len(sel), (dlg.result.errors, dlg.result.files_copied)
    out_dir = os.path.join(proj, "2026_11_14_DailyRun")
    assert os.path.isdir(out_dir) and os.path.isfile(os.path.join(out_dir, "_mmtx_checksums.xxh128"))
    dlg.accept()
    # viewer on a clip
    vi = next(i for i, r in enumerate(w.rows) if r.device == "dji")
    w.open_viewer(vi)
    pump(40)
    grab(w, "lib_4b_viewer_video.png")
    w.viewer.close_viewer()

    # -------- the Google Photos library
    w.add_library(photos)
    wait(lambda: w.cat is not None and w.cat.kind == "photos" and w.index_worker is None, 120)
    wait(thumbs_idle, 120)
    w.grid.viewport().update()
    pump(20)
    assert not any("Takeouts" in r.path for r in w.rows)
    grab(w, "lib_6_photos.png")
    hi = next(i for i, r in enumerate(w.rows) if r.name == "IMG_0005.jpg")
    w.open_viewer(hi)
    wait(lambda: w.viewer.image.pm is not None, 20)
    pump(30)
    grab(w, "lib_4_viewer_photo.png")
    w.viewer.close_viewer()
    heic = [r for r in w.rows if r.name == "IMG_8800.HEIC"]
    if heic:
        assert not w.thumbs.is_missing(heic[0]), "HEIC thumbnail failed"
    w.search.setText("zulu")
    wait(lambda: not w._search_timer.isActive(), 5)
    assert {r.name for r in w.rows} == {"IMG_0005.jpg", "IMG_0005-edited.jpg"}, {r.name for r in w.rows}
    grab(w, "lib_7_search.png")
    w.search.setText("")
    wait(lambda: not w._search_timer.isActive(), 5)
    w.group.setCurrentIndex(1)  # by month: sparse photo days read better
    wait(thumbs_idle, 60)
    w.grid.viewport().update()
    pump(20)
    assert all(len(sec.day) in (0, 7) for sec in w.grid.sections)
    grab(w, "lib_6b_photos_by_month.png")
    w.group.setCurrentIndex(0)
    pump(5)

    # -------- the footage drive unplugged
    entry = next(d for d in library_app.C.known_libraries() if os.path.normcase(d["root"]) == os.path.normcase(work))
    os.rename(work, work + "_away")
    w.refresh_side()
    w.open_entry(entry)
    wait(thumbs_idle, 60)
    w.grid.viewport().update()
    pump(20)
    assert not w.online and w.banner.isVisible() and w.rows
    vis = visible_rows(w)
    assert vis and all(w.thumbs.key(r) in w.thumbs.mem for r in vis), "every preview should show offline"
    grab(w, "lib_8_offline.png")
    os.rename(work + "_away", work)
    w.close()

    # -------- opening it again: nothing is made, every visible tile comes straight from the store
    w2 = library_app.LibraryWindow(S())
    w2.resize(1480, 940)
    w2.show()
    t0 = time.time()
    while not (w2.cat is not None and w2.root == work and w2.rows and visible_rows(w2)
               and all(w2.thumbs.key(r) in w2.thumbs.mem for r in visible_rows(w2))):
        app.processEvents()
        time.sleep(0.002)
        assert time.time() - t0 < 30, "reopen took too long"
    reopen_ms = (time.time() - t0) * 1000
    assert w2.thumbs.made_on_demand == 0, w2.thumbs.made_on_demand
    print(f"reopened: {len(visible_rows(w2))} visible previews in {reopen_ms:.0f} ms (window, catalog and grid "
          f"included), 0 made")
    # a video opens on its poster and the camera's low-res copy
    vi = next(i for i, r in enumerate(w2.rows) if r.device == "gopro" and r.kind == "video" and r.proxy)
    w2.open_viewer(vi)
    pump(3)
    assert w2.viewer.vposter.pm is not None, "the poster shows at once"
    assert w2.viewer.proxy_btn.isChecked() and w2.viewer.player.source().toLocalFile().upper().endswith(".LRV")
    wait(lambda: w2.viewer.vstack.currentWidget() is w2.viewer.video, 20)  # swaps once frames play
    w2.viewer.close_viewer()
    w2.close()

    # -------- MMTX keeps the catalogs current after its own jobs, and opens the Library
    from mmtx.ui import main_window

    mw = main_window.MainWindow()
    mw.watcher.changed.disconnect()
    newday = os.path.join(work, "2026_11_16_Night_Ride")
    clip(os.path.join(newday, "GX019999.MP4"), LOOKS[0], 2)

    class Res:  # what an offload of that shoot reports
        kind, files_copied, files_moved, dest_dirs = "offload", 1, 0, [newday]

    mw._catalog_after_job(Res())
    wait(lambda: not mw._catalog_workers, 60)
    with library_app.C.Catalog(work) as c:
        night = [r for r in c.query() if r.title == "Night Ride"]
        assert night, "the offload's folder wasn't catalogued"
    wait(lambda: mw._previews is not None and not mw._previews.busy, 60)
    assert not PV.todo(night, PV.shared_store()), "MMTX should make the new clip's preview right away"
    assert mw._library_root_for(os.path.join(photos, "2026", "2026_01", "2026_01_22")) == photos
    mw.open_library(work)
    lw = mw.library_win
    wait(lambda: lw.cat is not None and lw.index_worker is None and lw.root == work, 60)
    assert any(r.title == "Night Ride" for r in lw.rows)
    lw.group.setCurrentIndex(1)  # by month
    pump(10)
    assert all(len(sec.day) in (0, 7) for sec in lw.grid.sections)
    lw.close()
    mw.close()
    print("library GUI run-through ok")
    shutil.rmtree(TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
