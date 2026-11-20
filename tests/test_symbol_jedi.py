"""Unit tests for Semantic Symbol Navigator using Jedi (TASK-09)."""

from pathlib import Path

import pytest

from rmr.inspectors.symbol_jedi import find_symbol


class TestSymbolJedi:
    def test_find_symbol_definition_and_references(self, tmp_path: Path) -> None:
        # Create a small project structure
        pkg = tmp_path / "my_pkg"
        pkg.mkdir()

        mod_a = pkg / "module_a.py"
        mod_a.write_text(
            '''"""Module A."""

def compute_total(items: list[int]) -> int:
    """Calculate the sum of all elements in items."""
    return sum(items)
''',
            encoding="utf-8",
        )

        mod_b = pkg / "module_b.py"
        mod_b.write_text(
            '''"""Module B."""
from my_pkg.module_a import compute_total

def run():
    val = compute_total([1, 2, 3])
    return val
''',
            encoding="utf-8",
        )

        res = find_symbol(tmp_path, "compute_total", subpath="my_pkg/module_a.py")
        assert res["symbol"] == "compute_total"
        assert res["definition"] is not None

        defn = res["definition"]
        assert defn["file"] == "my_pkg/module_a.py"
        assert defn["line_start"] == 3
        assert defn["line_end"] >= 3
        assert "Calculate the sum" in defn["docstring"]

        refs = res["references"]
        assert len(refs) >= 1
        ref_files = [r["file"] for r in refs]
        assert "my_pkg/module_b.py" in ref_files
        assert any("compute_total([1, 2, 3])" in r["snippet"] for r in refs)

    def test_find_symbol_nonexistent_graceful(self, tmp_path: Path) -> None:
        res = find_symbol(tmp_path, "nonexistent_symbol_xyz_123")
        assert res == {
            "symbol": "nonexistent_symbol_xyz_123",
            "definition": None,
            "references": [],
            "truncated": False,
        }

    def test_symbol_jedi_references_field_name(self, tmp_path: Path) -> None:
        src = tmp_path / "app.py"
        src.write_text("class MyService:\n    pass\n", encoding="utf-8")

        res = find_symbol(tmp_path, "MyService")
        # Assert strictly that the key is named 'references', never 'callers'
        assert "references" in res
        assert "callers" not in res
        assert isinstance(res["references"], list)

    def test_symbol_jedi_subpath_traversal_blocked(self, tmp_path: Path) -> None:
        with pytest.raises((PermissionError, FileNotFoundError)):
            find_symbol(tmp_path, "any_symbol", subpath="../../etc/passwd")

    def test_find_symbol_truncation(self, tmp_path: Path) -> None:
        pkg = tmp_path / "trunc_pkg"
        pkg.mkdir()

        (pkg / "core.py").write_text("def shared_hook():\n    pass\n", encoding="utf-8")
        # Create 10 files referencing shared_hook
        for i in range(10):
            (pkg / f"consumer_{i}.py").write_text(
                f"from trunc_pkg.core import shared_hook\ndef call_{i}(): shared_hook()\n",
                encoding="utf-8",
            )

        res = find_symbol(tmp_path, "shared_hook", max_results=3)
        assert res["definition"] is not None
        assert res["truncated"] is True
        assert len(res["references"]) == 3
