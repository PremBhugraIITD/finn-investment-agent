import { ReactNode, useEffect } from "react";
import { CATEGORY_LABEL } from "./types";
import { pct, tone, toneText } from "./lib";

export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", k); document.body.style.overflow = ""; };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center md:items-center" role="dialog" aria-modal="true" aria-label={title}>
      <button className="fade-in absolute inset-0 bg-ink/35" onClick={onClose} aria-label="Close" />
      <div className="sheet-in safe-b relative flex max-h-[88dvh] w-full max-w-lg flex-col rounded-t-3xl bg-paper md:rounded-3xl">
        <div className="mx-auto mt-2 h-1 w-9 rounded-full bg-line md:hidden" />
        <div className="flex items-center justify-between px-5 pb-2 pt-3">
          <h2 className="text-[17px] font-semibold tracking-tight">{title}</h2>
          <button onClick={onClose} className="grid h-9 w-9 place-items-center rounded-full text-mute hover:bg-line-2" aria-label="Close sheet">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="M3 3l10 10M13 3L3 13" /></svg>
          </button>
        </div>
        <div className="overflow-y-auto px-5 pb-6">{children}</div>
      </div>
    </div>
  );
}

export function Spark({ data, w = 64, h = 22, color }: { data: number[]; w?: number; h?: number; color?: string }) {
  if (!data || data.length < 2) return <svg width={w} height={h} />;
  const min = Math.min(...data), max = Math.max(...data), r = max - min || 1;
  const pts = data.map((v, i) => `${((i / (data.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - min) / r) * (h - 4)).toFixed(1)}`).join(" ");
  const up = data[data.length - 1] >= data[0];
  const c = color ?? (up ? "var(--color-gain)" : "var(--color-loss)");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden>
      <polyline points={pts} fill="none" stroke={c} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export function Bars({ data, w = 72, h = 22, hi }: { data: number[]; w?: number; h?: number; hi?: number }) {
  if (!data || data.length < 2) return <svg width={w} height={h} />;
  const max = Math.max(...data) || 1, bw = w / data.length;
  return (
    <svg width={w} height={h} aria-hidden>
      {data.map((v, i) => (
        <rect key={i} x={i * bw + 0.5} width={Math.max(bw - 1.5, 1)} y={h - (v / max) * h} height={(v / max) * h}
          fill={i === data.length - 1 ? "var(--color-accent)" : "var(--color-line)"} rx="1" />
      ))}
    </svg>
  );
}

export function CatPill({ cat }: { cat: string }) {
  const hot = ["results", "M&A", "management_change", "litigation", "fund_raise", "credit_rating"].includes(cat);
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-medium leading-none ${hot ? "bg-accent-soft text-accent" : "bg-line-2 text-mute"}`}>
      {CATEGORY_LABEL[cat] ?? cat}
    </span>
  );
}

export function Delta({ v, digits = 1, className = "" }: { v: number | null | undefined; digits?: number; className?: string }) {
  return <span className={`num ${toneText[tone(v)]} ${className}`}>{pct(v, digits)}</span>;
}

export function StatusChip({ stale, source, asOf, refreshing }: { stale?: boolean; source?: string; asOf?: string; refreshing?: boolean }) {
  const label = refreshing ? "Updating" : stale ? "Snapshot" : "Live";
  const cls = stale ? "bg-warn-soft text-warn" : "bg-gain-soft text-gain";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px] font-medium leading-none ${cls}`} title={source}>
      <span className={`h-1.5 w-1.5 rounded-full ${stale ? "bg-warn" : "bg-gain"} ${refreshing ? "animate-pulse" : ""}`} />
      {label}{asOf ? ` · ${asOf}` : ""}
    </span>
  );
}

export function Skeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="divide-y divide-line-2" aria-busy>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="space-y-2 px-4 py-4">
          <div className="skel h-3 w-1/3" /><div className="skel h-4 w-11/12" /><div className="skel h-4 w-2/3" />
        </div>
      ))}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="px-6 py-16 text-center">
      <p className="text-[15px] font-medium">{title}</p>
      {hint && <p className="mx-auto mt-1 max-w-xs text-[13px] text-mute">{hint}</p>}
    </div>
  );
}

export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  return (
    <div className="m-4 rounded-2xl bg-loss-soft p-4 text-[13px] text-loss">
      <p className="font-medium">Couldn't load this</p>
      <p className="mt-0.5 opacity-80">{message}</p>
      {retry && <button onClick={retry} className="mt-2 rounded-lg bg-surface px-3 py-1.5 font-medium text-ink">Retry</button>}
    </div>
  );
}

export function Chip({ active, onClick, children, count }: { active?: boolean; onClick: () => void; children: ReactNode; count?: number }) {
  return (
    <button onClick={onClick} aria-pressed={active}
      className={`h-8 shrink-0 rounded-full px-3 text-[13px] font-medium transition-colors ${active ? "bg-ink text-paper" : "bg-surface text-ink-2 ring-1 ring-line hover:bg-line-2"}`}>
      {children}{count != null && <span className={`num ml-1.5 ${active ? "text-paper/60" : "text-faint"}`}>{count}</span>}
    </button>
  );
}

export function Ext({ href, children }: { href: string | null | undefined; children: ReactNode }) {
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[12px] font-medium text-accent hover:underline">
      {children}
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"><path d="M3 1h6v6M9 1L2 8" /></svg>
    </a>
  );
}
