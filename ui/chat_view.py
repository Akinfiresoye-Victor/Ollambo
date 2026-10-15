"""Per-conversation chat transcript widget.

One ChatView per open conversation. Switching chats only changes which view
is visible — this widget is not destroyed, and its stream timer keeps running.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ui.icons import icon
from ui.message_widget import ChatMessageWidget

_LAZY_PAGE = 40


class ChatView(QWidget):
    """Scrollable history for a single conversation."""

    retry_clicked = Signal()
    load_earlier_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.streaming_widget: ChatMessageWidget | None = None
        self._pending_stream_dirty = False
        self._pending_stream_text = ""
        self._user_scrolled_away = False
        self._lazy_load_offset = 0
        self._all_messages: list[dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        if sb := self.scroll_area.verticalScrollBar():
            sb.valueChanged.connect(self._on_scroll_value_changed)

        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.addStretch()
        self.scroll_area.setWidget(self.history_container)
        layout.addWidget(self.scroll_area)

        self._stream_flush_timer = QTimer(self)
        self._stream_flush_timer.setInterval(100)
        self._stream_flush_timer.timeout.connect(self._flush_stream_ui)

        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.setInterval(50)
        self._scroll_timer.timeout.connect(self._do_scroll_to_bottom)

    # ── Insert helpers ────────────────────────────────────────────────

    def _insert_before_stretch(self, widget: QWidget) -> None:
        self.history_layout.insertWidget(self.history_layout.count() - 1, widget)

    def add_user(self, text: str, attachments: list[dict[str, Any]] | None = None) -> ChatMessageWidget:
        w = ChatMessageWidget("user", text, attachments=attachments or None)
        self._insert_before_stretch(w)
        return w

    def add_assistant(self, text: str = "") -> ChatMessageWidget:
        w = ChatMessageWidget("assistant", text)
        self._insert_before_stretch(w)
        return w

    def ensure_assistant_widget(self, initial: str = "") -> ChatMessageWidget:
        if self.streaming_widget is None:
            self.streaming_widget = self.add_assistant(initial)
        elif initial:
            self.streaming_widget.set_content(initial)
        return self.streaming_widget

    def add_notice(self, markdown_text: str) -> QLabel:
        notice = QLabel(markdown_text)
        notice.setObjectName("notice_row")
        notice.setWordWrap(True)
        notice.setTextFormat(Qt.TextFormat.RichText)
        notice.setProperty("role", "muted")
        notice.setText(markdown_text)
        self._insert_before_stretch(notice)
        return notice

    def add_widget(self, widget: QWidget) -> None:
        self._insert_before_stretch(widget)

    # ── Streaming ─────────────────────────────────────────────────────

    def mark_stream_dirty(self, text: str) -> None:
        self._pending_stream_text = text
        self._pending_stream_dirty = True
        if not self._stream_flush_timer.isActive():
            self._stream_flush_timer.start()

    def finish_stream(self, text: str, stopped: bool = False) -> None:
        self._stream_flush_timer.stop()
        self._pending_stream_dirty = False
        self._pending_stream_text = text
        if self.streaming_widget:
            self.streaming_widget.set_content(self._display_content(text, stopped), is_final=True)
        self.streaming_widget = None

    def discard_stream_widget(self) -> None:
        self._stream_flush_timer.stop()
        self._pending_stream_dirty = False
        self._pending_stream_text = ""
        if self.streaming_widget is None:
            return
        self.history_layout.removeWidget(self.streaming_widget)
        self.streaming_widget.deleteLater()
        self.streaming_widget = None

    def stop_stream_timer(self) -> None:
        if self._stream_flush_timer.isActive():
            self._flush_stream_ui()
            self._stream_flush_timer.stop()

    def _flush_stream_ui(self) -> None:
        if not self._pending_stream_dirty:
            return
        self._pending_stream_dirty = False
        if self.streaming_widget:
            self.streaming_widget.set_content(self._pending_stream_text, is_final=False)
        self.scroll_to_bottom()

    # ── Retry (never targets notice rows) ─────────────────────────────

    def add_retry_button(self) -> None:
        self.remove_retry_button()
        retry_row = QWidget()
        retry_row.setObjectName("retry_row")
        retry_layout = QHBoxLayout(retry_row)
        retry_layout.setContentsMargins(0, 2, 0, 6)
        retry_layout.setSpacing(8)

        btn_retry = QPushButton("Retry request")
        btn_retry.setIcon(icon("refresh"))
        btn_retry.setToolTip("Re-run the last prompt")
        btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_retry.setProperty("variant", "ghost")
        btn_retry.clicked.connect(self.retry_clicked.emit)
        retry_layout.addWidget(btn_retry)
        retry_layout.addStretch()
        self._insert_before_stretch(retry_row)

    def remove_retry_button(self) -> None:
        for i in range(self.history_layout.count() - 1, -1, -1):
            item = self.history_layout.itemAt(i)
            if item and item.widget() and item.widget().objectName() == "retry_row":
                w = self.history_layout.takeAt(i).widget()
                if w:
                    w.deleteLater()
                return

    def pop_last_message_widget(self, role: str) -> bool:
        """Remove the last ChatMessageWidget of *role*. Skips notices and buttons."""
        for i in range(self.history_layout.count() - 1, -1, -1):
            item = self.history_layout.itemAt(i)
            widget = item.widget() if item else None
            if widget is None:
                continue
            name = widget.objectName()
            if name in ("retry_row", "notice_row", "load_earlier_btn"):
                continue
            if isinstance(widget, ChatMessageWidget) and widget.role == role:
                self.history_layout.takeAt(i)
                widget.deleteLater()
                return True
        return False

    # ── Rebuild / clear ───────────────────────────────────────────────

    def clear(self) -> None:
        self.remove_retry_button()
        self._remove_load_earlier_button()
        while self.history_layout.count() > 1:
            item = self.history_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self.streaming_widget = None
        self._pending_stream_dirty = False
        self._pending_stream_text = ""
        self._lazy_load_offset = 0
        self._all_messages = []

    def rebuild(self, messages: list[dict[str, Any]], stream_text: str = "") -> None:
        """Rebuild widgets from session.messages. Used when a dropped view is reopened."""
        self.clear()
        self._all_messages = list(messages)
        if len(messages) > _LAZY_PAGE:
            self._lazy_load_offset = len(messages) - _LAZY_PAGE
            self._add_load_earlier_button()
            visible = messages[self._lazy_load_offset :]
        else:
            self._lazy_load_offset = 0
            visible = messages
        for msg in visible:
            self._insert_before_stretch(self._create_message_widget(msg))
        if stream_text:
            self.streaming_widget = self.add_assistant(stream_text)
        self.scroll_to_bottom(force=True)

    def update_message_model(self, messages: list[dict[str, Any]]) -> None:
        if self._lazy_load_offset:
            delta = len(messages) - len(self._all_messages)
            self._lazy_load_offset = max(
                0,
                min(len(messages), self._lazy_load_offset + delta),
            )
        self._all_messages = list(messages)

    def load_earlier(self) -> None:
        offset = self._lazy_load_offset
        if offset <= 0:
            self._remove_load_earlier_button()
            return
        new_offset = max(0, offset - _LAZY_PAGE)
        batch = self._all_messages[new_offset:offset]
        self._lazy_load_offset = new_offset
        self._remove_load_earlier_button()
        insert_pos = 0
        for msg in batch:
            self.history_layout.insertWidget(
                insert_pos,
                self._create_message_widget(msg),
            )
            insert_pos += 1
        if new_offset > 0:
            self._add_load_earlier_button()

    @staticmethod
    def _display_content(content: str, stopped: bool = False) -> str:
        return f"{content}\n\n*Stopped*" if stopped else content

    def _create_message_widget(self, msg: dict[str, Any]) -> ChatMessageWidget:
        content = self._display_content(
            str(msg.get("content", "")),
            bool(msg.get("stopped")),
        )
        return ChatMessageWidget(
            str(msg.get("role", "user")),
            content,
            attachments=msg.get("attachments") or None,
        )

    def _add_load_earlier_button(self) -> None:
        self._remove_load_earlier_button()
        btn = QPushButton("⬆ Load earlier messages")
        btn.setObjectName("load_earlier_btn")
        btn.setProperty("variant", "ghost")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(self.load_earlier_clicked.emit)
        self.history_layout.insertWidget(0, btn)

    def _remove_load_earlier_button(self) -> None:
        for i in range(self.history_layout.count()):
            item = self.history_layout.itemAt(i)
            if item and item.widget() and item.widget().objectName() == "load_earlier_btn":
                w = self.history_layout.takeAt(i).widget()
                if w:
                    w.deleteLater()
                return

    # ── Scroll stability ──────────────────────────────────────────────

    def _on_scroll_value_changed(self, value: int) -> None:
        if sb := self.scroll_area.verticalScrollBar():
            self._user_scrolled_away = (sb.maximum() - value) > 80

    def scroll_to_bottom(self, force: bool = False) -> None:
        if force:
            self._user_scrolled_away = False
        if not self._scroll_timer.isActive():
            self._scroll_timer.start()

    def _do_scroll_to_bottom(self) -> None:
        if self._user_scrolled_away:
            return
        if sb := self.scroll_area.verticalScrollBar():
            sb.setValue(sb.maximum())
