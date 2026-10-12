"""
Unit tests for context manager pure logic (token estimation, compaction, fallbacks).
Compatible with both pytest and python -m unittest.
"""

import unittest

from core.context_manager import (
    build_compacted_messages,
    estimate_messages_tokens,
    estimate_tokens,
    fallback_truncate_messages,
    is_compaction_needed,
    sanitize_code_for_summary,
    split_for_compaction,
)


class TestContextManager(unittest.TestCase):

    def test_estimate_tokens(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("a"), 1)
        # 16 characters -> approx 4 tokens
        self.assertEqual(estimate_tokens("1234567890123456"), 4)

    def test_estimate_messages_tokens(self) -> None:
        msgs = [
            {"role": "user", "content": "Hello world!"},  # 12 chars -> 3 tokens + 4 overhead = 7
            {"role": "assistant", "content": "Hi there!"},  # 9 chars -> 2 tokens + 4 overhead = 6
        ]
        total = estimate_messages_tokens(msgs)
        self.assertEqual(total, 13)

    def test_is_compaction_needed(self) -> None:
        # 70% of 1000 = 700
        self.assertFalse(is_compaction_needed(699, 1000, 0.70))
        self.assertTrue(is_compaction_needed(700, 1000, 0.70))
        self.assertTrue(is_compaction_needed(800, 1000, 0.70))

    def test_sanitize_code_for_summary(self) -> None:
        text = (
            "Here is the code:\n"
            "```python\ndef add(a, b):\n    return a + b\n```\n"
            "That's all."
        )
        sanitized = sanitize_code_for_summary(text)
        self.assertNotIn("def add", sanitized)
        self.assertIn("[Code artifact: python]", sanitized)
        self.assertIn("That's all.", sanitized)

    def test_split_for_compaction(self) -> None:
        msgs = [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "msg 1"},
            {"role": "assistant", "content": "msg 2"},
            {"role": "user", "content": "msg 3"},
            {"role": "assistant", "content": "msg 4"},
            {"role": "user", "content": "msg 5"},
            {"role": "assistant", "content": "msg 6"},
        ]
        sys_msgs, to_summarize, recent = split_for_compaction(msgs, keep_recent=4)
        self.assertEqual(len(sys_msgs), 1)
        self.assertEqual(sys_msgs[0]["content"], "You are a helper.")
        self.assertEqual(len(to_summarize), 2)
        self.assertEqual(to_summarize[0]["content"], "msg 1")
        self.assertEqual(to_summarize[1]["content"], "msg 2")
        self.assertEqual(len(recent), 4)
        self.assertEqual(recent[0]["content"], "msg 3")
        self.assertEqual(recent[-1]["content"], "msg 6")

    def test_build_compacted_messages(self) -> None:
        sys_msgs = [{"role": "system", "content": "System prompt"}]
        recent = [{"role": "user", "content": "Recent question"}]
        compacted = build_compacted_messages(sys_msgs, "Users discussed math.", recent)

        self.assertEqual(len(compacted), 3)
        self.assertEqual(compacted[0], sys_msgs[0])
        self.assertEqual(compacted[1]["role"], "system")
        self.assertIn("Users discussed math.", compacted[1]["content"])
        self.assertEqual(compacted[2], recent[0])

    def test_fallback_truncate_messages(self) -> None:
        msgs = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Old 1"},
            {"role": "assistant", "content": "Old 2"},
            {"role": "user", "content": "New 1"},
            {"role": "assistant", "content": "New 2"},
        ]
        truncated = fallback_truncate_messages(msgs, keep_recent=2)
        self.assertEqual(len(truncated), 3)
        self.assertEqual(truncated[0]["content"], "System prompt")
        self.assertEqual(truncated[1]["content"], "New 1")
        self.assertEqual(truncated[2]["content"], "New 2")

    def test_build_extractive_summary(self) -> None:
        from core.context_manager import build_extractive_summary
        msgs = [
            {"role": "user", "content": "How do I build a FastAPI app?"},
            {"role": "assistant", "content": "You can install fastapi and uvicorn."},
        ]
        summary = build_extractive_summary(msgs)
        self.assertIn("User: How do I build a FastAPI app?", summary)
        self.assertIn("Assistant: You can install fastapi and uvicorn.", summary)


if __name__ == "__main__":
    unittest.main()
