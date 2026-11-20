"""Unit tests for RMR Core Registry and Payload Truncator (TASK-06)."""

from pathlib import Path

import pytest

from rmr.core.registry import (
    find_default_config_path,
    get_project,
    load_projects_config,
)
from rmr.core.truncator import (
    truncate_list,
    truncate_text_lines,
)


def test_registry_loading(tmp_path: Path) -> None:
    """Verify loading, parsing and validating project registry from YAML."""
    config_file = tmp_path / "projects.yaml"
    config_file.write_text(
        """
projects:
  web_frontend:
    root_path: "./frontend"
    language: "typescript"
    entry_docs:
      - "README.md"
  core_backend:
    root_path: "./backend"
    language: "python"
""",
        encoding="utf-8",
    )

    configs = load_projects_config(config_file)
    assert "web_frontend" in configs
    assert "core_backend" in configs

    fe = configs["web_frontend"]
    assert fe.project_id == "web_frontend"
    assert fe.root_path == "./frontend"
    assert fe.language == "typescript"
    assert fe.entry_docs == ["README.md"]

    be = configs["core_backend"]
    assert be.language == "python"
    assert be.entry_docs == []

    # Successful retrieval via get_project
    retrieved = get_project("core_backend", config_path=config_file)
    assert retrieved.project_id == "core_backend"

    # KeyError with available list when project is missing
    with pytest.raises(KeyError) as exc_info:
        get_project("unknown_project", config_path=config_file)
    assert "unknown_project" in str(exc_info.value)
    assert "core_backend" in str(exc_info.value)

    # find_default_config_path returns existing path in repository
    default_cfg = find_default_config_path()
    assert default_cfg.is_file()

    # Error conditions
    missing_cfg = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_projects_config(missing_cfg)

    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("projects: [invalid: {unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid YAML"):
        load_projects_config(bad_yaml)

    no_projects_key = tmp_path / "no_projects.yaml"
    no_projects_key.write_text("other_key: 123\n", encoding="utf-8")
    with pytest.raises(ValueError, match="'projects' key required"):
        load_projects_config(no_projects_key)

    invalid_item_type = tmp_path / "invalid_item.yaml"
    invalid_item_type.write_text("projects:\n  test: 'not a dict'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be a dictionary"):
        load_projects_config(invalid_item_type)


def test_truncator_lines() -> None:
    """Verify line truncation behavior and threshold boundaries."""
    # 250 lines text exceeding default limit 200
    lines_250 = "\n".join(f"line {i}" for i in range(250))
    truncated_text, is_truncated = truncate_text_lines(lines_250, max_lines=200)

    assert is_truncated is True
    assert "[... Truncated: showing 200 of 250 lines ...]" in truncated_text

    # 50 lines within boundary
    lines_50 = "\n".join(f"line {i}" for i in range(50))
    untouched_text, is_truncated_50 = truncate_text_lines(lines_50, max_lines=200)

    assert is_truncated_50 is False
    assert untouched_text == lines_50


def test_truncator_bytes() -> None:
    """Verify byte truncation behavior for large single-line or multi-line payloads."""
    # Payload > 8KB (8192 bytes)
    large_payload = "a" * 10000
    truncated_text, is_truncated = truncate_text_lines(large_payload, max_bytes=8192)

    assert is_truncated is True
    assert "[... Truncated: showing" in truncated_text
    assert "bytes ...]" in truncated_text

    # Payload within limits
    small_payload = "small payload"
    out, truncated = truncate_text_lines(small_payload, max_bytes=8192)
    assert truncated is False
    assert out == small_payload

    # Exceeding both line count and byte count
    both_exceeded = "\n".join("x" * 200 for _ in range(300))
    both_out, both_truncated = truncate_text_lines(
        both_exceeded, max_lines=10, max_bytes=500
    )
    assert both_truncated is True
    assert "lines" in both_out and "bytes" in both_out


def test_truncator_list() -> None:
    """Verify item list truncation behavior for payload capping."""
    # 60 items exceeding limit 50
    items_60 = list(range(60))
    truncated_items, is_truncated = truncate_list(items_60, max_count=50)

    assert is_truncated is True
    assert len(truncated_items) == 50
    assert truncated_items == list(range(50))

    # 30 items within limit
    items_30 = list(range(30))
    kept_items, is_truncated_30 = truncate_list(items_30, max_count=50)

    assert is_truncated_30 is False
    assert len(kept_items) == 30
    assert kept_items == items_30

    # Non-list iterable (e.g., range generator)
    gen_items, gen_truncated = truncate_list(range(70), max_count=50)
    assert gen_truncated is True
    assert len(gen_items) == 50
