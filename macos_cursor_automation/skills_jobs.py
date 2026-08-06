"""异步 skill 安装任务（主要为慢速 git clone）。"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

try:
    from .skills_store import SkillStoreError, install
except ImportError:
    from skills_store import SkillStoreError, install

_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def _public(job: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": job["id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "source": job.get("source"),
        "ref": job.get("ref"),
    }
    if job.get("started_at") is not None:
        out["started_at"] = job["started_at"]
    if job.get("finished_at") is not None:
        out["finished_at"] = job["finished_at"]
    if job.get("result") is not None:
        out["result"] = job["result"]
    if job.get("error") is not None:
        out["error"] = job["error"]
        out["error_status"] = job.get("error_status", 400)
    return out


def get_job(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return _public(job) if job else None


def _run_job(job_id: str, kwargs: dict[str, Any]) -> None:
    log = logging.getLogger("cursor_openai_bridge.skills")
    now = time.time()
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job["status"] = "running"
        job["started_at"] = now
        job["updated_at"] = now
    log.info(
        "skill job start id=%s source=%s ref=%s",
        job_id,
        kwargs.get("source"),
        kwargs.get("ref"),
    )
    try:
        meta = install(**kwargs)
        with _lock:
            job = _jobs.get(job_id)
            if not job:
                return
            job["status"] = "succeeded"
            job["result"] = meta
            job["finished_at"] = time.time()
            job["updated_at"] = job["finished_at"]
        log.info(
            "skill job ok id=%s name=%s",
            job_id,
            (meta or {}).get("name"),
        )
    except SkillStoreError as e:
        with _lock:
            job = _jobs.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = e.message
            job["error_status"] = e.status_code
            job["finished_at"] = time.time()
            job["updated_at"] = job["finished_at"]
        log.warning(
            "skill job failed id=%s status=%s err=%s",
            job_id,
            e.status_code,
            e.message,
        )
    except Exception as e:  # noqa: BLE001
        with _lock:
            job = _jobs.get(job_id)
            if not job:
                return
            job["status"] = "failed"
            job["error"] = str(e) or e.__class__.__name__
            job["error_status"] = 500
            job["finished_at"] = time.time()
            job["updated_at"] = job["finished_at"]
        log.exception("skill job crashed id=%s", job_id)

def start_install_job(
    *,
    source: str,
    ref: str,
    name: str | None = None,
    overwrite: bool = False,
    subdir: str | None = None,
    branch: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    kwargs = {
        "source": source,
        "ref": ref,
        "name": name,
        "overwrite": overwrite,
        "subdir": subdir,
        "branch": branch,
        "proxy": proxy,
    }
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "source": source,
            "ref": ref,
            "result": None,
            "error": None,
            "error_status": None,
        }
        if len(_jobs) > 50:
            oldest = sorted(_jobs.values(), key=lambda j: j["created_at"])[:-50]
            for j in oldest:
                _jobs.pop(j["id"], None)
    thread = threading.Thread(
        target=_run_job,
        args=(job_id, kwargs),
        name=f"skill-install-{job_id}",
        daemon=True,
    )
    thread.start()
    with _lock:
        return _public(_jobs[job_id])
