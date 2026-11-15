"""Dark theme: Fusion style + palette + stylesheet."""

from __future__ import annotations

import os
import sys
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

BG = "#0f1115"
SURFACE = "#171a21"
SURFACE2 = "#1d2129"
BORDER = "#2a2f3a"
TEXT = "#e7e9ee"
MUTED = "#8a93a6"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#729cff"
SUCCESS = "#34c38f"
WARN = "#f0a63a"
DANGER = "#f0525a"
INFO = "#4fb3d9"

STYLESHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#Page {{ background: {BG}; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid #242934; border-radius: 12px; }}
QFrame#Card QLabel {{ background: transparent; }}
QFrame#Card QCheckBox, QFrame#Card QRadioButton {{ background: transparent; }}
QLabel#CardTitle {{ color: {MUTED}; font-size: 8.5pt; font-weight: 700; letter-spacing: 1px; }}
QLabel#Brand {{ color: {ACCENT}; font-size: 18pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#BrandSub {{ color: {MUTED}; font-size: 10pt; }}
QLabel#Big {{ font-size: 13pt; font-weight: 700; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Hint {{ color: {MUTED}; font-size: 9pt; }}
QLabel#Badge {{ background: #1b2436; color: #a9c1ff; border: 1px solid #2c3b5c; border-radius: 10px;
               padding: 3px 10px; font-size: 9pt; font-weight: 600; }}

QLineEdit, QComboBox, QDateEdit, QSpinBox {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {ACCENT}; min-height: 20px;
}}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; selection-background-color: #26324d; padding: 4px; }}

QPushButton {{ background: #232834; border: 1px solid #303644; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: #2b3141; border-color: #3a4254; }}
QPushButton:pressed {{ background: #1f2430; }}
QPushButton:disabled {{ color: #5c6475; background: #191c23; border-color: #23272f; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; font-weight: 700; font-size: 11pt;
                       padding: 11px 26px; border-radius: 10px; }}
QPushButton#Primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Primary:disabled {{ background: #253049; color: #7d8bab; }}
QPushButton#Danger {{ background: transparent; border: 1px solid #5a2a30; color: {DANGER}; }}
QPushButton#Danger:hover {{ background: #2a1619; }}
QPushButton#Danger:disabled {{ border-color: #2a2f3a; color: #4b5262; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {MUTED}; padding: 6px 10px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; background: #1d2129; }}
QPushButton#Chip {{ background: transparent; border: 1px solid #303644; border-radius: 13px; padding: 4px 12px;
                    color: {MUTED}; font-size: 9pt; }}
QPushButton#Chip:hover {{ color: {TEXT}; border-color: {ACCENT}; }}

QTabWidget::pane {{ border: none; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 10px 20px; margin-right: 6px;
               border: none; border-bottom: 3px solid transparent; font-weight: 700; font-size: 10.5pt; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 3px solid {ACCENT}; }}
QTabBar::tab:disabled {{ color: #4b5262; }}

QTableWidget {{ background: {SURFACE}; alternate-background-color: #1a1e26; border: 1px solid #242934;
               border-radius: 10px; gridline-color: transparent; selection-background-color: #22304d;
               selection-color: {TEXT}; outline: none; }}
QTableWidget::item {{ padding: 4px 6px; border: none; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid #242934;
                        padding: 8px 6px; font-weight: 700; font-size: 9pt; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QProgressBar {{ background: {SURFACE2}; border: none; border-radius: 7px; min-height: 14px; max-height: 14px;
               text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 7px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
               stop:0 {ACCENT}, stop:1 #3ec6c0); }}
QProgressBar#Thin {{ min-height: 5px; max-height: 5px; border-radius: 2px; }}
QProgressBar#Thin::chunk {{ border-radius: 2px; background: #6b7894; }}
QProgressBar[state="ok"]::chunk {{ background: {SUCCESS}; }}
QProgressBar[state="bad"]::chunk {{ background: {DANGER}; }}

QPlainTextEdit#Log {{ background: #0b0d11; border: 1px solid #222631; border-radius: 8px;
                      font-family: "Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", monospace; font-size: 9pt; }}
QTextBrowser {{ background: #0b0d11; border: 1px solid #222631; border-radius: 8px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2e3442; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3b4253; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2e3442; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: #222733; color: {TEXT}; border: 1px solid #343b4b; padding: 6px; border-radius: 6px; }}
QMessageBox {{ background: {BG}; }}
QGroupBox {{ border: 1px solid #242934; border-radius: 10px; margin-top: 14px; padding: 12px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {MUTED}; font-weight: 700; }}
"""


def _check_image() -> str:
    """Draw the checkmark once (PNG, no SVG plugin needed) and return a QSS-friendly path."""
    d = os.path.join(tempfile.gettempdir(), "mmtx_theme_v1")
    path = os.path.join(d, "check.png")
    if not os.path.exists(path):
        os.makedirs(d, exist_ok=True)
        pm = QPixmap(36, 36)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("white"))
        pen.setWidthF(4.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        tick = QPainterPath()
        tick.moveTo(9, 18.5)
        tick.lineTo(15.5, 25)
        tick.lineTo(27.5, 11.5)
        p.drawPath(tick)
        p.end()
        pm.save(path)
    return path.replace("\\", "/")


def _indicator_qss() -> str:
    check = _check_image()
    return f"""
QCheckBox {{ spacing: 8px; }}
QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QTableView::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #4a5366; background: #12151b; }}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT};
    image: url("{check}"); }}
QCheckBox::indicator:disabled {{ background: #1a1d24; border-color: #2a2f3a; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid #4a5366; background: #12151b; }}
QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QRadioButton::indicator:checked {{ width: 8px; height: 8px; border: 5px solid {ACCENT}; background: white; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    c = QColor
    pal.setColor(QPalette.Window, c(BG))
    pal.setColor(QPalette.WindowText, c(TEXT))
    pal.setColor(QPalette.Base, c(SURFACE2))
    pal.setColor(QPalette.AlternateBase, c("#1a1e26"))
    pal.setColor(QPalette.ToolTipBase, c("#222733"))
    pal.setColor(QPalette.ToolTipText, c(TEXT))
    pal.setColor(QPalette.Text, c(TEXT))
    pal.setColor(QPalette.Button, c("#232834"))
    pal.setColor(QPalette.ButtonText, c(TEXT))
    pal.setColor(QPalette.BrightText, c("#ffffff"))
    pal.setColor(QPalette.Highlight, c(ACCENT))
    pal.setColor(QPalette.HighlightedText, c("#ffffff"))
    pal.setColor(QPalette.Link, c(ACCENT))
    pal.setColor(QPalette.PlaceholderText, c("#5f6779"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c("#5c6475"))
    app.setPalette(pal)
    f = QFont()
    if sys.platform == "win32":
        f.setFamily("Segoe UI")
    elif sys.platform == "darwin":
        f.setFamily(".AppleSystemUIFont")
    else:
        f.setFamilies(["Inter", "Noto Sans", "Ubuntu", "DejaVu Sans"])
    f.setPointSizeF(10)
    app.setFont(f)
    try:
        extra = _indicator_qss()
    except Exception:  # noqa: BLE001 - never block startup over a checkbox tick
        extra = ""
    app.setStyleSheet(STYLESHEET + extra)
