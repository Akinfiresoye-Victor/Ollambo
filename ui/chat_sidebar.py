"""
Sidebar widget for the Chat Page.

Manages the list of saved conversations, creating new chats, deleting chats,
opening settings, and showing row-level activity for active conversations.
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.storage import delete_chat, delete_work_session, list_chats
from ui.icons import Spinner, icon
from ui.theme import set_variant
from ui.theme.tokens import DARK
from ui.text_utils import strip_emoji_prefix


class _ChatRow(QWidget):
    selected = Signal()
    more_clicked = Signal()
    renamed = Signal(str)

    def __init__(self, title: str, active: bool, unread: bool, low_power: bool) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)
        self._title = title
        self._low_power = low_power
        self.spinner: Spinner | None = None
        marker = QLabel()
        marker.setFixedSize(16, 16)
        marker.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if active:
            self.spinner = Spinner(16, low_power_mode=low_power, parent=self)
            layout.addWidget(self.spinner)
        else:
            if unread:
                marker.setPixmap(icon("message", DARK.colors.accent, 16).pixmap(16, 16))
            layout.addWidget(marker)
        display_title, _ = strip_emoji_prefix(title)
        self.title_label = QLabel(display_title)
        self.title_label.setProperty("role", "body")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.title_label, 1)
        self._layout = layout
        self._title_editor: QLineEdit | None = None
        self.more_button = QToolButton()
        self.more_button.setIcon(icon("more-horizontal"))
        self.more_button.setToolTip("Chat actions")
        self.more_button.setProperty("variant", "ghost")
        self.more_button.setProperty("iconOnly", "true")
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.setFixedSize(28, 28)
        self.more_button.clicked.connect(self.more_clicked.emit)
        self.more_button.hide()
        layout.addWidget(self.more_button)

    def enterEvent(self, event) -> None:
        self.more_button.show()
        super().enterEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit()
        super().mousePressEvent(event)

    def leaveEvent(self, event) -> None:
        self.more_button.hide()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._title_editor is not None:
            return
        display_title, _ = strip_emoji_prefix(self._title)
        width = max(0, self.title_label.width() - 4)
        self.title_label.setText(QFontMetrics(self.title_label.font()).elidedText(
            display_title, Qt.TextElideMode.ElideRight, width
        ))

    def begin_rename(self) -> None:
        if self._title_editor is not None:
            return
        editor = QLineEdit(self._title, self)
        editor.selectAll()
        self._layout.replaceWidget(self.title_label, editor)
        self.title_label.hide()
        self._title_editor = editor
        editor.editingFinished.connect(self._finish_rename)
        editor.setFocus()

    def set_title(self, title: str) -> None:
        self._title = title
        display_title, _ = strip_emoji_prefix(title)
        self.title_label.setText(QFontMetrics(self.title_label.font()).elidedText(
            display_title, Qt.TextElideMode.ElideRight, max(0, self.title_label.width() - 4)
        ))

    def _finish_rename(self) -> None:
        editor = self._title_editor
        if editor is None:
            return
        title = editor.text().strip()
        if title:
            self._title = title
            self.renamed.emit(title)
        self._layout.replaceWidget(editor, self.title_label)
        editor.deleteLater()
        self._title_editor = None
        self.title_label.show()
        display_title, _ = strip_emoji_prefix(self._title)
        self.title_label.setText(QFontMetrics(self.title_label.font()).elidedText(
            display_title, Qt.TextElideMode.ElideRight, max(0, self.title_label.width() - 4)
        ))


class ChatSidebar(QWidget):
    """Collapsible navigation sidebar with grouped chats and local search."""

    new_chat_clicked = Signal()
    chat_selected = Signal(str)   # Passes chat_id
    chat_delete_requested = Signal(str)
    chat_deleted = Signal(str)    # Passes deleted chat_id
    settings_clicked = Signal()
    library_clicked = Signal()
    toggle_requested = Signal()
    chat_renamed = Signal(str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("Sidebar")
        self.setMinimumWidth(0)
        self.setMaximumWidth(DARK.sizes.sidebar_width)
        self._wip_chat_id: str | None = None
        self._low_power_mode = False
        self._active_chat_id: str | None = None
        self._runtime_statuses: dict[str, str] = {}
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
        self.btn_collapse = QToolButton()
        self.btn_collapse.setIcon(icon("sidebar-left"))
        self.btn_collapse.setToolTip("Collapse sidebar (Ctrl+B)")
        self.btn_collapse.setProperty("variant", "ghost")
        self.btn_collapse.setProperty("iconOnly", "true")
        self.btn_collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_collapse.clicked.connect(self.toggle_requested.emit)
        brand_row.addWidget(self.btn_collapse)
        pix = get_app_pixmap(26)
        if not pix.isNull():
            icon_lbl = QLabel()
            icon_lbl.setPixmap(pix)
            brand_row.addWidget(icon_lbl)
        brand_lbl = QLabel("Ollambo")
        brand_lbl.setProperty("role", "title")
        brand_row.addWidget(brand_lbl)
        brand_row.addStretch()
        layout.addLayout(brand_row)

        self.btn_new = QPushButton("New chat")
        self.btn_new.setIcon(icon("plus", DARK.colors.text_on_accent, 18))
        self.btn_new.setStyleSheet("padding-left: 12px;")
        set_variant(self.btn_new, "primary")
        self.btn_new.clicked.connect(self.new_chat_clicked.emit)
        layout.addWidget(self.btn_new)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search chats")
        self.search_input.setTextMargins(32, 0, 0, 0)
        self.search_input.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setToolTip("Search chat titles (Ctrl+K)")
        self.search_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        self.list_widget.setProperty("role", "body")
        self.list_widget.setStyleSheet("QListWidget { background: transparent; border: none; }")
        layout.addWidget(self.list_widget, 1)
        self.btn_library = QPushButton("Library")
        self.btn_library.setIcon(icon("flask"))
        self.btn_library.setToolTip("Research Library and Artifacts")
        self.btn_library.setStyleSheet("padding-left: 12px;")
        self.btn_library.clicked.connect(self.library_clicked.emit)
        layout.addWidget(self.btn_library)
        self.btn_cfg = QPushButton("Settings")
        self.btn_cfg.setIcon(icon("settings"))
        self.btn_cfg.setStyleSheet("padding-left: 12px;")
        self.btn_cfg.setToolTip("Settings (Ctrl+,)")
        self.btn_cfg.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self.btn_cfg)

    # ── Active build row state ────────────────────────────────────────

    def show_wip(self, chat_id: str) -> None:
        """Mark the building conversation so its row can offer a useful tooltip."""
        self._wip_chat_id = chat_id
        self.refresh_list()

    def hide_wip(self) -> None:
        """Clear the active build marker."""
        self._wip_chat_id = None
        self.refresh_list()

    # ── Chat list ─────────────────────────────────────────────────────

    def refresh_list(self) -> None:
        """Reload saved chat records from disk without reordering the list on simple selection updates."""
        previous_order = [
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) is not None
        ]
        chats = list_chats()
        chat_map = {c["id"]: c for c in chats}
        ordered_ids = [chat_id for chat_id in previous_order if chat_id in chat_map]
        remaining = [c for c in chats if c["id"] not in set(ordered_ids)]
        ordered_chats = [chat_map[chat_id] for chat_id in ordered_ids] + remaining

        self.list_widget.clear()
        current_group = None
        for c in ordered_chats:
            if c["id"] in self._runtime_statuses:
                status = self._runtime_statuses[c["id"]]
            else:
                status = c.get("status", "idle")
            if (
                c["id"] not in self._runtime_statuses
                and status in ("generating", "planning", "researching", "building")
            ):
                status = "idle"
            if c["id"] == self._wip_chat_id:
                status = "building"
            group = self._date_group(c.get("updated", 0))
            if group != current_group:
                header = QListWidgetItem(group)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_widget.addItem(header)
                current_group = group
            title = c["title"]
            display_title, _ = strip_emoji_prefix(title)
            item = QListWidgetItem("")
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setData(Qt.ItemDataRole.UserRole + 1, status)
            item.setData(Qt.ItemDataRole.UserRole + 2, bool(c.get("unread", False)))
            item.setData(Qt.ItemDataRole.UserRole + 3, c["title"])
            item.setData(Qt.ItemDataRole.UserRole + 4, c.get("updated", 0))
            self.list_widget.addItem(item)
            row = self._set_row(item, title, status, bool(c.get("unread", False)))
            if c["id"] == self._active_chat_id:
                self.list_widget.setCurrentItem(item)
            if status in ("generating", "planning", "researching", "building"):
                tooltip = "Work in progress. Click to return to this chat."
            elif c.get("unread", False):
                tooltip = "New activity"
            else:
                tooltip = ""
            item.setToolTip(tooltip)
            row.setToolTip(tooltip)
        self._apply_filter(self.search_input.text())
        if self._active_chat_id is None:
            self.list_widget.setCurrentRow(-1)
            self.list_widget.clearSelection()

    def _set_row(
        self,
        item: QListWidgetItem,
        title: str,
        status: str,
        unread: bool,
    ) -> _ChatRow:
        chat_id = str(item.data(Qt.ItemDataRole.UserRole))
        row = _ChatRow(
            title,
            status in ("generating", "planning", "researching", "building"),
            unread,
            self._low_power_mode,
        )
        self.list_widget.setItemWidget(item, row)
        item.setSizeHint(QSize(row.sizeHint().width(), max(row.sizeHint().height(), 36)))
        row.more_clicked.connect(lambda cid=chat_id, chat_item=item: self._show_chat_menu(cid, chat_item))
        row.selected.connect(lambda cid=chat_id, chat_item=item: self._select_chat(cid, chat_item))
        row.renamed.connect(
            lambda new_title, cid=chat_id, chat_item=item: self._commit_rename(cid, chat_item, new_title)
        )
        return row

    @staticmethod
    def _date_group(updated: object) -> str:
        try:
            stamp = float(updated or 0) if isinstance(updated, (int, float, str)) else 0.0
            day = datetime.datetime.fromtimestamp(stamp).date()
        except (OverflowError, OSError, TypeError, ValueError):
            day = datetime.date.min
        today = datetime.date.today()
        age = (today - day).days
        if age <= 0:
            return "Today"
        if age == 1:
            return "Yesterday"
        if age < 7:
            return "Previous 7 days"
        return "Older"

    def upsert_chat(
        self,
        chat_id: str,
        title: str,
        status: str | None = None,
        unread: bool | None = None,
        new_message: bool = False,
    ) -> None:
        """Update one row, moving it only when a new transcript message arrives."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) != chat_id:
                continue
            item.setData(Qt.ItemDataRole.UserRole + 3, title)
            row = self.list_widget.itemWidget(item)
            if isinstance(row, _ChatRow) and row._title != title:
                row.set_title(title)
            self.set_chat_state(chat_id, status=status, unread=unread)
            if new_message:
                chat = next((c for c in list_chats() if c["id"] == chat_id), None)
                if chat is not None:
                    item.setData(Qt.ItemDataRole.UserRole + 4, chat.get("updated", 0))
                    self._move_chat_to_group_top(item, self._date_group(chat.get("updated", 0)))
            break
        else:
            chat = next((c for c in list_chats() if c["id"] == chat_id), None)
            if chat is not None:
                if status is not None:
                    self._runtime_statuses[chat_id] = status
                row_status = self._runtime_statuses.get(chat_id, chat.get("status", "idle"))
                if (
                    chat_id not in self._runtime_statuses
                    and row_status in ("generating", "planning", "researching", "building")
                ):
                    row_status = "idle"
                if chat_id == self._wip_chat_id:
                    row_status = "building"
                row_unread = bool(chat.get("unread", False)) if unread is None else unread
                self._insert_chat_row(chat, row_status, row_unread, prepend=new_message)

    def _insert_chat_row(
        self,
        chat: dict,
        status: str,
        unread: bool,
        *,
        prepend: bool,
    ) -> None:
        chat_id = chat["id"]
        group = self._date_group(chat.get("updated", 0))
        header_row = next(
            (
                index
                for index in range(self.list_widget.count())
                if self.list_widget.item(index).data(Qt.ItemDataRole.UserRole) is None
                and self.list_widget.item(index).text() == group
            ),
            None,
        )
        group_order = ("Today", "Yesterday", "Previous 7 days", "Older")
        if header_row is None:
            insert_at = self.list_widget.count()
            for index in range(self.list_widget.count()):
                item = self.list_widget.item(index)
                if (
                    item.data(Qt.ItemDataRole.UserRole) is None
                    and group_order.index(item.text()) > group_order.index(group)
                ):
                    insert_at = index
                    break
            header = QListWidgetItem(group)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.insertItem(insert_at, header)
            header_row = insert_at

        insert_at = header_row + 1
        if not prepend:
            while (
                insert_at < self.list_widget.count()
                and self.list_widget.item(insert_at).data(Qt.ItemDataRole.UserRole) is not None
            ):
                insert_at += 1
        item = QListWidgetItem("")
        item.setData(Qt.ItemDataRole.UserRole, chat_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, status)
        item.setData(Qt.ItemDataRole.UserRole + 2, unread)
        item.setData(Qt.ItemDataRole.UserRole + 3, chat["title"])
        item.setData(Qt.ItemDataRole.UserRole + 4, chat.get("updated", 0))
        self.list_widget.insertItem(insert_at, item)
        row = self._set_row(item, chat["title"], status, unread)
        if chat_id == self._active_chat_id:
            self.list_widget.setCurrentItem(item)
        if status in ("generating", "planning", "researching", "building"):
            tooltip = "Work in progress. Click to return to this chat."
        elif unread:
            tooltip = "New activity"
        else:
            tooltip = ""
        item.setToolTip(tooltip)
        row.setToolTip(tooltip)
        self._apply_filter(self.search_input.text())

    def _move_chat_to_group_top(self, item: QListWidgetItem, group: str) -> None:
        old_row = self.list_widget.row(item)
        row_widget = self.list_widget.itemWidget(item)
        self.list_widget.removeItemWidget(item)
        self.list_widget.takeItem(old_row)

        old_header = None
        for index in range(old_row - 1, -1, -1):
            candidate = self.list_widget.item(index)
            if candidate.data(Qt.ItemDataRole.UserRole) is None:
                old_header = candidate
                break
        if old_header is not None:
            header_row = self.list_widget.row(old_header)
            next_header = next(
                (
                    self.list_widget.item(index)
                    for index in range(header_row + 1, self.list_widget.count())
                    if self.list_widget.item(index).data(Qt.ItemDataRole.UserRole) is None
                ),
                None,
            )
            if next_header is None:
                has_chat = header_row + 1 < self.list_widget.count()
            else:
                has_chat = self.list_widget.row(next_header) > header_row + 1
            if not has_chat:
                self.list_widget.takeItem(header_row)

        headers = {
            str(self.list_widget.item(index).text()): index
            for index in range(self.list_widget.count())
            if self.list_widget.item(index).data(Qt.ItemDataRole.UserRole) is None
        }
        if group not in headers:
            group_order = ("Today", "Yesterday", "Previous 7 days", "Older")
            group_rank = group_order.index(group)
            insert_at = self.list_widget.count()
            for existing_group, header_row in headers.items():
                if group_order.index(existing_group) > group_rank:
                    insert_at = header_row
                    break
            header = QListWidgetItem(group)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list_widget.insertItem(insert_at, header)
            header_row = insert_at
        else:
            header_row = headers[group]

        self.list_widget.insertItem(header_row + 1, item)
        if row_widget is not None:
            self.list_widget.setItemWidget(item, row_widget)
        if self._active_chat_id == item.data(Qt.ItemDataRole.UserRole):
            self.list_widget.setCurrentItem(item)
        self._apply_filter(self.search_input.text())

    def set_chat_state(
        self,
        chat_id: str,
        status: str | None = None,
        unread: bool | None = None,
    ) -> None:
        if status is not None:
            self._runtime_statuses[chat_id] = status
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) != chat_id:
                continue
            current_status = status or item.data(Qt.ItemDataRole.UserRole + 1) or "idle"
            current_unread = (
                bool(unread)
                if unread is not None
                else bool(item.data(Qt.ItemDataRole.UserRole + 2))
            )
            if (
                current_status == item.data(Qt.ItemDataRole.UserRole + 1)
                and current_unread == bool(item.data(Qt.ItemDataRole.UserRole + 2))
            ):
                return
            title = item.data(Qt.ItemDataRole.UserRole + 3) or ""
            item.setData(Qt.ItemDataRole.UserRole + 1, current_status)
            item.setData(Qt.ItemDataRole.UserRole + 2, current_unread)
            row = self._set_row(item, title, current_status, current_unread)
            if current_status in ("generating", "planning", "researching", "building"):
                tooltip = "Work in progress. Click to return to this chat."
            elif current_unread:
                tooltip = "New activity"
            else:
                tooltip = ""
            item.setToolTip(tooltip)
            row.setToolTip(tooltip)
            return

    def set_active_chat(self, chat_id: str | None) -> None:
        """Set the only chat row that should appear selected."""
        self._active_chat_id = chat_id
        if chat_id is not None:
            for index in range(self.list_widget.count()):
                item = self.list_widget.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == chat_id:
                    self.list_widget.setCurrentItem(item)
                    return
        self.list_widget.setCurrentRow(-1)
        self.list_widget.clearSelection()

    def set_low_power_mode(self, enabled: bool) -> None:
        self._low_power_mode = enabled
        self.refresh_list()

    def clear_selection(self) -> None:
        self.set_active_chat(None)

    def _select_chat(self, chat_id: str, item: QListWidgetItem) -> None:
        self.chat_selected.emit(chat_id)

    def _apply_filter(self, query: str) -> None:
        needle = query.strip().casefold()
        visible_in_group = False
        group_item: QListWidgetItem | None = None
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) is None:
                if group_item is not None:
                    group_item.setHidden(not visible_in_group)
                group_item = item
                visible_in_group = False
                continue
            title = str(item.data(Qt.ItemDataRole.UserRole + 3) or "")
            visible = not needle or needle in title.casefold()
            item.setHidden(not visible)
            visible_in_group = visible_in_group or visible
        if group_item is not None:
            group_item.setHidden(not visible_in_group)

    def _show_chat_menu(self, chat_id: str, item: QListWidgetItem) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction(icon("pencil"), "Rename")
        delete_action = menu.addAction(icon("trash"), "Delete")
        selected = menu.exec(self.list_widget.mapToGlobal(
            self.list_widget.visualItemRect(item).bottomRight()
        ))
        if selected is rename_action:
            row = self.list_widget.itemWidget(item)
            if isinstance(row, _ChatRow):
                row.begin_rename()
        elif selected is delete_action:
            self._delete_chat(chat_id, item)

    def _commit_rename(self, chat_id: str, item: QListWidgetItem, title: str) -> None:
        item.setData(Qt.ItemDataRole.UserRole + 3, title)
        self.chat_renamed.emit(chat_id, title)

    def _delete_chat(self, chat_id: str, item: QListWidgetItem) -> None:
        answer = QMessageBox.question(
            self,
            "Delete chat",
            "Delete this chat and its saved build session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        self.chat_delete_requested.emit(chat_id)
        delete_chat(chat_id)
        delete_work_session(chat_id)
        if self._wip_chat_id == chat_id:
            self._wip_chat_id = None

        # Remove the item from the list without reloading from disk
        row = self.list_widget.row(item)
        self.list_widget.takeItem(row)

        # Notify the parent that this chat was deleted
        self.chat_deleted.emit(chat_id)

        # Only create a new chat if the list is now completely empty
        if not any(
            self.list_widget.item(i).data(Qt.ItemDataRole.UserRole) is not None
            for i in range(self.list_widget.count())
        ):
            self.new_chat_clicked.emit()
