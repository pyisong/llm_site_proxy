export type ServiceSummary = {
  id: string;
  name: string;
  keepalive: boolean;
  requests: number;
  errors: number;
  avg_latency_ms: number | null;
  auth_state: string;
  auth_message: string | null;
};

export type Overview = {
  window_sec: number;
  bucket_sec?: number;
  kpi: {
    requests: number;
    errors: number;
    error_rate: number;
    avg_latency_ms: number | null;
    services_online: number;
    services_total: number;
  };
  services: ServiceSummary[];
  series: { t: number; requests: number; errors: number }[];
  generated_at: number;
};

export type RequestEvent = {
  id: string;
  proxy_id: string;
  mode: string;
  path: string | null;
  status_code: number | null;
  latency_ms: number | null;
  model: string | null;
  error: string | null;
  created_at: number;
  meta: unknown;
};

export type ConnectivityPayload = {
  results: {
    id: string;
    proxy_id: string;
    mode: string;
    ok: boolean;
    latency_ms: number | null;
    detail: string | null;
    created_at: number;
    auth_state?: string;
    auth_message?: string | null;
  }[];
  proxies: { id: string; name: string; keepalive: boolean }[];
  modes: string[];
};

export type SkillItem = {
  name: string;
  description?: string;
  path?: string;
  valid?: boolean;
  disabled: boolean;
  disabled_at?: number | null;
  disabled_reason?: string | null;
  uses: number;
  last_used_at?: number | null;
};

/** 把 Bridge / FastAPI 错误体收成一行可读文案（对齐其它页面的短错误风格）。 */
export function formatApiError(payload: unknown, fallback = "请求失败"): string {
  if (payload == null) return fallback;
  if (typeof payload === "string") {
    const t = payload.trim();
    if (t.startsWith("{") || t.startsWith("[")) {
      try {
        return formatApiError(JSON.parse(t), t);
      } catch {
        return t;
      }
    }
    return t || fallback;
  }
  if (typeof payload !== "object") return String(payload);

  const o = payload as Record<string, unknown>;

  if (o.error && typeof o.error === "object") {
    const e = o.error as Record<string, unknown>;
    if (typeof e.message === "string" && e.message.trim()) {
      return e.message.trim();
    }
  }
  if (typeof o.message === "string" && o.message.trim()) {
    return o.message.trim();
  }
  if (typeof o.detail === "string" && o.detail.trim()) {
    return formatApiError(o.detail, o.detail.trim());
  }
  if (Array.isArray(o.detail)) {
    const parts = o.detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return null;
      })
      .filter(Boolean);
    if (parts.length) return parts.join("；");
  }
  if (o.detail != null && typeof o.detail === "object") {
    return formatApiError(o.detail, fallback);
  }
  try {
    return JSON.stringify(payload);
  } catch {
    return fallback;
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      detail = await res.json();
    } catch {
      /* ignore */
    }
    throw new Error(formatApiError(detail, res.statusText || "请求失败"));
  }
  // 202 Accepted（异步 job）也当成功
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export const api = {
  overview: (windowSec = 3600) =>
    json<Overview>(`/api/overview?window_sec=${windowSec}`),
  requests: (limit = 40) =>
    json<{ items: RequestEvent[] }>(`/api/requests?limit=${limit}`),
  request: (id: string) => json<RequestEvent>(`/api/requests/${id}`),
  connectivity: () => json<ConnectivityPayload>("/api/connectivity"),
  probe: (body: { proxy_id?: string; mode?: string } = {}) =>
    json<{
      results: {
        id: string;
        proxy_id: string;
        mode: string;
        ok: boolean;
        latency_ms: number | null;
        detail: string | null;
        created_at: number;
      }[];
    }>("/api/connectivity/probe", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  authStatus: () =>
    json<{
      items: {
        proxy_id: string;
        name: string;
        state: string;
        message: string | null;
        keepalive: boolean;
        last_ok_at: number | null;
        last_fail_at: number | null;
      }[];
    }>("/api/auth-status"),
  markRefreshed: (proxyId: string) =>
    json(`/api/auth/${proxyId}/mark-refreshed`, { method: "POST" }),
  skills: () =>
    json<{ skills: SkillItem[]; source?: string; error?: string }>(
      "/api/skills",
    ),
  installSkill: (body: Record<string, unknown>) =>
    json<{
      name?: string;
      id?: string;
      status?: string;
      job_id?: string;
      result?: { name?: string };
    }>("/api/skills/install", { method: "POST", body: JSON.stringify(body) }),
  skillJob: (jobId: string) =>
    json<{
      id: string;
      status: string;
      error?: string;
      result?: { name?: string };
      ref?: string;
    }>(`/api/skills/jobs/${encodeURIComponent(jobId)}`),
  uploadSkill: (opts: {
    file: File;
    name?: string;
    overwrite?: boolean;
    subdir?: string;
    onProgress?: (ratio: number) => void;
  }) =>
    new Promise<{ name?: string }>((resolve, reject) => {
      const fd = new FormData();
      fd.append("file", opts.file);
      if (opts.name) fd.append("name", opts.name);
      fd.append("overwrite", opts.overwrite === false ? "false" : "true");
      if (opts.subdir) fd.append("subdir", opts.subdir);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/skills/upload");
      xhr.upload.onprogress = (ev) => {
        if (!opts.onProgress) return;
        if (ev.lengthComputable && ev.total > 0) {
          opts.onProgress(Math.min(1, ev.loaded / ev.total));
        }
      };
      xhr.onload = () => {
        let data: unknown = null;
        try {
          data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
        } catch {
          data = { detail: xhr.responseText || xhr.statusText };
        }
        if (xhr.status >= 400) {
          reject(
            new Error(
              formatApiError(data, xhr.statusText || `HTTP ${xhr.status}`),
            ),
          );
          return;
        }
        opts.onProgress?.(1);
        resolve((data || {}) as { name?: string });
      };
      xhr.onerror = () => reject(new Error("上传失败：网络错误"));
      xhr.ontimeout = () => reject(new Error("上传超时"));
      xhr.timeout = 180_000;
      xhr.send(fd);
    }),
  deleteSkill: (name: string) =>
    json(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }),
  disableSkill: (name: string, reason?: string) =>
    json(`/api/skills/${encodeURIComponent(name)}/disable`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  enableSkill: (name: string) =>
    json(`/api/skills/${encodeURIComponent(name)}/enable`, { method: "POST" }),
  skillUsage: (name: string) =>
    json<{
      items: {
        id: string;
        skill_name: string;
        label: string;
        request_id: string | null;
        created_at: number;
      }[];
    }>(`/api/skills/${encodeURIComponent(name)}/usage`),
};

export function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "-";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function authTone(state: string): string {
  if (state === "ok") return "text-ok";
  if (state === "login_required") return "text-fail";
  return "text-warn";
}
