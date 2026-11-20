"""Semantic Symbol Navigator: Locate definitions and references using Jedi."""

import tempfile
from pathlib import Path

import jedi
import parso.cache

from rmr.core.path_guard import resolve_project_file, resolve_read_path
from rmr.core.truncator import truncate_list

__all__ = [
    "find_symbol",
]

JEDI_ERRORS = (
    jedi.InternalError,
    AttributeError,
    ValueError,
    TypeError,
    KeyError,
    OSError,
    IndexError,
)


def _configure_jedi_cache() -> None:
    """Cấu hình cache trỏ vào tempdir để tránh lỗi phân quyền trên Windows."""
    tmp = Path(tempfile.gettempdir()) / "rmr_jedi_cache"
    parso.cache._default_cache_path = tmp
    jedi.settings.cache_directory = tmp


def _locate_target_def(project: jedi.Project, symbol: str, hint: Path | None):
    """Tìm đối tượng định nghĩa symbol qua file gợi ý hoặc tìm kiếm toàn dự án."""
    if hint and hint.is_file():
        try:
            s = jedi.Script(path=hint, project=project)
            names = [
                n
                for n in s.get_names(all_scopes=True, references=True)
                if n.name == symbol
            ]
            if names:
                goto_defs = names[0].goto()
                return goto_defs[0] if goto_defs else names[0]
        except JEDI_ERRORS:
            pass

    try:
        candidates = list(project.search(symbol))
        exact = [n for n in candidates if n.name == symbol and n.is_definition()] or [
            n for n in candidates if n.name == symbol
        ]
        if not exact:
            return None
        if hint:
            hint_res = hint.resolve()
            matched = [
                n
                for n in exact
                if n.module_path
                and (
                    hint_res in Path(n.module_path).resolve().parents
                    or Path(n.module_path).resolve() == hint_res
                )
            ]
            chosen = matched[0] if matched else exact[0]
        else:
            chosen = exact[0]
        goto_defs = chosen.goto()
        return goto_defs[0] if goto_defs else chosen
    except JEDI_ERRORS:
        return None


def _format_definition(target_def, root: Path) -> tuple[dict | None, Path | None]:
    """Định dạng vị trí định nghĩa của symbol."""
    if not getattr(target_def, "module_path", None):
        return None, None
    def_path = resolve_project_file(root, Path(target_def.module_path))
    if def_path is None:
        return None, None
    try:
        start = target_def.get_definition_start_position()
        end = target_def.get_definition_end_position()
        l_start = start[0] if start else target_def.line
        l_end = max(l_start, end[0] if end else target_def.line)
        doc = (target_def.docstring(raw=True) or target_def.docstring() or "").strip()
        return {
            "file": def_path.relative_to(root).as_posix(),
            "line_start": l_start,
            "line_end": l_end,
            "docstring": doc,
        }, def_path
    except JEDI_ERRORS:
        return None, None


def find_symbol(
    project_root: Path | str,
    symbol_name: str,
    subpath: str | None = None,
    max_results: int = 30,
) -> dict:
    """Tìm vị trí định nghĩa và danh sách nơi tham chiếu của một symbol bằng Jedi."""
    root = Path(project_root).resolve(strict=True)
    hint_path = resolve_read_path(root, subpath) if subpath else None
    _configure_jedi_cache()

    try:
        project = jedi.Project(path=root)
    except JEDI_ERRORS:
        return {
            "symbol": symbol_name,
            "definition": None,
            "references": [],
            "truncated": False,
        }

    target_def = _locate_target_def(project, symbol_name, hint_path)
    if not target_def:
        return {
            "symbol": symbol_name,
            "definition": None,
            "references": [],
            "truncated": False,
        }

    definition_dict, def_path = _format_definition(target_def, root)
    if not definition_dict or not def_path:
        return {
            "symbol": symbol_name,
            "definition": None,
            "references": [],
            "truncated": False,
        }

    ref_items: list[dict] = []
    seen: set[tuple[str, int]] = set()
    try:
        script = jedi.Script(path=def_path, project=project)
        for ref in script.get_references(
            line=target_def.line, column=target_def.column
        ):
            if not getattr(ref, "module_path", None):
                continue
            ref_path = resolve_project_file(root, Path(ref.module_path))
            if ref_path is None:
                continue
            rel = ref_path.relative_to(root).as_posix()
            if (rel, ref.line) in seen:
                continue
            seen.add((rel, ref.line))
            ref_items.append(
                {
                    "file": rel,
                    "line": ref.line,
                    "snippet": (ref.get_line_code() or "").strip(),
                }
            )
    except JEDI_ERRORS:
        ref_items = []

    truncated_refs, is_truncated = truncate_list(
        ref_items, max_count=min(max_results, 50)
    )
    return {
        "symbol": symbol_name,
        "definition": definition_dict,
        "references": truncated_refs,
        "truncated": is_truncated,
    }
