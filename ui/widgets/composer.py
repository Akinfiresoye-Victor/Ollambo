from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QTextEdit, QToolButton, QVBoxLayout, QWidget

from ui.icons import icon
from ui.theme import set_variant
from ui.theme.tokens import DARK


class ComposerInput(QTextEdit):
    """Multiline input that sends on Enter and inserts a newline on Shift+Enter."""

    send_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Message Ollambo, or describe what you want to build")
        self.setMinimumHeight(32)
        self.setMaximumHeight(120)
        self.setTabChangesFocus(False)
        self.textChanged.connect(self._sync_height)
        self._sync_height()

    def _sync_height(self) -> None:
        doc_height = self.document().size().height()
        line_height = self.fontMetrics().lineSpacing()
        desired = min(120, max(32, int(doc_height + line_height * 0.5)))
        self.setMaximumHeight(min(120, desired))

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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._generating = False

        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(16, 8, 16, 8)
        outer_layout.setSpacing(8)

        self.card = QWidget(self)
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setObjectName("Composer")
        self.card.setProperty("role", "card")
        self.card.setMinimumHeight(56)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(8)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(8)

        self.btn_attach = QToolButton(self.card)
        self.btn_attach.setIcon(icon("plus", DARK.colors.text_secondary, 18))
        self.btn_attach.setToolTip("Attach an existing project or artifact")
        self.btn_attach.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_attach.setProperty("variant", "ghost")
        self.btn_attach.setFixedSize(32, 32)
        self.btn_attach.setCursor(Qt.CursorShape.PointingHandCursor)
        set_variant(self.btn_attach, "ghost")
        input_row.addWidget(self.btn_attach)

        self.input_edit = ComposerInput(self.card)
        self.input_edit.setObjectName("ComposerInput")
        self.input_edit.send_requested.connect(self.send_requested.emit)
        input_row.addWidget(self.input_edit, 1)

        self.send_button = QToolButton(self.card)
        self.send_button.setIcon(icon("arrow-up", DARK.colors.text_on_accent, 18))
        self.send_button.setToolTip("Send message")
        self.send_button.setFixedSize(32, 32)
        self.send_button.setProperty("variant", "primary")
        self.send_button.setObjectName("ComposerSendButton")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.clicked.connect(self._on_send_clicked)
        set_variant(self.send_button, "primary")
        input_row.addWidget(self.send_button)

        card_layout.addLayout(input_row)
        outer_layout.addWidget(self.card, 1)
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
