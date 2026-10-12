"""
Unit tests for Long Code Continuation pure logic:
  - Last N lines extraction
  - Stray markdown code fence stripping
  - Exact line overlap deduplication
  - Partial line boundary stitching
Compatible with both pytest and python -m unittest.
"""

import unittest

from core.code_writer import (
    clean_code_fence,
    get_last_n_lines,
    stitch_continuation,
)


class TestCodeContinuation(unittest.TestCase):

    def test_get_last_n_lines(self) -> None:
        lines = "\n".join(f"line {i}" for i in range(50))
        last_10 = get_last_n_lines(lines, 10)
        split = last_10.splitlines()
        self.assertEqual(len(split), 10)
        self.assertEqual(split[0], "line 40")
        self.assertEqual(split[-1], "line 49")

    def test_clean_code_fence_variants(self) -> None:
        # Full fence
        text1 = "```python\ndef test():\n    pass\n```"
        self.assertEqual(clean_code_fence(text1), "def test():\n    pass")

        # Incomplete / cut off opening fence
        text2 = "```python\ndef test():\n    return 42"
        self.assertEqual(clean_code_fence(text2), "def test():\n    return 42")

        # Trailing fence only
        text3 = "    return 100\n```"
        self.assertEqual(clean_code_fence(text3), "    return 100")

    def test_stitch_without_overlap(self) -> None:
        part1 = "def part_one():\n    print('1')\n"
        part2 = "def part_two():\n    print('2')"
        stitched = stitch_continuation(part1, part2)
        expected = "def part_one():\n    print('1')\ndef part_two():\n    print('2')"
        self.assertEqual(stitched, expected)

    def test_stitch_with_exact_line_overlap(self) -> None:
        # Model repeats the last 2 lines in continuation
        part1 = (
            "def calculate(x, y):\n"
            "    step1 = x * 2\n"
            "    step2 = y + 10\n"
        )
        part2 = (
            "```python\n"
            "    step1 = x * 2\n"
            "    step2 = y + 10\n"
            "    return step1 + step2\n"
            "```"
        )
        stitched = stitch_continuation(part1, part2)
        expected = (
            "def calculate(x, y):\n"
            "    step1 = x * 2\n"
            "    step2 = y + 10\n"
            "    return step1 + step2"
        )
        self.assertEqual(stitched, expected)

    def test_stitch_with_partial_line_overlap(self) -> None:
        # Cut off mid-line: "    user_name = " -> "    user_name = get_user()"
        part1 = "def get_profile():\n    user_name = "
        part2 = "    user_name = get_user()\n    return user_name"
        stitched = stitch_continuation(part1, part2)
        self.assertIn("user_name = get_user()", stitched)
        self.assertNotIn("user_name =     user_name", stitched)


if __name__ == "__main__":
    unittest.main()
