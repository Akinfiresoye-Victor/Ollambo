"""
Unit tests for Planner and CodeWriter pure logic:
  - Plan JSON parsing and markdown fence stripping
  - Malformed JSON handling
  - AST signature extraction for Python classes and functions
  - Code fence extraction
Compatible with python -m unittest and pytest.
"""

import unittest

from core.code_writer import clean_code_fence, extract_python_signatures
from core.planner import parse_plan_json


class TestPlannerAndWriter(unittest.TestCase):

    def test_parse_valid_plan_json(self) -> None:
        json_str = """
        {
            "goal": "Build a calculator",
            "assumptions": ["Uses CLI", "Supports basic ops"],
            "files": ["calc.py"],
            "steps": [
                {"step": 1, "description": "Implement calculator operations", "file": "calc.py"}
            ]
        }
        """
        plan = parse_plan_json(json_str)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.goal, "Build a calculator")
        self.assertEqual(len(plan.assumptions), 2)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].file, "calc.py")

    def test_parse_markdown_fenced_json(self) -> None:
        fenced_str = """
        Here is the plan:
        ```json
        {
            "goal": "Create web server",
            "assumptions": [],
            "files": ["server.py"],
            "steps": [
                {"step": 1, "description": "Setup app", "file": "server.py"}
            ]
        }
        ```
        """
        plan = parse_plan_json(fenced_str)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.goal, "Create web server")
        self.assertEqual(len(plan.steps), 1)

    def test_parse_malformed_json_returns_none(self) -> None:
        broken_str = "This is not json { broken: "
        plan = parse_plan_json(broken_str)
        self.assertIsNone(plan)

    def test_extract_python_signatures(self) -> None:
        code = """
import os

class MathHelper:
    def add(self, a, b):
        return a + b

    def subtract(self, a, b):
        return a - b

def calculate_total(items):
    return sum(items)
"""
        sigs = extract_python_signatures(code)
        self.assertIn("class MathHelper:", sigs)
        self.assertIn("def add(self, a, b): ...", sigs)
        self.assertIn("def subtract(self, a, b): ...", sigs)
        self.assertIn("def calculate_total(items): ...", sigs)
        # Verify internal implementation details are omitted
        self.assertNotIn("return a + b", sigs)
        self.assertNotIn("return sum(items)", sigs)

    def test_clean_code_fence(self) -> None:
        fenced = "```python\nprint('hello')\n```"
        self.assertEqual(clean_code_fence(fenced), "print('hello')")

        unfenced = "print('hello')"
        self.assertEqual(clean_code_fence(unfenced), "print('hello')")


if __name__ == "__main__":
    unittest.main()
