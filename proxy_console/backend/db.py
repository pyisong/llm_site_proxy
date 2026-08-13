"""PostgreSQL persistence for proxy_console."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

PROXIES = (
    ("deepseek-openai-proxy", "DeepSeek", True),
    ("kimi-openai-proxy", "Kimi", True),
    ("stepfun-openai-proxy", "StepFun", True),
    ("qwen-openai-proxy", "Qwen", True),
    ("metaso-openai-proxy", "Metaso", True),
    ("cursor-openai-bridge", "Cursor Bridge", False),
    ("azure-tts-http-api", "Azure TTS", True),
)

MODES = ("health", "models", "chat")


def database_url() -> str:
    url = (os.getenv("CONSOLE_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if url:
        return url
    # Local fallback when Postgres is on localhost
    return (
        "postgresql://console:console@127.0.0.1:5433/proxy_console"
    )


def _connect() -> psycopg.Connection:
    return psycopg.connect(database_url(), row_factory=dict_row)


@contextmanager
def db() -> Iterator[psycopg.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def wait_for_db(*, attempts: int = 60, delay: float = 1.0) -> None:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            with _connect() as conn:
                conn.execute("SELECT 1")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay)
    raise RuntimeError(f"postgres not ready: {last}")


def init_db() -> None:
    wait_for_db()
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_events (
              id TEXT PRIMARY KEY,
              proxy_id TEXT NOT NULL,
              mode TEXT NOT NULL DEFAULT 'chat',
              path TEXT,
              status_code INTEGER,
              latency_ms DOUBLE PRECISION,
              model TEXT,
              error TEXT,
              created_at DOUBLE PRECISION NOT NULL,
              meta_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_req_created ON request_events(created_at DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_req_proxy ON request_events(proxy_id, created_at DESC)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connectivity_results (
              id TEXT PRIMARY KEY,
              proxy_id TEXT NOT NULL,
              mode TEXT NOT NULL,
              ok INTEGER NOT NULL,
              latency_ms DOUBLE PRECISION,
              detail TEXT,
              created_at DOUBLE PRECISION NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_conn_latest
              ON connectivity_results(proxy_id, mode, created_at DESC)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_status (
              proxy_id TEXT PRIMARY KEY,
              state TEXT NOT NULL,
              last_ok_at DOUBLE PRECISION,
              last_fail_at DOUBLE PRECISION,
              message TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_disabled (
              name TEXT PRIMARY KEY,
              disabled_at DOUBLE PRECISION NOT NULL,
              reason TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_usage_events (
              id TEXT PRIMARY KEY,
              skill_name TEXT NOT NULL,
              label TEXT NOT NULL,
              request_id TEXT,
              created_at DOUBLE PRECISION NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_skill_usage
              ON skill_usage_events(skill_name, created_at DESC)
            """
        )
        for proxy_id, _name, _ka in PROXIES:
            conn.execute(
                """
                INSERT INTO auth_status(proxy_id, state, message)
                VALUES (%s, %s, %s)
                ON CONFLICT (proxy_id) DO NOTHING
                """,
                (proxy_id, "unknown", None),
            )


def seed_demo_if_empty() -> None:
    with db() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM request_events").fetchone()["c"]
        if n > 0:
            return
        now = time.time()
        demo = [
            ("deepseek-openai-proxy", "chat", 200, 842.0, "deepseek-chat", None),
            ("deepseek-openai-proxy", "chat", 200, 1204.0, "deepseek-chat", None),
            ("kimi-openai-proxy", "chat", 200, 1560.0, "moonshot-v1", None),
            ("kimi-openai-proxy", "chat", 502, 3100.0, "moonshot-v1", "upstream busy"),
            ("qwen-openai-proxy", "chat", 200, 980.0, "qwen-max", None),
            ("stepfun-openai-proxy", "chat", 401, 120.0, None, "login required"),
            ("cursor-openai-bridge", "chat", 200, 4200.0, "composer", None),
            ("azure-tts-http-api", "tts", 200, 380.0, "zh-CN-XiaoxiaoNeural", None),
        ]
        for i, (pid, mode, code, lat, model, err) in enumerate(demo):
            conn.execute(
                """INSERT INTO request_events
                   (id, proxy_id, mode, path, status_code, latency_ms, model, error, created_at, meta_json)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    str(uuid.uuid4()),
                    pid,
                    mode,
                    "/v1/chat/completions" if mode == "chat" else "/v1/tts",
                    code,
                    lat,
                    model,
                    err,
                    now - (len(demo) - i) * 90,
                    None,
                ),
            )
        for pid, _n, _ka in PROXIES:
            for mode in MODES:
                ok = 0 if pid == "stepfun-openai-proxy" and mode == "chat" else 1
                conn.execute(
                    """INSERT INTO connectivity_results
                       (id, proxy_id, mode, ok, latency_ms, detail, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        str(uuid.uuid4()),
                        pid,
                        mode,
                        ok,
                        40.0 + (hash(pid + mode) % 200),
                        "login required" if not ok else "ok",
                        now - 30,
                    ),
                )
            state = "login_required" if pid == "stepfun-openai-proxy" else "ok"
            conn.execute(
                """UPDATE auth_status
                   SET state=%s, last_ok_at=%s, last_fail_at=%s, message=%s
                   WHERE proxy_id=%s""",
                (
                    state,
                    now if state == "ok" else None,
                    now if state != "ok" else None,
                    "storage state expired" if state != "ok" else None,
                    pid,
                ),
            )
        for skill, label in (
            ("hv-analysis", "requested+evidenced"),
            ("khazix-writer", "requested"),
            ("neat-freak", "evidenced"),
        ):
            conn.execute(
                """INSERT INTO skill_usage_events
                   (id, skill_name, label, request_id, created_at)
                   VALUES (%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), skill, label, f"req-{skill}", now - 600),
            )


def last_request_at(proxy_id: str) -> float | None:
    with db() as conn:
        row = conn.execute(
            """SELECT MAX(created_at) AS t FROM request_events
               WHERE proxy_id=%s""",
            (proxy_id,),
        ).fetchone()
    if not row or row["t"] is None:
        return None
    return float(row["t"])


def proxy_idle_seconds(proxy_id: str) -> float | None:
    """Seconds since last request; None if never seen."""
    ts = last_request_at(proxy_id)
    if ts is None:
        return None
    return max(0.0, time.time() - ts)


def needs_keepalive(proxy_id: str, *, idle_sec: float) -> bool:
    """True when no request within idle_sec (also true if never requested)."""
    idle = proxy_idle_seconds(proxy_id)
    if idle is None:
        return True
    return idle >= idle_sec


def ingest_request(payload: dict[str, Any]) -> dict[str, Any]:
    rid = payload.get("id") or str(uuid.uuid4())
    created = float(payload.get("created_at") or time.time())
    meta = payload.get("meta")
    with db() as conn:
        conn.execute(
            """INSERT INTO request_events
               (id, proxy_id, mode, path, status_code, latency_ms, model, error, created_at, meta_json)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                rid,
                payload["proxy_id"],
                payload.get("mode") or "chat",
                payload.get("path"),
                payload.get("status_code"),
                payload.get("latency_ms"),
                payload.get("model"),
                payload.get("error"),
                created,
                json.dumps(meta, ensure_ascii=False) if meta is not None else None,
            ),
        )
    return {"id": rid}


def last_request_at(proxy_id: str) -> float | None:
    """Latest request_events.created_at for proxy, or None if never."""
    with db() as conn:
        row = conn.execute(
            "SELECT MAX(created_at) AS t FROM request_events WHERE proxy_id=%s",
            (proxy_id,),
        ).fetchone()
    if not row or row["t"] is None:
        return None
    return float(row["t"])


def is_idle_since(proxy_id: str, idle_sec: float) -> bool:
    """True if no request within idle_sec (or never requested)."""
    last = last_request_at(proxy_id)
    if last is None:
        return True
    return (time.time() - last) >= idle_sec


def last_activity_at(proxy_id: str) -> float | None:
    """Latest request_events timestamp for a proxy (includes keepalive probes)."""
    with db() as conn:
        row = conn.execute(
            """SELECT MAX(created_at) AS t FROM request_events WHERE proxy_id=%s""",
            (proxy_id,),
        ).fetchone()
    t = row["t"] if row else None
    return float(t) if t is not None else None


def proxy_is_idle(proxy_id: str, *, idle_sec: float) -> bool:
    last = last_activity_at(proxy_id)
    if last is None:
        return True
    return (time.time() - last) >= idle_sec


def list_requests(
    *,
    proxy_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    q = "SELECT * FROM request_events"
    args: list[Any] = []
    if proxy_id:
        q += " WHERE proxy_id=%s"
        args.append(proxy_id)
    q += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
    args.extend([limit, offset])
    with db() as conn:
        rows = conn.execute(q, args).fetchall()
    return [_row_request(r) for r in rows]


def get_request(rid: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM request_events WHERE id=%s", (rid,)
        ).fetchone()
    return _row_request(row) if row else None


def _row_request(row: dict[str, Any]) -> dict[str, Any]:
    meta = None
    if row.get("meta_json"):
        try:
            meta = json.loads(row["meta_json"])
        except json.JSONDecodeError:
            meta = row["meta_json"]
    return {
        "id": row["id"],
        "proxy_id": row["proxy_id"],
        "mode": row["mode"],
        "path": row["path"],
        "status_code": row["status_code"],
        "latency_ms": row["latency_ms"],
        "model": row["model"],
        "error": row["error"],
        "created_at": row["created_at"],
        "meta": meta,
    }


def overview_bucket_sec(window_sec: int) -> int:
    """Pick bucket width so series stays roughly 12-72 bars."""
    w = max(300, int(window_sec))
    raw = max(300, w // 48)
    for sec in (300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400):
        if sec >= raw:
            return sec
    return 86400


def _local_day_start(ts: float) -> float:
    """服务器本地时区的自然日 00:00（epoch 秒）。"""
    lt = time.localtime(ts)
    return time.mktime(
        (lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, lt.tm_wday, lt.tm_yday, lt.tm_isdst)
    )


def overview(window_sec: int = 3600) -> dict[str, Any]:
    now = time.time()
    window_sec = max(300, min(int(window_sec), 30 * 86400))
    bucket_sec = overview_bucket_sec(window_sec)

    # 日粒度：按自然日对齐（含今天），避免滚动 24h 桶把今日请求标成「昨天」
    calendar_days = bucket_sec >= 86400
    if calendar_days:
        n_buckets = max(1, window_sec // 86400)
        today0 = _local_day_start(now)
        since = today0 - (n_buckets - 1) * 86400
        bucket_sec = 86400
    else:
        since = now - window_sec
        n_buckets = max(1, window_sec // bucket_sec)

    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM request_events WHERE created_at>=%s",
            (since,),
        ).fetchone()["c"]
        errors = conn.execute(
            """SELECT COUNT(*) AS c FROM request_events
               WHERE created_at>=%s AND (status_code IS NULL OR status_code>=400)""",
            (since,),
        ).fetchone()["c"]
        avg_lat = conn.execute(
            """SELECT AVG(latency_ms) AS a FROM request_events
               WHERE created_at>=%s AND latency_ms IS NOT NULL""",
            (since,),
        ).fetchone()["a"]
        by_proxy = conn.execute(
            """SELECT proxy_id,
                      COUNT(*) AS requests,
                      SUM(CASE WHEN status_code IS NULL OR status_code>=400 THEN 1 ELSE 0 END) AS errors,
                      AVG(latency_ms) AS avg_latency_ms
               FROM request_events WHERE created_at>=%s
               GROUP BY proxy_id""",
            (since,),
        ).fetchall()
        series_rows = conn.execute(
            """SELECT CAST((created_at - %s) / %s AS INTEGER) AS bucket,
                      COUNT(*) AS requests,
                      SUM(CASE WHEN status_code IS NULL OR status_code>=400 THEN 1 ELSE 0 END) AS errors
               FROM request_events WHERE created_at>=%s
               GROUP BY bucket ORDER BY bucket""",
            (since, bucket_sec, since),
        ).fetchall()
        auth = {
            r["proxy_id"]: dict(r)
            for r in conn.execute("SELECT * FROM auth_status").fetchall()
        }

    proxy_map = {r["proxy_id"]: dict(r) for r in by_proxy}
    services = []
    for pid, name, keepalive in PROXIES:
        stats = proxy_map.get(pid, {})
        a = auth.get(pid, {})
        services.append(
            {
                "id": pid,
                "name": name,
                "keepalive": keepalive,
                "requests": int(stats.get("requests") or 0),
                "errors": int(stats.get("errors") or 0),
                "avg_latency_ms": round(float(stats["avg_latency_ms"]), 1)
                if stats.get("avg_latency_ms") is not None
                else None,
                "auth_state": a.get("state") or "unknown",
                "auth_message": a.get("message"),
            }
        )

    series = []
    buckets = {int(r["bucket"]): r for r in series_rows}
    for b in range(n_buckets):
        row = buckets.get(b)
        series.append(
            {
                "t": since + b * bucket_sec,
                "requests": int(row["requests"]) if row else 0,
                "errors": int(row["errors"]) if row else 0,
            }
        )

    return {
        "window_sec": window_sec,
        "bucket_sec": bucket_sec,
        "kpi": {
            "requests": total,
            "errors": errors,
            "error_rate": round(errors / total, 4) if total else 0.0,
            "avg_latency_ms": round(float(avg_lat), 1) if avg_lat is not None else None,
            "services_online": sum(1 for s in services if s["auth_state"] == "ok"),
            "services_total": len(services),
        },
        "services": services,
        "series": series,
        "generated_at": now,
        "calendar_aligned": calendar_days,
    }


def save_connectivity(
    proxy_id: str,
    mode: str,
    *,
    ok: bool,
    latency_ms: float | None,
    detail: str | None,
) -> dict[str, Any]:
    rid = str(uuid.uuid4())
    now = time.time()
    with db() as conn:
        conn.execute(
            """INSERT INTO connectivity_results
               (id, proxy_id, mode, ok, latency_ms, detail, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (rid, proxy_id, mode, 1 if ok else 0, latency_ms, detail, now),
        )
        if not ok and _looks_like_auth_fail(detail or ""):
            conn.execute(
                """UPDATE auth_status
                   SET state='login_required', last_fail_at=%s, message=%s
                   WHERE proxy_id=%s""",
                (now, detail, proxy_id),
            )
        elif ok:
            conn.execute(
                """UPDATE auth_status
                   SET state='ok', last_ok_at=%s, message=NULL
                   WHERE proxy_id=%s""",
                (now, proxy_id),
            )
    return {
        "id": rid,
        "proxy_id": proxy_id,
        "mode": mode,
        "ok": ok,
        "latency_ms": latency_ms,
        "detail": detail,
        "created_at": now,
    }


def _looks_like_auth_fail(detail: str) -> bool:
    d = detail.lower()
    keys = ("login", "sign_in", "signin", "storage", "401", "unauthorized", "auth")
    return any(k in d for k in keys)


def latest_connectivity() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """SELECT c.* FROM connectivity_results c
               INNER JOIN (
                 SELECT proxy_id, mode, MAX(created_at) AS mx
                 FROM connectivity_results GROUP BY proxy_id, mode
               ) t ON c.proxy_id=t.proxy_id AND c.mode=t.mode AND c.created_at=t.mx
               ORDER BY c.proxy_id, c.mode"""
        ).fetchall()
        auth = {
            r["proxy_id"]: dict(r)
            for r in conn.execute("SELECT * FROM auth_status").fetchall()
        }
    out = []
    for r in rows:
        item = dict(r)
        item["ok"] = bool(item["ok"])
        a = auth.get(r["proxy_id"], {})
        item["auth_state"] = a.get("state")
        item["auth_message"] = a.get("message")
        out.append(item)
    return out


def list_auth_status() -> list[dict[str, Any]]:
    """Always emit one row per PROXIES entry (left-join DB), so new proxies appear immediately."""
    with db() as conn:
        rows = {
            r["proxy_id"]: dict(r)
            for r in conn.execute("SELECT * FROM auth_status").fetchall()
        }
    out: list[dict[str, Any]] = []
    for pid, name, keepalive in PROXIES:
        r = rows.get(pid) or {
            "proxy_id": pid,
            "state": "unknown",
            "message": None,
            "last_ok_at": None,
            "last_fail_at": None,
        }
        out.append(
            {
                **r,
                "name": name,
                "keepalive": keepalive,
            }
        )
    return out


def mark_auth_refreshed(proxy_id: str) -> dict[str, Any]:
    if proxy_id not in {p[0] for p in PROXIES}:
        raise KeyError(proxy_id)
    msg = "operator marked storage refreshed; awaiting next probe"
    with db() as conn:
        conn.execute(
            """INSERT INTO auth_status(proxy_id, state, message, last_ok_at)
               VALUES (%s, 'unknown', %s, NULL)
               ON CONFLICT (proxy_id) DO UPDATE SET
                 state='unknown', message=EXCLUDED.message, last_ok_at=NULL""",
            (proxy_id, msg),
        )
        row = conn.execute(
            "SELECT * FROM auth_status WHERE proxy_id=%s", (proxy_id,)
        ).fetchone()
    if not row:
        raise KeyError(proxy_id)
    return dict(row)


def set_skill_disabled(name: str, *, disabled: bool, reason: str | None = None) -> None:
    with db() as conn:
        if disabled:
            conn.execute(
                """INSERT INTO skill_disabled(name, disabled_at, reason)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (name) DO UPDATE SET
                     disabled_at=EXCLUDED.disabled_at,
                     reason=EXCLUDED.reason""",
                (name, time.time(), reason),
            )
        else:
            conn.execute("DELETE FROM skill_disabled WHERE name=%s", (name,))


def disabled_skills() -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM skill_disabled").fetchall()
    return {r["name"]: dict(r) for r in rows}


def skill_usage_summary() -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """SELECT skill_name,
                      COUNT(*) AS uses,
                      MAX(created_at) AS last_used_at
               FROM skill_usage_events GROUP BY skill_name"""
        ).fetchall()
    return {
        r["skill_name"]: {
            "uses": r["uses"],
            "last_used_at": r["last_used_at"],
        }
        for r in rows
    }


def skill_usage_detail(name: str, limit: int = 50) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM skill_usage_events
               WHERE skill_name=%s ORDER BY created_at DESC LIMIT %s""",
            (name, max(1, min(200, limit))),
        ).fetchall()
    return [dict(r) for r in rows]


def ingest_skill_usage(payload: dict[str, Any]) -> dict[str, Any]:
    rid = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO skill_usage_events
               (id, skill_name, label, request_id, created_at)
               VALUES (%s,%s,%s,%s,%s)""",
            (
                rid,
                payload["skill_name"],
                payload.get("label") or "evidenced",
                payload.get("request_id"),
                float(payload.get("created_at") or time.time()),
            ),
        )
    return {"id": rid}


def db_host_label() -> str:
    try:
        return urlparse(database_url()).hostname or "postgres"
    except Exception:  # noqa: BLE001
        return "postgres"
