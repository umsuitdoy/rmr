"""Project configuration registry for RMR."""

from collections.abc import Mapping
from pathlib import Path

import yaml

from rmr.schemas import ProjectConfig

__all__ = [
    "find_default_config_path",
    "get_project",
    "load_projects_config",
    "resolve_project_root",
]


def find_default_config_path() -> Path:
    """Tìm registry local trước, sau đó dùng registry mẫu của gói nguồn."""
    local_config = Path("config")
    repo_config = Path(__file__).resolve().parents[3] / "config"
    for filename in ("projects.yaml", "projects.example.yaml"):
        for directory in (local_config, repo_config):
            candidate = directory / filename
            if candidate.is_file():
                return candidate.resolve()
    return (local_config / "projects.yaml").resolve()


def load_projects_config(config_path: Path | None = None) -> dict[str, ProjectConfig]:
    """
    Đọc file projects.yaml an toàn và validate qua Pydantic v2 ProjectConfig.

    Trả về:
        Dict dạng {project_id: ProjectConfig}.

    Ngoại lệ:
        FileNotFoundError: Nếu file config không tồn tại.
        ValueError: Nếu định dạng yaml không hợp lệ.
    """
    path = config_path or find_default_config_path()
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML format in {path}: {exc}") from exc

    if not isinstance(raw_data, dict) or "projects" not in raw_data:
        raise ValueError(f"Invalid configuration format: 'projects' key required in {path}")

    projects_raw = raw_data.get("projects")
    if not isinstance(projects_raw, dict):
        raise ValueError(f"'projects' must be a mapping in {path}")

    result: dict[str, ProjectConfig] = {}
    for p_id, p_data in projects_raw.items():
        if not isinstance(p_data, dict):
            raise ValueError(f"Configuration for project '{p_id}' must be a dictionary")
        data = dict(p_data)
        data.setdefault("project_id", p_id)
        try:
            result[p_id] = ProjectConfig(**data)
        except Exception as exc:
            raise ValueError(f"Validation failed for project '{p_id}': {exc}") from exc

    return result


def get_project(
    project_id: str,
    config_path: Path | None = None,
    *,
    projects: Mapping[str, ProjectConfig] | None = None,
) -> ProjectConfig:
    """
    Lấy thông tin cấu hình của 1 project cụ thể.

    Ngoại lệ:
        KeyError: Nếu project_id không nằm trong danh sách được cấp phép (thông báo rõ danh sách khả dụng).
    """
    if projects is None:
        projects = load_projects_config(config_path)
    if project_id not in projects:
        available = ", ".join(repr(k) for k in sorted(projects.keys()))
        raise KeyError(
            f"Project {project_id!r} not found in registered projects. Available: [{available}]"
        )
    return projects[project_id]


def resolve_project_root(
    proj: ProjectConfig,
    config_path: Path | None = None,
    *,
    frozen: bool = False,
) -> Path:
    """Xác định đường dẫn vật lý thực tế của thư mục dự án."""
    raw = Path(proj.root_path)
    if raw.is_absolute():
        root = raw.resolve(strict=True)
        if frozen and root != raw:
            raise PermissionError("Desktop session project root changed. Restart the session.")
        return root

    candidates = []
    if config_path is not None:
        cfg = config_path.resolve()
        base = cfg.parent.parent if cfg.parent.name == "config" else cfg.parent
        candidates.append((base / raw).resolve())

    candidates.append((Path.cwd() / raw).resolve())
    def_cfg = find_default_config_path()
    def_base = def_cfg.parent.parent if def_cfg.parent.name == "config" else def_cfg.parent
    candidates.append((def_base / raw).resolve())

    for cand in candidates:
        if cand.exists():
            return cand
    return raw.resolve(strict=True)

