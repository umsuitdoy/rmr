"""Integration coverage against the official pinned CodeGraph index and traversal."""
import os
import subprocess
from pathlib import Path

import pytest

from rmr.inspectors.codegraph import trace_impact


@pytest.fixture
def indexed_repo(tmp_path, monkeypatch):
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    install = local / "codegraph" / "current"
    node = install / "node.exe"
    cli = install / "lib" / "dist" / "bin" / "codegraph.js"
    if os.name != "nt" or not node.is_file() or not cli.is_file():
        pytest.skip("official Windows CodeGraph installation unavailable")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text(
        "def anchor():\n    return 1\n\ndef intermediate():\n    return anchor()\n"
        "\ndef downstream():\n    return intermediate()\n"
        "\nclass Container:\n    def method(self):\n        return 1\n"
        "\ndef use_container():\n    return Container().method()\n", encoding="utf-8")
    (repo / "other.py").write_text("def anchor():\n    return 0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run([str(node), str(cli), "init", "--yes"], cwd=repo, check=True,
                   capture_output=True, timeout=90)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return repo


def test_impact_paths_depth_scope_and_caps(indexed_repo):
    repo = indexed_repo
    result = trace_impact(repo, "anchor", None, 30, 2)
    assert result["ambiguous"] is True
    assert len(result["definitions"]) == 2
    group = next(g for g in result["definitions"] if g["definition"]["file"] == "main.py")
    assert [a["name"] for a in group["affected"]] == ["intermediate", "downstream"]
    chain = group["affected"][1]
    assert chain["depth"] == 2
    assert [p["name"] for p in chain["path"]] == ["anchor", "intermediate", "downstream"]
    assert [p["relation"] for p in chain["path"][1:]] == ["calls", "calls"]
    scoped = trace_impact(repo, "anchor", repo / "main.py", 30, 1)
    assert not scoped["ambiguous"]
    assert [a["name"] for a in scoped["definitions"][0]["affected"]] == ["intermediate"]
    assert trace_impact(repo, "intermediate", repo / "other.py", 30, 2)["definitions"] == []
    capped = trace_impact(repo, "anchor", None, 1, 2)
    assert capped["truncated"] and capped["ambiguous"]
    assert len(capped["definitions"]) == 1
    assert capped["definitions"][0]["affected"][0]["name"] == "intermediate"


def test_impact_drops_unsafe_intermediate(tmp_path, monkeypatch):
    from rmr.inspectors import codegraph

    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "main.py"
    inside.write_text("pass\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    def loc(name, file):
        return {"name": name, "kind": "function", "file": str(file), "line": 1}
    definition = loc("anchor", inside)
    unsafe = {**loc("outside", outside), "relation": "calls"}
    end = {**loc("downstream", inside), "relation": "calls"}
    payload = {"status": "ok", "definitions": [{"definition": definition, "affected": [
        {**loc("downstream", inside), "depth": 2, "path": [definition, unsafe, end]}
    ]}], "ambiguous": False, "truncated": False}
    monkeypatch.setattr(codegraph, "_query", lambda *args: codegraph._ImpactTrace.model_validate(payload))
    result = trace_impact(repo, "anchor", None, 30, 2)
    assert result["definitions"][0]["affected"] == []


def test_impact_containment_has_zero_dependency_depth(indexed_repo):
    result = trace_impact(indexed_repo, "Container", None, 30, 2)
    affected = result["definitions"][0]["affected"]
    method = next(item for item in affected if item["name"] == "method")
    assert method["depth"] == 0
    assert method["path"][-1]["relation"] == "contains"
