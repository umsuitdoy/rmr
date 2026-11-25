"""Safe CLI Search Inspector: Lexical (ripgrep) and structural (ast-grep) search."""

import json
import re
import subprocess
from pathlib import Path

from rmr.core.path_guard import resolve_project_file, resolve_read_path

__all__ = ["run_ast_grep", "run_ripgrep"]

IGNORED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
SAFE_LANG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+#.-]*$")


def _normalize_match_path(raw_file: str | Path, target_input: Path, resolved: Path | None) -> str:
    """Normalize a matched file path to relative POSIX format ('/')."""
    cand = Path(raw_file)
    if resolved is not None and cand.is_absolute():
        try:
            res_cand = cand.resolve()
            if resolved.is_dir():
                rel = res_cand.relative_to(resolved)
                return rel.as_posix() if target_input.is_absolute() else (target_input / rel).as_posix()
            if res_cand == resolved:
                return resolved.name if target_input.is_absolute() else target_input.as_posix()
        except (ValueError, OSError):
            pass
    return str(raw_file).replace("\\", "/")


def _python_grep_fallback(target_input: Path, resolved: Path, query: str, limit: int) -> tuple[list[dict], bool]:
    """Pure-Python search fallback when ripgrep (rg) is not installed."""
    try:
        regex = re.compile(query)
    except re.error:
        regex = re.compile(re.escape(query))

    if resolved.is_file():
        files = [resolved]
    elif resolved.is_dir():
        files = [
            p for p in sorted(resolved.rglob("*"))
            if not any(part in IGNORED_DIRS or part.startswith(".git") for part in p.relative_to(resolved).parts)
        ]
    else:
        return [], False

    root = resolved.parent if resolved.is_file() else resolved
    matches: list[dict] = []
    for file_path in files:
        safe_file = resolve_project_file(root, file_path)
        if safe_file is None:
            continue
        try:
            content = safe_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        norm = _normalize_match_path(file_path, target_input, resolved)
        for lineno, line_text in enumerate(content.splitlines(), start=1):
            if regex.search(line_text):
                matches.append({"file": norm, "line": lineno, "snippet": line_text.rstrip("\r\n")})
                if len(matches) > limit:
                    return matches[:limit], True
    return matches, False


def _exec_cli(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Execute a CLI subprocess safely with shell=False."""
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=False, timeout=timeout, check=False
    )


def run_ripgrep(
    target_path: Path | str, query: str, max_results: int = 30, timeout: int = 5
) -> tuple[list[dict], bool]:
    """Run ripgrep with JSON stream output, preventing argument injection via '--'."""
    if not query:
        return [], False
    target_input = Path(target_path)
    resolved = resolve_read_path(target_input) if target_input.exists() else None
    limit = max(1, min(max_results, 50))
    cmd = [
        "rg", "--no-config", "--no-follow", "--json", "--max-count",
        str(limit + 1), "--", query, str(target_input),
    ]

    try:
        res = _exec_cli(cmd, timeout)
    except FileNotFoundError:
        return _python_grep_fallback(target_input, resolved, query, limit) if resolved else ([], False)
    except subprocess.TimeoutExpired:
        return [], False

    matches: list[dict] = []
    for line in res.stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "match":
            data = payload.get("data", {})
            raw_file = data.get("path", {}).get("text", "")
            if not raw_file:
                continue
            matches.append({
                "file": _normalize_match_path(raw_file, target_input, resolved),
                "line": int(data.get("line_number", 0)),
                "snippet": data.get("lines", {}).get("text", "").rstrip("\r\n"),
            })
            if len(matches) > limit:
                return matches[:limit], True
    return matches, False


def run_ast_grep(
    target_path: Path | str,
    pattern: str,
    language: str = "python",
    max_results: int = 30,
    timeout: int = 5,
) -> tuple[list[dict], bool]:
    """Run ast-grep for structural AST pattern search, preventing argument injection via '--'."""
    if not pattern:
        return [], False
    if not language or not SAFE_LANG_RE.match(language):
        raise ValueError(f"Invalid language parameter: {language!r}")

    target_input = Path(target_path)
    resolved = resolve_read_path(target_input) if target_input.exists() else None
    limit = max(1, min(max_results, 50))
    cmd = ["sg", "run", "--pattern", pattern, "--lang", language, "--json", "--", str(target_input)]

    try:
        res = _exec_cli(cmd, timeout)
    except FileNotFoundError:
        return [{"file": "", "line": 0, "snippet": "Note: ast-grep (sg) is not installed on this system"}], False
    except subprocess.TimeoutExpired:
        return [], False

    out = res.stdout.strip()
    if not out:
        return [], False
    try:
        raw_items = json.loads(out) if out.startswith("[") else [json.loads(ln) for ln in out.splitlines() if ln.strip()]
    except json.JSONDecodeError:
        return [], False

    matches: list[dict] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        raw_file = item.get("file") or item.get("path", {}).get("text", "")
        if not raw_file:
            continue
        line_no = int(item["line"]) if "line" in item else int(item.get("range", {}).get("start", {}).get("line", 0)) + 1
        snippet = (item.get("lines") or item.get("text") or item.get("snippet") or "").rstrip("\r\n")
        matches.append({"file": _normalize_match_path(raw_file, target_input, resolved), "line": line_no, "snippet": snippet})
        if len(matches) > limit:
            return matches[:limit], True
    return matches, False
