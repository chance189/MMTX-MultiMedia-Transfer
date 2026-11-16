"""Every connected card and device, each with its own scan and status.

The app reads every camera card / USB device as soon as it shows up (one at a time, in the
background) and keeps the result, so switching between them is instant and each one can say
"Reading…", "22 new · 1.4 GB", "All saved" or "Copying 7/12" on its own. One of them is the
selected card that the Offload page and Easy mode show; "Copy all" puts every device with new
files into one job (see planner.merge_offload_jobs).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from ..drives import auto_workers, drive_info
from ..planner import day_presence, existing_day_folders, offload_files
from .. import sources


@dataclass
class CardEntry:
    root: str
    vol: object  # volumes.Volume
    scan: Optional[object] = None  # scanner.CardScan
    state: str = "waiting"  # waiting | scanning | ready | empty | unreadable
    new_files: int = 0  # media files not yet at the destination
    new_bytes: int = 0
    new_days: int = 0
    # while a job runs: files of this device in the job / done so far
    job_total: int = 0
    job_done: int = 0
    job_failed: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        v = self.vol
        return (getattr(v, "camera", "") or getattr(v, "name", "") or "Card") if v is not None else "Card"

    @property
    def icon(self) -> str:
        v = self.vol
        kind = getattr(v, "icon", "") if v is not None else ""
        return kind or sources.device_kind_for(self.name)

    @property
    def portable(self) -> bool:
        return sources.is_portable(self.root) or bool(getattr(self.vol, "portable", False))

    @property
    def copying(self) -> bool:
        return self.job_total > 0 and self.job_done < self.job_total

    def status(self) -> tuple[str, str]:
        """(short text, tone) for chips and sidebar tiles. tone: busy | done | new | warn | idle"""
        if self.job_total:
            if self.job_done < self.job_total:
                return f"Copying {self.job_done}/{self.job_total}", "busy"
            if self.job_failed:
                return f"{self.job_failed} not copied", "warn"
            return "Saved", "done"
        if self.state in ("waiting", "scanning"):
            return "Reading…", "busy"
        if self.state == "empty":
            return "No videos", "idle"
        if self.state == "unreadable":
            return "Can't read", "warn"
        if self.new_files:
            from ..transfer import human_bytes

            return f"{self.new_files} new  ·  {human_bytes(self.new_bytes)}", "new"
        return "All saved", "done"


def summarize(entry: CardEntry, settings) -> None:
    """Count what on this card isn't at the destination yet (media files, like the Easy screens)."""
    from .offload_tab import device_folders, offload_options

    entry.new_files = entry.new_bytes = entry.new_days = 0
    scan = entry.scan
    if scan is None or not scan.files:
        return
    dest = settings.offload_dest
    existing = existing_day_folders(dest) if dest and os.path.isdir(dest) else {}
    opts = offload_options(settings, False)
    dev = device_folders(settings, scan)
    for day in scan.days:
        files = offload_files(day, opts)
        if day.day in existing:
            present = day_presence(day, os.path.join(dest, existing[day.day][0]), dev, opts)[2]
            missing = [f for f in files if f.path not in present]
        else:
            missing = files
        media = [f for f in missing if f.role == "media"]
        if media:
            entry.new_days += 1
            entry.new_files += len(media)
            entry.new_bytes += sum(f.size for f in missing)


def source_limit(entry_or_root, dest_root: str, settings) -> tuple[int, str]:
    """How many files to read at once from one card/device (same rule as a single-card job)."""
    root = entry_or_root.root if isinstance(entry_or_root, CardEntry) else entry_or_root
    portable = entry_or_root.portable if isinstance(entry_or_root, CardEntry) else sources.is_portable(root)
    if portable:
        return 1, "USB device: one file at a time"
    if settings.workers > 0:
        return settings.workers, "set in Settings"
    return auto_workers(drive_info(root), drive_info(dest_root))
