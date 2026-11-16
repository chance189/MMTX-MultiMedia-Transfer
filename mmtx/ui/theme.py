"""Dark theme: Fusion style + palette + stylesheet."""

from __future__ import annotations

import os
import sys
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

BG = "#0e1014"
SURFACE = "#151820"
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

/* ---------- layout shell ---------- */
QWidget#Root, QWidget#Main {{ background: {BG}; }}
QFrame#Sidebar {{ background: #0a0c10; border-right: 1px solid #1a1e27; }}
QFrame#Sidebar QLabel {{ background: transparent; }}
QPushButton#Logo {{ background: transparent; border: none; padding: 0; }}
QLabel#Brand {{ color: {TEXT}; font-size: 15pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#BrandSub {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#SideCaps {{ color: #5f6880; font-size: 8pt; font-weight: 800; letter-spacing: 1.2px; padding: 0 4px; }}
QLabel#SideHint {{ color: #5f6880; font-size: 8.5pt; padding: 2px 4px; }}
QPushButton#Nav {{ background: transparent; border: none; border-radius: 9px; padding: 9px 12px; text-align: left;
                   color: #aab2c3; font-size: 10.5pt; font-weight: 600; }}
QPushButton#Nav:hover {{ background: #141821; color: {TEXT}; }}
QPushButton#Nav:checked {{ background: #18213a; color: #ffffff; }}
QFrame#CardTile, QFrame#DriveTile {{ background: #11141b; border: 1px solid #1d2230; border-radius: 11px; }}
QFrame#CardTile:hover, QFrame#DriveTile:hover {{ border-color: #2c3448; }}
QFrame#CardTile[active="true"] {{ border: 1px solid {ACCENT}; background: #141b2c; }}
QLabel#TileTitle {{ color: {TEXT}; font-weight: 700; font-size: 9.5pt; }}
QLabel#TileSub {{ color: {MUTED}; font-size: 8.3pt; }}
QLabel#TileCaps {{ color: #5f6880; font-size: 7.6pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#TileKind {{ color: #8fb0ff; font-size: 7.8pt; font-weight: 700; }}
QProgressBar#Cap {{ min-height: 5px; max-height: 5px; border-radius: 2px; background: #1c212c; }}
QProgressBar#Cap::chunk {{ border-radius: 2px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT}, stop:1 #3ec6c0); }}
QProgressBar#Cap[state="bad"]::chunk {{ background: {DANGER}; }}

/* ---------- pages ---------- */
QLabel#PageTitle {{ font-size: 19pt; font-weight: 800; color: {TEXT}; }}
QLabel#EmptyTitle {{ font-size: 15pt; font-weight: 800; color: {TEXT}; }}
QScrollArea#Days {{ background: transparent; border: none; }}
QWidget#DaysInner {{ background: transparent; }}
QFrame#Empty {{ background: transparent; border: 1px dashed #252b38; border-radius: 16px; }}
QFrame#Empty QLabel {{ background: transparent; }}
QFrame#DayCard {{ background: {SURFACE}; border: 1px solid #20242e; border-radius: 14px; }}
QFrame#DayCard:hover {{ border-color: #2a3142; }}
QFrame#DayCard[selected="true"] {{ border-color: #2f4473; background: #151a26; }}
QFrame#DayCard QLabel, QFrame#DayCard QCheckBox {{ background: transparent; }}
QLineEdit#TitleEdit {{ font-size: 11.5pt; font-weight: 700; background: #0f1219; border: 1px solid #262c39; padding: 5px 10px; }}
QLineEdit#TitleEdit:focus {{ border-color: {ACCENT}; }}
QLabel#Arrow {{ color: #4d566b; font-size: 12pt; }}
QLabel#Folder {{ color: #9fb6ff; font-family: "Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", monospace; font-size: 9.5pt; }}
QListWidget#ClipGrid {{ background: transparent; border: none; }}
QLabel#Pill {{ border-radius: 11px; padding: 4px 12px; font-size: 8.8pt; font-weight: 700; }}
QLabel#Pill[tone="info"] {{ background: rgba(79,179,217,0.13); color: {INFO}; }}
QLabel#Pill[tone="ok"] {{ background: rgba(52,195,143,0.14); color: {SUCCESS}; }}
QLabel#Pill[tone="warn"] {{ background: rgba(240,166,58,0.14); color: {WARN}; }}
QLabel#Pill[tone="bad"] {{ background: rgba(240,82,90,0.14); color: {DANGER}; }}
QLabel#Pill[tone="muted"] {{ background: rgba(138,147,166,0.12); color: {MUTED}; }}
QLabel#MoreBadge {{ background: rgba(91,140,255,0.14); color: #9fb6ff; border-radius: 9px; padding: 2px 10px;
                    font-size: 8.5pt; font-weight: 700; }}
QFrame#Transfer {{ background: #12151c; border: 1px solid #20242e; border-radius: 14px; }}

/* ---------- Easy mode: big, high contrast, color = what to do ---------- */
QWidget#EasyPage {{ background: #0b0d12; }}
QWidget#EasyPage QLabel {{ background: transparent; }}
QWidget#EasyHolder, QScrollArea#EasyScroll {{ background: #0b0d12; border: none; }}
QLabel#EasyBrand {{ color: #ffffff; font-size: 20pt; font-weight: 900; letter-spacing: 1px; }}
QPushButton#EasyTop {{ background: #1a1f2b; color: #ffffff; border: 2px solid #343c4f; border-radius: 14px;
                       padding: 10px 20px; font-size: 13pt; font-weight: 700; }}
QPushButton#EasyTop:hover {{ border-color: #ffffff; }}
QPushButton#EasyTab {{ background: #141821; color: #e6eaf2; border: 2px solid #343c4f; border-radius: 18px;
                       padding: 14px 30px; font-size: 16pt; font-weight: 800; }}
QPushButton#EasyTab:hover {{ border-color: #ffffff; }}
QPushButton#EasyTab:checked {{ background: #f2f4f8; color: #0b0d12; border-color: #f2f4f8; }}
QLabel#EasyTitle {{ color: #ffffff; font-size: 30pt; font-weight: 900; }}
QLabel#EasyTitle[tone="do"] {{ color: #FFD400; }}
QLabel#EasyTitle[tone="ok"] {{ color: #4ade80; }}
QLabel#EasyTitle[tone="warn"] {{ color: #FFD400; }}
QLabel#EasySub {{ color: #e6eaf2; font-size: 17pt; }}
QLabel#EasyEta {{ color: #ffffff; font-size: 17pt; font-weight: 800; }}
QLabel#EasyFoot {{ color: #cfd6e4; font-size: 13.5pt; }}
QLabel#EasyMore {{ color: #ffffff; font-size: 14pt; font-weight: 800; padding: 0 8px; }}
QFrame#HowTo {{ background: #141821; border: 2px solid #2e3546; border-radius: 18px; }}
QWidget#EasyPage QLabel#HowNum {{ background: #FFD400; color: #0b0d12; border-radius: 20px; font-size: 17pt; font-weight: 900; }}
QLabel#HowText {{ color: #ffffff; font-size: 16pt; }}
QPushButton#EasyPick {{ background: #FFD400; color: #0b0d12; border: none; border-radius: 34px;
                        padding: 18px 56px; font-size: 22pt; font-weight: 900; min-height: 32px; }}
QPushButton#EasyPick:hover {{ background: #ffe14d; }}
QPushButton#EasyGo {{ background: #22C55E; color: #ffffff; border: none; border-radius: 52px;
                      padding: 22px 120px; font-size: 40pt; font-weight: 900; letter-spacing: 6px; min-height: 60px; }}
QPushButton#EasyGo:hover {{ background: #34d673; }}
QPushButton#EasyGo:disabled, QPushButton#EasyPick:disabled {{ background: #262c38; color: #6b7488; }}
QPushButton#EasyLink {{ background: transparent; border: none; color: #8fb6ff; font-size: 14pt; font-weight: 700;
                        text-decoration: underline; padding: 6px 10px; }}
QPushButton#EasyLink:hover {{ color: #ffffff; }}
QWidget#EasyPage QLabel#EasyWarn {{ background: rgba(239,68,68,0.16); color: #ff6b6b; border: 2px solid #EF4444; border-radius: 16px;
                   padding: 12px 28px; font-size: 18pt; font-weight: 900; }}
QPushButton#EasyStop {{ background: transparent; color: #ff6b6b; border: 2px solid #EF4444; border-radius: 16px;
                        padding: 12px 44px; font-size: 15pt; font-weight: 800; }}
QPushButton#EasyStop:hover {{ background: rgba(239,68,68,0.15); }}
QPushButton#EasyStop:disabled {{ color: #6b7488; border-color: #343c4f; }}
QDialog#EasyDialog {{ background: #0f1219; }}
QDialog#EasyDialog QLabel {{ background: transparent; }}
QLabel#EasyDialogTitle {{ color: #ffffff; font-size: 26pt; font-weight: 900; }}
QLabel#EasyDialogTitle[tone="ok"] {{ color: #4ade80; }}
QLabel#EasyDialogTitle[tone="bad"] {{ color: #ff6b6b; }}
QLabel#EasyDialogTitle[tone="warn"] {{ color: #FFD400; }}
QLabel#EasyDialogText {{ color: #e6eaf2; font-size: 16pt; }}
QPushButton#EasyDialogYes {{ background: #22C55E; color: #ffffff; border: none; border-radius: 18px;
                             padding: 14px 32px; font-size: 16pt; font-weight: 900; }}
QPushButton#EasyDialogYes:hover {{ background: #34d673; }}
QPushButton#EasyDialogNo {{ background: #1a1f2b; color: #ffffff; border: 2px solid #343c4f; border-radius: 18px;
                            padding: 12px 30px; font-size: 15pt; font-weight: 700; }}
QPushButton#EasyDialogNo:hover {{ border-color: #ffffff; }}
QFrame#Bubble {{ background: #ffffff; border: none; border-radius: 20px; }}
QFrame#Bubble QLabel {{ background: transparent; }}
QLabel#BubbleCount {{ color: #5b6478; font-size: 11pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#BubbleTitle {{ color: #0b0d12; font-size: 20pt; font-weight: 900; }}
QLabel#BubbleText {{ color: #1f2532; font-size: 15pt; }}
QFrame#Bubble QPushButton#EasyLink {{ color: #3a5bd9; }}
QPushButton#EasySwitch {{ background: #FFD400; color: #0b0d12; border: none; border-radius: 10px;
                          padding: 10px 12px; text-align: left; font-size: 10.5pt; font-weight: 800; }}
QPushButton#EasySwitch:hover {{ background: #ffe14d; }}

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
