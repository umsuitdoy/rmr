"""RMR FastMCP Server Entrypoint and Application Skeleton (TASK-05, TASK-21)."""

import argparse
import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from fastmcp import FastMCP

from rmr.core.registry import load_projects_config
from rmr.desktop_config import build_mcp_path, normalize_domain, validate_project_root
from rmr.tools import register_analysis_tools
from rmr.tunnel import run_keyed_session

ANALYSIS_WORKFLOW = """Workflow Guidelines for AI Agents:
1. Tier 1 (Orientation): Call get_project_overview first to view the project file tree, entry docs, and ADR list.
2. Tier 2 (Investigation): Use investigate_codebase with 'outline', 'symbol', 'grep', or 'ast' to locate code structures, and 'callers' or 'callees' for direct call relationships, or 'impact' for affected dependents with depth 1–5 (default 2) in a CodeGraph-indexed repository.
3. Tier 3 (Evidence): Call inspect_evidence with specific targets to retrieve accurate line ranges with content_sha256 or git diffs.
"""
SERVER_INSTRUCTIONS = f"""RMR provides read-only code analysis for registered repositories.
{ANALYSIS_WORKFLOW}"""
SESSION_INSTRUCTIONS = f"""RMR provides read-only code analysis for one selected repository.
{ANALYSIS_WORKFLOW}"""


def create_app() -> FastMCP:
    """Create the local MCP app with the analysis tools and a health check."""
    mcp = FastMCP(
        name="rmr",
        instructions=SERVER_INSTRUCTIONS,
    )

    @mcp.tool(
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        description="Check readiness status of the RMR MCP Server.",
    )
    def ping() -> dict[str, str]:
        return {"status": "ok", "system": "RMR Read-Only MCP"}

    register_analysis_tools(mcp)

    return mcp


def create_session_app(config_path: Path) -> FastMCP:
    """Create a read-only MCP app with the repository selected at startup."""
    projects = load_projects_config(config_path)
    if len(projects) != 1:
        raise ValueError("A desktop session registry must contain exactly one project.")

    project_id, project = next(iter(projects.items()))
    if project_id != project.project_id or not project_id.strip():
        raise ValueError("Desktop session project ID must match the registry key.")
    root = Path(project.root_path)
    if not root.is_absolute():
        raise ValueError("Desktop session project root must be absolute.")
    projects[project_id] = project.model_copy(
        update={"root_path": str(validate_project_root(root))}
    )

    mcp = FastMCP(name="rmr", instructions=SESSION_INSTRUCTIONS)
    register_analysis_tools(mcp, config_path=config_path, projects=projects)
    return mcp


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for RMR MCP server."""
    parser = argparse.ArgumentParser(
        prog="rmr",
        description="RMR Read-Only MCP Server",
    )
    default_transport = os.environ.get("RMR_TRANSPORT", "stdio").lower()
    default_host = os.environ.get("RMR_HOST", "127.0.0.1")
    default_port_str = os.environ.get("RMR_PORT", "8000")
    try:
        default_port = int(default_port_str)
    except ValueError:
        default_port = 8000
    default_path = os.environ.get("RMR_PATH", "/mcp/v1")

    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse", "http"],
        default=default_transport,
        help="Transport protocol (default: stdio, env: RMR_TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=default_host,
        help="Bind host for HTTP transports (default: 127.0.0.1, env: RMR_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help="Bind port for HTTP transports (default: 8000, env: RMR_PORT)",
    )
    parser.add_argument(
        "--path",
        default=default_path,
        help="Endpoint path for HTTP transports (default: /mcp/v1, env: RMR_PATH)",
    )
    parser.add_argument(
        "--session-config",
        type=Path,
        help="Run a loopback-only Desktop session using this one-project registry",
    )
    return parser


def _run_session(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    key = os.environ.pop("RMR_SESSION_URL_KEY", None)
    if key is None:
        parser.error("Desktop sessions require RMR_SESSION_URL_KEY.")
    if not 1 <= args.port <= 65535:
        parser.error("Desktop sessions require a port between 1 and 65535.")
    try:
        mcp_path = build_mcp_path(key)
    except ValueError:
        parser.error("RMR_SESSION_URL_KEY is invalid.")

    domain = os.environ.get("RMR_SESSION_DOMAIN")
    try:
        public_domain = normalize_domain(domain) if domain is not None else None
    except ValueError:
        parser.error("RMR_SESSION_DOMAIN is invalid.")

    run_keyed_session(
        create_session_app(args.session_config),
        port=args.port,
        mcp_path=mcp_path,
        public_domain=public_domain,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint: read flags/environment and start FastMCP server."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.session_config is not None:
            _run_session(args, parser)
        else:
            app = create_app()
            if args.transport == "stdio":
                app.run(transport="stdio")
            else:
                app.run(
                    transport=args.transport,
                    host=args.host,
                    port=args.port,
                    path=args.path,
                )
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    except BaseExceptionGroup as eg:
        _, rest = eg.split(
            lambda exc: isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))
        )
        if rest is not None:
            raise rest


if __name__ == "__main__":
    main()
