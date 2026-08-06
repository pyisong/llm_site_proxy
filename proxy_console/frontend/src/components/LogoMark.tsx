/** Proxy Console brand mark: client ↔ proxy hub ↔ upstream */
export function LogoMark({ className = "size-8" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <rect width="64" height="64" rx="14" className="fill-panel" />
      <rect
        x="1"
        y="1"
        width="62"
        height="62"
        rx="13"
        className="stroke-accent/35"
        strokeWidth="2"
      />
      <circle cx="16" cy="32" r="5" className="fill-accent" />
      <circle cx="48" cy="32" r="5" className="fill-accent" />
      <rect x="26" y="22" width="12" height="20" rx="3" className="fill-accent-dim" />
      <rect
        x="28"
        y="25"
        width="8"
        height="3"
        rx="1"
        className="fill-accent/90"
      />
      <rect
        x="28"
        y="30.5"
        width="8"
        height="3"
        rx="1"
        className="fill-accent/55"
      />
      <rect
        x="28"
        y="36"
        width="8"
        height="3"
        rx="1"
        className="fill-accent/35"
      />
      <path
        d="M21 32H26"
        className="stroke-accent"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
      <path
        d="M38 32H43"
        className="stroke-accent"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
