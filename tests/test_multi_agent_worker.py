"""
Unit tests for multi-agent parallel code generation in CodeWorker.
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from core.planner import CodingPlan, PlanStep
from workers.code_worker import CodeWorker

# Ensure QApplication exists for QThread tests
if not QApplication.instance():
    _app = QApplication(sys.argv)


class TestMultiAgentWorker(unittest.TestCase):
    @patch("workers.code_worker.generate_single_file")
    @patch("workers.code_worker.test_and_repair")
    def test_multi_agent_parallel_execution(self, mock_test_and_repair, mock_generate):
        mock_generate.side_effect = lambda **kwargs: f"# Code for {kwargs['step'].file}"
        mock_test_and_repair.side_effect = lambda **kwargs: (kwargs["code"], True, "Passed")

        plan = CodingPlan(
            goal="Build website",
            steps=[
                PlanStep(1, "Create HTML", "index.html"),
                PlanStep(2, "Create CSS", "style.css"),
                PlanStep(3, "Create JS", "main.js"),
            ],
        )

        worker = CodeWorker(
            ollama_url="http://localhost:11434",
            model="llama3",
            plan=plan,
            max_agents=2,
            auto_test=True,
        )

        from PySide6.QtCore import Qt
        completed_files = {}
        worker.file_completed.connect(
            lambda f, code, s: completed_files.update({f: code}),
            Qt.ConnectionType.DirectConnection,
        )

        worker.run()

        self.assertEqual(len(completed_files), 3)
        self.assertIn("index.html", completed_files)
        self.assertIn("style.css", completed_files)
        self.assertIn("main.js", completed_files)
        self.assertEqual(mock_generate.call_count, 3)
        self.assertEqual(mock_test_and_repair.call_count, 3)


if __name__ == "__main__":
    unittest.main()
