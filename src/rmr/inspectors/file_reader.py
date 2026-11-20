"""Evidence File Reader: Read exact line ranges or symbols with SHA-256 and hard truncation."""

import hashlib
from pathlib import Path

from rmr.core.path_guard import resolve_read_path
from rmr.core.truncator import truncate_text_lines
from rmr.inspectors.ast_outline import extract_file_outline
from rmr.schemas import EvidenceItem

__all__ = [
    "read_file_lines",
    "read_symbol_content",
]


def _resolve_safe_file(project_root: Path | str, rel_path: str) -> tuple[Path, Path, str]:
    """Xác thực đường dẫn đọc an toàn trong project_root và chuẩn hóa đường dẫn POSIX."""
    root = Path(project_root).resolve(strict=True)
    if not rel_path or not rel_path.strip():
        raise ValueError("rel_path must not be empty")

    pre_target = (root / rel_path).resolve(strict=False)
    if pre_target != root and root not in pre_target.parents:
        raise PermissionError(f"Path escapes project root: {rel_path!r}")

    target_path = resolve_read_path(root, rel_path)
    if not target_path.is_file():
        raise FileNotFoundError(f"Target is not a file: {rel_path!r}")

    normalized_rel = target_path.relative_to(root).as_posix()
    return root, target_path, normalized_rel


def read_file_lines(
    project_root: Path | str,
    rel_path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    max_lines: int = 200,
    max_bytes: int = 8192,
) -> EvidenceItem:
    """Đọc chính xác khoảng dòng của một file (1-indexed) kèm mã băm SHA-256 và cắt tải."""
    _, target_path, normalized_rel = _resolve_safe_file(project_root, rel_path)
    raw_text = target_path.read_text(encoding="utf-8", errors="replace")
    all_lines = raw_text.splitlines()
    total_lines = len(all_lines)

    if total_lines == 0:
        s = max(1, start_line if start_line is not None else 1)
        e = max(s, end_line if end_line is not None else s)
        selected_lines: list[str] = []
    else:
        s = max(1, start_line if start_line is not None else 1)
        e = min(total_lines, end_line if end_line is not None else total_lines)
        if s > total_lines:
            e = max(s, end_line if end_line is not None else s)
            selected_lines = []
        else:
            e = max(e, s)
            selected_lines = all_lines[s - 1 : e]

    raw_slice = "\n".join(selected_lines)
    sha256_hex = hashlib.sha256(raw_slice.encode("utf-8")).hexdigest()
    content, is_truncated = truncate_text_lines(
        raw_slice, max_lines=max_lines, max_bytes=max_bytes
    )

    return EvidenceItem(
        kind="file_lines",
        path=normalized_rel,
        line_range=(s, e),
        content_sha256=sha256_hex,
        content=content,
        truncated=is_truncated,
    )


def _find_symbol_lines(symbols: list[dict], symbol_name: str) -> tuple[int, int] | None:
    """Tìm khoảng dòng [line_start, line_end] của function/class hoặc Class.method."""
    if "." in symbol_name:
        cls_name, meth_name = symbol_name.split(".", 1)
        for sym in symbols:
            if sym.get("kind") == "class" and sym.get("name") == cls_name:
                for meth in sym.get("methods", []):
                    if meth.get("name") == meth_name:
                        lines = meth.get("lines", [1, 1])
                        return int(lines[0]), int(lines[1])
        return None

    for sym in symbols:
        if sym.get("name") == symbol_name:
            lines = sym.get("lines", [1, 1])
            return int(lines[0]), int(lines[1])

    for sym in symbols:
        if sym.get("kind") == "class":
            for meth in sym.get("methods", []):
                if meth.get("name") == symbol_name:
                    lines = meth.get("lines", [1, 1])
                    return int(lines[0]), int(lines[1])
    return None


def read_symbol_content(
    project_root: Path | str,
    rel_path: str,
    symbol_name: str,
    max_lines: int = 200,
    max_bytes: int = 8192,
) -> EvidenceItem:
    """Đọc trọn vẹn nội dung của một function/class theo symbol_name trong file."""
    root, target_path, normalized_rel = _resolve_safe_file(project_root, rel_path)
    outline = extract_file_outline(target_path, normalized_rel)
    line_bounds = _find_symbol_lines(outline.get("symbols", []), symbol_name)
    if line_bounds is None:
        raise KeyError(f"Symbol {symbol_name!r} not found in {normalized_rel}")

    item = read_file_lines(
        project_root=root,
        rel_path=normalized_rel,
        start_line=line_bounds[0],
        end_line=line_bounds[1],
        max_lines=max_lines,
        max_bytes=max_bytes,
    )
    return item.model_copy(update={"kind": "symbol"})
