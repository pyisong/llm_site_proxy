"""使用 ``open`` 与 AppleScript/System Events 在 macOS 上控制 Cursor。

程序化获取 Agent 文本/结构化结果：调用 :func:`run_cursor_agent`（底层为
``cursor agent --print --output-format json|text|stream-json``），需已登录 Cursor
（``cursor agent login`` / 环境变量 ``CURSOR_API_KEY``）。

使用前请在「系统设置 → 隐私与安全性 → 辅助功能」中授权运行本脚本的终端/Python
（仅 AppleScript 按键相关子命令需要）。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

try:
    from .log_utils import TruncatingAccessFormatter, TruncatingLogFormatter, truncate_log_text
except ImportError:
    from log_utils import TruncatingAccessFormatter, TruncatingLogFormatter, truncate_log_text

# Cursor 自带 CLI（PATH 未配置时仍可尝试）
_CURSOR_BUNDLED_CLI = Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor")


def resolve_cursor_cli() -> str | None:
    """返回可用的 ``cursor`` / ``cursor-agent`` 可执行路径；未找到则 ``None``（仅 ``open -a`` 时不需要）。

    优先使用 App 内置二进制，避免 PATH 中同名脚本冒充 ``cursor``。

    Docker 官方 Linux 安装脚本往往在 PATH 放置 ``cursor-agent`` 而非 ``cursor``，此处一并探测。
    """
    if _CURSOR_BUNDLED_CLI.is_file():
        return str(_CURSOR_BUNDLED_CLI)
    for name in ("cursor", "cursor-agent"):
        w = shutil.which(name)
        if w:
            return w
    return None


def _cursor_cli_is_standalone_agent_bin(cli_path: str) -> bool:
    """Linux 常为独立 ``cursor-agent`` 二进制；macOS 统一入口为 ``cursor agent ...``。"""
    return Path(cli_path).name in ("cursor-agent", "agent")


def cursor_agent_invocation_prefix(cli_path: str) -> list[str]:
    """插在可执行文件名与 ``--print`` 等参数之间的 argv 片段（``['agent']`` 或 ``[]``）。"""
    return [] if _cursor_cli_is_standalone_agent_bin(cli_path) else ["agent"]


def cursor_agent_models_argv(cli_path: str) -> list[str]:
    """``cursor agent models`` 与 ``cursor-agent models`` 的 argv 前缀（含可执行文件路径）。"""
    if _cursor_cli_is_standalone_agent_bin(cli_path):
        return [cli_path, "models"]
    return [cli_path, "agent", "models"]


def stream_readline_limit() -> int:
    """stream-json 单行 stdout 最大字节数（默认 64 MiB）。

    ``stream-json --stream-partial-output`` 每行携带累积快照，长回复会超过 asyncio 默认 64 KiB。
    可通过 ``CURSOR_BRIDGE_STREAM_READLINE_LIMIT`` 覆盖。
    """
    default = 64 * 1024 * 1024
    raw = (os.environ.get("CURSOR_BRIDGE_STREAM_READLINE_LIMIT") or "").strip()
    if not raw:
        return default
    try:
        limit = int(raw)
    except ValueError:
        return default
    return max(65536, limit)


def _sanitize_subprocess_argv_text(s: str) -> str:
    """去掉 ``\\x00``：POSIX ``exec`` 不允许 argv 含 NUL，否则 ``subprocess`` 报 ``embedded null byte``。"""
    if "\x00" not in s:
        return s
    return s.replace("\x00", "")


def _argv_prompt_max_bytes() -> int:
    """单次塞进 argv 的 prompt 上限（字节）。环境变量 + 其它参数也会挤占 ARG_MAX。"""
    raw = (os.environ.get("CURSOR_BRIDGE_ARGV_PROMPT_MAX_BYTES") or "").strip()
    if raw:
        try:
            return max(4096, int(raw))
        except ValueError:
            pass
    # 默认偏保守：长文 QA/精炼常超 100KB，易触发 OSError Errno 7
    return 96 * 1024


def _prepare_agent_prompt_for_argv(
    prompt: str,
    workspace: Path | str | None,
) -> tuple[str, Path | None]:
    """超长 prompt 写入临时文件，argv 只传短指针，避免 ``Argument list too long``。

    返回 ``(argv_prompt, cleanup_path)``；``cleanup_path`` 非空时调用方须在结束后删除。
    """
    text = _sanitize_subprocess_argv_text(prompt)
    if len(text.encode("utf-8")) <= _argv_prompt_max_bytes():
        return text, None

    if workspace is not None:
        base = Path(workspace).expanduser().resolve() / ".cursor_bridge_prompts"
        base.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="prompt_", suffix=".md", dir=str(base))
    else:
        fd, name = tempfile.mkstemp(prefix="cursor_bridge_prompt_", suffix=".md")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    abs_path = str(path.resolve())
    short = (
        "请使用 Read 工具完整打开并阅读下面文件的全部内容，将其作为本轮唯一指令与用户任务执行；"
        "不要省略、不要只读摘要；读完后直接给出最终答案（JSON/正文按文件要求）。\n\n"
        f"指令文件绝对路径：{abs_path}\n"
    )
    _agent_subprocess_logger().info(
        "agent prompt offloaded to file path=%s bytes=%d",
        abs_path,
        len(text.encode("utf-8")),
    )
    return short, path


def _cleanup_prompt_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _agent_subprocess_logger() -> logging.Logger:
    """与 ``openai_bridge`` 共用 logger，便于写入同一轮转文件。"""
    return logging.getLogger("cursor_openai_bridge")


def _argv_for_log(cmd: list[str], *, tail_chars: int = 240) -> list[str]:
    """日志用 argv：最后一项（prompt）过长时首尾截断。"""
    if not cmd:
        return []
    out = list(cmd[:-1])
    last = cmd[-1]
    if len(last) <= tail_chars * 2 + 32:
        out.append(last)
        return out
    mid = f"...<+{len(last) - 2 * tail_chars}chars>..."
    out.append(f"{last[:tail_chars]}{mid}{last[-tail_chars:]}")
    return out


def _run_cursor_agent_subprocess_monitored(
    cmd: list[str],
    *,
    cwd: str | None,
    env: dict[str, str],
    timeout: float | None,
    log_stderr_lines: bool,
) -> subprocess.CompletedProcess[str]:
    """``Popen`` + 心跳日志 + 超时杀进程；超时时抛出 ``TimeoutExpired``（带已收集的 output/stderr）。"""
    log = _agent_subprocess_logger()
    prefix = "[cursor_agent_subprocess]"
    interval = float(os.environ.get("CURSOR_BRIDGE_AGENT_PROGRESS_INTERVAL_SEC", "30") or "30")
    if interval < 5.0:
        interval = 5.0

    log.info(
        "%s START argv=%s cwd=%s timeout=%s",
        prefix,
        _argv_for_log(cmd),
        cwd,
        timeout,
    )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=cwd,
    )
    out_parts: list[str] = []
    err_parts: list[str] = []
    lock = threading.Lock()
    # 管道典型容量约 64KiB：若用 readline 而子进程长时间不写换行（或大段 JSON 单行），
    # 子进程写满管道后会阻塞，表现为「CLI 两分钟能完成、桥接十分钟超时」。按块 read 排空。
    try:
        chunk_sz = int(os.environ.get("CURSOR_BRIDGE_AGENT_PIPE_READ_CHUNK", "65536") or "65536")
    except ValueError:
        chunk_sz = 65536
    chunk_sz = max(4096, min(chunk_sz, 1024 * 1024))

    def _drain_stdout() -> None:
        if proc.stdout is None:
            return
        try:
            while True:
                chunk = proc.stdout.read(chunk_sz)
                if not chunk:
                    break
                with lock:
                    out_parts.append(chunk)
        finally:
            proc.stdout.close()

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        pending = ""
        try:
            while True:
                chunk = proc.stderr.read(chunk_sz)
                if not chunk:
                    break
                with lock:
                    err_parts.append(chunk)
                if not log_stderr_lines:
                    continue
                pending += chunk
                while True:
                    nl = pending.find("\n")
                    if nl < 0:
                        break
                    line = pending[:nl]
                    pending = pending[nl + 1 :]
                    if line.strip():
                        log.info("%s stderr %s", prefix, truncate_log_text(line.rstrip()))
            if log_stderr_lines and pending.strip():
                log.info("%s stderr %s", prefix, truncate_log_text(pending.rstrip()))
        finally:
            proc.stderr.close()

    t_out = threading.Thread(target=_drain_stdout, daemon=True)
    t_err = threading.Thread(target=_drain_stderr, daemon=True)
    t_out.start()
    t_err.start()

    t0 = time.monotonic()
    deadline = (t0 + float(timeout)) if timeout is not None and float(timeout) > 0 else None
    last_hb = t0
    timed_out = False

    while proc.poll() is None:
        time.sleep(0.5)
        now = time.monotonic()
        if deadline is not None and now >= deadline:
            timed_out = True
            log.warning(
                "%s TIMEOUT_KILL elapsed=%.1fs limit=%.1fs pid=%s",
                prefix,
                now - t0,
                float(timeout),
                proc.pid,
            )
            proc.kill()
            break
        if now - last_hb >= interval:
            last_hb = now
            with lock:
                oc = sum(len(x) for x in out_parts)
                ec = sum(len(x) for x in err_parts)
            log.info(
                "%s heartbeat elapsed=%.1fs pid=%s stdout_chars=%d stderr_chars=%d",
                prefix,
                now - t0,
                proc.pid,
                oc,
                ec,
            )
    # 先汇合读线程排空管道，再 wait 回收，降低子进程因写满管道而阻塞的概率
    t_out.join(timeout=120.0)
    t_err.join(timeout=120.0)
    rc = proc.wait()

    with lock:
        stdout = "".join(out_parts)
        stderr = "".join(err_parts)

    if timed_out:
        log.warning(
            "%s TIMEOUT partial stdout_chars=%d stderr_chars=%d stderr_tail=%r stdout_tail=%r",
            prefix,
            len(stdout),
            len(stderr),
            truncate_log_text(stderr[-12000:] if stderr else ""),
            truncate_log_text(stdout[-12000:] if stdout else ""),
        )
        raise subprocess.TimeoutExpired(cmd, float(timeout or 0), output=stdout, stderr=stderr)

    log.info(
        "%s END returncode=%s elapsed=%.1fs stdout_chars=%d stderr_chars=%d",
        prefix,
        rc,
        time.monotonic() - t0,
        len(stdout),
        len(stderr),
    )
    if (stderr or "").strip() and not log_stderr_lines:
        log.info(
            "%s stderr_full (may be large) %s",
            prefix,
            truncate_log_text(stderr.rstrip()),
        )
    return subprocess.CompletedProcess(cmd, rc, stdout, stderr)


async def _terminate_agent_proc(proc: asyncio.subprocess.Process) -> None:
    """安全结束子进程；已退出或 ``kill`` 竞态时忽略 ``ProcessLookupError``。"""
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    await proc.wait()


async def _read_stdout_line(
    reader: asyncio.StreamReader,
    *,
    max_bytes: int,
) -> bytes:
    """读取一行 stdout，不受 ``StreamReader.readline`` 默认 64 KiB 限制。"""
    line = bytearray()
    while True:
        chunk_size = max(1, min(65536, max_bytes - len(line) + 1))
        chunk = await reader.read(chunk_size)
        if not chunk:
            return bytes(line)
        line.extend(chunk)
        if len(line) > max_bytes:
            raise RuntimeError(
                f"cursor agent stream-json 单行超过 {max_bytes} 字节，"
                "可设置 CURSOR_BRIDGE_STREAM_READLINE_LIMIT 增大"
            )
        nl = line.find(b"\n")
        if nl >= 0:
            full_line = bytes(line[: nl + 1])
            rest = line[nl + 1 :]
            if rest:
                reader.feed_data(rest)
            return full_line


def build_serve_uvicorn_log_config(log_dir: Path) -> dict[str, Any]:
    """``uvicorn.run(..., log_config=...)``：在 ``log_dir`` 下写入 ``uvicorn.log`` / ``uvicorn_access.log``（轮转）。"""
    from uvicorn.config import LOGGING_CONFIG

    log_dir.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(LOGGING_CONFIG)

    # 控制台保留 uvicorn 自带 DefaultFormatter/AccessFormatter（含 levelprefix、use_colors）。
    # 仅文件 handler 使用截断 formatter，避免替换 default/access 导致 dictConfig 或运行时字段缺失。
    cfg["formatters"]["file_trunc_default"] = {
        "()": TruncatingLogFormatter,
        "fmt": "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(filename)s:%(lineno)d | %(name)s | %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
    }
    cfg["formatters"]["file_trunc_access"] = {
        "()": TruncatingAccessFormatter,
        "fmt": (
            "%(asctime)s.%(msecs)03d | %(levelname)-5s | %(filename)s:%(lineno)d | "
            '%(name)s | %(client_addr)s - "%(request_line)s" %(status_code)s'
        ),
        "datefmt": "%Y-%m-%d %H:%M:%S",
        "use_colors": False,
    }

    try:
        max_b = int(os.environ.get("CURSOR_BRIDGE_LOG_FILE_MAX_BYTES", str(10 * 1024 * 1024)))
    except ValueError:
        max_b = 10 * 1024 * 1024
    try:
        bk = int(os.environ.get("CURSOR_BRIDGE_LOG_FILE_BACKUP_COUNT", "5"))
    except ValueError:
        bk = 5
    max_b = max(1024, max_b)
    bk = max(1, bk)
    cfg["handlers"]["file_default"] = {
        "formatter": "file_trunc_default",
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str((log_dir / "uvicorn.log").resolve()),
        "maxBytes": max_b,
        "backupCount": bk,
        "encoding": "utf-8",
    }
    cfg["handlers"]["file_access"] = {
        "formatter": "file_trunc_access",
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str((log_dir / "uvicorn_access.log").resolve()),
        "maxBytes": max_b,
        "backupCount": bk,
        "encoding": "utf-8",
    }
    cfg["loggers"]["uvicorn"]["handlers"] = ["default", "file_default"]
    cfg["loggers"]["uvicorn.access"]["handlers"] = ["access", "file_access"]
    cfg["loggers"]["uvicorn.error"] = {
        "handlers": ["default", "file_default"],
        "level": "INFO",
        "propagate": False,
    }
    return cfg


def fetch_cursor_agent_models(*, timeout: float = 30.0) -> list[dict[str, Any]]:
    """执行 ``cursor agent models``，解析为 OpenAI ``/v1/models`` 风格的 ``data`` 项列表。

    解析失败、CLI 不可用或命令非零退出时返回空列表。
    """
    cli = resolve_cursor_cli()
    if not cli:
        return []
    try:
        cp = subprocess.run(
            cursor_agent_models_argv(cli),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ},
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if cp.returncode != 0:
        return []
    created = int(time.time())
    out: list[dict[str, Any]] = []
    for line in (cp.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("Available") or line.startswith("Tip:"):
            continue
        m = re.match(r"^([a-zA-Z0-9][-a-zA-Z0-9._]*)\s+-\s+(.+)$", line)
        if not m:
            continue
        mid = m.group(1)
        out.append(
            {
                "id": mid,
                "object": "model",
                "created": created,
                "owned_by": "cursor",
            }
        )
    return out


AgentOutputFormat = Literal["text", "json", "stream-json"]
AgentMode = Literal["plan", "ask", "agent"]


def normalize_agent_mode(mode: AgentMode | None) -> Literal["plan", "ask"] | None:
    """兼容旧值 ``agent``：新版 Cursor CLI 仅接受 ``plan``/``ask``。"""
    if mode is None:
        return None
    if mode == "agent":
        return "ask"
    return mode


@dataclass(frozen=True)
class CursorAgentResult:
    """:func:`run_cursor_agent` 的返回封装，便于脚本解析。"""

    returncode: int
    stdout: str
    stderr: str
    parsed: Any | None
    """``output_format=json`` 成功时为 dict；``stream-json`` 为每行 JSON 的 list；否则多为 ``None``。"""


def run_cursor_agent(
    prompt: str,
    *,
    workspace: Path | str | None = None,
    output_format: AgentOutputFormat = "json",
    trust: bool = True,
    mode: AgentMode | None = None,
    stream_partial_output: bool = False,
    force: bool = False,
    model: str | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    subprocess_progress: bool = False,
) -> CursorAgentResult:
    """非交互调用 Cursor Agent，捕获终端输出（需 ``cursor`` CLI 与账号登录）。

    等价于在 shell 中执行大致::

        cursor agent --print --output-format json --trust --workspace <dir> "<prompt>"

    - ``output_format=json``：结束时 stdout 通常为**单行** JSON，便于 ``result["result"]`` 取助手正文。
    - ``output_format=stream-json``：多行 NDJSON；可选 ``stream_partial_output`` 流式增量。
    - ``mode=ask`` / ``plan`` / ``agent``：传给 ``cursor agent --mode``。

    - ``subprocess_progress``：为 True 时使用 ``Popen`` 并写心跳 / stderr 行日志（用于 ``agent_interactive`` 等长任务）。
      也可设环境变量 ``CURSOR_BRIDGE_LOG_AGENT_SUBPROCESS=1`` 对所有调用启用。

    认证：使用本机已登录会话，或设置环境变量 ``CURSOR_API_KEY``（可向子进程传入 ``env``）。
    """
    cli = resolve_cursor_cli()
    if not cli:
        raise RuntimeError("未找到 cursor 可执行文件，无法运行 agent。")

    if model:
        model = _sanitize_subprocess_argv_text(model)

    prompt_argv, prompt_file = _prepare_agent_prompt_for_argv(prompt, workspace)

    cmd: list[str] = [cli, *cursor_agent_invocation_prefix(cli), "--print", "--output-format", output_format]
    if trust:
        cmd.append("--trust")
    if workspace is not None:
        root = Path(workspace).expanduser().resolve()
        cmd.extend(["--workspace", str(root)])
    effective_mode = normalize_agent_mode(mode)
    if effective_mode is not None:
        cmd.extend(["--mode", effective_mode])
    if stream_partial_output:
        cmd.append("--stream-partial-output")
    if force:
        cmd.append("--force")
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt_argv)

    run_env = {**os.environ, **(env or {})}
    cwd = str(Path(workspace).expanduser().resolve()) if workspace is not None else None

    use_monitor = subprocess_progress or (
        os.environ.get("CURSOR_BRIDGE_LOG_AGENT_SUBPROCESS", "").strip() == "1"
    )
    log_stderr_lines = subprocess_progress or (
        os.environ.get("CURSOR_BRIDGE_AGENT_SUBPROCESS_LIVE_STDERR", "").strip() == "1"
    )
    try:
        if use_monitor:
            cp = _run_cursor_agent_subprocess_monitored(
                cmd,
                cwd=cwd,
                env=run_env,
                timeout=timeout,
                log_stderr_lines=log_stderr_lines,
            )
        else:
            cp = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=run_env,
                cwd=cwd,
            )
    finally:
        _cleanup_prompt_file(prompt_file)

    parsed: Any | None = None
    out = cp.stdout or ""
    if cp.returncode == 0 and output_format == "json" and out.strip():
        try:
            parsed = json.loads(out.strip())
        except json.JSONDecodeError:
            parsed = None
    elif cp.returncode == 0 and output_format == "stream-json" and out.strip():
        lines: list[Any] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                lines.append({"raw": line})
        parsed = lines

    return CursorAgentResult(
        returncode=cp.returncode,
        stdout=out,
        stderr=cp.stderr or "",
        parsed=parsed,
    )


def agent_completion_text(result: CursorAgentResult) -> str:
    """从 ``run_cursor_agent`` 的结果中取出助手正文（兼容常见 JSON 字段）。"""
    p = result.parsed
    if isinstance(p, dict):
        for key in ("result", "message", "content", "text", "output"):
            v = p.get(key)
            if isinstance(v, str) and v.strip():
                return v
        # 嵌套 choices 风格
        ch = p.get("choices")
        if isinstance(ch, list) and ch:
            msg = ch[0].get("message") if isinstance(ch[0], dict) else None
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]
    if result.parsed is None:
        raw = (result.stdout or "").strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                d = json.loads(raw)
                if isinstance(d, dict):
                    return agent_completion_text(
                        CursorAgentResult(result.returncode, result.stdout, result.stderr, d)
                    )
            except json.JSONDecodeError:
                pass
        if raw:
            return raw
    return (result.stdout or "").strip()


def agent_stream_object_text(obj: Any) -> str:
    """从 ``stream-json`` 单行对象中尽量取出当前助手文本快照（用于与上一快照做差分）。"""
    if not isinstance(obj, dict):
        return ""
    for key in ("result", "message", "content", "text", "output"):
        v = obj.get(key)
        if isinstance(v, str):
            return v
    ch = obj.get("choices")
    if isinstance(ch, list) and ch:
        c0 = ch[0] if isinstance(ch[0], dict) else None
        if isinstance(c0, dict):
            msg = c0.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]
            delta = c0.get("delta")
            if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                return delta["content"]
    return ""


async def agen_cursor_agent_stream_json(
    prompt: str,
    *,
    workspace: Path | str | None = None,
    trust: bool = True,
    mode: AgentMode | None = None,
    force: bool = False,
    model: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> AsyncIterator[Any]:
    """异步迭代 ``cursor agent --output-format stream-json --stream-partial-output`` 的 stdout 每行 JSON。

    成功时每行解析为 dict 后 ``yield``；解析失败则 ``yield`` ``{\"_raw\": line}``。
    进程非零退出时在迭代末尾 ``raise RuntimeError``（stderr 摘要）。
    """
    cli = resolve_cursor_cli()
    if not cli:
        raise RuntimeError("未找到 cursor 可执行文件，无法运行 agent。")

    if model:
        model = _sanitize_subprocess_argv_text(model)

    prompt_argv, prompt_file = _prepare_agent_prompt_for_argv(prompt, workspace)

    cmd: list[str] = [
        cli,
        *cursor_agent_invocation_prefix(cli),
        "--print",
        "--output-format",
        "stream-json",
        "--stream-partial-output",
    ]
    if trust:
        cmd.append("--trust")
    if workspace is not None:
        root = Path(workspace).expanduser().resolve()
        cmd.extend(["--workspace", str(root)])
    effective_mode = normalize_agent_mode(mode)
    if effective_mode is not None:
        cmd.extend(["--mode", effective_mode])
    if force:
        cmd.append("--force")
    if model:
        cmd.extend(["--model", model])
    cmd.append(prompt_argv)

    run_env = {**os.environ, **(env or {})}
    cwd = str(Path(workspace).expanduser().resolve()) if workspace is not None else None

    readline_limit = stream_readline_limit()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=run_env,
        limit=readline_limit,
    )
    if proc.stdout is None:
        _cleanup_prompt_file(prompt_file)
        raise RuntimeError("cursor agent 未打开 stdout。")

    deadline = time.monotonic() + float(timeout) if timeout is not None and timeout > 0 else None

    try:
        while True:
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    await _terminate_agent_proc(proc)
                    raise TimeoutError("cursor agent 流式输出超时")
                read_timeout = min(max(left, 0.001), 60.0)
            else:
                read_timeout = None

            try:
                if read_timeout is not None:
                    line_b = await asyncio.wait_for(
                        _read_stdout_line(proc.stdout, max_bytes=readline_limit),
                        timeout=read_timeout,
                    )
                else:
                    line_b = await _read_stdout_line(proc.stdout, max_bytes=readline_limit)
            except asyncio.TimeoutError:
                await _terminate_agent_proc(proc)
                raise TimeoutError("cursor agent 流式读 stdout 超时") from None

            if not line_b:
                break
            line = line_b.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"_raw": line}
    finally:
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                await _terminate_agent_proc(proc)
        _cleanup_prompt_file(prompt_file)

    stderr_b = b""
    if proc.stderr is not None:
        stderr_b = await proc.stderr.read()
    rc = proc.returncode if proc.returncode is not None else -1
    if rc != 0:
        err = stderr_b.decode("utf-8", errors="replace").strip() or "cursor agent 失败"
        raise RuntimeError(err[:8000])


def run_osascript(source: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["osascript", "-e", source],
        check=check,
        text=True,
        capture_output=True,
    )


def run_applescript_file(path: Path | str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = Path(path)
    return subprocess.run(
        ["osascript", str(p)],
        check=check,
        text=True,
        capture_output=True,
    )


def open_in_cursor(
    target: Path | str,
    *,
    line: int | None = None,
    column: int | None = None,
) -> None:
    """打开文件或目录。

    - 未指定 ``line``：使用 ``open -a Cursor``（不依赖 ``cursor`` CLI，最稳）。
    - 指定 ``line``：使用 ``cursor -g file:line[:column]`` 跳转到行（需已安装 Cursor 且 CLI 可用）。
    """
    t = Path(target).expanduser().resolve()
    if line is None:
        subprocess.run(["open", "-a", "Cursor", str(t)], check=True)
        return

    if line < 1:
        raise ValueError("line 须为 >= 1 的整数")
    if column is not None and column < 1:
        raise ValueError("column 须为 >= 1 的整数")
    if not t.is_file():
        raise ValueError("指定 line/column 时 path 须为存在的文件路径")

    cli = resolve_cursor_cli()
    if not cli:
        raise RuntimeError(
            "未找到 cursor 命令行（请安装 Cursor 或将「Shell 命令: Install 'cursor'」加入 PATH），"
            "无法使用 --goto 跳转行号。"
        )

    goto = f"{t}:{line}"
    if column is not None:
        goto = f"{goto}:{column}"
    subprocess.run([cli, "-g", goto], check=True)


def activate_cursor() -> None:
    run_osascript('tell application "Cursor" to activate')


def send_keystroke(
    key: str,
    *,
    command: bool = False,
    shift: bool = False,
    option: bool = False,
    control: bool = False,
    delay_seconds: float = 0.25,
) -> None:
    """向前台 Cursor 发送按键（需辅助功能权限）。``key`` 为单字符或小写键名。"""
    mods: list[str] = []
    if command:
        mods.append("command down")
    if shift:
        mods.append("shift down")
    if option:
        mods.append("option down")
    if control:
        mods.append("control down")
    mod_clause = ""
    if mods:
        mod_clause = " using {" + ", ".join(mods) + "}"
    script = f'''
tell application "Cursor" to activate
delay {delay_seconds}
tell application "System Events"
    tell process "Cursor"
        set frontmost to true
    end tell
    keystroke "{key}"{mod_clause}
end tell
'''
    cp = run_osascript(script.strip(), check=False)
    if cp.returncode != 0:
        err = (cp.stderr or cp.stdout or "").strip()
        raise RuntimeError(f"osascript 失败: {err}")


def command_palette() -> None:
    """打开命令面板（默认 Cmd+Shift+P）。"""
    send_keystroke("p", command=True, shift=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="macOS Cursor 简单自动化")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open", help="打开路径；可选跳转到指定行（需 cursor CLI）")
    p_open.add_argument("path", type=Path, help="文件或目录")
    p_open.add_argument(
        "--line",
        "-n",
        type=int,
        metavar="N",
        default=None,
        help="打开文件并定位到第 N 行（1-based，使用 cursor -g）",
    )
    p_open.add_argument(
        "--column",
        "-c",
        type=int,
        metavar="M",
        default=None,
        help="与 --line 联用，定位到第 M 列（1-based）",
    )

    sub.add_parser("activate", help="激活 Cursor 窗口")

    p_key = sub.add_parser("keystroke", help="发送快捷键（需辅助功能）")
    p_key.add_argument("key", help="单字符，如 p")
    p_key.add_argument("--command", action="store_true", help="⌘ Command")
    p_key.add_argument("--shift", action="store_true")
    p_key.add_argument("--option", action="store_true")
    p_key.add_argument("--control", action="store_true")

    sub.add_parser("palette", help="Cmd+Shift+P 命令面板")

    p_agent = sub.add_parser(
        "agent",
        help="非交互运行 Cursor Agent 并打印 stdout（--print；建议 --format json）",
    )
    p_agent.add_argument("prompt", help="提示词，建议加引号")
    p_agent.add_argument(
        "--workspace",
        "-w",
        type=Path,
        default=None,
        help="工作区目录（默认：当前目录）",
    )
    p_agent.add_argument(
        "--format",
        choices=("text", "json", "stream-json"),
        default="json",
        dest="output_format",
        help="与 cursor agent --output-format 一致（默认 json）",
    )
    p_agent.add_argument(
        "--mode",
        choices=("plan", "ask", "agent"),
        default=None,
        help="传给 cursor agent 的 --mode（plan/ask/agent；agent 会兼容映射为 ask）",
    )
    p_agent.add_argument(
        "--no-trust",
        action="store_true",
        help="不传 --trust（可能卡在信任工作区提示）",
    )
    p_agent.add_argument(
        "--force",
        action="store_true",
        help="对应 cursor agent --force",
    )
    p_agent.add_argument("--model", default=None, help="例如 gpt-5、sonnet-4")
    p_agent.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SEC",
        help="子进程超时秒数",
    )
    p_agent.add_argument(
        "--stream-partial-output",
        action="store_true",
        help="仅在与 --format stream-json 联用时有效",
    )

    p_serve = sub.add_parser(
        "serve",
        help="启动 OpenAI 兼容 HTTP 服务（底层 cursor agent；支持图像/视频入参落盘）",
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    p_serve.add_argument("--port", type=int, default=8765, help="端口")
    p_serve.add_argument(
        "--workspace",
        "-w",
        type=Path,
        default=None,
        help="默认工作区（请求体未传时用于媒体缓存与 agent --workspace）",
    )
    p_serve.add_argument(
        "--mode",
        choices=("plan", "ask", "agent"),
        default="ask",
        help="传给 cursor agent 的 --mode（默认 ask；agent 会兼容映射为 ask）",
    )
    p_serve.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        metavar="SEC",
        help="单次 agent 子进程超时（秒）",
    )
    p_serve.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="服务日志目录（默认 ./logs；写入 bridge/uvicorn 轮转日志，并设置 CURSOR_BRIDGE_LOG_DIR）",
    )

    p_image = sub.add_parser(
        "image",
        help="文生图：本地网关 images/generations（SVG / agent_interactive PNG / SD WebUI）",
    )
    p_image.add_argument(
        "prompt",
        nargs="?",
        default="",
        help="画面描述；使用 --preset 时可省略",
    )
    p_image.add_argument("-n", type=int, default=1, help="生成张数（1–4）")
    p_image.add_argument("--size", default="1024x1024", help="如 1024x1024")
    p_image.add_argument(
        "--response-format",
        choices=("url", "b64_json"),
        default="url",
        dest="response_format",
    )
    p_image.add_argument(
        "--model",
        default=None,
        help="传给 cursor agent 的模型（仅 agent 引擎）",
    )
    p_image.add_argument(
        "--engine",
        choices=("agent", "agent_interactive", "sd_webui"),
        default=None,
        help="metadata.image_engine；agent_interactive 与 IDE 终端同款生图（PNG）",
    )
    p_image.add_argument(
        "--style",
        choices=("none", "vector", "realistic", "editorial"),
        default="none",
        help="矢量/写实/编辑插画风格（与 client.py image 一致）",
    )
    p_image.add_argument(
        "--preset",
        default=None,
        help="内置场景，如 park_flow_community",
    )
    p_image.add_argument(
        "-o",
        "--output",
        default=None,
        help="保存路径：文件或目录；省略则当前目录 generated_时间戳",
    )
    p_image.add_argument(
        "--save-as",
        choices=("svg", "png"),
        default="svg",
        dest="save_as",
        help="agent：svg 或栅格 png；sd_webui：始终为扩散 PNG",
    )

    args = parser.parse_args(argv)
    if args.cmd == "open":
        if args.column is not None and args.line is None:
            parser.error("--column 必须与 --line 同时使用")
        open_in_cursor(args.path, line=args.line, column=args.column)
    elif args.cmd == "activate":
        activate_cursor()
    elif args.cmd == "keystroke":
        send_keystroke(
            args.key,
            command=args.command,
            shift=args.shift,
            option=args.option,
            control=args.control,
        )
    elif args.cmd == "palette":
        command_palette()
    elif args.cmd == "agent":
        r = run_cursor_agent(
            args.prompt,
            workspace=args.workspace,
            output_format=args.output_format,
            trust=not args.no_trust,
            mode=args.mode,
            stream_partial_output=args.stream_partial_output,
            force=args.force,
            model=args.model,
            timeout=args.timeout,
        )
        sys.stdout.write(r.stdout)
        if r.stderr:
            sys.stderr.write(r.stderr)
        return r.returncode
    elif args.cmd == "serve":
        try:
            from .openai_bridge import create_app
        except ImportError:
            from openai_bridge import create_app

        import uvicorn

        log_dir = args.log_dir.expanduser().resolve()
        log_dir.mkdir(parents=True, exist_ok=True)
        os.environ["CURSOR_BRIDGE_LOG_DIR"] = str(log_dir)

        workspace = args.workspace
        if workspace is None:
            workspace = Path.cwd()
        app = create_app(
            default_workspace=workspace,
            agent_mode=args.mode,
            agent_timeout=args.timeout,
        )
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
            log_config=build_serve_uvicorn_log_config(log_dir),
        )
        return 0
    elif args.cmd == "image":
        import client as client_mod

        if not (args.prompt or "").strip() and not args.preset:
            parser.error("image 子命令需要 prompt 或 --preset")
        client_mod._load_env_file()
        client_mod.create_image(
            args.prompt or "",
            n=args.n,
            size=args.size,
            response_format=args.response_format,
            model=args.model,
            output=args.output,
            save_as=args.save_as,
            image_engine=args.engine,
            style=args.style,
            preset=args.preset,
        )
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
