"""Unit tests for Safe CLI Search Inspector with Ripgrep & AST-Grep (TASK-10)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from rmr.inspectors.search_cli import run_ast_grep, run_ripgrep


class TestSearchCli:
    def test_run_ripgrep_matching(self, tmp_path: Path) -> None:
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        sample = pkg / "service.py"
        sample.write_text(
            "def resolve_read_path(root, rel):\n    return root / rel\n",
            encoding="utf-8",
        )

        matches, is_truncated = run_ripgrep(tmp_path, "resolve_read_path", max_results=5)
        assert is_truncated is False
        assert len(matches) == 1
        item = matches[0]
        assert item["file"] == "pkg/service.py"
        assert "\\" not in item["file"]
        assert item["line"] == 1
        assert "def resolve_read_path" in item["snippet"]

    def test_run_ripgrep_argument_injection_defense(self, tmp_path: Path) -> None:
        sample = tmp_path / "flags.py"
        sample.write_text("# normal code without flags\nx = 1\n", encoding="utf-8")

        captured_cmds: list[list[str]] = []
        orig_run = subprocess.run

        def spy_run(cmd, *args, **kwargs):
            captured_cmds.append(list(cmd))
            assert kwargs.get("shell") is False
            return orig_run(cmd, *args, **kwargs)

        with patch("rmr.inspectors.search_cli.subprocess.run", side_effect=spy_run):
            for malicious_query in ["-e", "--pcre2", "-v", "--invalid-flag-attempt"]:
                matches, trunc = run_ripgrep(tmp_path, malicious_query, max_results=5)
                assert matches == []
                assert trunc is False

        assert len(captured_cmds) == 4
        for cmd in captured_cmds:
            assert "--" in cmd
            sep_idx = cmd.index("--")
            # Query and target_path must strictly appear after '--'
            assert sep_idx == len(cmd) - 3

    def test_search_missing_cli_graceful(self, tmp_path: Path) -> None:
        sample = tmp_path / "mod.py"
        sample.write_text("def target_func():\n    return 42\n", encoding="utf-8")

        with patch(
            "rmr.inspectors.search_cli.subprocess.run",
            side_effect=FileNotFoundError("Executable not found"),
        ):
            rg_matches, rg_trunc = run_ripgrep(tmp_path, "target_func", max_results=5)
            assert rg_trunc is False
            assert len(rg_matches) == 1
            assert rg_matches[0]["file"] == "mod.py"
            assert rg_matches[0]["line"] == 1

            sg_matches, sg_trunc = run_ast_grep(
                tmp_path, "def $FUNC(): $$$BODY", language="python"
            )
            assert sg_trunc is False
            assert len(sg_matches) == 1
            assert "ast-grep (sg) is not installed" in sg_matches[0]["snippet"]

    def test_run_ripgrep_max_results_truncation(self, tmp_path: Path) -> None:
        sample = tmp_path / "many.py"
        lines = [f"match_token_{i} = {i}" for i in range(15)]
        sample.write_text("\n".join(lines) + "\n", encoding="utf-8")

        matches, is_truncated = run_ripgrep(tmp_path, "match_token_", max_results=5)
        assert is_truncated is True
        assert len(matches) == 5
        assert matches[0]["line"] == 1
        assert matches[4]["line"] == 5

    def test_run_ripgrep_json_stream_parsing(self, tmp_path: Path) -> None:
        fake_lines = [
            json.dumps({"type": "begin", "data": {}}),
            json.dumps(
                {
                    "type": "match",
                    "data": {
                        "path": {"text": "src\\rmr\\core\\path_guard.py"},
                        "line_number": 16,
                        "lines": {"text": "def resolve_read_path():\r\n"},
                    },
                }
            ),
            json.dumps(
                {
                    "type": "match",
                    "data": {
                        "path": {"text": "src\\rmr\\inspectors\\ast_outline.py"},
                        "line_number": 6,
                        "lines": {"text": "from rmr.core.path_guard import resolve_read_path\n"},
                    },
                }
            ),
        ]
        fake_proc = subprocess.CompletedProcess(
            args=["rg"], returncode=0, stdout="\n".join(fake_lines), stderr=""
        )
        with patch("rmr.inspectors.search_cli.subprocess.run", return_value=fake_proc):
            matches, trunc = run_ripgrep(Path("src"), "resolve_read_path", max_results=1)
            assert trunc is True
            assert len(matches) == 1
            assert matches[0] == {
                "file": "src/rmr/core/path_guard.py",
                "line": 16,
                "snippet": "def resolve_read_path():",
            }

    def test_run_ast_grep_json_parsing_and_language_guard(self, tmp_path: Path) -> None:
        fake_json = json.dumps(
            [
                {
                    "file": "src\\rmr\\app.py",
                    "range": {"start": {"line": 9, "column": 0}},
                    "lines": "def create_server():\r\n",
                }
            ]
        )
        fake_proc = subprocess.CompletedProcess(
            args=["sg"], returncode=0, stdout=fake_json, stderr=""
        )
        with patch("rmr.inspectors.search_cli.subprocess.run", return_value=fake_proc) as m:
            matches, trunc = run_ast_grep(
                Path("src"), "def $F(): $$$", language="python", max_results=5
            )
            assert trunc is False
            assert len(matches) == 1
            assert matches[0] == {
                "file": "src/rmr/app.py",
                "line": 10,
                "snippet": "def create_server():",
            }
            called_cmd = m.call_args[0][0]
            assert "--" in called_cmd
            assert called_cmd[-2] == "--"

        with pytest.raises(ValueError, match="Invalid language"):
            run_ast_grep(tmp_path, "def $F(): $$$", language="--malicious-flag")
