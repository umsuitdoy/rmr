"""Comprehensive automated test suite for all Read-Only Inspectors (TASK-12).

Covers:
- TestGitCli (git_cli.py)
- TestAstOutline (ast_outline.py)
- TestSymbolJedi (symbol_jedi.py)
- TestSearchCli (search_cli.py)
- TestFileReader (file_reader.py)
"""

import hashlib
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from rmr.inspectors import (
    extract_directory_outline,
    extract_file_outline,
    find_symbol,
    get_git_diff,
    get_tree_state,
    list_project_files,
    read_file_lines,
    read_symbol_content,
    run_ast_grep,
    run_ripgrep,
)
from rmr.schemas import EvidenceItem, TreeState


def _run_git_cmd(cmd: list[str], cwd: Path) -> str:
    res = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    return res.stdout


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    """Khởi tạo một Git repository tạm có commit ban đầu phục vụ kiểm thử."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    _run_git_cmd(["git", "init"], cwd=repo)
    _run_git_cmd(["git", "config", "user.name", "Tester"], cwd=repo)
    _run_git_cmd(["git", "config", "user.email", "tester@example.com"], cwd=repo)

    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    src_dir = repo / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")

    _run_git_cmd(["git", "add", "."], cwd=repo)
    _run_git_cmd(["git", "commit", "-m", "Initial commit"], cwd=repo)
    return repo


class TestGitCli:
    def test_get_tree_state_clean_and_dirty(self, temp_git_repo: Path) -> None:
        state = get_tree_state(temp_git_repo)
        assert isinstance(state, TreeState)
        assert len(state.head_commit) == 7
        assert state.is_dirty is False
        assert len(state.worktree_fingerprint) == 12
        assert all(c in "0123456789abcdef" for c in state.worktree_fingerprint)
        clean_fingerprint = state.worktree_fingerprint

        main_py = temp_git_repo / "src" / "main.py"
        time.sleep(0.01)
        main_py.write_text("print('hello modified')\n", encoding="utf-8")

        state_dirty = get_tree_state(temp_git_repo)
        assert state_dirty.is_dirty is True
        assert state_dirty.worktree_fingerprint != clean_fingerprint

        untracked = temp_git_repo / "new_file.txt"
        untracked.write_text("untracked", encoding="utf-8")

        state_with_untracked = get_tree_state(temp_git_repo)
        assert state_with_untracked.is_dirty is True
        assert state_with_untracked.worktree_fingerprint != state_dirty.worktree_fingerprint

    def test_list_project_files_cached_and_others(self, temp_git_repo: Path) -> None:
        (temp_git_repo / "new_module.py").write_text("# new", encoding="utf-8")
        (temp_git_repo / "debug.log").write_text("ignored log", encoding="utf-8")

        files = list_project_files(temp_git_repo)
        assert ".gitignore" in files
        assert "README.md" in files
        assert "src/main.py" in files
        assert "new_module.py" in files
        assert "debug.log" not in files

        src_files = list_project_files(temp_git_repo, subpath="src")
        assert src_files == ["src/main.py"]

    def test_get_git_diff_clean(self, temp_git_repo: Path) -> None:
        diff_clean = get_git_diff(temp_git_repo)
        assert diff_clean == ""

        main_py = temp_git_repo / "src" / "main.py"
        main_py.write_text("print('diff test')\n", encoding="utf-8")

        diff = get_git_diff(temp_git_repo)
        assert "diff --git" in diff
        assert "print('diff test')" in diff

        diff_main = get_git_diff(temp_git_repo, rel_path="src/main.py")
        assert "src/main.py" in diff_main

        diff_other = get_git_diff(temp_git_repo, rel_path="README.md")
        assert diff_other == ""

        _run_git_cmd(["git", "add", "src/main.py"], cwd=temp_git_repo)
        _run_git_cmd(["git", "commit", "-m", "Second commit"], cwd=temp_git_repo)

        commit_diff = get_git_diff(temp_git_repo, base_ref="HEAD~1", target_ref="HEAD")
        assert "diff --git" in commit_diff
        assert "+print('diff test')" in commit_diff

    def test_git_ref_injection_blocked(self, temp_git_repo: Path) -> None:
        dangerous_refs = [
            "--help",
            "-v",
            "; rm -rf",
            "| cat",
            "HEAD && ls",
            "HEAD`id`",
            "HEAD$(whoami)",
            "",
            "   ",
        ]
        for bad_ref in dangerous_refs:
            with pytest.raises(ValueError):
                get_git_diff(temp_git_repo, base_ref=bad_ref)

            with pytest.raises(ValueError):
                get_git_diff(temp_git_repo, target_ref=bad_ref)


class TestAstOutline:
    def test_extract_file_outline_classes_and_functions(self, tmp_path: Path) -> None:
        sample_code = '''"""Module docstring."""

class Calculator:
    """Class docstring line 1.
    More explanation here.
    """

    def __init__(self, base: int = 0):
        self.base = base

    def add(self, x: int, y: int) -> int:
        """Add two numbers."""
        return x + y


def helper_func(val: str, flag: bool = False):
    """Standalone helper function."""
    return val


async def async_fetch(url: str):
    """Fetch data asynchronously."""
    pass
'''
        file_path = tmp_path / "sample.py"
        file_path.write_text(sample_code, encoding="utf-8")

        res = extract_file_outline(file_path, "sample.py")
        assert res["file"] == "sample.py"
        assert res["error"] is None
        symbols = res["symbols"]
        assert len(symbols) == 3

        cls_item = symbols[0]
        assert cls_item["kind"] == "class"
        assert cls_item["name"] == "Calculator"
        assert cls_item["doc"] == "Class docstring line 1."
        assert cls_item["lines"][0] == 3
        assert cls_item["lines"][1] > 3

        methods = cls_item["methods"]
        assert len(methods) == 2
        assert methods[0]["name"] == "__init__"
        assert methods[0]["args"] == ["self", "base"]
        assert methods[1]["name"] == "add"
        assert methods[1]["args"] == ["self", "x", "y"]
        assert methods[1]["doc"] == "Add two numbers."

        func_item = symbols[1]
        assert func_item["kind"] == "function"
        assert func_item["name"] == "helper_func"
        assert func_item["args"] == ["val", "flag"]
        assert func_item["doc"] == "Standalone helper function."

        async_item = symbols[2]
        assert async_item["kind"] == "function"
        assert async_item["name"] == "async_fetch"
        assert async_item["args"] == ["url"]
        assert async_item["doc"] == "Fetch data asynchronously."

    def test_docstring_first_line_only(self, tmp_path: Path) -> None:
        code = '''
def multi_line_doc():
    """
    Line one of documentation.
    Line two of documentation.
    Line three with details.
    """
    pass

def single_line_doc():
    """Single line documentation."""
    pass

def no_doc():
    pass
'''
        py_file = tmp_path / "docs_test.py"
        py_file.write_text(code, encoding="utf-8")

        res = extract_file_outline(py_file, "docs_test.py")
        symbols = {s["name"]: s for s in res["symbols"]}

        assert symbols["multi_line_doc"]["doc"] == "Line one of documentation."
        assert symbols["single_line_doc"]["doc"] == "Single line documentation."
        assert symbols["no_doc"]["doc"] == ""

    def test_extract_file_outline_syntax_error(self, tmp_path: Path) -> None:
        bad_code = "def broken_func(:\n    pass\n"
        py_file = tmp_path / "syntax_err.py"
        py_file.write_text(bad_code, encoding="utf-8")

        res = extract_file_outline(py_file, "syntax_err.py")
        assert res["file"] == "syntax_err.py"
        assert res["symbols"] == []
        assert res["error"] is not None
        assert "invalid syntax" in res["error"] or "expected" in res["error"]

    def test_extract_directory_outline_truncation(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "pkg"
        src_dir.mkdir()

        for i in range(10):
            p = src_dir / f"mod_{i:02d}.py"
            p.write_text(f"def func_{i}(): pass\n", encoding="utf-8")

        outlines, is_truncated = extract_directory_outline(
            tmp_path, subpath="pkg", max_results=5
        )
        assert len(outlines) == 5
        assert is_truncated is True

        outlines_all, is_truncated_all = extract_directory_outline(
            tmp_path, subpath="pkg", max_results=20
        )
        assert len(outlines_all) == 10
        assert is_truncated_all is False


class TestSymbolJedi:
    def test_find_symbol_definition_and_references(self, tmp_path: Path) -> None:
        pkg = tmp_path / "my_pkg"
        pkg.mkdir()

        mod_a = pkg / "module_a.py"
        mod_a.write_text(
            '''"""Module A."""

def compute_total(items: list[int]) -> int:
    """Calculate the sum of all elements in items."""
    return sum(items)
''',
            encoding="utf-8",
        )

        mod_b = pkg / "module_b.py"
        mod_b.write_text(
            '''"""Module B."""
from my_pkg.module_a import compute_total

def run():
    val = compute_total([1, 2, 3])
    return val
''',
            encoding="utf-8",
        )

        res = find_symbol(tmp_path, "compute_total", subpath="my_pkg/module_a.py")
        assert res["symbol"] == "compute_total"
        assert res["definition"] is not None

        defn = res["definition"]
        assert defn["file"] == "my_pkg/module_a.py"
        assert defn["line_start"] == 3
        assert defn["line_end"] >= 3
        assert "Calculate the sum" in defn["docstring"]

        refs = res["references"]
        assert len(refs) >= 1
        ref_files = [r["file"] for r in refs]
        assert "my_pkg/module_b.py" in ref_files
        assert any("compute_total([1, 2, 3])" in r["snippet"] for r in refs)

    def test_find_symbol_nonexistent_graceful(self, tmp_path: Path) -> None:
        res = find_symbol(tmp_path, "nonexistent_symbol_xyz_123")
        assert res == {
            "symbol": "nonexistent_symbol_xyz_123",
            "definition": None,
            "references": [],
            "truncated": False,
        }

    def test_symbol_jedi_references_field_name(self, tmp_path: Path) -> None:
        src = tmp_path / "app.py"
        src.write_text("class MyService:\n    pass\n", encoding="utf-8")

        res = find_symbol(tmp_path, "MyService")
        assert "references" in res
        assert "callers" not in res
        assert isinstance(res["references"], list)

    def test_symbol_jedi_subpath_traversal_blocked(self, tmp_path: Path) -> None:
        with pytest.raises((PermissionError, FileNotFoundError)):
            find_symbol(tmp_path, "any_symbol", subpath="../../etc/passwd")


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


class TestFileReader:
    def test_read_file_lines_exact_range(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample.py"
        lines = [f"line_{i}" for i in range(1, 11)]
        sample.write_text("\n".join(lines) + "\n", encoding="utf-8")

        item = read_file_lines(tmp_path, "sample.py", start_line=3, end_line=6)
        assert isinstance(item, EvidenceItem)
        assert item.kind == "file_lines"
        assert item.path == "sample.py"
        assert item.line_range == (3, 6)
        assert item.content == "line_3\nline_4\nline_5\nline_6"
        assert item.truncated is False

    def test_read_file_lines_sha256(self, tmp_path: Path) -> None:
        sample = tmp_path / "hash_check.py"
        sample.write_bytes(b"alpha = 1\r\nbeta = 2\r\ngamma = 3\r\n")

        item = read_file_lines(tmp_path, "hash_check.py", start_line=1, end_line=2)
        expected_raw = "alpha = 1\nbeta = 2"
        expected_sha = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()

        assert item.content_sha256 is not None
        assert len(item.content_sha256) == 64
        assert item.content_sha256 == expected_sha
        assert item.content == expected_raw

    def test_read_file_lines_hard_truncation(self, tmp_path: Path) -> None:
        many_lines_file = tmp_path / "large.py"
        raw_lines = [f"x_{i} = {i}" for i in range(1, 260)]
        raw_text = "\n".join(raw_lines)
        many_lines_file.write_text(raw_text, encoding="utf-8")

        item = read_file_lines(tmp_path, "large.py")
        assert item.truncated is True
        assert "[... Truncated:" in item.content
        assert item.content_sha256 == hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        large_bytes_file = tmp_path / "huge_bytes.py"
        huge_line = "a" * 9000
        large_bytes_file.write_text(huge_line, encoding="utf-8")
        item_bytes = read_file_lines(tmp_path, "huge_bytes.py")
        assert item_bytes.truncated is True
        assert item_bytes.content_sha256 == hashlib.sha256(huge_line.encode("utf-8")).hexdigest()

    def test_read_file_lines_path_traversal_blocked(self, tmp_path: Path) -> None:
        proj = tmp_path / "repo"
        proj.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("top secret", encoding="utf-8")

        with pytest.raises(PermissionError):
            read_file_lines(proj, "../../etc/passwd")

        with pytest.raises(PermissionError):
            read_file_lines(proj, "../secret.txt")

    def test_read_symbol_content_success(self, tmp_path: Path) -> None:
        mod = tmp_path / "calculator.py"
        mod.write_text(
            '''"""Calculator module."""

class MathEngine:
    """Core math class."""

    def multiply(self, a: int, b: int) -> int:
        """Multiply two integers."""
        return a * b


def standalone_add(x: int, y: int) -> int:
    """Add two integers."""
    res = x + y
    return res
''',
            encoding="utf-8",
        )

        fn_item = read_symbol_content(tmp_path, "calculator.py", "standalone_add")
        assert fn_item.kind == "symbol"
        assert fn_item.path == "calculator.py"
        assert fn_item.line_range == (11, 14)
        assert "def standalone_add" in fn_item.content
        assert "return res" in fn_item.content
        assert fn_item.truncated is False

        meth_item = read_symbol_content(tmp_path, "calculator.py", "MathEngine.multiply")
        assert meth_item.kind == "symbol"
        assert meth_item.line_range == (6, 8)
        assert "def multiply" in meth_item.content
        assert "return a * b" in meth_item.content

    def test_read_symbol_content_not_found(self, tmp_path: Path) -> None:
        mod = tmp_path / "empty_sym.py"
        mod.write_text("def existing_fn():\n    return 1\n", encoding="utf-8")

        with pytest.raises(KeyError, match="missing_symbol"):
            read_symbol_content(tmp_path, "empty_sym.py", "missing_symbol")
