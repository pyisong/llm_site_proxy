"""llm_site_proxy 统一日志：时间、级别、文件名、行号、logger 名。

各 browser proxy / catalog 共用同一格式；uvicorn 通过 ``build_uvicorn_log_config`` 接入，
避免默认 ``INFO:logger:msg`` 无定位信息。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

DEFAULT_FMT = (
    "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(filename)s:%(lineno)d | %(name)s | %(message)s"
)
DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 探活 / 模型清单：省略 access 与业务 request.start/end
_QUIET_GET_PATHS = frozenset(
    {
        "/health",
        "/v1/models",
    }
)

_configured = False


def is_quiet_http_path(method: str, path: str) -> bool:
    m = (method or "").upper()
    p = (path or "").split("?", 1)[0]
    return m == "GET" and p in _QUIET_GET_PATHS


class _QuietAccessFilter(logging.Filter):
    """过滤 uvicorn.access 里的探活噪声。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        # 典型：'172.x - "GET /health HTTP/1.1" 200'
        if '"GET /health ' in msg or '"GET /v1/models ' in msg:
            return False
        return True


def configure_logging(
    *,
    level: str | None = None,
    env_var: str = "LOG_LEVEL",
    force: bool = False,
) -> None:
    """配置 root + uvicorn logger；可重复调用（默认只生效一次，除非 force）。"""
    global _configured
    level_name = (level or os.getenv(env_var) or "INFO").upper()
    log_level = getattr(logging, level_name, logging.INFO)

    if _configured and not force:
        logging.getLogger().setLevel(log_level)
        return

    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(DEFAULT_FMT, datefmt=DEFAULT_DATEFMT))
    root.addHandler(handler)
    root.setLevel(log_level)

    # uvicorn 默认给 error/access 挂独立 handler 且 propagate=False，这里统一成同格式
    for name in ("uvicorn", "uvicorn.error"):
        ug = logging.getLogger(name)
        ug.handlers.clear()
        ug.propagate = True
        ug.setLevel(log_level)

    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access_handler = logging.StreamHandler(sys.stdout)
    try:
        from uvicorn.logging import AccessFormatter

        access_handler.setFormatter(
            AccessFormatter(
                DEFAULT_FMT.replace(
                    "%(message)s",
                    '%(client_addr)s - "%(request_line)s" %(status_code)s',
                ),
                datefmt=DEFAULT_DATEFMT,
                use_colors=False,
            )
        )
    except Exception:
        access_handler.setFormatter(logging.Formatter(DEFAULT_FMT, datefmt=DEFAULT_DATEFMT))
    access.addHandler(access_handler)
    access.propagate = False
    access.setLevel(log_level)
    # 避免重复挂载同一 filter
    if not any(isinstance(f, _QuietAccessFilter) for f in access.filters):
        access.addFilter(_QuietAccessFilter())

    _configured = True


def build_uvicorn_log_config(*, level: str | None = None, env_var: str = "LOG_LEVEL") -> dict[str, Any]:
    """供 ``uvicorn.run(..., log_config=...)`` / ``--log-config`` 使用。"""
    level_name = (level or os.getenv(env_var) or "INFO").upper()
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "quiet_access": {
                "()": "logging_setup._QuietAccessFilter",
            },
        },
        "formatters": {
            "default": {
                "format": DEFAULT_FMT,
                "datefmt": DEFAULT_DATEFMT,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": (
                    "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(filename)s:%(lineno)d | "
                    '%(name)s | %(client_addr)s - "%(request_line)s" %(status_code)s'
                ),
                "datefmt": DEFAULT_DATEFMT,
                "use_colors": False,
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "filters": ["quiet_access"],
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": level_name, "propagate": False},
        },
        "root": {
            "handlers": ["default"],
            "level": level_name,
        },
    }
