"""Metaso.cn webpage-internal client (Cookie session, no official API key)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Any
from urllib.parse import quote

import httpx

from models_map import SearchProfile

logger = logging.getLogger("metaso_openai_proxy.web_client")

HOME_URL = "https://metaso.cn/"
SESSION_URL = "https://metaso.cn/api/session"
SEARCH_V2_URL = "https://metaso.cn/api/searchV2"
MY_INFO_URL = "https://metaso.cn/api/my-info"
# Webpage reader endpoint used by the site; may evolve — keep centralized.
READER_URL = "https://metaso.cn/api/search/read"

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


def extract_meta_token(html: str) -> str | None:
    match = META_TOKEN_RE.search(html or "")
    return match.group(1) if match else None


def _strip_index_label(text: str) -> str:
    return re.sub(r"\[\d+\]", "", text or "")


def parse_search_sse_lines(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Normalize Metaso SSE lines into internal events."""
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
        if "TOO_MANY_REQUESTS" in data:
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
            events.append(
                {
                    "type": "error",
                    "code": payload.get("code"),
                    "msg": payload.get("msg") or payload.get("message") or str(payload),
                }
            )
        else:
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
        elif etype == "error":
            errors.append(f"[{event.get('code')}]{event.get('msg')}")
    content = "".join(texts)
    if errors and not content:
        content = "\n".join(errors)
    return {
        "content": format_answer_with_citations(content, citations),
        "raw_content": content,
        "citations": citations,
        "errors": errors,
    }


class MetasoWebClient:
    def __init__(
        self,
        *,
        cookie_header: str,
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not cookie_header.strip():
            raise MetasoAuthError("缺少 Cookie，请导出 metaso_storage.json")
        self._cookie = cookie_header.strip()
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        self._meta_token: str | None = None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
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
        conv_id = session_id
        if new_chat or not conv_id:
            conv_id = await self.create_session(question, profile)
        assert conv_id

        token = await self._meta()
        params = {
            "sessionId": conv_id,
            "question": question,
            "lang": "zh",
            "mode": profile.mode,
            "url": f"https://metaso.cn/search/{conv_id}?newSearch=true&q={quote(question)}",
            "enableMix": "true",
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
                        if event.get("type") == "error" and event.get("code") == "TOO_MANY_REQUESTS":
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
        events: list[dict[str, Any]] = []
        final_session = session_id
        async for event in self.chat_stream(
            q, profile, session_id=session_id, new_chat=new_chat
        ):
            if event.get("type") == "done" and event.get("session_id"):
                final_session = str(event["session_id"])
            events.append(event)
        result = aggregate_chat_events(events)
        result["session_id"] = final_session
        return result

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
