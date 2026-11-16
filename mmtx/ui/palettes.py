"""Easy mode color palettes.

Every Easy color comes from the current palette `P` (read at paint time), so switching is instant.
The status colors mean the same thing in every palette:

    red     a step you still have to do (the flashing button, the current step in the tracker)
    blue    copying right now (tiles pulse)
    orange  almost done (tiles flash)
    green   done (big check)
    grey    waiting / not part of this transfer

The base palettes are deliberately neutral so those five carry the meaning.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Palette:
    key: str
    name: str
    blurb: str
    dark: bool
    # page
    bg_top: str
    bg_bottom: str
    glows: tuple = ()  # (fx, fy, fradius, "r,g,b", alpha 0..255)
    ink: str = "255,255,255"  # glass overlays and hairlines are this color at low alpha
    hero_bg: str = "rgba(255,255,255,0.035)"
    hero_border: str = "1px solid rgba(255,255,255,0.08)"
    # text
    text: str = "#f4f4f5"
    sub: str = "#d4d4d8"
    muted: str = "#a1a1aa"
    faint: str = "#52525b"
    link: str = "#e4e4e7"
    eyebrow: str = "#a1a1aa"
    # solid (non-flashing) buttons in popups
    btn_bg: str = "#f4f4f5"
    btn_fg: str = "#18181b"
    btn_hover: str = "#ffffff"
    dialog_bg: str = "#18181b"
    # illustrations
    art_folders: tuple = ("#3f3f46", "#52525b", "#71717a")
    art_body: tuple = ("#27272a", "#18181b")
    art_line: str = "#3f3f46"
    art_card: tuple = ("#52525b", "#27272a")
    art_slot: str = "#09090b"
    # tracker / rings / chips
    line: str = "#2f2f35"
    step_fill: str = "#18181b"
    step_line: str = "#3f3f46"
    ring_track: str = "#27272a"
    chip_bg: str = "rgba(9,9,11,0.88)"
    placeholder: tuple = ("#27272a", "#18181b")
    # status colors
    todo_hi: str = "#ef4444"
    todo: str = "#dc2626"
    todo_lo: str = "#7f1d1d"
    done_hi: str = "#22c55e"
    done: str = "#16a34a"
    on_done: str = "#052e16"
    busy: str = "#38bdf8"
    busy_lo: str = "#0369a1"
    almost: str = "#fb923c"
    almost_lo: str = "#9a3412"
    wait: str = "#71717a"
    bad: str = "#f87171"
    warn: str = "#fbbf24"
    warn_bg: str = "rgba(251,191,36,0.12)"
    warn_line: str = "rgba(251,191,36,0.35)"
    warn_text: str = "#fcd34d"


PALETTES: dict[str, Palette] = {}


def _add(p: Palette) -> None:
    PALETTES[p.key] = p


_add(Palette(
    key="graphite", name="Graphite", dark=True,
    blurb="Neutral charcoal, like an editing app. Only the status colors stand out.",
    bg_top="#16171a", bg_bottom="#0d0e10",
    glows=((0.12, 0.04, 0.55, "148,163,184", 26), (0.95, 0.96, 0.5, "113,113,122", 18)),
))

_add(Palette(
    key="midnight", name="Midnight", dark=True,
    blurb="Deep navy with cool blue accents. Close to before, without the purple.",
    bg_top="#0b1528", bg_bottom="#070d1a",
    glows=((0.12, 0.04, 0.55, "37,99,235", 40), (0.95, 0.96, 0.5, "14,116,144", 26)),
    text="#f1f5f9", sub="#cbd5e1", muted="#94a3b8", faint="#475569", link="#93c5fd", eyebrow="#93c5fd",
    btn_bg="#2563eb", btn_fg="#ffffff", btn_hover="#3b82f6", dialog_bg="#0f172a",
    art_folders=("#1e3a8a", "#1d4ed8", "#2563eb"), art_body=("#1e293b", "#0f172a"), art_line="#334155",
    art_card=("#2563eb", "#1e3a8a"), art_slot="#020617",
    line="#1e2a44", step_fill="#0f1a30", step_line="#334155", ring_track="#1e293b",
    chip_bg="rgba(2,6,23,0.88)", placeholder=("#1e293b", "#0f172a"),
))

_add(Palette(
    key="daylight", name="Daylight", dark=False,
    blurb="Light background with dark text. Easiest to read in a bright room.",
    bg_top="#f8fafc", bg_bottom="#e9eef4",
    glows=((0.12, 0.04, 0.55, "59,130,246", 16), (0.95, 0.96, 0.5, "34,197,94", 10)),
    ink="15,23,42", hero_bg="rgba(255,255,255,0.88)", hero_border="1px solid rgba(15,23,42,0.10)",
    text="#0f172a", sub="#334155", muted="#64748b", faint="#94a3b8", link="#1d4ed8", eyebrow="#475569",
    btn_bg="#0f172a", btn_fg="#ffffff", btn_hover="#1e293b", dialog_bg="#ffffff",
    art_folders=("#cbd5e1", "#94a3b8", "#64748b"), art_body=("#e2e8f0", "#cbd5e1"), art_line="#94a3b8",
    art_card=("#334155", "#0f172a"), art_slot="#475569",
    line="#d5dde7", step_fill="#ffffff", step_line="#cbd5e1", ring_track="#e2e8f0",
    chip_bg="rgba(15,23,42,0.80)", placeholder=("#e2e8f0", "#cbd5e1"),
    done_hi="#16a34a", done="#15803d", on_done="#ffffff",
    busy="#0284c7", busy_lo="#7dd3fc", almost="#ea580c", almost_lo="#fdba74", wait="#94a3b8",
    bad="#dc2626", warn="#d97706", warn_bg="rgba(245,158,11,0.14)", warn_line="rgba(217,119,6,0.45)",
    warn_text="#92400e",
))

_add(Palette(
    key="contrast", name="High contrast", dark=True,
    blurb="Black and white with bright status colors and bold outlines.",
    bg_top="#000000", bg_bottom="#000000", glows=(),
    hero_bg="#000000", hero_border="2px solid #ffffff",
    text="#ffffff", sub="#ffffff", muted="#e5e5e5", faint="#737373", link="#ffffff", eyebrow="#ffffff",
    btn_bg="#ffffff", btn_fg="#000000", btn_hover="#e5e5e5", dialog_bg="#000000",
    art_folders=("#525252", "#a3a3a3", "#e5e5e5"), art_body=("#262626", "#000000"), art_line="#ffffff",
    art_card=("#e5e5e5", "#a3a3a3"), art_slot="#000000",
    line="#525252", step_fill="#000000", step_line="#ffffff", ring_track="#404040",
    chip_bg="rgba(0,0,0,0.92)", placeholder=("#262626", "#0a0a0a"),
    on_done="#000000", busy="#38bdf8", almost="#fb923c", wait="#a3a3a3",
))

DEFAULT = "graphite"


class _Current:
    """The palette in use. Attributes mirror Palette; swap with set_palette()."""

    def __init__(self) -> None:
        self.apply(PALETTES[DEFAULT])

    def apply(self, pal: Palette) -> None:
        self.__dict__.update(asdict(pal))


P = _Current()


def set_palette(key: str) -> str:
    pal = PALETTES.get(key) or PALETTES[DEFAULT]
    P.apply(pal)
    return pal.key


def ink(alpha: int):
    """QColor of the palette's ink (white on dark palettes, near-black on light) at an alpha 0..255."""
    from PySide6.QtGui import QColor

    r, g, b = (int(x) for x in P.ink.split(","))
    return QColor(r, g, b, alpha)


def easy_qss() -> str:
    """Style sheet for the Easy page and its popups (set on the page, so it cascades to them)."""
    i = P.ink
    return f"""
QWidget#EasyPage {{ background: transparent; }}
QWidget#EasyPage QLabel {{ background: transparent; }}
QWidget#EasyHolder, QScrollArea#EasyScroll, QScrollArea#EasyScroll > QWidget {{ background: transparent; border: none; }}
QFrame#EasyHero {{ background: {P.hero_bg}; border: {P.hero_border}; border-radius: 28px; }}
QLabel#EasyBrand {{ color: {P.text}; font-size: 18pt; font-weight: 900; letter-spacing: 1px; }}
QPushButton#EasyTop {{ background: rgba({i},0.05); color: {P.text}; border: 1px solid rgba({i},0.14);
                       border-radius: 12px; padding: 9px 18px; font-size: 12pt; font-weight: 700; }}
QPushButton#EasyTop:hover {{ background: rgba({i},0.10); }}
QPushButton#EasyTab {{ background: transparent; color: {P.sub}; border: 1px solid rgba({i},0.14);
                       border-radius: 14px; padding: 11px 24px; font-size: 13.5pt; font-weight: 800; }}
QPushButton#EasyTab:hover {{ background: rgba({i},0.06); color: {P.text}; }}
QPushButton#EasyTab:checked {{ background: rgba({i},0.12); color: {P.text}; border-color: rgba({i},0.32); }}
QLabel#EasyEyebrow {{ color: {P.eyebrow}; font-size: 11pt; font-weight: 800; letter-spacing: 1.5px; }}
QLabel#EasyTitle {{ color: {P.text}; font-size: 27pt; font-weight: 900; }}
QLabel#EasyTitle[tone="ok"] {{ color: {P.done_hi}; }}
QLabel#EasyTitle[tone="warn"] {{ color: {P.warn}; }}
QLabel#EasySub {{ color: {P.sub}; font-size: 15pt; }}
QLabel#EasySuggest {{ color: {P.text}; font-size: 14pt; background: rgba({i},0.06);
                      border: 1px solid rgba({i},0.18); border-radius: 14px; padding: 12px 22px; }}
QFrame#HowTile {{ background: rgba({i},0.04); border: 1px solid rgba({i},0.10); border-radius: 18px; }}
QWidget#EasyPage QLabel#HowNum {{ background: rgba({i},0.12); color: {P.text}; border-radius: 15px;
                                  font-size: 12pt; font-weight: 900; }}
QLabel#HowHead {{ color: {P.text}; font-size: 14pt; font-weight: 800; }}
QLabel#HowText {{ color: {P.sub}; font-size: 12.5pt; }}
QLabel#HowChevron {{ color: {P.faint}; font-size: 26pt; }}
QPushButton#EasyPrimary {{ background: transparent; color: #ffffff; border: none; border-radius: 30px;
                           padding: 16px 48px; font-size: 18pt; font-weight: 900; }}
QPushButton#EasyGo {{ background: transparent; color: #ffffff; border: none; border-radius: 40px;
                      padding: 22px 80px; font-size: 24pt; font-weight: 900; letter-spacing: 1px; }}
QPushButton#EasyGo:disabled, QPushButton#EasyPrimary:disabled {{ background: transparent; color: {P.faint}; }}
QPushButton#EasyGhost {{ background: transparent; color: {P.text}; border: 1px solid rgba({i},0.22);
                         border-radius: 14px; padding: 10px 22px; font-size: 13pt; font-weight: 700; }}
QPushButton#EasyGhost:hover {{ background: rgba({i},0.08); }}
QPushButton#EasyGhost:disabled {{ color: {P.faint}; border-color: rgba({i},0.10); }}
QPushButton#EasyLink {{ background: transparent; border: none; color: {P.link}; font-size: 12.5pt; font-weight: 700;
                        text-decoration: underline; padding: 4px 8px; }}
QPushButton#EasyLink:hover {{ color: {P.text}; }}
QPushButton#EasyLink:disabled {{ color: {P.faint}; }}
QLabel#EasyFoot {{ color: {P.muted}; font-size: 12.5pt; }}
QWidget#EasyPage QLabel#EasyNote {{ background: {P.warn_bg}; color: {P.warn_text}; border: 1px solid {P.warn_line};
                                    border-radius: 14px; padding: 9px 22px; font-size: 13.5pt; font-weight: 800; }}
QFrame#NowCard {{ background: rgba({i},0.04); border: 1px solid rgba({i},0.10); border-radius: 18px; }}
QLabel#NowName {{ color: {P.text}; font-size: 13.5pt; font-weight: 800; }}
QLabel#NowEta {{ color: {P.sub}; font-size: 12.5pt; }}
QPushButton#EasyStop {{ background: transparent; color: {P.sub}; border: 1px solid rgba({i},0.22);
                        border-radius: 14px; padding: 10px 40px; font-size: 13pt; font-weight: 700; }}
QPushButton#EasyStop:hover {{ border-color: {P.bad}; color: {P.bad}; }}
QPushButton#EasyStop:disabled {{ color: {P.faint}; border-color: rgba({i},0.08); }}
QPushButton#PickTool {{ background: rgba({i},0.05); color: {P.text}; border: 1px solid rgba({i},0.18);
                        border-radius: 12px; padding: 8px 18px; font-size: 12.5pt; font-weight: 700; }}
QPushButton#PickTool:hover {{ background: rgba({i},0.10); }}
QLabel#PickCount {{ color: {P.text}; font-size: 14pt; font-weight: 800; }}
QLabel#PickDay {{ color: {P.text}; font-size: 15pt; font-weight: 900; }}
QLabel#PickDaySub {{ color: {P.muted}; font-size: 12pt; }}
QFrame#PickDelete {{ background: rgba({i},0.04); border: 1px solid rgba({i},0.12); border-radius: 16px; }}
QFrame#PickDelete[on="true"] {{ background: rgba(239,68,68,0.10); border: 2px solid {P.todo}; }}
QLabel#PickDeleteHead {{ color: {P.text}; font-size: 14pt; font-weight: 800; }}
QLabel#PickDeleteText {{ color: {P.sub}; font-size: 12pt; }}
QDialog#EasyDialog {{ background: {P.dialog_bg}; }}
QDialog#EasyDialog QLabel {{ background: transparent; }}
QLabel#EasyDialogTitle {{ color: {P.text}; font-size: 22pt; font-weight: 900; }}
QLabel#EasyDialogTitle[tone="ok"] {{ color: {P.done_hi}; }}
QLabel#EasyDialogTitle[tone="bad"] {{ color: {P.bad}; }}
QLabel#EasyDialogTitle[tone="warn"] {{ color: {P.warn}; }}
QLabel#EasyDialogText {{ color: {P.sub}; font-size: 14pt; }}
QPushButton#EasyDialogYes {{ background: {P.btn_bg}; color: {P.btn_fg}; border: none; border-radius: 14px;
                             padding: 12px 30px; font-size: 14pt; font-weight: 800; }}
QPushButton#EasyDialogYes:hover {{ background: {P.btn_hover}; }}
QPushButton#EasyDialogNo {{ background: rgba({i},0.05); color: {P.text}; border: 1px solid rgba({i},0.18);
                            border-radius: 14px; padding: 11px 26px; font-size: 13pt; font-weight: 700; }}
QPushButton#EasyDialogNo:hover {{ background: rgba({i},0.10); }}
QPushButton#EasyDetails {{ background: transparent; border: none; color: {P.muted}; font-size: 11.5pt;
                           text-decoration: underline; }}
QPlainTextEdit#EasyDetailsText {{ background: rgba({i},0.05); color: {P.sub}; border: 1px solid rgba({i},0.12);
                                  border-radius: 10px; font-family: monospace; }}
QScrollArea#HelpScroll, QWidget#HelpBody {{ background: transparent; border: none; }}
QFrame#HelpItem {{ background: rgba({i},0.04); border: 1px solid rgba({i},0.10); border-radius: 16px; }}
QFrame#HelpItem QLabel {{ background: transparent; }}
QPushButton#HelpQ {{ background: transparent; border: none; color: {P.text}; text-align: left; font-size: 14pt;
                     font-weight: 800; padding: 6px 0; }}
QPushButton#HelpQ:hover {{ color: {P.link}; }}
QLabel#HelpA {{ color: {P.sub}; font-size: 13pt; }}
QFrame#Bubble {{ background: #ffffff; border: none; border-radius: 18px; }}
QFrame#Bubble QLabel {{ background: transparent; }}
QLabel#BubbleCount {{ color: #475569; font-size: 10.5pt; font-weight: 800; letter-spacing: 1px; }}
QLabel#BubbleTitle {{ color: #0f172a; font-size: 17pt; font-weight: 900; }}
QLabel#BubbleText {{ color: #334155; font-size: 13pt; }}
QPushButton#BubbleLink {{ background: transparent; border: none; color: #64748b; font-size: 11.5pt; font-weight: 700; }}
QPushButton#BubbleLink:hover {{ color: #0f172a; }}
QPushButton#BubbleBack {{ background: transparent; color: #334155; border: 1px solid #cbd5e1; border-radius: 12px;
                          padding: 8px 18px; font-size: 12pt; font-weight: 700; }}
QPushButton#BubbleNext {{ background: #0f172a; color: #ffffff; border: none; border-radius: 12px; padding: 9px 22px;
                          font-size: 12pt; font-weight: 800; }}
QPushButton#BubbleNext:hover {{ background: #1e293b; }}
QPushButton#SwatchCard {{ background: rgba({i},0.04); border: 2px solid rgba({i},0.12); border-radius: 18px;
                          text-align: left; padding: 0; }}
QPushButton#SwatchCard:hover {{ border-color: rgba({i},0.40); }}
QPushButton#SwatchCard:checked {{ border: 3px solid {P.done_hi}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px; }}
QScrollBar::handle:vertical {{ background: rgba({i},0.22); border-radius: 4px; min-height: 40px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""
