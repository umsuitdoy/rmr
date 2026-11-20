"""Task 23 checks for the MCP surface and boundaries of a desktop session."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from rmr.app import create_app, create_session_app
from rmr.tools import get_project_overview, investigate_codebase

ANALYSIS_TOOLS = {"get_project_overview", "investigate_codebase", "inspect_evidence"}


def _write_registry(path: Path, project_id: str, root: Path) -> None:
    projects = {project_id: {"root_path": str(root), "entry_docs": ["docs"]}}
    path.write_text(json.dumps({"projects": projects}), encoding="utf-8")


@pytest.fixture
def session_repos(tmp_path: Path) -> tuple[Path, Path, Path]:
    first = tmp_path / "first"
    second = tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(root)], check=True, capture_output=True
        )
        (root / "docs").mkdir()
    (first / "main.py").write_text("def included():\n    return 7\n", encoding="utf-8")
    (second / "outside.py").write_text(
        "def outside_only():\n    return 9\n", encoding="utf-8"
    )
    registry = tmp_path / "session.json"
    _write_registry(registry, "selected", first)
    return registry, first, second


def test_session_tools_use_the_startup_registry(session_repos):
    registry, _, second = session_repos
    app = create_session_app(registry)
    _write_registry(registry, "other", second)

    async def verify():
        async with Client(create_app()) as local:
            local_tools = {
                tool.name: tool.input_schema for tool in await local.list_tools()
            }
        assert (
            ANALYSIS_TOOLS | {"ping"}
            == local_tools.keys()
        )

        async with Client(app) as client:
            session_tools = {
                tool.name: tool.input_schema for tool in await client.list_tools()
            }
            assert session_tools == {name: local_tools[name] for name in ANALYSIS_TOOLS}
            assert all(
                "config_path" not in spec.get("properties", {})
                for spec in session_tools.values()
            )

            overview = await client.call_tool("get_project_overview")
            assert [project["project_id"] for project in overview.data["projects"]] == [
                "selected"
            ]
            detail = await client.call_tool(
                "get_project_overview", {"project_id": "selected"}
            )
            assert detail.data["files"] == ["main.py"]
            outline = await client.call_tool(
                "investigate_codebase", {"project_id": "selected", "mode": "outline"}
            )
            assert outline.data["outlines"][0]["symbols"][0]["name"] == "included"
            evidence = await client.call_tool(
                "inspect_evidence",
                {
                    "project_id": "selected",
                    "targets": [{"kind": "file_lines", "path": "main.py"}],
                },
            )
            assert "return 7" in evidence.data["evidence_items"][0]["content"]

            denied_calls = {
                "get_project_overview": {"project_id": "other"},
                "investigate_codebase": {"project_id": "other", "mode": "outline"},
                "inspect_evidence": {
                    "project_id": "other",
                    "targets": [{"kind": "file_lines", "path": "outside.py"}],
                },
                "ping": {},
            }
            for name, args in denied_calls.items():
                with pytest.raises(ToolError):
                    await client.call_tool(name, args)

        async with Client(create_session_app(registry)) as next_session:
            result = await next_session.call_tool("get_project_overview")
            assert [project["project_id"] for project in result.data["projects"]] == [
                "other"
            ]

    asyncio.run(verify())


def test_invalid_session_registry_rejected(session_repos):
    registry, first, second = session_repos
    with pytest.raises(FileNotFoundError):
        create_session_app(registry.with_name("missing.yaml"))

    invalid_entries = [
        {},
        {"first": {"root_path": str(first)}, "second": {"root_path": str(second)}},
        {"first": {"root_path": str(first), "project_id": "other"}},
        {"first": {"root_path": "first"}},
        {"first": {"root_path": str(first / "docs")}},
    ]
    for projects in invalid_entries:
        registry.write_text(json.dumps({"projects": projects}), encoding="utf-8")
        with pytest.raises(ValueError):
            create_session_app(registry)

    registry.write_text("projects: { malformed", encoding="utf-8")
    with pytest.raises(ValueError):
        create_session_app(registry)


def test_session_mcp_rejects_outside_paths(session_repos):
    registry, _, _ = session_repos
    app = create_session_app(registry)
    outside = "../second/outside.py"

    async def verify():
        async with Client(app) as client:
            with pytest.raises(ToolError, match="escapes project root"):
                await client.call_tool(
                    "get_project_overview",
                    {"project_id": "selected", "subpath": outside},
                )
            for mode in ("outline", "grep", "ast", "symbol"):
                with pytest.raises(ToolError, match="escapes project root"):
                    await client.call_tool(
                        "investigate_codebase",
                        {
                            "project_id": "selected",
                            "mode": mode,
                            "query": "outside_only",
                            "subpath": outside,
                        },
                    )
            for kind in ("file_lines", "symbol", "git_diff"):
                with pytest.raises(ToolError, match="escapes project root"):
                    await client.call_tool(
                        "inspect_evidence",
                        {
                            "project_id": "selected",
                            "targets": [{"kind": kind, "path": outside}],
                        },
                    )

    asyncio.run(verify())


def test_overview_and_outline_ignore_outside_symlinks(session_repos):
    registry, first, second = session_repos
    adr = second / "docs" / "outside.md"
    adr.write_text(
        "---\nid: ADR-1\nscope: project:other\ncategory: architecture\n---\n# Outside ADR\n",
        encoding="utf-8",
    )
    (first / ".rmr").mkdir()
    try:
        (first / "docs" / "outside.md").symlink_to(adr)
        (first / ".rmr" / "adr").symlink_to(second / "docs", target_is_directory=True)
        (first / "outside.py").symlink_to(second / "outside.py")
    except OSError as exc:
        pytest.skip(f"Symlinks are unavailable: {exc}")

    overview = get_project_overview("selected", config_path=registry)
    assert overview["adrs"] == []
    assert overview["entry_docs"][0]["files"] == []
    assert overview["files"] == ["main.py"]
    outline = investigate_codebase("selected", "outline", config_path=registry)
    assert [item["file"] for item in outline["outlines"]] == ["main.py"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junction")
def test_overview_does_not_read_adr_directory_junction(session_repos):
    registry, first, second = session_repos
    outside_docs = second / "docs"
    (outside_docs / "outside.md").write_text(
        "---\nid: ADR-1\nscope: project:other\ncategory: architecture\n---\n# Outside ADR\n",
        encoding="utf-8",
    )
    (first / ".rmr").mkdir()
    result = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(first / ".rmr" / "adr"),
            str(outside_docs),
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        pytest.skip("Directory junction creation is unavailable")
    assert get_project_overview("selected", config_path=registry)["adrs"] == []


@pytest.mark.parametrize(
    ("mode", "search"), [("grep", "run_ripgrep"), ("ast", "run_ast_grep")]
)
def test_search_drops_outside_result(session_repos, monkeypatch, mode, search):
    registry, first, second = session_repos
    matches = [
        {"file": str(first / "main.py"), "line": 1, "snippet": "inside"},
        {"file": str(second / "outside.py"), "line": 1, "snippet": "outside"},
    ]
    monkeypatch.setattr(
        f"rmr.tools.investigate_tool.{search}",
        lambda *args, **kwargs: (matches, False),
    )
    result = investigate_codebase(
        "selected", mode, query="pattern", config_path=registry
    )
    assert result["matches"] == [{"file": "main.py", "line": 1, "snippet": "inside"}]
