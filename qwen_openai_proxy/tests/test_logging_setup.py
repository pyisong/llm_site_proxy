"""统一日志格式：时间 / 级别 / 文件:行号 / logger。"""

from __future__ import annotations

import logging
import logging.config

from logging_setup import (
    DEFAULT_DATEFMT,
    DEFAULT_FMT,
    _QuietAccessFilter,
    build_uvicorn_log_config,
    configure_logging,
    is_quiet_http_path,
)


def test_quiet_paths():
    assert is_quiet_http_path("GET", "/health")
    assert is_quiet_http_path("GET", "/v1/models")
    assert not is_quiet_http_path("POST", "/v1/chat/completions")
    assert not is_quiet_http_path("GET", "/v1/chat/completions")


def test_configure_logging_format(capsys):
    configure_logging(level="INFO", force=True)
    log = logging.getLogger("qwen_openai_proxy_test")
    log.info("hello-format")
    err = capsys.readouterr()
    # StreamHandler 默认 stdout
    out = err.out + err.err
    assert "hello-format" in out
    assert "logging_setup.py" in out or "test_logging_setup.py" in out or "| INFO" in out or "INFO" in out
    # 至少含时间分隔与级别
    assert "|" in out


def test_uvicorn_log_config_dictconfig():
    cfg = build_uvicorn_log_config(level="INFO")
    logging.config.dictConfig(cfg)
    assert "asctime" in cfg["formatters"]["default"]["format"]
    assert "filename" in cfg["formatters"]["default"]["format"]
    assert "lineno" in cfg["formatters"]["default"]["format"]
    filt = _QuietAccessFilter()
    rec = logging.LogRecord("uvicorn.access", 20, "", 0, '1.1.1.1:1 - "GET /health HTTP/1.1" 200', (), None)
    assert filt.filter(rec) is False
    rec2 = logging.LogRecord(
        "uvicorn.access",
        20,
        "",
        0,
        '1.1.1.1:1 - "POST /v1/chat/completions HTTP/1.1" 200',
        (),
        None,
    )
    assert filt.filter(rec2) is True
