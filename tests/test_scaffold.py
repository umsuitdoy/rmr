"""Verification tests for RMR scaffold and configuration (TASK-01)."""

import tomllib
import unittest
from pathlib import Path

import yaml


class TestScaffold(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent

    def test_pyproject_toml_configuration(self):
        pyproject_path = self.repo_root / "pyproject.toml"
        self.assertTrue(pyproject_path.is_file(), "pyproject.toml must exist")

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        # Build backend
        self.assertEqual(data.get("build-system", {}).get("build-backend"), "hatchling.build")

        # Project metadata
        project = data.get("project", {})
        self.assertEqual(project.get("name"), "rmr")
        self.assertEqual(project.get("requires-python"), ">=3.11")

        # Core dependencies
        deps = project.get("dependencies", [])
        dep_names = [d.split(">=")[0].strip() for d in deps]
        expected_deps = ["fastmcp", "pydantic", "pyyaml", "python-frontmatter", "jedi"]
        for expected in expected_deps:
            self.assertIn(expected, dep_names, f"Missing core dependency: {expected}")

        # Dev dependencies
        dev_deps = project.get("optional-dependencies", {}).get("dev", [])
        dev_names = [d.split(">=")[0].strip() for d in dev_deps]
        self.assertIn("pytest", dev_names)
        self.assertIn("ruff", dev_names)

        # CLI script
        scripts = project.get("scripts", {})
        self.assertEqual(scripts.get("rmr-server"), "rmr.app:main")

    def test_projects_yaml_configuration(self):
        config_path = self.repo_root / "config" / "projects.example.yaml"
        self.assertTrue(config_path.is_file(), "The public registry sample must exist")

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)

        self.assertIn("projects", cfg)
        self.assertIn("rmr", cfg["projects"])
        rmr_cfg = cfg["projects"]["rmr"]
        self.assertEqual(rmr_cfg.get("root_path"), ".")
        self.assertEqual(rmr_cfg.get("language"), "python")
        self.assertIn("README.md", rmr_cfg.get("entry_docs", []))

    def test_directory_scaffold_and_inits(self):
        expected_inits = [
            "src/rmr/__init__.py",
            "src/rmr/core/__init__.py",
            "src/rmr/inspectors/__init__.py",
            "src/rmr/tools/__init__.py",
            "tests/__init__.py",
        ]
        for rel_path in expected_inits:
            file_path = self.repo_root / rel_path
            self.assertTrue(file_path.is_file(), f"File {rel_path} must exist")


if __name__ == "__main__":
    unittest.main()
