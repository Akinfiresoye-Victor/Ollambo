from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from ui.chat_top_bar import ChatTopBar
from ui.icons import Spinner, _ICON_PATHS, icon
from ui.text_utils import strip_emoji_prefix


class IconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        existing_app = QApplication.instance()
        cls.app = existing_app if isinstance(existing_app, QApplication) else QApplication([])

    def test_all_named_icons_render(self) -> None:
        for name in _ICON_PATHS:
            with self.subTest(name=name):
                self.assertFalse(icon(name).isNull())

    def test_unknown_icon_and_invalid_size_are_explicit(self) -> None:
        with self.assertRaises(ValueError):
            icon("missing-icon")
        with self.assertRaises(ValueError):
            icon("plus", size=0)

    def test_spinner_pauses_when_hidden_and_in_low_power_mode(self) -> None:
        spinner = Spinner(low_power_mode=True)
        spinner.show()
        self.app.processEvents()
        self.assertFalse(spinner.timer.isActive())

        spinner.set_low_power_mode(False)
        self.assertTrue(spinner.timer.isActive())
        spinner.hide()
        self.assertFalse(spinner.timer.isActive())

    def test_strips_leading_emoji_and_guesses_action(self) -> None:
        self.assertEqual(
            strip_emoji_prefix("🔍 Searching the web…"),
            ("Searching the web…", "search"),
        )
        self.assertEqual(
            strip_emoji_prefix("⚠️ Request failed"),
            ("Request failed", "warn"),
        )

    def test_does_not_strip_model_text_without_a_leading_emoji(self) -> None:
        text = "Answer: keep this emoji 🌱 and the whole response."
        self.assertEqual(strip_emoji_prefix(text), (text, None))

    def test_top_bar_uses_category_icons_for_clean_status_text(self) -> None:
        bar = ChatTopBar()
        bar.show_status("🔍 Searching the web…")
        self.assertEqual(bar.lbl_status.text(), "Searching the web…")
        self.assertFalse(bar._status_icon.pixmap().isNull())
        self.assertTrue(bar._status_spinner.isHidden())

    def test_top_bar_connection_copy_remains_compatible(self) -> None:
        bar = ChatTopBar()
        self.assertIn("Standby", bar.lbl_connection.text())
        bar.set_working()
        self.assertIn("Active", bar.lbl_connection.text())
        bar.set_idle()
        self.assertIn("03:00", bar.lbl_connection.text())


if __name__ == "__main__":
    unittest.main()
