"""
tests/test_pathsafe.py

Unit tests for safe_relpath path validation and normalization.
"""

import unittest
from core.pathsafe import safe_relpath


class TestSafeRelpath(unittest.TestCase):
    def test_valid_simple_filenames(self) -> None:
        self.assertEqual(safe_relpath("main.py"), "main.py")
        self.assertEqual(safe_relpath("README.md"), "README.md")
        self.assertEqual(safe_relpath("package.json"), "package.json")

    def test_valid_subdirectories(self) -> None:
        self.assertEqual(safe_relpath("src/app.py"), "src/app.py")
        self.assertEqual(safe_relpath("a/b/c/d.txt"), "a/b/c/d.txt")

    def test_normalizes_backslashes(self) -> None:
        self.assertEqual(safe_relpath("src\\components\\Button.tsx"), "src/components/Button.tsx")
        self.assertEqual(safe_relpath("app\\models.py"), "app/models.py")

    def test_empty_or_whitespace_raises(self) -> None:
        with self.assertRaises(ValueError):
            safe_relpath("")
        with self.assertRaises(ValueError):
            safe_relpath("   ")
        with self.assertRaises(ValueError):
            safe_relpath(None)  # type: ignore

    def test_absolute_paths_rejected(self) -> None:
        with self.assertRaises(ValueError):
            safe_relpath("/etc/passwd")
        with self.assertRaises(ValueError):
            safe_relpath("\\Windows\\System32")

    def test_drive_letters_rejected(self) -> None:
        with self.assertRaises(ValueError):
            safe_relpath("C:\\Users\\admin")
        with self.assertRaises(ValueError):
            safe_relpath("d:/projects/test.py")
        with self.assertRaises(ValueError):
            safe_relpath("c:test.py")

    def test_parent_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            safe_relpath("../secrets.txt")
        with self.assertRaises(ValueError):
            safe_relpath("src/../../etc/shadow")
        with self.assertRaises(ValueError):
            safe_relpath("foo/..")

    def test_windows_reserved_names_rejected(self) -> None:
        reserved = ["CON", "con.txt", "PRN", "prn.py", "AUX", "aux.json", "NUL", "nul.dat"]
        for name in reserved:
            with self.assertRaises(ValueError, msg=f"Should reject {name}"):
                safe_relpath(name)

        # COM1-COM9 and LPT1-LPT9
        with self.assertRaises(ValueError):
            safe_relpath("com1.txt")
        with self.assertRaises(ValueError):
            safe_relpath("LPT3")
        with self.assertRaises(ValueError):
            safe_relpath("nested/sub/COM5.bin")

    def test_similar_non_reserved_allowed(self) -> None:
        # Words that start with or contain reserved substrings but are not reserved
        self.assertEqual(safe_relpath("console.py"), "console.py")
        self.assertEqual(safe_relpath("contact.html"), "contact.html")
        self.assertEqual(safe_relpath("printer.py"), "printer.py")
        self.assertEqual(safe_relpath("auxiliary.ts"), "auxiliary.ts")
        self.assertEqual(safe_relpath("null_handler.go"), "null_handler.go")


if __name__ == "__main__":
    unittest.main()
