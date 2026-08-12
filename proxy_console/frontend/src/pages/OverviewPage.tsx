import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowClockwise, X } from "@phosphor-icons/react";
import {
  api,
  authTone,
  fmtMs,
  fmtTime,
  type Overview,
  type RequestEvent,
} from "../api";
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

const WINDOW_OPTIONS = [
  { label: "1 小时", short: "1h", sec: 3600 },
  { label: "6 小时", short: "6h", sec: 21600 },
  { label: "24 小时", short: "24h", sec: 86400 },
  { label: "7 天", short: "7d", sec: 604800 },
  { label: "30 天", short: "30d", sec: 2592000 },
] as const;

const WINDOW_STORAGE_KEY = "proxy_console_overview_window_sec";

function readStoredWindowSec(): number {
  try {
    const raw = localStorage.getItem(WINDOW_STORAGE_KEY);
    const n = raw ? Number(raw) : NaN;
    if (WINDOW_OPTIONS.some((o) => o.sec === n)) return n;
  } catch {
    /* ignore */
  }
  return 3600;
}

function formatWindowLabel(sec: number): string {
  const hit = WINDOW_OPTIONS.find((o) => o.sec === sec);
  return hit?.label ?? `${Math.round(sec / 3600)} 小时`;
}

function formatBucketHint(bucketSec: number): string {
  if (bucketSec < 3600) return `每 ${Math.round(bucketSec / 60)} 分钟一段`;
  if (bucketSec < 86400) return `每 ${Math.round(bucketSec / 3600)} 小时一段`;
  return `每 ${Math.round(bucketSec / 86400)} 天一段`;
}

export default function OverviewPage() {
  const [windowSec, setWindowSec] = useState(readStoredWindowSec);
  const [data, setData] = useState<Overview | null>(null);
  const [requests, setRequests] = useState<RequestEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<RequestEvent | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [ov, req] = await Promise.all([
        api.overview(windowSec),
        api.requests(40),
      ]);
      setData(ov);
      setRequests(req.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [windowSec]);

  useEffect(() => {
    setLoading(true);
    void load();
    const t = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(t);
  }, [load]);

  const onSelectWindow = (sec: number) => {
    setWindowSec(sec);
    try {
      localStorage.setItem(WINDOW_STORAGE_KEY, String(sec));
    } catch {
      /* ignore */
    }
  };

  const maxReq = useMemo(
    () => Math.max(1, ...(data?.series.map((s) => s.requests) || [1])),
    [data],
  );

  const seriesPeak = useMemo(() => {
    if (!data?.series.length) return null;
    let peak = data.series[0];
    for (const b of data.series) {
      if (b.requests > peak.requests) peak = b;
    }
    return peak;
  }, [data]);

  const windowLabel = formatWindowLabel(data?.window_sec ?? windowSec);
  const bucketSec = data?.bucket_sec ?? 300;
  const windowShort =
    WINDOW_OPTIONS.find((o) => o.sec === (data?.window_sec ?? windowSec))
      ?.short ?? windowLabel;

  return (
    <div>
      <PageHeader
        title="Overview"
        subtitle="各 proxy 吞吐、错误与登录态一屏扫读。数据每 15 秒刷新。"
        action={
          <div className="flex flex-wrap items-center gap-2">
            <div
              className="inline-flex flex-wrap items-center gap-1 rounded-md border border-line bg-panel-2/60 p-1"
              role="group"
              aria-label="查询时间区间"
            >
              {WINDOW_OPTIONS.map((opt) => {
                const active = windowSec === opt.sec;
                return (
                  <button
                    key={opt.sec}
                    type="button"
                    onClick={() => onSelectWindow(opt.sec)}
                    className={
                      active
                        ? "rounded px-2.5 py-1 text-[12px] font-medium bg-accent/15 text-accent border border-accent/30"
                        : "rounded px-2.5 py-1 text-[12px] text-muted hover:text-ink hover:bg-panel transition border border-transparent"
                    }
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
            <GhostButton onClick={() => void load()}>
              <ArrowClockwise size={16} />
              刷新
            </GhostButton>
          </div>
        }
      />

      {error ? <ErrorBanner message={error} /> : null}

      {loading && !data ? (
        <div className="grid gap-4">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-24" />
            ))}
          </div>
          <Skeleton className="h-28" />
          <Skeleton className="h-72" />
        </div>
      ) : null}

      {data ? (
        <>
          <Reveal className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
            <Kpi
              label={`请求 (${windowShort})`}
              value={String(data.kpi.requests)}
              hint={`${data.kpi.errors} 错误 · ${windowLabel}`}
            />
            <Kpi
              label="错误率"
              value={`${(data.kpi.error_rate * 100).toFixed(1)}%`}
              hint="含 4xx/5xx"
            />
            <Kpi
              label="平均延迟"
              value={fmtMs(data.kpi.avg_latency_ms)}
              hint="窗口内均值"
            />
            <Kpi
              label="登录正常"
              value={`${data.kpi.services_online}/${data.kpi.services_total}`}
              hint="auth_state = ok"
            />
          </Reveal>

          <Reveal delay={0.05} className="mb-4">
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3">
              {data.services.map((s) => (
                <Panel key={s.id} className="px-4 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium truncate">{s.name}</span>
                    <StatusPill
                      ok={s.auth_state === "ok"}
                      label={s.auth_state}
                    />
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 font-mono text-[11px]">
                    <div>
                      <div className="text-muted">req</div>
                      <div className="text-ink text-sm">{s.requests}</div>
                    </div>
                    <div>
                      <div className="text-muted">err</div>
                      <div
                        className={`text-sm ${s.errors ? "text-fail" : "text-ink"}`}
                      >
                        {s.errors}
                      </div>
                    </div>
                    <div>
                      <div className="text-muted">lat</div>
                      <div className="text-ink text-sm">
                        {fmtMs(s.avg_latency_ms)}
                      </div>
                    </div>
                  </div>
                  {s.auth_message ? (
                    <div
                      className={`mt-2 text-[11px] truncate ${authTone(s.auth_state)}`}
                      title={s.auth_message}
                    >
                      {s.auth_message}
                    </div>
                  ) : null}
                </Panel>
              ))}
            </div>
          </Reveal>

          <div className="grid lg:grid-cols-[1.2fr_1fr] gap-4">
            <Reveal delay={0.1}>
              <Panel className="p-4 md:p-5">
                <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-medium">
                      近 {windowLabel}请求量
                    </h2>
                    <p className="mt-0.5 text-[12px] text-muted">
                      {formatBucketHint(bucketSec)} · 柱高 = 请求数 · 红段 =
                      失败
                    </p>
                  </div>
                  <div className="flex items-center gap-3 font-mono text-[11px] text-muted">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="inline-block h-2 w-2 rounded-sm bg-accent" />
                      成功
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                      <span className="inline-block h-2 w-2 rounded-sm bg-fail" />
                      失败
                    </span>
                    {seriesPeak && seriesPeak.requests > 0 ? (
                      <span className="text-ink">
                        峰值 {seriesPeak.requests}
                        <span className="text-muted">
                          {" "}
                          @ {fmtBucketLabel(seriesPeak.t, data.window_sec)}
                        </span>
                      </span>
                    ) : null}
                  </div>
                </div>

                <ThroughputChart
                  series={data.series}
                  maxReq={maxReq}
                  windowSec={data.window_sec}
                />
              </Panel>
            </Reveal>

            <Reveal delay={0.14}>
              <Panel className="overflow-hidden">
                <div className="px-4 py-3 border-b border-line flex items-center justify-between">
                  <h2 className="text-sm font-medium">最近请求</h2>
                  <span className="font-mono text-[11px] text-muted">
                    {requests.length}
                  </span>
                </div>
                {requests.length === 0 ? (
                  <EmptyState
                    title="暂无请求"
                    body="各 proxy 上报 ingest 后会出现在这里。"
                  />
                ) : (
                  <ul className="max-h-[360px] overflow-y-auto divide-y divide-line">
                    {requests.map((r) => {
                      const bad =
                        r.status_code == null || r.status_code >= 400;
                      return (
                        <li key={r.id}>
                          <button
                            type="button"
                            onClick={() => setSelected(r)}
                            className="w-full text-left px-4 py-3 hover:bg-panel-2/80 transition"
                          >
                            <div className="flex items-center justify-between gap-3">
                              <span className="text-sm font-medium truncate">
                                {r.proxy_id
                                  .replace("-openai-proxy", "")
                                  .replace("-http-api", "")}
                              </span>
                              <span
                                className={`font-mono text-[11px] ${bad ? "text-fail" : "text-ok"}`}
                              >
                                {r.status_code ?? "ERR"}
                              </span>
                            </div>
                            <div className="mt-1 flex items-center gap-3 font-mono text-[11px] text-muted">
                              <span>{fmtTime(r.created_at)}</span>
                              <span>{fmtMs(r.latency_ms)}</span>
                              <span className="truncate">
                                {r.model || r.mode}
                              </span>
                            </div>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </Panel>
            </Reveal>
          </div>
        </>
      ) : null}

      {selected ? (
        <RequestDrawer item={selected} onClose={() => setSelected(null)} />
      ) : null}
    </div>
  );
}

function fmtBucketLabel(ts: number, windowSec: number): string {
  const d = new Date(ts * 1000);
  if (windowSec <= 86400) {
    return d.toLocaleString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }
  if (windowSec <= 7 * 86400) {
    return d.toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      hour12: false,
    });
  }
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

function ThroughputChart({
  series,
  maxReq,
  windowSec,
}: {
  series: { t: number; requests: number; errors: number }[];
  maxReq: number;
  windowSec: number;
}) {
  const yTicks = useMemo(() => {
    const top = Math.max(1, maxReq);
    if (top <= 2) return [0, top];
    if (top <= 4) return [0, Math.ceil(top / 2), top];
    return [0, Math.round(top / 2), top];
  }, [maxReq]);

  const labelEvery = Math.max(1, Math.ceil(series.length / 6));
  const chartH = 176; // px

  return (
    <div className="flex gap-3">
      <div
        className="flex w-8 shrink-0 flex-col justify-between text-right font-mono text-[10px] text-muted"
        style={{ height: chartH }}
      >
        {[...yTicks].reverse().map((v) => (
          <span key={v}>{v}</span>
        ))}
      </div>

      <div className="min-w-0 flex-1">
        <div
          className="relative flex items-end gap-[3px] border-b border-line"
          style={{ height: chartH }}
        >
          {yTicks.slice(1).map((v) => (
            <div
              key={v}
              className="pointer-events-none absolute inset-x-0 border-t border-line/50"
              style={{ bottom: `${(v / maxReq) * 100}%` }}
            />
          ))}

          {series.map((b, i) => {
            const ok = Math.max(0, b.requests - b.errors);
            const err = Math.max(0, Math.min(b.errors, b.requests));
            const okH = (ok / maxReq) * chartH;
            const errH = (err / maxReq) * chartH;
            const showLabel = i % labelEvery === 0 || i === series.length - 1;
            const label = fmtBucketLabel(b.t, windowSec);

            return (
              <div
                key={b.t}
                className="group relative z-[1] flex h-full flex-1 flex-col justify-end"
              >
                <div
                  className="absolute bottom-full left-1/2 z-10 mb-2 hidden w-max -translate-x-1/2 rounded border border-line bg-panel-2 px-2 py-1.5 font-mono text-[11px] shadow-lg group-hover:block"
                  role="tooltip"
                >
                  <div className="text-ink">{label}</div>
                  <div className="mt-0.5 text-muted">
                    共 <span className="text-ink">{b.requests}</span>
                    {" · "}
                    <span className="text-ok">{ok} 成功</span>
                    {" · "}
                    <span className="text-fail">{err} 失败</span>
                  </div>
                </div>

                {b.requests > 0 ? (
                  <div className="flex w-full flex-col justify-end overflow-hidden rounded-t-sm">
                    {errH > 0 ? (
                      <div
                        className="w-full bg-fail/90 group-hover:bg-fail"
                        style={{ height: Math.max(errH, err > 0 ? 2 : 0) }}
                      />
                    ) : null}
                    {okH > 0 ? (
                      <div
                        className="w-full bg-accent/80 group-hover:bg-accent"
                        style={{ height: Math.max(okH, ok > 0 ? 2 : 0) }}
                      />
                    ) : null}
                  </div>
                ) : (
                  <div className="mx-auto h-px w-full max-w-[80%] bg-line/40" />
                )}

                {showLabel ? (
                  <span className="pointer-events-none absolute -bottom-5 left-1/2 -translate-x-1/2 font-mono text-[10px] text-muted whitespace-nowrap">
                    {label}
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
        <div className="h-5" aria-hidden />
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint: string;
}) {
  return (
    <Panel className="px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-muted">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tracking-tight font-mono">
        {value}
      </div>
      <div className="mt-1 text-[11px] text-muted">{hint}</div>
    </Panel>
  );
}

function RequestDrawer({
  item,
  onClose,
}: {
  item: RequestEvent;
  onClose: () => void;
}) {
  const meta =
    item.meta && typeof item.meta === "object"
      ? (item.meta as Record<string, unknown>)
      : null;
  const requestBody = meta?.request;
  const responseText =
    typeof meta?.response_text === "string" ? meta.response_text : null;
  const responseBody = meta?.response;
  const source = typeof meta?.source === "string" ? meta.source : null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button
        type="button"
        className="absolute inset-0 bg-black/50"
        aria-label="关闭"
        onClick={onClose}
      />
      <aside className="relative z-10 h-full w-full max-w-lg border-l border-line bg-panel shadow-2xl shadow-black/40 p-5 overflow-y-auto">
        <div className="flex items-start justify-between gap-3 mb-4">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">请求详情</h2>
            <p className="mt-1 font-mono text-[11px] text-muted break-all">
              {item.id}
            </p>
          </div>
          <GhostButton onClick={onClose}>
            <X size={16} />
          </GhostButton>
        </div>
        <dl className="space-y-3 text-sm">
          {(
            [
              ["proxy", item.proxy_id],
              ["mode", item.mode],
              ["path", item.path || "-"],
              ["status", String(item.status_code ?? "-")],
              ["latency", fmtMs(item.latency_ms)],
              ["model", item.model || "-"],
              ["time", fmtTime(item.created_at)],
              ["source", source || "-"],
              ["error", item.error || "-"],
            ] as const
          ).map(([k, v]) => (
            <div
              key={k}
              className="grid grid-cols-[88px_1fr] gap-2 border-b border-line/60 pb-2"
            >
              <dt className="text-muted font-mono text-[11px] uppercase">{k}</dt>
              <dd className="break-all">{v}</dd>
            </div>
          ))}
        </dl>

        <section className="mt-5">
          <h3 className="text-sm font-medium mb-2">输入 (request)</h3>
          {requestBody ? (
            <pre className="rounded-md border border-line bg-canvas p-3 text-[11px] font-mono overflow-x-auto text-ink whitespace-pre-wrap break-all">
              {JSON.stringify(requestBody, null, 2)}
            </pre>
          ) : (
            <p className="text-sm text-muted">
              无请求体记录。新上报会写入截断后的输入；旧记录需重新请求后才会出现。
            </p>
          )}
        </section>

        <section className="mt-5">
          <h3 className="text-sm font-medium mb-2">输出 (response)</h3>
          {responseText ? (
            <div className="rounded-md border border-accent/30 bg-accent/5 p-3 text-sm whitespace-pre-wrap break-words mb-3">
              {responseText}
            </div>
          ) : null}
          {responseBody ? (
            <pre className="rounded-md border border-line bg-canvas p-3 text-[11px] font-mono overflow-x-auto text-muted whitespace-pre-wrap break-all">
              {JSON.stringify(responseBody, null, 2)}
            </pre>
          ) : !responseText ? (
            <p className="text-sm text-muted">
              无响应内容。失败时通常只有 error 字段；成功请求会显示模型回复摘要。
            </p>
          ) : null}
        </section>

        <div className="mt-6">
          <PrimaryButton onClick={onClose}>关闭</PrimaryButton>
        </div>
      </aside>
    </div>
  );
}
