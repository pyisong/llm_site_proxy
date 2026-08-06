# Proxy Console v2.0 设计

日期：2026-08-05

## 目标

为 `llm_site_proxy` 提供统一运维控制台，覆盖：

1. 各 proxy 调用统计与请求详情（大屏总览 + 下钻）
2. 各 proxy / 各模式联通性测试
3. 非 Cursor proxy 定时保活；登录失败时页面引导刷新登录态
4. Cursor Skills：列表、安装/下载、删除、禁用、使用情况
5. PostgreSQL 持久化（compose 服务 `proxy-console-db`）


## Design Read

运维控制台 + 数据大屏，内部运维受众；深色 cockpit；`design-taste-frontend` 只约束视觉纪律（反 AI-slop、单强调色、主题锁定），布局按数据 UI（非营销落地页）。

| Dial | Value | Reason |
|------|-------|--------|
| DESIGN_VARIANCE | 5 | 大屏可有不对称信息块；管理页保持可扫读网格 |
| MOTION_INTENSITY | 5 | 状态刷新、入场 stagger；无 scroll-hijack |
| VISUAL_DENSITY | 8 | 大屏与运维台信息密度优先 |

视觉：off-black zinc 底 + 单一 accent（electric cyan `#2BB8C8`）+ 语义色仅用于状态（ok/warn/fail）。字体：Geist Sans + Geist Mono。圆角统一 8px。

## 方案选择（已定）

**方案 C-console（推荐，已执行）**

- 新建服务 `proxy_console`：FastAPI BFF + Vite/React SPA
- 路由：`/` 大屏 Overview；`/connectivity`；`/skills`；请求详情为 Overview 侧栏/抽屉
- 数据：PostgreSQL（`CONSOLE_DATABASE_URL`）；聚合各 proxy 的请求日志（ingest API）与连通性结果
- Skills：BFF 代理 Cursor Bridge `/v1/skills*`；禁用态落在 console DB（`skill_disabled`），列表合并展示
- 保活：BFF 后台 asyncio 定时对非 cursor 服务打轻量探针（health + 可选 chat ping）；失败写 `auth_status=login_required`
- 部署：根目录 `docker-compose.yml` 含 `proxy-console` + `proxy-console-db`

备选未选：

- A 仅大屏静态页：无法完成登录/Skills 操作
- B 塞进 proxy-catalog：catalog 保持只读发现，避免职责膨胀

## 信息架构

```
Overview（大屏）     Connectivity（运维）     Skills（运维）
  KPI 条               服务×模式矩阵              列表+用量
  服务状态条带         手动探测 / 保活开关         安装 / 删 / 禁用
  时序吞吐             登录态 + 刷新引导           使用明细
  最近请求 → 抽屉
```

## API（BFF）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/overview` | KPI + 服务摘要 + 近时序 |
| GET | `/api/requests` | 分页请求列表；`?proxy=&limit=` |
| GET | `/api/requests/{id}` | 单条详情 |
| POST | `/api/ingest/request` | 各 proxy 上报（或 console 旁路采样） |
| GET | `/api/connectivity` | 最新连通性矩阵 |
| POST | `/api/connectivity/probe` | `{proxy_id?, mode?}` 触发探测 |
| GET | `/api/auth-status` | 各 proxy 登录/保活状态 |
| POST | `/api/auth/{proxy_id}/mark-refreshed` | 运维确认已重新导出 storage |
| GET | `/api/skills` | Bridge skills + disabled + usage 汇总 |
| POST | `/api/skills/install` | 透传 Bridge |
| DELETE | `/api/skills/{name}` | 透传 Bridge |
| POST | `/api/skills/{name}/disable` | console DB 标记禁用 |
| POST | `/api/skills/{name}/enable` | 取消禁用 |
| GET | `/api/skills/{name}/usage` | 用量明细（ingest + Bridge 日志解析落库） |

静态资源：`/` 挂载 frontend build。

## 数据模型（PostgreSQL）

- `request_events`：id, proxy_id, mode, path, status_code, latency_ms, model, error, created_at, meta_json
- `connectivity_results`：id, proxy_id, mode, ok, latency_ms, detail, created_at
- `auth_status`：proxy_id PK, state (`ok`/`login_required`/`unknown`), last_ok_at, last_fail_at, message
- `skill_disabled`：name PK, disabled_at, reason
- `skill_usage_events`：id, skill_name, label, request_id, created_at

## 保活与登录

- 每小时检查一次（`CONSOLE_KEEPALIVE_CHECK_INTERVAL`，默认 3600s）
- 仅当该 proxy 在 `CONSOLE_KEEPALIVE_IDLE_SEC`（默认 2 天）内 **无任何** `request_events` 时，才发真实聊天（默认内容 `hi`）
- 跳过 `cursor-openai-bridge`；TTS 发短文本 `/tts`
- 探测结果写入 `request_events`（meta.source=`keepalive_probe`），因此成功后 2 天内不再自动探
- Probe 失败且错误含 login/sign_in/storage/401 → `login_required`
- UI：失败卡片展示「重新导出 storage state」操作说明 + 「我已刷新」确认按钮（写回 `ok` 需下次探针验证）

## 前端页面结构

- Shell：顶栏单行导航（Overview / Connectivity / Skills），高度 ≤72px
- Overview：全宽 KPI + 服务条带 + 双栏（吞吐图 | 最近请求）
- Connectivity：矩阵表（非三等分卡片），行=proxy，列=mode
- Skills：主列表 + 右侧详情（用量、禁用状态）

## 非目标（本期）

- 多用户权限 / SSO
- 真实 chat 内容全文归档
- Cursor 账号网页登录自动化（仅展示状态与人工刷新引导）

## 成功标准

1. 打开 Overview 可在一屏看到各 proxy 在线/吞吐/错误率
2. Connectivity 可手动探测并看到结果落库
3. Skills 可列表、安装、删除、禁用/启用、看用量
4. 数据重启后仍在（Postgres volume `proxy_console_pgdata`）
5. 主题深色锁定；无营销页 AI-slop 默认套路
