"""Transfer engine: copy, checksum, verify, move. No Qt in here.

Per file:
  1. If the destination already exists with the same size, hash both sides.
     Identical -> skipped (already there, proven). Different -> new name.
  2. Copy to ``<name>.mmtx-partial`` while hashing the source stream.
  3. fsync, evict from cache, then read the partial back from disk and hash it.
  4. Hashes match -> rename to the final name. Mismatch -> retry once, then fail.
  5. If the source folder carries a manifest from an earlier offload, the source
     hash is also checked against it (catches rot on the working drive).

Per group (one day folder / one archive folder):
  * The manifest is written (merged) at the group's destination root.
  * "Move" deletes the source only if every file in the group verified.
"""

from __future__ import annotations

import errno
import os
import posixpath
import queue
import shutil
import stat
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from .hashing import (
    CHUNK,
    Cancelled,
    drop_cache_fd,
    hash_file,
    long_path as lp,
    new_hasher,
    set_nocache_fd,
)
from .manifest import is_manifest, load_folder_manifest, write_manifest

PARTIAL_SUFFIX = ".mmtx-partial"
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini", "ehthumbs.db"}


def is_junk(name: str) -> bool:
    low = name.lower()
    return low in JUNK_FILES or name.startswith("._") or low.endswith(PARTIAL_SUFFIX)


class DiskFull(Exception):
    pass


class SourceGone(Exception):
    pass


# ----------------------------------------------------------------------------
# Job description
# ----------------------------------------------------------------------------


@dataclass
class FileTask:
    src: str
    dst: str
    size: int
    rel: str  # path relative to group.dest_dir, forward slashes (manifest key)
    src_rel: Optional[str] = None  # key in the source folder's manifest
    status: str = "pending"  # copied | skipped | moved | failed | cancelled
    message: str = ""
    hash: str = ""


@dataclass
class Group:
    name: str
    dest_dir: str
    algo: str
    tasks: list[FileTask] = field(default_factory=list)
    source_root: str = ""  # used to detect a pulled card / unplugged drive
    source_dir: Optional[str] = None  # archive: folder removed after a move
    source_manifest: dict[str, str] = field(default_factory=dict)
    delete_source: bool = False
    prune_source_dir: bool = False
    fast_rename: bool = False
    # results
    status: str = "pending"
    notes: list[str] = field(default_factory=list)
    source_deleted: bool = False
    manifest_path: str = ""

    @property
    def size(self) -> int:
        return sum(t.size for t in self.tasks)


@dataclass
class JobOptions:
    verify_readback: bool = True
    uncached: bool = True
    retries: int = 1


@dataclass
class Job:
    kind: str  # offload | archive
    title: str
    groups: list[Group]
    options: JobOptions = field(default_factory=JobOptions)

    @property
    def total_bytes(self) -> int:
        return sum(g.size for g in self.groups)

    @property
    def total_files(self) -> int:
        return sum(len(g.tasks) for g in self.groups)


@dataclass
class JobResult:
    kind: str
    title: str
    algo: str = ""
    started: float = 0.0
    finished: float = 0.0
    files_total: int = 0
    files_ok: int = 0
    files_copied: int = 0
    files_skipped: int = 0
    files_moved: int = 0
    files_failed: int = 0
    bytes_total: int = 0
    bytes_written: int = 0
    cancelled: bool = False
    aborted: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    log_path: str = ""
    dest_dirs: list[str] = field(default_factory=list)
    # verify-specific
    verified_ok: int = 0
    mismatched: int = 0
    missing: int = 0
    extra: int = 0
    manifests: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors and not self.cancelled and not self.aborted

    @property
    def duration(self) -> float:
        return max(0.0, self.finished - self.started)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def unique_path(path: str) -> str:
    base, ext = os.path.splitext(path)
    i = 1
    while True:
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(lp(cand)) and not os.path.exists(lp(cand + PARTIAL_SUFFIX)):
            return cand
        i += 1


def same_device(a: str, b: str) -> bool:
    try:
        return os.stat(lp(a)).st_dev == os.stat(lp(b)).st_dev
    except OSError:
        return False


def free_bytes(path: str) -> int:
    p = os.path.abspath(path)
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return -1


def estimate_needed_bytes(job: Job) -> int:
    """Bytes that will actually be written (files already present are skipped)."""
    need = 0
    for g in job.groups:
        if g.fast_rename:
            continue
        for t in g.tasks:
            try:
                if os.path.getsize(lp(t.dst)) == t.size:
                    continue
            except OSError:
                pass
            need += t.size
    return need


def human_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000.0
    return f"{n:.1f} TB"


def human_duration(s: float) -> str:
    s = int(max(0, s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def _copy_times(src: str, dst: str) -> None:
    """Carry over the camera's timestamps (NLEs and date grouping rely on them).
    Permission/read-only bits are deliberately not copied."""
    try:
        st = os.stat(lp(src))
        os.utime(lp(dst), ns=(st.st_atime_ns, st.st_mtime_ns))
    except OSError:
        pass


def _retry(fn, *args, tries: int = 10, delay: float = 0.2):
    """Windows: antivirus/indexer can hold a fresh file for a moment (WinError 32)."""
    for i in range(tries):
        try:
            return fn(*args)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(delay)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        _retry(os.remove, path)


# ----------------------------------------------------------------------------
# Runner base: progress, speed, logging, cancel
# ----------------------------------------------------------------------------

EmitFn = Callable[[str, dict], None]


class RunnerBase:
    def __init__(self, emit: Optional[EmitFn] = None, log_dir: Optional[str] = None):
        self.emit = emit or (lambda kind, payload: None)
        self.cancel_event = threading.Event()
        self.total = 0
        self.done = 0
        self.files_total = 0
        self.files_done = 0
        self.phase = ""
        self.cur_name = ""
        self.cur_total = 0
        self.cur_done = 0
        self._last_emit = 0.0
        self._samples: deque = deque()
        self.mult = 1  # bytes processed per byte of data (2 = copy + read-back)
        self._log_fh = None
        self.log_path = ""
        if log_dir:
            try:
                os.makedirs(log_dir, exist_ok=True)
                self.log_path = os.path.join(
                    log_dir, datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{self.__class__.__name__}.log"
                )
                self._log_fh = open(self.log_path, "w", encoding="utf-8")
            except OSError:
                self._log_fh = None
                self.log_path = ""

    # -- public --
    def cancel(self) -> None:
        self.cancel_event.set()

    # -- logging --
    def log(self, level: str, msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')} [{level.upper():5}] {msg}"
        if self._log_fh:
            try:
                self._log_fh.write(line + "\n")
                self._log_fh.flush()
            except OSError:
                pass
        self.emit("log", {"level": level, "msg": msg, "line": line})

    def close_log(self) -> None:
        if self._log_fh:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None

    # -- progress --
    def set_file(self, phase: str, name: str, total: int) -> None:
        self.phase, self.cur_name, self.cur_total, self.cur_done = phase, name, total, 0
        self._emit_progress(force=True)

    def tick(self, n: int) -> None:
        self.done = min(self.total, self.done + n) if self.total else self.done + n
        self.cur_done += n
        if self.cancel_event.is_set():
            raise Cancelled()
        self._emit_progress()

    def _speed(self, now: float) -> float:
        self._samples.append((now, self.done))
        while len(self._samples) > 2 and now - self._samples[0][0] > 3.0:
            self._samples.popleft()
        t0, d0 = self._samples[0]
        return (self.done - d0) / (now - t0) if now - t0 > 0.25 else 0.0

    def _emit_progress(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < 0.1:
            return
        self._last_emit = now
        speed = self._speed(now)
        remaining = max(0, self.total - self.done)
        self.emit(
            "progress",
            {
                "phase": self.phase,
                "name": self.cur_name,
                "file_done": self.cur_done,
                "file_total": self.cur_total,
                "done": self.done,
                "total": self.total,
                "data_done": self.done // self.mult,
                "data_total": self.total // self.mult,
                "files_done": self.files_done,
                "files_total": self.files_total,
                "speed": speed,
                "eta": remaining / speed if speed > 1 else -1,
            },
        )


# ----------------------------------------------------------------------------
# Copy + verify runner
# ----------------------------------------------------------------------------


class TransferRunner(RunnerBase):
    def __init__(self, job: Job, emit: Optional[EmitFn] = None, log_dir: Optional[str] = None):
        super().__init__(emit, log_dir)
        self.job = job
        self.opt = job.options
        self.mult = 2 if self.opt.verify_readback else 1
        self.result = JobResult(kind=job.kind, title=job.title)
        # test hook: called with the partial path after copy, before verify
        self._after_copy_hook: Optional[Callable[[str], None]] = None

    # ---- copy with inline source hashing ----
    def _copy_hash(self, src: str, dst: str, algo: str) -> str:
        h = new_hasher(algo)
        q: queue.Queue = queue.Queue(maxsize=4)
        stop = threading.Event()
        errs: list[BaseException] = []

        def reader() -> None:
            try:
                with open(lp(src), "rb", buffering=0) as f:
                    while not stop.is_set():
                        b = f.read(CHUNK)
                        if not b:
                            break
                        q.put(b)
            except BaseException as e:  # noqa: BLE001
                errs.append(e)
            finally:
                q.put(None)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            fd = os.open(lp(dst), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o666)
            with os.fdopen(fd, "wb", buffering=0) as out:
                if self.opt.uncached:
                    set_nocache_fd(out.fileno())
                while True:
                    b = q.get()
                    if b is None:
                        break
                    h.update(b)
                    mv = memoryview(b)
                    while mv:
                        n = out.write(mv)
                        mv = mv[n:]
                    self.result.bytes_written += len(b)
                    self.tick(len(b))
                out.flush()
                os.fsync(out.fileno())
                if self.opt.uncached:
                    drop_cache_fd(out.fileno())
        finally:
            stop.set()
            while t.is_alive():
                try:
                    q.get(timeout=0.05)
                except queue.Empty:
                    pass
            t.join()
        if errs:
            raise errs[0]
        return h.hexdigest()

    # ---- one file ----
    def _process(self, g: Group, t: FileTask) -> None:
        budget = t.size * self.mult
        start = self.done
        partial = ""
        try:
            st = os.stat(lp(t.src))
            t.size = st.st_size
            final = t.dst
            os.makedirs(lp(os.path.dirname(final)), exist_ok=True)

            if os.path.exists(lp(final)):
                if os.path.getsize(lp(final)) == t.size:
                    self.set_file("Checking existing", os.path.basename(final), t.size * 2)
                    sh = hash_file(t.src, g.algo, uncached=False, on_bytes=self.tick, cancel=self.cancel_event)
                    dh = hash_file(final, g.algo, uncached=self.opt.uncached, on_bytes=self.tick, cancel=self.cancel_event)
                    if sh == dh:
                        t.hash, t.status = sh, "skipped"
                        t.message = "already at destination, checksum matches"
                        self.log("info", f"Skip  {t.rel}  (already there, {g.algo} match)")
                        self._check_expected(g, t)
                        return
                final = unique_path(final)
                t.dst = final
                t.rel = posixpath.join(posixpath.dirname(t.rel), os.path.basename(final))
                self.log("warn", f"Name conflict: different file already at destination, saving as {t.rel}")

            src_hash = dst_hash = ""
            for attempt in range(1 + self.opt.retries):
                partial = final + PARTIAL_SUFFIX
                if os.path.exists(lp(partial)):
                    os.remove(lp(partial))
                self.set_file("Copying", os.path.basename(final), t.size)
                src_hash = self._copy_hash(t.src, partial, g.algo)
                _copy_times(t.src, partial)
                if self._after_copy_hook:
                    self._after_copy_hook(partial)
                if self.opt.verify_readback:
                    self.set_file("Verifying", os.path.basename(final), t.size)
                    dst_hash = hash_file(partial, g.algo, uncached=self.opt.uncached, on_bytes=self.tick, cancel=self.cancel_event)
                else:
                    dst_hash = src_hash if os.path.getsize(lp(partial)) == t.size else "size-mismatch"
                if dst_hash == src_hash:
                    break
                self.log("error", f"Checksum MISMATCH on {t.rel} (attempt {attempt + 1}): src {src_hash} dst {dst_hash}")
                os.remove(lp(partial))
                partial = ""
            else:
                t.status = "failed"
                t.message = "checksum mismatch after retry"
                self.result.errors.append(f"{g.name}/{t.rel}: checksum mismatch after retry")
                return

            # source changed while we were reading it?
            st2 = os.stat(lp(t.src))
            if st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns:
                os.remove(lp(partial))
                partial = ""
                t.status, t.message = "failed", "source file changed during copy"
                self.result.errors.append(f"{g.name}/{t.rel}: source changed during copy")
                self.log("error", f"Source changed during copy: {t.src}")
                return

            _retry(os.rename, lp(partial), lp(final))
            partial = ""
            t.hash, t.status = src_hash, "copied"
            self.log("info", f"OK    {t.rel}  {human_bytes(t.size)}  {g.algo}:{src_hash}")
            self._check_expected(g, t)
        except Cancelled:
            t.status = "cancelled"
            raise
        except OSError as e:
            t.status, t.message = "failed", e.strerror or str(e)
            self.result.errors.append(f"{g.name}/{t.rel}: {t.message}")
            self.log("error", f"FAIL  {t.rel}: {e}")
            if e.errno == errno.ENOSPC:
                raise DiskFull(str(e))
            if g.source_root and not os.path.exists(lp(g.source_root)):
                raise SourceGone(g.source_root)
        finally:
            if partial:
                try:
                    os.remove(lp(partial))
                except OSError:
                    pass
            # keep the overall bar honest regardless of how this file ended
            spent = self.done - start
            if spent < budget:
                self.done = min(self.total, self.done + (budget - spent))
            self.files_done += 1

    def _check_expected(self, g: Group, t: FileTask) -> None:
        if not t.src_rel or not g.source_manifest:
            return
        exp = g.source_manifest.get(t.src_rel)
        if exp is None:
            return
        if exp != t.hash:
            msg = (
                f"{g.name}/{t.rel}: source no longer matches the checksum recorded at offload "
                f"(recorded {exp}, now {t.hash}). Source kept."
            )
            g.notes.append(msg)
            self.result.warnings.append(msg)
            self.log("warn", msg)

    # ---- group-level ----
    def _fast_rename(self, g: Group) -> None:
        assert g.source_dir
        self.set_file("Moving (same drive)", g.name, g.size)
        os.makedirs(lp(os.path.dirname(g.dest_dir)), exist_ok=True)
        os.rename(lp(g.source_dir), lp(g.dest_dir))
        for t in g.tasks:
            t.status = "moved"
        self.done = min(self.total, self.done + g.size * self.mult)
        self.files_done += len(g.tasks)
        g.source_deleted = True
        g.status = "moved"
        self.log("info", f"Moved folder (same drive, instant): {g.source_dir} -> {g.dest_dir}")

    def _finalize_group(self, g: Group, cancelled: bool) -> None:
        good = [t for t in g.tasks if t.status in ("copied", "skipped")]
        if good:
            try:
                g.manifest_path = write_manifest(g.dest_dir, g.algo, {t.rel: t.hash for t in good})
                self.log("info", f"Manifest written: {g.manifest_path} ({len(good)} entries)")
            except OSError as e:
                msg = f"{g.name}: could not write checksum manifest: {e}"
                self.result.errors.append(msg)
                self.log("error", msg)
        if cancelled:
            g.status = "cancelled"
            return
        all_ok = all(t.status in ("copied", "skipped") for t in g.tasks)
        mismatch_vs_record = bool(g.notes)
        if not all_ok:
            g.status = "errors"
        elif mismatch_vs_record:
            g.status = "warnings"
        else:
            g.status = "verified"
        if g.delete_source and all_ok and not mismatch_vs_record and g.manifest_path:
            self._delete_sources(g)
        elif g.delete_source and not (all_ok and not mismatch_vs_record):
            g.notes.append("Source NOT deleted because not every file verified cleanly.")
            self.log("warn", f"{g.name}: source kept (not every file verified cleanly)")

    def _delete_sources(self, g: Group) -> None:
        failed = []
        for t in g.tasks:
            try:
                _remove(lp(t.src))
            except FileNotFoundError:
                pass
            except OSError as e:
                failed.append(f"{t.src}: {e.strerror or e}")
        if g.prune_source_dir and g.source_dir:
            for root, dirs, files in os.walk(lp(g.source_dir), topdown=False):
                for f in files:
                    if is_junk(f) or is_manifest(f):
                        try:
                            _remove(os.path.join(root, f))
                        except OSError:
                            pass
                try:
                    os.rmdir(root)
                except OSError:
                    pass
        if failed:
            for f in failed:
                self.result.warnings.append(f"Could not delete source {f}")
            g.notes.append(f"{len(failed)} source file(s) could not be deleted.")
        leftover = g.source_dir and os.path.exists(lp(g.source_dir))
        if g.prune_source_dir and leftover:
            g.notes.append("Source folder still has files that were not part of this move; left in place.")
        g.source_deleted = not failed
        self.log("info", f"{g.name}: source files deleted after verification")

    # ---- run ----
    def run(self) -> JobResult:
        r = self.result
        r.started = time.time()
        r.groups = self.job.groups
        r.files_total = self.job.total_files
        r.bytes_total = self.job.total_bytes
        r.algo = ", ".join(sorted({g.algo for g in self.job.groups}))
        r.dest_dirs = [g.dest_dir for g in self.job.groups]
        self.total = self.job.total_bytes * self.mult
        self.files_total = self.job.total_files
        self.log("info", f"{self.job.kind.upper()} started: {self.job.title}")
        self.log("info", f"{r.files_total} files, {human_bytes(r.bytes_total)}, verify="
                 f"{'read-back' if self.opt.verify_readback else 'size only'}, cache bypass={self.opt.uncached}")
        current: Optional[Group] = None
        try:
            for g in self.job.groups:
                self.emit("group", {"name": g.name, "state": "start"})
                self.log("info", f"--- {g.name} -> {g.dest_dir}")
                if not g.tasks:  # empty folder: nothing to verify, just mirror it
                    os.makedirs(lp(g.dest_dir), exist_ok=True)
                    if g.delete_source and g.prune_source_dir and g.source_dir:
                        self._delete_sources(g)
                    g.status = "verified"
                    self.emit("group", {"name": g.name, "state": g.status})
                    continue
                if g.fast_rename:
                    try:
                        self._fast_rename(g)
                        self.emit("group", {"name": g.name, "state": g.status})
                        continue
                    except OSError as e:
                        self.log("warn", f"Fast move failed ({e}); falling back to copy + verify")
                        g.fast_rename = False
                current = g
                for t in g.tasks:
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    self._process(g, t)
                    self.emit("file", {"group": g.name, "rel": t.rel, "status": t.status})
                self._finalize_group(g, cancelled=False)
                self.emit("group", {"name": g.name, "state": g.status})
                current = None
        except Cancelled:
            r.cancelled = True
            self.log("warn", "Cancelled by user. Completed files are verified and kept; partial file removed.")
        except DiskFull as e:
            r.aborted = f"Destination is full: {e}"
            self.log("error", r.aborted)
        except SourceGone as e:
            r.aborted = f"Source disappeared (card or drive removed?): {e}"
            self.log("error", r.aborted)
        finally:
            if current is not None and not current.fast_rename:
                self._finalize_group(current, cancelled=True)
            for g in self.job.groups:
                for t in g.tasks:
                    if t.status == "copied":
                        r.files_copied += 1
                    elif t.status == "skipped":
                        r.files_skipped += 1
                    elif t.status == "moved":
                        r.files_moved += 1
                    elif t.status == "failed":
                        r.files_failed += 1
            r.files_ok = r.files_copied + r.files_skipped + r.files_moved
            r.finished = time.time()
            self.files_done = self.files_total if r.ok else self.files_done
            if r.ok:
                self.done = self.total
            self.phase = "Done" if r.ok else ("Cancelled" if r.cancelled else "Finished with problems")
            self._emit_progress(force=True)
            self.log(
                "info",
                f"Finished: {r.files_ok}/{r.files_total} files OK ({r.files_copied} copied, "
                f"{r.files_skipped} already there, {r.files_moved} moved), {r.files_failed} failed, "
                f"{human_bytes(r.bytes_written)} written in {human_duration(r.duration)}",
            )
            r.log_path = self.log_path
            self.close_log()
        return r


# ----------------------------------------------------------------------------
# Verify + create-checksum runners
# ----------------------------------------------------------------------------


def find_manifest_folders(root: str, recursive: bool = True) -> list[str]:
    out = []
    if recursive:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".") and x not in ("$RECYCLE.BIN", "System Volume Information")]
            if any(is_manifest(f) for f in files):
                out.append(d)
    elif any(is_manifest(f) for f in os.listdir(root)):
        out.append(root)
    return sorted(out)


def list_folder_files(folder: str) -> list[tuple[str, str, int]]:
    """(abs path, rel posix path, size) for every real file under folder."""
    out = []
    for d, dirs, files in os.walk(folder):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for f in sorted(files):
            if is_junk(f) or is_manifest(f) or f.endswith(".tmp") and is_manifest(f[:-4]):
                continue
            p = os.path.join(d, f)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            rel = os.path.relpath(p, folder).replace(os.sep, "/")
            out.append((p, rel, size))
    return out


class VerifyRunner(RunnerBase):
    """Re-hash folders against their manifests (bit-rot / archive check)."""

    def __init__(self, folders, uncached: bool = True, emit: Optional[EmitFn] = None,
                 log_dir: Optional[str] = None):
        """``folders``: list of folders, or a callable returning one (evaluated off the UI thread)."""
        super().__init__(emit, log_dir)
        self.folders = folders
        self.uncached = uncached
        self.result = JobResult(kind="verify", title="Verify checksums")
        self.rows: list[dict] = []

    def run(self) -> JobResult:
        r = self.result
        r.started = time.time()
        if callable(self.folders):
            self.set_file("Finding checksum files", "", 0)
            self.folders = self.folders()
        if not self.folders:
            r.errors.append("No _mmtx_checksums files found here. Use \"Create missing checksums\" first, "
                            "then verify any time later.")
        plan = []
        for folder in self.folders:
            algo, entries = load_folder_manifest(folder)
            if not algo:
                continue
            files = {rel: (p, size) for p, rel, size in list_folder_files(folder)}
            plan.append((folder, algo, entries, files))
        self.total = sum(files[rel][1] for _, _, entries, files in plan for rel in entries if rel in files)
        self.files_total = sum(len(e) for _, _, e, _ in plan)
        r.manifests = len(plan)
        self.log("info", f"Verifying {r.manifests} folder(s), {self.files_total} files, {human_bytes(self.total)}")
        try:
            for folder, algo, entries, files in plan:
                row = {"folder": folder, "algo": algo, "ok": 0, "mismatch": [], "missing": [], "extra": []}
                self.rows.append(row)
                for rel, expected in sorted(entries.items()):
                    if self.cancel_event.is_set():
                        raise Cancelled()
                    if rel not in files:
                        row["missing"].append(rel)
                        r.missing += 1
                        self.log("error", f"MISSING  {folder} :: {rel}")
                        self.files_done += 1
                        continue
                    p, size = files[rel]
                    self.set_file("Verifying", rel, size)
                    try:
                        got = hash_file(p, algo, uncached=self.uncached, on_bytes=self.tick, cancel=self.cancel_event)
                    except OSError as e:
                        got = f"read error: {e}"
                    self.files_done += 1
                    if got == expected:
                        row["ok"] += 1
                        r.verified_ok += 1
                    else:
                        row["mismatch"].append(rel)
                        r.mismatched += 1
                        self.log("error", f"MISMATCH {folder} :: {rel} expected {expected} got {got}")
                for rel in sorted(set(files) - set(entries)):
                    row["extra"].append(rel)
                    r.extra += 1
                state = "OK" if not row["mismatch"] and not row["missing"] else "PROBLEMS"
                self.log("info" if state == "OK" else "error",
                         f"{state}: {folder}  ok={row['ok']} mismatch={len(row['mismatch'])} "
                         f"missing={len(row['missing'])} not-in-manifest={len(row['extra'])}")
                self.emit("verify_row", row)
        except Cancelled:
            r.cancelled = True
            self.log("warn", "Verify cancelled")
        finally:
            for row in self.rows:
                for rel in row["mismatch"]:
                    r.errors.append(f"Checksum mismatch: {os.path.join(row['folder'], rel)}")
                for rel in row["missing"]:
                    r.errors.append(f"Missing: {os.path.join(row['folder'], rel)}")
            r.files_total = self.files_total
            r.files_ok = r.verified_ok
            r.bytes_total = self.total
            r.finished = time.time()
            if r.ok:
                self.done = self.total
            self.phase = "Done" if r.ok else ("Cancelled" if r.cancelled else "Problems found")
            self._emit_progress(force=True)
            r.log_path = self.log_path
            self.close_log()
        return r


class ChecksumRunner(RunnerBase):
    """Create (or extend) manifests for folders that do not have one yet."""

    def __init__(self, folders, algo: str, uncached: bool = False, emit: Optional[EmitFn] = None,
                 log_dir: Optional[str] = None):
        """``folders``: list of folders, or a callable returning one (evaluated off the UI thread)."""
        super().__init__(emit, log_dir)
        self.folders = folders
        self.algo = algo
        self.uncached = uncached
        self.result = JobResult(kind="checksum", title="Create checksums")

    def run(self) -> JobResult:
        r = self.result
        r.started = time.time()
        if callable(self.folders):
            self.set_file("Finding folders", "", 0)
            self.folders = self.folders()
        if not self.folders:
            r.warnings.append("Every dated folder here already has checksums. Nothing to do.")
        plan = []
        for folder in self.folders:
            algo, entries = load_folder_manifest(folder)
            algo = algo or self.algo
            todo = [(p, rel, s) for p, rel, s in list_folder_files(folder) if rel not in entries]
            plan.append((folder, algo, todo))
        self.total = sum(s for _, _, todo in plan for _, _, s in todo)
        self.files_total = sum(len(todo) for _, _, todo in plan)
        try:
            for folder, algo, todo in plan:
                new = {}
                try:
                    for p, rel, size in todo:
                        self.set_file("Hashing", rel, size)
                        new[rel] = hash_file(p, algo, uncached=self.uncached, on_bytes=self.tick, cancel=self.cancel_event)
                        self.files_done += 1
                finally:
                    if new:
                        path = write_manifest(folder, algo, new)
                        r.manifests += 1
                        r.files_ok += len(new)
                        self.log("info", f"Wrote {path} (+{len(new)} entries)")
        except Cancelled:
            r.cancelled = True
        finally:
            r.files_total = self.files_total
            r.bytes_total = self.total
            r.finished = time.time()
            if r.ok:
                self.done = self.total
            self.phase = "Done" if r.ok else "Cancelled"
            self._emit_progress(force=True)
            r.log_path = self.log_path
            self.close_log()
        return r
