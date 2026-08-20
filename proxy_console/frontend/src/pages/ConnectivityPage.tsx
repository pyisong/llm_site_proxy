import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowClockwise, CircleNotch, Plugs, SignIn } from "@phosphor-icons/react";
import {
  api,
  fmtMs,
  fmtTime,
  type ConnectivityPayload,
  type RequestEvent,
} from "../api";
import LoginRefreshModal from "../components/LoginRefreshModal";
import {
  ErrorBanner,
  GhostButton,
  PageHeader,
  Panel,
  PrimaryButton,
  Reveal,
  Skeleton,
  StatusPill,
} from "../components/ui";

const LOGIN_REFRESH_PROXY_IDS = new Set([
  "deepseek-openai-proxy",
  "kimi-openai-proxy",
  "stepfun-openai-proxy",
  "qwen-openai-proxy",
  "metaso-openai-proxy",
]);

type ProbeResult = {
  id: string;
  proxy_id: string;
  mode: string;
  ok: boolean;
  latency_ms: number | null;
  detail: string | null;
  created_at: number;
};

type ProbeProgress = {
  proxyId: string;
  proxyName: string;
  index: number;
  total: number;
  startedAt: number;
};

const TTS_PROXY_ID = "azure-tts-http-api";

function isTtsProxy(proxyId: string): boolean {
  return proxyId === TTS_PROXY_ID;
}

function probeProgressHint(proxyId: string): string {
  if (isTtsProxy(proxyId)) {
    return "正在探测语音服务，请勿关闭页面。";
  }
  return "正在探测聊天服务，请勿关闭页面。";
}

export default function ConnectivityPage() {
  const [data, setData] = useState<ConnectivityPayload | null>(null);
  const [auth, setAuth] = useState<
    {
      proxy_id: string;
      name: string;
      state: string;
      message: string | null;
      keepalive: boolean;
      last_ok_at: number | null;
      last_fail_at: number | null;
    }[]
  >([]);
  const [recentProbes, setRecentProbes] = useState<RequestEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProbeProgress | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [lastBatch, setLastBatch] = useState<ProbeResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [loginTarget, setLoginTarget] = useState<{
    proxyId: string;
    name: string;
  } | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [c, a, req] = await Promise.all([
        api.connectivity(),
        api.authStatus(),
        api.requests(30),
      ]);
      setData(c);
      setAuth(a.items);
      setRecentProbes(
        req.items.filter(
          (r) =>
            (r.meta &&
              typeof r.meta === "object" &&
              (r.meta as { probe?: boolean }).probe === true) ||
            r.path === "/v1/chat/completions" ||
            r.path === "/tts",
        ),
      );
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
    if (!progress) {
      setElapsed(0);
      return;
    }
    setElapsed(0);
    const t = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - progress.startedAt) / 1000));
    }, 500);
    return () => window.clearInterval(t);
  }, [progress]);

  const matrix = useMemo(() => {
    const map = new Map<string, ConnectivityPayload["results"][number]>();
    for (const r of data?.results || []) {
      map.set(`${r.proxy_id}::${r.mode}`, r);
    }
    return map;
  }, [data]);

  const nameOf = useCallback(
    (proxyId: string) =>
      data?.proxies.find((p) => p.id === proxyId)?.name || proxyId,
    [data],
  );

  async function runProbe(proxyId?: string) {
    if (!data) return;
    const targets = proxyId
      ? data.proxies.filter((p) => p.id === proxyId)
      : data.proxies;
    if (targets.length === 0) return;

    setBusy(proxyId || "all");
    setError(null);
    setLastBatch([]);
    const batch: ProbeResult[] = [];

    try {
      for (let i = 0; i < targets.length; i++) {
        const p = targets[i];
        setProgress({
          proxyId: p.id,
          proxyName: p.name,
          index: i + 1,
          total: targets.length,
          startedAt: Date.now(),
        });
        const res = await api.probe({ proxy_id: p.id, mode: "chat" });
        const items = (res.results || []) as ProbeResult[];
        batch.push(...items);
        setLastBatch([...batch]);
        // Refresh matrix mid-flight so chat cell updates after each proxy
        await load();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setProgress(null);
      setBusy(null);
      await load();
    }
  }

  async function markRefreshed(proxyId: string) {
    setBusy(`auth:${proxyId}`);
    try {
      await api.markRefreshed(proxyId);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Connectivity"
        subtitle="对所选服务发一次真实探测。聊天可能需要较长时间。"
        action={
          <div className="flex items-center gap-2">
            <GhostButton disabled={busy !== null} onClick={() => void load()}>
              <ArrowClockwise size={16} />
              刷新
            </GhostButton>
            <PrimaryButton
              disabled={busy !== null}
              onClick={() => void runProbe()}
            >
              {busy === "all" ? (
                <>
                  <CircleNotch size={16} className="animate-spin" />
                  探测中...
                </>
              ) : (
                <>
                  <Plugs size={16} weight="bold" />
                  全部探测
                </>
              )}
            </PrimaryButton>
          </div>
        }
      />

      {error ? <ErrorBanner message={error} /> : null}

      {progress ? (
        <Panel className="mb-4 border-accent/40 bg-accent/5 px-4 py-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <CircleNotch size={18} className="animate-spin text-accent" />
              <div>
                <div className="text-sm font-medium">
                  正在探测 {progress.proxyName}
                  <span className="ml-2 font-mono text-[11px] text-muted">
                    {progress.index}/{progress.total}
                  </span>
                </div>
                <div className="mt-0.5 text-[13px] text-muted">
                  {probeProgressHint(progress.proxyId)}
                </div>
              </div>
            </div>
            <div className="font-mono text-sm text-accent">
              已等待 {elapsed}s
            </div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-line">
            <div
              className="h-full bg-accent transition-all duration-500"
              style={{
                width: `${Math.min(95, (progress.index - 1) * (100 / progress.total) + Math.min(30, elapsed / 10))}%`,
              }}
            />
          </div>
        </Panel>
      ) : null}

      {lastBatch.length > 0 && !progress ? (
        <Panel className="mb-4 px-4 py-3">
          <div className="mb-2 text-sm font-medium">本次探测结果</div>
          <ul className="space-y-2">
            {lastBatch.map((r) => (
              <li
                key={`${r.proxy_id}-${r.created_at}`}
                className="flex flex-wrap items-center justify-between gap-2 text-sm"
              >
                <span>{nameOf(r.proxy_id)}</span>
                <div className="flex items-center gap-3">
                  <StatusPill ok={r.ok} label={r.ok ? "ok" : "fail"} />
                  <span className="font-mono text-[11px] text-muted">
                    {fmtMs(r.latency_ms)}
                  </span>
                  <span
                    className="max-w-[240px] truncate text-[12px] text-muted"
                    title={r.detail || ""}
                  >
                    {r.detail}
                  </span>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[12px] text-muted">
            记录已写入请求历史（Overview 最近请求 / 下方最近探测）。
          </p>
        </Panel>
      ) : null}

      {loading && !data ? <Skeleton className="h-64 mb-4" /> : null}

      {data ? (
        <Reveal>
          <Panel className="overflow-x-auto mb-6">
            <table className="w-full min-w-[720px] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-muted">
                  <th className="px-4 py-3 font-medium">Proxy</th>
                  <th className="px-4 py-3 font-mono text-[11px] uppercase font-medium">
                    probe
                  </th>
                  <th className="px-4 py-3 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {data.proxies.map((p) => {
                  const cell = matrix.get(`${p.id}::chat`);
                  const rowBusy = busy === p.id || (busy === "all" && progress?.proxyId === p.id);
                  return (
                    <tr
                      key={p.id}
                      className="border-b border-line/70 last:border-0"
                    >
                      <td className="px-4 py-3 align-top">
                        <div className="font-medium">{p.name}</div>
                        <div className="mt-1 font-mono text-[11px] text-muted">
                          {p.keepalive ? "keepalive on" : "no keepalive"}
                        </div>
                      </td>
                      <td className="px-4 py-3 align-top">
                        {cell ? (
                          <div className="space-y-1">
                            <StatusPill
                              ok={cell.ok}
                              label={cell.ok ? "ok" : "fail"}
                            />
                            <div className="font-mono text-[11px] text-muted">
                              {fmtMs(cell.latency_ms)}
                            </div>
                            <div
                              className="text-[11px] text-muted truncate max-w-[220px]"
                              title={cell.detail || ""}
                            >
                              {cell.detail}
                            </div>
                            <div className="font-mono text-[10px] text-muted">
                              {fmtTime(cell.created_at)}
                            </div>
                          </div>
                        ) : (
                          <span className="text-muted text-[11px]">未测</span>
                        )}
                      </td>
                      <td className="px-4 py-3 align-top">
                        <GhostButton
                          disabled={busy !== null}
                          onClick={() => void runProbe(p.id)}
                        >
                          {rowBusy ? (
                            <>
                              <CircleNotch
                                size={14}
                                className="animate-spin"
                              />
                              探测中 {elapsed}s
                            </>
                          ) : (
                            "探测"
                          )}
                        </GhostButton>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Panel>
        </Reveal>
      ) : null}

      <Reveal delay={0.05} className="mb-6">
        <h2 className="text-lg font-semibold tracking-tight mb-3">最近探测请求</h2>
        <Panel className="overflow-hidden">
          {recentProbes.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-muted">
              暂无探测记录。
            </div>
          ) : (
            <ul className="divide-y divide-line max-h-[320px] overflow-y-auto">
              {recentProbes.slice(0, 15).map((r) => {
                const bad = r.status_code == null || r.status_code >= 400;
                return (
                  <li key={r.id} className="px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium">
                        {nameOf(r.proxy_id)}
                      </span>
                      <span
                        className={`font-mono text-[11px] ${bad ? "text-fail" : "text-ok"}`}
                      >
                        {r.status_code ?? "ERR"}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-3 font-mono text-[11px] text-muted">
                      <span>{fmtTime(r.created_at)}</span>
                      <span>{fmtMs(r.latency_ms)}</span>
                      <span>{r.path || r.mode}</span>
                      <span>{r.model || "-"}</span>
                    </div>
                    {r.error ? (
                      <div className="mt-1 text-[12px] text-fail truncate" title={r.error}>
                        {r.error}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>
      </Reveal>

      <Reveal delay={0.08}>
        <h2 className="text-lg font-semibold tracking-tight mb-3">登录与保活</h2>
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
          {auth.map((a) => (
            <Panel key={a.proxy_id} className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{a.name}</div>
                  <div className="mt-1 font-mono text-[11px] text-muted">
                    {a.proxy_id}
                  </div>
                </div>
                <StatusPill ok={a.state === "ok"} label={a.state} />
              </div>
              {a.message ? (
                <p className="mt-3 text-sm text-muted">{a.message}</p>
              ) : (
                <p className="mt-3 text-sm text-muted">
                  {a.keepalive
                    ? "若 2 天内无业务请求，后台会自动发一次聊天保活。"
                    : "该服务不参与自动保活。"}
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-3 font-mono text-[11px] text-muted">
                <span>ok {fmtTime(a.last_ok_at)}</span>
                <span>fail {fmtTime(a.last_fail_at)}</span>
              </div>
              {LOGIN_REFRESH_PROXY_IDS.has(a.proxy_id) ? (
                <div className="mt-4 space-y-2">
                  {a.state === "login_required" ? (
                    <div className="rounded-md border border-fail/30 bg-fail/5 p-3">
                      <div className="flex items-center gap-2 text-sm text-fail">
                        <SignIn size={16} weight="bold" />
                        需要刷新登录态
                      </div>
                      <p className="mt-2 text-[13px] text-muted leading-relaxed">
                        登录后点「保存登录态」即可写回。
                      </p>
                    </div>
                  ) : null}
                  <div className="flex flex-wrap gap-2">
                    <PrimaryButton
                      disabled={busy !== null}
                      onClick={() =>
                        setLoginTarget({ proxyId: a.proxy_id, name: a.name })
                      }
                    >
                      网页刷新登录
                    </PrimaryButton>
                    {a.state === "login_required" ? (
                      <GhostButton
                        disabled={busy !== null}
                        onClick={() => void markRefreshed(a.proxy_id)}
                      >
                        我已手动刷新
                      </GhostButton>
                    ) : null}
                  </div>
                </div>
              ) : a.state === "login_required" ? (
                <div className="mt-4 rounded-md border border-fail/30 bg-fail/5 p-3">
                  <div className="flex items-center gap-2 text-sm text-fail">
                    <SignIn size={16} weight="bold" />
                    需要刷新登录态
                  </div>
                  <p className="mt-2 text-[13px] text-muted leading-relaxed">
                    请在对应服务上刷新登录态后确认。
                  </p>
                  <div className="mt-3">
                    <PrimaryButton
                      disabled={busy !== null}
                      onClick={() => void markRefreshed(a.proxy_id)}
                    >
                      我已刷新登录
                    </PrimaryButton>
                  </div>
                </div>
              ) : null}
            </Panel>
          ))}
        </div>
      </Reveal>
      {loginTarget ? (
        <LoginRefreshModal
          proxyId={loginTarget.proxyId}
          name={loginTarget.name}
          onClose={() => setLoginTarget(null)}
          onSaved={() => {
            setLoginTarget(null);
            void load();
          }}
        />
      ) : null}
    </div>
  );
}
