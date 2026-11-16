"""Summary and settings dialogs."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QPlainTextEdit,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..hashing import ALGORITHMS, available_algorithms
from ..media import find_ffmpeg
from ..settings import Settings, logs_dir
from ..transfer import JobResult, human_bytes, human_duration
from . import theme
from .widgets import Card, button, label, open_path, ro_item

GROUP_STATUS = {
    "verified": ("✓ Verified", theme.SUCCESS),
    "moved": ("✓ Moved (same drive)", theme.SUCCESS),
    "warnings": ("! Check notes", theme.WARN),
    "errors": ("✕ Errors", theme.DANGER),
    "cancelled": ("Stopped", theme.MUTED),
    "pending": ("Not started", theme.MUTED),
}


class SummaryDialog(QDialog):
    def __init__(self, res: JobResult, parent=None):
        super().__init__(parent)
        self.res = res
        self.setWindowTitle("Transfer summary")
        self.setMinimumWidth(720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)

        ok = res.ok and not res.warnings
        if res.cancelled:
            icon, color, head = "■", theme.MUTED, "Cancelled"
        elif res.aborted:
            icon, color, head = "✕", theme.DANGER, "Stopped"
        elif res.errors:
            icon, color, head = "✕", theme.DANGER, f"Finished with {len(res.errors)} problem{'s' if len(res.errors) != 1 else ''}"
        elif res.warnings:
            icon, color, head = "!", theme.WARN, "Done, with warnings"
        else:
            icon, color, head = "✓", theme.SUCCESS, self._headline_ok(res)
        top = QHBoxLayout()
        ic = label(icon)
        ic.setStyleSheet(f"color:{color}; font-size:34pt; font-weight:800; background:transparent;")
        ic.setFixedWidth(64)
        ic.setAlignment(Qt.AlignCenter)
        top.addWidget(ic)
        tv = QVBoxLayout()
        h = label(head, "Big")
        h.setStyleSheet("font-size:15pt; font-weight:800;")
        tv.addWidget(h)
        sub = self._subtitle(res, ok)
        tv.addWidget(label(sub, "Muted", wrap=True))
        top.addLayout(tv, 1)
        lay.addLayout(top)

        card = Card()
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(6)
        for i, (k, v) in enumerate(self._stats(res)):
            r, c = divmod(i, 3)
            box = QVBoxLayout()
            box.setSpacing(0)
            box.addWidget(label(k.upper(), "CardTitle"))
            val = label(v)
            val.setStyleSheet("font-size:13pt; font-weight:700; background:transparent;")
            box.addWidget(val)
            grid.addLayout(box, r, c)
        card.body.addLayout(grid)
        lay.addWidget(card)

        if res.kind in ("offload", "archive") and res.groups:
            t = QTableWidget(len(res.groups), 4)
            t.setHorizontalHeaderLabels(["Folder", "Files", "Result", "Source"])
            t.verticalHeader().setVisible(False)
            t.setEditTriggers(QTableWidget.NoEditTriggers)
            t.setShowGrid(False)
            t.setAlternatingRowColors(True)
            t.verticalHeader().setDefaultSectionSize(30)
            t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            t.setColumnWidth(1, 70)
            t.setColumnWidth(2, 170)
            t.setColumnWidth(3, 140)
            for r, g in enumerate(res.groups):
                t.setItem(r, 0, ro_item(g.name, g.dest_dir))
                okn = sum(1 for x in g.tasks if x.status in ("copied", "skipped", "moved"))
                t.setItem(r, 1, ro_item(f"{okn}/{len(g.tasks)}"))
                txt, col = GROUP_STATUS.get(g.status, (g.status, theme.MUTED))
                it = ro_item(txt, "\n".join(g.notes))
                it.setForeground(QColor(col))
                t.setItem(r, 2, it)
                if g.delete_source:
                    src = "deleted" if g.source_deleted else "kept"
                else:
                    src = "untouched"
                si = ro_item(src)
                si.setForeground(QColor(theme.MUTED if src != "kept" else theme.WARN))
                t.setItem(r, 3, si)
            t.setMaximumHeight(min(260, 40 + 30 * len(res.groups)))
            lay.addWidget(t)

        problems = list(res.errors) + list(res.warnings)
        if res.aborted:
            problems.insert(0, res.aborted)
        if problems:
            box = QPlainTextEdit("\n".join(problems))
            box.setObjectName("Log")
            box.setReadOnly(True)
            box.setMaximumHeight(160)
            lay.addWidget(box)

        btns = QHBoxLayout()
        dest = next((d for d in res.dest_dirs if os.path.isdir(d)), "")
        if dest:
            target = dest if len(res.dest_dirs) == 1 else os.path.dirname(dest)
            btns.addWidget(button("Open destination", slot=lambda: open_path(target)))
        if res.log_path:
            btns.addWidget(button("Open log", "Ghost", slot=lambda: open_path(res.log_path)))
        btns.addStretch(1)
        close = button("Done", "Primary", slot=self.accept)
        close.setDefault(True)
        btns.addWidget(close)
        lay.addLayout(btns)

    @staticmethod
    def _headline_ok(res: JobResult) -> str:
        if res.kind == "verify":
            return f"All {res.verified_ok} files are bit-perfect"
        if res.kind == "checksum":
            return f"Recorded checksums for {res.files_ok} files"
        return f"All {res.files_ok} files verified"

    @staticmethod
    def _subtitle(res: JobResult, ok: bool) -> str:
        if res.kind == "verify":
            return f"{res.manifests} folder(s) re-read from disk and compared to their recorded checksums."
        if res.kind == "checksum":
            return f"{res.manifests} manifest(s) written. Use Verify any time to re-check."
        if ok:
            return ("Every file was read back from the destination and matched its source checksum. "
                    "Nothing needs to be checked by hand.")
        return "Files that verified are safe at the destination. Anything listed below needs a look."

    @staticmethod
    def _stats(res: JobResult) -> list[tuple[str, str]]:
        dur = human_duration(res.duration)
        if res.kind == "verify":
            return [
                ("Bit-perfect", str(res.verified_ok)), ("Mismatched", str(res.mismatched)), ("Missing", str(res.missing)),
                ("Not in manifest", str(res.extra)), ("Data read", human_bytes(res.bytes_total)), ("Time", dur),
            ]
        if res.kind == "checksum":
            return [("Files hashed", str(res.files_ok)), ("Manifests", str(res.manifests)), ("Time", dur)]
        stats = [
            ("Copied + verified", str(res.files_copied)),
            ("Already there", str(res.files_skipped)),
            ("Failed", str(res.files_failed)),
            ("Data written", human_bytes(res.bytes_written)),
            ("Time", dur),
            ("Overall speed", f"{human_bytes(res.throughput)}/s" if res.throughput else "n/a")
            if not res.files_moved else ("Moved (instant)", str(res.files_moved)),
        ]
        if res.copy_secs > 0:
            stats.append(("Copy, per file", f"{human_bytes(res.copy_rate)}/s"))
        if res.verify_secs > 0:
            stats.append(("Read-back, per file", f"{human_bytes(res.verify_rate)}/s"))
        stats.append(("Files at once", str(res.workers)))
        if res.algo:
            stats.append(("Checksum", res.algo))
        return stats


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Settings")
        self.setMinimumWidth(720)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 14)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("Page")
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 18, 20, 6)
        lay.setSpacing(12)

        v = Card("Verification")
        row = QHBoxLayout()
        row.addWidget(label("Checksum"))
        self.algo = QComboBox()
        for a in available_algorithms():
            self.algo.addItem(ALGORITHMS[a][0], a)
        self.algo.setCurrentIndex(max(0, self.algo.findData(s.algo)))
        row.addWidget(self.algo, 1)
        v.body.addLayout(row)
        self.readback = QCheckBox("Read every file back from the destination and compare checksums  (recommended)")
        self.readback.setChecked(s.verify_readback)
        self.uncached = QCheckBox("Bypass the OS cache on read-back, so the check reads the actual disk")
        self.uncached.setChecked(s.uncached)
        v.body.addWidget(self.readback)
        v.body.addWidget(self.uncached)
        v.body.addWidget(label("Folders that already have a manifest keep its algorithm, so old checksums stay comparable.", "Hint", wrap=True))
        lay.addWidget(v)

        sp = Card("Speed")
        r3 = QHBoxLayout()
        r3.addWidget(label("Files at once"))
        self.workers = QComboBox()
        self.workers.addItem("Auto  (by drive type, recommended)", 0)
        for n_ in (1, 2, 3, 4):
            self.workers.addItem(f"{n_}" + ("  (one at a time)" if n_ == 1 else ""), n_)
        self.workers.setCurrentIndex(max(0, self.workers.findData(s.workers)))
        r3.addWidget(self.workers, 1)
        sp.body.addLayout(r3)
        sp.body.addWidget(label(
            "Auto copies one file at a time to hard drives (parallel writes make them seek and fragment files) and "
            "2-3 at once between flash drives, where one file's read-back overlaps the next copy. "
            "The summary after each transfer shows copy vs read-back speed so you can see what limits it.",
            "Hint", wrap=True))
        lay.addWidget(sp)

        n = Card("Naming")
        self.spaces = QCheckBox("Replace spaces in titles with underscores  (Morning Run → Morning_Run)")
        self.spaces.setChecked(s.spaces_to_underscores)
        self.keep = QCheckBox("Keep the card's original folder structure instead of flat camera folders")
        self.keep.setToolTip("Useful for Sony Catalyst Browse, which expects PRIVATE/M4ROOT/…")
        self.keep.setChecked(s.keep_structure)
        self.gopro = QCheckBox("Rename GoPro chapters so they sort together  (GX010123 → GX0123_01)")
        self.gopro.setChecked(s.rename_gopro)
        self.subfolders = QCheckBox("Put each camera in its own subfolder  (off: files go straight into the day folder)")
        self.subfolders.setChecked(s.per_camera_subfolders)
        for w in (self.spaces, self.keep, self.gopro, self.subfolders):
            n.body.addWidget(w)
        lay.addWidget(n)

        th = Card("Thumbnails")
        self.thumbs = QCheckBox("Show clip thumbnails  (paused automatically while a transfer runs)")
        self.thumbs.setChecked(s.thumbnails)
        th.body.addWidget(self.thumbs)
        ff = find_ffmpeg()
        th.body.addWidget(label(
            f"Video frames via ffmpeg: {ff}" if ff else
            "ffmpeg not found: video tiles use camera thumbnails (Sony, GoPro .THM) or a placeholder. "
            "pip install imageio-ffmpeg adds it.", "Hint", wrap=True))
        lay.addWidget(th)

        from .palettes import PALETTES

        ez = Card("Easy mode colors")
        self.palette = QComboBox()
        for k, pal in PALETTES.items():
            self.palette.addItem(f"{pal.name}   ({pal.blurb})", k)
        self.palette.setCurrentIndex(max(0, self.palette.findData(s.easy_palette)))
        ez.body.addWidget(self.palette)
        ez.body.addWidget(label("Red = still to do, blue = copying, orange = almost done, green = done, "
                                "grey = waiting. Same meaning in every palette.", "Hint", wrap=True))
        lay.addWidget(ez)

        lg = Card("Logs")
        r2 = QHBoxLayout()
        r2.addWidget(label(logs_dir(), "Muted"), 1)
        r2.addWidget(button("Open", slot=lambda: (os.makedirs(logs_dir(), exist_ok=True), open_path(logs_dir()))))
        lg.body.addLayout(r2)
        lay.addWidget(lg)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        brow = QHBoxLayout()
        brow.setContentsMargins(20, 0, 20, 0)
        brow.addWidget(bb)
        outer.addLayout(brow)
        self.resize(760, min(820, body.sizeHint().height() + 70))

    def _save(self) -> None:
        self.s.algo = self.algo.currentData()
        self.s.verify_readback = self.readback.isChecked()
        self.s.uncached = self.uncached.isChecked()
        self.s.spaces_to_underscores = self.spaces.isChecked()
        self.s.keep_structure = self.keep.isChecked()
        self.s.rename_gopro = self.gopro.isChecked()
        self.s.per_camera_subfolders = self.subfolders.isChecked()
        self.s.workers = int(self.workers.currentData())
        self.s.thumbnails = self.thumbs.isChecked()
        self.s.easy_palette = self.palette.currentData()
        self.s.save()
        self.accept()
