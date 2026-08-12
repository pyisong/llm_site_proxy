# Metaso OpenAI Proxy

基于 [秘塔 AI 搜索](https://metaso.cn/) **网页登录态** 的代理（不使用官方 Search API Key）。

- OpenAI 兼容：`/v1/chat/completions`、`/v1/models`
- 原生能力：`/v1/metaso/search`、`/v1/metaso/reader`、`/v1/metaso/chat`（支持流式）

运行时用 Cookie 调用网页内部接口（`/api/session` + `/api/searchV2` SSE）；Playwright 仅用于导出登录态。

## 导出登录态

```bash
cd metaso_openai_proxy
python3 -m pip install -e .
python3 -m playwright install chromium
python3 -m save_storage_state
```

浏览器登录 https://metaso.cn/ 后回到终端按 Enter，生成：

```text
secrets/metaso_storage.json
```

校验：

```bash
python3 -m verify_storage_state
```

## 本地启动

```bash
export METASO_PROXY_API_KEY=local-secret
export METASO_STORAGE_STATE_FILE=./secrets/metaso_storage.json
python3 -m uvicorn main:app --host 0.0.0.0 --port 18006
```

## 调用示例

```bash
curl http://127.0.0.1:18006/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "metaso-chat-web",
    "metaso_mode": "detail",
    "metaso_scope": "webpage",
    "messages": [{"role": "user", "content": "用一句话介绍秘塔AI"}],
    "stream": false
  }'
```

原生搜索：

```bash
curl http://127.0.0.1:18006/v1/metaso/search \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{"q":"秘塔AI","scope":"webpage","mode":"concise","size":5}'
```

读网页：

```bash
curl http://127.0.0.1:18006/v1/metaso/reader \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com","format":"markdown"}'
```

## 模型

`/v1/models` **只列出** `metaso-chat-web`（与 DeepSeek 一样单入口）；模式与范围用请求体 / 头覆盖。

| 参数 | 取值 |
| --- | --- |
| `metaso_mode` | `chat` / `fast` / `concise` / `detail` / `research` / `nosearch` |
| `metaso_scope` | `webpage` / `scholar` / `document` / `podcast` |

兼容：仍可直接传旧 model id（`metaso-detail`、`metaso-concise-scholar` 等）解析为 mode×scope；也可用头 `X-Metaso-Scope` / `X-Metaso-Mode`。

## Docker

```bash
docker compose up -d --build
```

默认宿主机端口 **18006**。根目录 `docker-compose.yml` 已注册同名服务。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `METASO_PROXY_API_KEY` | `local-secret` | 本地鉴权；空则关闭 |
| `METASO_STORAGE_STATE_FILE` | 自动发现 `secrets/metaso_storage.json` | Playwright storage |
| `METASO_TIMEOUT` | `300` | 上游超时秒 |
| `METASO_NEW_CHAT_PER_REQUEST` | `1` | 默认每请求新建会话 |
| `METASO_RATE_LIMIT_RETRIES` | `0` | 上游 `TOO_MANY_REQUESTS` 重试次数（硬限流时建议保持 0） |
| `METASO_HTTP_PROXY` | （空） | 出站代理；机房 IP 易被限流。也可用 `HTTPS_PROXY` / `HTTP_PROXY` |
| `METASO_CHAT_MODEL` | `fast_thinking` | 对话接口上游 model（对齐官网抓包） |
| `METASO_LOG_LEVEL` | `INFO` | 日志级别 |

## 说明

- 回答文末会追加「参考来源」列表（兼容通用 OpenAI 客户端）。
- 网页接口可能改版；协议集中在 `web_client.py`。
- P0 不含幻灯片 / 视频生成 / 自定义技能等，见设计文档。
