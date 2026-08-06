import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl md:text-3xl font-semibold tracking-tight leading-[1.1]">
          {title}
        </h1>
        {subtitle ? (
          <p className="mt-1 text-sm text-muted max-w-[65ch]">{subtitle}</p>
        ) : null}
      </div>
      {action}
    </div>
  );
}

export function Panel({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-md border border-line bg-panel/80 ${className}`}
    >
      {children}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-md bg-panel-2/80 ${className}`}
      aria-hidden
    />
  );
}

export function StatusPill({
  ok,
  label,
}: {
  ok?: boolean | null;
  label: string;
}) {
  const tone =
    ok === true ? "text-ok border-ok/30 bg-ok/10" : ok === false
      ? "text-fail border-fail/30 bg-fail/10"
      : "text-warn border-warn/30 bg-warn/10";
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-[11px] ${tone}`}
    >
      {label}
    </span>
  );
}

export function Reveal({
  children,
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay, ease: [0.16, 1, 0.3, 1] }}
    >
      {children}
    </motion.div>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="px-6 py-12 text-center">
      <div className="text-sm font-medium text-ink">{title}</div>
      <p className="mt-1 text-sm text-muted">{body}</p>
    </div>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-4 rounded-md border border-fail/40 bg-fail/10 px-4 py-3 text-sm text-fail">
      {message}
    </div>
  );
}

export function PrimaryButton({
  children,
  onClick,
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className="inline-flex items-center justify-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-medium text-canvas transition hover:brightness-110 active:scale-[0.98] disabled:opacity-50 disabled:pointer-events-none"
    >
      {children}
    </button>
  );
}

export function GhostButton({
  children,
  onClick,
  disabled,
  danger,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={[
        "inline-flex items-center justify-center gap-2 rounded-md border px-3 py-1.5 text-sm transition active:scale-[0.98] disabled:opacity-50",
        danger
          ? "border-fail/40 text-fail hover:bg-fail/10"
          : "border-line text-muted hover:text-ink hover:bg-panel-2",
      ].join(" ")}
    >
      {children}
    </button>
  );
}
