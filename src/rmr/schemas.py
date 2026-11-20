"""Pydantic v2 data contracts for RMR Core & MCP Tools."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "EvidenceItem",
    "EvidenceKind",
    "EvidenceTarget",
    "InspectEvidenceOutput",
    "InvestigateInput",
    "InvestigationMode",
    "ProjectConfig",
    "TreeState",
]


class TreeState(BaseModel):
    """Trạng thái git worktree và fingerprint nhất quán."""

    head_commit: str = Field(description="Mã băm ngắn 7 ký tự của commit HEAD hiện tại")
    is_dirty: bool = Field(
        description="True nếu working tree có file đã chỉnh sửa hoặc file mới chưa track"
    )
    worktree_fingerprint: str = Field(
        description="Mã SHA-256 12 ký tự tính từ git status porcelain và mtime file bẩn"
    )


class ProjectConfig(BaseModel):
    """Cấu hình khai báo dự án được quản lý bởi RMR."""

    project_id: str = Field(description="Định danh duy nhất của dự án")
    root_path: str = Field(description="Đường dẫn vật lý tới thư mục gốc của dự án")
    language: str = Field(default="python", description="Ngôn ngữ chính của dự án")
    entry_docs: list[str] = Field(
        default_factory=list,
        description="Danh sách tài liệu cốt lõi (README, architecture)",
    )


# --- Tool 2: investigate_codebase ---
ImpactDepth = Annotated[int, Field(strict=True, ge=1, le=5)]

InvestigationMode = Literal["outline", "symbol", "grep", "ast", "callers", "callees", "impact"]


class InvestigateInput(BaseModel):
    """Tham số điều tra mã nguồn đa chế độ."""

    project_id: str
    mode: InvestigationMode
    query: str = Field(
        default="", description="Tên symbol, regex pattern, hoặc AST pattern"
    )
    subpath: str | None = Field(
        default=None,
        description="Đường dẫn tương đối. Với callers/callees/impact, chỉ định file định nghĩa symbol.",
    )
    max_results: int = Field(
        default=30, ge=1, le=50, description="Giới hạn số lượng kết quả (tối đa 50)"
    )
    depth: ImpactDepth | None = Field(default=None, description="Impact dependency depth (1–5; default 2)")

    @field_validator("depth")
    @classmethod
    def _impact_depth_only(cls, value, info):
        if value is not None and info.data.get("mode") != "impact":
            raise ValueError("depth is only supported for impact mode")
        return value


# --- Tool 3: inspect_evidence ---
EvidenceKind = Literal["file_lines", "symbol", "git_diff"]


class EvidenceTarget(BaseModel):
    """Mục tiêu trích xuất bằng chứng kỹ thuật."""

    kind: EvidenceKind
    path: str = Field(description="Đường dẫn tương đối tới file trong dự án")
    start_line: int | None = Field(default=None, ge=1, description="Dòng bắt đầu (1-indexed)")
    end_line: int | None = Field(default=None, ge=1, description="Dòng kết thúc (1-indexed)")
    symbol_name: str | None = Field(default=None, description="Tên class/hàm nếu kind='symbol'")
    base_ref: str = Field(default="HEAD", description="Git ref cơ sở khi kind='git_diff'")
    target_ref: str = Field(default="WORKTREE", description="Git ref đích hoặc 'WORKTREE'")


class EvidenceItem(BaseModel):
    """Khối nội dung bằng chứng kỹ thuật được trích xuất an toàn."""

    kind: EvidenceKind
    path: str
    line_range: tuple[int, int] | None = None
    content_sha256: str | None = Field(default=None, description="Mã SHA-256 của nội dung")
    content: str = Field(description="Nội dung code hoặc diff")
    truncated: bool = Field(default=False, description="True nếu nội dung bị cắt")


class InspectEvidenceOutput(BaseModel):
    """Kết quả trả về tổng hợp của inspect_evidence kèm fingerprint worktree."""

    project_id: str
    tree_state: TreeState
    evidence_items: list[EvidenceItem]
