"""Tools module: MCP tool registrations and handlers."""

from collections.abc import Mapping
from pathlib import Path

from fastmcp import FastMCP

from rmr.schemas import ProjectConfig
from rmr.tools.evidence_tool import (
    inspect_evidence,
    register_evidence_tool,
)
from rmr.tools.investigate_tool import (
    investigate_codebase,
    register_investigate_tool,
)
from rmr.tools.overview_tool import (
    get_project_overview,
    register_overview_tool,
)

__all__ = [
    "get_project_overview",
    "inspect_evidence",
    "investigate_codebase",
    "register_analysis_tools",
    "register_evidence_tool",
    "register_investigate_tool",
    "register_overview_tool",
]


def register_analysis_tools(
    mcp: FastMCP,
    config_path: Path | None = None,
    *,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> None:
    """Register the three read-only analysis tools."""
    register_overview_tool(mcp, config_path=config_path, projects=projects)
    register_investigate_tool(mcp, config_path=config_path, projects=projects)
    register_evidence_tool(mcp, config_path=config_path, projects=projects)
