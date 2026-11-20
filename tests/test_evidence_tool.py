"""Unit tests for Tier 3 Evidence Tool (TASK-19)."""

from pathlib import Path
import subprocess
import pytest
from fastmcp import FastMCP

from rmr.schemas import EvidenceTarget
from rmr.tools.evidence_tool import (
    inspect_evidence,
    register_evidence_tool,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)


@pytest.fixture
def mock_evidence_env(tmp_path: Path):
    repo_dir = tmp_path / "evidence_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    src_dir = repo_dir / "src"
    src_dir.mkdir()

    sample_py = src_dir / "service.py"
    sample_py.write_text(
        '''"""Service module."""

class AuthManager:
    """Authentication manager."""

    def verify_token(self, token: str) -> bool:
        """Verify token validity."""
        return len(token) > 5


def hash_password(raw: str) -> str:
    """Hash raw password."""
    return f"hashed_{raw}"
''',
        encoding="utf-8",
    )

    large_py = src_dir / "large.py"
    large_py.write_text("\n".join(f"val_{i} = {i}" for i in range(1, 260)) + "\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True)

    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        f"""
projects:
  evidence_sample:
    root_path: "{repo_dir.as_posix()}"
    language: "python"
    entry_docs: []
""",
        encoding="utf-8",
    )

    return {
        "repo_dir": repo_dir,
        "config_path": config_file,
    }


def test_inspect_evidence_empty_targets(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    with pytest.raises(ValueError, match="targets list must not be empty"):
        inspect_evidence(
            project_id="evidence_sample",
            targets=[],
            config_path=cfg,
        )


def test_inspect_evidence_file_lines(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    res = inspect_evidence(
        project_id="evidence_sample",
        targets=[
            {
                "kind": "file_lines",
                "path": "src/service.py",
                "start_line": 3,
                "end_line": 8,
            }
        ],
        config_path=cfg,
    )
    assert res["project_id"] == "evidence_sample"
    assert "tree_state" in res
    assert len(res["evidence_items"]) == 1

    item = res["evidence_items"][0]
    assert item["kind"] == "file_lines"
    assert item["path"] == "src/service.py"
    assert tuple(item["line_range"]) == (3, 8)
    assert "class AuthManager:" in item["content"]
    assert len(item["content_sha256"]) == 64
    assert item["truncated"] is False


def test_inspect_evidence_symbol(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    res = inspect_evidence(
        project_id="evidence_sample",
        targets=[
            {
                "kind": "symbol",
                "path": "src/service.py",
                "symbol_name": "hash_password",
            },
            {
                "kind": "symbol",
                "path": "src/service.py",
                "symbol_name": "AuthManager.verify_token",
            },
        ],
        config_path=cfg,
    )
    items = res["evidence_items"]
    assert len(items) == 2

    fn_item = items[0]
    assert fn_item["kind"] == "symbol"
    assert "def hash_password" in fn_item["content"]
    assert tuple(fn_item["line_range"]) == (11, 13)

    meth_item = items[1]
    assert meth_item["kind"] == "symbol"
    assert "def verify_token" in meth_item["content"]
    assert tuple(meth_item["line_range"]) == (6, 8)


def test_inspect_evidence_symbol_missing_name(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    with pytest.raises(ValueError, match="symbol_name is required"):
        inspect_evidence(
            project_id="evidence_sample",
            targets=[{"kind": "symbol", "path": "src/service.py"}],
            config_path=cfg,
        )


def test_inspect_evidence_symbol_not_found(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    with pytest.raises(KeyError, match="nonexistent_symbol"):
        inspect_evidence(
            project_id="evidence_sample",
            targets=[{"kind": "symbol", "path": "src/service.py", "symbol_name": "nonexistent_symbol"}],
            config_path=cfg,
        )


def test_inspect_evidence_git_diff(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    repo = mock_evidence_env["repo_dir"]

    # Modify file in working tree
    (repo / "src" / "service.py").write_text("# modified service\n", encoding="utf-8")

    res = inspect_evidence(
        project_id="evidence_sample",
        targets=[
            {
                "kind": "git_diff",
                "path": "src/service.py",
                "base_ref": "HEAD",
                "target_ref": "WORKTREE",
            }
        ],
        config_path=cfg,
    )
    assert res["tree_state"]["is_dirty"] is True
    assert len(res["evidence_items"]) == 1
    diff_item = res["evidence_items"][0]
    assert diff_item["kind"] == "git_diff"
    assert "diff --git" in diff_item["content"]
    assert "+# modified service" in diff_item["content"]
    assert len(diff_item["content_sha256"]) == 64


def test_inspect_evidence_hard_truncation(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    res = inspect_evidence(
        project_id="evidence_sample",
        targets=[{"kind": "file_lines", "path": "src/large.py"}],
        config_path=cfg,
    )
    item = res["evidence_items"][0]
    assert item["truncated"] is True
    assert "[... Truncated:" in item["content"]


def test_inspect_evidence_path_traversal_blocked(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    with pytest.raises(PermissionError):
        inspect_evidence(
            project_id="evidence_sample",
            targets=[{"kind": "file_lines", "path": "../../etc/passwd"}],
            config_path=cfg,
        )


def test_inspect_evidence_pydantic_targets_input(mock_evidence_env):
    cfg = mock_evidence_env["config_path"]
    t1 = EvidenceTarget(kind="file_lines", path="src/service.py", start_line=1, end_line=5)
    res = inspect_evidence(
        project_id="evidence_sample",
        targets=[t1],
        config_path=cfg,
    )
    assert len(res["evidence_items"]) == 1
    assert res["evidence_items"][0]["kind"] == "file_lines"


def test_inspect_evidence_unknown_project():
    with pytest.raises(KeyError):
        inspect_evidence(
            project_id="unknown_proj_999",
            targets=[{"kind": "file_lines", "path": "any.py"}],
        )


def test_register_evidence_tool_fastmcp():
    mcp = FastMCP("test_mcp")
    register_evidence_tool(mcp)

    components = mcp.local_provider._components
    assert "tool:inspect_evidence@" in components

    tool = components["tool:inspect_evidence@" ]
    assert tool.name == "inspect_evidence"
    assert getattr(tool.annotations, "read_only_hint", getattr(tool.annotations, "readOnlyHint", None)) is True

    schema = tool.parameters
    props = schema.get("properties", {})
    assert "project_id" in props
    assert "targets" in props
    assert "config_path" not in props
