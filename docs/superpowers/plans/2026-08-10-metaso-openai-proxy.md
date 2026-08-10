# Metaso OpenAI Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `metaso_openai_proxy`：网页登录态对接秘塔 AI 搜索，对外提供 OpenAI 兼容聊天 + 原生 search/reader/chat（含流式）。

**Architecture:** Playwright 仅导出/校验 `secrets/metaso_storage.json`；运行时 `MetasoWebClient`（httpx）用 Cookie 调用 `metaso.cn` 网页内部接口；`app.py` 暴露双层 API，并接入 catalog / compose / console ingest。

**Tech Stack:** Python 3.11+、FastAPI、httpx、Playwright 1.60、uvicorn、pytest

## Global Constraints

- 禁止官方 Search API Key 后端；仅网页 Cookie（uid/sid 等）
- P0 仅：搜索 / 问答 / 读网页；不做幻灯片、视频、技能等 P1
- 端口 `18006`；本地鉴权 `METASO_PROXY_API_KEY`（默认 `local-secret`）
- 流式必须支持；OpenAI 回答文末附「参考来源」
- 协议常量集中在 `web_client.py`；日志脱敏 Cookie/Authorization
- 真实 secrets 不进 git；测试一律 mock 上游

## File Structure

| Path | Responsibility |
| --- | --- |
| `metaso_openai_proxy/app.py` | FastAPI：鉴权、路由、OpenAI/原生响应、ingest |
| `metaso_openai_proxy/web_client.py` | Cookie 会话、网页内部 search/chat/reader、SSE 解析 |
| `metaso_openai_proxy/models_map.py` | model id ↔ scope/mode 映射 |
| `metaso_openai_proxy/storage_state.py` | 加载 storage、提取 Cookie 头、登录态校验 |
| `metaso_openai_proxy/save_storage_state.py` | 导出登录态 CLI |
| `metaso_openai_proxy/verify_storage_state.py` | 校验 CLI |
| `metaso_openai_proxy/console_ingest.py` | 复用 common ingest 或本地薄封装 |
| `metaso_openai_proxy/logging_setup.py` | uvicorn 日志配置 |
| `metaso_openai_proxy/main.py` | 入口 |
| `metaso_openai_proxy/pyproject.toml` | 包与依赖 |
| `metaso_openai_proxy/Dockerfile` | 镜像（对齐 kimi） |
| `metaso_openai_proxy/docker-compose.yml` | 单服务 compose |
| `metaso_openai_proxy/README.md` | 使用说明 |
| `metaso_openai_proxy/secrets/*.example` | 示例 |
| `metaso_openai_proxy/tests/test_models_map.py` | 模型映射单测 |
| `metaso_openai_proxy/tests/test_storage_state.py` | Cookie 解析单测 |
| `metaso_openai_proxy/tests/test_web_client.py` | 协议解析单测（mock） |
| `metaso_openai_proxy/tests/test_proxy.py` | 路由单测（ASGI） |
| `docker-compose.yml` | 注册服务 |
| `proxy_catalog/registry.py` | 注册 + `search` capability |
| `common/console_ingest.py` | 原生 POST 路径纳入 ingest |
| `README.md` | 端口表 |

---

### Task 1: models_map + storage_state（可测基础库）

**Files:**
- Create: `metaso_openai_proxy/models_map.py`
- Create: `metaso_openai_proxy/storage_state.py`
- Create: `metaso_openai_proxy/tests/test_models_map.py`
- Create: `metaso_openai_proxy/tests/test_storage_state.py`
- Create: `metaso_openai_proxy/pyproject.toml`

**Interfaces:**
- Produces:
  - `resolve_search_profile(model: str | None, *, scope: str | None = None, mode: str | None = None) -> SearchProfile` where `SearchProfile` 有 `mode: str`（`concise|detail|research`）与 `scope: str`（`webpage|scholar|document|podcast`）
  - `MODEL_IDS: list[str]`
  - `load_storage_state(path) -> dict`
  - `extract_cookie_header(state: dict) -> str`
  - `extract_uid_sid(state: dict) -> tuple[str | None, str | None]`
  - `storage_state_login_issue(state: dict) -> str | None`

- [ ] **Step 1: 写失败测试（models_map）**

```python
# metaso_openai_proxy/tests/test_models_map.py
from models_map import resolve_search_profile, MODEL_IDS

def test_default_detail_webpage():
    p = resolve_search_profile("metaso-detail")
    assert p.mode == "detail" and p.scope == "webpage"

def test_scholar_and_overrides():
    p = resolve_search_profile("metaso-concise-scholar")
    assert p.mode == "concise" and p.scope == "scholar"
    p2 = resolve_search_profile("metaso-detail", scope="document", mode="research")
    assert p2.mode == "research" and p2.scope == "document"

def test_model_catalog_contains_aliases():
    assert "metaso-chat-web" in MODEL_IDS
    assert "metaso-podcast" in MODEL_IDS
```

- [ ] **Step 2: 运行确认失败**

Run: `cd metaso_openai_proxy && python3 -m pytest tests/test_models_map.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 models_map.py**

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class SearchProfile:
    mode: str  # concise|detail|research
    scope: str  # webpage|scholar|document|podcast

_MODEL_TABLE: dict[str, SearchProfile] = {
    "metaso-concise": SearchProfile("concise", "webpage"),
    "metaso-detail": SearchProfile("detail", "webpage"),
    "metaso-research": SearchProfile("research", "webpage"),
    "metaso-concise-scholar": SearchProfile("concise", "scholar"),
    "metaso-detail-scholar": SearchProfile("detail", "scholar"),
    "metaso-research-scholar": SearchProfile("research", "scholar"),
    "metaso-document": SearchProfile("detail", "document"),
    "metaso-podcast": SearchProfile("detail", "podcast"),
    "metaso-chat-web": SearchProfile("detail", "webpage"),
}

MODEL_IDS = list(_MODEL_TABLE.keys())

def resolve_search_profile(
    model: str | None,
    *,
    scope: str | None = None,
    mode: str | None = None,
) -> SearchProfile:
    base = _MODEL_TABLE.get((model or "").strip() or "metaso-detail", _MODEL_TABLE["metaso-detail"])
    return SearchProfile(mode=mode or base.mode, scope=scope or base.scope)
```

- [ ] **Step 4: 写 storage_state 测试并实现**

测试要点：从 cookies 列表提取 `uid`/`sid`；拼 `Cookie` 头；缺 cookie 时 `storage_state_login_issue` 非空。

```python
def extract_uid_sid(state: dict) -> tuple[str | None, str | None]:
    uid = sid = None
    for c in state.get("cookies") or []:
        if not isinstance(c, dict):
            continue
        name, value = c.get("name"), str(c.get("value") or "").strip()
        if name == "uid" and value:
            uid = value
        elif name == "sid" and value:
            sid = value
    return uid, sid

def extract_cookie_header(state: dict) -> str:
    parts = []
    for c in state.get("cookies") or []:
        if isinstance(c, dict) and c.get("name") and c.get("value") is not None:
            parts.append(f"{c['name']}={c['value']}")
    return "; ".join(parts)

def storage_state_login_issue(state: dict) -> str | None:
    uid, sid = extract_uid_sid(state)
    if uid and sid:
        return None
    return "metaso_storage.json 缺少 uid/sid Cookie，请重新导出登录态。"
```

- [ ] **Step 5: 补 pyproject.toml 最小依赖并跑通测试**

```toml
[project]
name = "metaso-openai-proxy"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.110",
  "httpx>=0.27",
  "playwright==1.60.0",
  "uvicorn>=0.29",
]
```

Run: `cd metaso_openai_proxy && python3 -m pip install -e ".[dev]" -q && python3 -m pytest tests/test_models_map.py tests/test_storage_state.py -v`
Expected: PASS

- [ ] **Step 6: Commit**（仅当用户要求提交时执行；默认跳过）

---

### Task 2: MetasoWebClient（协议层，mock 测试）

**Files:**
- Create: `metaso_openai_proxy/web_client.py`
- Create: `metaso_openai_proxy/tests/test_web_client.py`

**Interfaces:**
- Consumes: `SearchProfile`, Cookie header
- Produces:
  - `class MetasoWebClient`
  - `async def ensure_ready(self) -> None`
  - `async def search(self, q: str, profile: SearchProfile, *, size: int = 10) -> dict`
  - `async def reader(self, url: str, *, format: str = "markdown") -> dict`
  - `async def chat_stream(self, q: str, profile: SearchProfile, *, session_id: str | None = None, new_chat: bool = True) -> AsyncIterator[dict]`  # 解析后的事件：`{"type":"text","text":...}` / `{"type":"citation",...}` / `{"type":"done"}`
  - `async def chat(self, ...) -> dict`  # 聚合 stream → `{content, citations}`

协议实现步骤（实现时抓包锁定，下列为初始目标接口，可按抓包结果改动但保持对外接口不变）：

1. 用 Cookie 访问 `https://metaso.cn/`，必要时解析页面内 meta token / CSRF
2. 创建或复用会话 id
3. 调用搜索/问答流（参考社区实现：`/api/searchV2` SSE）；把 SSE 事件规范化
4. Reader：同源网页读取接口；若仅能通过问答路径拿到正文，则 reader 用内部「抓取 URL」请求并返回 markdown

- [ ] **Step 1: 写 SSE 解析单测（不打真网）**

```python
from web_client import parse_search_sse_lines, format_answer_with_citations

def test_parse_text_and_done():
    events = list(parse_search_sse_lines([
        'data: {"type":"text","text":"你好"}',
        "data: [DONE]",
    ]))
    assert events[0]["type"] == "text"
    assert events[-1]["type"] == "done"

def test_format_citations_appendix():
    body = format_answer_with_citations("答案", [{"title": "T", "link": "https://a.example"}])
    assert "参考来源" in body and "https://a.example" in body
```

- [ ] **Step 2: 实现解析与聚合辅助函数，测试 PASS**

- [ ] **Step 3: 实现 `MetasoWebClient` 方法骨架 + httpx mock 测试**

用 `httpx.MockTransport` 或 `respx`（若不想加依赖则手写 ASGI/transport）：至少覆盖 `chat()` 在 mock SSE 下返回完整 content。

- [ ] **Step 4: 对照真实站点抓包，把 URL/字段名写进常量并更新 mock fixtures**

手工步骤：浏览器登录 metaso → 发起一次全网深入搜索 → 从 Network 复制 `searchV2`（或现行等价）请求 URL、method、关键 query/body、响应事件类型。把结果固化为 `tests/fixtures/search_sse_sample.txt`。

- [ ] **Step 5: 跑 `pytest tests/test_web_client.py -v` → PASS**

---

### Task 3: FastAPI app（OpenAI + 原生路由）

**Files:**
- Create: `metaso_openai_proxy/app.py`
- Create: `metaso_openai_proxy/logging_setup.py`
- Create: `metaso_openai_proxy/main.py`
- Create: `metaso_openai_proxy/console_ingest.py`
- Create: `metaso_openai_proxy/tests/test_proxy.py`

**Interfaces:**
- Consumes: `MetasoWebClient`, `resolve_search_profile`, storage helpers
- Produces: `create_app() -> FastAPI` 路由：
  - `GET /health`
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - `POST /v1/metaso/search`
  - `POST /v1/metaso/reader`
  - `POST /v1/metaso/chat`
  - `GET /__debug/routes`

- [ ] **Step 1: 写路由测试（TestClient + 假 client）**

```python
from fastapi.testclient import TestClient
from app import create_app

class FakeClient:
    async def ensure_ready(self): ...
    async def chat(self, q, profile, **kw):
        return {"content": f"echo:{q}", "citations": []}
    async def chat_stream(self, q, profile, **kw):
        yield {"type": "text", "text": "hi"}
        yield {"type": "done"}
    async def search(self, q, profile, **kw):
        return {"webpages": [{"title": "t", "link": "https://x"}]}
    async def reader(self, url, **kw):
        return {"url": url, "markdown": "# ok"}

def test_chat_completions_non_stream(monkeypatch):
    app = create_app(web_client=FakeClient(), local_api_key="secret", skip_storage=True)
    c = TestClient(app)
    r = c.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret"},
        json={"model": "metaso-detail", "messages": [{"role": "user", "content": "你好"}], "stream": False},
    )
    assert r.status_code == 200
    assert "echo:你好" in r.json()["choices"][0]["message"]["content"]
```

同类覆盖：401、原生 search/reader、stream 含 `data: [DONE]`。

- [ ] **Step 2: 实现 `create_app`（对齐 kimi 的鉴权/错误/日志风格，但后端只挂 web_client）**

关键行为：
- 从 messages 提取最后一条 user 文本
- `stream=true` 时把 `chat_stream` 事件转为 OpenAI SSE chunk
- 无登录态：`503 authentication_error`
- 调用结束后 fire-and-forget console ingest

- [ ] **Step 3: `pytest tests/test_proxy.py -v` → PASS**

---

### Task 4: 登录态工具 + README 示例

**Files:**
- Create: `metaso_openai_proxy/save_storage_state.py`
- Create: `metaso_openai_proxy/verify_storage_state.py`
- Create: `metaso_openai_proxy/secrets/metaso_storage.json.example`
- Create: `metaso_openai_proxy/README.md`

- [ ] **Step 1: 实现 save/verify**（打开 `https://metaso.cn/`，用户登录后保存 storage；verify 检查 uid/sid）
- [ ] **Step 2: README 写清导出、curl 示例（chat/search/reader）、模型表、环境变量**
- [ ] **Step 3: example storage 仅占位 cookies 结构，无真实值**

---

### Task 5: Docker + 根 compose + catalog + ingest 路径

**Files:**
- Create: `metaso_openai_proxy/Dockerfile`
- Create: `metaso_openai_proxy/docker-compose.yml`
- Modify: `docker-compose.yml`（根）
- Modify: `proxy_catalog/registry.py`
- Modify: `common/console_ingest.py`
- Modify: `README.md`（根端口表）
- Modify: `proxy_catalog/tests/test_catalog.py`（若有硬编码服务数/capabilities）

- [ ] **Step 1: Dockerfile / 子 compose 对齐 kimi（端口环境 `METASO_*`）**
- [ ] **Step 2: 根 compose 增加服务 `18006`**
- [ ] **Step 3: registry 增加服务与 `KNOWN_CAPABILITIES += search`，Endpoint 增加 `search`/`reader`**
- [ ] **Step 4: `common/console_ingest.py` 的 `_EXACT_POST_PATHS` 加入三条 `/v1/metaso/*`**
- [ ] **Step 5: 更新 catalog 测试并 `pytest proxy_catalog/tests common/tests metaso_openai_proxy/tests -q` → PASS**

---

### Task 6: 手工冒烟（有真实登录态时）

- [ ] **Step 1: 导出 `secrets/metaso_storage.json`**
- [ ] **Step 2: 本地 `uvicorn` 或 compose 启动**
- [ ] **Step 3: 验收**

```bash
curl -s localhost:18006/health
curl -s localhost:18006/v1/models -H "Authorization: Bearer local-secret"
curl -s localhost:18006/v1/chat/completions -H "Authorization: Bearer local-secret" -H "Content-Type: application/json" -d '{"model":"metaso-detail","messages":[{"role":"user","content":"用一句话介绍秘塔AI"}],"stream":false}'
curl -s localhost:18006/v1/metaso/search -H "Authorization: Bearer local-secret" -H "Content-Type: application/json" -d '{"q":"秘塔AI","scope":"webpage","mode":"concise"}'
```

无真实登录态时本 Task 可记录为 blocked，但不阻塞代码合并前的 mock 测试成功标准。

---

## Spec Coverage Check

| Spec 要求 | Task |
| --- | --- |
| 网页登录态、无官方 API | 2, 4 |
| OpenAI + 原生双层 | 3 |
| P0 search/reader/chat + 流式 | 2, 3 |
| 模型映射表 | 1 |
| 文末参考来源 | 2 |
| Docker 18006 / catalog search / ingest | 5 |
| 测试 mock | 1–3, 5 |
| P1 不做 | （无任务） |
