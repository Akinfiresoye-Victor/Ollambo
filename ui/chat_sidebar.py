"""
Sidebar widget for the Chat Page.

Manages the list of saved conversations, creating new chats, deleting chats,
opening the settings dialog, and showing a "Work In Progress" indicator
when a build session is active.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.storage import delete_chat, list_chats, list_active_sessions


class ChatSidebar(QWidget):
    """Sidebar containing '+ New Chat', chat list, WIP banner, delete, and settings."""

    new_chat_clicked = Signal()
    chat_selected = Signal(str)   # Passes chat_id
    chat_deleted = Signal(str)    # Passes deleted chat_id
    settings_clicked = Signal()
    wip_clicked = Signal()        # Jump back to the active build session

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMaximumWidth(250)
        self.setMinimumWidth(180)
        self._wip_chat_id: str | None = None
        self._build_ui()
        self.refresh_list()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        from core.assets import get_app_pixmap
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(4, 2, 4, 4)
        brand_row.setSpacing(8)
        pix = get_app_pixmap(26)
        if not pix.isNull():
            icon_lbl = QLabel()
            icon_lbl.setPixmap(pix)
            brand_row.addWidget(icon_lbl)
        brand_lbl = QLabel("Ollambo")
        brand_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #e5e5e5;")
        brand_row.addWidget(brand_lbl)
        brand_row.addStretch()
        layout.addLayout(brand_row)

        # WIP banner — hidden by default, shown when a build is active
        self.btn_wip = QPushButton("🔨 Work In Progress")
        self.btn_wip.setStyleSheet(
            "QPushButton { background-color: #e5c07b; color: #1e1e1e; font-weight: bold;"
            " padding: 6px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #d4a84f; }"
        )
        self.btn_wip.setVisible(False)
        self.btn_wip.clicked.connect(self._on_wip_clicked)
        layout.addWidget(self.btn_wip)

        self.btn_new = QPushButton("+ New Chat")
        self.btn_new.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_new.clicked.connect(self.new_chat_clicked.emit)
        layout.addWidget(self.btn_new)

        self.list_widget = QListWidget()
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.btn_del = QPushButton("Delete")
        self.btn_del.clicked.connect(self._on_delete_clicked)
        btn_row.addWidget(self.btn_del)
        self.btn_cfg = QPushButton("Settings")
        self.btn_cfg.clicked.connect(self.settings_clicked.emit)
        btn_row.addWidget(self.btn_cfg)
        layout.addLayout(btn_row)

    # ── WIP banner control ────────────────────────────────────────────

    def show_wip(self, chat_id: str) -> None:
        """Show the WIP banner for an active build session."""
        self._wip_chat_id = chat_id
        self.refresh_list()

    def hide_wip(self) -> None:
        """Hide the WIP banner (build completed or cancelled)."""
        self._wip_chat_id = None
        self.refresh_list()

    def _on_wip_clicked(self) -> None:
        self.wip_clicked.emit()

    # ── Chat list ─────────────────────────────────────────────────────

    def refresh_list(self) -> None:
        """Reload saved chat records from disk."""
        active_ids = set(list_active_sessions())
        if self._wip_chat_id:
            active_ids.add(self._wip_chat_id)
        self.list_widget.clear()
        for c in list_chats():
            prefix = "🔨 " if c["id"] in active_ids else ""
            item = QListWidgetItem(prefix + c["title"])
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            self.list_widget.addItem(item)
        count = len(active_ids)
        if count > 0:
            self.btn_wip.setText(f"🔨 {count} In Progress" if count > 1 else "🔨 1 Work In Progress")
            self.btn_wip.setVisible(True)
        else:
            self.btn_wip.setVisible(False)

    def upsert_chat(self, chat_id: str, title: str) -> None:
        """Fast in-memory update or prepend of chat item without reading disk."""
        prefix = "🔨 " if (self._wip_chat_id == chat_id) else ""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == chat_id:
                if item.text() != prefix + title:
                    item.setText(prefix + title)
                return
        # If new item, insert at top
        new_item = QListWidgetItem(prefix + title)
        new_item.setData(Qt.ItemDataRole.UserRole, chat_id)
        self.list_widget.insertItem(0, new_item)

    def clear_selection(self) -> None:
        self.list_widget.clearSelection()

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        if chat_id := item.data(Qt.ItemDataRole.UserRole):
            self.chat_selected.emit(chat_id)

    def _on_delete_clicked(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        delete_chat(chat_id)
        from core.storage import delete_work_session
        delete_work_session(chat_id)
        if self._wip_chat_id == chat_id:
            self._wip_chat_id = None

        # Remove the item from the list without reloading from disk
        row = self.list_widget.row(item)
        self.list_widget.takeItem(row)

        # Notify the parent that this chat was deleted
        self.chat_deleted.emit(chat_id)

        # Only create a new chat if the list is now completely empty
        if self.list_widget.count() == 0:
            self.new_chat_clicked.emit()

