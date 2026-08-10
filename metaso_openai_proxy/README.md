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
    "model": "metaso-detail",
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

| model | 强度 | 范围 |
| --- | --- | --- |
| `metaso-concise` | 简洁 | 全网 |
| `metaso-detail` | 深入 | 全网（默认） |
| `metaso-research` | 研究 | 全网 |
| `metaso-concise-scholar` / `detail-scholar` / `research-scholar` | 对应强度 | 学术 |
| `metaso-document` | 深入 | 文库 |
| `metaso-podcast` | 深入 | 播客 |
| `metaso-chat-web` | 深入 | 全网（别名） |

也可用请求体 `metaso_scope` / `metaso_mode` 或头 `X-Metaso-Scope` / `X-Metaso-Mode` 覆盖。

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
| `METASO_LOG_LEVEL` | `INFO` | 日志级别 |

## 说明

- 回答文末会追加「参考来源」列表（兼容通用 OpenAI 客户端）。
- 网页接口可能改版；协议集中在 `web_client.py`。
- P0 不含幻灯片 / 视频生成 / 自定义技能等，见设计文档。
