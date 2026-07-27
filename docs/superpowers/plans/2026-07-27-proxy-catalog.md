# Proxy Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 独立 `proxy_catalog` 服务，实时探测并返回所有 proxy 的能力 / 模型 / 按能力聚合视图。

**Architecture:** FastAPI + httpx 并行探测；静态注册表描述能力与端口；公开 base_url 用 `CATALOG_PUBLIC_HOST`。

**Tech Stack:** Python 3.11、FastAPI、uvicorn、httpx、pytest

---

### Task 1: Catalog 核心服务

**Files:**
- Create: `proxy_catalog/registry.py`
- Create: `proxy_catalog/app.py`
- Create: `proxy_catalog/main.py`
- Create: `proxy_catalog/requirements.txt`
- Create: `proxy_catalog/Dockerfile`
- Create: `proxy_catalog/tests/test_catalog.py`

- [ ] 实现注册表与并行探测
- [ ] 单测覆盖 online/offline、capability 过滤、by_capability
- [ ] 接入 `docker-compose.start-all.yml`，端口 18010
- [ ] 更新 README
