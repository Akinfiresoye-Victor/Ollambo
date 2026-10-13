"""
tests/test_deep_research.py - Pure-logic unit tests for Deep Research feature.

Tests:
1. Quote verification (exact found, not found, fuzzy match)
2. Registered domain extraction & near-duplicate detection
3. Credibility scoring rules (deterministic signals)
4. Ledger grouping, status, and confidence calculation
5. Token budget trimming
6. Citation validation & post-checks
7. Mermaid syntax validation
8. Research store persistence (save, load, resume status, delete)
9. Markdown-to-HTML conversion security (html=False, script escaping, tables)
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.research_budget import (
    input_budget,
    trim_chunks_to_budget,
    trim_text_to_budget,
)
from core.research_engine import (
    are_near_duplicates,
    calculate_claim_confidence,
    credibility_score,
    extract_registered_domain,
    group_and_update_ledger,
    is_same_domain,
    jaccard_shingle_similarity,
    verify_quote,
)
from core.research_planner import ResearchPlan, SubQuestion, parse_plan_json
import core.research_store as research_store
from core.research_writer import (
    check_uncredited_findings,
    validate_citations,
    validate_mermaid_code,
)
from ui.report_viewer import markdown_to_report_html


class TestDeepResearch(unittest.TestCase):

    # -----------------------------------------------------------------------
    # 1. Quote Verification
    # -----------------------------------------------------------------------
    def test_quote_verification_found(self):
        page_text = (
            "The James Webb Space Telescope was launched on December 25, 2021, "
            "from Kourou, French Guiana, aboard an Ariane 5 rocket."
        )
        quote = "launched on December 25, 2021"
        self.assertTrue(verify_quote(quote, page_text))

    def test_quote_verification_not_found(self):
        page_text = (
            "The James Webb Space Telescope was launched on December 25, 2021."
        )
        quote = "launched in October 1998 aboard Apollo 11"
        self.assertFalse(verify_quote(quote, page_text))

    def test_quote_verification_fuzzy_match(self):
        # Slightly differing punctuation or whitespace
        page_text = (
            "The experiment demonstrated a 45.2% reduction in memory overhead, "
            "confirming the primary hypothesis."
        )
        quote = "demonstrated a 45.2% reduction in memory overhead confirming"
        self.assertTrue(verify_quote(quote, page_text, tolerance=0.8))

    # -----------------------------------------------------------------------
    # 2. Domain & Independence / Near-Duplicate Detection
    # -----------------------------------------------------------------------
    def test_registered_domain_and_same_domain(self):
        url1 = "https://subdomain.docs.python.org/3/library/ast.html"
        url2 = "https://wiki.python.org/moin/BeginnersGuide"
        url3 = "https://arxiv.org/abs/2301.00000"

        self.assertEqual(extract_registered_domain(url1), "python.org")
        self.assertEqual(extract_registered_domain(url2), "python.org")
        self.assertTrue(is_same_domain(url1, url2))
        self.assertFalse(is_same_domain(url1, url3))

    def test_near_duplicate_detection(self):
        orig_text = (
            "Quantum computers leverage superposition and entanglement to execute "
            "certain complex algorithms exponentially faster than classical computers. "
            "However, error correction and qubit coherence remain substantial hurdles."
        )
        syndicated = (
            "Quantum computers leverage superposition and entanglement to execute "
            "certain complex algorithms exponentially faster than classical computers. "
            "However error correction and qubit coherence remain huge hurdles."
        )
        different_text = (
            "Making sourdough bread requires wild yeast, lactic acid bacteria, "
            "flour, water, salt, and patient fermentation over several hours."
        )
        self.assertTrue(are_near_duplicates(orig_text, syndicated, threshold=0.5))
        self.assertFalse(are_near_duplicates(orig_text, different_text, threshold=0.5))

    # -----------------------------------------------------------------------
    # 3. Credibility Scoring
    # -----------------------------------------------------------------------
    def test_credibility_scoring_authoritative(self):
        score_gov, reason_gov = credibility_score("https://www.cdc.gov/flu/about/index.html")
        self.assertGreaterEqual(score_gov, 0.85)
        self.assertIn(".gov", reason_gov)

    def test_credibility_scoring_blog_http(self):
        score_blog, reason_blog = credibility_score("http://randomuser.blogspot.com/post")
        self.assertLessEqual(score_blog, 0.50)

    def test_credibility_author_date_detection(self):
        text = "Written by: John Doe on May 15, 2024. Today we analyze results."
        score, reason = credibility_score("https://example.com/article", text=text)
        self.assertIn("author", reason.lower())
        self.assertIn("date", reason.lower())

    # -----------------------------------------------------------------------
    # 4. Ledger Status & Confidence Calculation
    # -----------------------------------------------------------------------
    def test_ledger_confidence_corroborated(self):
        sources_map = {
            1: {"credibility": 0.90},
            2: {"credibility": 0.85},
        }
        conf, reason = calculate_claim_confidence(
            claim_status="corroborated",
            source_ids=[1, 2],
            sources_map=sources_map,
            independent_domains_count=2,
        )
        self.assertEqual(conf, "High")
        self.assertIn("Corroborated across 2", reason)

    def test_ledger_confidence_contested(self):
        sources_map = {1: {"credibility": 0.70}, 2: {"credibility": 0.70}}
        conf, _ = calculate_claim_confidence(
            claim_status="contested",
            source_ids=[1, 2],
            sources_map=sources_map,
            independent_domains_count=2,
        )
        self.assertEqual(conf, "Low")

    def test_ledger_grouping_logic(self):
        sources_map = {
            1: {"credibility": 0.90},
            2: {"credibility": 0.80},
        }
        ledger = []
        new_claim_1 = {
            "claim": "Atmospheric CO2 levels reached 420 parts per million.",
            "exact_quote": "CO2 levels reached 420 ppm",
            "source_id": 1,
            "url": "https://noaa.gov/co2",
        }
        ledger = group_and_update_ledger(ledger, [new_claim_1], sources_map, sq_index=1)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["status"], "single-source")

        # Second source with similar claim
        new_claim_2 = {
            "claim": "Atmospheric CO2 concentrations exceeded 420 parts per million.",
            "exact_quote": "concentrations exceeded 420 ppm",
            "source_id": 2,
            "url": "https://nature.com/articles/co2",
        }
        ledger = group_and_update_ledger(ledger, [new_claim_2], sources_map, sq_index=1)
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["status"], "corroborated")
        self.assertEqual(ledger[0]["confidence"], "High")

    # -----------------------------------------------------------------------
    # 5. Token Budget Trimming
    # -----------------------------------------------------------------------
    def test_token_budget_trimming(self):
        budget = input_budget(8192, fraction=0.55)
        self.assertLessEqual(budget, 8192)

        long_text = "word " * 10000
        trimmed = trim_text_to_budget(long_text, budget_tokens=500)
        self.assertLess(len(trimmed.split()), 1000)
        self.assertIn("truncated", trimmed)

        chunks = [
            {"text": "high score chunk " * 50, "score": 10.0},
            {"text": "medium score chunk " * 50, "score": 5.0},
            {"text": "low score chunk " * 1000, "score": 1.0},
        ]
        kept = trim_chunks_to_budget(chunks, budget_tokens=250)
        self.assertLessEqual(len(kept), 2)

    # -----------------------------------------------------------------------
    # 6. Citation Validation & Findings Check
    # -----------------------------------------------------------------------
    def test_citation_validation(self):
        text = "Finding A is confirmed [1][2]. Finding B is asserted [99]."
        valid_sids = {1, 2}
        cleaned, invalid = validate_citations(text, valid_sids)
        self.assertIn("[1][2]", cleaned)
        self.assertNotIn("[99]", cleaned)
        self.assertEqual(invalid, [99])

    def test_uncredited_findings_check(self):
        findings = (
            "## 2. Key Findings\n"
            "- Finding one with citation [1].\n"
            "- Finding two without citation at all."
        )
        uncredited = check_uncredited_findings(findings)
        self.assertEqual(len(uncredited), 1)
        self.assertIn("Finding two without citation", uncredited[0])

    # -----------------------------------------------------------------------
    # 7. Mermaid Validation
    # -----------------------------------------------------------------------
    def test_mermaid_validation_valid(self):
        valid_code = "flowchart TD\n    A[Start] --> B{Decision}\n    B -->|Yes| C[Done]"
        is_val, err = validate_mermaid_code(valid_code)
        self.assertTrue(is_val)

    def test_mermaid_validation_invalid_type(self):
        code = "foobar TD\n    A --> B"
        is_val, err = validate_mermaid_code(code)
        self.assertFalse(is_val)
        self.assertIn("Unknown diagram type", err)

    def test_mermaid_validation_unbalanced_brackets(self):
        code = "flowchart TD\n    A[Start --> B"
        is_val, err = validate_mermaid_code(code)
        self.assertFalse(is_val)
        self.assertIn("Unbalanced", err)

    def test_mermaid_validation_html_injection(self):
        code = "flowchart TD\n    A[<script>alert(1)</script>] --> B"
        is_val, err = validate_mermaid_code(code)
        self.assertFalse(is_val)
        self.assertIn("HTML", err)

    # -----------------------------------------------------------------------
    # 8. Research Store Persistence
    # -----------------------------------------------------------------------
    def test_research_store_save_load_delete(self):
        temp_dir = Path(tempfile.mkdtemp())
        try:
            with patch.object(research_store, "_research_root", return_value=temp_dir):
                # 1. Create
                pid = research_store.create_project("Quantum Key Distribution", depth="standard")
                projects = research_store.list_projects()
                self.assertEqual(len(projects), 1)
                self.assertEqual(projects[0]["id"], pid)

                # 2. Save & Load Plan
                plan_data = {"topic": "QKD", "main_question": "How secure is QKD?"}
                research_store.save_plan(pid, plan_data)
                loaded_plan = research_store.load_plan(pid)
                self.assertEqual(loaded_plan["main_question"], "How secure is QKD?")

                # 3. Save & Load Notes
                notes = [{"source_id": 1, "claims": [{"claim": "Secure"}]}]
                research_store.save_notes(pid, notes)
                self.assertEqual(len(research_store.load_notes(pid)), 1)

                # 4. Save & Load Status (Checkpoint)
                research_store.save_status(pid, {"phase": "researching", "sq_index": 2})
                status = research_store.load_status(pid)
                self.assertEqual(status["sq_index"], 2)

                # 5. Save & Load Report
                research_store.save_report(pid, "# Report Title\n\nContent")
                self.assertIn("Report Title", research_store.load_report(pid))

                # 6. Delete
                research_store.delete_project(pid)
                self.assertEqual(len(research_store.list_projects()), 0)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------------
    # 9. Markdown to HTML Conversion Security
    # -----------------------------------------------------------------------
    def test_markdown_to_html_security_and_tables(self):
        raw_md = (
            "# Research Findings\n\n"
            "| Claim | Source |\n"
            "|-------|--------|\n"
            "| Fact1 | [1]    |\n\n"
            "<details><summary>Quiz Question 1</summary>Answer 1</details>\n\n"
            "<script>evil_function()</script>\n\n"
            "```mermaid\nflowchart TD\n    A --> B\n```"
        )
        html = markdown_to_report_html(raw_md, mermaid_js_url="file:///assets/mermaid.min.js")
        # Script tags must be escaped, NEVER passed raw
        self.assertNotIn("<script>evil_function()</script>", html)
        self.assertIn("&lt;script&gt;evil_function()&lt;/script&gt;", html)
        # Tables must be present
        self.assertIn("<table>", html)
        self.assertIn("<th>Claim</th>", html)
        # Interactive quiz details/summary must be rendered
        self.assertIn("<details><summary>Quiz Question 1</summary>", html)
        # Mermaid block must be transformed to <pre class="mermaid"> with clean unescaped arrows
        self.assertIn('<pre class="mermaid">', html)
        self.assertIn("A --> B", html)
        self.assertNotIn("A --&gt; B", html)

    # -----------------------------------------------------------------------
    # 10. Web Engine Notifications
    # -----------------------------------------------------------------------
    def test_engine_web_notifications(self):
        plan = ResearchPlan(
            topic="WW2 Causes",
            main_question="What caused WW2?",
            sub_questions=[
                SubQuestion(
                    index=1,
                    question="Invasion of Poland",
                    why_it_matters="Spark of war",
                    search_queries=["invasion of poland 1939 causes"],
                    evidence_for="Germany invaded Poland",
                    evidence_against="Diplomatic treaty",
                    preferred_sources=[],
                )
            ],
        )
        notifications = []

        def dummy_progress(phase, step, total, msg):
            notifications.append((phase, msg))

        engine = research_store.ResearchEngine if hasattr(research_store, "ResearchEngine") else None
        from core.research_engine import ResearchEngine
        engine = ResearchEngine(
            project_id="test_proj",
            plan=plan,
            ollama_url="http://localhost:11434",
            model="llama3",
            progress_cb=dummy_progress,
        )
        with patch("core.research_engine.search", return_value=[{"url": "https://example.com/ww2", "title": "WW2"}]), \
             patch("core.research_engine.fetch_pages", return_value=[{"url": "https://example.com/ww2", "html": "<p>On September 1 1939 Germany invaded Poland starting the war.</p>"}]), \
             patch("core.research_engine.extract_evidence_from_source", return_value=[{"exact_quote": "Germany invaded Poland", "claim": "Invasion started war"}]):
            engine.run_subquestion(plan.sub_questions[0], 1, 1)

        search_notifs = [m for p, m in notifications if "🌐 Web Search:" in m]
        fetch_notifs = [m for p, m in notifications if "🌐 Web Fetch:" in m]
        self.assertTrue(len(search_notifs) >= 1)
        self.assertTrue(len(fetch_notifs) >= 1)


if __name__ == "__main__":
    unittest.main()
