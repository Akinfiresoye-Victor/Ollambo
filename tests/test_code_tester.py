"""
Unit tests for automated code testing and self-healing engine.
"""

import unittest
from unittest.mock import MagicMock, patch

from core.code_tester import (
    run_automated_tests,
    test_and_repair,
    validate_syntax,
)


class TestCodeTester(unittest.TestCase):
    def test_validate_syntax_valid_python(self):
        code = "def hello():\n    return 'world'\n"
        valid, err = validate_syntax("test.py", code)
        self.assertTrue(valid)
        self.assertEqual(err, "")

    def test_validate_syntax_broken_python(self):
        code = "def hello(\n    return 'world'\n"  # missing closing paren
        valid, err = validate_syntax("test.py", code)
        self.assertFalse(valid)
        self.assertIn("SyntaxError", err)

    def test_validate_syntax_empty_code(self):
        valid, err = validate_syntax("test.py", "   \n")
        self.assertFalse(valid)
        self.assertIn("empty", err)

    def test_validate_syntax_json(self):
        valid_json = '{"name": "ollambo", "version": 1}'
        valid, _ = validate_syntax("config.json", valid_json)
        self.assertTrue(valid)

        broken_json = '{"name": "ollambo", broken}'
        valid, err = validate_syntax("config.json", broken_json)
        self.assertFalse(valid)
        self.assertIn("JSONDecodeError", err)

    def test_run_automated_tests_placeholders(self):
        code_with_todo = "def calc():\n    # TODO: implement logic\n    pass\n"
        passed, msg = run_automated_tests("calc.py", code_with_todo)
        self.assertFalse(passed)
        self.assertIn("Quality check failed", msg)

    @patch("core.code_tester.OllamaClient")
    def test_test_and_repair_successful_heal(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        # Mock Ollama response providing fixed code
        fixed_code = "def add(a, b):\n    return a + b\n"
        mock_client.chat_stream.return_value = [
            {"message": {"content": f"```python\n{fixed_code}```"}, "done": True}
        ]

        broken_code = "def add(a, b\n    return a + b"
        status_updates = []

        final_code, passed, summary = test_and_repair(
            ollama_url="http://localhost:11434",
            model="llama3",
            filename="math_lib.py",
            code=broken_code,
            step_desc="Create math library",
            on_repair_status=lambda s: status_updates.append(s),
        )

        self.assertTrue(passed)
        self.assertIn("def add(a, b):", final_code)
        self.assertTrue(any("Testing caught issue" in s for s in status_updates))


if __name__ == "__main__":
    unittest.main()
