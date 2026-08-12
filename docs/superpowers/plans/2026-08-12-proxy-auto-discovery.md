# Proxy Auto-Discovery Implementation Plan

**Date:** 2026-08-12  
**Spec:** `docs/superpowers/specs/2026-08-12-proxy-auto-discovery-design.md`

## Done in this pass

1. **Catalog** (`proxy_catalog/registry.py` + `app.py`): `route_kind`, `short_name`, `ui_schema`, `session`, `models[].capabilities`
2. **Consumers** (wechat + vtok `proxy_catalog.py`): `discover_models`, `resolve_model_route`, enriched snapshot
3. **Routing / dropdown**: generation_models + llm_options prefer catalog discovery; unknown `foo-chat-web` covered by test
4. **Frontend**: IntegrationsPage + LlmConfigForm render status/ui_schema dynamically; Skills use `route_kind`

## Follow-ups (optional)

- Generic session binder from catalog `session` (still using per-site session modules for known proxies)
- Persist arbitrary ui_schema keys without dedicated DB columns
- Rebuild/reload: `proxy-catalog`, wechat API, vtok API (ask before restart)
