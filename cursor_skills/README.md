# Cursor global skills (persisted)

Host path mounted into the bridge container as `/root/.cursor/skills`.

Put each skill in a folder with `SKILL.md`. Use via chat prompt e.g. `/skill-name ...`.

Installed content under this directory is gitignored except this README, `.gitkeep`, and factory builtins such as `booktok-remotion/`.

官方 `remotion-*` 由 `llm_site_proxy/scripts/sync_factory_remotion_skills.sh` 从工作区 `.cursor/skills/remotion/skills/` 同步到本目录。`remotion-create` 会被工厂安全桥覆盖，避免 `npx create-video`。未在工厂勾选成片 Skill 时，生产路径不变。
