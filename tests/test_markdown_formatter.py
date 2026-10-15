import unittest
from core.markdown_formatter import markdown_to_html
from ui.message_widget import ChatMessageWidget
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])


class TestMarkdownFormatter(unittest.TestCase):
    def test_markdown_headings(self):
        html1 = markdown_to_html("# Main Heading")
        self.assertIn("<h1", html1)
        self.assertIn("Main Heading", html1)

        html2 = markdown_to_html("## Sub Heading")
        self.assertIn("<h2", html2)
        self.assertIn("Sub Heading", html2)

        html3 = markdown_to_html("### Minor Heading")
        self.assertIn("<h3", html3)
        self.assertIn("Minor Heading", html3)

    def test_markdown_inline_formatting(self):
        text = "This is **bold text** and `inline_code()` with [link](https://test.com)"
        html = markdown_to_html(text)
        self.assertIn("<b style=", html)
        self.assertIn("bold text", html)
        self.assertIn("font-family: Consolas", html)
        self.assertIn("inline_code()", html)
        self.assertIn("<a href='https://test.com'", html)
        self.assertIn("link", html)

    def test_code_span_protection(self):
        text = "Check `**not_bold**` and `*not_italic*` and `[not_link](https://test.com)`."
        html = markdown_to_html(text)
        self.assertNotIn("<b ", html)
        self.assertNotIn("<i ", html)
        self.assertNotIn("<a ", html)
        self.assertIn("**not_bold**", html)
        self.assertIn("*not_italic*", html)

    def test_snake_case_and_dunder_preserved(self):
        text = "Use snake_case_words and __init__ in Python, but _this is italic_."
        html = markdown_to_html(text)
        self.assertIn("snake_case_words", html)
        self.assertIn("__init__", html)
        self.assertIn("<i style=", html)
        self.assertIn("this is italic", html)

    def test_link_schemes(self):
        text = "[safe](http://example.com) [also_safe](https://example.com) [unsafe](javascript:alert(1)) [file](file:///etc/passwd)"
        html = markdown_to_html(text)
        self.assertIn("<a href='http://example.com'", html)
        self.assertIn("<a href='https://example.com'", html)
        self.assertNotIn("<a href='javascript", html)
        self.assertNotIn("<a href='file", html)

    def test_markdown_lists(self):
        text = "- Item One\n- Item Two\n- Item Three"
        html = markdown_to_html(text)
        self.assertIn("<ul", html)
        self.assertIn("<li style=", html)
        self.assertIn("Item One", html)
        self.assertIn("Item Two", html)

        ol_text = "1. First step\n2. Second step"
        ol_html = markdown_to_html(ol_text)
        self.assertIn("<ol", ol_html)
        self.assertIn("First step", ol_html)
        self.assertIn("Second step", ol_html)

    def test_markdown_blockquote_and_hr(self):
        text = "> Important advice\n---\nFinal thought"
        html = markdown_to_html(text)
        self.assertIn("border-left: 3px solid", html)
        self.assertIn("Important advice", html)
        self.assertIn("<hr", html)

    def test_chat_message_widget_rendering(self):
        content = "Here is an explanation:\n\n```python\ndef hello():\n    return 'world'\n```\nAnd a **conclusion**."
        widget = ChatMessageWidget("assistant", content)
        self.assertEqual(widget.get_content(), content)
        # Should have parsed code block
        self.assertIsNotNone(widget._content_layout)

    def test_chat_message_widget_in_place_streaming(self):
        widget = ChatMessageWidget("assistant", "")
        # Streaming token chunks without code fence
        widget.set_content("Streaming chunk 1", is_final=False)
        self.assertTrue(len(widget._blocks) > 0)
        widget.set_content("Streaming chunk 1 and chunk 2", is_final=False)
        self.assertIn("chunk 2", widget.get_content())
        # Final render
        widget.set_content("### Final Title\nDone **stream**.", is_final=True)
        self.assertIn("Final Title", widget.get_content())


if __name__ == "__main__":
    unittest.main()
