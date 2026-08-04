# Qwen OpenAI Proxy

OpenAI 兼容代理，对接 [https://chat.qwen.ai/](https://chat.qwen.ai/)。支持两种后端：

| 模式 | 环境变量 | 说明 |
| --- | --- | --- |
| `browser` | `QWEN_BACKEND=browser` | 用 Playwright 驱动已登录的 Qwen 网页，无需官方 API Key |
| `official` | `QWEN_BACKEND=official` | 转发至 DashScope OpenAI 兼容 API，需 `QWEN_API_KEY` |

Browser 模式通过真实浏览器页面完成聊天、生图、生视频，由网页自身处理登录态与风控。

---

## 功能

| 端点 | 说明 |
| --- | --- |
| `POST /v1/chat/completions` | 聊天对话，支持 `qwen_mode` 切换模式 |
| `POST /v1/images/generations` | 文生图；可选 `image_url` / `image` 作为参考图 |
| `POST /v1/images/edits` | 图生图/编辑，multipart 上传参考图 + `prompt` |
| `POST /v1/videos/generations` | 文生视频；可选 `image_url` / `image` 作为首帧参考图 |
| `GET /v1/models` | 模型列表 |

---

## 快速开始（Docker，推荐）

### 1. 本地导出登录态（只需一次）

```bash
cd qwen_openai_proxy
python3 -m pip install -e .
python3 -m playwright install chromium

python3 -m save_storage_state
```

浏览器弹出后登录 [https://chat.qwen.ai/](https://chat.qwen.ai/)，**在聊天页发一条测试消息并收到回复后**，回到终端按 **Enter**。

会生成 `secrets/qwen_storage.json`。导出后可校验：

```bash
python3 -m verify_storage_state secrets/qwen_storage.json
```

### 2. 启动

```bash
docker compose up -d --build
```

默认监听 **18005** 端口。

### 3. 验证聊天

```bash
curl http://10.1.10.113:18005/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-chat-web",
    "messages": [{"role": "user", "content": "用一句话介绍一下你自己"}]
  }'
```

### 4. 验证生图

```bash
curl http://10.1.10.113:18005/v1/images/generations \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "一只在草地上奔跑的金毛犬，写实风格"
  }'
```

### 5. 验证图生图（参考图）

```bash
curl http://10.1.10.113:18005/v1/images/edits \
  -H "Authorization: Bearer local-secret" \
  -F "prompt=把这张图改成水彩风格" \
  -F "image=@/path/to/reference.png"
```

### 6. 验证生视频

```bash
curl http://10.1.10.113:18005/v1/videos/generations \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "一只小猫在窗台上打哈欠，慢动作"
  }'
```

### 7. 验证图生视频（参考图）

```bash
curl http://10.1.10.113:18005/v1/videos/generations \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "让这张图动起来，镜头缓慢推进",
    "image_url": "https://example.com/frame.png"
  }'
```

---

## 参考图参数

三个生成端点都支持**单张参考图**：

| 端点 | 参考图传参方式 |
| --- | --- |
| `POST /v1/images/generations` | JSON 可选字段：`image_url` 或 `image`（本地路径 / data URL / 可下载 URL） |
| `POST /v1/images/edits` | multipart 必填字段：`image`（文件）+ `prompt` |
| `POST /v1/videos/generations` | JSON 可选字段：`image_url` 或 `image` |

`image_url` 支持：

- `https://...` 远程 URL（服务端自动下载）
- `data:image/png;base64,...` data URL
- 本地文件路径（仅本机开发时）

---

## Qwen 模式控制

Browser 模式支持在发送消息前切换 Qwen 网页模式：

| 参数 | 可选值 | 说明 |
| --- | --- | --- |
| `qwen_mode` | `chat` / `image` / `video` / `deep_research` / `web_dev` | 对应网页「选择模式」菜单 |
| `response_mode` | `auto` / `thinking` / `fast` | 对应输入框左侧「自动 / 思考 / 快速」下拉（仅聊天模式） |
| `thinking` | `true` / `false` | 兼容字段：`true` 等价于 `response_mode: thinking` |
| `new_chat` | `true` / `false` | 是否新建对话 |

别名：`t2i` → `image`，`t2v` → `video`，`生成图像` → `image`，`创建视频` → `video`；`快速` / `quick` → `fast`，`思考` / `think` → `thinking`，`自动` → `auto`。

优先级：

1. 请求体 `qwen_mode` / `response_mode`
2. 请求体 `metadata.qwen_mode` / `metadata.response_mode`
3. HTTP 头 `X-Qwen-Mode` / `X-Qwen-Response-Mode`（`X-Qwen-Thinking` 仍可作为响应模式别名）
4. 环境变量 `QWEN_DEFAULT_MODE` / `QWEN_RESPONSE_MODE`

### 思考模式示例

```bash
curl http://10.1.10.113:18005/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-chat-web",
    "response_mode": "thinking",
    "messages": [{"role": "user", "content": "分析这篇文章的结构问题"}]
  }'
```

兼容写法：`"thinking": true` 等价于 `"response_mode": "thinking"`。

### 通过聊天接口生图

```bash
curl http://10.1.10.113:18005/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-image-web",
    "qwen_mode": "image",
    "messages": [{"role": "user", "content": "画一只在月球上的兔子"}]
  }'
```

### 多轮对话

```bash
curl http://10.1.10.113:18005/v1/chat/completions \
  -H "Authorization: Bearer local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-chat-web",
    "new_chat": false,
    "messages": [{"role": "user", "content": "继续上一句"}]
  }'
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QWEN_BACKEND` | `browser` | `browser` 或 `official` |
| `QWEN_PROXY_API_KEY` | `local-secret` | 客户端 Bearer Token |
| `QWEN_API_KEY` | — | Official 模式必填 |
| `QWEN_STORAGE_STATE_FILE` | — | Playwright storage state（**推荐**） |
| `QWEN_CURL_FILE` | — | Copy as cURL 文件路径 |
| `QWEN_COOKIES_FILE` | — | Cookie JSON 文件路径 |
| `QWEN_BROWSER_PROFILE` | `./qwen-browser-profile` | Chromium 用户数据目录 |
| `QWEN_BROWSER_HEADLESS` | 有 storage state 时为 `1` | 无头运行 |
| `QWEN_CHAT_URL` | `https://chat.qwen.ai/` | 聊天页 URL |
| `QWEN_INPUT_SELECTOR` | `textarea.message-input-textarea` | 输入框选择器 |
| `QWEN_ANSWER_SELECTOR` | `.response-message-content.phase-answer, ...` | 回答区域选择器 |
| `QWEN_BROWSER_TIMEOUT` | `300` | 聊天等待超时（秒） |
| `QWEN_IMAGE_TIMEOUT` | `900` | 生图等待超时（秒） |
| `QWEN_VIDEO_TIMEOUT` | `1800` | 生视频等待超时（秒） |
| `QWEN_NEW_CHAT_PER_REQUEST` | `1` | 默认是否每次新建会话 |
| `QWEN_DEFAULT_MODE` | `chat` | 默认网页模式 |
| `QWEN_LOG_LEVEL` | `INFO` | 日志级别 |

---

## 本地开发

```bash
cd qwen_openai_proxy
python3 -m pip install -e .
export QWEN_STORAGE_STATE_FILE="./secrets/qwen_storage.json"
export QWEN_BACKEND=browser
export QWEN_PROXY_API_KEY="local-secret"
python3 -m uvicorn main:app --host 0.0.0.0 --port 18005
```

---

## 测试

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest tests/ -v
```

---

## 实现说明

Web 端通过 Playwright 在 [https://chat.qwen.ai/](https://chat.qwen.ai/) 页面上模拟用户操作：

- 聊天：默认模式，输入 prompt 后等待 `.response-message-content` 稳定
- 生图：点击「选择模式 → 生成图像」（`data-menu-id` 后缀 `t2i`），等待回答区出现 `img` 标签
- 生视频：点击「选择模式 → 创建视频」（`data-menu-id` 后缀 `t2v`），等待回答区出现 `video` 标签或视频链接

生图/生视频需要已登录账号；未登录时对应菜单项为 disabled 状态。

---

## 安全提示

- `secrets/qwen_storage.json` 含完整登录态，**切勿提交或公开**
- 生产环境请修改 `QWEN_PROXY_API_KEY`
