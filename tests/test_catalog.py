"""Library catalog tests: footage drives, archive drives, Google Photos libraries, search, export.
Run:  python tests/test_catalog.py  (or pytest)"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

os.environ["TZ"] = "America/New_York"
if hasattr(time, "tzset"):
    time.tzset()
_CFG = tempfile.mkdtemp(prefix="mmtx_cfg_")
os.environ["XDG_CONFIG_HOME"] = _CFG  # keep the library registry out of the real config
os.environ["XDG_CACHE_HOME"] = _CFG

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from make_fake_cards import make_cards  # noqa: E402
from make_fake_takeout import make_takeout  # noqa: E402

from mmtx import catalog as C  # noqa: E402
from mmtx import takeout as T  # noqa: E402
from mmtx.planner import OffloadOptions, day_folder_name, plan_archive, plan_offload  # noqa: E402
from mmtx.scanner import scan_card, scan_working  # noqa: E402
from mmtx.transfer import TransferRunner  # noqa: E402


def footage(tmp):
    cards = make_cards(os.path.join(tmp, "cards"))
    work = os.path.join(tmp, "Footage")
    os.makedirs(work)
    for card in cards.values():
        scan = scan_card(card)
        dev = {p: lbl.replace(" ", "_") for p, lbl in scan.devices.items()}
        titles = {"2026-11-14": "Daily Run", "2026-11-13": "Beach"}
        sel = [(d, day_folder_name(d.day, titles.get(d.day.isoformat(), "Run"))) for d in scan.days]
        res = TransferRunner(plan_offload(scan, sel, work, dev, OffloadOptions(algo="xxh128"))).run()
        assert res.ok, res.errors
    return cards, work


def test_footage_drive():
    tmp = tempfile.mkdtemp(prefix="mmtx_cat_")
    try:
        _cards, work = footage(tmp)
        with C.Catalog(work) as cat:
            r = cat.index()
            assert r.added > 10 and not r.errors, (r.added, r.errors)
            rows = cat.query()
            names = {x.name for x in rows}
            assert not any(n.upper().endswith((".LRV", ".LRF", ".THM", ".SRT", ".XML")) for n in names), names
            devs = {x.device for x in rows}
            assert {"gopro", "dji", "insta360", "sony"} <= devs, devs
            gx = next(x for x in rows if x.name == "GX010001.MP4")
            assert gx.day == "2026-11-13" and gx.title == "Beach" and gx.proxy.endswith("GL010001.LRV"), gx
            full = cat.get(gx.path)
            assert full["hash"].startswith("xxh128:"), full["hash"]
            assert {x.title for x in cat.query("beach")} == {"Beach"}
            assert all(x.device == "gopro" for x in cat.query("gopro"))
            assert all(x.device == "dji" for x in cat.query(device="dji")) and cat.query(device="dji")
            assert cat.query("november 2026") and not cat.query("march 2019")
            assert cat.query(kind="photo") and all(x.kind == "photo" for x in cat.query(kind="photo"))
            fx = [x for x in rows if x.device == "sony"]
            assert fx and fx[0].model == "Sony FX3", fx[0].model
            # incremental: nothing changed, nothing opened
            r2 = cat.index()
            assert r2.added == 0 and r2.updated == 0 and r2.unchanged == r.added, (r2.added, r2.updated)
            # one file grows, one disappears
            with open(os.path.join(work, *gx.path.split("/")), "ab") as fh:
                fh.write(b"x")
            dji = next(x for x in rows if x.device == "dji")
            os.remove(os.path.join(work, *dji.path.split("/")))
            r3 = cat.index()
            assert r3.updated == 1 and r3.removed == 1, (r3.updated, r3.removed)
            # only some folders (after a job)
            r4 = cat.index(folders=[gx.folder])
            assert r4.folders == 1 and r4.removed == 0
            f = cat.facets()
            assert f["total"] == len(rows) - 1 and f["kinds"]["video"] > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_archive_drive_and_saved_copy():
    tmp = tempfile.mkdtemp(prefix="mmtx_cat_")
    try:
        _cards, work = footage(tmp)
        arch = os.path.join(tmp, "Archive")
        job = plan_archive(scan_working(work), arch, "year", move=False, default_algo="xxh128")
        res = TransferRunner(job).run()
        assert res.ok, res.errors
        res_i, entry = C.index_and_remember(arch)
        assert res_i.added > 10
        with C.Catalog(arch) as cat:
            rows = cat.query()
            assert all(x.folder.startswith("2026/2026_11_") for x in rows), rows[0].folder
            lid = cat.library_id
        assert entry["id"] == lid and any(d["id"] == lid for d in C.known_libraries())
        # drive unplugged: the saved copy still answers
        copy = C.saved_copy_path(lid)
        assert os.path.isfile(copy)
        moved = arch + "_unplugged"
        os.rename(arch, moved)
        assert C.locate(entry) is None
        with C.Catalog.open_file(copy, arch) as off:
            assert len(off.query()) == len(rows) and off.query("beach")
        os.rename(moved, arch)
        assert C.locate(entry) == arch
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_photo_library_search_collections_export():
    tmp = tempfile.mkdtemp(prefix="mmtx_cat_")
    try:
        lib = os.path.join(tmp, "Photos")
        src = os.path.join(lib, "Takeouts")  # the takeouts parked inside the library are not catalogued
        expect = make_takeout(src)
        open(os.path.join(src, "IMG_7777.MOV"), "wb").write(b"\x00" * 100)
        T.TakeoutRunner(T.plan_takeout([src], lib), T.TakeoutOptions()).run()
        with C.Catalog(lib) as cat:
            r = cat.index()
            assert not r.errors, r.errors
            rows = cat.query()
            assert len(rows) == len(expect) + 1, (len(rows), len(expect))  # + IMG_7777.MOV dated by file
            assert not any("Takeouts" in x.path for x in rows)
            assert cat.kind == "photos"
            heic = next(x for x in rows if x.name == "IMG_6367.HEIC")
            assert heic.day == "2026-01-22" and heic.model == "iPhone" and heic.device == "phone", heic
            both = {"IMG_0005.jpg", "IMG_0005-edited.jpg"}  # the edited copy shares its original's details
            assert {x.name for x in cat.query("zulu")} == both  # people
            assert {x.name for x in cat.query("sunset pier")} == both  # caption
            assert {x.name for x in cat.query(album="Beach Trip")} == {"IMG_0005.jpg"}
            assert len(cat.query("-iphone")) < len(rows)
            unknown = [x for x in rows if x.day == ""]
            assert [x.name for x in unknown] == ["random.png"], [x.name for x in unknown]
            assert cat.facets()["albums"] == [("Beach Trip", 1)]
            # your marks
            cat.set_fav([heic.path], True)
            assert [x.name for x in cat.query(fav=True)] == ["IMG_6367.HEIC"]
            cat.add_to_collection("Blog post", [heic.path, unknown[0].path])
            assert {x.name for x in cat.query(collection="Blog post")} == {"IMG_6367.HEIC", "random.png"}
            # re-index keeps them
            cat.index()
            assert cat.query(fav=True) and len(cat.query(collection="Blog post")) == 2
            # export: day folders, date in the name, checked, a checksum file left behind
            proj = os.path.join(tmp, "Project")
            pick = cat.query(collection="Blog post")
            res = C.ExportRunner(lib, pick, C.ExportOptions(dest=proj, layout="days", rename="date")).run()
            assert res.ok and res.files_copied == 2, (res.errors, res.files_copied)
            out = os.path.join(proj, "2026_01_22", "2026_01_22_IMG_6367.HEIC")
            assert open(out, "rb").read() == open(os.path.join(lib, *heic.path.split("/")), "rb").read()
            assert os.path.isfile(os.path.join(proj, "Unknown_date", "random.png"))
            assert os.path.isfile(os.path.join(proj, "2026_01_22", "_mmtx_checksums.xxh128"))
            assert abs(os.stat(out).st_mtime - heic.taken) < 2
            # again: same files already there, compared and skipped
            res2 = C.ExportRunner(lib, pick, C.ExportOptions(dest=proj, layout="days", rename="date")).run()
            assert res2.files_skipped == 2 and res2.files_copied == 0
            # a different file with that name: never overwritten, the copy gets _1
            open(out, "wb").write(b"something else")
            res3 = C.ExportRunner(lib, [heic], C.ExportOptions(dest=proj, layout="days", rename="date")).run()
            assert res3.files_copied == 1 and open(out, "rb").read() == b"something else"
            assert os.path.isfile(os.path.join(proj, "2026_01_22", "2026_01_22_IMG_6367_1.HEIC"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        t0 = time.time()
        fn()
        print(f"ok  {fn.__name__}  ({time.time() - t0:.2f}s)")
    print(f"{len(fns)} passed")
    shutil.rmtree(_CFG, ignore_errors=True)
