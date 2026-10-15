from __future__ import annotations

import unittest
from math import ceil

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ui.chat_top_bar import ChatTopBar
from ui.message_widget import ChatMessageWidget
from ui.widgets.auto_text_view import AutoTextView


class MessageWidgetSizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = QApplication.instance()
        cls.app = app if isinstance(app, QApplication) else QApplication([])

    def test_auto_text_view_remeasures_after_width_changes(self) -> None:
        parent = QWidget()
        view = AutoTextView(parent)
        view.setPlainText("A line of text that wraps when its viewport becomes narrower.")
        view.resize(240, 80)
        parent.show()
        view.show()
        self.app.processEvents()

        self.assertGreaterEqual(view.viewport().width(), 50)
        self.assertEqual(
            view.height(),
            ceil(view.document().size().height()) + 2,
        )
        initial_height = view.height()

        view.resize(100, initial_height)
        self.app.processEvents()

        self.assertEqual(view.document().textWidth(), view.viewport().width())
        self.assertGreater(view.height(), initial_height)
        self.assertEqual(
            view.height(),
            ceil(view.document().size().height()) + 2,
        )
        parent.close()

    def test_user_bubbles_hug_text_and_reflow_to_column_cap(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)
        short = ChatMessageWidget("user", "hey")
        long = ChatMessageWidget("user", "A longer one-line message that should use the available width.")
        five_lines = ChatMessageWidget(
            "user",
            "\n".join(
                ["A five-line message with enough text to wrap at the narrow column"] * 5
            ),
        )
        code = ChatMessageWidget("user", "```python\nprint('hello')\n```")
        layout.addWidget(short)
        layout.addWidget(long)
        layout.addWidget(five_lines)
        layout.addWidget(code)
        parent.resize(760, 1200)
        parent.show()
        self.app.processEvents()

        self.assertLess(short._content_container.width(), long._content_container.width())
        self.assertLess(long._content_container.width(), 608)
        for message in (short, long, five_lines):
            text_view = message._blocks[0]["widget"]
            self.assertEqual(
                message._content_container.height(),
                text_view.height() + 20,
            )
        self.assertLessEqual(code._content_container.width(), 608)
        self.assertEqual(
            code._content_container.height(),
            code._blocks[0]["widget"].height() + 20,
        )
        five_line_height = five_lines._content_container.height()

        parent.resize(300, 1200)
        self.app.processEvents()

        self.assertLessEqual(long._content_container.width(), int(long.width() * 0.8))
        self.assertGreater(five_lines._content_container.height(), five_line_height)
        self.assertLessEqual(code._content_container.width(), int(code.width() * 0.8))
        parent.close()

    def test_empty_assistant_message_keeps_thinking_indicator_visible(self) -> None:
        parent = QWidget()
        layout = QVBoxLayout(parent)
        message = ChatMessageWidget("assistant", "")
        layout.addWidget(message)
        parent.show()
        self.app.processEvents()

        self.assertIsNotNone(message._placeholder)
        self.assertEqual(message._placeholder.height(), 20)
        self.assertTrue(message._placeholder.isVisible())
        parent.close()

    def test_status_row_reserves_height_and_hides_indeterminate_bar(self) -> None:
        top_bar = ChatTopBar()
        qt_warnings: list[str] = []
        previous_handler = qInstallMessageHandler(
            lambda _kind, _context, message: qt_warnings.append(message)
        )
        try:
            top_bar.show()
            self.app.processEvents()
        finally:
            qInstallMessageHandler(previous_handler)

        self.assertFalse(
            [message for message in qt_warnings if "Could not parse stylesheet" in message]
        )
        self.assertEqual(top_bar.status_container.height(), 20)
        top_bar.show_status("Connecting to local model...")
        self.assertFalse(top_bar.progress_bar.isVisible())
        top_bar.show_status("Loading", progress=50)
        self.assertTrue(top_bar.progress_bar.isVisible())
        self.assertEqual(top_bar.progress_bar.height(), 2)
        top_bar.close()


if __name__ == "__main__":
    unittest.main()
