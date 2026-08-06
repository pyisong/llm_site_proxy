# Cursor Bridge 全局 Skills 存储与安装 API 设计

日期：2026-08-04

## 目标

为 `macos_cursor_automation`（Cursor OpenAI Bridge）提供：

1. 持久化的**全局 Skills 目录**（宿主机文件，重启容器仍在）
2. 启动时挂载到 Cursor 能发现的用户级路径，使 `/v1/chat/completions` 等走 `cursor agent` 时自动可用
3. HTTP API：**按来源安装** + **按自然语言生成**；列出 / 查看 / 卸载

## 决策

| 项 | 选择 |
|----|------|
| 方案 | A：全局挂到用户级 skills，不依赖关 `ISOLATE_WORKSPACE` |
| 宿主机目录 | `llm_site_proxy/cursor_skills/` |
| 容器挂载目标 | `/root/.cursor/skills`（只读/读写：`rw`） |
| 安装方式 | `path` / `git` / `url` + `generate`；**本期不做 `builtin` catalog** |
| API 挂载 | 现有 bridge FastAPI（`openai_bridge`），鉴权同 `/v1/*` |
| 与 chat | 不改 completions 语义；依赖 Cursor 自动发现全局 skills |

## 背景与约束

- Cursor 仅从 `$HOME/.cursor/skills/`（及 `.agents/skills/`）与 `<workspace>/.cursor/skills/` 发现 Skills，**不会扫任意自定义路径**。
- Bridge 默认 `CURSOR_BRIDGE_ISOLATE_WORKSPACE=1`，agent 工作区为 `/workspace/jobs/<req_id>`，**项目级** skills 通常不可见。
- 因此全局目录必须挂到 **`/root/.cursor/skills`**，而非 `/workspace/.cursor/skills`。

## 目录布局

```text
llm_site_proxy/
  cursor_skills/                 # 持久卷内容（宿主机）
    .gitkeep
    README.md                    # 说明与挂载约定
    <skill-name>/
      SKILL.md                   # 必需
      ...                        # 可选 scripts/ reference 等
  macos_cursor_automation/
    docker-compose.yml           # 增加 volume 挂载
```

- `cursor_skills/` 下已安装内容可 gitignore（保留 `.gitkeep` + `README.md`），避免把生成物强制进库；若团队希望版本化某几个 skill，可显式 force-add。
- 本机非 Docker：环境变量 `CURSOR_SKILLS_DIR` 指向该目录，启动前确保 `~/.cursor/skills` 为该目录的软链或同等内容（实现计划里二选一，默认软链文档说明即可）。

## Compose 挂载

```yaml
volumes:
  - ../cursor_skills:/root/.cursor/skills:rw
  # 现有 workspace 挂载保持不变（可选）
```

环境变量（可选文档化）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `CURSOR_SKILLS_DIR` | `/root/.cursor/skills`（容器内） | Skills 根目录；API 读写此路径 |
| `CURSOR_SKILLS_ALLOW_REMOTE` | `0` | `1` 才允许 `git`/`url` 安装；默认仅 `path` + `generate` |
| `CURSOR_SKILLS_REMOTE_TIMEOUT` | `120` | 远程拉取超时（秒） |
| `CURSOR_SKILLS_MAX_BYTES` | `20971520` | 单次安装解压/拷贝体积上限（约 20MB） |

## API

鉴权：若设置 `CURSOR_OPENAI_BRIDGE_API_KEY`，则 `Authorization: Bearer`（与现有 `/v1/*` 一致）。

### `GET /v1/skills`

列出已安装 skill：扫描 `CURSOR_SKILLS_DIR` 下一级目录，解析 `SKILL.md` frontmatter 的 `name` / `description`（缺则标记 `invalid`）。

响应示例：

```json
{
  "skills": [
    {
      "name": "hv-analysis",
      "description": "...",
      "path": "/root/.cursor/skills/hv-analysis",
      "valid": true
    }
  ]
}
```

### `GET /v1/skills/{name}`

返回元数据；查询参数 `include_body=1` 时附带 `SKILL.md` 正文。

### `POST /v1/skills/install`

按标识/路径确定性安装。

```json
{
  "source": "path" | "git" | "url",
  "ref": "/abs/or/rel/path/or/git-url/or-archive-url",
  "name": "optional-folder-name",
  "overwrite": false
}
```

行为：

| source | 行为 |
|--------|------|
| `path` | 从本地目录拷贝；目录内必须有 `SKILL.md` |
| `git` | `git clone --depth 1`（需 `ALLOW_REMOTE=1`）；若仓库根无 `SKILL.md`，可约定 `ref` 带 `#subdir` 或 body 字段 `subdir`（实现时二选一，推荐 body `subdir`） |
| `url` | 下载 zip/tarball（需 `ALLOW_REMOTE=1`），解压后定位含 `SKILL.md` 的根 |

校验：

- `name`（目标文件夹）：`^[a-z0-9]+(-[a-z0-9]+)*$`，与 frontmatter `name` 不一致时以**文件夹名为准并改写 frontmatter `name`**，或直接 400（推荐 **400**，要求调用方对齐）
- `overwrite: false` 且已存在 → `409`
- 禁止路径穿越（`..`）；拷贝只落在 skills 根下一级
- 失败时不留下半成品目录（先写临时目录再 `rename`）

本期**不**实现 `source: "builtin"`。

### `POST /v1/skills/generate`

按自然语言生成并落盘。

```json
{
  "prompt": "……",
  "name": "my-skill",
  "overwrite": false
}
```

行为：

1. 校验 `name`；`overwrite: false` 且已存在 → `409`
2. 在临时 job 目录调用 `run_cursor_agent`（writable，不传 `--mode ask`），prompt 要求产出符合 Cursor Skill 规范的目录（至少 `SKILL.md` + frontmatter）
3. 校验产物 → 原子移入 `CURSOR_SKILLS_DIR/<name>`
4. 超时/失败返回 502/500，正式目录不变

响应：与 `GET` 单条结构类似，含 `name` / `description` / `path`。

### `DELETE /v1/skills/{name}`

删除 `CURSOR_SKILLS_DIR/<name>`（仅允许 skills 根下一级目录名）。成功 `204` 或 `{"ok": true}`（与现有 bridge 错误风格对齐，实现时统一）。

## 与 chat / agent 的关系

- **不修改** `/v1/chat/completions` 与 `/v1/messages` 的核心逻辑（除增加 skill 使用情况日志，见下节）。
- Agent 启动后应能发现 `/root/.cursor/skills` 下的 skills；文档说明可用 `/skill-name` 显式触发。
- `ISOLATE_WORKSPACE=1` 保持默认开启；全局 skills 不依赖关闭隔离。
- 本期不做 `metadata.skills` 强制注入（可选后续）；若后续加入，计入下方「请求侧」信号。

## 执行日志：是否使用了 Skill

每次走 `run_cursor_agent` 的请求（至少 `/v1/chat/completions`、`/v1/messages`；生图路径可选同等打点）在 **业务日志**（`cursor_openai_bridge`）中必须输出 skill 使用结论，便于检索。

### 日志字段（建议单行 INFO）

```text
id=<req_id> skill_usage=none|requested|evidenced|requested+evidenced
  requested=[...] evidenced=[...] installed_count=N
```

| 字段 | 含义 |
|------|------|
| `skill_usage` | 汇总标签，便于 grep |
| `requested` | 请求侧明确点名的 skill 名列表 |
| `evidenced` | 从 agent 输出/工具轨迹推断「实际动用」的 skill 名列表 |
| `installed_count` | 当时全局目录下合法 skill 数量（上下文） |

示例：

```text
id=abc skill_usage=requested+evidenced requested=[hv-analysis] evidenced=[hv-analysis] installed_count=3
id=def skill_usage=none requested=[] evidenced=[] installed_count=3
id=ghi skill_usage=requested requested=[hv-analysis] evidenced=[] installed_count=3
```

`requested` 有而 `evidenced` 空：用户点了名，但日志未能证明 agent 读了该 skill（仍打 `requested`，不要静默丢掉）。

### 检测规则（实现必须可测）

**请求侧 `requested`（确定性）：**

1. 扫描最终发给 agent 的 prompt 文本：匹配 `/<name>`，且 `<name>` 在当前已安装合法 skill 集合中。
2. （若将来支持）`metadata.skills: ["a","b"]` 并入 `requested`。

**证据侧 `evidenced`（启发式，CLI 无稳定官方字段时的务实方案）：**

对 `stdout` / `stderr` / `parsed`（json 或 stream-json 行）做文本/路径扫描，命中任一即计入对应 `name`：

1. 路径片段：`.cursor/skills/<name>/` 或 `.agents/skills/<name>/`
2. 文件名：`.../skills/<name>/SKILL.md`
3. stream-json 工具事件里 `path` / `args` / `input` 等字段含上述路径（实现时对常见键做递归字符串收集）

**不作为「已使用」的充分条件（可记 debug，不进 `evidenced`）：**

- 仅助手正文里口头提到 skill 名字（误报高）
- 仅 `installed_count > 0`（装了≠用了）

### 限制（写进文档，避免过度承诺）

- Cursor CLI **未必**返回结构化 `skills_used`；本期以 **requested + 路径/工具证据** 为准，不是 100% 内核级审计。
- `output_format=json` 且工具轨迹被折叠时，`evidenced` 可能偏空 → 仍保证 `requested` 与 `skill_usage` 有值。
- 流式请求：在流结束（或失败）时打同一条汇总日志，不要求每个 SSE chunk 都带。

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `CURSOR_BRIDGE_LOG_SKILL_USAGE` | `1` | `0` 关闭该汇总日志 |

## 安全

- 安装/生成/删除均走 bridge API Key。
- 默认关闭远程安装（`CURSOR_SKILLS_ALLOW_REMOTE=0`）。
- Skill 可含 `scripts/`：安装 API 视为向可信指令区写入；调用方自负；文档警告勿装不可信来源。
- 生成路径：agent 仅在临时目录写文件，网关校验后再晋升。

## 成功标准

1. Compose 启动后，向宿主机 `cursor_skills/` 放入合法 skill，经 chat/`cursor agent` 可被发现或 `/name` 触发。
2. `install`（path）与 `generate` 成功后文件出现在宿主机目录；**重启容器后仍在**，`GET /v1/skills` 仍列出。
3. `DELETE` 后列表与磁盘一致；非法 `name` / 关闭远程时的 `git`/`url` 返回明确 4xx。
4. 带 `/已知skill名` 的 chat 请求，日志出现 `skill_usage=requested`（或 `requested+evidenced`）；未点名且无路径证据时为 `skill_usage=none`。

## 非目标（本期）

- `builtin` / `skill_catalog` 预置包市场
- UI、版本回滚、多租户 skills 隔离
- 修改 Cursor CLI 发现逻辑
- 将「生成」作为唯一安装路径
- 依赖 Cursor 官方结构化 `skills_used` 字段（若日后 CLI 提供，再升级 `evidenced`）

## 实现落点（预览）

- 新模块：`macos_cursor_automation/skills_store.py`（扫描、校验、install、原子晋升）
- 新模块或同文件：`skill_usage.py`（从 prompt + agent 输出推断 `requested` / `evidenced`）
- `openai_bridge.create_app` 注册 skills 路由；chat/messages 结束路径打 skill 使用日志
- `docker-compose.yml` + `.env.example` + `cursor_skills/README.md`
- 单测：name 校验、path 安装、overwrite 409、路径穿越拒绝；skill_usage 解析（`/name`、路径证据、none）

## 已确认选项

- 方案 A + 安装与生成双接口
- `install` 本期：`path` / `git` / `url`；不做 `builtin`
- 执行日志须输出是否使用了 skill（`requested` / `evidenced` 双轨）
