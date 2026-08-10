# Metaso (秘塔AI搜索) OpenAI Proxy 设计

日期：2026-08-10

## 目标

在 `llm_site_proxy` 中新增 `metaso_openai_proxy`，基于 **网页登录态**（不使用官方 Search API Key）对接 [https://metaso.cn/](https://metaso.cn/)，对外提供：

1. **OpenAI 兼容层**：`/v1/chat/completions`、`/v1/models`、`/health`
2. **秘塔原生能力层**：搜索 / 读网页 / 问答（流式）
3. 与现有代理一致的 Docker、catalog、console ingest、storage_state 导出流程

P0（本规格范围）仅覆盖官网核心检索与问答能力。幻灯片、视频生成、自定义技能、学点啥、导出 Word/PDF 等列为 P1，不在本规格实现。

## 已定约束

| 项 | 决定 |
| --- | --- |
| 上游鉴权 | 仅网页登录 Cookie（`uid`/`sid` 等），**禁止**官方 API Key 后端 |
| API 形态 | 双层：OpenAI 兼容 + 原生端点 |
| 交付范围 | 分期 B：P0 = 搜索 + 问答 + 读网页 |
| 流式 | 必须支持（OpenAI `stream=true` + 原生问答 SSE） |
| 实现路径 | 方案 3：Playwright 仅负责登录态导出/校验；运行时 httpx 调网页内部接口 |
| 宿主机端口 | `18006` |
| 本地鉴权 | `METASO_PROXY_API_KEY`（默认 `local-secret`；空则关闭） |
| capability | `llm` + `search`（catalog 需扩展 `search`） |

## 方案选择

**方案 3（已定）**：storage_state / Cookie → 网页内部 HTTP/SSE；Playwright 用于 `save_storage_state` 与可选保活探测。

未选：

- 方案 1 纯 DOM：结构化引用与吞吐差
- 方案 2 无 Playwright 纯手填 Cookie：与仓库其余 proxy 的导出/运维流程不一致

## 架构

```
Client
  │  Bearer METASO_PROXY_API_KEY
  ▼
metaso_openai_proxy (FastAPI)
  ├─ /v1/models, /health
  ├─ /v1/chat/completions          → OpenAI 映射层
  ├─ /v1/metaso/search             → 原生搜索
  ├─ /v1/metaso/reader             → 原生读网页
  └─ /v1/metaso/chat               → 原生问答（SSE/非流）
           │
           ▼
    MetasoWebClient (httpx)
      Cookie: uid, sid, …（来自 storage_state）
      伪装浏览器 Origin/Referer/UA
           │
           ▼
    metaso.cn 网页内部接口（如 searchV2 / reader / session）
```

登录态生命周期：

1. 本机 `python -m save_storage_state` → `secrets/metaso_storage.json`
2. Docker 只读挂载该文件
3. 启动时解析 Cookie；`/health` 与 console 保活可探测登录是否仍有效
4. 失效时返回可识别错误（`authentication_error` / console `login_required`）

## 组件与文件

新建目录 `metaso_openai_proxy/`，对齐 `kimi_openai_proxy` 骨架：

| 文件 | 职责 |
| --- | --- |
| `app.py` | FastAPI 路由、鉴权、OpenAI/原生响应封装、console ingest |
| `web_client.py` | 网页内部协议：建会话、搜索、问答流、读网页、Cookie 头 |
| `storage_state.py` | 从 Playwright storage 提取 Cookie（复用 kimi 模式） |
| `save_storage_state.py` | 导出登录态 CLI |
| `verify_storage_state.py` | 校验登录态 |
| `console_ingest.py` | 薄封装或复用 `common/console_ingest` |
| `main.py` / `logging_setup.py` | 入口与日志 |
| `Dockerfile` / `docker-compose.yml` / `pyproject.toml` | 部署 |
| `tests/` | 协议映射与路由单测（mock httpx，不打真网） |
| `README.md` | 用法、模型表、导出步骤 |
| `secrets/*.example` | Cookie/storage 示例（无真实密钥） |

根目录改动：

- `docker-compose.yml`：增加 `metaso-openai-proxy` 服务
- `proxy_catalog/registry.py`：注册服务与 `search` capability
- `README.md`：端口表增加 18006
- `common/console_ingest.py`：将 `/v1/metaso/search`、`/v1/metaso/reader`、`/v1/metaso/chat` 纳入应上报 POST 路径

## 模型与参数映射

### OpenAI `model` → 搜索强度 × 范围

| model id | 强度 | 范围 |
| --- | --- | --- |
| `metaso-concise` | 简洁 | 全网 |
| `metaso-detail` | 深入 | 全网（默认） |
| `metaso-research` | 研究 | 全网 |
| `metaso-concise-scholar` | 简洁 | 学术 |
| `metaso-detail-scholar` | 深入 | 学术 |
| `metaso-research-scholar` | 研究 | 学术 |
| `metaso-document` | 深入 | 文库 |
| `metaso-podcast` | 深入 | 播客 |
| `metaso-chat-web` | 深入 | 全网（别名，对齐其他 `*-chat-web`） |

额外参数（请求体 / `metadata` / HTTP 头，优先级同 Kimi）：

| 参数 | 说明 |
| --- | --- |
| `metaso_scope` | `webpage` / `scholar` / `document` / `podcast` |
| `metaso_mode` | `concise` / `detail` / `research` |
| `new_chat` | 默认 `true`；为 `false` 时复用 `session_id` 对应会话 |
| `session_id` | 可选；映射网页会话 id |

`messages` 取最后一条 user 文本作为查询；多轮时可将历史拼进 prompt 或复用网页会话（由 `new_chat`/`session_id` 控制）。P0 以「单轮查询 + 可选会话复用」为主。

### 原生端点

| Method | Path | Body 要点 | 响应 |
| --- | --- | --- | --- |
| POST | `/v1/metaso/search` | `q`, `scope`, `mode`, `size?` | JSON：条目列表 + 引用元数据 |
| POST | `/v1/metaso/reader` | `url`, `format?`=`markdown` | JSON：标题/正文 |
| POST | `/v1/metaso/chat` | `q`, `scope`, `mode`, `stream?` | 非流 JSON 或 SSE |

原生搜索与问答均使用同一网页登录态；实现时以抓包确认的内部路径为准（设计期参考：`/api/searchV2` 流式问答，以及站点 reader/search 相关同源 API）。**协议细节在实现任务中用一次真实抓包锁定，并集中写在 `web_client.py`，禁止散落魔法字符串。**

## OpenAI 响应约定

- 非流：标准 `chat.completion`；`content` 为最终回答 Markdown，文末追加「参考来源」列表以兼容通用客户端（不依赖扩展字段）。
- 流式：`text/event-stream`，chunk 形态对齐现有 proxy；结束发 `data: [DONE]`。
- 错误：`{"error":{"message","type"}}`，类型含 `authentication_error` / `rate_limit_error` / `invalid_request_error` / `api_error`。

## 登录态与安全

- 真实 `secrets/metaso_storage.json`、`.env` 不进 git；仅提供 `.example`
- 日志对 Cookie / Authorization 脱敏（复用现有 `_safe_headers` / `_safe_json` 模式）
- 本地 Bearer 与上游 Cookie 分离：客户端只持有 `METASO_PROXY_API_KEY`

## 集成

### Docker

- 服务名：`metaso-openai-proxy`
- 端口：`18006:8000`
- 环境变量：`METASO_*`、`CONSOLE_INGEST_URL`、`CONSOLE_PROXY_ID=metaso-openai-proxy`
- volume：`./metaso_openai_proxy/secrets:/run/secrets:ro`

### Catalog

```text
id: metaso-openai-proxy
public_port: 18006
capabilities: ("llm", "search")
endpoints: chat=/v1/chat/completions, models=/v1/models,
           search=/v1/metaso/search, reader=/v1/metaso/reader
```

`KNOWN_CAPABILITIES` 增加 `search`。`ProxyEndpoint` 增加可选字段 `search`、`reader`（及对应 `as_dict`），探测逻辑仍只用 health/models，不强制探活原生 POST。

### Console

- ingest 上报 chat + 原生 POST
- 保活：`/health`；可选轻量 `search`/`models` 探测；Cookie 失效标 `login_required`

## 错误处理

| 场景 | 行为 |
| --- | --- |
| 无 storage / Cookie 缺 `uid`/`sid` | 503/`authentication_error`，提示导出登录态 |
| 上游 401/登录页 | 同上，并建议 console 刷新 |
| 上游限流 | 503 `rate_limit_error`，可配置有限次退避重试 |
| 无效 model / 缺 `q` | 400 `invalid_request_error` |
| 上游改版/解析失败 | 502 `api_error`，日志保留截断响应片段 |

## 测试策略

- 单元：model 映射、Cookie 解析、OpenAI chunk 封装、原生请求校验（全部 mock `MetasoWebClient`）
- 不做默认 CI 真网集成测试
- README 提供手工 curl 验收清单（health / models / chat 流式 / search / reader）

## P1（明确不在本规格）

- 幻灯片、视频生成、文件上传知识库、自定义技能、学点啥
- 脑图/大纲独立端点、导出 Word/PDF
- 官方 Search API Key 后端
- 多账号 Cookie 池（P0 单账号 storage_state）

## 成功标准

1. `docker compose` 可启动，宿主机 `18006` 健康
2. 挂载有效登录态后，`/v1/chat/completions` 非流与流式均可返回秘塔检索问答
3. `/v1/metaso/search`、`/reader`、`/chat` 可用且与官网能力语义一致（范围×强度）
4. catalog 能发现该服务；console 能收到 ingest
5. 无登录态时错误类型明确，不 silent fail
6. 测试（mock）通过

## 风险

- 网页内部接口改版：集中在 `web_client.py`，用抓包回归
- 账号风控：仅个人登录态、合理超时与重试；文档注明非官方、勿滥用
- 学术/文库/播客若内部参数名与全网不同：实现时按抓包补映射表，对外 model id 保持稳定
