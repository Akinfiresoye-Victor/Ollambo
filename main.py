"""
Ollambo — your local AI coding buddy.

This is the entry point.  Run with:   python main.py

It does three things:
  1. Load settings from disk.
  2. Create the Qt application and main window.
  3. Enter the event loop.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from core.storage import load_settings
from ui.main_window import MainWindow


def main() -> None:
    # Explicitly set AppUserModelID on Windows so the taskbar groups with custom icon
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ollambo.desktop.assistant")
        except Exception:
            pass

    settings = load_settings()

    app = QApplication(sys.argv)
    app.setApplicationName("Ollambo")
    app.setStyle("Fusion")  # Consistent look across Windows, macOS, Linux.

    from core.assets import get_app_icon
    app_icon = get_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)

    window = MainWindow(settings)
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
