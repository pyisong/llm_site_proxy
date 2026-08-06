"""共享日志截断工具：过长文本保留首尾、省略中间。"""

from __future__ import annotations

import logging
import os

from uvicorn.logging import AccessFormatter

DEFAULT_LOG_MAX_CHARS = 2048


def log_max_chars() -> int:
    """``CURSOR_BRIDGE_LOG_MAX_CHARS``；``0`` 表示不截断。"""
    raw = os.environ.get("CURSOR_BRIDGE_LOG_MAX_CHARS", str(DEFAULT_LOG_MAX_CHARS))
    try:
        return int(raw or "0")
    except ValueError:
        return DEFAULT_LOG_MAX_CHARS


_TRUNCATE_MARKER_PREFIX = "[省略中间"


def truncate_log_text(text: str, *, max_chars: int | None = None) -> str:
    """过长文本保留开头与结尾，中间省略并标注总长。"""
    mc = log_max_chars() if max_chars is None else max_chars
    if mc <= 0 or len(text) <= mc:
        return text

    total = len(text)
    # 预留标记位：先估 omitted，再按实际 keep 微调
    marker_tpl = "...{prefix} omitted={omitted} total={total}]..."
    # omitted 位数最多与 total 同宽
    probe = marker_tpl.format(
        prefix=_TRUNCATE_MARKER_PREFIX,
        omitted=total,
        total=total,
    )
    if len(probe) >= mc:
        # 极限短预算：只留开头
        keep_head = max(0, mc - 3)
        return text[:keep_head] + "..."

    keep = mc - len(probe)
    head = keep // 2
    tail = keep - head
    omitted = total - head - tail
    if omitted < 1:
        return text
    marker = marker_tpl.format(
        prefix=_TRUNCATE_MARKER_PREFIX,
        omitted=omitted,
        total=total,
    )
    # 标记长度可能因 omitted 位数变化略有浮动，再压一次
    while head + tail + len(marker) > mc and (head > 0 or tail > 0):
        if head >= tail and head > 0:
            head -= 1
        elif tail > 0:
            tail -= 1
        else:
            break
        omitted = total - head - tail
        if omitted < 1:
            return text
        marker = marker_tpl.format(
            prefix=_TRUNCATE_MARKER_PREFIX,
            omitted=omitted,
            total=total,
        )
    if head + tail + len(marker) > mc:
        keep_head = max(0, mc - 3)
        return text[:keep_head] + "..."
    if tail > 0:
        return text[:head] + marker + text[-tail:]
    return text[:head] + marker


def truncate_exc_text(exc: BaseException, *, max_chars: int | None = None) -> str:
    """异常 ``str(exc)`` 同样首尾截断，避免 TimeoutExpired 把整段 prompt 打进 traceback。"""
    return truncate_log_text(f"{type(exc).__name__}: {exc}", max_chars=max_chars)


class TruncatingLogFormatter(logging.Formatter):
    """格式化后对整行日志再做长度截断，兜底未显式调用 ``truncate_log_text`` 的输出。"""

    def __init__(
        self,
        fmt: str | None = None,
        datefmt: str | None = None,
        style: str = "%",
        *,
        use_colors: bool | None = None,
        **kwargs: object,
    ) -> None:
        # uvicorn LOGGING_CONFIG 会传入 use_colors；标准 Formatter 不接受，此处忽略。
        del use_colors, kwargs
        super().__init__(fmt=fmt, datefmt=datefmt, style=style)

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if _TRUNCATE_MARKER_PREFIX in line or "[日志已截断 total_chars=" in line:
            return line
        return truncate_log_text(line)


class TruncatingAccessFormatter(AccessFormatter):
    """Uvicorn access 日志：先由 ``AccessFormatter`` 解析 ``record.args``，再截断整行。"""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if _TRUNCATE_MARKER_PREFIX in line or "[日志已截断 total_chars=" in line:
            return line
        return truncate_log_text(line)
