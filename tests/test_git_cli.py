"""Unit tests for Git CLI Inspector (TASK-07)."""

import subprocess
import time
from pathlib import Path

import pytest

from rmr.inspectors.git_cli import (
    get_git_diff,
    get_tree_state,
    list_project_files,
)
from rmr.schemas import TreeState


def _run(cmd: list[str], cwd: Path) -> str:
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
    """Create a temporary initialized git repository with initial commit."""
    repo = tmp_path / "test_repo"
    repo.mkdir()

    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.name", "Tester"], cwd=repo)
    _run(["git", "config", "user.email", "tester@example.com"], cwd=repo)

    # Create initial files
    (repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    src_dir = repo / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")

    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "Initial commit"], cwd=repo)
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

        # Modify tracked file
        main_py = temp_git_repo / "src" / "main.py"
        time.sleep(0.01)
        main_py.write_text("print('hello modified')\n", encoding="utf-8")

        state_dirty = get_tree_state(temp_git_repo)
        assert state_dirty.is_dirty is True
        assert state_dirty.worktree_fingerprint != clean_fingerprint

        # Add untracked file
        untracked = temp_git_repo / "new_file.txt"
        untracked.write_text("untracked", encoding="utf-8")

        state_with_untracked = get_tree_state(temp_git_repo)
        assert state_with_untracked.is_dirty is True
        assert (
            state_with_untracked.worktree_fingerprint
            != state_dirty.worktree_fingerprint
        )

    def test_list_project_files_cached_and_others(self, temp_git_repo: Path) -> None:
        # Create untracked file and ignored file
        (temp_git_repo / "new_module.py").write_text("# new", encoding="utf-8")
        (temp_git_repo / "debug.log").write_text("ignored log", encoding="utf-8")

        files = list_project_files(temp_git_repo)
        assert ".gitignore" in files
        assert "README.md" in files
        assert "src/main.py" in files
        assert "new_module.py" in files
        # Ignored file must not be present
        assert "debug.log" not in files

        # Filter by subpath
        src_files = list_project_files(temp_git_repo, subpath="src")
        assert src_files == ["src/main.py"]

    def test_get_git_diff_clean(self, temp_git_repo: Path) -> None:
        # Initially clean
        diff_clean = get_git_diff(temp_git_repo)
        assert diff_clean == ""

        # Modify tracked file
        main_py = temp_git_repo / "src" / "main.py"
        main_py.write_text("print('diff test')\n", encoding="utf-8")

        diff = get_git_diff(temp_git_repo)
        assert "diff --git" in diff
        assert "print('diff test')" in diff

        # Filter by rel_path
        diff_main = get_git_diff(temp_git_repo, rel_path="src/main.py")
        assert "src/main.py" in diff_main

        diff_other = get_git_diff(temp_git_repo, rel_path="README.md")
        assert diff_other == ""

        # Diff between commits
        _run(["git", "add", "src/main.py"], cwd=temp_git_repo)
        _run(["git", "commit", "-m", "Second commit"], cwd=temp_git_repo)

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
