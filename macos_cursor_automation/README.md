# macOS Cursor 自动化

在 macOS 上通过 `open`、AppleScript、`cursor` / `cursor agent` CLI 驱动 Cursor，并提供可选的 **OpenAI Chat Completions 风格 HTTP 网关**（含图像/视频入参的落盘桥接）。

## 环境要求

- macOS，已安装 [Cursor](https://cursor.com/download)。
- 使用 **`keystroke` / `palette` / `activate`（System Events 路径）** 时：在 **系统设置 → 隐私与安全性 → 辅助功能** 中为运行脚本的终端或 Python 解释器授权。
- 使用 **`open --line`**、`**agent**`、`**serve**`：需要可用的 **`cursor`** 命令（Cursor 菜单 **Shell Command: Install 'cursor' command in PATH**），或默认路径存在  
  `/Applications/Cursor.app/Contents/Resources/app/bin/cursor`。
- **`agent` / `serve`**：需完成 Cursor 账号登录（`cursor agent login`）或设置 **`CURSOR_API_KEY`**。

## 安装

```bash
cd /path/to/macos_cursor_automation
python3 -m pip install -r requirements.txt
```

`requirements.txt` 仅 **`serve`（OpenAI 网关）** 需要：`fastapi`、`uvicorn`、`python-multipart`（``/v1/images/edits`` 解析 multipart 表单）、`httpx`。若只使用 `open` / `activate` / `keystroke` 等，可不安装。

## 命令行用法

在项目目录下执行（将 `python3 -m cursor_automation` 换成你的入口方式，例如包名不同时用 `python3 -m <包名>`）：

```bash
python3 -m cursor_automation <子命令> [选项]
```

### `open` — 用 Cursor 打开路径，可选跳转到行

```bash
# 打开文件或目录（open -a Cursor）
python3 -m cursor_automation open ./README.md

# 打开文件并跳到第 42 行（cursor -g，需 cursor CLI）
python3 -m cursor_automation open ./src/app.py --line 42
python3 -m cursor_automation open ./src/app.py -n 10 -c 3   # 第 10 行第 3 列
```

指定 `--line` 时路径须为**已存在的文件**。`--column` 必须与 `--line` 同时使用。

### `activate` — 激活 Cursor 应用

```bash
python3 -m cursor_automation activate
```

### `keystroke` — 向前台 Cursor 发送按键（需辅助功能）

```bash
python3 -m cursor_automation keystroke p --command --shift   # Cmd+Shift+P 中的 p，示例组合见下
```

常用修饰：`--command`、`--shift`、`--option`、`--control`。

### `palette` — 打开命令面板（Cmd+Shift+P）

```bash
python3 -m cursor_automation palette
```

### `agent` — 非交互运行 Cursor Agent，结果打印到 stdout

```bash
python3 -m cursor_automation agent "用一句话说明当前目录用途。" \
  --workspace /path/to/repo \
  --format json \
  --mode ask
```

| 选项 | 说明 |
|------|------|
| `-w` / `--workspace` | 工作区目录，传给 `cursor agent --workspace` |
| `--format` | `json`（默认）、`text`、`stream-json` |
| `--mode` | `ask`（问答）、`plan`（规划）、`agent`（Agent 默认行为） |
| `--no-trust` | 不传 `--trust`（无头场景可能卡在信任提示） |
| `--force` | 对应 `cursor agent --force` |
| `--model` | 例如 `gpt-5`、`sonnet-4` |
| `--timeout` | 子进程超时（秒） |
| `--stream-partial-output` | 与 `--format stream-json` 联用 |

退出码与 `cursor agent` 子进程一致；**标准输出**为 `--print` 内容（`json` 时多为单行 JSON）。

### `serve` — OpenAI 兼容 HTTP 服务

```bash
export CURSOR_OPENAI_BRIDGE_API_KEY='your-secret'   # 可选：设置后须带 Bearer 访问 /v1
python3 -m cursor_automation serve --host 0.0.0.0 --port 8765 -w ./

# 模型列表（示例）
curl -s http://127.0.0.1:8765/v1/models | head
```

| 选项 | 说明 |
|------|------|
| `--host` | 默认 `127.0.0.1` |
| `--port` | 默认 `8765` |
| `-w` / `--workspace` | 默认工作区；未在请求中覆盖时使用 |
| `--mode` | 传给 agent：`ask`（默认）、`plan` 或 `agent` |
| `--timeout` | 单次 agent 超时（秒），默认 `600` |
| `--log-dir` | 服务日志目录，默认 `./logs`（见下文「运行日志」）；会设置 `CURSOR_BRIDGE_LOG_DIR` |

另可使用仓库内脚本 **`open_in_cursor.sh`**：`./open_in_cursor.sh [路径]`。

## Python API 示例

```python
from pathlib import Path
from cursor_automation import (
    open_in_cursor,
    run_cursor_agent,
    agent_completion_text,
)

# 打开项目
open_in_cursor(Path("~/proj").expanduser())
open_in_cursor("~/proj/foo.py", line=100, column=1)

# 调用 Agent 并取正文
r = run_cursor_agent(
    "当前仓库主要做什么？一句话。",
    workspace=Path.cwd(),
    output_format="json",
    mode="ask",
    trust=True,
    timeout=120.0,
)
if r.returncode == 0:
    print(agent_completion_text(r))
else:
    print(r.stderr or r.stdout)
```

`openai_bridge` 供服务使用，一般无需直接导入：

```python
from pathlib import Path
from openai_bridge import create_app

app = create_app(default_workspace=Path("."), agent_mode="ask", agent_timeout=600.0)
# 再用 uvicorn 挂载 app
```

## OpenAI 兼容网关（`serve`）

### 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（不校验 API Key） |
| GET | `/v1/models` | 模型列表：首项为网关占位 `cursor-agent`，其余来自本机 ``cursor agent models``（默认缓存 60s，见环境变量） |
| POST | `/v1/chat/completions` | 与 OpenAI Chat Completions 对齐；支持 **`stream: true`**（SSE，`text/event-stream`），底层为 `cursor agent --output-format stream-json --stream-partial-output` |
| POST | `/v1/messages` | Anthropic Messages 兼容端点，便于 Claude Code CLI 通过 `ANTHROPIC_BASE_URL` 直连本服务（支持 `stream: true`）。 |
| POST | `/v1/images/generations` | 兼容 OpenAI Images：默认 **agent** 产出 **SVG**（可选栅格 PNG）；**agent_interactive** 走终端同款 Agent 生图工具，将 **PNG** 写入 ``workspace/.cursor_bridge_generated/`` 并返回 ``data:image/...``；**sd_webui** 为 A1111 扩散 PNG |
| POST | `/v1/images/edits` | 兼容 OpenAI **images.edit**（``multipart/form-data``）：上传参考图 + prompt；**agent_interactive** 读参考图生 PNG；**sd_webui** 走 ``/sdapi/v1/img2img``；**agent** 读参考图产出 SVG |

**`/v1/chat/completions` 日志**：logger 名为 `cursor_openai_bridge`。默认将每条请求的 **完整 JSON 请求体**、**完整 JSON 响应体**（及 `Authorization` 脱敏后的请求头）打到 **stderr**；若配置了日志目录（见下），**同时写入** `cursor_openai_bridge.log`。大 body 可用 **`CURSOR_BRIDGE_LOG_MAX_CHARS`** 截断；**502** 时额外有一条 **WARNING** 含 agent 的 `stdout`/`stderr` 片段。**流式请求**（`stream: true`）不会在「结束」时再打整段响应体，仅有一条 **STREAM** 级别的 info 标记。

### 运行日志（`serve`）

- **`--log-dir`**：默认 **`./logs`**（相对启动时的当前工作目录解析）；启动前会创建目录，并导出 **`CURSOR_BRIDGE_LOG_DIR`** 供网关写业务日志。
- 典型文件：
  - **`cursor_openai_bridge.log`**：网关业务日志（轮转）。
  - **`uvicorn.log`** / **`uvicorn_access.log`**：Uvicorn 进程与访问日志（轮转）。
- 轮转大小与备份个数由 **`CURSOR_BRIDGE_LOG_FILE_MAX_BYTES`**、**`CURSOR_BRIDGE_LOG_FILE_BACKUP_COUNT`** 控制（默认与 `openai_bridge` 文档一致）。
- 仅 **`import create_app`**、不经 `serve` 时：若未设置 **`CURSOR_BRIDGE_LOG_DIR`**，默认仍会把业务日志写到 **`logs/cursor_openai_bridge.log`**（与网关模块默认值一致）。若希望**只打控制台不写文件**，可将 **`CURSOR_BRIDGE_LOG_DIR`** 设为空字符串（不推荐与 `serve` 的 `--log-dir` 同时混用，以 CLI 为准）。

若设置了 **`CURSOR_OPENAI_BRIDGE_API_KEY`**，所有 **`/v1/*`** 请求需带头：

```http
Authorization: Bearer <与环境变量相同的密钥>
```

### 限制说明

- **流式**：`stream: true` 时响应为 **SSE**；增量正文依赖 Cursor CLI 的 **stream-json** 行快照与网关侧**差分**，若 CLI 输出字段变化，块切分可能与 IDE 内聊天不完全一致。
- **`/v1/chat/completions` 与 `/v1/messages`**：请求体根级可带 **`metadata`**（OpenAI Python SDK：`chat.completions.create(..., extra_body={"metadata": {...}})`）。字段 **`cursor_agent_mode`**（或 **`agent_mode`**）：省略时沿用 `serve` 的 **`--mode`**；设为 **`writable`** / **`full`** / **`tools`** / **`auto`** / **`omit`** / **`none`** 时**不向** `cursor agent` 传 `--mode`，与 **`agent_interactive`** 默认可写 Agent 一致（可调用生图等工具）；**`ask`** / **`plan`** 显式传给 CLI（**`agent`** 会按 CLI 规则映射为 **`ask`**）。
- **`usage`** 中 token 数为占位 **0**（底层 agent 未提供分词统计）；流式路径下通常不附带 `usage` chunk（与部分 OpenAI 客户端对 `stream_options` 的期望可能不同）。
- **图像 / 视频**：`cursor agent` 仅接受文本；网关会把 `image_url` / 视频类片段中的 URL 或 `data:` 内容**下载或解码到工作区临时目录**，在 prompt 中写入 **`file://` 绝对路径**，由 Agent 自行读文件分析；能否理解媒体取决于模型与工具能力。
- **`/v1/images/generations`**：``metadata.image_engine`` / ``CURSOR_BRIDGE_IMAGE_ENGINE``：**agent**（默认）产出 **SVG**；**agent_interactive** 要求本机 Cursor Agent 具备与 IDE 一致的生图能力，将光栅图保存到工作区约定路径或由网关从回复中解析 **data URL / 图片 URL**；**sd_webui** 为本地 Stable Diffusion WebUI。默认 **agent** 路径不是 DALL·E 级照片生成；**agent_interactive** 与 **sd_webui** 可得到 PNG 光栅结果。**注意**：``agent_interactive`` 默认**不传** ``--mode ask``；勿设 ``CURSOR_BRIDGE_IMAGE_AGENT_MODE=ask``。若希望 **chat** 与生图一致，可在单次 chat 请求中带 ``metadata.cursor_agent_mode: "writable"``（见上条）。
- **`/v1/images/edits`**：``multipart/form-data``，必填 **`image`**（参考图文件）与 **`prompt`**。``metadata.image_engine`` 默认可通过环境变量指定；未指定时网关默认 **`agent_interactive`**（读参考图生 PNG）。**sd_webui** 走 ``/sdapi/v1/img2img``（``CURSOR_BRIDGE_SDWEBUI_IMG2IMG_DENOISING`` 默认 `0.65`）。**agent** 读参考图路径产出 SVG。
- **PNG 栅格化失败**：优先使用 **`rsvg-convert`**（`brew install librsvg`）；网关会尝试 `/opt/homebrew/bin/rsvg-convert` 等固定路径，避免 IDE 子进程 PATH 过短。若走 **cairosvg**，需与本机 Python **同架构**的 **libcairo**（Apple Silicon 上勿混用 `/usr/local` 里旧的 x86_64 cairo，可 `brew install cairo` 并保证 arm64 库在搜索路径中）。

### 工作区

- 默认：启动 `serve` 时的 **`--workspace`**，未传则为**当前工作目录**。
- 单次请求可在 JSON 根增加 **`metadata.workspace`** 或 **`metadata.workspace_path`**（字符串）覆盖。
- **按请求隔离（默认开启）**：环境变量 **`CURSOR_BRIDGE_ISOLATE_WORKSPACE=1`**（默认）时，每次 HTTP 请求使用 **`<workspace>/jobs/<req_id>`** 作为 agent 工作区（`req_id` 为网关日志中的 UUID），避免多篇生成/并发请求共享同一目录导致文件与上下文污染。关闭：`CURSOR_BRIDGE_ISOLATE_WORKSPACE=0`，或请求体 **`metadata.isolate_workspace: false`**。
- 网关每次调用底层 `cursor agent` 时附带 **`--force`**：不延续上一轮 Agent **会话**；与 **`jobs/<req_id>`** 目录隔离互补（会话 vs 磁盘产物）。
- 网关日志里每条请求仍有独立 **`req_id`**（UUID）；开启隔离时 DEBUG 日志会打印实际 `workspace` 路径。

### 多模态 `content` 片段（与 OpenAI 风格对齐）

在 `messages[].content` 为数组时，支持例如：

- **图像**：`{"type": "image_url", "image_url": {"url": "<https 或 data:image/...;base64,...>"}}`
- **视频**（任选一种结构）：  
  `{"type": "input_video", "input_video": {"url": "..."}}`  
  `{"type": "video_url", "video_url": {"url": "..."}}`  
  `{"type": "video", "video": {"url": "..."}}`

文本片段：`{"type": "text", "text": "..."}`。

### 使用 OpenAI 官方 Python SDK 调用

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key=os.environ.get("CURSOR_OPENAI_BRIDGE_API_KEY", "dummy"),
)

resp = client.chat.completions.create(
    model="cursor-agent",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "简要描述附件图像。"},
                {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
            ],
        }
    ],
)
print(resp.choices[0].message.content)
```

**流式**（SDK 会处理 SSE）：

```python
stream = client.chat.completions.create(
    model="cursor-agent",
    messages=[{"role": "user", "content": "用一句话说你好。"}],
    stream=True,
)
for ev in stream:
    c = ev.choices[0].delta.content
    if c:
        print(c, end="", flush=True)
print()
```

命令行调试可 **`curl -N`**（禁用缓冲）观察 SSE。

将 `model` 设为具体 Cursor 模型名（如 `gpt-5`）时，会传给 `cursor agent --model`（与占位名 `cursor-agent` 区分）。

本仓库 **`client.py`** 示例支持切换模型：

```bash
# 查看当前账号可用模型（Cursor CLI）
cursor agent models

# 调用本地 Cursor 桥（chat；首参可省略 chat，与写 ``chat`` 等价）
python3 client.py chat -p "你好" --model auto
python3 client.py -p "描述图" -i ./photo.png

# 图像生成（走本地桥：images.generate → Agent 输出 SVG，或 sd_webui 扩散 PNG）
python3 client.py image "一只橘猫，扁平插画" --size 1024x1024 --response-format url
# 公园心流社区编辑插画（内置 preset；需配置 CURSOR_BRIDGE_SDWEBUI_URL）
python3 client.py image --preset park_flow_community --engine sd_webui --size 1344x768 -o ./outputs/

python3 client.py image '一份为“{argument name="brand" default="AM Cosmetics"}”制作的专业广告，展示了来自 The Midnight Gala Collection 系列的 {argument name="palette" default="Eye Journey Palette"}。画面中心是一位留着棕色长波浪卷发的女性，展示着色彩鲜艳的眼影妆容，色调包括祖母绿、深蓝色和闪耀的金色。她周围环绕着打开的彩妆盘，展示了 18 种高显色度色号、散落的眼影粉以及金柄化妆刷。整体美学风格奢华，背景为 {argument name="background" default="白色大理石背景"}，配以优雅的金色装饰，并附有详细说明彩妆盘哑光、珠光、金属光泽和闪粉质地的文字。' \
  --engine agent_interactive \
  --style realistic \
  -o ./outputs/out.png

python3 client.py image '16:9 autonomous kinetic architecture, the heliotropic tracking mechanics of [aerospace/solar tracking array] shaping an adaptive, luxury [outdoor architectural structure], sequence from [astronomical/solar path diagrams] to [robotic kinematic wireframes] to a programmable louvre abstraction to the final architectural installation, ai to infer smart-motor integration and weather-responsive materials utilizing [material 1] and [material 2], featuring time-lapse shadow projection diagrams, [aesthetic style] aesthetic, presentation layout: solar path charts at the top, robotic hinge details in the margins, stunning photorealistic architectural render below, [lighting style].  input: [deep space network satellite dish array], [smart kinetic patio pergola], [equatorial solar trajectory mapping], [multi-axis pivoting joint schematics], [photovoltaic-coated tinted glass], [extruded matte bronze aluminum], [contemporary silicon valley billionaire estate], [golden hour sunlight casting intricate geometric shadows]' \
  --engine agent_interactive \
  --style realistic \
  -o ./outputs/out3.png

# 保存为 PNG（服务端 SVG→栅格；需 brew install librsvg 或 pip install cairosvg）
python3 client.py image "扁平插画猫" --save-as png -o ./out.png
# 图像编辑（参考图 + prompt → images/edits；默认 agent_interactive 出 PNG）
python3 client.py image-edit "Turn into a clean cartoon portrait, preserve identity" \
  -i ./outputs/out.png --style editorial -o ./outputs/portrait_cartoon.png
# 默认自动解码为 SVG（./generated_时间戳_0.svg）；指定路径：
python3 client.py image "..." -o ./out.svg
# 超长 data URL 默认截断打印；需完整打印：export CURSOR_CLIENT_IMAGE_PRINT_FULL_URL=1
```

### 图像编辑 `POST /v1/images/edits`

基于**参考图**生成/变换（OpenAI 兼容 ``images.edit``，``multipart/form-data``）。典型场景：人像卡通化、风格迁移、以图生图编辑。

**curl**（``agent_interactive``，返回 base64 PNG）：

```bash
curl -sS -X POST "http://127.0.0.1:8765/v1/images/edits" \
  -H "Authorization: Bearer ${CURSOR_OPENAI_BRIDGE_API_KEY:-dummy}" \
  -F "image=@./portrait.jpg" \
  -F "prompt=Turn the reference portrait into a clean premium cartoon narrator character. Preserve recognizable facial structure and hair shape. Warm editorial colors, half-body pose, no text, no watermark." \
  -F "size=1024x1024" \
  -F "response_format=b64_json" \
  -F 'metadata={"image_engine":"agent_interactive","image_style":"editorial"}'
```

**curl**（``sd_webui`` img2img，需配置 ``CURSOR_BRIDGE_SDWEBUI_URL``）：

```bash
export CURSOR_BRIDGE_SDWEBUI_URL=http://127.0.0.1:7860

curl -sS -X POST "http://127.0.0.1:8765/v1/images/edits" \
  -H "Authorization: Bearer ${CURSOR_OPENAI_BRIDGE_API_KEY:-dummy}" \
  -F "image=@./portrait.jpg" \
  -F "prompt=soft editorial cartoon portrait, warm colors, clean silhouette" \
  -F "size=1024x1024" \
  -F "response_format=url" \
  -F 'metadata={"image_engine":"sd_webui","image_style":"editorial"}'
```

**OpenAI Python SDK**（与 ``vtok_ai_factory`` 角色卡通化调用方式一致）：

```python
import json
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key=os.environ.get("CURSOR_OPENAI_BRIDGE_API_KEY", "dummy"),
)

with open("./portrait.jpg", "rb") as f:
    resp = client.images.edit(
        image=f,
        prompt=(
            "Turn the reference portrait into a clean premium cartoon narrator character. "
            "Preserve recognizable facial structure, hair shape, and overall identity. "
            "Warm editorial colors, half-body pose, no text, no logo, no watermark."
        ),
        model="cursor-agent",
        n=1,
        size="1024x1024",
        response_format="b64_json",
        extra_body={
            "metadata": {
                "image_engine": "agent_interactive",
                "image_style": "editorial",
            }
        },
    )

b64 = resp.data[0].b64_json
with open("./portrait_cartoon.png", "wb") as out:
    import base64
    out.write(base64.standard_b64decode(b64))
print("已保存: ./portrait_cartoon.png")
```

**client.py 命令行**（与上文 SDK 等价）：

```bash
python3 client.py image-edit \
  "Turn the reference portrait into a clean premium cartoon narrator character. Preserve recognizable facial structure and hair shape." \
  -i ./portrait.jpg \
  --engine agent_interactive \
  --style editorial \
  --size 1024x1024 \
  --response-format b64_json \
  -o ./portrait_cartoon.png
```

表单字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| `image` | 是 | 参考图文件（``png`` / ``jpg`` / ``webp``） |
| `prompt` | 是 | 编辑/变换说明 |
| `metadata` | 否 | JSON 字符串：`image_engine`、`image_style`、`workspace` 等（同 ``generations``） |
| `size` | 否 | 默认 ``1024x1024`` |
| `response_format` | 否 | ``url``（默认，``data:image/...``）或 ``b64_json`` |
| `model` | 否 | 传给 ``cursor agent`` 的模型；省略或 ``cursor-agent`` 表示默认路由 |
| `n` | 否 | 生成张数，1–4 |

`client.py`：Chat 与 **image** / **image-edit** 均使用 **`CURSOR_CLIENT_BASE_URL`**、**`CURSOR_OPENAI_BRIDGE_API_KEY`**（与网关一致）。**image** 默认返回 **SVG**；**image-edit** 默认 **PNG**（``--engine agent_interactive``）。运行前可在项目根目录放 **`.env`**，`client.py` 会自动加载且**不覆盖**已 `export` 的变量。详见 `client.py` 文件头。

`.env.example` 中的 **`OPENAI_*`** 仅作其它脚本/官方 API 参考；**本仓库 `client.py image` 不再要求**。

## 环境变量小结

| 变量 | 用途 |
|------|------|
| `CURSOR_API_KEY` | `cursor agent` 认证（亦可 `cursor agent login`） |
| `CURSOR_OPENAI_BRIDGE_API_KEY` | 网关 `/v1/*` 可选 Bearer 校验 |
| `CURSOR_BRIDGE_MODELS_CACHE_TTL` | `/v1/models` 结果缓存秒数（默认 `60`） |
| `CURSOR_BRIDGE_LOG_LEVEL` | 网关详细日志级别（默认 `INFO`） |
| `CURSOR_BRIDGE_LOG_DIR` | 业务日志目录；默认 `logs`（写入 `cursor_openai_bridge.log`）；设为空字符串则仅控制台 |
| `CURSOR_BRIDGE_LOG_FILE_MAX_BYTES` | 业务日志单文件最大字节（默认 `10485760`） |
| `CURSOR_BRIDGE_LOG_FILE_BACKUP_COUNT` | 轮转保留文件个数（默认 `5`） |
| `CURSOR_BRIDGE_LOG_MAX_CHARS` | 单条请求/响应日志最大字符，`0` 不截断 |
| `CURSOR_BRIDGE_LOG_AGENT_PROMPT` | 设为 `1` 时额外打印发给 agent 的 prompt |
| `CURSOR_BRIDGE_LOG_AGENT_SUBPROCESS` | 设为 `1` 时对所有 agent 子进程打心跳与超时尾部输出 |
| `CURSOR_BRIDGE_AGENT_PROGRESS_INTERVAL_SEC` | 子进程心跳间隔（秒，默认 `30`） |
| `CURSOR_BRIDGE_AGENT_SUBPROCESS_LIVE_STDERR` | 设为 `1` 时逐行打印子进程 stderr（日志量大） |
| `CURSOR_BRIDGE_AGENT_PIPE_READ_CHUNK` | 监控模式下读管道块大小（字节，默认 `65536`） |
| `CURSOR_CLIENT_MODEL` | `client.py` 默认模型 id（可被 `--model` 覆盖） |
| `CURSOR_CLIENT_BASE_URL` | `client.py` 调用的网关 Base URL |
| `CURSOR_BRIDGE_IMAGE_ENGINE` | 默认图像引擎：`agent` / `agent_interactive` / `sd_webui`（``edits`` 未指定 metadata 时默认 `agent_interactive`） |
| `CURSOR_BRIDGE_SDWEBUI_URL` | Stable Diffusion WebUI 根 URL（``sd_webui`` 引擎必填） |
| `CURSOR_BRIDGE_SDWEBUI_IMG2IMG_DENOISING` | ``images/edits`` 走 ``sd_webui`` 时的 denoising strength（默认 `0.65`） |

### Claude Code CLI 直连示例

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8765"
export ANTHROPIC_AUTH_TOKEN=""
export ANTHROPIC_MODEL="auto"
```

- `ANTHROPIC_MODEL=auto` 时，网关会使用默认 Cursor 路由。
- 若你设置了 `CURSOR_OPENAI_BRIDGE_API_KEY`，Anthropic 风格请求同样可用 `x-api-key`（或 `Authorization: Bearer`）鉴权。

## Docker / docker-compose

仓库根目录提供 **`Dockerfile`** 与 **`docker-compose.yml`**，将 **OpenAI 兼容网关**（`serve`）打成镜像并在 Linux 容器中运行。

```bash
cd /path/to/macos_cursor_automation
cp .env.example .env
# 编辑 .env：至少配置 CURSOR_API_KEY；若启用网关鉴权则配置 CURSOR_OPENAI_BRIDGE_API_KEY

docker compose build
docker compose up -d
# 默认 http://0.0.0.0:8765 ，工作区为 ./workspace → 容器 /workspace
```

- **容器内认证（与「宿主机已登录 IDE」不是一回事）**：你在 macOS 上直接跑 `serve` 时，`cursor agent` 能用到本机已登录会话或 Keychain；**Linux 容器**是隔离的 `$HOME`，默认拿不到这些凭据，日志里会出现 `Authentication required` / 需 `CURSOR_API_KEY`。**推荐做法**：在 `.env` 中设置 **`CURSOR_API_KEY`**（与 Cursor 账号关联的 API Key，用于无头/CI，见 [CLI 认证说明](https://docs.cursor.com/en/cli/reference/authentication)），再 `docker compose up -d`。**不要**把宿主 `~/.local/share/cursor-agent` 整目录挂进 Linux 容器：`versions` 下是 **darwin** 二进制，会覆盖镜像内的 **linux** `cursor-agent` 导致更糟。若需实验性复用宿主 CLI 配置，可只挂 **`~/.cursor` → `/root/.cursor`**（`docker-compose.yml` 内有注释示例；未文档保证、注意密钥暴露风险）。
- **构建时安装 Cursor CLI**：默认 `INSTALL_CURSOR=1`，执行官方 `curl https://cursor.com/install -fsS | bash`（需能访问外网）。离线构建可设 **`INSTALL_CURSOR=0`**，再在运行时把宿主机的 **`cursor` 可执行文件** 挂进容器并保证在 `PATH` 中。
- **Apple Silicon**：若官方仅提供 `linux/amd64` CLI，可尝试  
  `docker compose build --platform linux/amd64`。
- 环境变量与日志选项与上文一致，可通过 **`.env`** 或 `docker-compose.yml` 的 `environment` 传入。
- 需要持久化日志时，为 **`CURSOR_BRIDGE_LOG_DIR`** 挂载卷（例如映射到容器内 `/workspace/logs`），或在 compose 中覆盖 **`CURSOR_BRIDGE_LOG_DIR`** / 启动命令里的 **`--log-dir`**。

## 许可与免责

本仓库脚本会驱动本机 GUI与 CLI，请在可信环境使用；生产部署请自行加固网络、鉴权与资源限制。
