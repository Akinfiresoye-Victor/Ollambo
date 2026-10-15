from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QTextEdit, QToolButton, QWidget

from ui.icons import icon
from ui.theme import set_variant
from ui.theme.tokens import DARK


class ComposerInput(QTextEdit):
    """Multiline input that sends on Enter and inserts a newline on Shift+Enter."""

    send_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Type a message or coding request…")
        self.setMinimumHeight(36)
        self.setMaximumHeight(120)
        self.setTabChangesFocus(False)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Composer(QWidget):
    """Rounded chat composer with inline send/stop state and attach affordance."""

    send_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._generating = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self.btn_attach = QToolButton(self)
        self.btn_attach.setIcon(icon("plus", DARK.colors.text_secondary, 18))
        self.btn_attach.setToolTip("Attach an existing project or artifact")
        self.btn_attach.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_attach.setProperty("variant", "ghost")
        self.btn_attach.setFixedSize(32, 32)
        set_variant(self.btn_attach, "ghost")
        layout.addWidget(self.btn_attach)

        self.input_edit = ComposerInput(self)
        self.input_edit.setObjectName("ComposerInput")
        self.input_edit.send_requested.connect(self.send_requested.emit)
        layout.addWidget(self.input_edit, 1)

        self.send_button = QToolButton(self)
        self.send_button.setIcon(icon("arrow-up", DARK.colors.text_on_accent, 18))
        self.send_button.setToolTip("Send message")
        self.send_button.setFixedSize(32, 32)
        self.send_button.setProperty("variant", "primary")
        self.send_button.setObjectName("ComposerSendButton")
        self.send_button.clicked.connect(self._on_send_clicked)
        set_variant(self.send_button, "primary")
        layout.addWidget(self.send_button)

        self.setProperty("role", "card")
        self.setObjectName("Composer")
        self.setMinimumHeight(56)

    def _on_send_clicked(self) -> None:
        if self._generating:
            self.stop_requested.emit()
        else:
            self.send_requested.emit()

    def set_generating(self, generating: bool) -> None:
        self._generating = generating
        if generating:
            self.send_button.setIcon(icon("square", DARK.colors.text_on_accent, 16))
            self.send_button.setToolTip("Stop generation")
        else:
            self.send_button.setIcon(icon("arrow-up", DARK.colors.text_on_accent, 18))
            self.send_button.setToolTip("Send message")

    def set_send_enabled(self, enabled: bool) -> None:
        self.send_button.setEnabled(enabled)

    def text(self) -> str:
        return self.input_edit.toPlainText().strip()

    def clear(self) -> None:
        self.input_edit.clear()

    def set_text(self, value: str) -> None:
        self.input_edit.setPlainText(value)

    def set_placeholder(self, text: str) -> None:
        self.input_edit.setPlaceholderText(text)
