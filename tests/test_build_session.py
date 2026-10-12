"""
Unit tests for build session persistence, recovery, and plan reconstruction.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.build_session import (
    complete_session,
    get_resume_step,
    load_session,
    reconstruct_plan,
    save_session,
)
from core.planner import CodingPlan, PlanStep
from core.storage import (
    delete_work_session,
    list_active_sessions,
    load_chat,
    load_work_session,
    save_chat,
    save_work_session,
)


class TestBuildSession(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir_patch = patch("core.storage._data_dir", return_value=Path(self.temp_dir.name))
        self.data_dir_patch.start()

    def tearDown(self):
        self.data_dir_patch.stop()
        self.temp_dir.cleanup()

    def test_save_and_load_work_session(self):
        session_data = {
            "chat_id": "chat-123",
            "goal": "Build app",
            "completed": False,
            "next_step": 2,
        }
        save_work_session(session_data)
        loaded = load_work_session("chat-123")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["goal"], "Build app")
        self.assertIn("chat-123", list_active_sessions())

        delete_work_session("chat-123")
        self.assertIsNone(load_work_session("chat-123"))
        self.assertNotIn("chat-123", list_active_sessions())

    def test_build_session_manager_flow(self):
        plan = CodingPlan(
            goal="Create REST API",
            assumptions=["Python 3.12"],
            files=["app.py", "models.py"],
            steps=[
                PlanStep(1, "Create models", "models.py", completed=True),
                PlanStep(2, "Create app", "app.py", completed=False),
            ],
        )
        build_files = {"models.py": "class User: pass"}

        save_session("chat-456", plan, build_files, 2)
        sess = load_session("chat-456")
        self.assertIsNotNone(sess)
        self.assertEqual(sess["chat_id"], "chat-456")

        reconstructed = reconstruct_plan(sess)
        self.assertEqual(reconstructed.goal, "Create REST API")
        self.assertEqual(len(reconstructed.steps), 2)
        self.assertTrue(reconstructed.steps[0].completed)
        self.assertFalse(reconstructed.steps[1].completed)

        resume_step = get_resume_step(reconstructed)
        self.assertEqual(resume_step, 2)

        complete_session("chat-456")
        self.assertIsNone(load_session("chat-456"))

    def test_save_chat_with_plan_and_artifacts(self):
        chat_data = {
            "id": "chat-789",
            "title": "Build Calculator",
            "messages": [{"role": "user", "content": "Make calculator"}],
            "artifacts": {"calc.py": "def add(a, b): return a + b"},
            "plan": {
                "goal": "Make calculator",
                "steps": [{"step": 1, "description": "Calc logic", "file": "calc.py", "completed": True}],
            },
        }
        save_chat(chat_data)
        loaded = load_chat("chat-789")
        self.assertIsNotNone(loaded)
        self.assertIn("calc.py", loaded["artifacts"])
        self.assertEqual(loaded["plan"]["goal"], "Make calculator")


if __name__ == "__main__":
    unittest.main()
