"""Library previews: one-pass making, the store, background preparation, following files to the archive,
and the quick catalog check. Run:  python tests/test_previews.py  (or pytest)"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

os.environ["TZ"] = "America/New_York"
if hasattr(time, "tzset"):
    time.tzset()
_CFG = tempfile.mkdtemp(prefix="mmtx_prev_cfg_")
os.environ["XDG_CONFIG_HOME"] = _CFG
os.environ["XDG_CACHE_HOME"] = _CFG

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image  # noqa: E402

from mmtx import catalog as C  # noqa: E402
from mmtx import media  # noqa: E402
from mmtx import previews as PV  # noqa: E402
from mmtx.planner import OffloadOptions, day_folder_name, plan_archive, plan_offload  # noqa: E402
from mmtx.scanner import scan_card, scan_working  # noqa: E402
from mmtx.transfer import TransferRunner  # noqa: E402

FF = media.find_ffmpeg()


def clip(path, seconds=3.0, size="640x360", extra=()):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10", "-t", str(seconds),
                    *extra, "-c:v", "libx264", "-preset", "ultrafast", "-g", "10", "-pix_fmt", "yuv420p", "-f", "mp4",
                    path], check=True)


def stamp(p, when):
    os.utime(p, (when.timestamp(), when.timestamp()))


def dims(data: bytes):
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def gopro_card(root, n_days=3, per_day=3, broken=True):
    g = os.path.join(root, "GOPRO")
    os.makedirs(os.path.join(g, "MISC"))
    open(os.path.join(g, "MISC", "version.txt"), "w").write('{"camera type":"HERO13 Black"}')
    dc = os.path.join(g, "DCIM", "100GOPRO")
    n = 1
    for d in range(n_days):
        day = datetime(2026, 11, 10 + d, 7, 0)
        for j in range(per_day):
            lrv = os.path.join(dc, f"GL01{n:04d}.LRV")
            clip(lrv, 2 + j, extra=("-metadata", f"comment=clip {n}"))  # every clip different
            shutil.copy(lrv, lrv.replace("GL01", "GX01").replace(".LRV", ".MP4"))
            for p in (lrv, lrv.replace("GL01", "GX01").replace(".LRV", ".MP4")):
                stamp(p, day + timedelta(minutes=5 * j))
            n += 1
        ph = os.path.join(dc, f"GOPR{9000 + d}.JPG")
        Image.new("RGB", (1600, 1200), (40 * d + 7, 120, 200)).save(ph, quality=90)
        stamp(ph, day + timedelta(minutes=40))
    if broken:
        bad = os.path.join(dc, "GX019999.MP4")
        open(bad, "wb").write(b"\x00\x00\x00\x18ftypmp42" + os.urandom(4000))
        stamp(bad, datetime(2026, 11, 10, 9, 0))
    return g


def offload(card, work):
    scan = scan_card(card)
    dev = {p: lbl.replace(" ", "_") for p, lbl in scan.devices.items()}
    sel = [(d, day_folder_name(d.day, "Run")) for d in scan.days]
    res = TransferRunner(plan_offload(scan, sel, work, dev, OffloadOptions(algo="xxh128"))).run()
    assert res.ok, res.errors


def fresh_store(tmp):
    return PV.PreviewStore(os.path.join(tmp, "previews.db"))


def test_maker_edge_cases():
    tmp = tempfile.mkdtemp(prefix="mmtx_prev_")
    try:
        flat = os.path.join(tmp, "flat.mp4")
        clip(flat, 3, "1280x720")
        rot = os.path.join(tmp, "rot.mp4")  # a phone held upright: landscape pixels, rotation flag
        subprocess.run([FF, "-y", "-v", "error", "-display_rotation", "90", "-i", flat, "-c", "copy", rot], check=True)
        short = os.path.join(tmp, "short.mp4")
        clip(short, 0.3)
        th, st = PV.video_preview(flat, 3.0)
        assert dims(th) == (PV.THUMB, 248) and dims(st) == (PV.CELL * PV.FRAMES, PV.CELL), (dims(th), dims(st))
        th, _st = PV.video_preview(rot, 3.0)
        assert dims(th)[1] == PV.THUMB and dims(th)[0] < dims(th)[1], dims(th)
        for dur in (0.3, 0.0, 99.0):  # very short, unknown length, wrong length: still a preview
            th, st = PV.video_preview(short, dur)
            assert dims(st) == (PV.CELL * PV.FRAMES, PV.CELL)
        audio = os.path.join(tmp, "audio.mp4")
        subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i", "sine", "-t", "1", "-c:a", "aac", audio], check=True)
        for bad in (audio, __file__):
            try:
                PV.video_preview(bad, 1.0)
                raise AssertionError("expected PreviewError for " + bad)
            except PV.PreviewError:
                pass
        # photos: EXIF orientation, an embedded HEIC preview
        jp = os.path.join(tmp, "p.jpg")
        ex = Image.Exif()
        ex[0x0112] = 6
        Image.new("RGB", (4000, 3000), (200, 80, 40)).save(jp, exif=ex.tobytes(), quality=90)
        assert dims(PV.photo_preview(jp)) == (330, PV.THUMB)
        try:
            import pillow_heif  # noqa: F401
            from pillow_heif import register_heif_opener

            register_heif_opener()
            hp = os.path.join(tmp, "IMG_0001.HEIC")
            Image.new("RGB", (4032, 3024), (20, 140, 90)).save(hp, quality=60, thumbnails=[320])
            t = time.time()
            assert dims(PV.photo_preview(hp)) == (320, 240)
            assert time.time() - t < 0.5, "the embedded preview should be used, not a full decode"
        except ImportError:
            print("  (pillow-heif not installed: HEIC skipped)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_store():
    tmp = tempfile.mkdtemp(prefix="mmtx_prev_")
    try:
        s = fresh_store(tmp)
        s.put("a", b"T1")
        s.put("a", b"T2", b"S2")
        s.put("a", b"T3")  # a thumbnail alone keeps the strip made earlier
        assert s.thumb("a") == b"T3" and s.strip("a") == b"S2" and s.done() == {"a": True}
        s.fail("b", "broken")
        assert s.is_failed("b") and s.failed_keys() == {"b"}
        s.put("b", b"T")
        assert not s.is_failed("b")
        # older format: dropped on open
        c = s._connect()
        c.execute("UPDATE meta SET v='0' WHERE k='format'")
        c.commit()
        s2 = PV.PreviewStore(s.path)
        assert s2.count() == 0
        # a damaged file is replaced, not an error
        open(s.path, "wb").write(b"not a database at all" * 100)
        for suf in ("-wal", "-shm"):
            if os.path.exists(s.path + suf):
                os.remove(s.path + suf)
        s3 = PV.PreviewStore(s.path)
        s3.put("x", b"T")
        assert s3.thumb("x") == b"T"
        s3.clear()
        assert s3.count() == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prepare_resume_and_archive_move():
    tmp = tempfile.mkdtemp(prefix="mmtx_prev_")
    try:
        card = gopro_card(os.path.join(tmp, "cards"))
        work = os.path.join(tmp, "Footage")
        os.makedirs(work)
        offload(card, work)
        with C.Catalog(work) as cat:
            cat.index()
            rows = cat.query()
        assert all(r.hash.startswith("xxh128:") for r in rows), "rows carry their checksum"
        store = fresh_store(tmp)
        n = len(PV.todo(rows, store))
        assert n == len(rows) > 9, (n, len(rows))
        # cancel part-way: what's done stays done
        cancel = threading.Event()
        seen = []

        def prog(done, total, left):
            seen.append(done)
            if done >= 3:
                cancel.set()

        r1 = PV.prepare(work, rows, store, workers=2, on_progress=prog, cancel=cancel)
        assert r1.cancelled and 0 < r1.made < n, (r1.made, n)
        left = len(PV.todo(rows, store))
        assert left == n - r1.made, (left, n, r1.made)
        # paused: nothing happens until it's lifted
        pause = threading.Event()
        pause.set()
        box = {}
        th = threading.Thread(target=lambda: box.setdefault("r", PV.prepare(work, rows, store, workers=2, pause=pause)))
        th.start()
        time.sleep(1.0)
        assert len(PV.todo(rows, store)) == left, "made previews while paused"
        pause.clear()
        th.join(120)
        r2 = box["r"]
        assert r2.made + r2.failed == left and r2.failed == 1, (r2.made, r2.failed, left)
        assert not PV.todo(rows, store), "the broken clip is recorded, not retried"
        assert PV.prepare(work, rows, store).total == 0
        # the hover strip is 8 square frames; photos have none
        v = next(r for r in rows if r.kind == "video" and "9999" not in r.name)
        p = next(r for r in rows if r.kind == "photo")
        assert dims(store.strip(PV.key_for(v))) == (PV.CELL * PV.FRAMES, PV.CELL)
        assert store.thumb(PV.key_for(p)) and store.strip(PV.key_for(p)) is None
        # Move to archive: the archive's catalog finds every preview already made
        arch = os.path.join(tmp, "Archive")
        res = TransferRunner(plan_archive(scan_working(work), arch, "year", move=True, default_algo="xxh128")).run()
        assert res.ok and res.files_moved, res.errors
        with C.Catalog(arch) as cat:
            cat.index()
            arows = cat.query()
        assert len(arows) == len(rows) and arows[0].folder.startswith("2026/")
        assert PV.prepare(arch, arows, store).total == 0, "previews should follow the files to the archive"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_ffmpeg_records_nothing():
    tmp = tempfile.mkdtemp(prefix="mmtx_prev_")
    real = media.find_ffmpeg
    try:
        card = gopro_card(os.path.join(tmp, "cards"), n_days=1, per_day=2, broken=False)
        work = os.path.join(tmp, "Footage")
        os.makedirs(work)
        offload(card, work)
        with C.Catalog(work) as cat:
            cat.index()
            rows = cat.query()
        store = fresh_store(tmp)
        media.find_ffmpeg = lambda: None
        r = PV.prepare(work, rows, store, workers=2)
        assert r.skipped_no_ffmpeg == 2 and r.made == 1 and r.failed == 0, r  # the photo still gets one
        assert not store.failed_keys()
        media.find_ffmpeg = real
        assert PV.prepare(work, rows, store, workers=2).made == 2  # ffmpeg back: the clips get theirs
    finally:
        media.find_ffmpeg = real
        shutil.rmtree(tmp, ignore_errors=True)


def test_yields_to_the_screen():
    tmp = tempfile.mkdtemp(prefix="mmtx_prev_")
    try:
        card = gopro_card(os.path.join(tmp, "cards"), n_days=1, per_day=3, broken=False)
        work = os.path.join(tmp, "Footage")
        os.makedirs(work)
        offload(card, work)
        with C.Catalog(work) as cat:
            cat.index()
            rows = cat.query()
        store = fresh_store(tmp)
        store.demand = 1  # the grid is making something on screen
        box = {}
        th = threading.Thread(target=lambda: box.setdefault("r", PV.prepare(work, rows, store, workers=2)))
        th.start()
        time.sleep(0.8)
        assert store.count() == 0
        store.demand = 0
        th.join(60)
        assert box["r"].made == len(rows)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_quick_index():
    tmp = tempfile.mkdtemp(prefix="mmtx_prev_")
    try:
        card = gopro_card(os.path.join(tmp, "cards"), n_days=3, per_day=1, broken=False)
        work = os.path.join(tmp, "Footage")
        os.makedirs(work)
        offload(card, work)
        days = sorted(d for d in os.listdir(work) if d.startswith("2026"))
        with C.Catalog(work) as cat:
            full = cat.index()
            walked = []
            real_walk = os.walk

            def spy(top, *a, **k):
                walked.append(top)
                return real_walk(top, *a, **k)

            C.os.walk = spy
            try:
                r = cat.index(quick=True)
                day_walks = [w for w in walked if os.path.basename(w).startswith("2026_")]
                assert r.unchanged == full.added and not day_walks, (r.unchanged, day_walks)
                # a new file in one folder: only that folder is read
                clip(os.path.join(work, days[0], "GX015555.MP4"), 2)
                walked.clear()
                r = cat.index(quick=True)
                day_walks = [os.path.basename(w) for w in walked if os.path.basename(w).startswith("2026_")]
                assert r.added == 1 and day_walks == [days[0]], (r.added, day_walks)
                # a folder gone (archived with Move)
                shutil.rmtree(os.path.join(work, days[1]))
                r = cat.index(quick=True)
                assert r.removed == 2, r.removed  # its clip and its photo
                # a folder with subfolders is always read (its own time doesn't see changes below)
                os.makedirs(os.path.join(work, days[2], "GoPro"))
                cat.index(quick=True)
                clip(os.path.join(work, days[2], "GoPro", "GX016666.MP4"), 2)
                r = cat.index(quick=True)
                assert r.added == 1, r.added
            finally:
                C.os.walk = real_walk
            # a checksum file written after the first look fills in the missing checksums
            cat.db.execute("UPDATE media SET hash=''")
            cat.db.execute("DELETE FROM folders")
            cat.db.commit()
            cat.index()
            assert all(x.hash for x in cat.query() if "5555" not in x.name and "6666" not in x.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    if not FF:
        print("ffmpeg not found: skipped")
        return 0
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t0 = time.time()
        t()
        print(f"ok  {t.__name__}  ({time.time() - t0:.2f}s)")
    print(f"{len(tests)} passed")
    shutil.rmtree(_CFG, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
