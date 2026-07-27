# DeepSeek OpenAI Proxy

OpenAI 兼容代理，对接 DeepSeek。支持两种后端：

| 模式 | 环境变量 | 说明 |
| --- | --- | --- |
| `browser` | `DEEPSEEK_BACKEND=browser` | 用 Playwright 驱动已登录的 [https://chat.deepseek.com/](https://chat.deepseek.com/)，无需 DeepSeek API Key |
| `official` | `DEEPSEEK_BACKEND=official` | 转发至 DeepSeek 官方 OpenAI 兼容 API，需 `DEEPSEEK_API_KEY` |

Browser 模式通过真实浏览器页面完成聊天，由网页自身处理 PoW、WAF、验证码等风控，无需手动导出 token。

---

## 快速开始（Docker，推荐）

> **重要：** DeepSeek 网页会话依赖 **cookie + localStorage**，仅粘贴 Copy as cURL 的 cookie **通常会跳转到登录页**。请用下面的 **storage state** 方式。

### 1. 本地导出登录态（只需一次）

在有图形界面的机器上（Mac / Windows / Linux 桌面）：

```bash
cd deepseek_openai_proxy
python3 -m pip install -e .
python3 -m playwright install chromium

python3 -m save_storage_state
```

浏览器弹出后登录 [https://chat.deepseek.com/](https://chat.deepseek.com/)，**在聊天页发一条测试消息并收到回复后**，回到终端按 **Enter**。

会生成 `secrets/deepseek_storage.json`（含 cookie、localStorage 与 **Bearer 令牌**）。导出后可本地校验：

```bash
python3 -m verify_storage_state secrets/deepseek_storage.json
```

若输出 `bearer=无`，说明导出时未捕获到 authorization，Docker 仍会跳转登录页。

**手动补令牌（备选）：** 在 DevTools → Network 里找到 `completion` 请求，复制请求头 `authorization` 的值，然后：

```bash
python3 -m inject_bearer_token secrets/deepseek_storage.json 'Bearer eyJ...'
python3 -m verify_storage_state secrets/deepseek_storage.json
```

### 2. 上传到服务器并启动

```bash
# 将 secrets/deepseek_storage.json 复制到远程项目 secrets/ 目录
docker compose up -d --build
```

默认监听 **18002** 端口。

### 3. 验证

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat-web",
    "messages": [{"role": "user", "content": "用一句话介绍一下你自己"}]
  }'
```

默认每次请求会开启**新对话**（见 [Browser 会话控制](#browser-会话控制)）。

### 备选：仅 curl cookie（可能失效）

若仍想尝试 curl，可保存到 `secrets/deepseek_curl.txt`（见 `secrets/deepseek_curl.txt.example`）。
若请求报错「DeepSeek 未登录」或超时，请改用上面的 `save_storage_state` 方式。

```bash
python3 -m curl_cookies secrets/deepseek_curl.txt -o secrets/deepseek_cookies.json
```

---

## Browser 会话控制

Browser 模式下，代理维护一个长期运行的 Playwright 页面。每次 API 调用前，可决定是**新建网页会话**还是**复用当前会话**。

| 行为 | 说明 |
| --- | --- |
| 新建会话（`new_chat: true`） | 点击「新对话 / New chat」，或回退到重新打开聊天首页；各次 API 调用互不影响 |
| 复用会话（`new_chat: false`） | 在同一网页对话中继续发送，适合多轮追问 |

实现方式：发送消息前点击「新对话 / New chat」按钮；若按钮未找到，则回退到重新打开聊天首页。

### 优先级（前者覆盖后者）

| 优先级 | 来源 | 示例 |
| --- | --- | --- |
| 1 | 请求体 `new_chat` | `"new_chat": false` |
| 2 | 请求体 `metadata.new_chat` | `"metadata": {"new_chat": true}` |
| 3 | HTTP 头 `X-DeepSeek-New-Chat` | `X-DeepSeek-New-Chat: false` |
| 4 | 环境变量 `DEEPSEEK_NEW_CHAT_PER_REQUEST` | 默认 `1`（新建） |

布尔值支持：`true` / `false`、`1` / `0`、`yes` / `no`、`on` / `off`（大小写不敏感）。

> **注意：** `new_chat` 仅对 `browser` 后端生效；`official` 模式由客户端自行维护 `messages` 历史，忽略此参数。

### DeepSeek 网页模式控制

Browser 模式还支持在发送消息前切换 DeepSeek 网页上的模式按钮：

| 参数 | 可选值 | 默认 | 说明 |
| --- | --- | --- | --- |
| `deepseek_mode` | `fast` / `expert` / `vision` | `fast` | 对应页面的「快速模式 / 专家模式 / 识图模式」 |
| `deep_thinking` | `true` / `false` | `false` | 是否开启页面上的「深度思考」 |

`deepseek_mode` 也接受别名：`quick`、`normal`、`快速模式` → `fast`；`pro`、`专家模式` → `expert`；`image`、`识图模式` → `vision`。

优先级同 `new_chat`：

| 优先级 | 来源 | 示例 |
| --- | --- | --- |
| 1 | 请求体字段 | `"deepseek_mode": "expert"`、`"deep_thinking": true` |
| 2 | 请求体 `metadata` | `"metadata": {"deepseek_mode": "vision", "deep_thinking": true}` |
| 3 | HTTP 头 | `X-DeepSeek-Mode: expert`、`X-DeepSeek-Deep-Thinking: true` |
| 4 | 环境变量 | `DEEPSEEK_WEB_MODE=fast`、`DEEPSEEK_DEEP_THINKING=0` |

### curl 示例

**新建会话（默认）：**

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat-web",
    "new_chat": true,
    "messages": [{"role": "user", "content": "第一句"}]
  }'
```

**复用当前网页会话（多轮对话）：**

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat-web",
    "new_chat": false,
    "messages": [{"role": "user", "content": "继续上一句"}]
  }'
```

**快速模式 + 深度思考：**

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat-web",
    "deep_thinking": true,
    "messages": [{"role": "user", "content": "用快速模式深入分析这个问题"}]
  }'
```


**专家模式 + 深度思考：**

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat-web",
    "deepseek_mode": "expert",
    "deep_thinking": true,
    "messages": [{"role": "user", "content": "用专家模式深入分析这个问题"}]
  }'
```

**识图模式 + 深度思考：**

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat-web",
    "deepseek_mode": "vision",
    "deep_thinking": true,
    "messages": [{"role": "user", "content": "切换到识图模式并开启深度思考"}]
  }'
```

> 当前代理已支持切换到「识图模式」，但尚未实现自动上传图片文件；OpenAI 多模态 `image_url` 内容仍会按原有逻辑转成文本占位。

**通过 HTTP 头控制：**

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "X-DeepSeek-New-Chat: false" \
  -H "X-DeepSeek-Mode: expert" \
  -H "X-DeepSeek-Deep-Thinking: true" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat-web",
    "messages": [{"role": "user", "content": "追问"}]
  }'
```

### OpenAI SDK 示例

```python
from openai import OpenAI

client = OpenAI(api_key="local-secret", base_url="http://127.0.0.1:18002/v1")

# 新会话（也可省略，与服务端默认一致）
client.chat.completions.create(
    model="deepseek-chat-web",
    messages=[{"role": "user", "content": "hello"}],
    extra_body={"new_chat": True},
)

# 复用当前浏览器会话
client.chat.completions.create(
    model="deepseek-chat-web",
    messages=[{"role": "user", "content": "继续"}],
    extra_body={"new_chat": False},
)

# 或通过 metadata（部分 SDK 更推荐此写法）
client.chat.completions.create(
    model="deepseek-chat-web",
    messages=[{"role": "user", "content": "继续"}],
    extra_body={"metadata": {"new_chat": False}},
)

# 专家模式 + 深度思考
client.chat.completions.create(
    model="deepseek-chat-web",
    messages=[{"role": "user", "content": "深入分析一下"}],
    extra_body={"deepseek_mode": "expert", "deep_thinking": True},
)
```

### 典型用法

| 场景 | 建议 |
| --- | --- |
| 每次独立任务（文章生成、单次问答） | 不传或 `"new_chat": true` |
| 同一话题多轮追问 | 首次 `true`，后续 `false` |
| 全局默认复用会话 | 设 `DEEPSEEK_NEW_CHAT_PER_REQUEST=0`，需要时单次传 `new_chat: true` |

---

## 本地开发

```bash
cd deepseek_openai_proxy
python3 -m pip install -e .

# 方式 A：storage state（推荐，与 Docker 相同）
export DEEPSEEK_STORAGE_STATE_FILE="./secrets/deepseek_storage.json"

# 方式 B：curl cookie（可能不足，见快速开始说明）
export DEEPSEEK_CURL_FILE="./secrets/deepseek_curl.txt"

# 方式 C：本地浏览器 profile（需图形界面首次登录）
export DEEPSEEK_BROWSER_PROFILE="$HOME/.deepseek-browser-profile"
export DEEPSEEK_BROWSER_HEADLESS=0   # 首次登录设为 0，之后可改 1

export DEEPSEEK_BACKEND=browser
export DEEPSEEK_PROXY_API_KEY="local-secret"
python3 -m uvicorn main:app --host 0.0.0.0 --port 18000
```

首次使用 profile 方式时，Chromium 窗口会弹出，在 `https://chat.deepseek.com/` 完成登录；登录态保存在 `DEEPSEEK_BROWSER_PROFILE` 目录。

---

## Official API 模式

```bash
export DEEPSEEK_API_KEY="sk-..."
export DEEPSEEK_PROXY_API_KEY="local-secret"
export DEEPSEEK_BACKEND=official
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{"model": "deepseek-chat", "messages": [{"role": "user", "content": "hello"}]}'
```

---

## Docker 配置说明

`docker-compose.yml` 默认配置：

```yaml
ports:
  - "18002:8000"
environment:
  DEEPSEEK_BACKEND: browser
  DEEPSEEK_PROXY_API_KEY: local-secret
  DEEPSEEK_BROWSER_PROFILE: /data/profile
  DEEPSEEK_BROWSER_HEADLESS: "1"
  DEEPSEEK_STORAGE_STATE_FILE: /run/secrets/deepseek_storage.json
  DEEPSEEK_CURL_FILE: /run/secrets/deepseek_curl.txt
  DEEPSEEK_NEW_CHAT_PER_REQUEST: "1"   # 默认每次新建会话；设为 0 则默认复用
  DEEPSEEK_WEB_MODE: fast              # fast / expert / vision
  DEEPSEEK_DEEP_THINKING: "0"          # 默认不开启深度思考；设为 1 则默认开启
volumes:
  - ./secrets:/run/secrets:ro
```

### 国内镜像加速

构建时使用微软中国区端点 + 清华 pip 源 + 阿里云 apt 源：

```yaml
build:
  args:
    USE_CN_MIRROR: "1"
    BASE_IMAGE: mcr.azure.cn/playwright/python:v1.60.0-noble
    PIP_INDEX_URL: https://pypi.tuna.tsinghua.edu.cn/simple
    APT_MIRROR: mirrors.aliyun.com
```

海外环境可设 `USE_CN_MIRROR=0`，`BASE_IMAGE` 改为 `mcr.microsoft.com/playwright/python:v1.60.0-noble`。

> **版本对齐：** Docker 镜像 tag 必须与 `pyproject.toml` 中 `playwright==1.60.0` 一致，否则浏览器无法启动。

### 登录方式对比

| 方式 | 配置 | 适用场景 |
| --- | --- | --- |
| **storage state（推荐）** | `DEEPSEEK_STORAGE_STATE_FILE` | Docker / 远程部署，一次导出长期使用 |
| curl 粘贴 | `DEEPSEEK_CURL_FILE` | 仅 cookie，常会跳转登录页 |
| Cookie JSON | `DEEPSEEK_COOKIES_FILE` | 已有 Playwright 格式 cookie 文件 |
| Browser Profile | 挂载 `./profile:/data/profile` | 最稳定，需图形界面或 noVNC 首次登录 |

Cookie / curl 登录在 session 过期后需重新复制；Profile 方式持久性更好。

Cookie JSON 格式：

```json
[
  {
    "name": "ds_session_id",
    "value": "...",
    "domain": ".deepseek.com",
    "path": "/",
    "secure": true
  }
]
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_BACKEND` | `browser` | `browser` 或 `official` |
| `DEEPSEEK_PROXY_API_KEY` | `local-secret` | 客户端 Bearer Token |
| `DEEPSEEK_API_KEY` | — | Official 模式必填 |
| `DEEPSEEK_STORAGE_STATE_FILE` | — | Playwright storage state（**推荐**） |
| `DEEPSEEK_CURL_FILE` | — | 浏览器 Copy as cURL 文件路径 |
| `DEEPSEEK_COOKIES_FILE` | — | Cookie JSON 文件路径（与 curl 二选一） |
| `DEEPSEEK_BROWSER_PROFILE` | `./deepseek-browser-profile` | Chromium 用户数据目录 |
| `DEEPSEEK_BROWSER_HEADLESS` | `0` | `1` 无头运行（curl 登录后建议开启） |
| `DEEPSEEK_USER_AGENT` | — | 覆盖 UA（curl 文件中有则自动提取） |
| `DEEPSEEK_CHAT_URL` | `https://chat.deepseek.com/` | 聊天页 URL |
| `DEEPSEEK_INPUT_SELECTOR` | `textarea[placeholder*="DeepSeek"]` | 输入框选择器 |
| `DEEPSEEK_ANSWER_SELECTOR` | `.ds-assistant-message-main-content` | 回答区域选择器 |
| `DEEPSEEK_BROWSER_TIMEOUT` | `300` | 等待回答**完成**超时（秒） |
| `DEEPSEEK_BROWSER_START_TIMEOUT` | 同 `DEEPSEEK_BROWSER_TIMEOUT` | 等待回答**开始**超时（秒）；深度思考可单独调大 |
| `DEEPSEEK_NEW_CHAT_PER_REQUEST` | `1` | 默认是否每次新建会话（`0` 默认复用） |
| `DEEPSEEK_NEW_CHAT_SELECTOR` | — | 自定义「新对话」按钮 CSS 选择器（UI 改版时使用） |
| `DEEPSEEK_WEB_MODE` | `fast` | 默认网页模式：`fast` 快速模式、`expert` 专家模式、`vision` 识图模式 |
| `DEEPSEEK_DEEP_THINKING` | `0` | 默认是否开启「深度思考」（`1` 开启，`0` 关闭） |
| `DEEPSEEK_LOG_LEVEL` | `INFO` | 日志级别 |
| `DEEPSEEK_LOG_MAX_CHARS` | `500` | 单条日志字段最大字符数，超出部分截断；设为 `0` 则省略内容 |

### Browser 模式请求扩展字段

以下字段通过 `POST /v1/chat/completions` 请求体或 HTTP 头传入（非 OpenAI 标准字段）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `new_chat` | `boolean` | `true` 新建网页会话；`false` 复用当前会话 |
| `metadata.new_chat` | `boolean` | 同上，适用于 `extra_body={"metadata": {...}}` |
| `X-DeepSeek-New-Chat` | HTTP 头 | 同上，`true` / `false` |
| `deepseek_mode` | `string` | 网页模式：`fast` / `expert` / `vision`，分别对应「快速模式 / 专家模式 / 识图模式」 |
| `metadata.deepseek_mode` | `string` | 同上，适用于 `extra_body={"metadata": {...}}` |
| `X-DeepSeek-Mode` | HTTP 头 | 同上，也支持 `quick`、`normal`、`pro`、`image`、中文模式名等别名 |
| `deep_thinking` | `boolean` | 是否开启网页上的「深度思考」 |
| `metadata.deep_thinking` | `boolean` | 同上，适用于 `extra_body={"metadata": {...}}` |
| `X-DeepSeek-Deep-Thinking` | HTTP 头 | 同上，`true` / `false` |

请求级字段优先级高于环境变量。未传 `deepseek_mode` 时使用 `DEEPSEEK_WEB_MODE`，未传 `deep_thinking` 时使用 `DEEPSEEK_DEEP_THINKING`。

---

## 日志

| 事件 | 含义 |
| --- | --- |
| `service.start` | 启动完成，含 backend 与路由列表 |
| `request.start` / `request.end` | HTTP 请求与耗时 |
| `chat.request` / `chat.response` | 聊天内容与回答摘要 |
| `chat.new_chat` | 本次是否新建网页会话、选择的网页模式和深度思考开关 |
| `browser.new_chat` | 点击新对话按钮或回退导航 |
| `browser.option` | 页面模式 / 深度思考控件的点击或跳过记录 |
| `browser.input` | 匹配到的输入框选择器 |

`Authorization`、`Cookie` 等敏感头在日志中会自动脱敏。

查看 Docker 日志：

```bash
docker logs -f deepseek-openai-proxy
```

---

## 故障排查

| 现象 | 处理 |
| --- | --- |
| `Executable doesn't exist` | Playwright 版本与镜像 tag 不一致，确保均为 `1.60.0` / `v1.60.0-noble` |
| 等待输入框超时 / 500 | 多为 `deepseek_storage.json` 缺少 Bearer 令牌，运行 `python3 -m verify_storage_state` 检查 |
| `DeepSeek 未登录` / `缺少 Bearer` | 登录后发一条测试消息，再运行 `save_storage_state`；确认 Network 里 completion 请求有 authorization 头 |
| `Cookie/curl file does not exist` | 创建 `secrets/deepseek_curl.txt` |
| Cookie Editor E2EE 文件 | 不支持加密导出，请用 DevTools **Copy as cURL** |
| 端口不通 | 确认映射端口（默认 `18002`）与 curl 地址一致 |
| 多轮对话无上下文 | 确认后续请求传 `"new_chat": false`；检查日志中 `chat.new_chat=false` |
| `Timed out waiting for DeepSeek to start answering` | 多为消息未发出或深度思考启动慢；复用会话时仅发送当前 system+user（不含历史 assistant）；可设 `DEEPSEEK_BROWSER_START_TIMEOUT=600` |
| 每次回答混入上一轮内容 | 确认传 `"new_chat": true` 或保持默认 `DEEPSEEK_NEW_CHAT_PER_REQUEST=1` |
| `未找到可用的 DeepSeek 页面选项控件` | DeepSeek 页面 UI 可能改版；先确认已进入聊天页，再检查「快速模式 / 专家模式 / 识图模式 / 深度思考」是否仍可见 |
| 传了 `deep_thinking=true` 但未开启 | 检查日志 `browser.option`；若页面控件已开启会记录 skip，若未命中控件会返回错误 |

更新 curl 或 storage state 后重启：

```bash
docker compose up -d
```

---

## 高级：远程 noVNC 登录 Profile（可选）

curl 方式失效或需要更稳定登录态时，可在远程服务器用 noVNC 生成 profile，再挂载 `./profile:/data/profile` 使用。步骤较多，一般仅在 curl 方式不满足时使用。详见 git 历史或联系维护者。

临时登录容器内 pip 安装（Playwright 基础镜像）：

```bash
pip install --ignore-installed typing_extensions \
  fastapi httpx "playwright==1.60.0" uvicorn \
  -i https://pypi.tuna.tsinghua.edu.cn/simple \
  --break-system-packages
```

---

## Browser 实现说明

Web 端聊天调用私有接口（如 `/api/v0/chat/completion`），并携带 `X-DS-PoW-Response`、SSE、WAF 脚本等动态风控字段。本代理不导出或重放这些密钥，而是通过 Playwright 在页面上模拟用户发送消息，由浏览器自行完成风控流程。

OpenAI 请求中的 `messages` 会被合并为单条 prompt 后填入网页输入框：

- **`new_chat: true`**：合并全部 `messages`（含 assistant 历史）
- **`new_chat: false`**：仅发送当前请求的 `system` + 最后一条 `user`（网页侧已保留上一轮上下文，避免重复粘贴历史）

若需网页端多轮上下文，请使用 `new_chat: false` 并在每次请求中只追加新的 user 内容（客户端 `messages` 可含 assistant 条目，代理会自动忽略）。

---

## 安全提示

- `secrets/deepseek_curl.txt` 含完整登录态，已加入 `.gitignore`，**切勿提交或公开**
- curl / cookie 过期后需重新从浏览器复制
- 生产环境请修改 `DEEPSEEK_PROXY_API_KEY`
