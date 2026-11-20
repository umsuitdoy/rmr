"""Unit tests for Evidence File Reader (TASK-11)."""

import hashlib
from pathlib import Path

import pytest

from rmr.inspectors.file_reader import read_file_lines, read_symbol_content


class TestFileReader:
    def test_read_file_lines_exact_range(self, tmp_path: Path) -> None:
        sample = tmp_path / "sample.py"
        lines = [f"line_{i}" for i in range(1, 11)]
        sample.write_text("\n".join(lines) + "\n", encoding="utf-8")

        item = read_file_lines(tmp_path, "sample.py", start_line=3, end_line=6)
        assert item.kind == "file_lines"
        assert item.path == "sample.py"
        assert item.line_range == (3, 6)
        assert item.content == "line_3\nline_4\nline_5\nline_6"
        assert item.truncated is False

    def test_read_file_lines_sha256(self, tmp_path: Path) -> None:
        sample = tmp_path / "hash_check.py"
        sample.write_bytes(b"alpha = 1\r\nbeta = 2\r\ngamma = 3\r\n")

        item = read_file_lines(tmp_path, "hash_check.py", start_line=1, end_line=2)
        expected_raw = "alpha = 1\nbeta = 2"
        expected_sha = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()

        assert item.content_sha256 is not None
        assert len(item.content_sha256) == 64
        assert item.content_sha256 == expected_sha
        assert item.content == expected_raw

    def test_read_file_lines_hard_truncation(self, tmp_path: Path) -> None:
        many_lines_file = tmp_path / "large.py"
        raw_lines = [f"x_{i} = {i}" for i in range(1, 260)]
        raw_text = "\n".join(raw_lines)
        many_lines_file.write_text(raw_text, encoding="utf-8")

        item = read_file_lines(tmp_path, "large.py")
        assert item.truncated is True
        assert "[... Truncated:" in item.content
        # SHA-256 is computed on the raw slice before truncation notice
        assert item.content_sha256 == hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

        large_bytes_file = tmp_path / "huge_bytes.py"
        huge_line = "a" * 9000
        large_bytes_file.write_text(huge_line, encoding="utf-8")
        item_bytes = read_file_lines(tmp_path, "huge_bytes.py")
        assert item_bytes.truncated is True
        assert item_bytes.content_sha256 == hashlib.sha256(huge_line.encode("utf-8")).hexdigest()

    def test_read_file_lines_path_traversal_blocked(self, tmp_path: Path) -> None:
        proj = tmp_path / "repo"
        proj.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("top secret", encoding="utf-8")

        with pytest.raises(PermissionError):
            read_file_lines(proj, "../../etc/passwd")

        with pytest.raises(PermissionError):
            read_file_lines(proj, "../secret.txt")

    def test_read_symbol_content_success(self, tmp_path: Path) -> None:
        mod = tmp_path / "calculator.py"
        mod.write_text(
            '''"""Calculator module."""

class MathEngine:
    """Core math class."""

    def multiply(self, a: int, b: int) -> int:
        """Multiply two integers."""
        return a * b


def standalone_add(x: int, y: int) -> int:
    """Add two integers."""
    res = x + y
    return res
''',
            encoding="utf-8",
        )

        fn_item = read_symbol_content(tmp_path, "calculator.py", "standalone_add")
        assert fn_item.kind == "symbol"
        assert fn_item.path == "calculator.py"
        assert fn_item.line_range == (11, 14)
        assert "def standalone_add" in fn_item.content
        assert "return res" in fn_item.content
        assert fn_item.truncated is False

        meth_item = read_symbol_content(tmp_path, "calculator.py", "MathEngine.multiply")
        assert meth_item.kind == "symbol"
        assert meth_item.line_range == (6, 8)
        assert "def multiply" in meth_item.content
        assert "return a * b" in meth_item.content

    def test_read_symbol_content_not_found(self, tmp_path: Path) -> None:
        mod = tmp_path / "empty_sym.py"
        mod.write_text("def existing_fn():\n    return 1\n", encoding="utf-8")

        with pytest.raises(KeyError, match="missing_symbol"):
            read_symbol_content(tmp_path, "empty_sym.py", "missing_symbol")
