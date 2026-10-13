"""
tests/test_web_research.py

Pure-logic unit tests for core.web_research.
All network functions are mocked so tests need NO internet access.
Tests cover:
  - BM25 tokenizer, IDF, scorer
  - chunk_and_rank
  - is_private_address blocking
  - disk cache expiry
  - build_research_prompt structure
  - parse_followup_queries
  - format_sources_list deduplication
"""

import time
import unittest
from unittest.mock import MagicMock, patch


class TestTokenizer(unittest.TestCase):
    def test_basic_tokenize(self) -> None:
        from core.web_research import _tokenize
        result = _tokenize("Hello World! This is a test-case.")
        self.assertIn("hello", result)
        self.assertIn("world", result)
        self.assertIn("this", result)
        self.assertIn("test", result)
        self.assertIn("case", result)
        # Single-char tokens should be filtered
        self.assertNotIn("a", result)

    def test_tokenize_empty(self) -> None:
        from core.web_research import _tokenize
        self.assertEqual(_tokenize(""), [])


class TestBM25(unittest.TestCase):
    def test_idf_penalises_common_terms(self) -> None:
        from core.web_research import _build_idf
        corpus = [["python", "code"], ["python", "java"], ["ruby", "code"]]
        idf = _build_idf(corpus)
        # "python" appears in 2 of 3 docs; "ruby" in 1 — ruby should have higher IDF
        self.assertGreater(idf.get("ruby", 0), idf.get("python", 0))

    def test_bm25_relevant_doc_scores_higher(self) -> None:
        from core.web_research import _bm25_score, _build_idf, _tokenize
        corpus = [
            _tokenize("python programming language"),
            _tokenize("java coffee beans drink"),
        ]
        idf = _build_idf(corpus)
        avg_dl = sum(len(d) for d in corpus) / len(corpus)
        query = _tokenize("python programming")
        s0 = _bm25_score(query, corpus[0], idf, avg_dl)
        s1 = _bm25_score(query, corpus[1], idf, avg_dl)
        self.assertGreater(s0, s1)


class TestChunkAndRank(unittest.TestCase):
    def _make_pages(self) -> list[dict]:
        return [
            {
                "url": "https://example.com/python",
                "title": "Python page",
                "text": " ".join(["python programming language"] * 80),
            },
            {
                "url": "https://example.com/java",
                "title": "Java page",
                "text": " ".join(["java coffee drink beans"] * 80),
            },
        ]

    def test_top_chunk_matches_question(self) -> None:
        from core.web_research import chunk_and_rank
        pages = self._make_pages()
        chunks = chunk_and_rank("python programming", pages, top_k=2)
        self.assertTrue(len(chunks) > 0)
        # Top chunk should be from python page
        self.assertEqual(chunks[0]["url"], "https://example.com/python")

    def test_returns_at_most_top_k(self) -> None:
        from core.web_research import chunk_and_rank
        pages = self._make_pages()
        chunks = chunk_and_rank("python", pages, chunk_words=50, top_k=3)
        self.assertLessEqual(len(chunks), 3)

    def test_empty_pages_returns_empty(self) -> None:
        from core.web_research import chunk_and_rank
        self.assertEqual(chunk_and_rank("anything", []), [])

    def test_short_chunks_filtered(self) -> None:
        from core.web_research import chunk_and_rank
        pages = [{"url": "https://x.com", "title": "", "text": "hi"}]
        result = chunk_and_rank("hi", pages)
        # Text "hi" is too short (< 80 chars)
        self.assertEqual(result, [])


class TestPrivateAddressBlocking(unittest.TestCase):
    def test_localhost_blocked(self) -> None:
        from core.web_research import is_private_address
        self.assertTrue(is_private_address("http://localhost/admin"))
        self.assertTrue(is_private_address("http://127.0.0.1/data"))

    def test_private_ip_blocked(self) -> None:
        from core.web_research import is_private_address
        self.assertTrue(is_private_address("http://192.168.1.1/secret"))
        self.assertTrue(is_private_address("http://10.0.0.5/internal"))
        self.assertTrue(is_private_address("http://172.20.5.3/api"))

    def test_non_http_blocked(self) -> None:
        from core.web_research import is_private_address
        self.assertTrue(is_private_address("ftp://example.com/file.txt"))
        self.assertTrue(is_private_address("file:///etc/passwd"))

    def test_public_url_allowed(self) -> None:
        from core.web_research import is_private_address
        # Patch gethostbyname so test doesn't need internet
        with patch("socket.gethostbyname", return_value="93.184.216.34"):
            self.assertFalse(is_private_address("https://example.com/page"))


class TestCacheExpiry(unittest.TestCase):
    def test_cache_set_and_get(self) -> None:
        from core.web_research import _cache_get, _cache_key, _cache_set
        key = _cache_key("test_cache_key_unique_12345")
        _cache_set(key, {"data": "hello"})
        result = _cache_get(key)
        self.assertIsNotNone(result)
        self.assertEqual(result["data"], "hello")

    def test_expired_cache_returns_none(self) -> None:
        from core.web_research import _cache_dir, _cache_get, _cache_key
        import json

        key = _cache_key("test_expired_cache_unique_99999")
        path = _cache_dir() / f"{key}.json"
        # Write with ts in the past (25 hours ago)
        path.write_text(
            json.dumps({"ts": time.time() - 90_000, "payload": "stale"}),
            encoding="utf-8",
        )
        result = _cache_get(key)
        self.assertIsNone(result)
        # File should be deleted
        self.assertFalse(path.exists())


class TestBuildResearchPrompt(unittest.TestCase):
    def test_prompt_contains_sources(self) -> None:
        from core.web_research import build_research_prompt
        chunks = [
            {"title": "Python Docs", "url": "https://docs.python.org", "text": "Python is a programming language."},
        ]
        prompt = build_research_prompt("What is Python?", chunks)
        self.assertIn("[1]", prompt)
        self.assertIn("Python Docs", prompt)
        self.assertIn("https://docs.python.org", prompt)
        self.assertIn("QUESTION: What is Python?", prompt)

    def test_prompt_empty_chunks(self) -> None:
        from core.web_research import build_research_prompt
        prompt = build_research_prompt("anything", [])
        self.assertIn("No sources retrieved", prompt)

    def test_prompt_has_safety_rules(self) -> None:
        from core.web_research import build_research_prompt
        prompt = build_research_prompt("q", [{"title": "T", "url": "u", "text": "x" * 200}])
        self.assertIn("untrusted", prompt.lower())
        self.assertIn("cite", prompt.lower())


class TestParseFollowupQueries(unittest.TestCase):
    def test_extracts_two_queries(self) -> None:
        from core.web_research import parse_followup_queries
        text = "SEARCH: python async programming\nSEARCH: asyncio tutorial 2024\n"
        queries = parse_followup_queries(text)
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0], "python async programming")
        self.assertEqual(queries[1], "asyncio tutorial 2024")

    def test_caps_at_two(self) -> None:
        from core.web_research import parse_followup_queries
        text = "SEARCH: q1\nSEARCH: q2\nSEARCH: q3\n"
        self.assertEqual(len(parse_followup_queries(text)), 2)

    def test_no_queries(self) -> None:
        from core.web_research import parse_followup_queries
        self.assertEqual(parse_followup_queries("nothing here"), [])

    def test_case_insensitive(self) -> None:
        from core.web_research import parse_followup_queries
        queries = parse_followup_queries("search: hello world\n")
        self.assertEqual(queries, ["hello world"])


class TestFormatSourcesList(unittest.TestCase):
    def test_deduplicates_urls(self) -> None:
        from core.web_research import format_sources_list
        chunks = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://a.com", "title": "A duplicate"},
            {"url": "https://b.com", "title": "B"},
        ]
        result = format_sources_list(chunks)
        # Should appear exactly once
        self.assertEqual(result.count("https://a.com"), 1)
        self.assertEqual(result.count("https://b.com"), 1)

    def test_no_chunks(self) -> None:
        from core.web_research import format_sources_list
        result = format_sources_list([])
        self.assertIn("## Sources", result)


if __name__ == "__main__":
    unittest.main()
