# Proxy Auto-Discovery (A+B) Design

**Date:** 2026-08-12  
**Status:** Approved for implementation

## Goal

Adding a new OpenAI-compatible web proxy should require changes only in `llm_site_proxy` (registry + compose). `wechat_article_agent` and `vtok_ai_factory` must discover models, route requests, and render secondary UI from the proxy catalog—no per-site constants/routing/frontend prefixes.

## Catalog contract

`GET /v1/services` each service includes:

| Field | Meaning |
|-------|---------|
| `route_kind` | `web_proxy` \| `cursor_bridge` \| `tts` |
| `ui_schema` | Optional secondary option fields (select/boolean) |
| `session` | Optional `{ metadata_key, supports_new_chat }` |
| `models[].capabilities` | e.g. `["llm"]`, `["image"]`, `["video"]` |

`ui_schema.fields[]`: `key` (settings field), `request_key` (extra_body), `label`, `type`, `options`, `default`.

Schema lives in `proxy_catalog/registry.py` alongside `SERVICES`.

## Consumers

- LLM dropdown: online `web_proxy` models with capability `llm`
- Image/video dropdowns: models with those capabilities
- Resolve `(base_url, api_key)` by model→owning online service
- Settings API exposes `llm_model_meta` / per-service `ui_schema`
- Frontend: dynamic status strip + dynamic form fields; Cursor vs web from `route_kind`

## Compatibility

Keep existing DeepSeek/Kimi/StepFun/Qwen/Metaso session helpers and DB columns whose `key` matches schema. New proxies without schema still list + route; without session metadata they are single-shot until schema is added.

## Success

Register `foo-openai-proxy` in registry only → catalog online → both apps show its chat models and route correctly with zero app code changes.
