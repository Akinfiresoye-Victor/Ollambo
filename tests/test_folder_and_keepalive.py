import unittest
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication
from ui.artifacts_panel import ArtifactsPanel
from ui.chat_top_bar import ChatTopBar
from core.ollama_client import OllamaClient
from core.walkthrough import generate_project_walkthrough, detect_entrypoint, detect_dependencies
from core.planner import CodingPlan, PlanStep

# Ensure QApplication instance exists for GUI tests
app = QApplication.instance() or QApplication([])


class TestFolderAndKeepAlive(unittest.TestCase):
    def test_artifacts_panel_folder_structure(self):
        panel = ArtifactsPanel()
        panel.add_file("src/utils/helpers.py", "def add(a, b): return a + b")
        panel.add_file("src/core/engine.py", "class Engine: pass")
        panel.add_file("README.md", "# My Project")

        self.assertIn("src/utils/helpers.py", panel._files)
        self.assertIn("src/core/engine.py", panel._files)
        self.assertIn("README.md", panel._files)

        # Check tree nodes
        self.assertIn("src", panel._tree_items)
        self.assertIn("src/utils", panel._tree_items)
        self.assertIn("src/utils/helpers.py", panel._tree_items)
        self.assertIn("src/core", panel._tree_items)
        self.assertIn("src/core/engine.py", panel._tree_items)
        self.assertIn("README.md", panel._tree_items)

        # Verify disk save with directory structures
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            for name, code in panel._files.items():
                parts = name.replace("\\", "/").lstrip("/").split("/")
                fp = out_dir.joinpath(*parts)
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(code, encoding="utf-8")

            self.assertTrue((out_dir / "src" / "utils" / "helpers.py").exists())
            self.assertTrue((out_dir / "src" / "core" / "engine.py").exists())
            self.assertTrue((out_dir / "README.md").exists())
            self.assertEqual((out_dir / "src" / "utils" / "helpers.py").read_text(), "def add(a, b): return a + b")

    def test_chat_top_bar_idle_countdown_and_paused_working(self):
        bar = ChatTopBar()
        self.assertFalse(bar._is_connected)
        self.assertIn("Standby", bar.lbl_connection.text())

        # When working, timer is paused and shows Active (working...)
        bar.set_working()
        self.assertTrue(bar._is_working)
        self.assertTrue(bar._is_connected)
        self.assertFalse(bar._keep_alive_timer.isActive())
        self.assertIn("Active", bar.lbl_connection.text())

        # When task finishes -> set_idle starts 3m idle window
        bar.set_idle()
        self.assertFalse(bar._is_working)
        self.assertTrue(bar._is_connected)
        self.assertEqual(bar._seconds_remaining, 180)
        self.assertIn("03:00", bar.lbl_connection.text())

        # 1 tick decrements by 1 second during idle
        bar._on_timer_tick()
        self.assertEqual(bar._seconds_remaining, 179)
        self.assertIn("02:59", bar.lbl_connection.text())

        # New task arrives -> pauses countdown again!
        bar.set_working()
        self.assertTrue(bar._is_working)
        self.assertFalse(bar._keep_alive_timer.isActive())
        self.assertIn("Active", bar.lbl_connection.text())

        # Manual disconnect / expiration
        bar.set_disconnected()
        self.assertFalse(bar._is_connected)
        self.assertIn("Standby", bar.lbl_connection.text())

    def test_ollama_client_preload_and_unload_3m(self):
        client = OllamaClient("http://localhost:11434")
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            # Test preload with 3m
            ok = client.preload_model("llama3", keep_alive="3m")
            self.assertTrue(ok)
            mock_post.assert_called_with(
                "/api/generate",
                json={"model": "llama3", "keep_alive": "3m"},
                timeout=unittest.mock.ANY,
            )

            # Test unload
            ok = client.unload_model("llama3")
            self.assertTrue(ok)
            mock_post.assert_called_with(
                "/api/generate",
                json={"model": "llama3", "keep_alive": 0},
                timeout=unittest.mock.ANY,
            )
        client.close()

    def test_walkthrough_generator(self):
        plan = CodingPlan(
            goal="Simple Calculator CLI",
            steps=[
                PlanStep(step=1, description="Create calculator logic", file="calc.py"),
                PlanStep(step=2, description="Create CLI entrypoint", file="main.py"),
            ]
        )
        files = {
            "calc.py": "def add(x, y): return x + y\n",
            "main.py": "import sys\nimport requests\nfrom calc import add\nif __name__ == '__main__':\n    print(add(2, 3))\n",
            "tests/test_calc.py": "import unittest\nfrom calc import add\n",
        }

        entrypoint = detect_entrypoint(files, plan)
        self.assertEqual(entrypoint, "main.py")

        deps = detect_dependencies(files)
        self.assertIn("requests", deps)

        walkthrough = generate_project_walkthrough(plan, files)
        self.assertIn("Simple Calculator CLI", walkthrough)
        self.assertIn("python main.py", walkthrough)
        self.assertIn("pip install requests", walkthrough)
        self.assertIn("python -m unittest discover tests", walkthrough)


if __name__ == "__main__":
    unittest.main()
