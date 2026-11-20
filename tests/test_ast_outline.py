"""Unit tests for AST Outline Inspector (TASK-08)."""

from pathlib import Path

import pytest

from rmr.inspectors.ast_outline import (
    extract_directory_outline,
    extract_file_outline,
)


class TestAstOutline:
    def test_extract_file_outline_classes_and_functions(self, tmp_path: Path) -> None:
        sample_code = '''"""Module docstring."""

class Calculator:
    """Class docstring line 1.
    More explanation here.
    """

    def __init__(self, base: int = 0):
        self.base = base

    def add(self, x: int, y: int) -> int:
        """Add two numbers."""
        return x + y


def helper_func(val: str, flag: bool = False):
    """Standalone helper function."""
    return val


async def async_fetch(url: str):
    """Fetch data asynchronously."""
    pass
'''
        file_path = tmp_path / "sample.py"
        file_path.write_text(sample_code, encoding="utf-8")

        res = extract_file_outline(file_path, "sample.py")
        assert res["file"] == "sample.py"
        assert res["error"] is None
        symbols = res["symbols"]
        assert len(symbols) == 3

        # Class verification
        cls_item = symbols[0]
        assert cls_item["kind"] == "class"
        assert cls_item["name"] == "Calculator"
        assert cls_item["doc"] == "Class docstring line 1."
        assert cls_item["lines"][0] == 3
        assert cls_item["lines"][1] > 3

        methods = cls_item["methods"]
        assert len(methods) == 2
        assert methods[0]["name"] == "__init__"
        assert methods[0]["args"] == ["self", "base"]
        assert methods[1]["name"] == "add"
        assert methods[1]["args"] == ["self", "x", "y"]
        assert methods[1]["doc"] == "Add two numbers."

        # Standalone function
        func_item = symbols[1]
        assert func_item["kind"] == "function"
        assert func_item["name"] == "helper_func"
        assert func_item["args"] == ["val", "flag"]
        assert func_item["doc"] == "Standalone helper function."

        # Async function
        async_item = symbols[2]
        assert async_item["kind"] == "function"
        assert async_item["name"] == "async_fetch"
        assert async_item["args"] == ["url"]
        assert async_item["doc"] == "Fetch data asynchronously."

    def test_docstring_first_line_only(self, tmp_path: Path) -> None:
        code = '''
def multi_line_doc():
    """
    Line one of documentation.
    Line two of documentation.
    Line three with details.
    """
    pass

def single_line_doc():
    """Single line documentation."""
    pass

def no_doc():
    pass
'''
        py_file = tmp_path / "docs_test.py"
        py_file.write_text(code, encoding="utf-8")

        res = extract_file_outline(py_file, "docs_test.py")
        symbols = {s["name"]: s for s in res["symbols"]}

        assert symbols["multi_line_doc"]["doc"] == "Line one of documentation."
        assert symbols["single_line_doc"]["doc"] == "Single line documentation."
        assert symbols["no_doc"]["doc"] == ""

    def test_extract_file_outline_syntax_error(self, tmp_path: Path) -> None:
        bad_code = "def broken_func(:\n    pass\n"
        py_file = tmp_path / "syntax_err.py"
        py_file.write_text(bad_code, encoding="utf-8")

        res = extract_file_outline(py_file, "syntax_err.py")
        assert res["file"] == "syntax_err.py"
        assert res["symbols"] == []
        assert res["error"] is not None
        assert "invalid syntax" in res["error"] or "expected" in res["error"]

    def test_extract_directory_outline_truncation(self, tmp_path: Path) -> None:
        # Create project root with multiple python files
        src_dir = tmp_path / "pkg"
        src_dir.mkdir()

        for i in range(10):
            p = src_dir / f"mod_{i:02d}.py"
            p.write_text(f"def func_{i}(): pass\n", encoding="utf-8")

        # Test truncation with max_results=5
        outlines, is_truncated = extract_directory_outline(
            tmp_path, subpath="pkg", max_results=5
        )
        assert len(outlines) == 5
        assert is_truncated is True

        # Test non-truncated with max_results=20
        outlines_all, is_truncated_all = extract_directory_outline(
            tmp_path, subpath="pkg", max_results=20
        )
        assert len(outlines_all) == 10
        assert is_truncated_all is False

    def test_extract_directory_outline_single_file(self, tmp_path: Path) -> None:
        file_path = tmp_path / "single.py"
        file_path.write_text("def single_fn(): pass\n", encoding="utf-8")

        outlines, is_truncated = extract_directory_outline(
            tmp_path, subpath="single.py"
        )
        assert len(outlines) == 1
        assert is_truncated is False
        assert outlines[0]["symbols"][0]["name"] == "single_fn"

    def test_extract_directory_outline_path_traversal_blocked(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises((PermissionError, FileNotFoundError)):
            extract_directory_outline(tmp_path, subpath="../../etc/passwd")
