"""Throughput benchmark: files at once = 1..4 for big clips and for many small files.

    python tests/bench.py <scratch_dir_on_source_drive> [<scratch_dir_on_dest_drive>]

Run it with the source on your card/NVMe and the destination on the drive you care about.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mmtx.drives import auto_workers, drive_info  # noqa: E402
from mmtx.transfer import FileTask, Group, Job, JobOptions, TransferRunner, human_bytes  # noqa: E402


def make(src: str, n: int, size: int, prefix: str) -> list[str]:
    os.makedirs(src, exist_ok=True)
    out = []
    block = os.urandom(1 << 20)
    for i in range(n):
        p = os.path.join(src, f"{prefix}{i:05d}.MP4")
        if not os.path.exists(p) or os.path.getsize(p) != size:
            with open(p, "wb") as f:
                left = size
                while left > 0:
                    f.write(block[: min(left, len(block))])
                    left -= len(block)
        out.append(p)
    return out


def run(files: list[str], dest: str, workers: int) -> tuple[float, object]:
    shutil.rmtree(dest, ignore_errors=True)
    g = Group(name="bench", dest_dir=dest, algo="xxh128")
    for p in files:
        rel = os.path.basename(p)
        g.tasks.append(FileTask(src=p, dst=os.path.join(dest, rel), size=os.path.getsize(p), rel=rel))
    job = Job("offload", "bench", [g], JobOptions(workers=workers))
    t0 = time.perf_counter()
    res = TransferRunner(job).run()
    dt = time.perf_counter() - t0
    assert res.ok, res.errors
    return dt, res


def main() -> None:
    src_root = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "bench_src")
    dst_root = os.path.abspath(sys.argv[2] if len(sys.argv) > 2 else src_root + "_dst")
    si, di = drive_info(src_root if os.path.exists(src_root) else os.path.dirname(src_root)), drive_info(os.path.dirname(dst_root))
    print(f"source: {si.kind} {si.model}   dest: {di.kind} {di.model}   auto -> {auto_workers(si, di)}")
    sets = {
        "big clips (6 x 256 MB)": make(os.path.join(src_root, "big"), 6, 256 << 20, "GX01"),
        "small files (1500 x 64 KB)": make(os.path.join(src_root, "small"), 1500, 64 << 10, "IMG"),
    }
    for name, files in sets.items():
        total = sum(os.path.getsize(p) for p in files)
        print(f"\n{name}: {human_bytes(total)}")
        base = None
        for w in (1, 2, 3, 4):
            dt, res = run(files, os.path.join(dst_root, "out"), w)
            base = base or dt
            print(f"  {w} at once: {dt:6.2f}s  {human_bytes(total / dt):>9}/s overall   "
                  f"copy {human_bytes(res.copy_rate):>9}/s  read-back {human_bytes(res.verify_rate):>9}/s per file"
                  f"   x{base / dt:.2f}")
    shutil.rmtree(os.path.join(dst_root, "out"), ignore_errors=True)


if __name__ == "__main__":
    main()
