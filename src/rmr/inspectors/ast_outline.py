"""AST Outline Inspector: Extract symbols, signatures and docstrings from Python files."""

import ast
from pathlib import Path

from rmr.core.path_guard import resolve_project_file, resolve_read_path
from rmr.core.truncator import truncate_list

__all__ = [
    "extract_directory_outline",
    "extract_file_outline",
]


def _format_func(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    """Trích xuất thông tin hàm/method: tên, tham số, khoảng dòng và dòng đầu docstring."""
    args: list[str] = [a.arg for a in getattr(node.args, "posonlyargs", [])]
    args.extend(a.arg for a in node.args.args)
    if hasattr(node.args, "kwonlyargs"):
        for a in node.args.kwonlyargs:
            if a.arg not in args:
                args.append(a.arg)

    doc_raw = ast.get_docstring(node) or ""
    doc_lines = [line.strip() for line in doc_raw.splitlines() if line.strip()]

    return {
        "kind": "function",
        "name": node.name,
        "args": args,
        "lines": [node.lineno, getattr(node, "end_lineno", node.lineno)],
        "doc": doc_lines[0] if doc_lines else "",
    }


def extract_file_outline(py_file: Path, rel_path: str) -> dict:
    """Trích xuất chữ ký class/function và dòng đầu docstring từ một file Python."""
    normalized_rel = rel_path.replace("\\", "/")
    if not py_file.is_file():
        return {
            "file": normalized_rel,
            "symbols": [],
            "error": f"File not found: {py_file}",
        }

    try:
        source = py_file.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=normalized_rel)
    except (SyntaxError, OSError, UnicodeDecodeError) as exc:
        return {
            "file": normalized_rel,
            "symbols": [],
            "error": str(exc),
        }

    items: list[dict] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append(_format_func(node))
        elif isinstance(node, ast.ClassDef):
            methods = [
                _format_func(sub)
                for sub in node.body
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            doc_raw = ast.get_docstring(node) or ""
            doc_lines = [line.strip() for line in doc_raw.splitlines() if line.strip()]
            items.append(
                {
                    "kind": "class",
                    "name": node.name,
                    "lines": [node.lineno, getattr(node, "end_lineno", node.lineno)],
                    "doc": doc_lines[0] if doc_lines else "",
                    "methods": methods,
                }
            )

    return {
        "file": normalized_rel,
        "symbols": items,
        "error": None,
    }


def extract_directory_outline(
    project_root: Path | str,
    subpath: str | None = None,
    max_results: int = 30,
) -> tuple[list[dict], bool]:
    """Quét các file .py trong subpath (hoặc project_root) và trích xuất outline."""
    root = Path(project_root).resolve(strict=True)
    target_path = resolve_read_path(root, subpath)

    if target_path.is_file():
        rel_path = target_path.relative_to(root).as_posix()
        return [extract_file_outline(target_path, rel_path)], False

    py_files: list[tuple[Path, str]] = []
    for p in sorted(target_path.rglob("*.py")):
        try:
            rel = p.relative_to(target_path)
        except ValueError:
            continue
        if any(
            part.startswith(".") or part in ("__pycache__", "venv")
            for part in rel.parts
        ):
            continue
        resolved = resolve_project_file(root, p)
        if resolved is not None:
            py_files.append((resolved, p.relative_to(root).as_posix()))

    selected_files, is_truncated = truncate_list(py_files, max_count=max_results)
    outlines = [
        extract_file_outline(file, rel_path) for file, rel_path in selected_files
    ]
    return outlines, is_truncated
