# StepFun OpenAI Proxy

OpenAI 兼容代理，对接 StepFun 网页登录态。实现方式与 `deepseek_openai_proxy` / `kimi_openai_proxy` 一致：服务端启动 Playwright 浏览器，复用已登录 profile 或导出的 storage state，在真实 [https://chat.stepfun.com/chats/new](https://chat.stepfun.com/chats/new) 页面里输入问题并读取回答。

支持两种后端：

| 模式 | 环境变量 | 说明 |
| --- | --- | --- |
| `browser` | `STEPFUN_BACKEND=browser` | 驱动 StepFun 网页，无需 StepFun API Key |
| `official` | `STEPFUN_BACKEND=official` | 转发至可配置的 StepFun OpenAI 兼容 API，需 `STEPFUN_API_KEY` |

## 当前网页分析结果

在未发送测试消息的前提下，已确认页面结构：

| 元素 | 选择器 / 文本 |
| --- | --- |
| 页面 URL | `https://chat.stepfun.com/chats/new` |
| 标题 | `阶跃AI` |
| 输入框 | `textarea[placeholder*="任何问题"]:not([disabled])` |
| 发送按钮 | `button:has(.custom-icon-send-outline):not([disabled])` |
| 新话题 | `button:has-text("开启新话题")` |
| 模式按钮 | `搜索`、`快速`、`深入核查`、`知识库问答`、`图片创作` |
| 登录弹窗标志 | `欢迎来到阶跃AI`、`阅读并同意`、`下一步` |

如果页面显示登录弹窗，代理会返回明确的未登录错误，不会在遮罩层下尝试发送消息。

StepFun 页面在不同会话状态下会切换输入框 class / placeholder，因此浏览器层会在每次发送前重新探测输入框，而不是只复用启动时命中的 selector。复用网页会话时，回答 DOM 的顺序也可能不是“最新回答在最后”，代理会优先读取与上一轮不同的新回答，避免返回旧答案。

## 快速开始

```bash
cd stepfun_openai_proxy
python3 -m pip install -e .
python3 -m playwright install chromium

export STEPFUN_BACKEND=browser
export STEPFUN_PROXY_API_KEY=local-secret

python3 -m uvicorn main:app --host 0.0.0.0 --port 18004
```

当前仓库里的 `stepfun-browser-profile/` 已验证可用：服务会自动发现当前目录或模块目录下的 `stepfun-browser-profile/`，也会自动发现 `secrets/stepfun_storage.json`。命中任一登录态时，浏览器默认以 headless 模式启动，不会弹出可视浏览器窗口。

首次启动如果没有登录态，Chromium 会打开 StepFun 页面。请在浏览器窗口里完成登录；看到输入框且登录弹窗关闭后即可调用。需要可视化调试时，把 `STEPFUN_BROWSER_HEADLESS` 设为 `0`。

## 调用示例

```bash
curl http://127.0.0.1:18004/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stepfun-chat-web",
    "messages": [{"role": "user", "content": "用一句话介绍一下你自己"}]
  }'
```

OpenAI SDK：

```python
from openai import OpenAI

client = OpenAI(api_key="local-secret", base_url="http://127.0.0.1:18004/v1")

resp = client.chat.completions.create(
    model="stepfun-chat-web",
    messages=[{"role": "user", "content": "hello"}],
    extra_body={"new_chat": True, "stepfun_mode": "fast"},
)

print(resp.choices[0].message.content)
```

## Browser 会话和模式控制

Browser 模式下，代理维护一个长期运行的 Playwright 页面。每次请求可以选择新建网页话题，或复用当前网页话题。

| 行为 | 说明 |
| --- | --- | --- |
| `new_chat: true` | 点击「开启新话题」，或回退到重新打开 `STEPFUN_CHAT_URL`；适合独立任务 |
| `new_chat: false` | 复用当前网页话题；适合多轮追问（**深入核查除外**） |

> **深入核查（`deep_research`）不支持多轮**：即使请求带 `new_chat=false`，代理也会强制 `new_chat=true` 并新开话题。

`new_chat` 优先级：

| 优先级 | 来源 | 示例 |
| --- | --- | --- |
| 1 | 请求体 `new_chat` | `"new_chat": false` |
| 2 | 请求体 `metadata.new_chat` | `"metadata": {"new_chat": true}` |
| 3 | HTTP 头 `X-StepFun-New-Chat` | `X-StepFun-New-Chat: false` |
| 4 | 环境变量 `STEPFUN_NEW_CHAT_PER_REQUEST` | 默认 `1` |

布尔值支持：`true` / `false`、`1` / `0`、`yes` / `no`、`on` / `off`，大小写不敏感。

`stepfun_mode` 会在发送前点击网页上的模式按钮：

| 值 | 网页按钮 |
| --- | --- |
| `fast` / `quick` / `快速` | 快速 |
| `search` / `web` / `搜索` | 搜索 |
| `deep_research` / `research` / `深入核查` | 深入核查 |
| `knowledge` / `kb` / `知识库问答` | 知识库问答 |
| `image` / `图片创作` | 图片创作 |

也可通过 `metadata.stepfun_mode` 或 `X-StepFun-Mode` 传入。

### curl 示例

**新话题 + 快速模式：**

```bash
curl http://127.0.0.1:18004/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stepfun-chat-web",
    "new_chat": true,
    "stepfun_mode": "fast",
    "messages": [{"role": "user", "content": "只回复 A1"}]
  }'
```

**复用当前网页话题：**

```bash
curl http://127.0.0.1:18004/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stepfun-chat-web",
    "new_chat": false,
    "messages": [{"role": "user", "content": "只回复 B2"}]
  }'
```

**搜索模式：**

```bash
curl http://127.0.0.1:18004/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stepfun-chat-web",
    "stepfun_mode": "search",
    "messages": [{"role": "user", "content": "联网查一下这个问题"}]
  }'
```

**通过 HTTP 头控制：**

```bash
curl http://127.0.0.1:18004/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "X-StepFun-New-Chat: false" \
  -H "X-StepFun-Mode: fast" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "stepfun-chat-web",
    "messages": [{"role": "user", "content": "继续上一轮"}]
  }'
```

### OpenAI SDK 示例

```python
from openai import OpenAI

client = OpenAI(api_key="local-secret", base_url="http://127.0.0.1:18004/v1")

# 新话题
client.chat.completions.create(
    model="stepfun-chat-web",
    messages=[{"role": "user", "content": "hello"}],
    extra_body={"new_chat": True, "stepfun_mode": "fast"},
)

# 复用网页话题
client.chat.completions.create(
    model="stepfun-chat-web",
    messages=[{"role": "user", "content": "继续"}],
    extra_body={"new_chat": False},
)

# 通过 metadata 传参
client.chat.completions.create(
    model="stepfun-chat-web",
    messages=[{"role": "user", "content": "继续"}],
    extra_body={"metadata": {"new_chat": False, "stepfun_mode": "search"}},
)
```

## Docker

先在有桌面的机器导出登录态：

```bash
cd stepfun_openai_proxy
python3 -m pip install -e .
python3 -m playwright install chromium
python3 -m save_storage_state
```

登录后确认输入框可见且登录弹窗已关闭，回到终端按 Enter。脚本会生成 `secrets/stepfun_storage.json`。

启动容器：

```bash
docker compose up -d --build
```

默认映射宿主机 `18004`。

## Official 模式

```bash
export STEPFUN_BACKEND=official
export STEPFUN_API_KEY=sk-...
export STEPFUN_PROXY_API_KEY=local-secret
export STEPFUN_BASE_URL=https://api.stepfun.com/v1

python3 -m uvicorn main:app --host 0.0.0.0 --port 18004
```

此模式会把 `/v1/chat/completions` 原样转发到 `${STEPFUN_BASE_URL}/chat/completions`。`/v1/models` 当前列出官方 Chat 模型：`step-3.7-flash`（推荐）、`step-3.5-flash`、`step-3.5-flash-2603`、`step-1o-turbo-vision`。国内默认 `https://api.stepfun.com/v1`，国际站可用 `https://api.stepfun.ai/v1`（需匹配密钥所属区域）。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `STEPFUN_BACKEND` | `browser` | `browser` 或 `official` |
| `STEPFUN_PROXY_API_KEY` | `local-secret` | 本地代理鉴权 key；设为空可关闭鉴权 |
| `STEPFUN_API_KEY` | 空 | official 模式的上游 API Key |
| `STEPFUN_BASE_URL` | `https://api.stepfun.com/v1` | official 模式上游地址 |
| `STEPFUN_CHAT_URL` | `https://chat.stepfun.com/chats/new` | browser 模式聊天页 URL |
| `STEPFUN_BROWSER_PROFILE` | 自动发现 `stepfun-browser-profile` | 持久浏览器 profile；优先当前目录，其次模块目录 |
| `STEPFUN_STORAGE_STATE_FILE` | 自动发现 `secrets/stepfun_storage.json` | Playwright storage state 文件；优先当前目录，其次模块目录 |
| `STEPFUN_BROWSER_HEADLESS` | 自动 | 显式 `1` 为无头，`0` 为可视；未设置时，有 profile 或 storage state 则默认无头 |
| `STEPFUN_INPUT_SELECTOR` | `textarea[placeholder*="任何问题"]:not([disabled])` | 初始输入框选择器；发送前会自动 fallback 到其他 textarea selector |
| `STEPFUN_ANSWER_SELECTOR` | `div[data-message-id="markdown"] > div[class*="message-markdown_markdown"]:not([class*="reason-render-ext"])` | 回答区域主选择器（排除推理 markdown）。深入核查等场景若命中为 0，会自动走 fallback 选择器 / 页面刮取，并在超时重试前尝试 salvage 已渲染内容 |
| `STEPFUN_NEW_CHAT_PER_REQUEST` | `1` | 默认每次请求新话题 |
| `STEPFUN_WEB_MODE` | `fast` | 默认网页模式 |
| `STEPFUN_DEEP_THINKING` | `0` | 兼容字段；当前 StepFun 页面未发现独立深度思考开关 |
| `STEPFUN_BROWSER_TIMEOUT` | `1800` | 等待完整回答的**绝对上限**秒数（深入核查可很长） |
| `STEPFUN_BROWSER_START_TIMEOUT` | `300` | 等待「开始生成」超时秒数 |
| `STEPFUN_BROWSER_IDLE_TIMEOUT` | `120` | 无进度（无新字且未在生成）闲置超时；有流式进度会续期 |
| `STEPFUN_BROWSER_MAX_RETRIES` | `2` | 闲置/超时后自动重试次数 |
| `STEPFUN_LOG_LEVEL` | `INFO` | 日志级别 |
| `STEPFUN_LOG_MAX_CHARS` | `500` | 请求/响应日志截断长度 |

## 调试

```bash
curl http://127.0.0.1:18004/__debug/routes \
  -H "Authorization: Bearer local-secret"
```

常见问题：

| 现象 | 处理 |
| --- | --- |
| `404 Not Found` | 多半打到了旧进程或错端口，先看 `/__debug/routes` |
| 找不到 Chromium | 运行 `python3 -m playwright install chromium` |
| `StepFun 未登录` | 完成网页登录，或运行 `save_storage_state` 导出登录态 |
| 请求时弹出浏览器并卡住 | 多半是没有命中已登录 profile/storage；新版会从当前目录和模块目录自动发现，仍需可视调试时显式设 `STEPFUN_BROWSER_HEADLESS=0` |
| 找不到输入框 | 页面可能改版；当前会依次尝试 `Publisher_textarea__pMX9t`、`任何问题`、`探索更多`、`发送`、通用 `textarea` |
| `输入框在 Enter 后仍有内容，且未找到发送按钮` | 多发生在长文本或 StepFun 发送按钮 DOM 改版时；新版会优先点击发送按钮，失败才回退 Enter，并扩大 `send` 类名/aria/role 检测 |
| `new_chat=false` 返回旧答案 | 确认使用最新代码；代理会等待答案数量增加或文本变化，并排除上一轮文本 |
| 等不到回答 | StepFun 有时响应较慢；可增大 `STEPFUN_BROWSER_TIMEOUT` / `STEPFUN_BROWSER_START_TIMEOUT`，并检查消息是否真的发出 |

## 当前验证记录

本地 `stepfun-browser-profile/` 已完成验证：

```text
python3 -m pytest
53 passed

new_chat=true  -> content: A1
new_chat=false -> content: B2
```

验证时服务监听在 `http://127.0.0.1:18004`。当前版本未显式设置 `STEPFUN_BROWSER_PROFILE` / `STEPFUN_STORAGE_STATE_FILE` 时，也会自动复用项目内登录态。
