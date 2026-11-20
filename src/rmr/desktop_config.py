"""Desktop session configuration and URL protection keys (TASK-22)."""

import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)

__all__ = [
    "DesktopSettings",
    "build_mcp_path",
    "build_public_url",
    "generate_url_key",
    "load_settings",
    "normalize_domain",
    "save_settings",
    "validate_project_root",
]

DOMAIN_REGEX = re.compile(
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
)
URL_KEY_REGEX = re.compile(r"[A-Za-z0-9_-]+")


def normalize_domain(value: str) -> str:
    """Chuẩn hóa và xác thực domain / hostname (bare hostname hoặc HTTPS URL)."""
    hostname = value.strip()
    if not hostname:
        raise ValueError("Domain cannot be empty.")
    scheme, separator, authority = hostname.partition("://")
    if separator:
        if scheme.lower() != "https":
            raise ValueError("Only HTTPS URLs or bare hostnames are accepted.")
        hostname = authority.removesuffix("/")
    for marker, component in (
        ("@", "user credentials"),
        (":", "a port"),
        ("/", "path segments"),
        ("\\", "path segments"),
        ("?", "query parameters"),
        ("#", "URL fragments"),
    ):
        if marker in hostname:
            raise ValueError(f"Domain must not contain {component}.")
    hostname = hostname.removesuffix(".")
    if len(hostname) > 253 or not DOMAIN_REGEX.fullmatch(hostname):
        raise ValueError("Invalid domain format.")
    return hostname.lower()


def validate_project_root(path: Path | str) -> Path:
    """Xác thực đường dẫn là thư mục gốc của một Git worktree."""
    target = Path(path).resolve()
    if not target.exists():
        raise ValueError(f"Path does not exist: {target}")
    if not target.is_dir():
        raise ValueError(f"Path is not a directory: {target}")
    try:
        res = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
            capture_output=True,
            encoding="utf-8",
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise ValueError(f"Failed to execute git check on {target}: {exc}") from exc
    if res.returncode != 0:
        raise ValueError(f"Directory '{target}' is not inside a Git repository.")
    git_root = Path(res.stdout.rstrip("\r\n")).resolve()
    try:
        is_same = target.samefile(git_root)
    except (OSError, ValueError):
        is_same = target == git_root
    if not is_same:
        raise ValueError(
            f"Selected directory '{target}' is a subdirectory of Git repository '{git_root}'. Please select the repository root."
        )
    return target


def generate_url_key() -> str:
    """Sinh khóa bảo vệ URL ngẫu nhiên an toàn (tối thiểu 32 ký tự)."""
    return secrets.token_urlsafe(32)


def _validated_key_value(key: SecretStr | str) -> str:
    raw = key.get_secret_value() if isinstance(key, SecretStr) else key
    if len(raw) < 32:
        raise ValueError(
            "URL key must be at least 32 characters long. Use a password generator."
        )
    if not URL_KEY_REGEX.fullmatch(raw):
        raise ValueError("URL key must use URL-safe characters [A-Za-z0-9_-].")
    return raw


def build_mcp_path(key: SecretStr | str) -> str:
    """Tạo đúng một đoạn đường dẫn MCP cho URL và route bảo vệ."""
    return f"/mcp/{_validated_key_value(key)}"


class DesktopSettings(BaseModel):
    """Cấu hình phiên làm việc Desktop độc lập với registry hệ thống."""

    model_config = ConfigDict(hide_input_in_errors=True)

    project_root: Annotated[Path, AfterValidator(validate_project_root)]
    domain: Annotated[str, Field(strict=True), AfterValidator(normalize_domain)]
    port: int = Field(
        default=8000, strict=True, ge=1, le=65535, description="Cổng local (1-65535)"
    )
    url_key: SecretStr = Field(
        strict=True, description="Khóa bảo vệ URL an toàn (tối thiểu 32 ký tự)"
    )

    @field_validator("port", mode="before")
    @classmethod
    def _parse_port(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().isdecimal():
            return int(value)
        return value

    @field_validator("url_key")
    @classmethod
    def _validate_url_key(cls, value: SecretStr) -> SecretStr:
        _validated_key_value(value)
        return value


def build_public_url(settings: DesktopSettings) -> str:
    """Tạo URL HTTPS hoàn chỉnh để chia sẻ hoặc sao chép."""
    return f"https://{settings.domain}{build_mcp_path(settings.url_key)}"


def save_settings(path: Path, settings: DesktopSettings) -> None:
    """Ghi cấu hình desktop nguyên tử ra file để bảo vệ khóa không bị hỏng nếu gặp sự cố."""
    target = Path(path).resolve()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "project_root": str(settings.project_root),
        "domain": settings.domain,
        "port": settings.port,
        "url_key": settings.url_key.get_secret_value(),
    }
    temp_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, suffix=".tmp", delete=False
        ) as f:
            temp_path = Path(f.name)
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target)
    except BaseException:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def load_settings(path: Path) -> DesktopSettings | None:
    """Đọc cấu hình desktop từ file. Trả về None nếu file thiếu hoặc lỗi dữ liệu."""
    try:
        target = Path(path)
        if not target.is_file():
            return None
        raw = target.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = yaml.safe_load(raw)
        return DesktopSettings.model_validate(data)
    except (OSError, ValueError, RecursionError, yaml.YAMLError):
        return None
