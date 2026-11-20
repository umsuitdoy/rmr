"""Unit tests for RMR Core Path Guard (TASK-03)."""

import unittest
from pathlib import Path

from rmr.core.path_guard import resolve_read_path


class TestPathGuard(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent

    # --- Tests for resolve_read_path ---

    def test_resolve_read_path_root_and_empty(self):
        root = self.repo_root
        self.assertEqual(resolve_read_path(root), root)
        self.assertEqual(resolve_read_path(root, None), root)
        self.assertEqual(resolve_read_path(root, ""), root)

    def test_resolve_read_path_valid_subpath(self):
        root = self.repo_root
        target = resolve_read_path(root, "pyproject.toml")
        self.assertEqual(target, root / "pyproject.toml")
        self.assertTrue(target.is_file())

        core_dir = resolve_read_path(root, "src/rmr/core")
        self.assertEqual(core_dir, root / "src" / "rmr" / "core")
        self.assertTrue(core_dir.is_dir())

    def test_resolve_read_path_nonexistent_inside_root(self):
        root = self.repo_root
        with self.assertRaises(FileNotFoundError):
            resolve_read_path(root, "nonexistent_file_xyz.py")

    def test_resolve_read_path_traversal_blocked(self):
        root = self.repo_root
        with self.assertRaises((PermissionError, FileNotFoundError)):
            resolve_read_path(root, "../../etc/passwd")

        # Existing path outside root must raise PermissionError
        with self.assertRaises(PermissionError):
            resolve_read_path(root, "../..")

    def test_resolve_read_path_absolute_escape_blocked(self):
        root = self.repo_root
        # Try resolving an absolute path to drive or system root
        drive_root = Path(root.anchor)
        with self.assertRaises(PermissionError):
            resolve_read_path(root, str(drive_root))



if __name__ == "__main__":
    unittest.main()
