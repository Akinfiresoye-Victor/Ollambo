"""
Read-only terminal widget for showing live command output.

This is a QPlainTextEdit in read-only mode with a monospace font.
Call append_text(str) from any thread via its signal — it is thread-safe.
"""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit


class TerminalWidget(QPlainTextEdit):
    """Read-only text box that displays live output, like a mini terminal."""

    # Emit this signal from any thread to safely append text.
    append_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Consolas", 10))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setMaximumBlockCount(5000)  # Prevent unbounded memory use.

        # Style: dark background, light text.
        self.setStyleSheet(
            "QPlainTextEdit {"
            "  background-color: #1e1e1e;"
            "  color: #cccccc;"
            "  border: 1px solid #333;"
            "  padding: 4px;"
            "}"
        )

        # Connect the signal so appending from any thread is safe.
        self.append_requested.connect(self._do_append)

    def append_text(self, text: str) -> None:
        """Thread-safe: queue text to be appended on the main thread."""
        self.append_requested.emit(text)

    @Slot(str)
    def _do_append(self, text: str) -> None:
        """Actually append text (runs on the main/GUI thread)."""
        self.moveCursor(QTextCursor.MoveOperation.End)
        self.insertPlainText(text)
        # Auto-scroll to the bottom.
        self.moveCursor(QTextCursor.MoveOperation.End)

    def clear_output(self) -> None:
        """Clear all text."""
        self.clear()
