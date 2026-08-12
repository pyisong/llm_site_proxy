import { useEffect, useRef, useState } from "react";
import { X } from "@phosphor-icons/react";
import { api, formatApiError } from "../api";
import { PrimaryButton } from "../components/ui";

type Props = {
  proxyId: string;
  name: string;
  onClose: () => void;
  onSaved: () => void;
};

const VW = 1280;
const VH = 900;

export default function LoginRefreshModal({
  proxyId,
  name,
  onClose,
  onSaved,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [homeUrl, setHomeUrl] = useState("");
  const [status, setStatus] = useState("启动浏览器…");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [busyStart, setBusyStart] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocket | null = null;

    (async () => {
      try {
        const sess = await api.startLoginSession(proxyId);
        if (cancelled) {
          await api.closeLoginSession(sess.session_id).catch(() => undefined);
          return;
        }
        setSessionId(sess.session_id);
        setNotes(sess.notes || "");
        setHomeUrl(sess.home_url || "");
        setStatus("已连接，请在下方画面中登录");
        setBusyStart(false);

        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const url = `${proto}//${window.location.host}${sess.ws_path}`;
        ws = new WebSocket(url);
        wsRef.current = ws;
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(String(ev.data));
            if (msg.type === "hello") {
              setNotes(msg.notes || "");
              return;
            }
            if (msg.type === "frame" && typeof msg.data === "string") {
              const canvas = canvasRef.current;
              if (!canvas) return;
              const img = new Image();
              img.onload = () => {
                const ctx = canvas.getContext("2d");
                if (!ctx) return;
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
              };
              img.src = `data:image/jpeg;base64,${msg.data}`;
            }
          } catch {
            /* ignore */
          }
        };
        ws.onerror = () => setError("画面通道异常");
        ws.onclose = () => setStatus((s) => (s.includes("已保存") ? s : "画面已断开"));
      } catch (e) {
        setError(formatApiError(e, "无法启动登录会话"));
        setBusyStart(false);
      }
    })();

    return () => {
      cancelled = true;
      ws?.close();
      wsRef.current = null;
    };
  }, [proxyId]);

  useEffect(() => {
    return () => {
      if (sessionId) {
        void api.closeLoginSession(sessionId).catch(() => undefined);
      }
    };
  }, [sessionId]);

  function mapPoint(ev: React.MouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    const x = ((ev.clientX - rect.left) / rect.width) * VW;
    const y = ((ev.clientY - rect.top) / rect.height) * VH;
    return { x, y };
  }

  function send(msg: Record<string, unknown>) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  async function save() {
    if (!sessionId) return;
    setSaving(true);
    setError(null);
    try {
      const result = await api.saveLoginSession(sessionId);
      setSessionId(null);
      const reloadOk = Boolean(result.reload?.ok);
      setStatus(
        reloadOk
          ? "已保存并热加载"
          : "已写入 storage；该服务若缓存登录态，可能仍需重启进程（未自动重启）",
      );
      onSaved();
    } catch (e) {
      setError(formatApiError(e, "保存失败"));
    } finally {
      setSaving(false);
    }
  }

  async function closeAll() {
    if (sessionId) {
      await api.closeLoginSession(sessionId).catch(() => undefined);
      setSessionId(null);
    }
    onClose();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="flex max-h-[95dvh] w-full max-w-[1100px] flex-col overflow-hidden rounded-md border border-line bg-canvas shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
          <div>
            <div className="text-sm font-semibold">刷新登录态 · {name}</div>
            <div className="mt-0.5 font-mono text-[11px] text-muted">{proxyId}</div>
            {homeUrl ? (
              <div className="mt-1 text-[12px] text-muted">{homeUrl}</div>
            ) : null}
          </div>
          <button
            type="button"
            className="rounded-md p-1 text-muted hover:bg-panel hover:text-ink"
            onClick={() => void closeAll()}
            aria-label="关闭"
          >
            <X size={18} weight="bold" />
          </button>
        </div>

        <div className="space-y-2 border-b border-line px-4 py-2 text-[13px] text-muted">
          <div>{status}</div>
          {notes ? <div>{notes}</div> : null}
          {error ? <div className="text-fail">{error}</div> : null}
        </div>

        <div className="min-h-0 flex-1 overflow-auto bg-panel-2/40 p-3">
          <canvas
            ref={canvasRef}
            width={VW}
            height={VH}
            tabIndex={0}
            className="mx-auto block max-h-[70dvh] w-full cursor-crosshair rounded-md border border-line bg-black object-contain outline-none"
            onClick={(ev) => {
              const { x, y } = mapPoint(ev);
              send({ type: "click", x, y });
              canvasRef.current?.focus();
            }}
            onDoubleClick={(ev) => {
              const { x, y } = mapPoint(ev);
              send({ type: "dblclick", x, y });
            }}
            onWheel={(ev) => {
              ev.preventDefault();
              send({ type: "wheel", dx: ev.deltaX, dy: ev.deltaY });
            }}
            onKeyDown={(ev) => {
              if (ev.key === "Tab") ev.preventDefault();
              // Prefer printable via type; specials via keydown
              if (ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
                send({ type: "type", text: ev.key });
                ev.preventDefault();
                return;
              }
              const map: Record<string, string> = {
                Enter: "Enter",
                Backspace: "Backspace",
                Escape: "Escape",
                Tab: "Tab",
                ArrowLeft: "ArrowLeft",
                ArrowRight: "ArrowRight",
                ArrowUp: "ArrowUp",
                ArrowDown: "ArrowDown",
              };
              const key = map[ev.key];
              if (key) {
                send({ type: "keydown", key });
                ev.preventDefault();
              }
            }}
          />
          {busyStart ? (
            <div className="py-8 text-center text-sm text-muted">正在启动远程浏览器…</div>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-4 py-3">
          <button
            type="button"
            className="rounded-md border border-line px-3 py-2 text-sm text-muted hover:bg-panel"
            onClick={() => void closeAll()}
          >
            取消
          </button>
          <PrimaryButton
            disabled={!sessionId || saving || busyStart}
            onClick={() => void save()}
          >
            {saving ? "保存中…" : "保存登录态"}
          </PrimaryButton>
        </div>
      </div>
    </div>
  );
}
