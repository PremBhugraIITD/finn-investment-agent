const IST = "Asia/Kolkata";

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 0) return "just now";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return fmtDay(iso);
}

export function fmtDay(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: IST });
}
export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: IST });
}
export function dayKey(iso: string): string {
  return new Date(iso).toLocaleDateString("en-CA", { timeZone: IST });
}
export function dayLabel(iso: string): string {
  const k = dayKey(iso);
  const today = new Date().toLocaleDateString("en-CA", { timeZone: IST });
  const y = new Date(Date.now() - 86400000).toLocaleDateString("en-CA", { timeZone: IST });
  if (k === today) return "Today";
  if (k === y) return "Yesterday";
  return new Date(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: IST });
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v == null) return "–";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}
export function num(v: number | null | undefined): string {
  if (v == null) return "–";
  return v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}
export function inr(v: number | null | undefined): string {
  if (v == null) return "–";
  return "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}
export function mcap(cr: number | null | undefined): string {
  if (!cr) return "–";
  if (cr >= 100000) return `₹${(cr / 100000).toFixed(2)}L cr`;
  return `₹${Math.round(cr).toLocaleString("en-IN")} cr`;
}
export function tone(v: number | null | undefined): "gain" | "loss" | "mute" {
  if (v == null || v === 0) return "mute";
  return v > 0 ? "gain" : "loss";
}
export const toneText = { gain: "text-gain", loss: "text-loss", mute: "text-mute" } as const;

export function shortNum(v: number | null | undefined): string {
  if (v == null) return "–";
  const a = Math.abs(v);
  if (a >= 1e5) return v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  return v.toLocaleString("en-IN", { maximumFractionDigits: 1 });
}
