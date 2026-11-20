"""Unit tests for Tier 2 Investigation Tool (TASK-18)."""

from pathlib import Path
import subprocess
import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from rmr.tools.investigate_tool import (
    investigate_codebase,
    register_investigate_tool,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)


@pytest.fixture
def mock_investigate_env(tmp_path: Path):
    repo_dir = tmp_path / "investigate_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    src_dir = repo_dir / "src"
    src_dir.mkdir()

    math_mod = src_dir / "math_utils.py"
    math_mod.write_text(
        '''"""Math utilities."""

class MathEngine:
    """Core math engine."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b


def calculate_square(x: int) -> int:
    """Square a number."""
    return x * x
''',
        encoding="utf-8",
    )

    runner_mod = src_dir / "runner.py"
    runner_mod.write_text(
        '''from src.math_utils import calculate_square

def run():
    return calculate_square(10)
''',
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True)

    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        f"""
projects:
  investigate_sample:
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


def test_investigate_outline(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    res = investigate_codebase(
        project_id="investigate_sample",
        mode="outline",
        config_path=cfg,
    )
    assert res["mode"] == "outline"
    assert "tree_state" in res
    assert res["truncated"] is False
    assert len(res["outlines"]) >= 2

    math_outline = next(o for o in res["outlines"] if "math_utils.py" in o["file"])
    symbol_names = [s["name"] for s in math_outline["symbols"]]
    assert "MathEngine" in symbol_names
    assert "calculate_square" in symbol_names


def test_investigate_outline_subpath(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    res = investigate_codebase(
        project_id="investigate_sample",
        mode="outline",
        subpath="src/math_utils.py",
        config_path=cfg,
    )
    assert len(res["outlines"]) == 1
    assert "math_utils.py" in res["outlines"][0]["file"]


def test_investigate_symbol(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    res = investigate_codebase(
        project_id="investigate_sample",
        mode="symbol",
        query="calculate_square",
        subpath="src/math_utils.py",
        config_path=cfg,
    )
    assert res["mode"] == "symbol"
    assert res["symbol"] == "calculate_square"
    assert res["definition"] is not None
    assert "math_utils.py" in res["definition"]["file"]
    assert res["definition"]["line_start"] == 11
    assert "Square a number" in res["definition"]["docstring"]

    # References should point to runner.py
    refs = res["references"]
    assert isinstance(refs, list)
    assert len(refs) >= 1
    assert any("runner.py" in r["file"] for r in refs)


def test_investigate_symbol_missing_query(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    with pytest.raises(ValueError, match="query is required"):
        investigate_codebase(
            project_id="investigate_sample",
            mode="symbol",
            query="",
            config_path=cfg,
        )


def test_investigate_symbol_not_found(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    res = investigate_codebase(
        project_id="investigate_sample",
        mode="symbol",
        query="nonexistent_function_xyz",
        config_path=cfg,
    )
    assert res["definition"] is None
    assert res["references"] == []


def test_investigate_grep(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    res = investigate_codebase(
        project_id="investigate_sample",
        mode="grep",
        query="class MathEngine",
        config_path=cfg,
    )
    assert res["mode"] == "grep"
    assert "tree_state" in res
    assert len(res["matches"]) >= 1
    match = res["matches"][0]
    assert "src/math_utils.py" in match["file"]
    assert match["line"] == 3
    assert "\\" not in match["file"]


def test_investigate_grep_missing_query(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    with pytest.raises(ValueError, match="query is required"):
        investigate_codebase(
            project_id="investigate_sample",
            mode="grep",
            query="  ",
            config_path=cfg,
        )


def test_investigate_ast(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    res = investigate_codebase(
        project_id="investigate_sample",
        mode="ast",
        query="def $F($$$ARGS): $$$B",
        config_path=cfg,
    )
    assert res["mode"] == "ast"
    assert "matches" in res
    assert isinstance(res["matches"], list)


def test_investigate_ast_missing_query(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    with pytest.raises(ValueError, match="query is required"):
        investigate_codebase(
            project_id="investigate_sample",
            mode="ast",
            query="",
            config_path=cfg,
        )


def test_investigate_path_traversal_blocked(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    with pytest.raises((PermissionError, FileNotFoundError)):
        investigate_codebase(
            project_id="investigate_sample",
            mode="outline",
            subpath="../../etc/passwd",
            config_path=cfg,
        )


def test_investigate_unknown_project():
    with pytest.raises(KeyError):
        investigate_codebase(
            project_id="unknown_proj_999",
            mode="outline",
        )


def test_investigate_invalid_mode():
    with pytest.raises(ValidationError):
        investigate_codebase(
            project_id="rmr",
            mode="invalid_mode",  # type: ignore[arg-type]
        )


def test_register_investigate_tool_fastmcp():
    mcp = FastMCP("test_mcp")
    register_investigate_tool(mcp)

    components = mcp.local_provider._components
    assert "tool:investigate_codebase@" in components

    tool = components["tool:investigate_codebase@"]
    assert tool.name == "investigate_codebase"
    assert getattr(tool.annotations, "read_only_hint", getattr(tool.annotations, "readOnlyHint", None)) is True

    schema = tool.parameters
    props = schema.get("properties", {})
    assert "project_id" in props
    assert "mode" in props
    assert "query" in props
    assert "subpath" in props
    assert "max_results" in props
    assert "depth" in props
    depth_schema = next(item for item in props["depth"]["anyOf"] if item.get("type") == "integer")
    assert depth_schema["minimum"] == 1 and depth_schema["maximum"] == 5
    assert "impact" in props["mode"]["enum"]
    assert "config_path" not in props


def test_impact_depth_validation(mock_investigate_env):
    cfg = mock_investigate_env["config_path"]
    for depth in (0, 6, True, 1.5):
        with pytest.raises(ValidationError):
            investigate_codebase("investigate_sample", "impact", "anchor", config_path=cfg, depth=depth)
    with pytest.raises(ValidationError, match="only supported"):
        investigate_codebase("investigate_sample", "outline", config_path=cfg, depth=2)
