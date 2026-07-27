"""Proxy catalog: live-probe discovery API for llm_site_proxy services."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request

from registry import KNOWN_CAPABILITIES, SERVICES, ProxyService

PROBE_TIMEOUT = float(os.getenv("CATALOG_PROBE_TIMEOUT", "2.0"))


def _public_host(request: Request | None = None) -> str:
    explicit = (os.getenv("CATALOG_PUBLIC_HOST") or "").strip()
    if explicit:
        return explicit
    if request is not None:
        # Prefer X-Forwarded-Host when behind a reverse proxy
        forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
        if forwarded:
            return forwarded.split(":")[0]
        host = (request.headers.get("host") or "").split(":")[0].strip()
        if host and host not in {"localhost", "127.0.0.1", "proxy-catalog"}:
            return host
    return "127.0.0.1"


def _auth_header(svc: ProxyService) -> dict[str, str]:
    if not svc.auth_env:
        return {}
    key = (os.getenv(svc.auth_env) or "").strip()
    if not key:
        # Most browser proxies default to local-secret
        if svc.id != "cursor-openai-bridge":
            key = "local-secret"
        else:
            return {}
    return {"Authorization": f"Bearer {key}"}


async def _probe_one(
    client: httpx.AsyncClient,
    svc: ProxyService,
    public_host: str,
) -> dict[str, Any]:
    health_url = f"{svc.probe_base}{svc.health_path}"
    status = "offline"
    error: str | None = None
    models: list[dict[str, Any]] = []

    try:
        resp = await client.get(health_url)
        if resp.status_code < 500:
            status = "online"
        else:
            error = f"health HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001 — surface probe failures as offline
        error = str(exc) or exc.__class__.__name__

    if status == "online" and svc.models_path and "llm" in svc.capabilities:
        models_url = f"{svc.probe_base}{svc.models_path}"
        try:
            mresp = await client.get(models_url, headers=_auth_header(svc))
            if mresp.status_code < 400:
                payload = mresp.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    models = [
                        {"id": item.get("id"), "object": item.get("object", "model")}
                        for item in data
                        if isinstance(item, dict) and item.get("id")
                    ]
                else:
                    error = (error + "; " if error else "") + "models: unexpected payload"
            else:
                error = (error + "; " if error else "") + f"models HTTP {mresp.status_code}"
        except Exception as exc:  # noqa: BLE001
            error = (error + "; " if error else "") + f"models: {exc}"

    item: dict[str, Any] = {
        "id": svc.id,
        "name": svc.name,
        "status": status,
        "base_url": svc.public_base_url(public_host),
        "capabilities": list(svc.capabilities),
        "endpoints": svc.endpoints.as_dict(),
        "models": models,
    }
    if error and status == "offline":
        item["error"] = error
    elif error:
        item["warning"] = error
    return item


def _by_capability(services: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {cap: [] for cap in KNOWN_CAPABILITIES}
    for svc in services:
        if svc.get("status") != "online":
            continue
        entry = {
            "id": svc["id"],
            "name": svc["name"],
            "base_url": svc["base_url"],
            "endpoints": svc.get("endpoints") or {},
            "models": svc.get("models") or [],
        }
        for cap in svc.get("capabilities") or []:
            if cap in out:
                out[cap].append(entry)
    return out


async def discover_services(public_host: str) -> dict[str, Any]:
    timeout = httpx.Timeout(PROBE_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(
            *[_probe_one(client, svc, public_host) for svc in SERVICES]
        )
    services = list(results)
    return {
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "public_host": public_host,
        "services": services,
        "by_capability": _by_capability(services),
    }


def create_app() -> FastAPI:
    app = FastAPI(title="llm_site_proxy catalog", version="1.0.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/services")
    async def list_services(
        request: Request,
        capability: str | None = Query(
            default=None,
            description="Filter by capability: llm | image | video | tts",
        ),
    ) -> dict[str, Any]:
        host = _public_host(request)
        payload = await discover_services(host)
        if capability:
            cap = capability.strip().lower()
            payload["services"] = [
                s for s in payload["services"] if cap in (s.get("capabilities") or [])
            ]
            by_cap = payload.get("by_capability") or {}
            payload["by_capability"] = {cap: list(by_cap.get(cap) or [])}
        return payload

    return app


app = create_app()
