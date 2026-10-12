"""
Unit tests for autonomous task intent classifier (code, chat, or both).
"""

import unittest
from unittest.mock import MagicMock, patch

from core.task_classifier import (
    classify_heuristic,
    classify_task,
    classify_with_llm,
)


class TestTaskClassifier(unittest.TestCase):
    def test_pure_code_prompts(self):
        code_prompts = [
            "build a flappy bird game in pygame",
            "create a python script to download files",
            "write code for a binary search tree",
            "implement a rest api with fastapi",
            "make a discord bot in python",
            "fix this syntax error: def test(:",
            "scaffold a react dashboard application",
        ]
        for p in code_prompts:
            verdict = classify_heuristic(p)
            self.assertEqual(verdict, "code", f"Failed for: {p}")

    def test_pure_chat_prompts(self):
        chat_prompts = [
            "hello ollambo, how are you?",
            "what is the capital of France?",
            "explain the theory of general relativity",
            "why is the sky blue?",
            "tell me a joke",
            "what is the difference between a mutex and a semaphore?",
            "who invented python?",
        ]
        for p in chat_prompts:
            verdict = classify_heuristic(p)
            self.assertEqual(verdict, "chat", f"Failed for: {p}")

    def test_hybrid_both_prompts(self):
        both_prompts = [
            "explain how quicksort works and build a python visualizer for it",
            "what is an LRU cache? explain it and code a full implementation with tests",
            "explain microservices architecture and implement a sample flask service",
            "describe the observer pattern and write a program demonstrating it",
        ]
        for p in both_prompts:
            verdict = classify_heuristic(p)
            self.assertEqual(verdict, "both", f"Failed for: {p}")

    @patch("core.task_classifier.OllamaClient")
    def test_llm_fallback_for_ambiguous_prompt(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.chat_stream.return_value = [
            {"message": {"content": "code"}, "done": True}
        ]

        # Ambiguous prompt
        res = classify_with_llm("http://localhost:11434", "llama3", "Need something for inventory")
        self.assertEqual(res, "code")


if __name__ == "__main__":
    unittest.main()
