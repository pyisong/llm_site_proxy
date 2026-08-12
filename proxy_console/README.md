# Proxy Console

运维控制台：Overview 大屏、Connectivity 联通性、Cursor Skills 管理。设计见
`docs/superpowers/specs/2026-08-05-proxy-console-design.md`。

## 一键启动（推荐）

在 `llm_site_proxy` 根目录：

```bash
# 仅启动控制台 + Postgres（不碰已在跑的其他服务时可用 --no-deps 以外的单独 up）
docker compose up -d --build proxy-console-db proxy-console

# 或与全部代理一起
docker compose up -d --build
```

控制台：http://\<host\>:18020/  
Postgres 宿主机映射：`5433`（容器内 `5432`）

> 若栈里已有运行中的 worker/proxy，请自行决定是否 `up`；本仓库规则下代理不会替你重启业务容器。

## 本地开发

需要本机 Postgres（或先 `docker compose up -d proxy-console-db`）：

```bash
# DB
docker compose up -d proxy-console-db

# 后端
cd proxy_console/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CONSOLE_DATABASE_URL=postgresql://console:console@127.0.0.1:5433/proxy_console \
  CONSOLE_PORT=18020 python main.py

# 前端
cd ../frontend && npm install && npm run dev
```

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `CONSOLE_PORT` | `18020` | 控制台端口 |
| `CONSOLE_DATABASE_URL` | compose 内指向 `proxy-console-db` | Postgres DSN |
| `CONSOLE_DB_USER/PASSWORD/NAME` | `console` / `console` / `proxy_console` | 库账号 |
| `CONSOLE_DB_PORT` | `5433` | 宿主机映射端口 |
| `CONSOLE_PROBE_MODE` | compose 默认 `docker` | `docker` 走服务 DNS；本地可用 `host` |
| `CONSOLE_CHAT_PROBE_MESSAGE` | `hi` | 真实联通/保活聊天内容 |
| `CONSOLE_CHAT_PROBE_TIMEOUT` | `600` | 聊天探测超时（秒，browser proxy 较慢） |
| `CONSOLE_KEEPALIVE_IDLE_SEC` | `172800`（2 天） | 无请求超过此时长才自动发聊天保活 |
| `CONSOLE_KEEPALIVE_CHECK_INTERVAL` | `3600` | 多久检查一次是否需要保活（秒） |
| `CONSOLE_SEED_DEMO` | `1` | 空库时写入演示数据 |
| `CONSOLE_CURSOR_BRIDGE_URL` | `http://cursor-openai-bridge:8765` | Skills 透传 |
| `CONSOLE_SECRETS_ROOT` | `/secrets` | 各 proxy storage 挂载根目录 |
| `CONSOLE_LOGIN_HTTP_PROXY` | （空） | 登录浏览器出站代理（换出口用） |

## 网页刷新登录态

Connectivity → 各 Cookie 系 proxy（DeepSeek / Kimi / StepFun / Qwen / Metaso）→ **网页刷新登录**：

1. Console 在服务端开 Playwright，画面经 WebSocket 投屏到弹窗（非 iframe 嵌第三方站）
2. 你在画面里完成登录后点「保存登录态」
3. 自动写入对应 `secrets/*_storage.json`；Metaso 会尝试 `POST /v1/admin/reload-storage` 热加载（**不重启容器**）
4. 其他 proxy 若进程内缓存了登录态，可能仍需你手动同意后重启对应容器

需要重建 `proxy-console` 镜像（含 Chromium）。compose 已把各 `*_openai_proxy/secrets` 以 RW 挂到 `/secrets/<name>`。

Postgres 卷：`proxy_console_pgdata`。表结构由控制台启动时 `CREATE TABLE IF NOT EXISTS` 自动建。
