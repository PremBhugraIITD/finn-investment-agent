import type { AlertT, Company, DigestT, InventoryT, JobT, ResearchResult, SettingsT } from "./types";

const BASE = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  code: string;
  constructor(code: string, message: string) { super(message); this.code = code; }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let code = "ERROR", msg = `Request failed (${r.status})`;
    try { const j = await r.json(); code = j.error?.code ?? code; msg = j.error?.message ?? j.detail ?? msg; } catch { /* ignore */ }
    throw new ApiError(code, msg);
  }
  return r.json();
}

export const api = {
  portfolio: () => req<{ companies: Company[]; as_of: string; count: number }>("/api/v1/portfolio"),
  digest: (hours: number) => req<DigestT>(`/api/v1/digest?hours=${hours}`),
  alerts: () => req<{ alerts: AlertT[]; unread: number; settings: SettingsT; is_stale: boolean; as_of: string }>("/api/v1/alerts?days=7"),
  readAlerts: (ids: string[], all = false) => req("/api/v1/alerts/read", { method: "POST", body: JSON.stringify({ ids, all }) }),
  settings: () => req<SettingsT>("/api/v1/settings"),
  putSettings: (s: SettingsT) => req<SettingsT>("/api/v1/settings", { method: "PUT", body: JSON.stringify(s) }),
  universe: () => req<{ suggested: { symbol: string; name: string; sector: string }[]; portfolio: { symbol: string; name: string }[] }>("/api/v1/research/universe"),
  inventory: (sym: string) => req<InventoryT>(`/api/v1/research/${encodeURIComponent(sym)}/documents`),
  analyze: (sym: string, modules: string[], question?: string, refresh = false) =>
    req<{ job_id: string }>(`/api/v1/research/${encodeURIComponent(sym)}/analyze`, { method: "POST", body: JSON.stringify({ modules, question, refresh }) }),
  job: (id: string) => req<JobT>(`/api/v1/research/jobs/${id}`),
  result: (sym: string) => req<ResearchResult>(`/api/v1/research/${encodeURIComponent(sym)}/result`),
  ask: (sym: string, question: string) =>
    req<{ answer: string; claims: import("./types").Claim[] }>(`/api/v1/research/${encodeURIComponent(sym)}/ask`, { method: "POST", body: JSON.stringify({ question }) }),
};
