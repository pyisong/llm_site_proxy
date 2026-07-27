# Proxy Catalog 服务发现设计

日期：2026-07-27

## 目标

在 `llm_site_proxy` 提供独立 catalog 服务，供 `wechat_article_agent` / `vtok_ai_factory` 等应用查询当前有哪些 proxy API 可用，并报告 LLM / 生图 / 生视频 / TTS 等能力。

## 决策

| 项 | 选择 |
|----|------|
| 探测方式 | 请求时并行实时探测各服务 `/health` |
| 部署形态 | 独立 FastAPI 小服务 `proxy_catalog` |
| 返回内容 | 服务级能力 + online 时 `/v1/models` + `by_capability` 聚合 |
| base_url | 宿主机可达地址（`CATALOG_PUBLIC_HOST` + 公开端口） |
| 对外端口 | `18010`（避免与 vtok 前端 `18001` 冲突） |

## 接口

- `GET /health` — catalog 自身
- `GET /v1/services` — 完整发现结果
- `GET /v1/services?capability=image` — 按能力过滤（过滤 `services` 与 `by_capability`）

## 探测

- 容器内用 Docker 服务名探测健康（如 `http://deepseek-openai-proxy:8000/health`）
- 响应里的 `base_url` 用 `http://{CATALOG_PUBLIC_HOST}:{public_port}/v1`（TTS 无 `/v1` 前缀）
- online 且声明 `llm` 时再拉 models；失败则 `models: []`，status 仍为 online

## 范围外

- 本轮不改业务应用去自动消费该接口
