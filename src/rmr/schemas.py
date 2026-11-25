"""Pydantic v2 data contracts for RMR Core & MCP Tools."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceTarget",
    "InspectEvidenceOutput",
    "InvestigateInput",
    "InvestigationMode",
    "ProjectConfig",
    "TreeState",
]


class TreeState(BaseModel):
    """Git worktree state and consistency fingerprint."""

    head_commit: str = Field(description="Short 7-character hash of the current HEAD commit")
    is_dirty: bool = Field(
        description="True if the working tree has modified or untracked files"
    )
    worktree_fingerprint: str = Field(
        description="12-character SHA-256 hash computed from git status porcelain and dirty file mtimes"
    )


class ProjectConfig(BaseModel):
    """Configuration for a project managed by RMR."""

    project_id: str = Field(description="Unique project identifier")
    root_path: str = Field(description="Filesystem path to the project root directory")
    language: str = Field(default="python", description="Primary programming language of the project")
    entry_docs: list[str] = Field(
        default_factory=list,
        description="List of core documentation files (e.g., README, architecture)",
    )


# --- Tool 2: investigate_codebase ---
ImpactDepth = Annotated[int, Field(strict=True, ge=1, le=5)]

InvestigationMode = Literal["outline", "symbol", "grep", "ast", "callers", "callees", "impact"]


class InvestigateInput(BaseModel):
    """Parameters for multi-mode codebase investigation."""

    project_id: str
    mode: InvestigationMode
    query: str = Field(
        default="", description="Symbol name, regex pattern, or AST pattern"
    )
    subpath: str | None = Field(
        default=None,
        description="Relative path. For callers/callees/impact, specifies the file defining the symbol.",
    )
    max_results: int = Field(
        default=30, ge=1, le=50, description="Maximum number of results (up to 50)"
    )
    depth: ImpactDepth | None = Field(default=None, description="Impact dependency depth (1–5; default 2)")

    @field_validator("depth")
    @classmethod
    def _impact_depth_only(cls, value, info):
        if value is not None and info.data.get("mode") != "impact":
            raise ValueError("depth is only supported for impact mode")
        return value


# --- Tool 3: inspect_evidence ---
EvidenceKind = Literal["file_lines", "symbol", "git_diff"]


class EvidenceTarget(BaseModel):
    """Target specification for technical evidence extraction."""

    kind: EvidenceKind
    path: str = Field(description="Relative path to a file within the project")
    start_line: int | None = Field(default=None, ge=1, description="Start line (1-indexed)")
    end_line: int | None = Field(default=None, ge=1, description="End line (1-indexed)")
    symbol_name: str | None = Field(default=None, description="Class or function name when kind='symbol'")
    base_ref: str = Field(default="HEAD", description="Base Git ref when kind='git_diff'")
    target_ref: str = Field(default="WORKTREE", description="Target Git ref or 'WORKTREE'")


class EvidenceItem(BaseModel):
    """Safely extracted technical evidence block."""

    kind: EvidenceKind
    path: str
    line_range: tuple[int, int] | None = None
    content_sha256: str | None = Field(default=None, description="SHA-256 hash of the content")
    content: str = Field(description="Source code or diff content")
    truncated: bool = Field(default=False, description="True if the content was truncated")


class InspectEvidenceOutput(BaseModel):
    """Aggregated result from inspect_evidence including the worktree fingerprint."""

    project_id: str
    tree_state: TreeState
    evidence_items: list[EvidenceItem]
