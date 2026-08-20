import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Brain,
  CheckCircle,
  Clock,
  Desktop,
  FileText,
  FilmStrip,
  Folder,
  Lightning,
  Moon,
  PenNib,
  Prohibit,
  PuzzlePiece,
  ShareNetwork,
  Sparkle,
  Trophy,
  Wrench,
  type Icon,
} from "@phosphor-icons/react";
import {
  fmtTime,
  type SkillCategory,
  type SkillItem,
  type SkillUsageEvent,
} from "../api";
import {
  groupSkillsByCategory,
  resolveCategoryCatalog,
  skillDisplayName,
} from "../skillTaxonomy";
import { EmptyState, GhostButton, Panel } from "./ui";

const CATEGORY_ICONS: Record<string, Icon> = {
  perspective: Brain,
  content: PenNib,
  publish: ShareNetwork,
  docs: FileText,
  generation: Sparkle,
  utility: Wrench,
  platform: Desktop,
  motion: FilmStrip,
  other: Folder,
};

function categoryIcon(id: string): Icon {
  return CATEGORY_ICONS[id] || PuzzlePiece;
}

export function SkillsShowcase({
  skills,
  categories,
  recent,
}: {
  skills: SkillItem[];
  categories: SkillCategory[];
  recent: SkillUsageEvent[];
}) {
  const catalog = resolveCategoryCatalog(categories, skills);
  const grouped = groupSkillsByCategory(skills, catalog);
  const [filter, setFilter] = useState<string>("all");

  const enabled = skills.filter((s) => !s.disabled).length;
  const totalUses = skills.reduce((n, s) => n + (s.uses || 0), 0);
  const idle = skills.filter((s) => !s.uses).length;

  const ranked = useMemo(
    () =>
      [...skills]
        .filter((s) => (s.uses || 0) > 0)
        .sort((a, b) => (b.uses || 0) - (a.uses || 0))
        .slice(0, 5),
    [skills],
  );

  const visibleGroups = useMemo(() => {
    if (filter === "all") return grouped;
    return grouped.filter((g) => g.category.id === filter);
  }, [grouped, filter]);

  const flatVisible = useMemo(
    () => visibleGroups.flatMap((g) => g.skills.map((s) => ({ skill: s, cat: g.category }))),
    [visibleGroups],
  );

  return (
    <section className="mt-8 border-t border-line pt-6">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="inline-flex size-8 shrink-0 items-center justify-center rounded-md border border-line bg-panel-2 text-accent">
            <PuzzlePiece size={18} weight="bold" />
          </span>
          <div className="min-w-0">
            <h2 className="text-lg font-semibold tracking-tight leading-[1.1]">
              Skills
            </h2>
            <p className="text-[11px] text-muted truncate">
              已安装目录与调用用量
            </p>
          </div>
        </div>
        <Link to="/skills">
          <GhostButton>
            管理
            <ArrowRight size={14} />
          </GhostButton>
        </Link>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        <StatChip icon={PuzzlePiece} label="安装" value={skills.length} />
        <StatChip icon={CheckCircle} label="启用" value={enabled} tone="ok" />
        <StatChip icon={Lightning} label="调用" value={totalUses} tone="accent" />
        <StatChip icon={Moon} label="未用" value={idle} />
      </div>

      {skills.length === 0 ? (
        <Panel>
          <EmptyState
            title="还没有 Skills"
            body="到管理页安装。"
          />
        </Panel>
      ) : (
        <Panel className="overflow-hidden">
          <div className="flex flex-wrap items-center gap-1 border-b border-line px-3 py-2">
            <button
              type="button"
              onClick={() => setFilter("all")}
              className={chipClass(filter === "all")}
            >
              全部
              <span className="font-mono tabular-nums opacity-70">{skills.length}</span>
            </button>
            {grouped.map((g) => {
              const Icon = categoryIcon(g.category.id);
              return (
                <button
                  key={g.category.id}
                  type="button"
                  onClick={() => setFilter(g.category.id)}
                  title={g.category.hint}
                  className={chipClass(filter === g.category.id)}
                >
                  <Icon size={12} weight="bold" />
                  {g.category.label}
                  <span className="font-mono tabular-nums opacity-70">
                    {g.skills.length}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="grid lg:grid-cols-[minmax(0,1fr)_220px]">
            <ul className="max-h-[280px] overflow-y-auto divide-y divide-line/60">
              {flatVisible.map(({ skill, cat }) => (
                <SkillRow key={skill.name} skill={skill} category={cat} />
              ))}
            </ul>

            <aside className="border-t border-line lg:border-t-0 lg:border-l divide-y divide-line/60">
              <div className="px-3 py-2">
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted">
                  <Trophy size={13} weight="bold" className="text-warn" />
                  用量 Top
                </div>
                {ranked.length === 0 ? (
                  <p className="mt-2 text-[11px] text-muted">暂无</p>
                ) : (
                  <ol className="mt-1.5 space-y-1">
                    {ranked.map((s, i) => (
                      <li key={s.name}>
                        <Link
                          to={`/skills?skill=${encodeURIComponent(s.name)}`}
                          className="flex items-center gap-2 rounded px-1 py-0.5 text-[12px] hover:bg-panel-2 transition"
                        >
                          <span className="w-4 shrink-0 font-mono text-[10px] text-muted">
                            {i + 1}
                          </span>
                          <span className="min-w-0 flex-1 truncate">
                            {skillDisplayName(s)}
                          </span>
                          <span className="font-mono text-[11px] tabular-nums text-accent">
                            {s.uses}
                          </span>
                        </Link>
                      </li>
                    ))}
                  </ol>
                )}
              </div>

              <div className="px-3 py-2">
                <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted">
                  <Clock size={13} weight="bold" />
                  最近
                </div>
                {recent.length === 0 ? (
                  <p className="mt-2 text-[11px] text-muted">暂无</p>
                ) : (
                  <ul className="mt-1.5 max-h-[140px] overflow-y-auto space-y-1">
                    {recent.slice(0, 8).map((ev) => (
                      <li key={ev.id}>
                        <Link
                          to={`/skills?skill=${encodeURIComponent(ev.skill_name)}`}
                          className="block rounded px-1 py-0.5 hover:bg-panel-2 transition"
                        >
                          <div className="flex items-center justify-between gap-2 text-[12px]">
                            <span className="truncate">
                              {skillDisplayName(ev.skill_name)}
                            </span>
                            <span className="shrink-0 font-mono text-[10px] text-accent">
                              {ev.label}
                            </span>
                          </div>
                          <div className="font-mono text-[10px] text-muted">
                            {fmtTime(ev.created_at)}
                          </div>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </aside>
          </div>
        </Panel>
      )}
    </section>
  );
}

function chipClass(active: boolean): string {
  return [
    "inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] transition",
    active
      ? "border-accent/40 bg-accent/10 text-ink"
      : "border-transparent text-muted hover:text-ink hover:bg-panel-2",
  ].join(" ");
}

function StatChip({
  icon: Icon,
  label,
  value,
  tone,
}: {
  icon: Icon;
  label: string;
  value: number;
  tone?: "ok" | "accent";
}) {
  const toneClass =
    tone === "ok"
      ? "text-ok"
      : tone === "accent"
        ? "text-accent"
        : "text-ink";
  return (
    <div className="inline-flex items-center gap-1.5 rounded-md border border-line bg-panel/60 px-2 py-1">
      <Icon size={14} className="text-muted" />
      <span className="text-[11px] text-muted">{label}</span>
      <span className={`font-mono text-[12px] font-medium tabular-nums ${toneClass}`}>
        {value}
      </span>
    </div>
  );
}

function SkillRow({
  skill,
  category,
}: {
  skill: SkillItem;
  category: SkillCategory;
}) {
  const CatIcon = categoryIcon(category.id);
  const accent = category.accent || "#2bb8c8";
  const off = skill.disabled || skill.valid === false;

  return (
    <li>
      <Link
        to={`/skills?skill=${encodeURIComponent(skill.name)}`}
        className="flex items-center gap-2 px-3 py-1.5 hover:bg-panel-2/70 transition active:scale-[0.995]"
      >
        <span
          className="inline-flex size-7 shrink-0 items-center justify-center rounded border border-line/80 bg-canvas/80"
          style={{ color: accent }}
          title={category.label}
        >
          <CatIcon size={14} weight="bold" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium truncate">
              {skillDisplayName(skill)}
            </span>
            {off ? (
              <Prohibit size={12} className="shrink-0 text-fail" weight="bold" />
            ) : (
              <CheckCircle size={12} className="shrink-0 text-ok/80" weight="fill" />
            )}
          </div>
          <div className="font-mono text-[10px] text-muted truncate">
            {skill.name}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="inline-flex items-center gap-1 font-mono text-[12px] tabular-nums">
            <Lightning size={11} className="text-muted" />
            {skill.uses || 0}
          </div>
          <div className="font-mono text-[10px] text-muted">
            {skill.last_used_at ? fmtTime(skill.last_used_at) : "-"}
          </div>
        </div>
      </Link>
    </li>
  );
}

export function SkillsShowcaseSkeleton() {
  return (
    <section className="mt-8 border-t border-line pt-6">
      <div className="mb-3 flex items-center gap-2">
        <div className="size-8 animate-pulse rounded-md bg-panel-2" />
        <div className="h-5 w-24 animate-pulse rounded bg-panel-2" />
      </div>
      <div className="mb-3 flex gap-2">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-7 w-20 animate-pulse rounded-md bg-panel-2" />
        ))}
      </div>
      <div className="h-48 animate-pulse rounded-md bg-panel-2/80" />
    </section>
  );
}
