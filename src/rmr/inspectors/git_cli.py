"""Git CLI Inspector: Read-only interaction with Git repository and worktree."""

import hashlib
import re
import subprocess
from pathlib import Path

from rmr.core.path_guard import resolve_read_path
from rmr.schemas import TreeState

__all__ = [
    "get_git_diff",
    "get_tree_state",
    "list_project_files",
]

REF_PATTERN = re.compile(r"^[A-Za-z0-9_./~^@-]+$")


def _run_git(repo_root: Path | str, args: list[str], timeout: int = 5) -> str:
    """Run a git subprocess safely with shell=False and UTF-8 encoding."""
    cmd = ["git", "-C", str(Path(repo_root)), "-c", "core.fsmonitor=false", *args]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout,
        check=True,
    )
    return res.stdout


def get_tree_state(repo_root: Path | str) -> TreeState:
    """Compute head_commit, is_dirty, and worktree_fingerprint to detect changes between calls.

    Fingerprint hashing logic:
    - SHA-256 of head_commit + porcelain output + mtime/size of modified or untracked files.
    - Truncated to the first 12 hex characters.
    """
    root = Path(repo_root).resolve(strict=True)
    try:
        head_commit = _run_git(root, ["rev-parse", "--short", "HEAD"]).strip()
        if len(head_commit) > 7:
            head_commit = head_commit[:7]
    except subprocess.CalledProcessError:
        head_commit = "0000000"

    porcelain = _run_git(root, ["status", "--porcelain=v1"])
    lines = [line for line in porcelain.splitlines() if line.strip()]
    is_dirty = len(lines) > 0

    hasher = hashlib.sha256()
    hasher.update(head_commit.encode("utf-8"))
    hasher.update(porcelain.encode("utf-8"))

    for line in lines:
        if len(line) < 3:
            continue
        rel_file = line[3:].strip().strip('"')
        if " -> " in rel_file:
            rel_file = rel_file.split(" -> ")[-1].strip().strip('"')
        try:
            candidate = resolve_read_path(root, rel_file)
            if candidate.is_file():
                stat = candidate.stat()
                hasher.update(f"{rel_file}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        except (OSError, RuntimeError):
            continue

    return TreeState(
        head_commit=head_commit,
        is_dirty=is_dirty,
        worktree_fingerprint=hasher.hexdigest()[:12],
    )


def list_project_files(repo_root: Path | str, subpath: str | None = None) -> list[str]:
    """List project files (both tracked and untracked, respecting .gitignore).

    Command:
        git -C <repo_root> ls-files --cached --others --exclude-standard [-- <subpath>]

    Returns:
        Deduplicated, alphabetically sorted list of relative POSIX file paths ('/').
    """
    root = Path(repo_root).resolve(strict=True)
    args = ["--literal-pathspecs", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    if subpath:
        args.extend(["--", str(subpath).replace("\\", "/")])

    out = _run_git(root, args)
    files = []
    for rel_path in filter(None, out.split("\0")):
        try:
            resolve_read_path(root, rel_path, allow_missing=True)
        except (OSError, RuntimeError, ValueError):
            continue
        files.append(rel_path)
    return sorted(set(files))


def get_git_diff(
    repo_root: Path | str,
    rel_path: str | None = None,
    base_ref: str = "HEAD",
    target_ref: str = "WORKTREE",
) -> str:
    """Safely extract a git diff.

    Args:
        repo_root: Repository root directory.
        rel_path: Relative file path (placed after '--' to prevent argument injection).
        base_ref: Base commit hash, branch, or tag (default 'HEAD').
        target_ref: Target ref or 'WORKTREE' (compares against the working directory when 'WORKTREE').
    """
    root = Path(repo_root).resolve(strict=True)
    if not base_ref or base_ref.startswith("-") or not REF_PATTERN.match(base_ref):
        raise ValueError(f"Invalid base_ref: {base_ref!r}")

    args = [
        "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv",
        "--ignore-submodules=all", base_ref,
    ]
    if target_ref != "WORKTREE":
        if (
            not target_ref
            or target_ref.startswith("-")
            or not REF_PATTERN.match(target_ref)
        ):
            raise ValueError(f"Invalid target_ref: {target_ref!r}")
        args.append(target_ref)

    if rel_path:
        resolve_read_path(root, rel_path, allow_missing=True)
        args.extend(["--", str(rel_path).replace("\\", "/")])
    else:
        args.append("--")

    return _run_git(root, args)
