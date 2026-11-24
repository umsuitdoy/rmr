"""Tier 2 Investigation Tool: investigate_codebase (TASK-18)."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from rmr.core.path_guard import resolve_project_file, resolve_read_path
from rmr.core.registry import get_project, resolve_project_root
from rmr.inspectors.ast_outline import extract_directory_outline
from rmr.inspectors.codegraph import trace_calls, trace_impact
from rmr.inspectors.git_cli import get_tree_state
from rmr.inspectors.search_cli import run_ast_grep, run_ripgrep
from rmr.inspectors.symbol_jedi import find_symbol
from rmr.schemas import ImpactDepth, InvestigateInput, InvestigationMode, ProjectConfig

__all__ = ["investigate_codebase", "register_investigate_tool"]


def _normalize_match_file(raw_file: str, root: Path, target_path: Path) -> str | None:
    """Giữ đường dẫn tương đối nếu kết quả là file trong project."""
    cand = Path(raw_file)
    if not cand.is_absolute():
        cand = (target_path / cand) if target_path.is_dir() else (target_path.parent / cand)
    resolved = resolve_project_file(root, cand)
    return resolved.relative_to(root).as_posix() if resolved is not None else None


def _normalize_matches(
    matches: list[dict], root: Path, target_path: Path
) -> list[dict]:
    normalized = []
    for match in matches:
        file = match.get("file", "")
        if file:
            file = _normalize_match_file(file, root, target_path)
            if file is None:
                continue
        normalized.append({**match, "file": file})
    return normalized


def investigate_codebase(
    project_id: str,
    mode: InvestigationMode,
    query: str = "",
    subpath: str | None = None,
    max_results: int = 30,
    config_path: Path | None = None,
    *,
    depth: int | None = None,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> dict[str, Any]:
    """Điều tra cấu trúc, tham chiếu và quan hệ gọi trong kho mã nguồn."""
    req = InvestigateInput(
        project_id=project_id,
        mode=mode,
        query=query,
        subpath=subpath,
        max_results=max_results,
        depth=depth,
    )

    proj = get_project(req.project_id, config_path=config_path, projects=projects)
    root = resolve_project_root(proj, config_path=config_path, frozen=projects is not None)
    target_path = resolve_read_path(root, req.subpath) if req.subpath else root
    tree_state = get_tree_state(root)

    if req.mode == "impact":
        return {
            "project_id": proj.project_id, "mode": req.mode, "tree_state": tree_state.model_dump(),
            **trace_impact(root, req.query.strip(), target_path if req.subpath else None,
                           req.max_results, req.depth if req.depth is not None else 2),
        }

    if req.mode in ("callers", "callees"):
        return {
            "project_id": proj.project_id,
            "mode": req.mode,
            "tree_state": tree_state.model_dump(),
            **trace_calls(
                root, req.mode, req.query.strip(),
                definition_file=target_path if req.subpath else None,
                max_results=req.max_results,
            ),
        }

    if req.mode == "outline":
        outlines, is_truncated = extract_directory_outline(
            root, subpath=req.subpath, max_results=req.max_results
        )
        return {
            "project_id": proj.project_id,
            "mode": "outline",
            "tree_state": tree_state.model_dump(),
            "outlines": outlines,
            "truncated": is_truncated,
        }

    if req.mode == "symbol":
        clean_query = req.query.strip()
        if not clean_query:
            raise ValueError("query is required when mode='symbol' (e.g. function or class name)")
        res = find_symbol(
            root, symbol_name=clean_query, subpath=req.subpath, max_results=req.max_results
        )
        return {
            "project_id": proj.project_id,
            "mode": "symbol",
            "tree_state": tree_state.model_dump(),
            "symbol": res["symbol"],
            "definition": res["definition"],
            "references": res["references"],
            "truncated": res["truncated"],
        }

    if req.mode == "grep":
        if not req.query or not req.query.strip():
            raise ValueError("query is required when mode='grep'")
        matches, is_truncated = run_ripgrep(
            target_path, query=req.query, max_results=req.max_results
        )
        return {
            "project_id": proj.project_id,
            "mode": "grep",
            "tree_state": tree_state.model_dump(),
            "matches": _normalize_matches(matches, root, target_path),
            "truncated": is_truncated,
        }

    if req.mode == "ast":
        if not req.query or not req.query.strip():
            raise ValueError("query is required when mode='ast' (structural AST pattern)")
        matches, is_truncated = run_ast_grep(
            target_path, pattern=req.query, language=proj.language, max_results=req.max_results
        )
        return {
            "project_id": proj.project_id,
            "mode": "ast",
            "tree_state": tree_state.model_dump(),
            "matches": _normalize_matches(matches, root, target_path),
            "truncated": is_truncated,
        }

    raise ValueError(f"Unsupported mode: {req.mode!r}")


def register_investigate_tool(
    mcp: FastMCP,
    config_path: Path | None = None,
    *,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> None:
    """Đăng ký tool 'investigate_codebase' lên đối tượng FastMCP."""

    @mcp.tool(
        name="investigate_codebase",
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        description=(
            "Điều tra kho mã nguồn Tier 2: "
            "'outline' (chữ ký class/hàm/docstring), 'symbol' (định nghĩa và references bằng Jedi), "
            "'grep' (văn bản/regex), 'ast' (mẫu cú pháp), "
            "'callers' (ai gọi symbol), 'callees' (symbol gọi ai), 'impact' (phụ thuộc bị ảnh hưởng). "
            "Với callers/callees/impact, truyền tên symbol vào query và dùng subpath để chọn file "
            "định nghĩa khi tên bị trùng. Impact dùng depth 1–5 (mặc định 2). Các chế độ này yêu cầu index CodeGraph cục bộ."
        ),
    )
    def investigate_codebase_tool(
        project_id: str,
        mode: InvestigationMode,
        query: str = "",
        subpath: str | None = None,
        max_results: int = 30,
        depth: ImpactDepth | None = None,
    ) -> dict[str, Any]:
        return investigate_codebase(
            project_id=project_id,
            mode=mode,
            query=query,
            subpath=subpath,
            max_results=max_results,
            config_path=config_path,
            projects=projects,
            depth=depth,
        )
