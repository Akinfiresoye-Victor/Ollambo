"""
Unit tests for laptop resource optimizations:
  - Low-power mode settings and defaults
  - Context size (num_ctx) automatic calculation and clamping
  - Shared thread resolver (letting Ollama choose by default)
  - Configurable keep_alive duration and low-power shortening
  - Error line number extraction and surrounding code snippet extraction
  - Hardware RAM capabilities detection
"""

import unittest
from unittest.mock import patch

from core.code_tester import extract_error_line_number, get_surrounding_code_snippet
from core.setup_checker import check_hardware_capabilities
from core.storage import (
    DEFAULT_SETTINGS,
    calculate_auto_num_ctx,
    get_configured_threads,
    get_effective_keep_alive,
    get_effective_keep_alive_seconds,
    parse_keep_alive_seconds,
    resolve_num_ctx,
)


class TestLaptopOptimizations(unittest.TestCase):

    def test_default_settings_parallelism_and_low_power(self) -> None:
        """Verify default settings match laptop optimization requirements."""
        self.assertEqual(DEFAULT_SETTINGS.get("max_agents"), 1)
        self.assertFalse(DEFAULT_SETTINGS.get("low_power_mode"))
        self.assertFalse(DEFAULT_SETTINGS.get("use_model_summaries"))
        self.assertEqual(DEFAULT_SETTINGS.get("keep_alive"), "3m")
        self.assertIsNone(DEFAULT_SETTINGS.get("num_thread"))

    def test_extract_error_line_number(self) -> None:
        """Verify regex extraction of line numbers from various error strings."""
        err1 = "SyntaxError in `math.py` (line 42, col 5): invalid syntax"
        self.assertEqual(extract_error_line_number(err1), 42)

        err2 = 'File "test_app.py", line 128, in run_test\nZeroDivisionError'
        self.assertEqual(extract_error_line_number(err2), 128)

        err3 = "JSONDecodeError in `config.json` at line 7, col 2: Expecting value"
        self.assertEqual(extract_error_line_number(err3), 7)

        err4 = "General runtime failure with no line information"
        self.assertIsNone(extract_error_line_number(err4))

        self.assertIsNone(extract_error_line_number(""))

    def test_get_surrounding_code_snippet(self) -> None:
        """Verify ~40 surrounding lines extraction around error location."""
        code = "\n".join(f"line_{i} = {i}" for i in range(1, 101))

        # Centered around line 50
        snippet, start, end = get_surrounding_code_snippet(code, line_no=50, window=40)
        lines = snippet.splitlines()
        self.assertEqual(len(lines), 40)
        self.assertEqual(start, 29)
        self.assertEqual(end, 69)
        self.assertIn("line_50", snippet)

        # Near top (line 5)
        snippet_top, start_top, end_top = get_surrounding_code_snippet(code, line_no=5, window=40)
        self.assertEqual(start_top, 0)
        self.assertEqual(end_top, 40)
        self.assertIn("line_1", snippet_top)

        # Small file (< 40 lines)
        small_code = "a = 1\nb = 2\nc = 3"
        snippet_small, start_small, end_small = get_surrounding_code_snippet(small_code, line_no=2, window=40)
        self.assertEqual(snippet_small, small_code)
        self.assertEqual(start_small, 0)
        self.assertEqual(end_small, 3)

    def test_calculate_auto_num_ctx(self) -> None:
        """Verify auto num_ctx selection based on RAM and model limit."""
        # 16 GB RAM, model max 32768
        self.assertEqual(calculate_auto_num_ctx(available_ram_gb=16.0, model_max_ctx=32768), 16384)

        # 8 GB RAM, model max 32768
        self.assertEqual(calculate_auto_num_ctx(available_ram_gb=8.0, model_max_ctx=32768), 8192)

        # Clamped to model's real limit (e.g. 4096)
        self.assertEqual(calculate_auto_num_ctx(available_ram_gb=16.0, model_max_ctx=4096), 4096)

        # Low power mode chooses smaller auto context
        self.assertEqual(calculate_auto_num_ctx(available_ram_gb=8.0, model_max_ctx=32768, low_power=True), 4096)
        self.assertEqual(calculate_auto_num_ctx(available_ram_gb=4.0, model_max_ctx=32768, low_power=True), 2048)

    def test_resolve_num_ctx_user_override_and_clamp(self) -> None:
        """Verify user setting override respects model real limit."""
        settings = {"num_ctx": 4096, "low_power_mode": False}
        self.assertEqual(resolve_num_ctx(settings, model_max_ctx=8192), 4096)

        # User setting higher than model's limit is clamped
        settings_high = {"num_ctx": 16384, "low_power_mode": False}
        self.assertEqual(resolve_num_ctx(settings_high, model_max_ctx=8192), 8192)

    def test_get_configured_threads(self) -> None:
        """Verify thread resolver returns None unless explicitly set by user."""
        self.assertIsNone(get_configured_threads({}))
        self.assertIsNone(get_configured_threads({"num_thread": None}))
        self.assertIsNone(get_configured_threads({"num_thread": 0}))
        self.assertEqual(get_configured_threads({"num_thread": 4}), 4)
        self.assertEqual(get_configured_threads({"num_thread": "6"}), 6)

    def test_parse_keep_alive_and_effective_duration(self) -> None:
        """Verify parsing of keep_alive durations in seconds and low_power shortening."""
        self.assertEqual(parse_keep_alive_seconds("3m"), 180)
        self.assertEqual(parse_keep_alive_seconds("1m"), 60)
        self.assertEqual(parse_keep_alive_seconds("90s"), 90)
        self.assertEqual(parse_keep_alive_seconds(120), 120)

        # In standard mode: configured value
        self.assertEqual(get_effective_keep_alive({"keep_alive": "5m", "low_power_mode": False}), "5m")
        self.assertEqual(get_effective_keep_alive_seconds({"keep_alive": "5m", "low_power_mode": False}), 300)

        # In low-power mode: shortened to 1 minute
        self.assertEqual(get_effective_keep_alive({"keep_alive": "5m", "low_power_mode": True}), "1m")
        self.assertEqual(get_effective_keep_alive_seconds({"keep_alive": "5m", "low_power_mode": True}), 60)

    @patch("core.storage.get_system_ram_gb")
    def test_hardware_check_recommendations(self, mock_ram) -> None:
        """Verify low RAM check suggests low-power mode and 1.5B–3B model."""
        mock_ram.return_value = 8.0
        hw = check_hardware_capabilities()
        self.assertTrue(hw["is_low_ram"])
        self.assertTrue(hw["suggest_low_power"])
        self.assertIn("1.5b", hw["recommended_model"])

        mock_ram.return_value = 16.0
        hw_high = check_hardware_capabilities()
        self.assertFalse(hw_high["is_low_ram"])
        self.assertFalse(hw_high["suggest_low_power"])


if __name__ == "__main__":
    unittest.main()
