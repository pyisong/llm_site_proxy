"""全局 Cursor Skills 目录：扫描、校验、安装、生成晋升、删除。

根目录由 ``CURSOR_SKILLS_DIR`` 决定（默认 ``/root/.cursor/skills``）。
远程安装受 ``CURSOR_SKILLS_ALLOW_REMOTE`` 门控（默认关闭）。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


class SkillStoreError(Exception):
    """可映射为 HTTP 状态的 skills 操作错误。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def skills_root(override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    raw = (os.environ.get("CURSOR_SKILLS_DIR") or "/root/.cursor/skills").strip()
    return Path(raw).expanduser().resolve()


def remote_allowed() -> bool:
    return os.environ.get("CURSOR_SKILLS_ALLOW_REMOTE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _remote_timeout() -> float:
    try:
        return max(5.0, float(os.environ.get("CURSOR_SKILLS_REMOTE_TIMEOUT", "1200")))
    except ValueError:
        return 1200.0


def _max_bytes() -> int:
    try:
        return max(1024, int(os.environ.get("CURSOR_SKILLS_MAX_BYTES", str(20 * 1024 * 1024))))
    except ValueError:
        return 20 * 1024 * 1024


def validate_skill_name(name: str) -> str:
    n = (name or "").strip()
    if not n or not _SKILL_NAME_RE.fullmatch(n):
        raise SkillStoreError(
            "skill name 须匹配 ^[a-z0-9]+(-[a-z0-9]+)*$",
            status_code=400,
        )
    if ".." in n or "/" in n or "\\" in n:
        raise SkillStoreError("skill name 非法", status_code=400)
    return n


def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 SKILL.md YAML frontmatter（支持单行与 ``|`` / ``>`` 多行块）。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    lines = m.group(1).splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if ":" not in raw:
            i += 1
            continue
        # 仅接受顶层 key（无缩进），避免把块内带冒号的行当新字段
        if raw[:1] in (" ", "\t"):
            i += 1
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        if not key:
            i += 1
            continue
        # YAML block scalar: description: |  /  >
        if val in ("|", ">", "|-", ">-", "|+", ">+") or re.fullmatch(
            r"[|>][+-]?\d*", val
        ):
            block: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if nxt.strip() == "":
                    # 空行：若后续仍有缩进内容则保留为段落分隔
                    j = i + 1
                    while j < len(lines) and lines[j].strip() == "":
                        j += 1
                    if j < len(lines) and lines[j][:1] in (" ", "\t"):
                        block.append("")
                        i += 1
                        continue
                    break
                if nxt[:1] in (" ", "\t"):
                    block.append(nxt.strip())
                    i += 1
                    continue
                break
            out[key] = "\n".join(block).strip()
            continue
        out[key] = val.strip('"').strip("'")
        i += 1
    return out


def parse_skill_md(path: Path) -> dict[str, Any]:
    """解析 ``SKILL.md``，返回 name/description/valid。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"name": path.parent.name, "description": "", "valid": False}
    fm = _parse_frontmatter(text)
    name = (fm.get("name") or "").strip()
    desc = (fm.get("description") or "").strip()
    folder = path.parent.name
    valid = bool(name) and bool(desc) and name == folder and bool(_SKILL_NAME_RE.fullmatch(name))
    return {
        "name": name or folder,
        "description": desc,
        "valid": bool(valid),
        "folder": folder,
    }


def _skill_meta(skill_dir: Path, *, include_body: bool = False) -> dict[str, Any]:
    md = skill_dir / "SKILL.md"
    parsed = (
        parse_skill_md(md)
        if md.is_file()
        else {
            "name": skill_dir.name,
            "description": "",
            "valid": False,
            "folder": skill_dir.name,
        }
    )
    item: dict[str, Any] = {
        "name": skill_dir.name,
        "description": parsed.get("description") or "",
        "path": str(skill_dir.resolve()),
        "valid": bool(parsed.get("valid")),
    }
    if include_body and md.is_file():
        try:
            item["body"] = md.read_text(encoding="utf-8")
        except OSError:
            item["body"] = ""
    return item


def list_skills(root: Path | None = None) -> list[dict[str, Any]]:
    base = skills_root(root)
    if not base.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if not (child / "SKILL.md").is_file():
            items.append(
                {
                    "name": child.name,
                    "description": "",
                    "path": str(child.resolve()),
                    "valid": False,
                }
            )
            continue
        items.append(_skill_meta(child))
    return items


def installed_skill_names(root: Path | None = None) -> list[str]:
    return [s["name"] for s in list_skills(root) if s.get("valid")]


def get_skill(
    name: str,
    *,
    include_body: bool = False,
    root: Path | None = None,
) -> dict[str, Any] | None:
    n = validate_skill_name(name)
    skill_dir = skills_root(root) / n
    if not skill_dir.is_dir():
        return None
    return _skill_meta(skill_dir, include_body=include_body)


def _ensure_skill_dir_valid(skill_dir: Path, *, expected_name: str) -> None:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        raise SkillStoreError("目录内缺少 SKILL.md", status_code=400)
    parsed = parse_skill_md(md)
    if not parsed.get("valid"):
        raise SkillStoreError(
            "SKILL.md frontmatter 无效：须含 name、description，且 name 与文件夹名一致",
            status_code=400,
        )
    if parsed.get("folder") != expected_name or parsed.get("name") != expected_name:
        raise SkillStoreError(
            f"frontmatter name / 文件夹须为 {expected_name!r}",
            status_code=400,
        )


def _atomic_promote(src_skill_dir: Path, dest: Path, *, overwrite: bool) -> None:
    dest_parent = dest.parent
    dest_parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        if not overwrite:
            raise SkillStoreError(f"skill 已存在: {dest.name}", status_code=409)
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
    tmp_dest = dest_parent / f".promote_{dest.name}_{os.getpid()}"
    if tmp_dest.exists():
        shutil.rmtree(tmp_dest, ignore_errors=True)
    try:
        shutil.copytree(src_skill_dir, tmp_dest)
        os.replace(str(tmp_dest), str(dest))
    except Exception:
        shutil.rmtree(tmp_dest, ignore_errors=True)
        raise


def _resolve_skill_source_dir(extracted: Path, *, subdir: str | None) -> Path:
    if subdir:
        sub = (subdir or "").strip().lstrip("/")
        if ".." in Path(sub).parts:
            raise SkillStoreError("subdir 非法", status_code=400)
        candidate = (extracted / sub).resolve()
        if not str(candidate).startswith(str(extracted.resolve())):
            raise SkillStoreError("subdir 越界", status_code=400)
        if not candidate.is_dir():
            raise SkillStoreError(f"subdir 不存在: {subdir}", status_code=400)
        return candidate
    if (extracted / "SKILL.md").is_file():
        return extracted
    kids = [p for p in extracted.iterdir() if p.is_dir() and not p.name.startswith(".")]
    if len(kids) == 1 and (kids[0] / "SKILL.md").is_file():
        return kids[0]
    raise SkillStoreError("未找到含 SKILL.md 的目录", status_code=400)


_OWNER_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")


def normalize_skill_git_ref(ref: str) -> str:
    """把 ``npx skills add ...`` / ``owner/repo`` 规范成可解析的 git/URL ref。

    支持示例：
    - ``npx skills add alchaincyf/zhangxuefeng-skill``
    - ``npx skills add alchaincyf/zhangxuefeng-skill -a cursor``
    - ``skills add https://github.com/acme/foo.git --agent claude-code``
    - ``alchaincyf/zhangxuefeng-skill``
    - 原有的 https / git@ / github.com/... 地址（原样返回）
    """
    raw = (ref or "").strip()
    if not raw:
        raise SkillStoreError("git ref 不能为空", status_code=400)

    # 整段粘贴：npx skills add <pkg> [flags...] / skills add <pkg> ...
    m = re.match(
        r"^(?:npx\s+)?skills\s+add(?:\s+(.+))?$",
        raw,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        rest = (m.group(1) or "").strip()
        if not rest:
            raise SkillStoreError(
                "npx skills add 后须跟 owner/repo 或 GitHub URL",
                status_code=400,
            )
        tokens = rest.split()
        pkg: str | None = None
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("-"):
                # --agent=cursor / -a=cursor
                if "=" in tok:
                    i += 1
                    continue
                flag = tok.split("=", 1)[0]
                # 带值的短/长选项：跳过下一 token（若非另一 flag）
                if flag in ("-a", "--agent", "-s", "--skill") and i + 1 < len(tokens):
                    nxt = tokens[i + 1]
                    if not nxt.startswith("-"):
                        i += 2
                        continue
                i += 1
                continue
            pkg = tok
            break
        if not pkg:
            raise SkillStoreError(
                "无法从 npx skills add 命令中解析仓库参数",
                status_code=400,
            )
        raw = pkg.strip().strip("'\"")

    # owner/repo 短名 → github HTTPS
    if _OWNER_REPO_RE.fullmatch(raw) and not raw.startswith("git@"):
        owner, repo = raw.split("/", 1)
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        return f"https://github.com/{owner}/{repo}"

    return raw


def parse_github_skill_ref(ref: str) -> dict[str, str | None]:
    """将 GitHub 地址规范为 clone URL + 可选 branch/subdir。

    支持：
    - npx skills add owner/repo [-a cursor]（先经 normalize_skill_git_ref）
    - owner/repo
    - https://github.com/owner/repo[.git]
    - https://github.com/owner/repo/tree/<branch>[/<subdir...>]
    - git@github.com:owner/repo.git
    - github.com/owner/repo/...
    """
    raw = normalize_skill_git_ref(ref)

    if raw.startswith("git@"):
        # git@github.com:owner/repo.git
        m = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", raw)
        if not m:
            raise SkillStoreError(f"无法解析 git SSH 地址: {raw}", status_code=400)
        host, path = m.group(1), m.group(2).strip("/")
        parts = path.split("/")
        if len(parts) < 2:
            raise SkillStoreError("GitHub 地址须含 owner/repo", status_code=400)
        owner, repo = parts[0], parts[1]
        return {
            "clone_url": f"https://{host}/{owner}/{repo}.git",
            "branch": None,
            "subdir": None,
        }

    url = raw
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return {"clone_url": raw.rstrip("/"), "branch": None, "subdir": None}

    segs = [s for s in (parsed.path or "").strip("/").split("/") if s]
    if len(segs) < 2:
        raise SkillStoreError("GitHub 地址须含 owner/repo", status_code=400)
    owner, repo = segs[0], segs[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    clone_url = f"https://github.com/{owner}/{repo}.git"
    branch: str | None = None
    subdir: str | None = None
    if len(segs) >= 4 and segs[2] in ("tree", "blob"):
        branch = segs[3]
        rest = segs[4:]
        if segs[2] == "blob" and rest:
            # .../blob/branch/path/to/SKILL.md → 目录为 path/to
            if rest[-1].lower() == "skill.md":
                rest = rest[:-1]
            subdir = "/".join(rest) if rest else None
        elif rest:
            subdir = "/".join(rest)
    return {"clone_url": clone_url, "branch": branch, "subdir": subdir}


def install_from_path(
    src: Path | str,
    *,
    name: str | None = None,
    overwrite: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    src_path = Path(src).expanduser().resolve()
    if not src_path.is_dir():
        raise SkillStoreError(f"path 不是目录: {src_path}", status_code=400)
    if (src_path / "SKILL.md").is_file():
        skill_src = src_path
    else:
        skill_src = _resolve_skill_source_dir(src_path, subdir=None)
    folder_name = validate_skill_name(name or skill_src.name)
    md = skill_src / "SKILL.md"
    if not md.is_file():
        raise SkillStoreError("目录内缺少 SKILL.md", status_code=400)
    parsed = parse_skill_md(md)
    if parsed.get("name") != folder_name:
        raise SkillStoreError(
            f"frontmatter name={parsed.get('name')!r} 须与目标 name={folder_name!r} 一致",
            status_code=400,
        )
    if not parsed.get("description"):
        raise SkillStoreError("SKILL.md 缺少 description", status_code=400)

    base = skills_root(root)
    dest = base / folder_name
    with tempfile.TemporaryDirectory(prefix="skill_install_") as td:
        staged = Path(td) / folder_name
        shutil.copytree(skill_src, staged)
        _ensure_skill_dir_valid(staged, expected_name=folder_name)
        _atomic_promote(staged, dest, overwrite=overwrite)
    return _skill_meta(dest)


def _preferred_skill_name(skill_src: Path, name: str | None = None) -> str:
    """优先 frontmatter name；避免临时目录名（如 clone 的 repo）覆盖真实 skill 名。"""
    if name and str(name).strip():
        return str(name).strip()
    md = skill_src / "SKILL.md"
    parsed = parse_skill_md(md) if md.is_file() else {}
    fm = parsed.get("name") if isinstance(parsed.get("name"), str) else ""
    fm = fm.strip()
    folder = skill_src.name
    # git clone 落在 TemporaryDirectory/.../repo；zip 解压也可能叫 extract
    if folder in {"repo", "extract", "pack", "src", "tmp"} and fm:
        return fm
    return fm or folder


def install_from_git(
    ref: str,
    *,
    name: str | None = None,
    subdir: str | None = None,
    overwrite: bool = False,
    root: Path | None = None,
    branch: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    if not remote_allowed():
        raise SkillStoreError(
            "远程安装已关闭（设 CURSOR_SKILLS_ALLOW_REMOTE=1 启用）",
            status_code=403,
        )
    raw = (ref or "").strip()
    if not raw:
        raise SkillStoreError("git ref 不能为空", status_code=400)
    parsed = parse_github_skill_ref(raw)
    url = str(parsed["clone_url"] or raw)
    eff_branch = (branch or parsed.get("branch") or None)
    if isinstance(eff_branch, str):
        eff_branch = eff_branch.strip() or None
    else:
        eff_branch = None
    eff_subdir = subdir if subdir is not None else parsed.get("subdir")
    if isinstance(eff_subdir, str):
        eff_subdir = eff_subdir.strip() or None
    else:
        eff_subdir = None
    proxy_url = _normalize_http_proxy(proxy)
    timeout = _remote_timeout()
    env = os.environ.copy()
    if proxy_url:
        env["http_proxy"] = proxy_url
        env["https_proxy"] = proxy_url
        env["HTTP_PROXY"] = proxy_url
        env["HTTPS_PROXY"] = proxy_url
        env["ALL_PROXY"] = proxy_url
        env["all_proxy"] = proxy_url
    log = logging.getLogger("cursor_openai_bridge.skills")
    log.info(
        "git clone start url=%s branch=%s subdir=%s proxy=%s timeout=%.0fs",
        url,
        eff_branch or "-",
        eff_subdir or "-",
        "yes" if proxy_url else "no",
        timeout,
    )
    with tempfile.TemporaryDirectory(prefix="skill_git_") as td:
        clone_dir = Path(td) / "repo"
        cmd = ["git", "clone", "--depth", "1"]
        if eff_branch:
            cmd.extend(["--branch", eff_branch])
        cmd.extend([url, str(clone_dir)])
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except FileNotFoundError as e:
            log.error("git clone failed: git not installed")
            raise SkillStoreError("系统未安装 git", status_code=500) from e
        except subprocess.TimeoutExpired as e:
            log.error("git clone timeout url=%s timeout=%.0fs", url, timeout)
            raise SkillStoreError("git clone 超时", status_code=504) from e
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or str(e))[:500]
            log.error("git clone failed url=%s err=%s", url, err)
            raise SkillStoreError(f"git clone 失败: {err}", status_code=400) from e
        skill_src = _resolve_skill_source_dir(clone_dir, subdir=eff_subdir)
        eff_name = _preferred_skill_name(skill_src, name)
        log.info(
            "git clone ok src=%s name=%s",
            skill_src.name,
            eff_name,
        )
        return install_from_path(
            skill_src, name=eff_name, overwrite=overwrite, root=root
        )


def _normalize_http_proxy(proxy: str | None) -> str | None:
    raw = (proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https", "socks5", "socks5h"):
        raise SkillStoreError(
            "proxy 仅支持 http/https/socks5（例: http://10.1.1.109:7890）",
            status_code=400,
        )
    if not parsed.hostname:
        raise SkillStoreError("proxy 地址无效", status_code=400)
    return raw


def install_from_url(
    url: str,
    *,
    name: str | None = None,
    overwrite: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    if not remote_allowed():
        raise SkillStoreError(
            "远程安装已关闭（设 CURSOR_SKILLS_ALLOW_REMOTE=1 启用）",
            status_code=403,
        )
    u = (url or "").strip()
    if not u:
        raise SkillStoreError("url 不能为空", status_code=400)
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise SkillStoreError("url 仅支持 http/https", status_code=400)
    timeout = _remote_timeout()
    max_b = _max_bytes()
    with tempfile.TemporaryDirectory(prefix="skill_url_") as td:
        td_path = Path(td)
        archive = td_path / "archive.zip"
        try:
            req = Request(u, headers={"User-Agent": "cursor-openai-bridge-skills/1.0"})
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — gated by ALLOW_REMOTE
                data = resp.read(max_b + 1)
        except Exception as e:
            raise SkillStoreError(f"下载失败: {e}", status_code=400) from e
        if len(data) > max_b:
            raise SkillStoreError("下载内容超过 CURSOR_SKILLS_MAX_BYTES", status_code=400)
        return install_from_zip_bytes(
            data, name=name, overwrite=overwrite, root=root
        )


def _upload_max_bytes() -> int:
    try:
        return max(
            _max_bytes(),
            int(os.environ.get("CURSOR_SKILLS_UPLOAD_MAX_BYTES", str(64 * 1024 * 1024))),
        )
    except ValueError:
        return 64 * 1024 * 1024


def install_from_zip_bytes(
    data: bytes,
    *,
    name: str | None = None,
    overwrite: bool = False,
    subdir: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """从 zip 字节安装（本地上传或 url 下载复用）。"""
    if not data:
        raise SkillStoreError("zip 内容为空", status_code=400)
    max_b = _upload_max_bytes()
    if len(data) > max_b:
        raise SkillStoreError(
            f"zip 超过上限 {max_b} bytes（CURSOR_SKILLS_UPLOAD_MAX_BYTES）",
            status_code=400,
        )
    with tempfile.TemporaryDirectory(prefix="skill_zip_") as td:
        td_path = Path(td)
        archive = td_path / "archive.zip"
        extract_dir = td_path / "extract"
        extract_dir.mkdir()
        archive.write_bytes(data)
        try:
            with zipfile.ZipFile(archive) as zf:
                root_resolved = extract_dir.resolve()
                for info in zf.infolist():
                    dest = (extract_dir / info.filename).resolve()
                    if not str(dest).startswith(str(root_resolved)):
                        raise SkillStoreError("zip 含非法路径", status_code=400)
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as e:
            raise SkillStoreError("不是有效 zip", status_code=400) from e
        skill_src = _resolve_skill_source_dir(extract_dir, subdir=subdir)
        eff_name = _preferred_skill_name(skill_src, name)
        return install_from_path(
            skill_src, name=eff_name, overwrite=overwrite, root=root
        )


def install(
    *,
    source: str,
    ref: str,
    name: str | None = None,
    overwrite: bool = False,
    subdir: str | None = None,
    root: Path | None = None,
    branch: str | None = None,
    proxy: str | None = None,
) -> dict[str, Any]:
    src = (source or "").strip().lower()
    if src == "path":
        return install_from_path(ref, name=name, overwrite=overwrite, root=root)
    if src in ("git", "github"):
        return install_from_git(
            ref,
            name=name,
            subdir=subdir,
            overwrite=overwrite,
            root=root,
            branch=branch,
            proxy=proxy,
        )
    if src == "url":
        return install_from_url(ref, name=name, overwrite=overwrite, root=root)
    if src == "builtin":
        raise SkillStoreError("本期不支持 builtin 安装", status_code=400)
    raise SkillStoreError(f"未知 source: {source!r}（支持 path|git|url）", status_code=400)


_GENERATE_PROMPT_TEMPLATE = """Create a Cursor Agent Skill directory at ./{name}/ with a valid SKILL.md.

Requirements:
- Folder name must be exactly: {name}
- SKILL.md must start with YAML frontmatter:
  ---
  name: {name}
  description: <one paragraph: what it does and when to use it>
  ---
- Then Markdown instructions the agent should follow.
- Do not create other top-level skill folders.
- After writing files, print DONE.

User request for this skill:
{user_prompt}
"""


def generate_skill(
    prompt: str,
    *,
    name: str,
    overwrite: bool = False,
    root: Path | None = None,
    run_agent: Callable[..., Any] | None = None,
    agent_timeout: float = 600.0,
) -> dict[str, Any]:
    folder = validate_skill_name(name)
    user_prompt = (prompt or "").strip()
    if not user_prompt:
        raise SkillStoreError("prompt 不能为空", status_code=400)
    base = skills_root(root)
    dest = base / folder
    if dest.exists() and not overwrite:
        raise SkillStoreError(f"skill 已存在: {folder}", status_code=409)

    agent_fn = run_agent
    if agent_fn is None:
        try:
            from .cursor_automation import run_cursor_agent as _rca
        except ImportError:
            from cursor_automation import run_cursor_agent as _rca  # type: ignore

        agent_fn = _rca

    with tempfile.TemporaryDirectory(prefix="skill_gen_") as td:
        work = Path(td)
        agent_prompt = _GENERATE_PROMPT_TEMPLATE.format(
            name=folder, user_prompt=user_prompt
        )
        result = agent_fn(
            agent_prompt,
            workspace=work,
            output_format="json",
            trust=True,
            mode=None,
            force=True,
            timeout=agent_timeout,
        )
        rc = getattr(result, "returncode", 0)
        if rc not in (0, None):
            err = (
                getattr(result, "stderr", None)
                or getattr(result, "stdout", None)
                or "agent failed"
            )
            raise SkillStoreError(f"生成 skill 失败: {str(err)[:800]}", status_code=502)

        candidate = work / folder
        if not candidate.is_dir():
            if (work / "SKILL.md").is_file():
                wrap_parent = work / f"_wrap_{folder}"
                wrap_parent.mkdir(exist_ok=True)
                staged = wrap_parent / folder
                staged.mkdir()
                shutil.copy2(work / "SKILL.md", staged / "SKILL.md")
                candidate = staged
            else:
                raise SkillStoreError(
                    f"agent 未产出目录 ./{folder}/ 或 SKILL.md",
                    status_code=502,
                )
        elif candidate.name != folder:
            wrap_parent = work / f"_wrap_{folder}"
            wrap_parent.mkdir(exist_ok=True)
            staged = wrap_parent / folder
            shutil.copytree(candidate, staged)
            candidate = staged

        try:
            _ensure_skill_dir_valid(candidate, expected_name=folder)
        except SkillStoreError:
            md = candidate / "SKILL.md"
            text = md.read_text(encoding="utf-8")
            fm = _parse_frontmatter(text)
            if fm.get("description") and (not fm.get("name") or fm.get("name") != folder):
                body = _FRONTMATTER_RE.sub("", text, count=1)
                new_text = (
                    f"---\nname: {folder}\n"
                    f"description: {fm.get('description')}\n"
                    f"---\n{body.lstrip()}"
                )
                md.write_text(new_text, encoding="utf-8")
            _ensure_skill_dir_valid(candidate, expected_name=folder)

        _atomic_promote(candidate, dest, overwrite=overwrite)
    return _skill_meta(dest)


def delete_skill(name: str, root: Path | None = None) -> bool:
    n = validate_skill_name(name)
    base = skills_root(root)
    target = (base / n).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise SkillStoreError("路径非法", status_code=400)
    if not target.is_dir():
        raise SkillStoreError(f"skill 不存在: {n}", status_code=404)
    shutil.rmtree(target)
    return True
