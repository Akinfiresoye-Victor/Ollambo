from __future__ import annotations

from PySide6.QtCore import QPoint, QPropertyAnimation, QEasingCurve, Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class Toast(QWidget):
    """Small bottom-center transient status notification."""

    def __init__(self, parent: QWidget | None = None, *, message: str = "", duration: int = 2000) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.ToolTip)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._duration = duration
        self._label = QLabel(message)
        self._label.setObjectName("ToastLabel")
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setProperty("role", "muted")
        self.setContentsMargins(0, 0, 0, 0)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(12, 10, 12, 10)
        self._layout.addWidget(self._label)
        self.resize(self._label.sizeHint().width() + 24, 38)
        self._opacity = 0.0
        self._fade_in = None
        self._fade_out = None
        self.setWindowOpacity(0.0)

    def set_message(self, text: str) -> None:
        self._label.setText(text)
        self.resize(self._label.sizeHint().width() + 24, 38)

    def show_for(self, parent: QWidget | None = None, *, low_power: bool = False) -> None:
        if parent is not None:
            self.setParent(parent)
            self.raise_()
            rect = parent.rect()
            x = max(0, (rect.width() - self.width()) // 2)
            y = max(12, rect.height() - self.height() - 28)
            self.move(parent.mapToGlobal(QPoint(x, y)))
        else:
            app = QApplication.instance()
            if app is not None:
                screen = app.primaryScreen()
                if screen is not None:
                    geom = screen.availableGeometry()
                    x = (geom.width() - self.width()) // 2
                    y = geom.height() - self.height() - 36
                    self.move(x, y)
        self.show()
        if low_power:
            QTimer.singleShot(self._duration, self.hide)
            return
        self._fade_in = QPropertyAnimation(self, b"windowOpacity")
        self._fade_in.setDuration(180)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_in.start()
        self._fade_out = QPropertyAnimation(self, b"windowOpacity")
        self._fade_out.setDuration(220)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        QTimer.singleShot(self._duration, self._begin_fade_out)

    def _begin_fade_out(self) -> None:
        if self._fade_out is not None:
            self._fade_out.start()
            QTimer.singleShot(230, self.hide)


def show_toast(parent: QWidget | None, message: str, *, duration: int = 2000, low_power: bool = False) -> None:
    toast = Toast(parent, message=message, duration=duration)
    toast.show_for(parent, low_power=low_power)
    return toast
