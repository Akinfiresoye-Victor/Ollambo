"""
tests/test_chat_title.py

Unit tests for AI conversation title generation and cleaning.
"""

import unittest
from unittest.mock import MagicMock, patch

from core.chat_title import clean_title, generate_chat_title


class TestCleanTitle(unittest.TestCase):
    def test_plain_title(self) -> None:
        self.assertEqual(clean_title("PostgreSQL Active Users Script"), "PostgreSQL Active Users Script")

    def test_strip_prefix_title(self) -> None:
        self.assertEqual(clean_title("Title: Python Discord Bot"), "Python Discord Bot")
        self.assertEqual(clean_title("title: Python Discord Bot"), "Python Discord Bot")
        self.assertEqual(clean_title("Chat Title: Discord Bot"), "Discord Bot")
        self.assertEqual(clean_title("Conversation Title: Discord Bot"), "Discord Bot")

    def test_strip_prefix_topic_and_subject(self) -> None:
        self.assertEqual(clean_title("Topic: Quantum Computing Basics"), "Quantum Computing Basics")
        self.assertEqual(clean_title("Subject: React Routing Setup"), "React Routing Setup")
        self.assertEqual(clean_title("Summary: Machine Learning Pipeline"), "Machine Learning Pipeline")

    def test_strip_conversational_intro(self) -> None:
        self.assertEqual(clean_title("Here is a title: Weather Forecast App"), "Weather Forecast App")
        self.assertEqual(clean_title("Here's a concise title: Weather App"), "Weather App")
        self.assertEqual(clean_title("Here is a suggested chat title: Weather App"), "Weather App")

    def test_strip_quotes_and_markdown(self) -> None:
        self.assertEqual(clean_title('"Flask REST API"'), "Flask REST API")
        self.assertEqual(clean_title("'FastAPI Tutorial'"), "FastAPI Tutorial")
        self.assertEqual(clean_title("`Next.js Deployment`"), "Next.js Deployment")
        self.assertEqual(clean_title("**Docker Compose Setup**"), "Docker Compose Setup")
        self.assertEqual(clean_title("### Kubernetes Cluster"), "Kubernetes Cluster")

    def test_strip_trailing_punctuation(self) -> None:
        self.assertEqual(clean_title("Data Analysis with Pandas."), "Data Analysis with Pandas")
        self.assertEqual(clean_title("Data Analysis with Pandas:"), "Data Analysis with Pandas")
        self.assertEqual(clean_title("Data Analysis with Pandas!"), "Data Analysis with Pandas")

    def test_multiline_output_takes_first_line(self) -> None:
        raw = "Git Branch Management\n\nThis title describes the user's question about branching."
        self.assertEqual(clean_title(raw), "Git Branch Management")

    def test_whitespace_normalization(self) -> None:
        self.assertEqual(clean_title("   Rust   Async    Programming   "), "Rust Async Programming")

    def test_empty_and_invalid(self) -> None:
        self.assertEqual(clean_title(""), "")
        self.assertEqual(clean_title("   "), "")
        self.assertEqual(clean_title(":::"), "")

    def test_length_truncation(self) -> None:
        long_title = "Building a Very Complex Distributed Real-Time Microservice Architecture"
        cleaned = clean_title(long_title, max_chars=35)
        self.assertTrue(len(cleaned) <= 36)
        self.assertTrue(cleaned.endswith("…"))


class TestGenerateChatTitle(unittest.TestCase):
    def test_empty_prompt_returns_empty(self) -> None:
        self.assertEqual(generate_chat_title("", model="llama3"), "")

    def test_empty_model_returns_empty(self) -> None:
        self.assertEqual(generate_chat_title("How do I use Docker?", model=""), "")

    def test_generate_from_string(self) -> None:
        mock_client = MagicMock()
        mock_client.chat_stream.return_value = [
            {"message": {"content": "Title: "}, "done": False},
            {"message": {"content": "Docker Container Setup"}, "done": True},
        ]
        result = generate_chat_title(
            "How do I set up Docker containers for Python?",
            model="llama3",
            client=mock_client,
        )
        self.assertEqual(result, "Docker Container Setup")
        mock_client.chat_stream.assert_called_once()

    def test_generate_from_messages_list(self) -> None:
        mock_client = MagicMock()
        mock_client.chat_stream.return_value = [
            {"message": {"content": "Kafka Event Streaming"}, "done": True},
        ]
        messages = [
            {"role": "user", "content": "Explain Apache Kafka architecture"},
            {"role": "assistant", "content": "Kafka is a distributed streaming platform..."},
            {"role": "user", "content": "How do consumer groups work?"},
        ]
        result = generate_chat_title(messages, model="llama3", client=mock_client)
        self.assertEqual(result, "Kafka Event Streaming")


class TestTitleWorker(unittest.TestCase):
    @patch("workers.title_worker.generate_chat_title")
    def test_title_worker_run(self, mock_gen) -> None:
        from workers.title_worker import TitleWorker
        mock_gen.return_value = "Golang Concurrency Patterns"

        worker = TitleWorker(
            chat_id="test-chat-1",
            ollama_url="http://localhost:11434",
            model="llama3",
            prompt="Explain goroutines and channels in Go",
        )

        received_results = []
        worker.title_ready.connect(lambda cid, title: received_results.append((cid, title)))
        worker.run()

        self.assertEqual(len(received_results), 1)
        self.assertEqual(received_results[0], ("test-chat-1", "Golang Concurrency Patterns"))


if __name__ == "__main__":
    unittest.main()
