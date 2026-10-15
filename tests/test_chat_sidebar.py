from __future__ import annotations

import datetime
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from ui.chat_sidebar import ChatSidebar, _ChatRow


class ChatSidebarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = QApplication.instance()
        cls.app = app if isinstance(app, QApplication) else QApplication([])

    def setUp(self) -> None:
        now = datetime.datetime.now().timestamp()
        self.chats = [
            {"id": "chat-1", "title": "Alpha project", "updated": now, "status": "idle"},
            {"id": "chat-2", "title": "Beta notes", "updated": now, "status": "idle"},
        ]
        self.chat_list = patch("ui.chat_sidebar.list_chats", return_value=self.chats)
        self.active_sessions = patch("ui.chat_sidebar.list_active_sessions", return_value=[])
        self.chat_list.start()
        self.active_sessions.start()
        self.addCleanup(self.chat_list.stop)
        self.addCleanup(self.active_sessions.stop)
        self.sidebar = ChatSidebar()
        self.sidebar.show()
        self.app.processEvents()

    def _item_for(self, chat_id: str):
        for index in range(self.sidebar.list_widget.count()):
            item = self.sidebar.list_widget.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == chat_id:
                return item
        self.fail(f"Chat {chat_id!r} not found")

    def test_local_search_filters_titles_and_groups(self) -> None:
        self.sidebar.search_input.setText("beta")
        self.app.processEvents()

        self.assertTrue(self._item_for("chat-1").isHidden())
        self.assertFalse(self._item_for("chat-2").isHidden())
        self.assertFalse(self.sidebar.list_widget.item(0).isHidden())

    def test_state_refresh_keeps_custom_row_selection_connected(self) -> None:
        selected: list[str] = []
        self.sidebar.chat_selected.connect(selected.append)

        item = self._item_for("chat-1")
        first_row = self.sidebar.list_widget.itemWidget(item)
        self.sidebar.set_chat_state("chat-1", status="generating")
        row = self.sidebar.list_widget.itemWidget(item)
        self.assertIsInstance(row, _ChatRow)
        self.assertIsNot(row, first_row)

        QTest.mouseClick(row, Qt.MouseButton.LeftButton)
        self.assertEqual(selected, ["chat-1"])

    def test_inline_rename_updates_row_and_emits_persistence_signal(self) -> None:
        renamed: list[tuple[str, str]] = []
        self.sidebar.chat_renamed.connect(lambda chat_id, title: renamed.append((chat_id, title)))
        item = self._item_for("chat-1")
        row = self.sidebar.list_widget.itemWidget(item)
        self.assertIsInstance(row, _ChatRow)

        row.begin_rename()
        editor = row.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        editor.setText("Renamed project")
        editor.editingFinished.emit()

        self.assertEqual(renamed, [("chat-1", "Renamed project")])
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole + 3), "Renamed project")
        self.assertEqual(row.title_label.text(), "Renamed project")

    def test_upsert_changes_title_without_clearing_saved_row_state(self) -> None:
        self.chats[0]["status"] = "generating"
        self.chats[0]["unread"] = True

        self.sidebar.upsert_chat("chat-1", "Updated title")

        item = self._item_for("chat-1")
        row = self.sidebar.list_widget.itemWidget(item)
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole + 3), "Updated title")
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole + 1), "generating")
        self.assertTrue(item.data(Qt.ItemDataRole.UserRole + 2))
        self.assertIsNotNone(row.spinner)


if __name__ == "__main__":
    unittest.main()
