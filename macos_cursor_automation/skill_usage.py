"""从 prompt / agent 输出推断本次是否使用了全局 Skills。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class SkillUsageResult:
    requested: list[str] = field(default_factory=list)
    evidenced: list[str] = field(default_factory=list)
    installed_count: int = 0

    @property
    def label(self) -> str:
        has_r = bool(self.requested)
        has_e = bool(self.evidenced)
        if has_r and has_e:
            return "requested+evidenced"
        if has_r:
            return "requested"
        if has_e:
            return "evidenced"
        return "none"


def _collect_strings(obj: Any, out: list[str], *, depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_strings(v, out, depth=depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_strings(v, out, depth=depth + 1)


def _slash_requested(prompt: str, installed: set[str]) -> list[str]:
    found: list[str] = []
    for m in re.finditer(
        r"(?:^|[\s\"'`(/])/(?P<name>[a-z0-9]+(?:-[a-z0-9]+)*)\b", prompt or ""
    ):
        name = m.group("name")
        if name in installed and name not in found:
            found.append(name)
    return found


def _evidenced_from_text(blob: str, installed: set[str]) -> list[str]:
    found: list[str] = []
    if not blob or not installed:
        return found
    for name in sorted(installed):
        patterns = (
            f".cursor/skills/{name}/",
            f".agents/skills/{name}/",
            f"/skills/{name}/SKILL.md",
            f".cursor/skills/{name}/SKILL.md",
            f".agents/skills/{name}/SKILL.md",
        )
        if any(p in blob for p in patterns):
            if name not in found:
                found.append(name)
    return found


def infer_skill_usage(
    prompt: str,
    *,
    agent_stdout: str = "",
    agent_stderr: str = "",
    parsed: Any = None,
    installed_names: Iterable[str] | None = None,
) -> SkillUsageResult:
    installed = [n for n in (installed_names or []) if isinstance(n, str) and n]
    installed_set = set(installed)
    requested = _slash_requested(prompt or "", installed_set)

    chunks: list[str] = [agent_stdout or "", agent_stderr or ""]
    if parsed is not None:
        try:
            if isinstance(parsed, (dict, list)):
                _collect_strings(parsed, chunks)
            else:
                chunks.append(str(parsed))
        except Exception:
            chunks.append(str(parsed))
    blob = "\n".join(chunks)
    evidenced = _evidenced_from_text(blob, installed_set)
    return SkillUsageResult(
        requested=requested,
        evidenced=evidenced,
        installed_count=len(installed_set),
    )


def format_skill_usage_log(req_id: str, usage: SkillUsageResult) -> str:
    return (
        f"id={req_id} skill_usage={usage.label} "
        f"requested={usage.requested} evidenced={usage.evidenced} "
        f"installed_count={usage.installed_count}"
    )


def skill_usage_logging_enabled() -> bool:
    return os.environ.get("CURSOR_BRIDGE_LOG_SKILL_USAGE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
