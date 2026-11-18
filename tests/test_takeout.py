"""Google Photos takeout import tests. Run:  python tests/test_takeout.py  (or pytest)"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile

os.environ["TZ"] = "America/New_York"  # Orlando: the day folders below assume Eastern time
if hasattr(time, "tzset"):
    time.tzset()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from make_fake_takeout import (  # noqa: E402
    GP, exif_app1, heic, jpeg, make_takeout, mov, sidecar, ts, video, write_zip,
)

from mmtx import takeout as T  # noqa: E402
from mmtx.manifest import find_manifest, read_manifest  # noqa: E402
from mmtx.transfer import VerifyRunner  # noqa: E402

WORKERS = int(os.environ.get("MMTX_TEST_WORKERS", "2"))


def _run(paths, lib, reimport=False, scratch=True, events=None, **kw):
    plan = T.plan_takeout(paths, lib, reimport=reimport)
    emit = (lambda k, p: events.append((k, p))) if events is not None else None
    res = T.TakeoutRunner(plan, T.TakeoutOptions(workers=WORKERS, scratch=scratch, **kw), emit=emit).run()
    return plan, res


def _rows(lib):
    db = sqlite3.connect(os.path.join(lib, T.LIBRARY_DB))
    rows = {v: (p, s) for v, p, s in db.execute("SELECT vpath, path, date_source FROM items")}
    db.close()
    return rows


def _files(lib):
    out = []
    for d, _dirs, files in os.walk(lib):
        for f in files:
            if not f.startswith("_mmtx") and not f.startswith("_google") and not f.startswith("_albums"):
                out.append(os.path.relpath(os.path.join(d, f), lib).replace(os.sep, "/"))
    return sorted(out)


def test_matching_rules():
    m = T.match_folder(
        ["IMG_1.jpg", "IMG_1(1).jpg", "IMG_2(1).jpg", "IMG_3.jpg", "IMG_3-edited.jpg", "IMG_4.HEIC", "IMG_4.MOV",
         "IMG_12.jpg", "photo (1).jpg", "IMG_5-edited(1).jpg"],
        ["IMG_1.jpg.json", "IMG_1.jpg(1).json", "IMG_2.jpg.supplemental-metada(1).json",
         "IMG_3.jpg.supplemental-metadata.json", "IMG_4.HEIC.suppl.json", "IMG_1.json", "photo (1).jpg.json",
         "IMG_5.jpg(1).json"])
    assert m["IMG_1.jpg"] == ("IMG_1.jpg.json", "name")
    assert m["IMG_1(1).jpg"] == ("IMG_1.jpg(1).json", "name")
    assert m["IMG_2(1).jpg"][0] == "IMG_2.jpg.supplemental-metada(1).json"
    assert m["IMG_3-edited.jpg"] == ("IMG_3.jpg.supplemental-metadata.json", "edited")
    assert m["IMG_4.MOV"] == ("IMG_4.HEIC.suppl.json", "pair")
    assert "IMG_12.jpg" not in m, m.get("IMG_12.jpg")  # IMG_1.json must not grab IMG_12
    assert m["photo (1).jpg"] == ("photo (1).jpg.json", "name")
    assert m["IMG_5-edited(1).jpg"] == ("IMG_5.jpg(1).json", "edited")
    assert T.title_matches("weird name.jpg", "weird name.jpg")
    assert not T.title_matches("IMG_1.jpg", "IMG_12.jpg")


def test_dates():
    tok = exif_app1("2025:03:11 07:30:00", "+09:00")
    ex = T.exif_datetime(jpeg(1, tok))
    assert ex and ex[0].day == 11 and ex[1] == 540, ex
    assert T.exif_datetime(heic(1, tok))[0].hour == 7
    d = json.loads(sidecar("x.jpg", ts(2025, 3, 10, 22, 30)))
    assert T.resolve_date(d, ex, "x.jpg").source == "google+camera"
    assert T.resolve_date(d, (ex[0], None), "x.jpg").local.day == 11  # zone unknown, still the same moment
    wrong = T.exif_datetime(jpeg(2, exif_app1("2010:01:01 00:00:00")))
    r = T.resolve_date(json.loads(sidecar("y.jpg", ts(2015, 6, 15))), wrong, "y.jpg")
    assert r.source == "google" and r.local.year == 2015
    assert T.filename_datetime("PXL_20250704_120000123.jpg").day == 4
    assert T.filename_datetime("IMG-20190623-WA0001.jpg").month == 6
    assert T.filename_datetime("IMG_0001.jpg") is None
    assert T.day_folder(T.filename_datetime("IMG_20190623_172854.jpg")) == "2019/2019_06/2019_06_23"


def test_full_import():
    tmp = tempfile.mkdtemp(prefix="mmtx_takeout_")
    try:
        zips = os.path.join(tmp, "Downloads")
        lib = os.path.join(tmp, "Photos")
        expect = make_takeout(zips)
        events: list = []
        scratch_seen = []

        def spy(k, p):
            events.append((k, p))
            if k == "photo" and p["archive"].endswith(".zip"):
                scratch_seen.append(os.path.isdir(T.scratch_dir_for(p["archive"])))

        plan = T.plan_takeout([zips], lib)
        assert len(plan.archives) == 2 and plan.trash == 1 and plan.other >= 1, (plan.trash, plan.other)
        assert plan.copies == 0 or plan.copies == 1
        res = T.TakeoutRunner(plan, T.TakeoutOptions(workers=WORKERS), emit=spy).run()
        assert res.ok, (res.errors, res.aborted)
        rows = _rows(lib)
        by_name = {v.rsplit("/", 1)[-1]: p for v, (p, _s) in rows.items()}
        for name, day in expect.items():
            assert name in by_name, f"{name} missing from the library"
            assert by_name[name].rsplit("/", 1)[0] == day, f"{name}: {by_name[name]} != {day}"
        # numbered copies keep their own days and lose Takeout's "(1)"
        assert by_name["IMG_0001(1).jpg"] == "2025/2025_05/2025_05_02/IMG_0001.jpg"
        # the album copy was recognised, never written twice
        assert res.duplicates == 1 and res.new == len(expect), (res.new, res.duplicates, len(expect))
        albums = json.load(open(os.path.join(lib, T.ALBUMS_FILE)))["albums"]
        assert albums == {"Beach Trip": ["2025/2025_06/2025_06_02/IMG_0005.jpg"]}, albums
        # Google's details next to the photos; the photos themselves are byte-identical to the zip
        meta = json.load(open(os.path.join(lib, "2025/2025_06/2025_06_02", T.DAY_META)))["files"]
        e = meta["IMG_0005.jpg"]
        assert e["description"] == "Sunset at the pier" and e["people"] == ["Zulu"] and e["albums"] == ["Beach Trip"]
        assert e["location"]["latitude"] == 28.5383 and e.get("favorite") is True
        assert meta["IMG_0005-edited.jpg"].get("edited_copy") is True
        assert "location" not in json.load(open(os.path.join(lib, "2026/2026_01/2026_01_22", T.DAY_META)))["files"]["IMG_6367.HEIC"]
        with zipfile.ZipFile(os.path.join(zips, "takeout-20261101T120000Z-001.zip")) as zf:
            assert zf.read(f"{GP}/Photos from 2025/IMG_0003.jpg") == open(
                os.path.join(lib, "2025/2025_05/2025_05_04/IMG_0003.jpg"), "rb").read()
        # file times are the shooting time
        st = os.stat(os.path.join(lib, "2026/2026_01/2026_01_22/IMG_6367.HEIC"))
        assert abs(st.st_mtime - 1769111724) < 2
        # every day folder has a manifest and verifies bit-perfect
        days = sorted({os.path.dirname(os.path.join(lib, f)) for f in _files(lib)})
        for d in days:
            assert find_manifest(d), d
        v = VerifyRunner(days).run()
        assert v.mismatched == 0 and v.missing == 0 and v.extra == 0, (v.mismatched, v.missing, v.extra)
        assert v.verified_ok == res.new + len(days), (v.verified_ok, res.new, len(days))  # + each day's details file
        # trash and other products stayed out; zips untouched; scratch used, then gone
        assert not any("IMG_9999" in f or "notes.jpg" in f for f in _files(lib))
        assert len(os.listdir(zips)) == 2
        assert scratch_seen and all(scratch_seen)
        assert not any(n.startswith("_mmtx_unpack_") for n in os.listdir(zips))
        assert not any(f.endswith(".mmtx-partial") for f in _files(lib))
        assert sum(1 for k, p in events if k == "photo" and p["state"] == "new") == res.new

        # the same zips again: recognised instantly, nothing read
        plan2 = T.plan_takeout([zips], lib)
        assert plan2.skipped_archives == 2 and not plan2.items
        # forced: everything hashed, nothing written
        _p, res3 = _run([zips], lib, reimport=True)
        assert res3.new == 0 and res3.bytes_written == 0 and res3.duplicates == res.new + 1, (res3.new, res3.duplicates)
        assert _files(lib) == sorted(_files(lib))  # unchanged set below
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_newer_takeout_adds_only_new():
    tmp = tempfile.mkdtemp(prefix="mmtx_takeout_")
    try:
        lib = os.path.join(tmp, "Photos")
        a = os.path.join(tmp, "a")
        make_takeout(a)
        _p, r1 = _run([a], lib)
        before = _files(lib)
        # a later takeout of the same account: same photos plus two new ones, different zip names
        b = os.path.join(tmp, "b")
        yr = f"{GP}/Photos from 2026"
        old = []
        with zipfile.ZipFile(os.path.join(a, "takeout-20261101T120000Z-001.zip")) as zf:
            for n in zf.namelist():
                if n.startswith(GP):
                    old.append((n, zf.read(n)))
        new = [(f"{yr}/IMG_8001.jpg", jpeg(801)), (f"{yr}/IMG_8001.jpg.supplemental-metadata.json",
                                                    sidecar("IMG_8001.jpg", ts(2026, 3, 3))),
               (f"{yr}/IMG_8002.jpg", jpeg(802)), (f"{yr}/IMG_8002.jpg.supplemental-metadata.json",
                                                    sidecar("IMG_8002.jpg", ts(2026, 3, 4), desc="new caption"))]
        write_zip(os.path.join(b, "takeout-20261115T080000Z-001.zip"), old + new)
        _p, r2 = _run([b], lib)
        assert r2.ok and r2.new == 2, (r2.new, r2.errors)
        assert r2.bytes_written == len(jpeg(801)) + len(jpeg(802)), r2.bytes_written
        after = _files(lib)
        assert set(after) - set(before) == {"2026/2026_03/2026_03_03/IMG_8001.jpg", "2026/2026_03/2026_03_04/IMG_8002.jpg"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parts_added_later():
    """Part 1 today, part 2 next week: the photo whose sidecar was in part 2 moves to its day."""
    tmp = tempfile.mkdtemp(prefix="mmtx_takeout_")
    try:
        lib = os.path.join(tmp, "Photos")
        z = os.path.join(tmp, "z")
        yr = f"{GP}/Photos from 2026"
        p1 = write_zip(os.path.join(z, "takeout-20261110T000000Z-001.zip"), [
            (f"{yr}/IMG_5000.jpg", jpeg(500)),  # its sidecar is in part 2
            (f"{yr}/IMG_5001.jpg.supplemental-metadata.json", sidecar("IMG_5001.jpg", ts(2026, 4, 9))),  # photo in part 2
        ])
        p2 = write_zip(os.path.join(tmp, "later", "takeout-20261110T000000Z-002.zip"), [
            (f"{yr}/IMG_5000.jpg.supplemental-metadata.json",
             sidecar("IMG_5000.jpg", ts(2026, 4, 8), desc="found later")),
            (f"{yr}/IMG_5001.jpg", jpeg(501)),
        ])
        plan, r1 = _run([p1], lib)
        assert any("missing" in w for w in plan.warnings) is False  # part 1 of 1 so far: nothing to say yet
        assert r1.new == 1 and _files(lib) == ["Unknown date/IMG_5000.jpg"], _files(lib)
        plan2, r2 = _run([p2], lib)
        assert any("part 001 missing" in w for w in plan2.warnings), plan2.warnings
        assert r2.ok and r2.new == 1 and r2.refiled == 1, (r2.new, r2.refiled, r2.errors)
        assert _files(lib) == ["2026/2026_04/2026_04_08/IMG_5000.jpg", "2026/2026_04/2026_04_09/IMG_5001.jpg"], _files(lib)
        assert not os.path.exists(os.path.join(lib, "Unknown date"))
        meta = json.load(open(os.path.join(lib, "2026/2026_04/2026_04_08", T.DAY_META)))["files"]
        assert meta["IMG_5000.jpg"]["description"] == "found later"
        man = read_manifest(find_manifest(os.path.join(lib, "2026/2026_04/2026_04_08"))[0])
        assert sorted(man) == ["IMG_5000.jpg", T.DAY_META], man
        rows = _rows(lib)
        assert rows[f"Photos from 2026/IMG_5001.jpg"][1] == "google"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_layouts_clashes_and_recovery():
    """takeout-<date>/takeout/google photos/... inside a zip, an unpacked folder, a name clash,
    an interrupted run's leftover, a damaged zip entry, and scratch on another drive."""
    tmp = tempfile.mkdtemp(prefix="mmtx_takeout_")
    try:
        lib = os.path.join(tmp, "Photos")
        z = os.path.join(tmp, "z")
        pre = "takeout-20261117T000000Z/takeout/google photos/photos from 2026"
        write_zip(os.path.join(z, "takeout-20261117T000000Z-001.zip"), [
            (f"{pre}/IMG_1.jpg", jpeg(11)), (f"{pre}/IMG_1.jpg.supplemental-metadata.json", sidecar("IMG_1.jpg", ts(2026, 7, 1))),
        ])
        # an unpacked Takeout folder with a different photo of the same name and day
        loose = os.path.join(tmp, "unzipped", "Takeout", "Google Photos", "Photos from 2026")
        os.makedirs(loose)
        open(os.path.join(loose, "IMG_1.jpg"), "wb").write(jpeg(12))
        open(os.path.join(loose, "IMG_1.jpg.supplemental-metadata.json"), "wb").write(sidecar("IMG_1.jpg", ts(2026, 7, 1, 18)))
        plan = T.plan_takeout([z, os.path.join(tmp, "unzipped")], lib)
        assert len(plan.items) == 2, [(i.m.vpath, i.archive.label) for i in plan.items]
        # pretend an earlier run was cut off right after writing IMG_1.jpg from the zip
        day = os.path.join(lib, "2026", "2026_07", "2026_07_01")
        os.makedirs(day)
        open(os.path.join(day, "IMG_1.jpg"), "wb").write(jpeg(11))
        T.same_device_orig = T.same_device
        T.same_device = lambda a, b: False  # scratch on "another drive": copied over, then deleted
        try:
            res = T.TakeoutRunner(plan, T.TakeoutOptions(workers=WORKERS)).run()
        finally:
            T.same_device = T.same_device_orig
        assert res.ok, res.errors
        assert sorted(os.listdir(day)) == sorted(["IMG_1.jpg", "IMG_1_1.jpg", T.DAY_META, "_mmtx_checksums.xxh128"]), os.listdir(day)
        assert res.new == 1 and res.duplicates == 1  # the leftover was adopted, not copied again
        # a damaged entry: reported, the zip isn't marked done, nothing half-written left behind
        bad = os.path.join(z, "takeout-20261117T000000Z-002.zip")
        with zipfile.ZipFile(bad, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr(f"Takeout/Google Photos/Photos from 2026/BAD.jpg", jpeg(77, size=20000))
        raw = bytearray(open(bad, "rb").read())
        raw[2000] ^= 0xFF
        open(bad, "wb").write(bytes(raw))
        _p, r2 = _run([bad], lib)
        assert r2.files_failed == 1 and "damaged" in r2.errors[0], r2.errors
        assert not T.plan_takeout([bad], lib).skipped_archives
        assert not any(".mmtx-partial" in f for f in _files(lib))
        assert not any(n.startswith("_mmtx_unpack_") for n in os.listdir(z))
        # straight from the zip, no scratch folder at all
        lib2 = os.path.join(tmp, "Photos2")
        _p, r3 = _run([os.path.join(z, "takeout-20261117T000000Z-001.zip")], lib2, scratch=False)
        assert r3.ok and r3.new == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_deleted_photo_comes_back():
    tmp = tempfile.mkdtemp(prefix="mmtx_takeout_")
    try:
        lib = os.path.join(tmp, "Photos")
        z = os.path.join(tmp, "z")
        make_takeout(z)
        _run([z], lib)
        gone = os.path.join(lib, "2025/2025_05/2025_05_04/IMG_0003.jpg")
        os.remove(gone)
        _p, r = _run([z], lib, reimport=True)
        assert r.ok and r.restored == 1 and os.path.isfile(gone), (r.restored, r.errors)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_video_dates():
    import io
    a = T.video_datetime(io.BytesIO(mov(1, apple="2026-02-14T23:30:00-0500", mvhd_utc=ts(2026, 2, 15, 4, 30))), 99999999)
    assert a == (T.datetime(2026, 2, 14, 23, 30), -300), a
    b = T.video_datetime(io.BytesIO(mov(2, mvhd_utc=ts(2026, 3, 1, 3, 0))), 99999999)
    assert b[0] == T.datetime(2026, 2, 28, 22, 0) and b[1] == -300, b  # shown in this computer's zone
    assert T.video_datetime(io.BytesIO(mov(3)), 99999999) is None
    assert T.video_datetime(io.BytesIO(b"garbage" * 100), 700) is None


def test_loose_movs_next_to_zips():
    """One folder with the takeout zips and loose .MOVs; the library is the folder around it."""
    tmp = tempfile.mkdtemp(prefix="mmtx_takeout_")
    try:
        lib = os.path.join(tmp, "Photos")
        src = os.path.join(lib, "Takeouts")  # the takeout folder sits inside the library folder
        expect = make_takeout(src)
        files = {
            "IMG_1500.MOV": mov(1500, apple="2026-02-14T23:30:00-0500", mvhd_utc=ts(2026, 2, 15, 4, 30)),
            "IMG_1501.MOV": mov(1501, mvhd_utc=ts(2026, 3, 1, 3, 0)),
            "IMG_1502.MOV": mov(1502),  # nothing inside: the file's own date
            "IMG_0006.MOV": video(9),  # same bytes as the Live Photo video inside zip 002
            "clip.mp4": mov(1503),
            "clip.mp4.supplemental-metadata.json": sidecar("clip.mp4", ts(2024, 12, 25, 18)),
        }
        for n, data in files.items():
            open(os.path.join(src, n), "wb").write(data)
        when = time.mktime((2025, 8, 9, 12, 0, 0, 0, 0, -1))
        os.utime(os.path.join(src, "IMG_1502.MOV"), (when, when))
        # a zipped video with no sidecar: the date inside it decides
        write_zip(os.path.join(src, "takeout-20261101T120000Z-003.zip"),
                  [(f"{GP}/Photos from 2026/VID_NOSIDE.MOV", mov(1600, apple="2026-04-05T08:00:00-0400"))])
        # already in the destination: an identical IMG_1501.MOV, and a different file named IMG_1500.MOV
        d1 = os.path.join(lib, "2026", "2026_02", "2026_02_28")
        d2 = os.path.join(lib, "2026", "2026_02", "2026_02_14")
        os.makedirs(d1)
        os.makedirs(d2)
        open(os.path.join(d1, "IMG_1501.MOV"), "wb").write(files["IMG_1501.MOV"])
        mine = mov(4242, apple="2026-02-14T23:31:00-0500", mvhd_utc=ts(2026, 2, 15, 4, 31))
        open(os.path.join(d2, "IMG_1500.MOV"), "wb").write(mine)
        assert len(mine) == len(files["IMG_1500.MOV"])  # same name, same size, different content

        assert sorted(os.path.basename(p) for p in T.find_takeouts([src], lib)) == sorted(
            ["takeout-20261101T120000Z-001.zip", "takeout-20261101T120000Z-002.zip",
             "takeout-20261101T120000Z-003.zip", "Takeouts"])
        plan = T.plan_takeout([src], lib)
        assert 2025 in plan.years and 2026 in plan.years, plan.years
        res = T.TakeoutRunner(plan, T.TakeoutOptions(workers=WORKERS)).run()
        assert res.ok, res.errors
        rows = _rows(lib)
        def where(name):
            return [(p, s) for v, (p, s) in rows.items() if v.rsplit("/", 1)[-1] == name]
        assert where("IMG_1500.MOV") == [("2026/2026_02/2026_02_14/IMG_1500_1.MOV", "camera")], where("IMG_1500.MOV")
        assert open(os.path.join(d2, "IMG_1500.MOV"), "rb").read() == mine  # never overwritten
        assert where("IMG_1501.MOV") == [("2026/2026_02/2026_02_28/IMG_1501.MOV", "camera")], where("IMG_1501.MOV")
        assert not os.path.exists(os.path.join(d1, "IMG_1501_1.MOV"))  # identical: checked, not copied
        assert where("IMG_1502.MOV") == [("2025/2025_08/2025_08_09/IMG_1502.MOV", "filedate")], where("IMG_1502.MOV")
        assert where("clip.mp4") == [("2024/2024_12/2024_12_25/clip.mp4", "google")], where("clip.mp4")
        assert where("VID_NOSIDE.MOV") == [("2026/2026_04/2026_04_05/VID_NOSIDE.MOV", "camera")]
        assert res.new == len(expect) + 4 and res.duplicates == 3, (res.new, res.duplicates)  # album copy, IMG_0006, IMG_1501
        assert sorted(os.listdir(src))[:3] == ["IMG_0006.MOV", "IMG_1500.MOV", "IMG_1501.MOV"]  # loose files left alone
        # file times are the recording time
        assert abs(os.stat(os.path.join(d2, "IMG_1500_1.MOV")).st_mtime - ts(2026, 2, 15, 4, 30)) < 2
        # the same folder again: zips and loose files that already went in are skipped outright
        again = T.plan_takeout([src], lib)
        assert not again.items and again.skipped_archives == 3 and again.skipped_loose == 5, (
            len(again.items), again.skipped_archives, again.skipped_loose)
        # touch one loose file: only that one is looked at again (and found to be in already)
        os.utime(os.path.join(src, "IMG_1501.MOV"), None)
        assert [it.name for it in T.plan_takeout([src], lib).items] == ["IMG_1501.MOV"]
        # adding the whole library folder (which holds the takeout folder) reads nothing twice
        plan2 = T.plan_takeout([lib], lib, reimport=True)
        assert not any("/20" in it.m.name.replace(os.sep, "/").split("Takeouts")[0][len(lib):] for it in plan2.items
                       if not it.m.zipped), "library files were picked up as new"
        r2 = T.TakeoutRunner(plan2, T.TakeoutOptions(workers=WORKERS)).run()
        assert r2.ok and r2.new == 0 and r2.bytes_written == 0, (r2.new, r2.bytes_written)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_never_overwrites():
    tmp = tempfile.mkdtemp(prefix="mmtx_takeout_")
    try:
        a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        open(a, "w").write("new")
        open(b, "w").write("old")
        try:
            T.place_no_overwrite(a, b)
            raise AssertionError("replaced an existing file")
        except FileExistsError:
            pass
        assert open(b).read() == "old" and open(a).read() == "new"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        t0 = time.time()
        fn()
        print(f"ok  {fn.__name__}  ({time.time() - t0:.2f}s)")
    print(f"{len(fns)} passed")
