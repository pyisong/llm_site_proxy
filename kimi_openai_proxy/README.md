# Kimi OpenAI Proxy

OpenAI 兼容代理，对接 Kimi 网页登录态。实现方式参考 `deepseek_openai_proxy`：服务端启动一个 Playwright 浏览器，复用已登录 profile 或导出的 storage state，在真实 Kimi 页面里输入问题并读取回答。

支持两种后端：

| 模式 | 环境变量 | 说明 |
| --- | --- | --- |
| `browser` | `KIMI_BACKEND=browser` | 驱动 [https://www.kimi.com/](https://www.kimi.com/) 页面，无需 Kimi API Key |
| `official` | `KIMI_BACKEND=official` | 转发至 Moonshot OpenAI 兼容 API，需 `KIMI_API_KEY` |

Browser 模式优先使用持久浏览器 profile。Docker/远程部署时推荐先导出 Playwright storage state，再挂载到容器。

## 快速开始

### 本地 browser 模式

```bash
cd kimi_openai_proxy
python3 -m pip install -e .
python3 -m playwright install chromium

export KIMI_BACKEND=browser
export KIMI_PROXY_API_KEY=local-secret
export KIMI_BROWSER_PROFILE="$HOME/.kimi-openai-proxy-profile"

python3 -m uvicorn main:app --host 0.0.0.0 --port 18003
```

如果当前目录存在 `secrets/kimi_storage.json`，服务会自动优先使用该登录态，并默认以无头模式启动，不会弹出 Chromium。需要强制打开浏览器调试时可设置 `KIMI_BROWSER_HEADLESS=0`。

首次启动如果没有 storage state 或持久 profile 登录态，Chromium 会打开 `https://www.kimi.com/`。请在浏览器窗口里完成登录；看到 Kimi 输入框后即可调用。

### 调用示例

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "messages": [{"role": "user", "content": "用一句话介绍一下你自己"}]
  }'
```

OpenAI SDK：

```python
from openai import OpenAI

client = OpenAI(api_key="local-secret", base_url="http://127.0.0.1:18003/v1")

resp = client.chat.completions.create(
    model="kimi-chat-web",
    messages=[{"role": "user", "content": "hello"}],
    extra_body={"new_chat": True, "kimi_mode": "fast"},
)

print(resp.choices[0].message.content)
```

## Docker

### 1. 导出登录态

在有桌面的机器上运行：

```bash
cd kimi_openai_proxy
python3 -m pip install -e .
python3 -m playwright install chromium
python3 -m save_storage_state
```

浏览器打开后登录 [https://www.kimi.com/](https://www.kimi.com/)，确认能看到输入框，回到终端按 Enter。脚本会生成：

```text
secrets/kimi_storage.json
```

### 2. 启动容器

```bash
docker compose up -d --build
```

默认映射到宿主机 `18003`：

```bash
curl http://127.0.0.1:18003/health
```

## Browser 会话控制

默认每次请求都会点击「新建会话」或重新打开首页，避免多次 API 调用串在同一个网页上下文里。

| 参数 | 位置 | 示例 |
| --- | --- | --- |
| `new_chat` | 请求体 | `"new_chat": false` |
| `metadata.new_chat` | 请求体 | `"metadata": {"new_chat": true}` |
| `X-Kimi-New-Chat` | HTTP 头 | `X-Kimi-New-Chat: false` |
| `KIMI_NEW_CHAT_PER_REQUEST` | 环境变量 | 默认 `1`，即每次新建会话 |

优先级：请求体 `new_chat` -> 请求体 `metadata.new_chat` -> HTTP 头 `X-Kimi-New-Chat` -> 环境变量 `KIMI_NEW_CHAT_PER_REQUEST`。

布尔值支持：`true` / `false`、`1` / `0`、`yes` / `no`、`on` / `off`，大小写不敏感。

多轮复用示例：

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "new_chat": false,
    "session_id": "task-1",
    "messages": [{"role": "user", "content": "继续上一轮回答"}]
  }'
```

`session_id` 只用于浏览器会话切换和日志关联；当传入新的 `session_id` 时，服务会强制新建网页会话。

## Kimi 网页参数

Browser 模式会在发送消息前切换 Kimi 页面输入框上方的 **模型** 与 **思考强度**。参数优先级：请求体字段 -> `metadata` -> HTTP 头 -> 环境变量。

| 参数 | 可选值 | 默认 | 说明 |
| --- | --- | --- | --- |
| `kimi_mode` | 见下表 | `fast` | 预设：模型 + 思考强度 |
| `reasoning_effort` / `kimi_effort` | `standard` / `advanced` / `extreme` | 跟随预设 | 单独覆盖思考强度（亦支持 `low`/`high`/`max`） |
| `deep_thinking` | `true` / `false` | `false` | 兼容 DeepSeek；`true` 且预设为标准时抬升为「进阶」 |

页面实际选项：

| 模型 | 思考强度 |
| --- | --- |
| `快速`（帮助中心称 K2.6） | 标准 / 进阶 |
| `K3` | 标准 / 进阶 / 极致 |
| `K3 集群` | 标准 / 进阶 / 极致 |

`kimi_mode` 预设：

| 标准值 | 页面效果 | 常用别名 |
| --- | --- | --- |
| `fast` | 快速 · 标准 | `quick`、`k2.6`、`快速` |
| `thinking` | 快速 · 进阶 | `think`、`思考`、`K2.6 思考` |
| `k3` | K3 · 标准 | `agent`、`kimi-k3` |
| `k3_advanced` | K3 · 进阶 | `k3 进阶` |
| `k3_extreme` | K3 · 极致 | `k3_max`、`k3 极致` |
| `k3_cluster` | K3 集群 · 标准 | `agent_group`、`K3 集群` |
| `k3_cluster_advanced` / `k3_cluster_extreme` | K3 集群 · 进阶 / 极致 | — |

### curl 示例

**新建会话 + 快速（默认）：**

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "new_chat": true,
    "kimi_mode": "fast",
    "messages": [{"role": "user", "content": "用一句话介绍一下你自己"}]
  }'
```

**复用当前网页会话：**

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "new_chat": false,
    "messages": [{"role": "user", "content": "继续上一轮回答"}]
  }'
```

**快速 · 进阶思考：**

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "kimi_mode": "thinking",
    "messages": [{"role": "user", "content": "分析这个问题并给出结论"}]
  }'
```

**兼容 DeepSeek 的深度思考参数：**

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "deep_thinking": true,
    "messages": [{"role": "user", "content": "用思考模式回答"}]
  }'
```

**K3 / K3 集群：**

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "kimi_mode": "k3",
    "messages": [{"role": "user", "content": "帮我调研一个主题"}]
  }'
```

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "kimi_mode": "k3_cluster",
    "reasoning_effort": "max",
    "messages": [{"role": "user", "content": "批量整理这些材料"}]
  }'
```

**通过 HTTP 头控制：**

```bash
curl http://127.0.0.1:18003/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "X-Kimi-New-Chat: false" \
  -H "X-Kimi-Mode: thinking" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "kimi-chat-web",
    "messages": [{"role": "user", "content": "继续并深入分析"}]
  }'
```

### OpenAI SDK 示例

```python
from openai import OpenAI

client = OpenAI(api_key="local-secret", base_url="http://127.0.0.1:18003/v1")

# 新会话 + 快速
client.chat.completions.create(
    model="kimi-chat-web",
    messages=[{"role": "user", "content": "hello"}],
    extra_body={"new_chat": True, "kimi_mode": "fast"},
)

# 复用网页会话
client.chat.completions.create(
    model="kimi-chat-web",
    messages=[{"role": "user", "content": "继续"}],
    extra_body={"new_chat": False},
)

# 快速 · 进阶
client.chat.completions.create(
    model="kimi-chat-web",
    messages=[{"role": "user", "content": "深入分析"}],
    extra_body={"kimi_mode": "thinking"},
)

# K3 极致
client.chat.completions.create(
    model="kimi-chat-web",
    messages=[{"role": "user", "content": "复杂任务"}],
    extra_body={"kimi_mode": "k3_extreme"},
)

# 通过 metadata 传参
client.chat.completions.create(
    model="kimi-chat-web",
    messages=[{"role": "user", "content": "继续"}],
    extra_body={"metadata": {"new_chat": False, "kimi_mode": "k3_cluster"}},
)
```

## Official 模式

如果你有 Moonshot/Kimi 官方 API Key，可以使用转发模式：

```bash
export KIMI_BACKEND=official
export KIMI_API_KEY=sk-...
export KIMI_PROXY_API_KEY=local-secret
export KIMI_BASE_URL=https://api.moonshot.cn/v1

python3 -m uvicorn main:app --host 0.0.0.0 --port 18003
```

此模式会把 `/v1/chat/completions` 原样转发到 `${KIMI_BASE_URL}/chat/completions`。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `KIMI_BACKEND` | `browser` | `browser` 或 `official` |
| `KIMI_PROXY_API_KEY` | `local-secret` | 本地代理鉴权 key；设为空可关闭鉴权 |
| `KIMI_API_KEY` | 空 | official 模式的上游 API Key |
| `KIMI_BASE_URL` | `https://api.moonshot.cn/v1` | official 模式上游地址 |
| `KIMI_CHAT_URL` | `https://www.kimi.com/` | browser 模式聊天页 URL |
| `KIMI_BROWSER_PROFILE` | `./kimi-browser-profile` | 持久浏览器 profile |
| `KIMI_STORAGE_STATE_FILE` | 自动发现 `./secrets/kimi_storage.json` | Playwright storage state 文件 |
| `KIMI_BROWSER_HEADLESS` | 有 storage state 时为 `1`，否则 `0` | `1` 为无头模式；显式设置会覆盖默认值 |
| `KIMI_INPUT_SELECTOR` | `.chat-input-editor[contenteditable="true"]` | 输入框选择器 |
| `KIMI_ANSWER_SELECTOR` | `.chat-content-item-assistant .segment-content-box, .chat-content-item-assistant [class*='markdown'], .chat-content-item-assistant` | 回答区域候选选择器，默认只读取助手消息，避免误读用户消息 |
| `KIMI_NEW_CHAT_PER_REQUEST` | `1` | 默认每次请求新会话 |
| `KIMI_WEB_MODE` | `fast` | 默认 Kimi 网页模式 |
| `KIMI_DEEP_THINKING` | `0` | 默认是否抬升思考强度为「进阶」；`kimi_mode` / `reasoning_effort` 显式传参优先 |
| `KIMI_BROWSER_TIMEOUT` | `300` | 等待完整回答超时秒数 |
| `KIMI_BROWSER_START_TIMEOUT` | 同上 | 等待开始回答超时秒数 |
| `KIMI_BUSY_MAX_ATTEMPTS` | `5` | 遇到「人太多/有点累了」等软限流提示时的最大尝试次数 |
| `KIMI_BUSY_RETRY_WAIT_SECONDS` | `60` | 首次忙线重试前等待秒数 |
| `KIMI_BUSY_RETRY_BACKOFF` | `1.5` | 忙线重试等待指数退避倍率 |
| `KIMI_BUSY_RETRY_WAIT_MAX_SECONDS` | `180` | 单次忙线等待上限秒数 |
| `KIMI_LOG_LEVEL` | `INFO` | 日志级别 |
| `KIMI_LOG_MAX_CHARS` | `500` | 请求/响应日志截断长度 |

## 调试

确认当前端口跑的是这个服务：

```bash
curl http://127.0.0.1:18003/__debug/routes \
  -H "Authorization: Bearer local-secret"
```

常见问题：

| 现象 | 处理 |
| --- | --- |
| `404 Not Found` | 多半打到了旧进程或错端口，先看 `/__debug/routes` |
| 找不到 Chromium | 运行 `python3 -m playwright install chromium` |
| 找不到输入框 | Kimi 页面可能改版，打开浏览器确认 `.chat-input-editor` 是否还存在 |
| `未找到 Kimi 模式下拉控件` | 页面模式入口可能改版；新版会记录 warning 并沿用当前模式继续请求，避免直接 500 |
| 等不到回答 | 检查消息是否真的发出；必要时更新 `KIMI_ANSWER_SELECTOR` |
| 返回「人太多了 / 有点累了」 | 代理会自动等待并重试；仍失败则返回 503 `rate_limit_error`，可调大 `KIMI_BUSY_*` |
| Docker 无法登录 | 在有桌面的机器导出 `secrets/kimi_storage.json` 后挂载到容器 |
