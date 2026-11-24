"""Integration and End-to-End tests for RMR analysis tools (TASK-21)."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from rmr.app import create_app
from rmr.tools import (
    get_project_overview,
    inspect_evidence,
    investigate_codebase,
)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "RmrTester"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=path, check=True, capture_output=True)


@pytest.fixture
def sample_rmr_env(tmp_path: Path):
    """Thiết lập môi trường kiểm thử đầy đủ: repo git, tài liệu, ADRs và config."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)

    src_dir = repo_dir / "src"
    src_dir.mkdir()

    main_py = src_dir / "main.py"
    main_py.write_text(
        '''"""Main application server."""

class AppServer:
    """Core server class."""

    def start(self, port: int = 8080) -> bool:
        """Start listening on given port."""
        return port > 0

    def stop(self) -> None:
        """Stop server gracefully."""
        pass


def create_server() -> AppServer:
    """Factory function for AppServer."""
    return AppServer()
''',
        encoding="utf-8",
    )

    large_py = src_dir / "large.py"
    large_py.write_text("\n".join(f"line_{i} = {i}" for i in range(1, 260)) + "\n", encoding="utf-8")

    docs_dir = repo_dir / "docs"
    docs_dir.mkdir()
    readme_md = repo_dir / "README.md"
    readme_md.write_text("# Sample Project\nOverview documentation.", encoding="utf-8")

    # .rmr/adr
    adr_dir = repo_dir / ".rmr" / "adr"
    adr_dir.mkdir(parents=True)
    (adr_dir / "ADR-001-architecture.md").write_text(
        "---\nid: ADR-001\nscope: project:sample_project\ncategory: architecture\n"
        "status: verified\n---\n# ADR-001: Architecture Decision\nDecoupled modules.\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True)

    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        f"""
projects:
  sample_project:
    root_path: "{repo_dir.as_posix()}"
    language: "python"
    entry_docs:
      - "README.md"
""",
        encoding="utf-8",
    )

    return {
        "repo_dir": repo_dir,
        "config_path": config_file,
    }


def test_local_mcp_exposes_read_only_tools():
    components = create_app().local_provider._components
    expected_tools = {"get_project_overview", "investigate_codebase", "inspect_evidence", "ping"}

    assert {tool.name for tool in components.values()} == expected_tools
    for tool in components.values():
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is False
        assert "config_path" not in tool.parameters.get("properties", {})


def test_tool_get_project_overview_list_and_detail(sample_rmr_env):
    """2. Tool 1 (get_project_overview): kiểm tra cả danh sách projects và thông tin chi tiết."""
    cfg = sample_rmr_env["config_path"]

    # 2.1. List mode (project_id=None)
    overview_list = get_project_overview(project_id=None, config_path=cfg)
    assert "projects" in overview_list
    assert any(p["project_id"] == "sample_project" for p in overview_list["projects"])

    # 2.2. Detail mode
    detail = get_project_overview(project_id="sample_project", config_path=cfg)
    assert detail["project_id"] == "sample_project"
    assert detail["language"] == "python"
    assert "tree_state" in detail
    assert detail["tree_state"]["is_dirty"] is False
    assert len(detail["tree_state"]["head_commit"]) == 7
    assert len(detail["tree_state"]["worktree_fingerprint"]) == 12

    # Files & entry_docs
    files = detail["files"]
    assert "src/main.py" in files
    assert "README.md" in files

    entry_docs = detail["entry_docs"]
    assert len(entry_docs) >= 1
    assert entry_docs[0]["path"] == "README.md"
    assert "Sample Project" in entry_docs[0]["content"]

    # ADRs
    adrs = detail["adrs"]
    assert len(adrs) == 1
    assert adrs[0]["id"] == "ADR-001"
    assert adrs[0]["title"] == "ADR-001: Architecture Decision"

    # Subpath escape check
    with pytest.raises((PermissionError, FileNotFoundError)):
        get_project_overview(project_id="sample_project", subpath="../../etc", config_path=cfg)


def test_tool_investigate_codebase_all_modes(sample_rmr_env):
    """3. Tool 2 (investigate_codebase): kiểm tra cả 4 chế độ outline, symbol, grep, ast."""
    cfg = sample_rmr_env["config_path"]

    # 3.1. Outline mode
    res_outline = investigate_codebase(project_id="sample_project", mode="outline", config_path=cfg)
    assert res_outline["mode"] == "outline"
    assert "tree_state" in res_outline
    main_outline = next(o for o in res_outline["outlines"] if "main.py" in o["file"])
    names = [s["name"] for s in main_outline["symbols"]]
    assert "AppServer" in names
    assert "create_server" in names

    # 3.2. Symbol mode
    res_symbol = investigate_codebase(
        project_id="sample_project",
        mode="symbol",
        query="create_server",
        config_path=cfg,
    )
    assert res_symbol["mode"] == "symbol"
    assert res_symbol["definition"] is not None
    assert "main.py" in res_symbol["definition"]["file"]

    # 3.3. Grep mode
    res_grep = investigate_codebase(
        project_id="sample_project",
        mode="grep",
        query="def start",
        config_path=cfg,
    )
    assert res_grep["mode"] == "grep"
    assert len(res_grep["matches"]) >= 1
    assert "main.py" in res_grep["matches"][0]["file"]
    assert "\\" not in res_grep["matches"][0]["file"]

    # 3.4. AST mode
    res_ast = investigate_codebase(
        project_id="sample_project",
        mode="ast",
        query="def $F($$$ARGS): $$$B",
        config_path=cfg,
    )
    assert res_ast["mode"] == "ast"
    assert isinstance(res_ast["matches"], list)

    # Empty query check
    with pytest.raises(ValueError, match="query is required"):
        investigate_codebase(project_id="sample_project", mode="symbol", query="", config_path=cfg)


def test_tool_inspect_evidence_lines_symbol_and_diff(sample_rmr_env):
    """4. Tool 3 (inspect_evidence): kiểm tra file_lines, symbol, git_diff, SHA-256 và cắt tải."""
    cfg = sample_rmr_env["config_path"]
    repo = sample_rmr_env["repo_dir"]

    # Modify main.py in working tree to create git diff
    (repo / "src" / "main.py").write_text(
        (repo / "src" / "main.py").read_text(encoding="utf-8") + "\n# Modified for diff\n",
        encoding="utf-8",
    )

    targets = [
        {"kind": "file_lines", "path": "src/main.py", "start_line": 3, "end_line": 7},
        {"kind": "symbol", "path": "src/main.py", "symbol_name": "AppServer.start"},
        {"kind": "git_diff", "path": "src/main.py", "base_ref": "HEAD", "target_ref": "WORKTREE"},
        {"kind": "file_lines", "path": "src/large.py"},
    ]

    res = inspect_evidence(project_id="sample_project", targets=targets, config_path=cfg)
    assert res["project_id"] == "sample_project"
    assert res["tree_state"]["is_dirty"] is True
    items = res["evidence_items"]
    assert len(items) == 4

    # 4.1. file_lines
    assert items[0]["kind"] == "file_lines"
    assert "class AppServer:" in items[0]["content"]
    assert len(items[0]["content_sha256"]) == 64
    assert items[0]["truncated"] is False

    # 4.2. symbol
    assert items[1]["kind"] == "symbol"
    assert "def start" in items[1]["content"]

    # 4.3. git_diff
    assert items[2]["kind"] == "git_diff"
    assert "+# Modified for diff" in items[2]["content"]
    assert len(items[2]["content_sha256"]) == 64

    # 4.4. Hard truncation (>200 lines)
    assert items[3]["truncated"] is True
    assert "[... Truncated:" in items[3]["content"]

    # Path traversal check
    with pytest.raises(PermissionError):
        inspect_evidence(
            project_id="sample_project",
            targets=[{"kind": "file_lines", "path": "../../etc/shadow"}],
            config_path=cfg,
        )


def test_fastmcp_end_to_end_tool_calls_and_security(sample_rmr_env, monkeypatch):
    """Exercise the three analysis tools and path boundaries through FastMCP."""
    cfg = sample_rmr_env["config_path"]

    monkeypatch.setattr("rmr.core.registry.find_default_config_path", lambda: cfg)

    app = create_app()

    async def _run_e2e():
        # Tool 1: get_project_overview
        r1 = await app.call_tool("get_project_overview", {})
        assert not getattr(r1, "is_error", getattr(r1, "isError", False))
        assert "sample_project" in r1.content[0].text

        r1_detail = await app.call_tool("get_project_overview", {"project_id": "sample_project"})
        assert not getattr(r1_detail, "is_error", getattr(r1_detail, "isError", False))
        assert "tree_state" in r1_detail.content[0].text

        # Tool 2: investigate_codebase
        r2 = await app.call_tool("investigate_codebase", {"project_id": "sample_project", "mode": "outline"})
        assert not getattr(r2, "is_error", getattr(r2, "isError", False))
        assert "AppServer" in r2.content[0].text

        # Tool 3: inspect_evidence
        r3 = await app.call_tool(
            "inspect_evidence",
            {
                "project_id": "sample_project",
                "targets": [{"kind": "file_lines", "path": "src/main.py", "start_line": 1, "end_line": 5}],
            },
        )
        assert not getattr(r3, "is_error", getattr(r3, "isError", False))
        assert "main.py" in r3.content[0].text

        # Security check: Path traversal escape via call_tool
        with pytest.raises((ToolError, Exception)):
            await app.call_tool(
                "inspect_evidence",
                {
                    "project_id": "sample_project",
                    "targets": [{"kind": "file_lines", "path": "../../etc/shadow"}],
                },
            )

        # Security check: Unknown project isolation
        with pytest.raises((ToolError, Exception)):
            await app.call_tool("get_project_overview", {"project_id": "nonexistent_project_xyz"})

    asyncio.run(_run_e2e())
