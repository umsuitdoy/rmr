"""Tier 1 Overview Tool: get_project_overview (TASK-17)."""

from collections.abc import Mapping
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any, Literal

import frontmatter
from fastmcp import FastMCP
from pydantic import BaseModel, field_validator
from yaml import YAMLError

from rmr.core.path_guard import resolve_project_file, resolve_read_path
from rmr.core.registry import (
    get_project,
    load_projects_config,
    resolve_project_root,
)
from rmr.core.truncator import truncate_list
from rmr.inspectors.file_reader import read_file_lines
from rmr.inspectors.git_cli import get_tree_state, list_project_files
from rmr.schemas import ProjectConfig

__all__ = ["get_project_overview", "register_overview_tool"]


class _AdrMetadata(BaseModel):
    id: str
    scope: str
    category: Literal["architecture", "security", "testing", "convention", "contract"]
    status: Literal["proposed", "verified", "deprecated", "rejected"] = "proposed"
    updated_at: str | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def _coerce_updated_at(cls, value: Any) -> Any:
        return value.isoformat() if isinstance(value, date) else value


def _extract_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        title = line.strip().lstrip("#").strip()
        if title:
            return title
    return fallback


def _get_entry_docs_summary(root: Path, entry_docs: list[str]) -> list[dict[str, Any]]:
    """Extract short previews (up to 40 lines) of the configured entry documents."""
    results: list[dict[str, Any]] = []
    for doc_rel in entry_docs:
        norm = doc_rel.replace("\\", "/")
        try:
            target = resolve_read_path(root, norm)
        except (PermissionError, FileNotFoundError) as exc:
            results.append({"path": norm, "kind": "missing", "error": str(exc)})
            continue

        if target.is_file():
            try:
                ev = read_file_lines(root, rel_path=norm, start_line=1, end_line=40, max_lines=40, max_bytes=4096)
                results.append({"path": norm, "kind": "file", "content": ev.content, "content_sha256": ev.content_sha256, "truncated": ev.truncated})
            except (OSError, ValueError, RuntimeError) as exc:
                results.append({"path": norm, "kind": "file", "error": str(exc)})
        elif target.is_dir():
            md_files = [
                p.relative_to(root).as_posix()
                for p in sorted(target.rglob("*.md"))
                if resolve_project_file(root, p) is not None
            ]
            results.append({"path": norm, "kind": "directory", "files": md_files})
        else:
            results.append({"path": norm, "kind": "missing", "error": f"Path not found: {norm}"})
    return results


def _get_project_adrs(root: Path) -> list[dict[str, Any]]:
    """Scan and extract summary metadata for ADRs (.rmr/adr/*.md)."""
    try:
        adr_dir = resolve_read_path(root, ".rmr/adr")
    except (OSError, RuntimeError):
        return []
    if not adr_dir.is_dir():
        return []

    adrs: list[dict[str, Any]] = []
    for adr_path in sorted(adr_dir.glob("*.md")):
        target = resolve_project_file(root, adr_path)
        if target is None:
            continue
        with suppress(OSError, ValueError, YAMLError):
            post = frontmatter.loads(target.read_text(encoding="utf-8", errors="replace"))
            metadata = _AdrMetadata.model_validate(post.metadata)
            title = post.get("title") or _extract_title(post.content, adr_path.stem)
            adrs.append({
                "id": metadata.id,
                "title": str(title),
                "status": metadata.status,
                "updated_at": metadata.updated_at,
                "file_path": f".rmr/adr/{adr_path.name}",
            })
    return adrs


def get_project_overview(
    project_id: str | None = None,
    subpath: str | None = None,
    config_path: Path | None = None,
    *,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> dict[str, Any]:
    """Return a Tier 1 overview of registered projects or a specific project."""
    if not project_id:
        configs = projects if projects is not None else load_projects_config(config_path)
        projects_list = [
            {"project_id": p.project_id, "root_path": p.root_path, "language": p.language, "entry_docs": p.entry_docs}
            for p in sorted(configs.values(), key=lambda x: x.project_id)
        ]
        return {"projects": projects_list}

    proj = get_project(project_id, config_path=config_path, projects=projects)
    root = resolve_project_root(proj, config_path=config_path, frozen=projects is not None)
    if subpath:
        resolve_read_path(root, subpath)

    tree_state = get_tree_state(root)
    all_files = list_project_files(root, subpath=subpath)
    truncated_files, is_truncated = truncate_list(all_files, max_count=200)

    return {
        "project_id": proj.project_id,
        "language": proj.language,
        "root_path": root.as_posix(),
        "tree_state": tree_state.model_dump(),
        "total_files": len(all_files),
        "files": truncated_files,
        "files_truncated": is_truncated,
        "entry_docs": _get_entry_docs_summary(root, proj.entry_docs),
        "adrs": _get_project_adrs(root),
    }


def register_overview_tool(
    mcp: FastMCP,
    config_path: Path | None = None,
    *,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> None:
    """Register the 'get_project_overview' tool on a FastMCP instance."""

    @mcp.tool(
        name="get_project_overview",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        description=(
            "Get a Tier 1 project overview: list registered projects (when project_id is omitted) "
            "or return the file tree, entry documents (entry_docs), ADRs, and working tree state (tree_state)."
        ),
    )
    def get_project_overview_tool(
        project_id: str | None = None,
        subpath: str | None = None,
    ) -> dict[str, Any]:
        return get_project_overview(
            project_id=project_id, subpath=subpath, config_path=config_path, projects=projects
        )
