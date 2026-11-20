"""Read-only path guard for RMR Core Security."""

from pathlib import Path

__all__ = [
    "resolve_project_file",
    "resolve_read_path",
]


def resolve_read_path(
    project_root: Path,
    rel_path: str | Path | None = None,
    *,
    allow_missing: bool = False,
) -> Path:
    """
    Phân giải symlink và cưỡng chế đường dẫn đọc phải nằm trong project_root.

    Tham số:
        project_root: Đường dẫn gốc của project đã cấu hình.
        rel_path: Đường dẫn tương đối do client truyền vào (có thể là None).
        allow_missing: Cho phép file đã xóa khi duyệt index hoặc lịch sử Git.

    Trả về:
        Path vật lý thực tế đã được chuẩn hóa.

    Ngoại lệ:
        PermissionError: Nếu đường dẫn thoát khỏi project_root hoặc vi phạm symlink.
        FileNotFoundError: Nếu project_root hoặc file không tồn tại (khi strict=True).
    """
    root = project_root.resolve(strict=True)
    if not rel_path:
        return root

    target = (root / rel_path).resolve(strict=not allow_missing)
    if target != root and root not in target.parents:
        raise PermissionError(f"Path escapes project root: {rel_path!r}")
    return target


def resolve_project_file(project_root: Path, path: Path) -> Path | None:
    """Resolve a discovered file without following links outside the project."""
    try:
        target = resolve_read_path(project_root, path)
    except (OSError, RuntimeError, ValueError):
        return None
    return target if target.is_file() else None
