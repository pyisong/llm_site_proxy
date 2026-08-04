# llm_site_proxy

统一管理各站点 OpenAI 兼容代理与周边网关（DeepSeek / Kimi / StepFun / Qwen / Cursor Bridge / Azure TTS），并通过 **proxy-catalog** 对外提供服务发现接口。

## 一键启动全部代理

```bash
cd llm_site_proxy
# 建议设置对外可达 IP，供其他机器/应用识别 base_url
export CATALOG_PUBLIC_HOST=10.1.10.113
docker-compose -f docker-compose.yml up -d --build

docker-compose up -d --build deepseek-openai-proxy
```

## 服务发现（推荐）

Catalog 监听宿主机 **`18010`**：

```bash
curl -s http://10.1.10.113:18010/v1/services | python3 -m json.tool
# 仅看能生图的服务
curl -s 'http://10.1.10.113:18010/v1/services?capability=image' | python3 -m json.tool
```

返回包含：

- `services[]`：每个 proxy 的 `status`（online/offline）、公开 `base_url`、`capabilities`（`llm` / `image` / `video` / `tts`）、端点与 `models`
- `by_capability`：按能力聚合的在线服务列表

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `CATALOG_PUBLIC_HOST` | `10.1.10.113` | 写入响应 `base_url` 的宿主机 IP/域名 |
| （响应字段）`internal_base_url` | Docker DNS | 同 `llm_site_proxy_net` 内用服务名，如 `http://cursor-openai-bridge:8765/v1` |
| `CATALOG_PORT` | `18010` | catalog 对外端口 |
| `CATALOG_PROBE_TIMEOUT` | `2.0` | 单次健康/models 探测超时（秒） |

## 默认端口

| 服务 | 端口 |
|------|------|
| proxy-catalog | 18010 |
| deepseek-openai-proxy | 18002 |
| kimi-openai-proxy | 18003 |
| stepfun-openai-proxy | 18004 |
| qwen-openai-proxy | 18005 |
| cursor-openai-bridge | 8765 |
| azure-tts-http-api | 8787 |

各子目录仍可单独 `docker compose up`；生产推荐只用根目录 `docker-compose.start-all.yml`。

## 忽略规则

仓库根目录 `.gitignore` 已汇总各子项目规则。本地运行 `save_storage_state` 产生的 `*-browser-profile-export/`（Chromium 用户数据目录）以及 `secrets/*_storage.json`、`.env` 等不会进入 git；磁盘上仍可正常使用。
