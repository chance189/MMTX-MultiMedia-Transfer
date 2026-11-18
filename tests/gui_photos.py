"""Headless run-through of the Google Photos pages (Advanced, then Easy) with fake takeouts.
Saves screenshots. Usage: python tests/gui_photos.py <out_dir>"""

from __future__ import annotations

import json
import os
import sys
import time

os.environ["TZ"] = "America/New_York"
if hasattr(time, "tzset"):
    time.tzset()
sys.path.insert(0, os.path.dirname(__file__))
import gui_smoke as G  # noqa: E402  (temp dirs, env)

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from make_fake_takeout import GP, jpeg, make_takeout, mov, sidecar, ts, write_zip  # noqa: E402
from mmtx import takeout as T  # noqa: E402
from mmtx.app import make_icon  # noqa: E402
from mmtx.ui import dialogs, easy_widgets, main_window  # noqa: E402
from mmtx.ui.theme import apply_theme  # noqa: E402
from mmtx.ui.volumes import Volume  # noqa: E402

OUT = G.OUT


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(make_icon())

    def grab(widget, name):
        p = os.path.join(OUT, name)
        widget.grab().save(p)
        print("saved", p)

    def pump(n=15):
        for _ in range(n):
            app.processEvents()
            time.sleep(0.01)

    seen: list[tuple[str, str]] = []

    def fake_exec(self):
        self.show()
        pump(2)
        seen.append((getattr(self, "kind", ""), self.windowTitle() or ""))
        grab(self, f"photos_dialog_{len(seen)}.png")
        self.choice = 0
        self.accept()
        return 1

    easy_widgets.EasyDialog.exec = fake_exec
    summaries = []

    class AutoSummary(dialogs.SummaryDialog):
        def exec(self):  # noqa: A003
            self.resize(820, self.sizeHint().height())
            self.show()
            pump(3)
            summaries.append(self.res)
            grab(self, "photos_3_summary.png")
            self.accept()
            return 1

    main_window.SummaryDialog = AutoSummary

    # slow each photo down a little so the screenshots catch the import mid-way
    real = T.TakeoutRunner._process

    def slow(self, it):
        time.sleep(0.03)
        return real(self, it)

    T.TakeoutRunner._process = slow

    downloads = os.path.join(G.TMP, "Downloads")
    expect = make_takeout(downloads, n_extra=90)
    # loose videos in the same folder as the zips
    open(os.path.join(downloads, "IMG_1500.MOV"), "wb").write(mov(1500, apple="2026-02-14T23:30:00-0500"))
    open(os.path.join(downloads, "IMG_1502.MOV"), "wb").write(mov(1502))
    when = time.mktime((2025, 8, 9, 12, 0, 0, 0, 0, -1))
    os.utime(os.path.join(downloads, "IMG_1502.MOV"), (when, when))
    n_loose = 2
    big = os.path.join(G.TMP, "BigDrive")
    os.makedirs(big)
    lib = os.path.join(big, "Photos")

    w = main_window.MainWindow()
    w.watcher.changed.disconnect()
    w.resize(1480, 980)
    w.show()
    pump()
    vols = [Volume(big, "Photos drive", 4_000_000_000_000, "ntfs", None, False, "HDD")]
    w._on_volumes(vols)
    w.easy.on_volumes(vols)

    # ---------------------------------------------------------------- Advanced
    w.set_easy(False)
    w.show_page(3)
    ses = w.takeout
    assert w.photos.go.text() == "Add takeout zips first", w.photos.go.text()
    ses.add([downloads])
    w.photos.lib.set_path(lib)
    G.wait_until(app, lambda: not ses.planning and ses.plan is not None, 60)
    pump(20)
    plan = ses.plan
    assert len(plan.archives) == 3 and plan.trash == 1, (len(plan.archives), plan.trash)
    assert w.photos.go.isEnabled(), w.photos.go.text()
    assert w.photos.table.rowCount() == 3
    grab(w, "photos_1_advanced_ready.png")
    events: list = []
    ses.photo.connect(events.append)
    w.photos.go.click()
    G.wait_until(app, lambda: len(events) >= 45, 60)
    pump(3)
    grab(w, "photos_2_advanced_importing.png")
    G.wait_until(app, lambda: not w.busy, 120)
    pump(30)
    assert summaries and summaries[-1].ok, (summaries[-1].errors if summaries else "no summary")
    res = summaries[-1]
    assert res.new == len(expect) + n_loose and res.duplicates == 1, (res.new, res.duplicates, len(expect))
    assert res.date_sources.get("filedate") == 1, res.date_sources
    grab(w, "photos_4_advanced_done.png")
    # what's on disk
    assert os.path.isfile(os.path.join(lib, "2026/2026_01/2026_01_22/IMG_6367.HEIC"))
    assert os.path.isfile(os.path.join(lib, "2025/2025_03/2025_03_11/TOKYO1.jpg"))
    assert os.path.isfile(os.path.join(lib, "2026/2026_02/2026_02_14/IMG_1500.MOV"))
    assert os.path.isfile(os.path.join(lib, "2025/2025_08/2025_08_09/IMG_1502.MOV"))
    assert sorted(os.listdir(downloads)) == ["IMG_1500.MOV", "IMG_1502.MOV", "takeout-20261101T120000Z-001.zip",
                                             "takeout-20261101T120000Z-002.zip"]
    meta = json.load(open(os.path.join(lib, "2025/2025_06/2025_06_02", T.DAY_META)))
    assert meta["files"]["IMG_5000.jpg" if False else "IMG_0005.jpg"]["albums"] == ["Beach Trip"]
    G.wait_until(app, lambda: not ses.planning, 30)
    pump(10)
    assert ses.plan is not None and ses.plan.skipped_archives == 2 and ses.plan.skipped_loose == n_loose \
        and not ses.plan.items, (ses.plan.skipped_archives, ses.plan.skipped_loose, len(ses.plan.items))
    assert not w.photos.go.isEnabled()

    # ---------------------------------------------------------------- Easy: a new library from scratch
    ses.clear()
    w.settings.takeout_library = ""
    ses.set_library("")
    w.settings.easy_tour_done = True
    w.set_easy(True)
    pump()
    e = w.easy
    e.tab_photos.click()
    pump(10)
    assert e.title.text() == "Bring in your Google Photos", e.title.text()
    assert e.primary.isVisible()
    grab(w, "photos_5_easy_add.png")
    e._photos_add([downloads])
    G.wait_until(app, lambda: not ses.planning and ses.plan is not None and not e._advancing, 30)
    pump(20)
    assert e.title.text() == "Where should your photo library live?", e.title.text()
    grab(w, "photos_6_easy_library.png")
    e._photos_use_suggestion()
    G.wait_until(app, lambda: not ses.planning and not e._advancing and ses.plan is not None
                 and ses.library_ok(), 30)
    pump(30)
    lib2 = w.settings.takeout_library
    assert lib2 == os.path.join(big, "Google Photos"), lib2
    assert e.start.isVisible() and e.years.isVisible(), e.title.text()
    grab(w, "photos_7_easy_ready.png")
    n_before = len(seen)
    e._photos_go()
    G.wait_until(app, lambda: e.stage.currentWidget() is e.run_view and len(events) >= len(expect) + 1 + n_loose + 40, 60)
    pump(3)
    grab(w, "photos_8_easy_importing.png")
    G.wait_until(app, lambda: not w.busy and len(seen) > n_before, 120)
    pump(40)
    assert seen[-1][0] == "ok", seen[-1]
    assert os.path.isfile(os.path.join(lib2, "2026/2026_01/2026_01_11/IMG_6282.HEIC"))
    G.wait_until(app, lambda: not ses.planning, 30)
    pump(30)
    assert e.title.text() == "Everything in these takeouts is in your library", e.title.text()
    grab(w, "photos_9_easy_all_in.png")

    # ---------------------------------------------------------------- next year's takeout, dropped on the window
    later = os.path.join(G.TMP, "Downloads2")
    yr = f"{GP}/Photos from 2026"
    write_zip(os.path.join(later, "takeout-20261201T090000Z-001.zip"), [
        (f"{yr}/IMG_9001.jpg", jpeg(901)), (f"{yr}/IMG_9001.jpg.supplemental-metadata.json", sidecar("IMG_9001.jpg", ts(2026, 11, 20))),
        (f"{yr}/IMG_9002.jpg", jpeg(902)), (f"{yr}/IMG_9002.jpg.supplemental-metadata.json", sidecar("IMG_9002.jpg", ts(2026, 11, 21))),
        (f"{yr}/IMG_7000.jpg", jpeg(200)), (f"{yr}/IMG_7000.jpg.supplemental-metadata.json", sidecar("IMG_7000.jpg", ts(2026, 2, 1, 15))),
    ])
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(os.path.join(later, "takeout-20261201T090000Z-001.zip"))])
    ev = QDropEvent(QPointF(400, 400), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    e.dropEvent(ev)
    G.wait_until(app, lambda: not ses.planning and not e._advancing, 30)
    pump(30)
    assert ses.plan.in_library == 1 and len(ses.plan.items) == 3, (ses.plan.in_library, len(ses.plan.items))
    assert e.title.text() == "2 new photos ready", e.title.text()
    grab(w, "photos_10_easy_next_takeout.png")
    n_before = len(seen)
    e._photos_go()
    G.wait_until(app, lambda: not w.busy and len(seen) > n_before, 60)
    pump(20)
    assert os.path.isfile(os.path.join(lib2, "2026/2026_11/2026_11_20/IMG_9001.jpg"))
    print("photos GUI run-through ok;", len(seen), "dialogs")
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
