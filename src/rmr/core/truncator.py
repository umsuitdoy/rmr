"""Payload truncation for RMR LLM context protection."""

__all__ = [
    "truncate_text_lines",
    "truncate_list",
]


def truncate_text_lines(
    text: str,
    max_lines: int = 200,
    max_bytes: int = 8192,
) -> tuple[str, bool]:
    """
    Cắt bớt chuỗi text nếu vượt quá giới hạn dòng hoặc dung lượng byte.

    Tham số:
        text: Chuỗi văn bản đầu vào.
        max_lines: Giới hạn số dòng tối đa (mặc định 200).
        max_bytes: Giới hạn kích thước byte tối đa (mặc định 8192 - 8KB).

    Trả về:
        Tuple (nội dung_sau_cắt, cờ_truncated_bool).
        Nếu bị cắt, đính kèm dòng thông báo:
        '\\n[... Truncated: showing X of Y lines / bytes ...]'
    """
    lines = text.splitlines()
    total_lines = len(lines)
    text_bytes = text.encode("utf-8")
    total_bytes = len(text_bytes)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return text, False

    kept_lines: list[str] = []
    current_bytes = 0

    for i, line in enumerate(lines):
        if i >= max_lines:
            break
        line_bytes = line.encode("utf-8")
        extra = 1 if kept_lines else 0
        if current_bytes + len(line_bytes) + extra > max_bytes:
            if not kept_lines:
                trimmed = line_bytes[:max_bytes].decode("utf-8", errors="ignore")
                kept_lines.append(trimmed)
            break
        kept_lines.append(line)
        current_bytes += len(line_bytes) + extra

    trimmed_text = "\n".join(kept_lines)
    shown_lines = len(kept_lines)
    shown_bytes = len(trimmed_text.encode("utf-8"))

    if total_lines > max_lines and total_bytes > max_bytes:
        notice = f"\n[... Truncated: showing {shown_lines} of {total_lines} lines / {shown_bytes} of {total_bytes} bytes ...]"
    elif total_lines > max_lines:
        notice = f"\n[... Truncated: showing {shown_lines} of {total_lines} lines ...]"
    else:
        notice = f"\n[... Truncated: showing {shown_bytes} of {total_bytes} bytes ...]"

    return trimmed_text + notice, True


def truncate_list(items: list, max_count: int = 50) -> tuple[list, bool]:
    """
    Cắt bớt danh sách nếu vượt quá max_count (mặc định 50 mục).

    Trả về:
        Tuple (danh_sách_sau_cắt, cờ_truncated_bool).
    """
    if not isinstance(items, list):
        items = list(items)
    if len(items) <= max_count:
        return items, False
    return items[:max_count], True
