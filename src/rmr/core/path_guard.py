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
    Resolve symlinks and enforce that the target read path stays within project_root.

    Args:
        project_root: Configured root directory of the project.
        rel_path: Relative path provided by the client (may be None).
        allow_missing: Allow missing/deleted files when inspecting Git index or history.

    Returns:
        Normalized real filesystem Path.

    Raises:
        PermissionError: If the path escapes project_root or violates symlink boundaries.
        FileNotFoundError: If project_root or the target file does not exist (when strict=True).
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
