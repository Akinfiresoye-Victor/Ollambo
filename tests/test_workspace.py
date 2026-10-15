"""
tests/test_workspace.py - Unit tests for workspace service, project router, and context builder.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.project_router import classify_with_attachment, decide_project
import core.workspace as workspace
from core.workspace import (
    create_workspace,
    export_zip,
    get_workspace,
    list_files,
    list_workspaces,
    read_all,
    read_file,
    rebuild_index,
    slugify,
    unique_slug,
    workspace_path,
    write_file,
)
from core.workspace_context import build_attachment_context


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.patcher = patch("core.workspace._workspace_root", return_value=self.temp_dir)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_slugify_and_collisions(self):
        # Normal slugification
        self.assertEqual(slugify("My Cool Project!"), "my-cool-project")
        self.assertEqual(slugify("Hello   World  -- 123"), "hello-world-123")

        # Reserved Windows names
        con_slug = slugify("CON")
        self.assertNotEqual(con_slug, "con")
        self.assertTrue(con_slug.startswith("p-") or con_slug != "con")

        nul_slug = slugify("NUL")
        self.assertNotEqual(nul_slug, "nul")

        # Collision handling
        s1 = unique_slug(self.temp_dir, "my-game")
        (self.temp_dir / s1).mkdir(parents=True, exist_ok=True)
        s2 = unique_slug(self.temp_dir, "my-game")
        self.assertEqual(s2, "my-game-2")
        (self.temp_dir / s2).mkdir(parents=True, exist_ok=True)
        s3 = unique_slug(self.temp_dir, "my-game")
        self.assertEqual(s3, "my-game-3")

    def test_path_rejection(self):
        ws = create_workspace("Security Test")
        wid = ws["id"]

        with self.assertRaises(ValueError):
            write_file(wid, "../escaped.py", "malicious code")

        with self.assertRaises(ValueError):
            write_file(wid, "C:/Windows/System32/evil.dll", "malicious code")

        with self.assertRaises(ValueError):
            read_file(wid, "../escaped.py")

    def test_write_plus_backup(self):
        ws = create_workspace("Backup Test")
        wid = ws["id"]

        # Initial write
        ok = write_file(wid, "src/main.py", "print('version 1')")
        self.assertTrue(ok)
        self.assertEqual(read_file(wid, "src/main.py"), "print('version 1')")

        # Overwrite should create backup
        ok2 = write_file(wid, "src/main.py", "print('version 2')")
        self.assertTrue(ok2)
        self.assertEqual(read_file(wid, "src/main.py"), "print('version 2')")

        p = workspace_path(wid)
        self.assertIsNotNone(p)
        backup_dir = p / ".ollambo" / "backups" / "src"
        self.assertTrue(backup_dir.exists())

        backups = list(backup_dir.glob("main.py.*.bak"))
        self.assertGreaterEqual(len(backups), 1)
        self.assertIn("version 1", backups[0].read_text(encoding="utf-8"))

    def test_list_exclusion_of_ollambo(self):
        ws = create_workspace("Exclusion Test")
        wid = ws["id"]

        write_file(wid, "index.html", "<h1>Hello</h1>")
        write_file(wid, "css/style.css", "body { color: red; }")

        files = list_files(wid)
        self.assertIn("index.html", files)
        self.assertIn("css/style.css", files)

        # Internal and hidden files must NOT be listed
        for f in files:
            self.assertFalse(f.startswith(".ollambo"))
            self.assertNotIn(".ollambo", f)

    def test_zip_excludes_ollambo(self):
        ws = create_workspace("Zip Test")
        wid = ws["id"]
        write_file(wid, "app.py", "# code")

        zip_dest = self.temp_dir / "export.zip"
        ok = export_zip(wid, zip_dest)
        self.assertTrue(ok)
        self.assertTrue(zip_dest.exists())

        with zipfile.ZipFile(zip_dest, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("app.py", namelist)
            for name in namelist:
                self.assertNotIn(".ollambo", name)

    def test_index_rebuild(self):
        ws1 = create_workspace("Project 1", goal="Goal 1")
        ws2 = create_workspace("Project 2", goal="Goal 2")

        # Delete the central index
        index_file = self.temp_dir / "index.json"
        if index_file.exists():
            index_file.unlink()

        # Rebuild index should recover both workspaces
        entries = rebuild_index()
        self.assertEqual(len(entries), 2)
        ids = {e["id"] for e in entries}
        self.assertIn(ws1["id"], ids)
        self.assertIn(ws2["id"], ids)

        # list_workspaces uses the rebuilt index
        workspaces = list_workspaces()
        self.assertEqual(len(workspaces), 2)

    def test_router_decisions(self):
        game_summary = {
            "id": "game-123",
            "name": "Snake Game",
            "goal": "Classic snake game in python",
            "files": ["snake.py", "game.py"],
        }

        # 1. Game then "add a score counter" -> continue
        d1 = decide_project("add a score counter", game_summary)
        self.assertEqual(d1, "continue")

        # 2. Game then "build a website" -> ambiguous or new
        d2 = decide_project("build a website", game_summary)
        self.assertIn(d2, ["ambiguous", "new"])

        # 3. "start a new project" -> new
        d3 = decide_project("start a new project", game_summary)
        self.assertEqual(d3, "new")

        # 4. Attachment present -> continue
        d4 = decide_project("let's review the code", game_summary, has_attachment=True)
        self.assertEqual(d4, "continue")

        # 5. Strong new overrides attachment
        d5 = decide_project("start fresh from scratch", game_summary, has_attachment=True)
        self.assertEqual(d5, "new")

        # 6. Attachment classification
        self.assertEqual(classify_with_attachment("add highscore saving"), "code")
        self.assertEqual(classify_with_attachment("what does snake.py do?"), "chat")

    def test_context_budget_trimming(self):
        ws = create_workspace("Context Test")
        wid = ws["id"]
        write_file(wid, "main.py", "x = 1\n" * 50)
        write_file(wid, "large.py", "y = 2\n" * 500)

        # Budget of 40 tokens (only small file / header fits)
        ctx = build_attachment_context([wid], user_prompt="test", budget_tokens=40)
        self.assertIn("=== Attached Project: Context Test", ctx)
        self.assertIn("main.py", ctx)
        self.assertIn("large.py", ctx)
        # Verify large file was trimmed / omitted from body
        self.assertIn("omitted to fit context budget", ctx)


if __name__ == "__main__":
    unittest.main()
