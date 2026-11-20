"""Automated security invariants and defensive boundaries verification (TASK-06)."""

from pathlib import Path

import pytest

from rmr.core.path_guard import resolve_read_path


def test_path_traversal_blocked(tmp_path: Path) -> None:
    """Ensure path traversal attempts are strictly blocked by resolve_read_path."""
    project_root = tmp_path / "my_project"
    project_root.mkdir()

    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("classified data", encoding="utf-8")

    # Relative traversal to an existing file outside project root must raise PermissionError
    with pytest.raises(PermissionError):
        resolve_read_path(project_root, "../secret.txt")

    # Deep relative traversal escaping root
    with pytest.raises((PermissionError, FileNotFoundError)):
        resolve_read_path(project_root, "../../etc/passwd")

    # Windows-style backslash traversal
    with pytest.raises((PermissionError, FileNotFoundError)):
        resolve_read_path(project_root, "..\\..\\secret.txt")

    # Absolute path escape outside root
    drive_root = Path(project_root.anchor)
    with pytest.raises(PermissionError):
        resolve_read_path(project_root, str(drive_root))

    # Safe file within project root resolves successfully
    safe_file = project_root / "safe.py"
    safe_file.write_text("print('safe')", encoding="utf-8")
    resolved = resolve_read_path(project_root, "safe.py")
    assert resolved == safe_file.resolve()


def test_symlink_escape_blocked(tmp_path: Path) -> None:
    """Ensure symlinks pointing outside the project root are rejected with PermissionError."""
    project_root = tmp_path / "my_project"
    project_root.mkdir()

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret.txt"
    secret_file.write_text("confidential", encoding="utf-8")

    leak_link = project_root / "leak_link.txt"
    try:
        leak_link.symlink_to(secret_file)
    except OSError:
        pytest.skip("Symlink creation requires elevated privileges on this OS/environment")

    with pytest.raises(PermissionError):
        resolve_read_path(project_root, "leak_link.txt")
