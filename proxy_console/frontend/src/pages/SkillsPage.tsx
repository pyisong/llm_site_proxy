import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  ArrowClockwise,
  CircleNotch,
  DownloadSimple,
  FileZip,
  GithubLogo,
  FolderSimple,
  Prohibit,
  Trash,
} from "@phosphor-icons/react";
import { api, fmtTime, type SkillItem } from "../api";
import {
  EmptyState,
  ErrorBanner,
  GhostButton,
  PageHeader,
  Panel,
  PrimaryButton,
  Reveal,
  Skeleton,
  StatusPill,
} from "../components/ui";

type InstallMode = "github" | "path" | "zip";

type InstallProgress = {
  mode: InstallMode;
  ref: string;
  startedAt: number;
  phase: "upload" | "install";
  /** 0–100；压缩包上传为真实进度，其它为估算 */
  percent: number;
};

const inputClass =
  "w-full rounded-md border border-line bg-canvas px-3 py-2.5 text-sm text-ink placeholder:text-muted/70 outline-none focus:border-accent disabled:opacity-60";

function installModeLabel(mode: InstallMode): string {
  if (mode === "github") return "GitHub";
  if (mode === "zip") return "压缩包";
  return "本地路径";
}

function installModeHint(mode: InstallMode): string {
  if (mode === "github") {
    return "后台异步 git clone，当前网络较慢时可能需要 10–20 分钟。请勿关闭页面。";
  }
  if (mode === "zip") {
    return "上传并解压 zip，校验 SKILL.md。请勿关闭页面。";
  }
  return "复制并校验 skill 目录。请勿关闭页面。";
}
export default function SkillsPage() {
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [source, setSource] = useState<string | undefined>();
  const [selected, setSelected] = useState<string | null>(null);
  const [usage, setUsage] = useState<
    {
      id: string;
      label: string;
      request_id: string | null;
      created_at: number;
    }[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [installMode, setInstallMode] = useState<InstallMode>("github");
  const [installPath, setInstallPath] = useState("");
  const [installGithub, setInstallGithub] = useState("");
  const [installName, setInstallName] = useState("");
  const [installZip, setInstallZip] = useState<File | null>(null);
  const [installProxy, setInstallProxy] = useState(() => {
    try {
      return localStorage.getItem("proxy_console_skill_proxy") || "";
    } catch {
      return "";
    }
  });
  const [overwrite, setOverwrite] = useState(true);
  const [installProgress, setInstallProgress] = useState<InstallProgress | null>(
    null,
  );
  const [elapsed, setElapsed] = useState(0);
  const [lastInstallOk, setLastInstallOk] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.skills();
      setSkills(res.skills);
      setSource(res.source);
      if (res.error) setError(`Bridge: ${res.error} (已用本地回退或空列表)`);
      setSelected((prev) => prev ?? res.skills[0]?.name ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) return;
    void api
      .skillUsage(selected)
      .then((r) => setUsage(r.items))
      .catch(() => setUsage([]));
  }, [selected]);

  useEffect(() => {
    if (!installProgress) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const t = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - installProgress.startedAt) / 1000));
    }, 250);
    return () => window.clearInterval(t);
  }, [installProgress]);

  const current = skills.find((s) => s.name === selected) || null;
  const canSubmit =
    installMode === "github"
      ? Boolean(installGithub.trim())
      : installMode === "zip"
        ? Boolean(installZip)
        : Boolean(installPath.trim());

  async function onInstall(e: FormEvent) {
    e.preventDefault();
    if (!canSubmit || busy) return;
    const ref =
      installMode === "github"
        ? installGithub.trim()
        : installMode === "zip"
          ? installZip?.name || "skill.zip"
          : installPath.trim();
    setBusy(true);
    setError(null);
    setLastInstallOk(null);
    const startedAt = Date.now();
    setInstallProgress({
      mode: installMode,
      ref,
      startedAt,
      phase: installMode === "zip" ? "upload" : "install",
      percent: 4,
    });
    try {
      if (installMode === "github") {
        const body: Record<string, unknown> = {
          source: "git",
          ref,
          overwrite,
        };
        if (installName.trim()) body.name = installName.trim();
        if (installProxy.trim()) {
          body.proxy = installProxy.trim();
          try {
            localStorage.setItem(
              "proxy_console_skill_proxy",
              installProxy.trim(),
            );
          } catch {
            /* ignore */
          }
        }
        const started = await api.installSkill(body);
        const jobId = started.id || started.job_id;
        if (jobId) {
          for (;;) {
            await new Promise((r) => window.setTimeout(r, 2000));
            const job = await api.skillJob(jobId);
            const waited = Math.floor((Date.now() - startedAt) / 1000);
            setInstallProgress((prev) =>
              prev
                ? {
                    ...prev,
                    phase: "install",
                    percent: Math.min(92, 10 + Math.floor(waited / 8)),
                  }
                : prev,
            );
            if (job.status === "succeeded") {
              setInstallGithub("");
              setInstallName("");
              setLastInstallOk(job.result?.name || "安装成功");
              break;
            }
            if (job.status === "failed") {
              throw new Error(job.error || "安装失败");
            }
          }
        } else {
          setInstallGithub("");
          setInstallName("");
          setLastInstallOk(started.name || started.result?.name || "安装成功");
        }
      } else if (installMode === "zip") {
        if (!installZip) return;
        const result = await api.uploadSkill({
          file: installZip,
          name: installName.trim() || undefined,
          overwrite,
          onProgress: (ratio) => {
            // 上传占 0–85%，服务端解压安装留到 85–95
            const pct = Math.round(ratio * 85);
            setInstallProgress((prev) =>
              prev
                ? {
                    ...prev,
                    phase: ratio >= 1 ? "install" : "upload",
                    percent: Math.max(4, pct),
                  }
                : prev,
            );
          },
        });
        setInstallProgress((prev) =>
          prev ? { ...prev, phase: "install", percent: 96 } : prev,
        );
        setInstallZip(null);
        setInstallName("");
        setLastInstallOk(result?.name || "安装成功");
      } else {
        const result = (await api.installSkill({
          source: "path",
          ref,
          overwrite,
        })) as { name?: string };
        setInstallPath("");
        setLastInstallOk(result?.name || "安装成功");
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstallProgress(null);
      setBusy(false);
    }
  }

  async function onDelete(name: string) {
    if (!window.confirm(`删除 skill「${name}」？此操作不可撤销。`)) return;
    setBusy(true);
    try {
      await api.deleteSkill(name);
      setSelected(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function toggleDisable(skill: SkillItem) {
    setBusy(true);
    try {
      if (skill.disabled) await api.enableSkill(skill.name);
      else await api.disableSkill(skill.name, "disabled from console");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Skills"
        subtitle="Cursor Bridge 全局 Skills：安装、禁用、删除与使用记录。"
        action={
          <GhostButton onClick={() => void load()}>
            <ArrowClockwise size={16} />
            刷新
          </GhostButton>
        }
      />

      {error ? <ErrorBanner message={error} /> : null}

      {installProgress ? (
        <Panel className="mb-4 border-accent/40 bg-accent/5 px-4 py-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <CircleNotch size={18} className="shrink-0 animate-spin text-accent" />
              <div className="min-w-0">
                <div className="text-sm font-medium">
                  正在从 {installModeLabel(installProgress.mode)} 安装
                  <span className="ml-2 font-mono text-[11px] text-muted">
                    {installProgress.phase === "upload" ? "上传中" : "安装中"}
                    {" · "}
                    {installProgress.percent}%
                  </span>
                </div>
                <div className="mt-0.5 truncate font-mono text-[12px] text-muted">
                  {installProgress.ref}
                </div>
                <div className="mt-0.5 text-[12px] text-muted">
                  {installProgress.phase === "upload"
                    ? "正在上传压缩包到服务器…"
                    : installModeHint(installProgress.mode)}
                </div>
              </div>
            </div>
            <div className="shrink-0 font-mono text-sm text-accent">
              已等待 {elapsed}s
            </div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-line">
            <div
              className="h-full bg-accent transition-all duration-300"
              style={{ width: `${installProgress.percent}%` }}
            />
          </div>
        </Panel>
      ) : null}

      {lastInstallOk && !installProgress && !error ? (
        <div className="mb-4 rounded-md border border-ok/40 bg-ok/10 px-4 py-3 text-sm text-ok">
          已安装「{lastInstallOk}」
        </div>
      ) : null}

      <Reveal className="mb-4">
        <Panel className="overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
            <h2 className="text-sm font-medium">安装 Skill</h2>
            <div
              className="inline-flex flex-wrap rounded-md border border-line bg-canvas p-0.5"
              role="tablist"
              aria-label="安装来源"
            >
              {(
                [
                  ["zip", FileZip, "压缩包"],
                  ["github", GithubLogo, "GitHub"],
                  ["path", FolderSimple, "本地路径"],
                ] as const
              ).map(([mode, Icon, label]) => (
                <button
                  key={mode}
                  type="button"
                  role="tab"
                  aria-selected={installMode === mode}
                  disabled={Boolean(installProgress)}
                  onClick={() => setInstallMode(mode)}
                  className={[
                    "inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-[13px] transition disabled:opacity-50",
                    installMode === mode
                      ? "bg-panel-2 text-ink"
                      : "text-muted hover:text-ink",
                  ].join(" ")}
                >
                  <Icon size={14} weight="bold" />
                  {label}
                </button>
              ))}
            </div>
          </div>

          <form onSubmit={onInstall} className="space-y-3 p-4">
            <div className="flex items-stretch gap-2">
              {installMode === "zip" ? (
                <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 rounded-md border border-dashed border-line bg-canvas px-3 py-2.5 transition hover:border-accent/50">
                  <FileZip size={18} className="shrink-0 text-muted" />
                  <span className="min-w-0 flex-1 truncate text-sm text-muted">
                    {installZip
                      ? `${installZip.name}（${(installZip.size / 1024 / 1024).toFixed(1)} MB）`
                      : "选择 .zip（含 SKILL.md，或 GitHub 下载的源码包）"}
                  </span>
                  <input
                    type="file"
                    accept=".zip,application/zip"
                    className="sr-only"
                    disabled={Boolean(installProgress)}
                    onChange={(e) => {
                      const f = e.target.files?.[0] || null;
                      setInstallZip(f);
                    }}
                  />
                </label>
              ) : (
                <input
                  value={installMode === "github" ? installGithub : installPath}
                  onChange={(e) =>
                    installMode === "github"
                      ? setInstallGithub(e.target.value)
                      : setInstallPath(e.target.value)
                  }
                  placeholder={
                    installMode === "github"
                      ? "npx skills add owner/repo  或  https://github.com/owner/repo"
                      : "/path/to/skill-dir"
                  }
                  className={`min-w-0 flex-1 ${inputClass}`}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={Boolean(installProgress)}
                />
              )}
              <div className="shrink-0">
                <PrimaryButton
                  type="submit"
                  disabled={Boolean(installProgress) || !canSubmit}
                >
                  {installProgress ? (
                    <CircleNotch size={16} className="animate-spin" />
                  ) : (
                    <DownloadSimple size={16} weight="bold" />
                  )}
                  {installProgress ? `安装中 ${elapsed}s` : "安装"}
                </PrimaryButton>
              </div>
            </div>

            {installMode === "path" ? (
              <label className="flex items-center gap-2 text-[12px] text-muted">
                <input
                  type="checkbox"
                  checked={overwrite}
                  disabled={Boolean(installProgress)}
                  onChange={(e) => setOverwrite(e.target.checked)}
                  className="rounded border-line"
                />
                同名时覆盖已安装 skill
              </label>
            ) : (
              <div className="space-y-3">
                {installMode === "github" ? (
                  <label className="flex min-w-0 flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
                    <span className="shrink-0 text-[12px] text-muted">
                      临时代理
                    </span>
                    <input
                      value={installProxy}
                      onChange={(e) => setInstallProxy(e.target.value)}
                      placeholder="http://10.1.1.109:7890（可选，仅本次 git clone）"
                      disabled={Boolean(installProgress)}
                      className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-2.5 py-1.5 font-mono text-[13px] text-ink placeholder:text-muted/70 outline-none focus:border-accent disabled:opacity-60"
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </label>
                ) : null}
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <label className="flex min-w-0 flex-1 items-center gap-2">
                    <span className="shrink-0 text-[12px] text-muted">
                      名称（可选）
                    </span>
                    <input
                      value={installName}
                      onChange={(e) => setInstallName(e.target.value)}
                      placeholder="默认取目录名"
                      disabled={Boolean(installProgress)}
                      className="min-w-0 flex-1 rounded-md border border-line bg-canvas px-2.5 py-1.5 font-mono text-[13px] text-ink placeholder:text-muted/70 outline-none focus:border-accent disabled:opacity-60 sm:max-w-[200px]"
                      autoComplete="off"
                      spellCheck={false}
                    />
                  </label>
                  <label className="flex items-center gap-2 text-[12px] text-muted">
                    <input
                      type="checkbox"
                      checked={overwrite}
                      disabled={Boolean(installProgress)}
                      onChange={(e) => setOverwrite(e.target.checked)}
                      className="rounded border-line"
                    />
                    覆盖同名
                  </label>
                </div>
              </div>
            )}

            <p className="text-[11px] leading-relaxed text-muted">
              {installMode === "zip"
                ? "本机下载 zip 后上传（推荐绕过服务器访问 GitHub 超时）。支持单 skill 目录或 GitHub Code → Download ZIP。"
                : installMode === "github"
                  ? "可粘贴完整 npx skills add owner/repo [-a cursor]、owner/repo 短名，或 GitHub URL（含 /tree/<branch>/<subdir>）。临时代理仅作用于本次 git clone。"
                  : "填写 Bridge 容器内可读路径，对应 POST /v1/skills/install（source=path）。"}
              {source ? (
                <span className="ml-2 font-mono text-muted/80">
                  source: {source}
                </span>
              ) : null}
            </p>
          </form>
        </Panel>
      </Reveal>

      {loading ? <Skeleton className="h-72" /> : null}

      {!loading ? (
        <div className="grid lg:grid-cols-[1fr_360px] gap-4">
          <Reveal delay={0.04}>
            <Panel className="overflow-hidden">
              <div className="px-4 py-3 border-b border-line flex justify-between">
                <h2 className="text-sm font-medium">已安装</h2>
                <span className="font-mono text-[11px] text-muted">
                  {skills.length}
                </span>
              </div>
              {skills.length === 0 ? (
                <EmptyState
                  title="没有 Skills"
                  body="安装一个 skill，或确认 Cursor Bridge 可达。"
                />
              ) : (
                <ul className="divide-y divide-line max-h-[560px] overflow-y-auto">
                  {skills.map((s) => (
                    <li key={s.name}>
                      <button
                        type="button"
                        onClick={() => setSelected(s.name)}
                        className={[
                          "w-full text-left px-4 py-3 transition hover:bg-panel-2/70",
                          selected === s.name ? "bg-panel-2" : "",
                        ].join(" ")}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="font-medium font-mono text-sm">
                            {s.name}
                          </span>
                          <div className="flex items-center gap-2">
                            {s.disabled ? (
                              <StatusPill ok={false} label="disabled" />
                            ) : (
                              <StatusPill ok label="active" />
                            )}
                            <span className="font-mono text-[11px] text-muted">
                              {s.uses} uses
                            </span>
                          </div>
                        </div>
                        <p className="mt-1 text-[13px] text-muted line-clamp-2">
                          {s.description || "无描述"}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </Reveal>

          <Reveal delay={0.08}>
            <Panel className="p-4 min-h-[320px]">
              {!current ? (
                <EmptyState title="选择一个 Skill" body="查看用量与管理操作。" />
              ) : (
                <div>
                  <h2 className="text-lg font-semibold tracking-tight font-mono">
                    {current.name}
                  </h2>
                  <p className="mt-2 text-sm text-muted leading-relaxed">
                    {current.description || "无描述"}
                  </p>
                  <dl className="mt-4 space-y-2 text-sm">
                    <div className="flex justify-between gap-3 border-b border-line/60 pb-2">
                      <dt className="text-muted">path</dt>
                      <dd className="font-mono text-[11px] text-right break-all">
                        {current.path || "-"}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3 border-b border-line/60 pb-2">
                      <dt className="text-muted">uses</dt>
                      <dd className="font-mono">{current.uses}</dd>
                    </div>
                    <div className="flex justify-between gap-3 border-b border-line/60 pb-2">
                      <dt className="text-muted">last used</dt>
                      <dd className="font-mono text-[11px]">
                        {fmtTime(current.last_used_at)}
                      </dd>
                    </div>
                  </dl>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <GhostButton
                      disabled={busy}
                      onClick={() => void toggleDisable(current)}
                    >
                      <Prohibit size={16} />
                      {current.disabled ? "启用" : "禁用"}
                    </GhostButton>
                    <GhostButton
                      danger
                      disabled={busy}
                      onClick={() => void onDelete(current.name)}
                    >
                      <Trash size={16} />
                      删除
                    </GhostButton>
                  </div>

                  <h3 className="mt-6 text-sm font-medium">使用记录</h3>
                  {usage.length === 0 ? (
                    <p className="mt-2 text-sm text-muted">暂无用量事件。</p>
                  ) : (
                    <ul className="mt-2 space-y-2 max-h-56 overflow-y-auto">
                      {usage.map((u) => (
                        <li
                          key={u.id}
                          className="rounded-md border border-line bg-canvas/60 px-3 py-2"
                        >
                          <div className="flex justify-between gap-2 text-[12px]">
                            <span className="font-mono text-accent">
                              {u.label}
                            </span>
                            <span className="font-mono text-muted">
                              {fmtTime(u.created_at)}
                            </span>
                          </div>
                          {u.request_id ? (
                            <div className="mt-1 font-mono text-[11px] text-muted truncate">
                              {u.request_id}
                            </div>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </Panel>
          </Reveal>
        </div>
      ) : null}
    </div>
  );
}
