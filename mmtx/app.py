"""Entry point: python -m mmtx"""

from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .ui.main_window import MainWindow
from .ui.theme import apply_theme


def make_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        g = QLinearGradient(QPointF(0, 0), QPointF(size, size))
        g.setColorAt(0, QColor("#5b8cff"))
        g.setColorAt(1, QColor("#3ec6c0"))
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        p.fillPath(path, g)
        # two stacked arrows: card -> drive
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        s = size / 64.0
        arrow = QPainterPath()
        arrow.moveTo(20 * s, 14 * s)
        arrow.lineTo(44 * s, 14 * s)
        arrow.lineTo(44 * s, 30 * s)
        arrow.lineTo(52 * s, 30 * s)
        arrow.lineTo(32 * s, 50 * s)
        arrow.lineTo(12 * s, 30 * s)
        arrow.lineTo(20 * s, 30 * s)
        arrow.closeSubpath()
        p.drawPath(arrow)
        p.fillRect(QRectF(12 * s, 53 * s, 40 * s, 4 * s), QColor("white"))
        p.end()
        icon.addPixmap(pm)
    return icon


def main() -> int:
    if sys.platform == "win32":
        try:  # own taskbar icon instead of python.exe's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("apotheotech.mmtx")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    apply_theme(app)
    app.setWindowIcon(make_icon())
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
