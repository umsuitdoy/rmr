"""Unit tests for RMR Project Registry (TASK-04)."""

from pathlib import Path
import tempfile
import unittest

from rmr.core.registry import (
    find_default_config_path,
    get_project,
    load_projects_config,
)
from rmr.schemas import ProjectConfig


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent

    def test_find_default_config_path(self):
        cfg_path = find_default_config_path()
        self.assertTrue(cfg_path.is_file(), f"Config file must exist: {cfg_path}")
        self.assertTrue(cfg_path.name.endswith(".yaml"))

    def test_load_projects_config_default(self):
        configs = load_projects_config()
        self.assertIn("rmr", configs)
        rmr_cfg = configs["rmr"]
        self.assertIsInstance(rmr_cfg, ProjectConfig)
        self.assertEqual(rmr_cfg.project_id, "rmr")
        self.assertEqual(rmr_cfg.root_path, ".")
        self.assertEqual(rmr_cfg.language, "python")
        self.assertIn("README.md", rmr_cfg.entry_docs)

    def test_load_projects_config_custom_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                """
projects:
  demo_app:
    root_path: "/tmp/demo"
    language: "python"
    entry_docs:
      - "DOC.md"
"""
            )
            custom_path = Path(f.name)

        try:
            configs = load_projects_config(custom_path)
            self.assertIn("demo_app", configs)
            self.assertEqual(configs["demo_app"].root_path, "/tmp/demo")
            self.assertEqual(configs["demo_app"].entry_docs, ["DOC.md"])
        finally:
            custom_path.unlink(missing_ok=True)

    def test_load_projects_config_not_found(self):
        non_existent = Path("/nonexistent/projects_xyz.yaml")
        with self.assertRaises(FileNotFoundError):
            load_projects_config(non_existent)

    def test_load_projects_config_invalid_yaml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("projects: [invalid yaml: {unclosed")
            bad_yaml = Path(f.name)

        try:
            with self.assertRaises(ValueError):
                load_projects_config(bad_yaml)
        finally:
            bad_yaml.unlink(missing_ok=True)

    def test_load_projects_config_missing_projects_key(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("other_key: 123\n")
            bad_cfg = Path(f.name)

        try:
            with self.assertRaises(ValueError) as ctx:
                load_projects_config(bad_cfg)
            self.assertIn("projects", str(ctx.exception))
        finally:
            bad_cfg.unlink(missing_ok=True)

    def test_load_projects_config_invalid_project_type(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write("projects:\n  demo: 'not a dictionary'\n")
            bad_cfg = Path(f.name)

        try:
            with self.assertRaises(ValueError):
                load_projects_config(bad_cfg)
        finally:
            bad_cfg.unlink(missing_ok=True)

    def test_get_project_success(self):
        cfg = get_project("rmr")
        self.assertEqual(cfg.project_id, "rmr")

    def test_get_project_key_error_with_available(self):
        with self.assertRaises(KeyError) as ctx:
            get_project("unknown_project_999")
        err_msg = str(ctx.exception)
        self.assertIn("unknown_project_999", err_msg)
        self.assertIn("rmr", err_msg)


if __name__ == "__main__":
    unittest.main()
