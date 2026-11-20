"""Core module: path guarding, configuration registry, and truncator."""

from rmr.core.path_guard import resolve_read_path
from rmr.core.registry import (
    find_default_config_path,
    get_project,
    load_projects_config,
)
from rmr.core.truncator import (
    truncate_list,
    truncate_text_lines,
)

__all__ = [
    "find_default_config_path",
    "get_project",
    "load_projects_config",
    "resolve_read_path",
    "truncate_list",
    "truncate_text_lines",
]
