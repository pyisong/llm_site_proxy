# Cursor Bridge Global Skills Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist global Cursor skills under `llm_site_proxy/cursor_skills/`, mount them to `/root/.cursor/skills`, expose install/generate/list/delete APIs, and log whether each chat/messages run requested or evidenced skill use.

**Architecture:** Pure-Python `skills_store.py` owns the skills directory (scan, validate, atomic install/promote, delete). `skill_usage.py` infers `requested` / `evidenced` from the final agent prompt and agent stdout/stderr/parsed. FastAPI routes in `openai_bridge.create_app` expose `/v1/skills*`; chat/messages completion paths call a small logger helper. Docker Compose bind-mounts the host dir to `/root/.cursor/skills`.

**Tech Stack:** Python 3.12, FastAPI, unittest (existing style), docker-compose; optional `git`/`httpx` for remote install when `CURSOR_SKILLS_ALLOW_REMOTE=1`.

**Spec:** `llm_site_proxy/docs/superpowers/specs/2026-08-04-cursor-skills-global-store-design.md`

## Global Constraints

- Mount target for discovery: `/root/.cursor/skills` (user-level); host path `llm_site_proxy/cursor_skills/`
- Install sources this phase: `path` | `git` | `url` only — **no `builtin`**
- Skill folder `name`: `^[a-z0-9]+(-[a-z0-9]+)*$`; frontmatter `name` must match folder or return 400
- `CURSOR_SKILLS_ALLOW_REMOTE` default `0`; remote install returns 403 when off
- Atomic install: write temp dir under skills root (or system temp), validate, then `os.replace` / rename into final name; no half-written final dirs
- Skill usage log default on (`CURSOR_BRIDGE_LOG_SKILL_USAGE=1`); fields: `skill_usage`, `requested`, `evidenced`, `installed_count`
- Do not restart running Docker services unless the user explicitly asks
- Match existing bridge style: unittest, no new heavy deps unless needed; auth already covers `/v1/*`
- Surgical diffs: do not refactor unrelated bridge code

## File map

| File | Responsibility |
|------|----------------|
| `llm_site_proxy/cursor_skills/.gitkeep` + `README.md` | Persistent volume root + usage notes |
| `macos_cursor_automation/skills_store.py` | Dir resolve, list, get, install path/git/url, generate promote, delete, name validation, frontmatter parse |
| `macos_cursor_automation/skill_usage.py` | Infer requested/evidenced; format log line |
| `macos_cursor_automation/openai_bridge.py` | Register `/v1/skills*` routes; after agent runs log skill usage |
| `macos_cursor_automation/Dockerfile` | COPY new modules if needed |
| `macos_cursor_automation/docker-compose.yml` | Volume + env for skills |
| `macos_cursor_automation/.env.example` | Document new env vars |
| `macos_cursor_automation/README.md` | Short section: skills API + how to use + logging |
| `macos_cursor_automation/tests/test_skills_store.py` | Store unit tests |
| `macos_cursor_automation/tests/test_skill_usage.py` | Usage inference unit tests |
| `llm_site_proxy/.gitignore` | Ignore `cursor_skills/*` except `.gitkeep` / `README.md` |

---

### Task 1: `skills_store` core (list / validate / path install / delete)

**Files:**
- Create: `macos_cursor_automation/skills_store.py`
- Create: `macos_cursor_automation/tests/test_skills_store.py`
- Create: `llm_site_proxy/cursor_skills/.gitkeep`
- Create: `llm_site_proxy/cursor_skills/README.md`

**Interfaces:**
- Produces:
  - `skills_root() -> Path` — from `CURSOR_SKILLS_DIR` or default `/root/.cursor/skills` (also accept override arg for tests)
  - `validate_skill_name(name: str) -> str` — raises `ValueError` if invalid
  - `parse_skill_md(path: Path) -> dict` — at least `name`, `description`, `valid: bool`
  - `list_skills(root: Path | None = None) -> list[dict]`
  - `get_skill(name: str, *, include_body: bool = False, root: Path | None = None) -> dict | None`
  - `install_from_path(src: Path, *, name: str | None, overwrite: bool, root: Path | None = None) -> dict`
  - `delete_skill(name: str, root: Path | None = None) -> bool`
  - `SkillStoreError` with `.status_code` hint (400/403/409/404) for HTTP mapping

- [ ] **Step 1: Write failing tests for name validation, list, path install, overwrite 409, path traversal, delete**

```python
# tests/test_skills_store.py (sketch)
def test_validate_skill_name_rejects_traversal():
    with self.assertRaises(ValueError):
        validate_skill_name("../x")

def test_install_from_path_copies_skill(tmp):
    # create src/foo/SKILL.md with matching frontmatter
    # install_from_path -> list_skills contains foo
```

- [ ] **Step 2: Run tests — expect FAIL (module missing)**

Run: `cd macos_cursor_automation && python3 -m unittest tests.test_skills_store -v`

- [ ] **Step 3: Implement `skills_store.py` (path install + delete + list only; stub git/url raising NotImplemented or clear error until Task 2)**

- [ ] **Step 4: Re-run tests — expect PASS**

- [ ] **Step 5: Add `cursor_skills/.gitkeep` + README (mount + how to use `/skill-name`); update root `.gitignore`**

- [ ] **Step 6: Commit** (only if user asked to commit; otherwise stop and note ready)

---

### Task 2: Remote install (`git` / `url`) gated by env

**Files:**
- Modify: `macos_cursor_automation/skills_store.py`
- Modify: `macos_cursor_automation/tests/test_skills_store.py`

**Interfaces:**
- Produces:
  - `install_from_git(ref: str, *, name: str | None, subdir: str | None, overwrite: bool, root: Path | None = None) -> dict`
  - `install_from_url(url: str, *, name: str | None, overwrite: bool, root: Path | None = None) -> dict`
  - `remote_allowed() -> bool` — `CURSOR_SKILLS_ALLOW_REMOTE` in `1/true/yes`
- Consumes: Task 1 atomic promote helper

- [ ] **Step 1: Tests — remote disabled → error status 403; with env + mocked clone/download → success**

Prefer mocking `subprocess.run` / download function; do not hit network in CI.

- [ ] **Step 2: Implement git clone `--depth 1` into temp; optional `subdir`; validate SKILL.md; promote**

- [ ] **Step 3: Implement url download (zip preferred) with size/timeout caps from env; extract; find SKILL.md root; promote**

- [ ] **Step 4: Tests PASS**

- [ ] **Step 5: Commit if requested**

---

### Task 3: Generate skill via agent + atomic promote

**Files:**
- Modify: `macos_cursor_automation/skills_store.py`
- Create helper prompt builder in same file or `skills_store.py`
- Modify: `macos_cursor_automation/tests/test_skills_store.py` (mock `run_cursor_agent`)

**Interfaces:**
- Produces:
  - `generate_skill(prompt: str, *, name: str, overwrite: bool, root: Path | None = None, run_agent: Callable | None = None) -> dict`
- Consumes: `run_cursor_agent` from `cursor_automation` (injectable for tests)

- [ ] **Step 1: Failing test — mock agent that writes `tmp/.../name/SKILL.md`; `generate_skill` promotes to root**

- [ ] **Step 2: Implement generate: temp workspace → agent writable prompt requiring SKILL.md under `./<name>/` → validate → promote; failures leave root unchanged**

- [ ] **Step 3: Tests PASS**

- [ ] **Step 4: Commit if requested**

---

### Task 4: `skill_usage` inference + log line

**Files:**
- Create: `macos_cursor_automation/skill_usage.py`
- Create: `macos_cursor_automation/tests/test_skill_usage.py`

**Interfaces:**
- Produces:
  - `dataclass SkillUsageResult: requested: list[str]; evidenced: list[str]; installed_count: int; label: str`  # none|requested|evidenced|requested+evidenced
  - `infer_skill_usage(prompt: str, *, agent_stdout: str = "", agent_stderr: str = "", parsed: Any = None, installed_names: Iterable[str]) -> SkillUsageResult`
  - `format_skill_usage_log(req_id: str, usage: SkillUsageResult) -> str`
- Rules per spec: `/name` in prompt ∩ installed; evidenced via `.cursor/skills/<name>/` or `SKILL.md` path in text/parsed

- [ ] **Step 1: Write failing tests for none / requested / evidenced / both**

- [ ] **Step 2: Implement minimal parser (regex for `/name`, path fragments; recurse dict/list for string values in parsed)**

- [ ] **Step 3: Tests PASS**

- [ ] **Step 4: Commit if requested**

---

### Task 5: Wire FastAPI `/v1/skills*` + chat/messages logging

**Files:**
- Modify: `macos_cursor_automation/openai_bridge.py`
- Modify: `macos_cursor_automation/Dockerfile` (COPY `skills_store.py` `skill_usage.py`)
- Optional thin tests with FastAPI TestClient if fastapi already in requirements (prefer one smoke test file `tests/test_skills_api.py` mocking store)

**Interfaces:**
- Routes:
  - `GET /v1/skills`
  - `GET /v1/skills/{name}?include_body=0|1`
  - `POST /v1/skills/install` body `{source, ref, name?, overwrite?, subdir?}`
  - `POST /v1/skills/generate` body `{prompt, name, overwrite?}`
  - `DELETE /v1/skills/{name}`
- After successful/failed agent completion for chat + messages (stream end included): if `CURSOR_BRIDGE_LOG_SKILL_USAGE` not `0`, log `format_skill_usage_log(...)`

- [ ] **Step 1: Add routes mapping `SkillStoreError` → JSONResponse with appropriate status**

- [ ] **Step 2: Hook `_log_skill_usage(req_id, prompt, result)` in non-stream and stream completion paths for chat + messages**

- [ ] **Step 3: Manual or TestClient smoke: list empty; install path; get; delete; generate with mock if feasible**

- [ ] **Step 4: Update Dockerfile COPY list**

- [ ] **Step 5: Commit if requested**

---

### Task 6: Compose mount, env example, README usage

**Files:**
- Modify: `macos_cursor_automation/docker-compose.yml`
- Modify: `macos_cursor_automation/.env.example`
- Modify: `macos_cursor_automation/README.md` (skills section: mount, API, `/skill-name` usage, log fields)
- Optional one-line pointer in `llm_site_proxy/README.md`

- [ ] **Step 1: Uncomment/add volume `../cursor_skills:/root/.cursor/skills:rw` and env `CURSOR_SKILLS_DIR=/root/.cursor/skills`, `CURSOR_SKILLS_ALLOW_REMOTE`, `CURSOR_BRIDGE_LOG_SKILL_USAGE`**

- [ ] **Step 2: Document host-native softlink option: `ln -s .../cursor_skills ~/.cursor/skills`**

- [ ] **Step 3: Do not `docker compose up` / restart unless user asks; note “挂载需重建容器后生效，请用户确认后再重启”**

---

### Task 7: Verification checklist

- [ ] Run: `python3 -m unittest discover -s tests -v` under `macos_cursor_automation`
- [ ] Confirm no service restart performed without approval
- [ ] Grep log format string exists in bridge path
- [ ] Spec success criteria 1–4 covered by tests or documented manual steps

---

## Execution notes for agents

- Prefer **subagent-driven-development** with review between tasks.
- Do not restart `booktok_*` / celery / this bridge container unless the user says so in-session.
- Keep commits optional: user rules require explicit commit requests — skip commit steps unless asked.
