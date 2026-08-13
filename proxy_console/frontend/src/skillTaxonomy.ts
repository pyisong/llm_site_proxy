/**
 * Skills 展示辅助：分类完全来自 bridge ``GET /v1/skills``（categories + skill.category*）。
 * 本文件不再内置 by_name / 规则表。
 */

import type { SkillCategory, SkillItem } from "./api";

const FALLBACK_OTHER: SkillCategory = {
  id: "other",
  label: "其它",
  hint: "尚未归类",
  accent: "#5a6570",
  purposes: ["text", "image"],
};

export type SkillGroup = {
  category: SkillCategory;
  skills: SkillItem[];
};

export function skillDisplayName(skill: SkillItem | string): string {
  if (typeof skill === "string") {
    const n = skill.trim();
    if (n.startsWith("baoyu-") && n.length > 6) return n.slice(6);
    if (n.endsWith("-perspective") && n.length > 12) return n.slice(0, -12);
    return n;
  }
  if (skill.display_name?.trim()) return skill.display_name.trim();
  return skillDisplayName(skill.name);
}

export function skillFamilyLabel(skill: SkillItem): string | null {
  if (skill.family === null) return null;
  if (typeof skill.family === "string" && skill.family.trim()) {
    return skill.family.trim();
  }
  const n = skill.name || "";
  if (n.startsWith("baoyu-")) return "baoyu";
  if (n.endsWith("-perspective")) return "视角";
  return null;
}

function categoryFromSkill(
  skill: SkillItem,
  catalog: SkillCategory[],
): SkillCategory {
  const id = (skill.category || "other").trim() || "other";
  const hit = catalog.find((c) => c.id === id);
  if (hit) return hit;
  return {
    id,
    label: skill.category_label || id,
    hint: skill.category_hint || "",
    accent: skill.category_accent || FALLBACK_OTHER.accent,
    purposes: skill.purposes,
  };
}

export function resolveCategoryCatalog(
  categories: SkillCategory[] | undefined,
  skills: SkillItem[],
): SkillCategory[] {
  if (Array.isArray(categories) && categories.length > 0) {
    return categories;
  }
  // bridge 未返回目录时，从 skill 字段拼一份
  const seen = new Map<string, SkillCategory>();
  for (const s of skills) {
    const cat = categoryFromSkill(s, []);
    if (!seen.has(cat.id)) seen.set(cat.id, cat);
  }
  if (!seen.has("other")) seen.set("other", FALLBACK_OTHER);
  return [...seen.values()];
}

export function groupSkillsByCategory(
  skills: SkillItem[],
  categories?: SkillCategory[],
): SkillGroup[] {
  const catalog = resolveCategoryCatalog(categories, skills);
  const buckets = new Map<string, SkillItem[]>();
  for (const s of skills) {
    const cat = categoryFromSkill(s, catalog);
    const list = buckets.get(cat.id);
    if (list) list.push(s);
    else buckets.set(cat.id, [s]);
  }
  for (const list of buckets.values()) {
    list.sort((a, b) => a.name.localeCompare(b.name));
  }
  const ordered = catalog
    .map((cat) => ({
      category: cat,
      skills: buckets.get(cat.id) || [],
    }))
    .filter((g) => g.skills.length > 0);
  // 目录外的残留 id
  for (const [id, list] of buckets) {
    if (catalog.some((c) => c.id === id)) continue;
    if (!list.length) continue;
    ordered.push({
      category: categoryFromSkill(list[0]!, catalog),
      skills: list,
    });
  }
  return ordered;
}
