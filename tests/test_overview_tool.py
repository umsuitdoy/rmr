"""Unit tests for Tier 1 Overview Tool (TASK-17)."""

import subprocess
from pathlib import Path

import pytest
from fastmcp import FastMCP

from rmr.tools.overview_tool import (
    get_project_overview,
    register_overview_tool,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)


@pytest.fixture
def mock_project_env(tmp_path: Path):
    repo_dir = tmp_path / "sample_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    readme = repo_dir / "README.md"
    readme.write_text("# Sample Project\nDescription here.\n", encoding="utf-8")

    docs_dir = repo_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "arch.md").write_text("# Architecture\nArch details.\n", encoding="utf-8")

    src_dir = repo_dir / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')\n", encoding="utf-8")

    adr_dir = repo_dir / ".rmr" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "ADR-001-use-fastmcp.md").write_text(
        "---\nid: ADR-001\nscope: project:sample\ncategory: architecture\n"
        "status: verified\nupdated_at: 2026-11-10\n---\n# Use FastMCP\nDecision content.\n",
        encoding="utf-8",
    )
    (adr_dir / "invalid.md").write_text(
        "---\nid: [invalid\n---\n# Broken ADR\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True)

    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        f"""
projects:
  sample:
    root_path: "{repo_dir.as_posix()}"
    language: "python"
    entry_docs:
      - "README.md"
      - "docs"
      - "missing_doc.md"
""",
        encoding="utf-8",
    )

    return {
        "repo_dir": repo_dir,
        "config_path": config_file,
    }


def test_get_project_overview_list_mode():
    res = get_project_overview()
    assert "projects" in res
    assert isinstance(res["projects"], list)
    project_ids = [p["project_id"] for p in res["projects"]]
    assert "rmr" in project_ids


def test_get_project_overview_custom_config(mock_project_env):
    cfg_path = mock_project_env["config_path"]
    res = get_project_overview(project_id="sample", config_path=cfg_path)

    assert res["project_id"] == "sample"
    assert res["language"] == "python"
    assert "tree_state" in res
    assert len(res["tree_state"]["head_commit"]) == 7
    assert res["tree_state"]["is_dirty"] is False
    assert res["total_files"] >= 4
    assert res["files_truncated"] is False

    entry_docs = res["entry_docs"]
    assert len(entry_docs) == 3

    readme_doc = next(d for d in entry_docs if d["path"] == "README.md")
    assert readme_doc["kind"] == "file"
    assert "Sample Project" in readme_doc["content"]
    assert len(readme_doc["content_sha256"]) == 64

    dir_doc = next(d for d in entry_docs if d["path"] == "docs")
    assert dir_doc["kind"] == "directory"
    assert "docs/arch.md" in dir_doc["files"]

    missing_doc = next(d for d in entry_docs if d["path"] == "missing_doc.md")
    assert missing_doc["kind"] == "missing"

    assert res["adrs"] == [{
        "id": "ADR-001",
        "title": "Use FastMCP",
        "status": "verified",
        "updated_at": "2026-11-10",
        "file_path": ".rmr/adr/ADR-001-use-fastmcp.md",
    }]


def test_get_project_overview_subpath_filtering(mock_project_env):
    cfg_path = mock_project_env["config_path"]
    res = get_project_overview(project_id="sample", subpath="src", config_path=cfg_path)
    assert res["files"] == ["src/main.py"]


def test_get_project_overview_path_traversal_blocked(mock_project_env):
    cfg_path = mock_project_env["config_path"]
    with pytest.raises((PermissionError, FileNotFoundError)):
        get_project_overview(project_id="sample", subpath="../../etc/passwd", config_path=cfg_path)


def test_get_project_overview_unknown_project():
    with pytest.raises(KeyError):
        get_project_overview(project_id="nonexistent_proj_999")


def test_get_project_overview_rmr():
    res = get_project_overview("rmr")
    assert res["project_id"] == "rmr"
    assert "tree_state" in res
    assert res["total_files"] > 0
    assert any("README.md" in d["path"] for d in res["entry_docs"])


def test_register_overview_tool_fastmcp():
    mcp = FastMCP("test_mcp")
    register_overview_tool(mcp)

    components = mcp.local_provider._components
    assert "tool:get_project_overview@" in components

    tool = components["tool:get_project_overview@"]
    assert tool.name == "get_project_overview"
    assert getattr(tool.annotations, "read_only_hint", getattr(tool.annotations, "readOnlyHint", None)) is True

    # Verify the parameter schema exposes only project_id and subpath, without leaking config_path
    schema = tool.parameters
    props = schema.get("properties", {})
    assert "project_id" in props
    assert "subpath" in props
    assert "config_path" not in props
