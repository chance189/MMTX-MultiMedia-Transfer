"""End-to-end engine tests. Run:  python -m pytest tests  (or python tests/test_engine.py)"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from make_fake_cards import make_cards  # noqa: E402

from mmtx import hashing  # noqa: E402
from mmtx.manifest import find_manifest, read_manifest  # noqa: E402
from mmtx.planner import (  # noqa: E402
    OffloadOptions,
    day_folder_name,
    day_presence,
    plan_archive,
    plan_offload,
)
from mmtx.scanner import scan_card, scan_working  # noqa: E402
from mmtx.transfer import ChecksumRunner, TransferRunner, VerifyRunner  # noqa: E402


def _env():
    tmp = tempfile.mkdtemp(prefix="mmtx_test_")
    cards = make_cards(os.path.join(tmp, "cards"))
    work = os.path.join(tmp, "work")
    arch = os.path.join(tmp, "archive")
    os.makedirs(work)
    return tmp, cards, work, arch


WORKERS = int(os.environ.get("MMTX_TEST_WORKERS", "1"))


def _offload(card, work, titles, **kw):
    scan = scan_card(card)
    kw.setdefault("workers", WORKERS)
    opts = OffloadOptions(algo="xxh128", **kw)
    dev = {p: lbl.replace(" ", "_") for p, lbl in scan.devices.items()}
    sel = [(d, day_folder_name(d.day, titles.get(d.day.isoformat(), "Run"))) for d in scan.days]
    job = plan_offload(scan, sel, work, dev, opts)
    return scan, job, dev, opts


def _independent_check(folder):
    path, algo = find_manifest(folder)
    entries = read_manifest(path)
    for rel, h in entries.items():
        p = os.path.join(folder, *rel.split("/"))
        assert hashing.hash_file(p, algo, uncached=False) == h, rel
    return entries


def test_offload_all_cameras_and_resume():
    tmp, cards, work, _ = _env()
    try:
        for name, card in cards.items():
            scan, job, dev, opts = _offload(card, work, {"2026-11-14": "Daily Run", "2026-11-13": "Beach"})
            res = TransferRunner(job).run()
            assert res.ok, (name, res.errors)
            assert res.files_copied == len(scan.files), name
        days = sorted(os.listdir(work))
        assert days == ["2026_11_12_Run", "2026_11_13_Beach", "2026_11_14_Daily_Run"], days
        d14 = os.path.join(work, "2026_11_14_Daily_Run")
        assert sorted(os.listdir(d14)) == sorted(
            ["DJI", "GoPro_HERO12_Black", "Insta360_X4", "Sony_FX3", "_mmtx_checksums.xxh128"]
        ), os.listdir(d14)
        # midnight-crossing GoPro thumbnail lands with its clip on the 13th
        assert os.path.exists(os.path.join(work, "2026_11_13_Beach", "GoPro_HERO12_Black", "GX010002.THM"))
        # FX3: XML date beats the wrong file mtime
        assert os.path.exists(os.path.join(d14, "Sony_FX3", "C0001.MP4"))
        assert os.path.exists(os.path.join(work, "2026_11_13_Beach", "Sony_FX3", "C0002M01.XML"))
        # mtimes preserved
        src = os.path.join(cards["gopro"], "DCIM", "100GOPRO", "GX010003.MP4")
        dst = os.path.join(d14, "GoPro_HERO12_Black", "GX010003.MP4")
        assert int(os.path.getmtime(src)) == int(os.path.getmtime(dst))
        entries = _independent_check(d14)
        assert len(entries) == 3 + 4 + 2 + 5  # gopro + dji + insta + sony on the 14th
        assert not any(f.endswith(".mmtx-partial") for _, _, fs in os.walk(work) for f in fs)

        # resume: running the same offload again copies nothing and proves everything
        scan, job, dev, opts = _offload(cards["gopro"], work, {"2026-11-14": "Daily Run", "2026-11-13": "Beach"})
        for d in scan.days:
            present, total, _ = day_presence(d, os.path.join(work, day_folder_name(d.day, {"2026-11-14": "Daily Run", "2026-11-13": "Beach"}[d.day.isoformat()])), dev, opts)
            assert present == total
        res = TransferRunner(job).run()
        assert res.ok and res.files_copied == 0 and res.files_skipped == len(scan.files)
        assert res.bytes_written == 0
    finally:
        shutil.rmtree(tmp)


def test_corruption_retry_and_hard_fail():
    tmp, cards, work, _ = _env()
    try:
        # transient corruption: flip a byte only on the first attempt -> retry succeeds
        scan, job, *_ = _offload(cards["dji"], work, {})
        r = TransferRunner(job)
        seen = set()

        def flip_once(partial):
            if partial not in seen:
                seen.add(partial)
                with open(partial, "r+b") as f:
                    f.seek(100)
                    b = f.read(1)
                    f.seek(100)
                    f.write(bytes([b[0] ^ 0xFF]))

        r._after_copy_hook = flip_once
        res = r.run()
        assert res.ok, res.errors
        _independent_check(os.path.join(work, "2026_11_14_Run"))

        # persistent corruption + delete-from-card: file fails, card is NOT touched
        work2 = os.path.join(tmp, "work2")
        scan, job, *_ = _offload(cards["insta360"], work2, {}, delete_source=True)

        def flip_always(partial):
            if partial.endswith(".insv.mmtx-partial"):
                with open(partial, "r+b") as f:
                    b = f.read(3)
                    f.seek(0)
                    f.write(bytes(x ^ 0x5A for x in b))

        r = TransferRunner(job)
        r._after_copy_hook = flip_always
        res = r.run()
        assert not res.ok and res.files_failed == 1
        bad = os.path.join(work2, "2026_11_14_Run", "Insta360_X4", "VID_20261114_062000_00_001.insv")
        assert not os.path.exists(bad) and not os.path.exists(bad + ".mmtx-partial")
        assert os.path.exists(os.path.join(cards["insta360"], "DCIM", "Camera01", "VID_20261114_062000_00_001.insv"))
        # the other, fully verified day WAS deleted from the card
        assert not os.path.exists(os.path.join(cards["insta360"], "DCIM", "Camera01", "IMG_20261112_180000_00_002.insp"))
    finally:
        shutil.rmtree(tmp)


def test_name_conflict_keeps_both():
    tmp, cards, work, _ = _env()
    try:
        scan, job, *_ = _offload(cards["gopro"], work, {})
        target = job.groups[0].tasks[0].dst
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as f:
            f.write(b"some other clip with the same name")
        res = TransferRunner(job).run()
        assert res.ok
        base, ext = os.path.splitext(target)
        assert os.path.exists(base + "_1" + ext)
        assert open(target, "rb").read() == b"some other clip with the same name"
    finally:
        shutil.rmtree(tmp)


def test_cancel_cleans_partial():
    tmp, cards, work, _ = _env()
    try:
        scan, job, *_ = _offload(cards["gopro"], work, {})
        holder = {}

        def emit(kind, p):
            if kind == "progress" and p["phase"] == "Copying":
                holder["r"].cancel()

        r = TransferRunner(job, emit=emit)
        holder["r"] = r
        res = r.run()
        assert res.cancelled
        assert not any(f.endswith(".mmtx-partial") for _, _, fs in os.walk(work) for f in fs)
    finally:
        shutil.rmtree(tmp)


def test_archive_move_copy_verify_bitrot():
    tmp, cards, work, arch = _env()
    try:
        for card in cards.values():
            _, job, *_ = _offload(card, work, {"2026-11-14": "Daily Run", "2026-11-13": "Beach"})
            assert TransferRunner(job).run().ok
        # a non-camera file added during editing (project file) also gets archived
        with open(os.path.join(work, "2026_11_13_Beach", "Beach.prproj"), "wb") as f:
            f.write(b"premiere project")
        with open(os.path.join(work, "2026_11_13_Beach", "Thumbs.db"), "wb") as f:
            f.write(b"junk")
        os.makedirs(os.path.join(work, "random stuff"))

        folders = scan_working(work)
        names = [w.name for w in folders]
        assert names[:3] == ["2026_11_14_Daily_Run", "2026_11_13_Beach", "2026_11_12_Run"], names
        assert "random stuff" in names

        # bit rot on the working drive since offload
        rot = os.path.join(work, "2026_11_12_Run", "Insta360_X4", "IMG_20261112_180000_00_002.insp")
        with open(rot, "r+b") as f:
            f.seek(5000)
            f.write(b"ROT")

        sel = [w for w in folders if w.day]
        job = plan_archive(sel, arch, "year", move=True, default_algo="xxh128", workers=WORKERS)
        for g in job.groups:  # force the cross-drive path (sandbox is one filesystem)
            g.fast_rename = False
        res = TransferRunner(job).run()
        assert not res.errors, res.errors
        assert len(res.warnings) == 1 and "no longer matches" in res.warnings[0]
        # clean folders moved, bit-rotted folder kept at source for inspection
        assert not os.path.exists(os.path.join(work, "2026_11_14_Daily_Run"))
        assert not os.path.exists(os.path.join(work, "2026_11_13_Beach"))
        assert os.path.exists(os.path.join(work, "2026_11_12_Run"))
        a13 = os.path.join(arch, "2026", "2026_11_13_Beach")
        assert os.path.exists(os.path.join(a13, "Beach.prproj"))
        assert not os.path.exists(os.path.join(a13, "Thumbs.db"))
        _independent_check(a13)

        # Verify tab: archive passes, then simulate bit rot on the archive
        folders_with_manifest = [os.path.join(arch, "2026", n) for n in os.listdir(os.path.join(arch, "2026"))]
        v = VerifyRunner(folders_with_manifest).run()
        assert v.ok and v.mismatched == 0 and v.verified_ok > 0
        victim = os.path.join(arch, "2026", "2026_11_14_Daily_Run", "DJI", "DJI_20261114063012_0002_D.MP4")
        with open(victim, "r+b") as f:
            f.seek(1234)
            f.write(b"X")
        os.remove(os.path.join(arch, "2026", "2026_11_14_Daily_Run", "Sony_FX3", "C0001T01.JPG"))
        v = VerifyRunner(folders_with_manifest).run()
        assert v.mismatched == 1 and v.missing == 1 and not v.ok

        # same-drive move is an instant rename
        os.makedirs(os.path.join(work, "2026_10_01_Old_Run"))
        with open(os.path.join(work, "2026_10_01_Old_Run", "a.mp4"), "wb") as f:
            f.write(os.urandom(1000))
        wf = [w for w in scan_working(work) if w.name == "2026_10_01_Old_Run"]
        job = plan_archive(wf, arch, "year_month", move=True, default_algo="xxh128")
        assert job.groups[0].fast_rename
        res = TransferRunner(job).run()
        assert res.ok and res.files_moved == 1
        assert os.path.exists(os.path.join(arch, "2026", "2026_10", "2026_10_01_Old_Run", "a.mp4"))

        # copy mode leaves the source alone; create-checksums for a folder with none
        os.makedirs(os.path.join(work, "2026_09_01_NoManifest", "sub"))
        with open(os.path.join(work, "2026_09_01_NoManifest", "sub", "b.mov"), "wb") as f:
            f.write(os.urandom(50000))
        c = ChecksumRunner([os.path.join(work, "2026_09_01_NoManifest")], "xxh64").run()
        assert c.ok and c.files_ok == 1
        wf = [w for w in scan_working(work) if w.name == "2026_09_01_NoManifest"]
        assert wf[0].manifest_algo == "xxh64"
        res = TransferRunner(plan_archive(wf, arch, "flat", move=False, default_algo="xxh128")).run()
        assert res.ok
        assert os.path.exists(os.path.join(work, "2026_09_01_NoManifest", "sub", "b.mov"))
        assert find_manifest(os.path.join(arch, "2026_09_01_NoManifest"))[1] == "xxh64"
    finally:
        shutil.rmtree(tmp)


def test_archive_never_into_itself():
    tmp, cards, work, _ = _env()
    try:
        os.makedirs(os.path.join(work, "2026_11_01_X"))
        inner = os.path.join(work, "2026_11_01_X", "archive")
        job = plan_archive(scan_working(work), inner, "year", move=True, default_algo="xxh128")
        assert not job.groups
    finally:
        shutil.rmtree(tmp)


def test_empty_folder_archive_status():
    tmp, cards, work, arch = _env()
    try:
        os.makedirs(os.path.join(work, "2026_11_02_Empty", "nested"))
        with open(os.path.join(work, "2026_11_02_Empty", "nested", "Thumbs.db"), "wb") as f:
            f.write(b"junk")
        job = plan_archive(scan_working(work), arch, "year", move=True, default_algo="xxh128")
        job.groups[0].fast_rename = False
        res = TransferRunner(job).run()
        assert res.ok and job.groups[0].status == "verified", job.groups[0].status
        assert os.path.isdir(os.path.join(arch, "2026", "2026_11_02_Empty"))
        assert not os.path.exists(os.path.join(work, "2026_11_02_Empty"))
    finally:
        shutil.rmtree(tmp)


def test_sha256_manifest_is_sha256sum_compatible():
    import subprocess

    tmp, cards, work, _ = _env()
    try:
        scan = scan_card(cards["sony"])
        opts = OffloadOptions(algo="sha256")
        job = plan_offload(scan, [(scan.days[0], "2026_11_14_X")], work, {"sony": "Sony_FX3"}, opts)
        assert TransferRunner(job).run().ok
        folder = os.path.join(work, "2026_11_14_X")
        if shutil.which("sha256sum"):
            out = subprocess.run(["sha256sum", "-c", "_mmtx_checksums.sha256"], cwd=folder, capture_output=True, text=True)
            assert out.returncode == 0, out.stdout + out.stderr
    finally:
        shutil.rmtree(tmp)


def _many_files_card(root, n=240, size=20_000, dup=True):
    from datetime import datetime as dt
    d1 = os.path.join(root, "DCIM", "100GOPRO")
    d2 = os.path.join(root, "DCIM", "101GOPRO")
    os.makedirs(d1)
    os.makedirs(d2)
    ts = dt(2026, 11, 14, 7).timestamp()
    for i in range(n):
        p = os.path.join(d1, f"G{i // 100:03d}{i % 100:04d}.JPG")
        with open(p, "wb") as f:
            f.write(os.urandom(size))
        os.utime(p, (ts, ts))
    if dup:  # same file name in two card folders, different content
        for d in (d1, d2):
            p = os.path.join(d, "GOPR0001.JPG")
            with open(p, "wb") as f:
                f.write(os.urandom(5000))
            os.utime(p, (ts, ts))


def test_parallel_many_small_files_and_duplicate_names():
    tmp = tempfile.mkdtemp()
    try:
        card = os.path.join(tmp, "card")
        _many_files_card(card)
        work = os.path.join(tmp, "work")
        scan = scan_card(card)
        job = plan_offload(scan, [(scan.days[0], "2026_11_14_Run")], work, {"gopro": "GoPro"},
                           OffloadOptions(algo="xxh128", workers=4))
        events = []
        r = TransferRunner(job, emit=lambda k, p: events.append((k, p)) if k == "progress" else None)
        peak = {"n": 0}

        def watch(_partial):
            with r._lock:
                peak["n"] = max(peak["n"], len(r.slots))
            time.sleep(0.002)

        r._after_copy_hook = watch
        res = r.run()
        assert res.ok, res.errors
        assert res.files_copied == 242
        names = os.listdir(os.path.join(work, "2026_11_14_Run", "GoPro"))
        assert "GOPR0001.JPG" in names and "GOPR0001_1.JPG" in names
        entries = _independent_check(os.path.join(work, "2026_11_14_Run"))
        assert len(entries) == 242
        last = events[-1][1]
        assert last["done"] == last["total"] and last["files_done"] == last["files_total"] == 242
        assert peak["n"] >= 2, "never had two files in flight"
    finally:
        shutil.rmtree(tmp)


def test_parallel_disk_full_aborts_cleanly():
    import errno as _errno

    tmp, cards, work, _ = _env()
    try:
        scan, job, *_ = _offload(cards["gopro"], work, {}, workers=3)
        r = TransferRunner(job)
        calls = {"n": 0}

        def boom(partial):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError(_errno.ENOSPC, "No space left on device")

        r._after_copy_hook = boom
        res = r.run()
        assert res.aborted.startswith("Destination is full"), res.aborted
        assert not any(f.endswith(".mmtx-partial") for _, _, fs in os.walk(work) for f in fs)
        # whatever made it is verified and in the manifest; nothing half-written was kept
        for g in job.groups:
            for t in g.tasks:
                if t.status == "copied":
                    assert os.path.exists(t.dst)
                elif t.status in ("failed", "cancelled", "pending"):
                    assert not os.path.exists(t.dst), t.dst
    finally:
        shutil.rmtree(tmp)


def test_parallel_cancel():
    tmp = tempfile.mkdtemp()
    try:
        card = os.path.join(tmp, "card")
        _many_files_card(card, n=120, size=400_000, dup=False)
        work = os.path.join(tmp, "work")
        scan = scan_card(card)
        job = plan_offload(scan, [(scan.days[0], "2026_11_14_Run")], work, {"gopro": "GoPro"},
                           OffloadOptions(algo="xxh128", workers=4))
        holder = {}

        def emit(kind, p):
            if kind == "progress" and p["files_done"] >= 10:
                holder["r"].cancel()

        r = TransferRunner(job, emit=emit)
        holder["r"] = r
        res = r.run()
        assert res.cancelled and 0 < res.files_copied < 120
        assert not any(f.endswith(".mmtx-partial") for _, _, fs in os.walk(work) for f in fs)
        entries = _independent_check(os.path.join(work, "2026_11_14_Run"))
        assert len(entries) == res.files_copied
    finally:
        shutil.rmtree(tmp)


def test_hash_paths_agree():
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "f.bin")
        data = os.urandom(hashing.CHUNK * 2 + 12345)
        with open(p, "wb") as f:
            f.write(data)
        import xxhash

        expect = xxhash.xxh3_128(data).hexdigest()
        assert hashing.hash_file(p, "xxh128", uncached=True) == expect
        assert hashing.hash_file(p, "xxh128", uncached=False) == expect
        cancel = threading.Event()
        cancel.set()
        try:
            hashing.hash_file(p, "xxh128", cancel=cancel)
            raise AssertionError("expected Cancelled")
        except hashing.Cancelled:
            pass
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"{len(fns)} tests passed")
