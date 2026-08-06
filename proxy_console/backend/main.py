"""Proxy Console FastAPI BFF."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import catalog_client
import db
import skills_proxy
from keepalive import runner

logging.basicConfig(level=os.getenv("CONSOLE_LOG_LEVEL", "INFO"))
log = logging.getLogger("proxy_console")

STATIC_DIR = Path(os.getenv("CONSOLE_STATIC_DIR", str(Path(__file__).resolve().parents[1] / "frontend" / "dist")))


class IngestRequest(BaseModel):
    proxy_id: str
    mode: str = "chat"
    path: str | None = None
    status_code: int | None = None
    latency_ms: float | None = None
    model: str | None = None
    error: str | None = None
    id: str | None = None
    created_at: float | None = None
    meta: dict[str, Any] | None = None


class ProbeBody(BaseModel):
    proxy_id: str | None = None
    mode: str | None = None


class SkillInstallBody(BaseModel):
    source: str = Field(default="auto", description="path | git | url | github | auto")
    ref: str | None = None
    path: str | None = None
    url: str | None = None
    name: str | None = None
    subdir: str | None = None
    branch: str | None = None
    proxy: str | None = None
    overwrite: bool = True


class SkillDisableBody(BaseModel):
    reason: str | None = None


class SkillUsageIngest(BaseModel):
    skill_name: str
    label: str = "evidenced"
    request_id: str | None = None
    created_at: float | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    if os.getenv("CONSOLE_SEED_DEMO", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    ):
        db.seed_demo_if_empty()
    runner.start()
    log.info("proxy_console ready db=%s", db.db_host_label())
    yield
    await runner.stop()


app = FastAPI(title="Proxy Console", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/overview")
def api_overview(
    window_sec: int = Query(3600, ge=300, le=30 * 86400),
) -> dict[str, Any]:
    return db.overview(window_sec)


@app.get("/api/requests")
def api_requests(
    proxy_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    items = db.list_requests(proxy_id=proxy_id, limit=limit, offset=offset)
    return {"items": items, "limit": limit, "offset": offset}


@app.get("/api/requests/{rid}")
def api_request_detail(rid: str) -> dict[str, Any]:
    item = db.get_request(rid)
    if not item:
        raise HTTPException(404, "request not found")
    return item


@app.post("/api/ingest/request")
def api_ingest_request(body: IngestRequest) -> dict[str, Any]:
    return db.ingest_request(body.model_dump())


@app.get("/api/connectivity")
def api_connectivity() -> dict[str, Any]:
    return {
        "results": db.latest_connectivity(),
        "proxies": [
            {"id": p[0], "name": p[1], "keepalive": p[2]} for p in db.PROXIES
        ],
        "modes": list(db.MODES),
    }


@app.post("/api/connectivity/probe")
async def api_probe(body: ProbeBody) -> dict[str, Any]:
    results = await catalog_client.probe_all(body.proxy_id, body.mode)
    return {"results": results}


@app.get("/api/auth-status")
def api_auth_status() -> dict[str, Any]:
    return {"items": db.list_auth_status()}


@app.post("/api/auth/{proxy_id}/mark-refreshed")
def api_mark_refreshed(proxy_id: str) -> dict[str, Any]:
    try:
        return db.mark_auth_refreshed(proxy_id)
    except KeyError:
        raise HTTPException(404, "unknown proxy") from None


@app.get("/api/skills")
async def api_skills() -> dict[str, Any]:
    remote = await skills_proxy.list_skills()
    disabled = db.disabled_skills()
    usage = db.skill_usage_summary()
    skills = []
    for s in remote.get("skills") or []:
        name = s.get("name")
        if not name:
            continue
        d = disabled.get(name)
        u = usage.get(name, {})
        skills.append(
            {
                **s,
                "disabled": bool(d),
                "disabled_at": d.get("disabled_at") if d else None,
                "disabled_reason": d.get("reason") if d else None,
                "uses": u.get("uses") or 0,
                "last_used_at": u.get("last_used_at"),
            }
        )
    return {
        "skills": skills,
        "source": remote.get("source"),
        "error": remote.get("error"),
    }


@app.post("/api/skills/install")
async def api_skills_install(body: SkillInstallBody) -> Any:
    status, payload = await skills_proxy.install_skill(body.model_dump(exclude_none=True))
    if status >= 400:
        raise HTTPException(status, payload)
    return payload


@app.get("/api/skills/jobs/{job_id}")
async def api_skills_job(job_id: str) -> Any:
    status, payload = await skills_proxy.get_install_job(job_id)
    if status >= 400:
        raise HTTPException(status, payload)
    return payload


@app.post("/api/skills/upload")
async def api_skills_upload(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    overwrite: bool = Form(True),
    subdir: str | None = Form(None),
) -> Any:
    raw = await file.read()
    status, payload = await skills_proxy.upload_skill(
        filename=file.filename or "skill.zip",
        content=raw,
        name=name,
        overwrite=overwrite,
        subdir=subdir,
    )
    if status >= 400:
        raise HTTPException(status, payload)
    return payload


@app.delete("/api/skills/{name}")
async def api_skills_delete(name: str) -> Any:
    status, payload = await skills_proxy.delete_skill(name)
    if status >= 400:
        raise HTTPException(status, payload)
    db.set_skill_disabled(name, disabled=False)
    return payload


@app.post("/api/skills/{name}/disable")
def api_skills_disable(name: str, body: SkillDisableBody | None = None) -> dict[str, Any]:
    db.set_skill_disabled(name, disabled=True, reason=(body.reason if body else None))
    return {"name": name, "disabled": True}


@app.post("/api/skills/{name}/enable")
def api_skills_enable(name: str) -> dict[str, Any]:
    db.set_skill_disabled(name, disabled=False)
    return {"name": name, "disabled": False}


@app.get("/api/skills/{name}/usage")
def api_skills_usage(name: str, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"items": db.skill_usage_detail(name, limit)}


@app.post("/api/ingest/skill-usage")
def api_ingest_skill_usage(body: SkillUsageIngest) -> dict[str, Any]:
    return db.ingest_skill_usage(body.model_dump())


def _mount_spa() -> None:
    if not STATIC_DIR.is_dir():
        log.warning("static dir missing: %s (API-only mode)", STATIC_DIR)
        return
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")


_mount_spa()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("CONSOLE_PORT", "18020")),
        reload=False,
    )
