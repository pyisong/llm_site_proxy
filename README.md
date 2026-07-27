# llm_site_proxy

统一管理各站点 OpenAI 兼容代理与周边网关（DeepSeek / Kimi / StepFun / Qwen / Cursor Bridge / Azure TTS），并通过 **proxy-catalog** 对外提供服务发现接口。

## 一键启动全部代理

```bash
cd llm_site_proxy
# 建议设置对外可达 IP，供其他机器/应用识别 base_url
export CATALOG_PUBLIC_HOST=10.1.10.113
docker-compose -f docker-compose.start-all.yml up -d --build
```

## 服务发现（推荐）

Catalog 监听宿主机 **`18010`**：

```bash
curl -s http://127.0.0.1:18010/v1/services | python3 -m json.tool
# 仅看能生图的服务
curl -s 'http://127.0.0.1:18010/v1/services?capability=image' | python3 -m json.tool
```

返回包含：

- `services[]`：每个 proxy 的 `status`（online/offline）、公开 `base_url`、`capabilities`（`llm` / `image` / `video` / `tts`）、端点与 `models`
- `by_capability`：按能力聚合的在线服务列表

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `CATALOG_PUBLIC_HOST` | `127.0.0.1` | 写入响应 `base_url` 的宿主机 IP/域名 |
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
