# Proxy Console Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Ship `proxy_console` BFF + dark ops SPA covering Overview 大屏, Connectivity, Skills, SQLite persistence.

**Architecture:** FastAPI serves `/api/*` and built SPA. React+Vite+Tailwind frontend. Background keepalive task for non-cursor proxies. Skills proxy to Cursor Bridge.

**Tech Stack:** Python 3.11+, FastAPI, aiosqlite, httpx, React 19, Vite, Tailwind v4, Motion, Phosphor icons

## Global Constraints

- No docker restart without user approval
- Chinese UI copy; no em-dash in UI strings
- One accent cyan; dark theme locked
- Cards only where interaction needs a container

## File map

```
proxy_console/
  backend/
    main.py           # FastAPI app + static mount
    db.py             # schema + queries
    keepalive.py      # scheduled probes
    catalog_client.py # probe helpers
    skills_proxy.py   # bridge forward
    requirements.txt
  frontend/
    package.json
    vite.config.ts
    index.html
    src/main.tsx
    src/App.tsx
    src/styles.css
    src/api.ts
    src/components/*
    src/pages/*
  Dockerfile
```

---

### Task 1: Backend DB + API skeleton

- [x] Create schema and CRUD in `db.py`
- [x] Wire FastAPI routes in `main.py` with seed/demo data if empty
- [x] Add keepalive loop
- [x] `pip install` + smoke `GET /api/overview`

### Task 2: Frontend shell + Overview

- [x] Scaffold Vite React TS + Tailwind v4
- [x] Shell nav + Overview page (KPI, strip, chart, requests drawer)
- [x] Wire to `/api/overview` and `/api/requests`

### Task 3: Connectivity + Skills pages

- [x] Connectivity matrix + probe actions + auth refresh UX
- [x] Skills list/install/delete/disable/usage
- [x] Production build served by FastAPI

### Task 4: Compose + README

- [x] Dockerfile + compose service (document; do not restart running stack)
- [x] Short README for local `uvicorn` + `npm run dev`
