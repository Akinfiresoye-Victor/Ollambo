from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QPushButton

from core.markdown_formatter import markdown_to_html, set_palette
from ui.report_viewer import markdown_to_report_html
from ui.theme import DARK, apply_theme, build_stylesheet, set_variant


existing_app = QApplication.instance()
app = existing_app if isinstance(existing_app, QApplication) else QApplication([])


class TestTheme(unittest.TestCase):
    def tearDown(self) -> None:
        set_palette()

    def test_tokens_are_immutable_and_stylesheet_uses_them(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            setattr(DARK.colors, "accent", "white")

        stylesheet = build_stylesheet()
        self.assertIn(DARK.colors.window, stylesheet)
        self.assertIn(DARK.colors.accent, stylesheet)
        self.assertIn('QPushButton[variant="primary"]', stylesheet)
        self.assertIn("QScrollBar::handle", stylesheet)

    def test_apply_theme_sets_palette_and_markdown_colors(self) -> None:
        apply_theme(app)
        palette = app.palette()
        self.assertEqual(
            palette.color(QPalette.ColorRole.Window).name().upper(),
            DARK.colors.window,
        )
        self.assertEqual(
            palette.color(QPalette.ColorRole.Highlight).name().upper(),
            DARK.colors.accent,
        )
        rendered = markdown_to_html("# Heading\n\n`inline code`")
        self.assertIn("rgb(204, 126, 91)", rendered)
        self.assertIn("font-family: Cascadia Code, JetBrains Mono, SF Mono, Consolas, monospace", rendered)
        report = markdown_to_report_html("# Report theme", mermaid_js_url="")
        self.assertIn(DARK.colors.accent, report)
        self.assertIn(DARK.colors.window, report)

    def test_variant_sets_dynamic_property(self) -> None:
        button = QPushButton("Save")
        set_variant(button, "primary")
        self.assertEqual(button.property("variant"), "primary")


if __name__ == "__main__":
    unittest.main()
