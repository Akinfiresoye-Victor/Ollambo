"""Cached inline SVG icons and a visibility-aware activity spinner."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ui.theme.tokens import DARK


_ICON_PATHS = {
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "arrow-up": '<path d="M12 19V5m-7 7 7-7 7 7"/>',
    "square": '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="m19.4 15 .1.1 1.4 1.1-1.4 2.4-1.7-.7-.2.1a8 8 0 0 1-1.8 1l-.2.1-.3 1.9h-2.8l-.3-1.9-.2-.1a8 8 0 0 1-1.8-1l-.2-.1-1.7.7-1.4-2.4 1.4-1.1.1-.2a8 8 0 0 1 0-2.1l-.1-.2-1.4-1.1 1.4-2.4 1.7.7.2-.1a8 8 0 0 1 1.8-1l.2-.1.3-1.9h2.8l.3 1.9.2.1a8 8 0 0 1 1.8 1l.2.1 1.7-.7 1.4 2.4-1.4 1.1-.1.2a8 8 0 0 1 0 2.1Z"/>',
    "trash": '<path d="M3 6h18M8 6V4h8v2m3 0-1 14H6L5 6m4 4v6m6-6v6"/>',
    "pencil": '<path d="m15 5 4 4M4 20l4.5-1 10-10a2.1 2.1 0 0 0-3-3l-10 10L4 20Z"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "search": '<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4 4"/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z"/>',
    "file": '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10Z"/><path d="M13 3v7h7"/>',
    "file-code": '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10Z"/><path d="M13 3v7h7m-9 2-3 2 3 2m4-4 3 2-3 2"/>',
    "refresh": '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 9a7 7 0 0 1 11.6-2L20 12M4 12l2.9 5a7 7 0 0 0 11.6-2"/>',
    "bolt": '<path d="m13 2-9 12h7l-1 8 10-13h-7l1-7Z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18"/>',
    "message": '<path d="M21 11.5a8.5 8.5 0 0 1-9 8.5 9.7 9.7 0 0 1-4-.8L3 21l1.5-4A8.5 8.5 0 1 1 21 11.5Z"/>',
    "list-checks": '<path d="m3 6 2 2 4-4M3 12l2 2 4-4M3 18l2 2 4-4M12 7h9m-9 6h9m-9 6h9"/>',
    "package": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 8 9 5 9-5v9l-9 5-9-5V8Zm9 5v9"/>',
    "flask": '<path d="M9 3h6m-5 0v7l-5.5 9a1.3 1.3 0 0 0 1.1 2h12.8a1.3 1.3 0 0 0 1.1-2L14 10V3M8 16h8"/>',
    "more-horizontal": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "x": '<path d="m18 6-12 12M6 6l12 12"/>',
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 20h16"/>',
    "external-link": '<path d="M14 4h6v6m0-6-9 9"/><path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5"/>',
    "alert-triangle": '<path d="m10.3 4.3-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-2.7l-8-14a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/>',
    "cpu": '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9zM9 1v4m6-4v4m-6 14v4m6-4v4M1 9h4m-4 6h4m14-6h4m-4 6h4"/>',
    "sidebar-left": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    "panel-right": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    "loader": '<path d="M12 3a9 9 0 1 0 9 9"/>',
}


@lru_cache(maxsize=512)
def _render_icon(name: str, color_name: str, size: int, ratio: float) -> QIcon:
    path = _ICON_PATHS[name]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color_name}" stroke-width="1.75" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f"{path}</svg>"
    )
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        raise RuntimeError(f"Could not render SVG icon: {name}")

    pixel_size = max(1, round(size * ratio))
    pixmap = QPixmap(pixel_size, pixel_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


def icon(name: str, color: Optional[str] = None, size: int = 18) -> QIcon:
    """Return a tinted, cached icon, rendered for the current screen density."""
    if name not in _ICON_PATHS:
        raise ValueError(f"Unknown icon: {name}")
    if size < 1:
        raise ValueError("Icon size must be positive")

    color_value = QColor(color or DARK.colors.text_secondary)
    if not color_value.isValid():
        raise ValueError(f"Invalid icon color: {color}")
    color_name = color_value.name()

    ratio = 1.0
    app = QApplication.instance()
    if isinstance(app, QApplication) and app.primaryScreen() is not None:
        ratio = app.primaryScreen().devicePixelRatio()
    return _render_icon(name, color_name, size, ratio)


class Spinner(QWidget):
    """Rotating loader icon that pauses while hidden. Low-power mode slows it to 400ms steps."""

    def __init__(
        self,
        size: int = 18,
        color: Optional[str] = None,
        low_power_mode: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._size = max(1, size)
        self._color = color or DARK.colors.text_secondary
        self._angle = 0
        self._low_power_mode = low_power_mode
        self._timer = QTimer(self)
        self._timer.setInterval(62 if not low_power_mode else 400)
        self._timer.timeout.connect(self._rotate)
        self.setFixedSize(self._size, self._size)

    @property
    def timer(self) -> QTimer:
        return self._timer

    def set_low_power_mode(self, enabled: bool) -> None:
        self._low_power_mode = enabled
        self._timer.setInterval(400 if enabled else 62)
        self._sync_timer()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_timer()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _sync_timer(self) -> None:
        if self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _rotate(self) -> None:
        self._angle = (self._angle + (90 if self._low_power_mode else 22.5)) % 360
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._angle)
        side = self._size
        icon("loader", self._color, side).paint(
            painter,
            QRect(-side // 2, -side // 2, side, side),
        )
        painter.end()


class StatusIcon(QLabel):
    """A fixed-size status glyph that can animate as a spinner or show a static check/x/error."""

    def __init__(
        self,
        size: int = 16,
        color: Optional[str] = None,
        low_power_mode: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._size = max(1, size)
        self._color = color or DARK.colors.text_secondary
        self._spinner = Spinner(self._size, self._color, low_power_mode=low_power_mode, parent=self)
        self._spinner.setVisible(False)
        self.setFixedSize(self._size, self._size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    @property
    def spinner(self) -> Spinner:
        return self._spinner

    def set_color(self, color: str) -> None:
        self._color = color
        self._spinner._color = color
        if self._spinner.isVisible():
            self._spinner.update()
        elif self.pixmap() is not None and not self.pixmap().isNull():
            last_name = getattr(self, "_last_static_name", "message")
            self.setPixmap(icon(last_name, color, self._size).pixmap(self._size, self._size))

    def set_low_power_mode(self, enabled: bool) -> None:
        self._spinner.set_low_power_mode(enabled)

    def set_state(self, state: str) -> None:
        if state == "loading":
            self._spinner.setVisible(True)
            self._spinner.show()
            self.clear()
            return
        self._spinner.hide()
        self._spinner.setVisible(False)
        if state == "idle":
            self.clear()
            return
        icon_name = {
            "ok": "check",
            "failed": "alert-triangle",
            "warning": "alert-triangle",
            "message": "message",
            "search": "search",
            "read": "file",
            "fetch": "external-link",
            "check": "check",
        }.get(state, state)
        self._last_static_name = icon_name
        pix = icon(icon_name, self._color, self._size).pixmap(self._size, self._size)
        self.setPixmap(pix)
