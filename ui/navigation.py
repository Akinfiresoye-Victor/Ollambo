"""Small navigation facade for chat, inspector, and library surfaces."""

from __future__ import annotations

from PySide6.QtWidgets import QStackedWidget, QWidget


class Navigator:
    def __init__(
        self,
        page_stack: QStackedWidget,
        chat_page: QWidget,
        library_page: QWidget,
        inspector_stack: QStackedWidget,
        plan_page: QWidget,
        files_page: QWidget,
        inspector: QWidget,
    ) -> None:
        self._page_stack = page_stack
        self._chat_page = chat_page
        self._library_page = library_page
        self._inspector_stack = inspector_stack
        self._plan_page = plan_page
        self._files_page = files_page
        self._inspector = inspector

    @property
    def current_view(self) -> str:
        if self._page_stack.currentWidget() is self._library_page:
            return "library"
        if self._inspector.isHidden():
            return "chat"
        if self._inspector_stack.currentWidget() is self._files_page:
            return "files"
        if self._inspector_stack.currentWidget() is self._plan_page:
            return "plan"
        return "chat"

    def show_chat(self) -> None:
        self._page_stack.setCurrentWidget(self._chat_page)
        self._inspector.hide()

    def show_plan(self) -> None:
        self._page_stack.setCurrentWidget(self._chat_page)
        self._inspector_stack.setCurrentWidget(self._plan_page)
        self._inspector.show()

    def show_files(self) -> None:
        self._page_stack.setCurrentWidget(self._chat_page)
        self._inspector_stack.setCurrentWidget(self._files_page)
        self._inspector.show()

    def show_library(self) -> None:
        self._page_stack.setCurrentWidget(self._library_page)
        self._inspector.hide()

    def restore_view(self, view: str) -> None:
        if view == "library":
            self.show_library()
        elif view == "files":
            self.show_files()
        elif view == "plan":
            self.show_plan()
        else:
            self.show_chat()
