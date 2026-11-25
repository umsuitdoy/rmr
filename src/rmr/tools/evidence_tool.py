"""Tier 3 Evidence Tool: inspect_evidence (TASK-19)."""

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from rmr.core.path_guard import resolve_read_path
from rmr.core.registry import get_project, resolve_project_root
from rmr.core.truncator import truncate_text_lines
from rmr.inspectors.file_reader import read_file_lines, read_symbol_content
from rmr.inspectors.git_cli import get_git_diff, get_tree_state
from rmr.schemas import (
    EvidenceItem,
    EvidenceTarget,
    InspectEvidenceOutput,
    ProjectConfig,
)

__all__ = ["inspect_evidence", "register_evidence_tool"]


def _process_target(root: Path, target: EvidenceTarget | dict[str, Any]) -> EvidenceItem:
    """Process a single evidence extraction target (file_lines, symbol, or git_diff)."""
    t = target if isinstance(target, EvidenceTarget) else EvidenceTarget(**target)
    norm_path = t.path.replace("\\", "/")

    # Guard against path traversal for every target
    resolve_read_path(root, norm_path, allow_missing=True)

    if t.kind == "file_lines":
        return read_file_lines(
            root,
            rel_path=norm_path,
            start_line=t.start_line,
            end_line=t.end_line,
        )

    if t.kind == "symbol":
        if not t.symbol_name or not t.symbol_name.strip():
            raise ValueError(f"symbol_name is required when kind='symbol' for path {t.path!r}")
        return read_symbol_content(
            root,
            rel_path=norm_path,
            symbol_name=t.symbol_name.strip(),
        )

    if t.kind == "git_diff":
        raw_diff = get_git_diff(
            root,
            rel_path=norm_path,
            base_ref=t.base_ref,
            target_ref=t.target_ref,
        )
        sha256_hex = hashlib.sha256(raw_diff.encode("utf-8")).hexdigest()
        content, is_truncated = truncate_text_lines(raw_diff, max_lines=200, max_bytes=8192)
        return EvidenceItem(
            kind="git_diff",
            path=norm_path,
            line_range=None,
            content_sha256=sha256_hex,
            content=content,
            truncated=is_truncated,
        )

    raise ValueError(f"Unsupported evidence kind: {t.kind!r}")


def inspect_evidence(
    project_id: str,
    targets: list[EvidenceTarget | dict[str, Any]],
    config_path: Path | None = None,
    *,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> dict[str, Any]:
    """Extract exact technical evidence (Tier 3) with SHA-256 hashes and working tree state."""
    if not targets:
        raise ValueError("targets list must not be empty")

    proj = get_project(project_id, config_path=config_path, projects=projects)
    root = resolve_project_root(proj, config_path=config_path, frozen=projects is not None)
    tree_state = get_tree_state(root)

    items: list[EvidenceItem] = [_process_target(root, t) for t in targets]

    output = InspectEvidenceOutput(
        project_id=proj.project_id,
        tree_state=tree_state,
        evidence_items=items,
    )
    return output.model_dump()


def register_evidence_tool(
    mcp: FastMCP,
    config_path: Path | None = None,
    *,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> None:
    """Register the 'inspect_evidence' tool on a FastMCP instance."""

    @mcp.tool(
        name="inspect_evidence",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        description=(
            "Extract exact technical evidence (Tier 3) with SHA-256 content hashes "
            "and working tree state: read line ranges ('file_lines'), extract symbol source ('symbol'), "
            "or fetch a Git diff ('git_diff')."
        ),
    )
    def inspect_evidence_tool(
        project_id: str,
        targets: list[EvidenceTarget],
    ) -> dict[str, Any]:
        return inspect_evidence(
            project_id=project_id, targets=targets, config_path=config_path, projects=projects
        )
