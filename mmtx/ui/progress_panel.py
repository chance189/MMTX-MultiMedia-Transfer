"""Bottom panel: live progress, speed, ETA, cancel, log."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QProgressBar

from ..transfer import human_bytes, human_duration
from . import theme
from .widgets import Card, button, label

LEVEL_COLORS = {"error": theme.DANGER, "warn": theme.WARN, "info": "#b9c0ce"}


class ProgressPanel(Card):
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__("", parent)
        self.body.setSpacing(6)

        top = QHBoxLayout()
        self.phase = label("Ready", "Big")
        self.file = label("", "Muted")
        self.file.setMinimumWidth(100)
        self.rate = label("", "Muted")
        self.rate.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self.phase)
        top.addSpacing(10)
        top.addWidget(self.file, 1)
        top.addWidget(self.rate)
        self.body.addLayout(top)

        self.file_bar = QProgressBar()
        self.file_bar.setObjectName("Thin")
        self.file_bar.setRange(0, 1000)
        self.file_bar.setTextVisible(False)
        self.body.addWidget(self.file_bar)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.body.addWidget(self.bar)

        bottom = QHBoxLayout()
        self.stats = label("Insert a card or pick a folder to get started.", "Muted")
        bottom.addWidget(self.stats, 1)
        self.log_btn = button("Show log", "Ghost", slot=self._toggle_log)
        bottom.addWidget(self.log_btn)
        self.cancel_btn = button("Cancel", "Danger", slot=self.cancel_requested.emit)
        self.cancel_btn.setEnabled(False)
        bottom.addWidget(self.cancel_btn)
        self.body.addLayout(bottom)

        self.log = QPlainTextEdit()
        self.log.setObjectName("Log")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMinimumHeight(160)
        self.log.setVisible(False)
        self.body.addWidget(self.log)
        self._full_name = ""

    # ---------------------------------------------------------------- state
    def start(self, title: str) -> None:
        self.phase.setText("Starting…")
        self.file.setText(title)
        self.rate.setText("")
        self.stats.setText("")
        self._set_state("")
        self.bar.setValue(0)
        self.file_bar.setValue(0)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")

    def finish(self, ok: bool, headline: str, detail: str) -> None:
        self.cancel_btn.setEnabled(False)
        self.phase.setText(headline)
        self.file.setText("")
        self.rate.setText("")
        self.stats.setText(detail)
        self._set_state("ok" if ok else "bad")
        if ok:
            self.bar.setValue(1000)
            self.file_bar.setValue(1000)

    def set_busy_text(self, phase: str, detail: str = "") -> None:
        self.phase.setText(phase)
        self.file.setText(detail)

    def cancelling(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Cancelling…")

    def _set_state(self, s: str) -> None:
        self.bar.setProperty("state", s)
        self.bar.style().unpolish(self.bar)
        self.bar.style().polish(self.bar)

    # ---------------------------------------------------------------- events
    def on_event(self, kind: str, p: dict) -> None:
        if kind == "progress":
            self._progress(p)
        elif kind == "log":
            self.append_log(p["level"], p["line"])

    def _progress(self, p: dict) -> None:
        self.phase.setText(p["phase"] or "Working")
        self._full_name = p["name"]
        fm = QFontMetrics(self.file.font())
        self.file.setText(fm.elidedText(p["name"], Qt.ElideMiddle, max(80, self.file.width())))
        self.file.setToolTip(p["name"])
        ft = p["file_total"]
        self.file_bar.setValue(int(1000 * p["file_done"] / ft) if ft else 0)
        total = p["total"]
        self.bar.setValue(int(1000 * p["done"] / total) if total else 0)
        spd = p["speed"]
        eta = p["eta"]
        rate = f"{human_bytes(spd)}/s" if spd > 0 else ""
        if eta >= 0:
            rate += f"   ·   {human_duration(eta)} left"
        self.rate.setText(rate)
        pct = 100.0 * p["done"] / total if total else 0
        self.stats.setText(
            f"{pct:.0f}%   ·   {human_bytes(p['data_done'])} of {human_bytes(p['data_total'])}"
            f"   ·   file {min(p['files_done'] + 1, p['files_total'])} of {p['files_total']}"
        )

    def append_log(self, level: str, line: str) -> None:
        color = LEVEL_COLORS.get(level, "#b9c0ce")
        safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.log.appendHtml(f'<span style="color:{color}; white-space:pre">{safe}</span>')

    def _toggle_log(self) -> None:
        vis = not self.log.isVisible()
        self.log.setVisible(vis)
        self.log_btn.setText("Hide log" if vis else "Show log")
