"""
Application assets management (app icons, branding pixmaps, and logos).
"""

from __future__ import annotations

import sys
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

_ROOT_DIR = (
    Path(sys._MEIPASS)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
    else Path(__file__).resolve().parent.parent
)


def get_icon_path() -> Path:
    """Return the primary application icon path (favicon.ico or Favicon.ico)."""
    for candidate in ("favicon.ico", "Favicon.ico", "icon.png"):
        p = _ROOT_DIR / candidate
        if p.exists():
            return p
    return _ROOT_DIR / "favicon.ico"


def get_app_icon() -> QIcon:
    """Return a QIcon instance for the application and windows."""
    path = get_icon_path()
    return QIcon(str(path)) if path.exists() else QIcon()


def get_app_pixmap(size: int = 32) -> QPixmap:
    """Return a smooth-scaled QPixmap of Ollambo's face for in-app UI."""
    png_path = _ROOT_DIR / "icon.png"
    src = png_path if png_path.exists() else get_icon_path()
    if not src.exists():
        return QPixmap()

    pix = QPixmap(str(src))
    if pix.isNull():
        return QPixmap()
    return pix.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
