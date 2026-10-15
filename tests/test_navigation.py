from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget

from ui.navigation import Navigator


class NavigatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = QApplication.instance()
        cls.app = app if isinstance(app, QApplication) else QApplication([])

    def setUp(self) -> None:
        self.pages = QStackedWidget()
        self.chat = QWidget()
        self.library = QWidget()
        self.inspector_stack = QStackedWidget()
        self.plan = QWidget()
        self.files = QWidget()
        self.inspector = QWidget()
        self.pages.addWidget(self.chat)
        self.pages.addWidget(self.library)
        self.inspector_stack.addWidget(self.plan)
        self.inspector_stack.addWidget(self.files)
        self.navigator = Navigator(
            self.pages,
            self.chat,
            self.library,
            self.inspector_stack,
            self.plan,
            self.files,
            self.inspector,
        )

    def test_navigation_keeps_library_separate_from_inspector(self) -> None:
        self.navigator.show_plan()
        self.assertEqual(self.navigator.current_view, "plan")
        self.assertTrue(self.inspector.isVisible())

        self.navigator.show_files()
        self.assertEqual(self.navigator.current_view, "files")
        self.assertIs(self.pages.currentWidget(), self.chat)

        self.navigator.show_library()
        self.assertEqual(self.navigator.current_view, "library")
        self.assertIs(self.pages.currentWidget(), self.library)

        self.navigator.show_chat()
        self.assertEqual(self.navigator.current_view, "chat")
        self.assertTrue(self.inspector.isHidden())

    def test_restore_view_routes_through_public_navigation_methods(self) -> None:
        for view in ("chat", "plan", "files", "library"):
            with self.subTest(view=view):
                self.navigator.restore_view(view)
                self.assertEqual(self.navigator.current_view, view)


if __name__ == "__main__":
    unittest.main()
