"""服务端 Playwright 登录会话：CDP 投屏 + 键鼠转发 + 导出 storage_state。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

from login_sites import LoginSite, get_site, validate_and_enrich_state

log = logging.getLogger("proxy_console.login_session")

SESSION_TTL_SEC = float(os.getenv("CONSOLE_LOGIN_SESSION_TTL_SEC", "900"))
VIEWPORT = {"width": 1280, "height": 900}


@dataclass
class LoginSession:
    id: str
    site: LoginSite
    created_at: float = field(default_factory=time.time)
    bearer_token: str | None = None
    _playwright: Any = None
    _browser: Any = None
    _context: Any = None
    _page: Any = None
    _cdp: Any = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _closed: bool = False
    last_frame_b64: str | None = None
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > SESSION_TTL_SEC

    def publish_frame(self, data: str) -> None:
        self.last_frame_b64 = data
        msg = {"type": "frame", "data": data, "w": VIEWPORT["width"], "h": VIEWPORT["height"]}
        for q in list(self.subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    pass



class LoginSessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, LoginSession] = {}
        self._by_proxy: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start(self, proxy_id: str) -> LoginSession:
        site = get_site(proxy_id)
        async with self._lock:
            old_id = self._by_proxy.get(proxy_id)
            if old_id and old_id in self._sessions:
                await self._close_unlocked(old_id)
            session = LoginSession(id=uuid.uuid4().hex, site=site)
            self._sessions[session.id] = session
            self._by_proxy[proxy_id] = session.id

        await self._launch(session)
        return session

    def get(self, session_id: str) -> LoginSession | None:
        sess = self._sessions.get(session_id)
        if sess is None:
            return None
        if sess.expired or sess._closed:
            return None
        return sess

    async def close(self, session_id: str) -> None:
        async with self._lock:
            await self._close_unlocked(session_id)

    async def _close_unlocked(self, session_id: str) -> None:
        sess = self._sessions.pop(session_id, None)
        if not sess:
            return
        if self._by_proxy.get(sess.site.proxy_id) == session_id:
            self._by_proxy.pop(sess.site.proxy_id, None)
        sess._closed = True
        try:
            if sess._cdp:
                try:
                    await sess._cdp.send("Page.stopScreencast")
                except Exception:  # noqa: BLE001
                    pass
            if sess._context:
                await sess._context.close()
            if sess._browser:
                await sess._browser.close()
            if sess._playwright:
                await sess._playwright.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("close session %s: %s", session_id, exc)

    async def _launch(self, session: LoginSession) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "proxy-console 未安装 Playwright。请重建镜像（Dockerfile 已含 chromium）。"
            ) from exc

        proxy = (os.getenv("CONSOLE_LOGIN_HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "").strip()
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": ["--disable-dev-shm-usage", "--no-sandbox"],
        }
        context_kwargs: dict[str, Any] = {
            "viewport": VIEWPORT,
            "locale": "zh-CN",
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
        }
        if proxy:
            context_kwargs["proxy"] = {"server": proxy}

        pw = await async_playwright().start()
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()

        def on_request(request: Any) -> None:
            try:
                headers = request.headers
                auth = headers.get("authorization") or headers.get("Authorization")
                if not auth:
                    return
                token = str(auth).strip()
                if token.lower().startswith("bearer ") and len(token) > 7:
                    session.bearer_token = token[7:].strip()
            except Exception:  # noqa: BLE001
                return

        page.on("request", on_request)

        session._playwright = pw
        session._browser = browser
        session._context = context
        session._page = page

        await page.goto(session.site.home_url, wait_until="domcontentloaded", timeout=90000)
        cdp = await context.new_cdp_session(page)
        session._cdp = cdp

        async def on_frame(params: dict[str, Any]) -> None:
            data = params.get("data")
            session_id = params.get("sessionId")
            if data:
                session.publish_frame(str(data))
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:  # noqa: BLE001
                pass

        cdp.on("Page.screencastFrame", lambda params: asyncio.create_task(on_frame(params)))
        await cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 55,
                "maxWidth": VIEWPORT["width"],
                "maxHeight": VIEWPORT["height"],
                "everyNthFrame": 2,
            },
        )
        log.info("login session started proxy=%s id=%s", session.site.proxy_id, session.id)

    async def handle_input(self, session: LoginSession, msg: dict[str, Any]) -> None:
        page = session._page
        if page is None or session._closed:
            return
        typ = msg.get("type")
        async with session._lock:
            if typ == "click":
                x, y = float(msg.get("x") or 0), float(msg.get("y") or 0)
                await page.mouse.click(x, y)
            elif typ == "dblclick":
                x, y = float(msg.get("x") or 0), float(msg.get("y") or 0)
                await page.mouse.dblclick(x, y)
            elif typ == "move":
                x, y = float(msg.get("x") or 0), float(msg.get("y") or 0)
                await page.mouse.move(x, y)
            elif typ == "down":
                await page.mouse.down()
            elif typ == "up":
                await page.mouse.up()
            elif typ == "wheel":
                await page.mouse.wheel(float(msg.get("dx") or 0), float(msg.get("dy") or 0))
            elif typ == "keydown":
                key = str(msg.get("key") or "")
                if key:
                    await page.keyboard.press(key)
            elif typ == "type":
                text = str(msg.get("text") or "")
                if text:
                    await page.keyboard.type(text, delay=20)
            elif typ == "navigate":
                url = str(msg.get("url") or session.site.home_url)
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)

    async def save(self, session: LoginSession) -> dict[str, Any]:
        if session._context is None:
            raise RuntimeError("session not ready")
        page = session._page
        if page is not None:
            # soft ready check — do not hard-fail on selectors
            url = page.url
            if "/login" in url or "sign_in" in url:
                raise RuntimeError(f"仍在登录页（{url}），请先完成登录。")

        state = await session._context.storage_state()
        enriched, err = validate_and_enrich_state(
            session.site, state, bearer_token=session.bearer_token
        )
        if err or enriched is None:
            raise RuntimeError(err or "校验失败")

        path = session.site.storage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(enriched, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        log.info("wrote storage %s (%s bytes)", path, path.stat().st_size)

        reload_info = await self._try_reload(session.site)
        return {
            "ok": True,
            "proxy_id": session.site.proxy_id,
            "storage_path": str(path),
            "reload": reload_info,
        }

    async def _try_reload(self, site: LoginSite) -> dict[str, Any]:
        """通知对应 proxy 热加载 storage（不重启容器）。"""
        from catalog_client import PROXY_DOCKER_ROOTS

        base = PROXY_DOCKER_ROOTS.get(site.proxy_id) or f"http://{site.reload_base_env}:8000"
        api_key = (os.getenv(site.api_key_env) or "local-secret").strip()
        url = f"{base.rstrip('/')}/v1/admin/reload-storage"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(url, headers=headers)
            return {
                "attempted": True,
                "status_code": resp.status_code,
                "body": (resp.text or "")[:300],
                "ok": resp.status_code < 400,
            }
        except Exception as exc:  # noqa: BLE001
            return {"attempted": True, "ok": False, "error": str(exc)[:200]}


manager = LoginSessionManager()
