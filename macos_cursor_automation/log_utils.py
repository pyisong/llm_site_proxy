"""共享日志截断工具。"""

from __future__ import annotations

import logging
import os

from uvicorn.logging import AccessFormatter

DEFAULT_LOG_MAX_CHARS = 8192


def log_max_chars() -> int:
    """``CURSOR_BRIDGE_LOG_MAX_CHARS``；``0`` 表示不截断。"""
    raw = os.environ.get("CURSOR_BRIDGE_LOG_MAX_CHARS", str(DEFAULT_LOG_MAX_CHARS))
    try:
        return int(raw or "0")
    except ValueError:
        return DEFAULT_LOG_MAX_CHARS


_TRUNCATE_MARKER = "[日志已截断 total_chars="


def truncate_log_text(text: str, *, max_chars: int | None = None) -> str:
    """过长文本截断并附加总长度提示。"""
    mc = log_max_chars() if max_chars is None else max_chars
    if mc <= 0 or len(text) <= mc:
        return text
    suffix = f"\n... {_TRUNCATE_MARKER}{len(text)}]"
    keep = max(0, mc - len(suffix))
    return text[:keep] + suffix


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
        if _TRUNCATE_MARKER in line:
            return line
        return truncate_log_text(line)


class TruncatingAccessFormatter(AccessFormatter):
    """Uvicorn access 日志：先由 ``AccessFormatter`` 解析 ``record.args``，再截断整行。"""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        if _TRUNCATE_MARKER in line:
            return line
        return truncate_log_text(line)
