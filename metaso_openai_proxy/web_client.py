"""Metaso.cn webpage-internal client (Cookie session, no official API key)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx

from models_map import SearchProfile

logger = logging.getLogger("metaso_openai_proxy.web_client")

# 上游 SSE 限流默认不重试（硬封锁时重试只会多建 session）；需要时可设 METASO_RATE_LIMIT_RETRIES
RATE_LIMIT_RETRIES = max(0, int(os.getenv("METASO_RATE_LIMIT_RETRIES", "0")))
RATE_LIMIT_BACKOFF_SEC = float(os.getenv("METASO_RATE_LIMIT_BACKOFF_SEC", "2.0"))
# 上一请求结束后到下一请求开始的最小间隔（整段串行，避免并行/密打）
MIN_REQUEST_INTERVAL_SEC = max(0.0, float(os.getenv("METASO_MIN_REQUEST_INTERVAL_SEC", "15.0")))
# 触发 TOO_MANY_REQUESTS 后熔断冷却，期间不再打上游（重试只会加重封锁）
RATE_LIMIT_COOLDOWN_SEC = max(0.0, float(os.getenv("METASO_RATE_LIMIT_COOLDOWN_SEC", "300")))

HOME_URL = "https://metaso.cn/"
SESSION_URL = "https://metaso.cn/api/session"
SEARCH_V2_URL = "https://metaso.cn/api/searchV2"
# 官网「对话」走此接口（非 searchV2）；默认 model=fast_thinking
SEARCH_CHAT_URL = "https://metaso.cn/api/search/chat"
MY_INFO_URL = "https://metaso.cn/api/my-info"
# Webpage reader endpoint used by the site; may evolve — keep centralized.
READER_URL = "https://metaso.cn/api/search/read"
# 网页 chat 模型：fast_thinking | fast | …（对齐官网抓包）
CHAT_UPSTREAM_MODEL = (os.getenv("METASO_CHAT_MODEL") or "fast_thinking").strip()

META_TOKEN_RE = re.compile(r'<meta\s+id="meta-token"\s+content="([^"]*)"', re.I)

FAKE_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://metaso.cn",
    "Referer": "https://metaso.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class MetasoAuthError(RuntimeError):
    pass


class MetasoUpstreamError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MetasoRateLimitError(MetasoUpstreamError):
    pass


def _is_rate_limit_signal(code: Any = None, msg: Any = None, data: str = "") -> bool:
    """上游限流可能是字面 TOO_MANY_REQUESTS，也可能是 code=429 / 文案 Too Many Requests。"""
    blob = f"{code or ''} {msg or ''} {data or ''}".upper()
    if "TOO_MANY_REQUESTS" in blob or "TOO MANY REQUESTS" in blob:
        return True
    if code in (429, "429") or str(code).strip() == "429":
        return True
    return False


def extract_meta_token(html: str) -> str | None:
    match = META_TOKEN_RE.search(html or "")
    return match.group(1) if match else None


def _strip_index_label(text: str) -> str:
    return re.sub(r"\[\d+\]", "", text or "")


def _strip_ai_generated_tag(text: str) -> str:
    # 检索回答偶发附带 HTML 角标，OpenAI content 里去掉
    return re.sub(
        r"\s*<span[^>]*>\s*\[AI生成\]\s*</span>\s*",
        "",
        text or "",
        flags=re.I,
    ).rstrip()


def parse_search_sse_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Normalize Metaso searchV2 SSE lines into internal events."""
    events: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("data:"):
            data = line[5:].strip()
        else:
            data = line
        if data == "[DONE]":
            events.append({"type": "done"})
            continue
        if _is_rate_limit_signal(data=data):
            events.append({"type": "error", "code": "TOO_MANY_REQUESTS", "msg": data})
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        etype = str(payload.get("type") or "")
        if etype == "append-text":
            text = _strip_index_label(str(payload.get("text") or ""))
            if text:
                events.append({"type": "text", "text": text})
        elif etype == "error":
            code = payload.get("code")
            msg = payload.get("msg") or payload.get("message") or str(payload)
            if _is_rate_limit_signal(code=code, msg=msg):
                code = "TOO_MANY_REQUESTS"
            events.append({"type": "error", "code": code, "msg": msg})
        else:
            citation = _citation_from_payload(payload)
            if citation:
                events.append({"type": "citation", **citation})
            else:
                events.append({"type": "meta", "raw": payload})
    return events


def parse_chat_sse_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Normalize Metaso ``/api/search/chat`` SSE (OpenAI-like deltas) into events."""
    events: list[dict[str, Any]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("data:"):
            data = line[5:].strip()
        else:
            data = line
        if data == "[DONE]":
            events.append({"type": "done"})
            continue
        if _is_rate_limit_signal(data=data):
            events.append({"type": "error", "code": "TOO_MANY_REQUESTS", "msg": data})
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        etype = str(payload.get("type") or "")
        if etype == "conversation_init":
            cid = None
            if isinstance(payload.get("data"), dict):
                cid = payload["data"].get("id")
            if cid:
                events.append({"type": "session", "session_id": str(cid)})
            else:
                events.append({"type": "meta", "raw": payload})
            continue
        if etype in {"heartbeat", "user_message_init"}:
            events.append({"type": "meta", "raw": payload})
            continue
        if etype == "response_message_init":
            mid = None
            if isinstance(payload.get("data"), dict):
                mid = payload["data"].get("id")
            if mid:
                events.append({"type": "response_message", "message_id": str(mid)})
            else:
                events.append({"type": "meta", "raw": payload})
            continue
        if etype == "error":
            code = payload.get("code")
            msg = payload.get("msg") or payload.get("message") or str(payload)
            if _is_rate_limit_signal(code=code, msg=msg):
                code = "TOO_MANY_REQUESTS"
            events.append({"type": "error", "code": code, "msg": msg})
            continue
        # OpenAI-style: choices[].delta.content
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    events.append({"type": "text", "text": content})
                # citations may appear as delta.citations; skip empty stubs
                cites = delta.get("citations")
                if isinstance(cites, list):
                    for c in cites:
                        if isinstance(c, dict):
                            citation = _citation_from_payload(c)
                            if citation:
                                events.append({"type": "citation", **citation})
            continue
        citation = _citation_from_payload(payload)
        if citation:
            events.append({"type": "citation", **citation})
        else:
            events.append({"type": "meta", "raw": payload})
    return events


def _citation_from_payload(payload: dict[str, Any]) -> dict[str, str] | None:
    # Be tolerant of site event shapes (title/link/url/snippet…).
    title = payload.get("title") or payload.get("name")
    link = payload.get("link") or payload.get("url") or payload.get("href")
    if not link and isinstance(payload.get("data"), dict):
        data = payload["data"]
        title = title or data.get("title") or data.get("name")
        link = data.get("link") or data.get("url") or data.get("href")
    if not link:
        return None
    snippet = payload.get("snippet") or payload.get("summary") or ""
    if isinstance(payload.get("data"), dict):
        snippet = snippet or payload["data"].get("snippet") or payload["data"].get("summary") or ""
    return {
        "title": str(title or link),
        "link": str(link),
        "snippet": str(snippet or ""),
    }


def format_answer_with_citations(content: str, citations: list[dict[str, Any]]) -> str:
    body = (content or "").rstrip()
    if not citations:
        return body
    lines = [body, "", "参考来源:"]
    for i, item in enumerate(citations, 1):
        title = str(item.get("title") or item.get("link") or f"来源{i}")
        link = str(item.get("link") or "")
        if link:
            lines.append(f"{i}. {title} — {link}")
        else:
            lines.append(f"{i}. {title}")
    return "\n".join(lines).rstrip()


def aggregate_chat_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    texts: list[str] = []
    citations: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    errors: list[str] = []
    session_id: str | None = None
    for event in events:
        etype = event.get("type")
        if etype == "text":
            texts.append(str(event.get("text") or ""))
        elif etype == "citation":
            link = str(event.get("link") or "")
            if link and link not in seen_links:
                seen_links.add(link)
                citations.append(
                    {
                        "title": event.get("title") or link,
                        "link": link,
                        "snippet": event.get("snippet") or "",
                    }
                )
        elif etype == "session" and event.get("session_id"):
            session_id = str(event["session_id"])
        elif etype == "done" and event.get("session_id"):
            session_id = str(event["session_id"])
        elif etype == "error":
            errors.append(f"[{event.get('code')}]{event.get('msg')}")
    content = _strip_ai_generated_tag("".join(texts))
    if errors and not content:
        content = "\n".join(errors)
    out = {
        "content": format_answer_with_citations(content, citations),
        "raw_content": content,
        "citations": citations,
        "errors": errors,
    }
    if session_id:
        out["session_id"] = session_id
    return out


class MetasoWebClient:
    def __init__(
        self,
        *,
        cookie_header: str,
        timeout: float = 300.0,
        proxy: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not cookie_header.strip():
            raise MetasoAuthError("缺少 Cookie，请导出 metaso_storage.json")
        self._cookie = cookie_header.strip()
        self._timeout = timeout
        # 机房出口 IP 常被秘塔限流（SSE: TOO_MANY_REQUESTS）；可走住宅/梯子出口
        self._proxy = (proxy or "").strip() or None
        self._client = client
        self._owns_client = client is None
        self._meta_token: str | None = None
        # 客户端逻辑 session_id（任务级）→ 秘塔上游 conversationId
        self._session_aliases: dict[str, str] = {}
        # 客户端逻辑 session_id → 当前叶子回复 messageId（用于 parentId 追问，而非同轮再生）
        self._session_leaves: dict[str, str] = {}
        self._min_interval = MIN_REQUEST_INTERVAL_SEC
        self._last_request_at = 0.0
        self._throttle_lock = asyncio.Lock()
        self._rate_limited_until = 0.0
        self._cooldown_sec = RATE_LIMIT_COOLDOWN_SEC

    def _trip_rate_limit(self, *, reason: str = "") -> None:
        if self._cooldown_sec <= 0:
            return
        self._rate_limited_until = time.monotonic() + self._cooldown_sec
        logger.warning(
            "metaso circuit OPEN for %.0fs%s",
            self._cooldown_sec,
            f" ({reason})" if reason else "",
        )

    def _rate_limit_remaining(self) -> float:
        return max(0.0, self._rate_limited_until - time.monotonic())

    def _remember_session(self, client_sid: str | None, real_sid: str | None) -> None:
        cid = (client_sid or "").strip()
        rid = (real_sid or "").strip()
        if cid and rid:
            self._session_aliases[cid] = rid

    def _remember_leaf(self, client_sid: str | None, leaf_id: str | None) -> None:
        cid = (client_sid or "").strip()
        lid = (leaf_id or "").strip()
        if cid and lid:
            self._session_leaves[cid] = lid

    def _resolve_upstream_session(
        self,
        session_id: str | None,
        *,
        new_chat: bool,
    ) -> tuple[str | None, str | None, str | None]:
        """返回 (client_sid, upstream_sid, leaf_id)。"""
        client_sid = (session_id or "").strip() or None
        if new_chat:
            if client_sid:
                self._session_aliases.pop(client_sid, None)
                self._session_leaves.pop(client_sid, None)
            return client_sid, None, None
        if not client_sid:
            return None, None, None
        return (
            client_sid,
            self._session_aliases.get(client_sid),
            self._session_leaves.get(client_sid),
        )

    @asynccontextmanager
    async def _request_slot(self):
        """整段串行占用：熔断冷却 / 间隔等待后独占上游，结束后释放。"""
        async with self._throttle_lock:
            remain = self._rate_limit_remaining()
            if remain > 0:
                raise MetasoRateLimitError(
                    f"秘塔限流冷却中，还需 {remain:.0f}s（熔断中不再打上游）",
                    status_code=429,
                )
            if self._min_interval > 0 and self._last_request_at > 0:
                wait = self._min_interval - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    logger.info("metaso throttle: sleep %.1fs before next upstream request", wait)
                    await asyncio.sleep(wait)
            try:
                yield
            except MetasoRateLimitError as exc:
                self._trip_rate_limit(reason=str(exc)[:120])
                raise
            finally:
                self._last_request_at = time.monotonic()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            kwargs: dict[str, Any] = {
                "timeout": self._timeout,
                "follow_redirects": True,
            }
            if self._proxy:
                kwargs["proxy"] = self._proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    def _headers(self, *, token: str | None = None, accept: str | None = None) -> dict[str, str]:
        headers = dict(FAKE_HEADERS)
        headers["Cookie"] = self._cookie
        if accept:
            headers["Accept"] = accept
        if token:
            headers["Token"] = token
        headers["Is-Mini-Webview"] = "0"
        return headers

    async def ensure_ready(self) -> None:
        client = await self._http()
        resp = await client.get(MY_INFO_URL, headers=self._headers(accept="application/json"))
        if resp.status_code in {401, 403}:
            raise MetasoAuthError("登录态失效，请重新导出 secrets/metaso_storage.json")
        if resp.status_code >= 400:
            # my-info 可能改版；退化为拉首页拿 meta-token
            await self._refresh_meta_token()
            return
        try:
            data = resp.json()
        except Exception:
            data = None
        if isinstance(data, dict) and data.get("err") and "login" in str(data.get("err")).lower():
            raise MetasoAuthError("登录态失效，请重新导出 secrets/metaso_storage.json")
        await self._refresh_meta_token()

    async def _refresh_meta_token(self) -> str:
        client = await self._http()
        resp = await client.get(
            HOME_URL,
            headers=self._headers(
                accept="text/html,application/xhtml+xml",
            )
            | {
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        if resp.status_code >= 400:
            raise MetasoUpstreamError(f"打开首页失败: HTTP {resp.status_code}", status_code=resp.status_code)
        token = extract_meta_token(resp.text)
        if not token:
            raise MetasoAuthError("未找到 meta-token，登录态可能无效")
        self._meta_token = token
        return token

    async def _meta(self) -> str:
        if self._meta_token:
            return self._meta_token
        return await self._refresh_meta_token()

    async def create_session(self, question: str, profile: SearchProfile) -> str:
        client = await self._http()
        token = await self._meta()
        body = {
            "question": question,
            "mode": profile.mode,
            "engineType": profile.engine_type,
            "scholarSearchDomain": "all",
        }
        resp = await client.post(
            SESSION_URL,
            headers=self._headers(token=token, accept="application/json"),
            json=body,
        )
        if resp.status_code in {401, 403}:
            raise MetasoAuthError("创建会话失败：登录态无效")
        if resp.status_code == 429 or "TOO_MANY_REQUESTS" in resp.text:
            raise MetasoRateLimitError("秘塔限流 TOO_MANY_REQUESTS", status_code=429)
        if resp.status_code >= 400:
            raise MetasoUpstreamError(
                f"创建会话失败: HTTP {resp.status_code} {resp.text[:300]}",
                status_code=resp.status_code,
            )
        try:
            data = resp.json()
        except Exception as exc:
            raise MetasoUpstreamError(f"创建会话响应非 JSON: {resp.text[:300]}") from exc
        # Shapes seen: {"data":{"id":...}} or {"id":...}
        conv_id = None
        if isinstance(data, dict):
            if isinstance(data.get("data"), dict):
                conv_id = data["data"].get("id") or data["data"].get("sessionId")
            conv_id = conv_id or data.get("id") or data.get("sessionId")
        if not conv_id:
            raise MetasoUpstreamError(f"创建会话未返回 id: {json.dumps(data, ensure_ascii=False)[:300]}")
        return str(conv_id)

    async def chat_stream(
        self,
        q: str,
        profile: SearchProfile,
        *,
        session_id: str | None = None,
        new_chat: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        question = (q or "").strip()
        if not question:
            raise ValueError("q 不能为空")
        async with self._request_slot():
            # 官网对话页用 POST /api/search/chat；searchV2+mode=chat 仍是检索百科
            if profile.mode == "chat":
                async for event in self._chat_api_stream(
                    question, profile, session_id=session_id, new_chat=new_chat
                ):
                    yield event
                return
            async for event in self._search_v2_stream(
                question, profile, session_id=session_id, new_chat=new_chat
            ):
                yield event

    async def _chat_api_stream(
        self,
        question: str,
        profile: SearchProfile,
        *,
        session_id: str | None = None,
        new_chat: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        token = await self._meta()
        upstream_model = CHAT_UPSTREAM_MODEL or "fast_thinking"
        client_sid, conv_id, leaf_id = self._resolve_upstream_session(
            session_id, new_chat=new_chat
        )
        reuse = bool(conv_id)
        temp_conv = conv_id or f"temp-{uuid.uuid4()}"
        # chat 档案用 mode=chat；勿写死 detail，否则官网 UI 会变成「深度研究」且易出现 N/M 再生
        ui_mode = (profile.mode or "chat").strip() or "chat"
        logger.info(
            "metaso.chat new_chat=%s client_sid=%s upstream_sid=%s leaf=%s conv=%s reuse=%s mode=%s",
            new_chat,
            client_sid or "-",
            conv_id or "-",
            leaf_id or "-",
            temp_conv if reuse else f"new:{temp_conv[:20]}",
            reuse,
            ui_mode,
        )
        message: dict[str, Any] = {
            "id": f"temp-{uuid.uuid4()}",
            "key": f"temp-{uuid.uuid4()}",
            "conversationId": temp_conv,
            "role": "user",
            "content": question,
            "markdownContent": question,
            "engineType": profile.engine_type,
            "filter": "all",
            "contentType": 0,
            "outputHtml": False,
            "mode": ui_mode,
            "model": upstream_model,
            "outputStyle": "正常",
        }
        # 追问必须挂 parentId=上一轮回答 messageId，才会在上条回答后继续提问；
        # 只带 conversationId 会被官网当成同轮再生（出现 18/20 这种翻页）。
        if reuse and leaf_id:
            message["parentId"] = leaf_id
        body: dict[str, Any] = {
            "model": upstream_model,
            "stream": True,
            "messages": [message],
            "engineType": profile.engine_type,
            "mode": ui_mode,
            "filter": "all",
            "outputHtml": False,
            "outputStyle": "正常",
            "darkMode": False,
            "outputLanguage": "中文",
            "htmlNoDisplayEnable": True,
            "metaso-pc": "pc",
            "token": token,
        }
        # 续聊必须在 body 根级带 conversationId/id，才不会新开历史记录。
        if reuse and conv_id:
            body["conversationId"] = conv_id
            body["id"] = conv_id
            if leaf_id:
                body["currentLeafId"] = leaf_id
        client = await self._http()
        headers = self._headers(token=token, accept="text/event-stream")
        headers["Content-Type"] = "application/json"
        final_sid = conv_id
        final_leaf = leaf_id
        async with client.stream(
            "POST",
            SEARCH_CHAT_URL,
            headers=headers,
            json=body,
        ) as resp:
            if resp.status_code in {401, 403}:
                raise MetasoAuthError("对话流失败：登录态无效")
            if resp.status_code == 429:
                raise MetasoRateLimitError("秘塔限流", status_code=429)
            if resp.status_code >= 400:
                err_body = (await resp.aread()).decode("utf-8", errors="replace")
                if "TOO_MANY_REQUESTS" in err_body:
                    raise MetasoRateLimitError(err_body[:200], status_code=resp.status_code)
                raise MetasoUpstreamError(
                    f"search/chat 失败: HTTP {resp.status_code} {err_body[:300]}",
                    status_code=resp.status_code,
                )
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    for event in parse_chat_sse_lines([line]):
                        if event.get("type") == "error" and _is_rate_limit_signal(
                            code=event.get("code"), msg=event.get("msg")
                        ):
                            raise MetasoRateLimitError(
                                str(event.get("msg") or "TOO_MANY_REQUESTS")
                            )
                        if event.get("type") == "session" and event.get("session_id"):
                            if not reuse:
                                final_sid = str(event["session_id"])
                        if event.get("type") == "response_message" and event.get("message_id"):
                            final_leaf = str(event["message_id"])
                        yield event
            if buffer.strip():
                for event in parse_chat_sse_lines([buffer]):
                    if event.get("type") == "session" and event.get("session_id"):
                        if not reuse:
                            final_sid = str(event["session_id"])
                    if event.get("type") == "response_message" and event.get("message_id"):
                        final_leaf = str(event["message_id"])
                    yield event
        keep_sid = conv_id if reuse else final_sid
        self._remember_session(client_sid, keep_sid)
        self._remember_leaf(client_sid, final_leaf)
        if client_sid and keep_sid:
            logger.info(
                "metaso.chat.mapped client_sid=%s -> upstream=%s leaf=%s reuse=%s",
                client_sid,
                keep_sid,
                final_leaf or "-",
                reuse,
            )
        yield {"type": "done", "session_id": keep_sid or client_sid}

    async def _search_v2_stream(
        self,
        question: str,
        profile: SearchProfile,
        *,
        session_id: str | None = None,
        new_chat: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        client_sid, conv_id, _leaf_id = self._resolve_upstream_session(
            session_id, new_chat=new_chat
        )
        if new_chat or not conv_id:
            conv_id = await self.create_session(question, profile)
            self._remember_session(client_sid, conv_id)
        assert conv_id

        token = await self._meta()
        # 检索向才 enableMix；fast/nosearch 关闭混合
        enable_mix = profile.mode not in {"fast", "nosearch"}
        params = {
            "sessionId": conv_id,
            "question": question,
            "lang": "zh",
            "mode": profile.mode,
            "url": f"https://metaso.cn/search/{conv_id}?newSearch=true&q={quote(question)}",
            "enableMix": "true" if enable_mix else "false",
            "scholarSearchDomain": "all",
            "expectedCurrentSessionSearchCount": "1",
            "is-mini-webview": "0",
            "token": token,
        }
        if profile.engine_type:
            params["engineType"] = profile.engine_type

        client = await self._http()
        async with client.stream(
            "GET",
            SEARCH_V2_URL,
            params=params,
            headers=self._headers(accept="text/event-stream"),
        ) as resp:
            if resp.status_code in {401, 403}:
                raise MetasoAuthError("搜索流失败：登录态无效")
            if resp.status_code == 429:
                raise MetasoRateLimitError("秘塔限流", status_code=429)
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                if "TOO_MANY_REQUESTS" in body:
                    raise MetasoRateLimitError(body[:200], status_code=resp.status_code)
                raise MetasoUpstreamError(
                    f"searchV2 失败: HTTP {resp.status_code} {body[:300]}",
                    status_code=resp.status_code,
                )

            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    for event in parse_search_sse_lines([line]):
                        if event.get("type") == "error" and _is_rate_limit_signal(
                            code=event.get("code"), msg=event.get("msg")
                        ):
                            raise MetasoRateLimitError(str(event.get("msg") or "TOO_MANY_REQUESTS"))
                        yield event
            if buffer.strip():
                for event in parse_search_sse_lines([buffer]):
                    yield event
            yield {"type": "done", "session_id": conv_id}

    async def chat(
        self,
        q: str,
        profile: SearchProfile,
        *,
        session_id: str | None = None,
        new_chat: bool = True,
    ) -> dict[str, Any]:
        attempts = RATE_LIMIT_RETRIES + 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                events: list[dict[str, Any]] = []
                final_session = session_id
                use_new = True if attempt > 0 else new_chat
                use_sid = None if attempt > 0 else session_id
                async for event in self.chat_stream(
                    q, profile, session_id=use_sid, new_chat=use_new
                ):
                    if event.get("type") in {"done", "session"} and event.get("session_id"):
                        final_session = str(event["session_id"])
                    events.append(event)
                result = aggregate_chat_events(events)
                result["session_id"] = result.get("session_id") or final_session
                return result
            except MetasoRateLimitError as exc:
                last_exc = exc
                if attempt + 1 >= attempts:
                    break
                delay = RATE_LIMIT_BACKOFF_SEC * (2**attempt)
                logger.warning(
                    "metaso rate-limited, retry %s/%s after %.1fs",
                    attempt + 1,
                    RATE_LIMIT_RETRIES,
                    delay,
                )
                self._meta_token = None
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def search(
        self,
        q: str,
        profile: SearchProfile,
        *,
        size: int = 10,
    ) -> dict[str, Any]:
        result = await self.chat(q, profile, new_chat=True)
        webpages = result.get("citations") or []
        if size > 0:
            webpages = webpages[:size]
        return {
            "q": q,
            "mode": profile.mode,
            "scope": profile.scope,
            "webpages": webpages,
            "answer": result.get("raw_content") or "",
            "session_id": result.get("session_id"),
        }

    async def reader(self, url: str, *, format: str = "markdown") -> dict[str, Any]:
        target = (url or "").strip()
        if not target:
            raise ValueError("url 不能为空")
        client = await self._http()
        token = await self._meta()
        # Prefer dedicated reader if available; fall back to chat extraction.
        resp = await client.post(
            READER_URL,
            headers=self._headers(token=token, accept="application/json"),
            json={"url": target, "format": format},
        )
        if resp.status_code < 400:
            try:
                data = resp.json()
            except Exception:
                data = {"markdown": resp.text}
            if isinstance(data, dict):
                markdown = (
                    data.get("markdown")
                    or data.get("content")
                    or data.get("text")
                    or ""
                )
                if isinstance(data.get("data"), dict):
                    nested = data["data"]
                    markdown = (
                        markdown
                        or nested.get("markdown")
                        or nested.get("content")
                        or nested.get("text")
                        or ""
                    )
                if markdown:
                    return {
                        "url": target,
                        "format": format,
                        "title": data.get("title") or (data.get("data") or {}).get("title") if isinstance(data.get("data"), dict) else data.get("title"),
                        "markdown": str(markdown),
                        "source": "reader",
                    }

        profile = SearchProfile(mode="detail", scope="webpage")
        prompt = (
            f"请抓取并完整提取以下网页正文，仅输出 Markdown，不要寒暄：\n{target}"
        )
        chat = await self.chat(prompt, profile, new_chat=True)
        return {
            "url": target,
            "format": format,
            "title": None,
            "markdown": chat.get("raw_content") or chat.get("content") or "",
            "source": "chat_fallback",
            "session_id": chat.get("session_id"),
        }


def new_request_id() -> str:
    return f"metaso-{uuid.uuid4().hex[:24]}"
