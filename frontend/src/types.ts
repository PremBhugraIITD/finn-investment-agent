export type Category =
  | "results" | "M&A" | "fund_raise" | "management_change" | "litigation" | "insider_trading"
  | "earnings_call" | "investor_meetings" | "credit_rating" | "dividend" | "bonus_split" | "capex"
  | "press_release" | "postal_ballot" | "agm_egm" | "pledging" | "unclassified";

export const CATEGORY_LABEL: Record<string, string> = {
  results: "Results", "M&A": "M&A", fund_raise: "Fund raise", management_change: "Mgmt change",
  litigation: "Litigation", insider_trading: "Insider", earnings_call: "Earnings call",
  investor_meetings: "Investor mtg", credit_rating: "Rating", dividend: "Dividend",
  bonus_split: "Bonus/Split", capex: "Capex", press_release: "News", postal_ballot: "Postal ballot",
  agm_egm: "AGM/EGM", pledging: "Pledging", unclassified: "Routine",
};

export interface Metric {
  current: number | null; prev_quarter: number | null; year_ago: number | null;
  qoq_pct: number | null; yoy_pct: number | null; derived: boolean; page: number | null; quote: string | null;
}
export interface ResultsCard {
  ok: boolean; quarter: string; unit: string; basis: string; source_url: string;
  metrics: Record<"revenue" | "ebitda" | "pbt" | "pat", Metric>;
}
export interface BioCard {
  name: string; role: string; bio: string | null; previous_roles: string[]; confidence: string | null;
  sources: { label: string; url: string | null }[]; external_profile: boolean; note?: string;
}
export interface Person { name: string; role: string; action: string; effective_date: string | null; page: number | null }
export interface FeedItemT {
  id: string; symbol: string; company: string; ts: string; category: Category; confidence: number | null;
  summary: string; subject: string; attachment: string | null; direction: string | null;
  materiality: number; factors: { label: string; points: number }[]; tier: "high" | "normal" | "routine";
  results: ResultsCard | null; people: Person[]; bios: BioCard[];
}
export interface DigestT {
  items: FeedItemT[]; counts: Record<string, number>; total: number; window_hours: number; as_of: string;
  source_status: Record<string, number>; is_stale: boolean; snapshot_as_of: string | null; refreshing: boolean;
  llm_available: boolean;
}
export interface Company {
  symbol: string; name: string; label: string; sector: string; cap_band: string; market_cap_cr: number | null;
  last_close: number | null; change_pct: number | null; volume_ratio: number | null; volume: number | null;
  last_date: string | null; spark: number[]; source: string; is_stale: boolean;
  latest_event: { category: Category; summary: string; ts: string; materiality: number } | null;
}
export interface SettingsT { price_move_pct: number; volume_ratio: number; materiality: number; categories: string[] }
export interface AlertT {
  id: string; kind: "price" | "volume" | "announcement"; symbol: string; company: string; ts: string; title: string;
  reason: string; read: boolean; value?: number; threshold?: number; history?: number[]; category?: Category;
  materiality?: number; item?: FeedItemT; is_stale?: boolean;
}

// ---- research ----
export interface Cite { doc_id: string; page: number; quote: string; url: string; label: string }
export interface Claim { text: string; stance?: "bull" | "bear" | null; cites: Cite[] }
export interface RMetric { label: string; value: string; note?: string; cite?: Cite | null }
export interface Section { id: string; title: string; claims: Claim[]; metrics: RMetric[]; gap?: string | null }
export interface ScoreRow {
  quarter: string; metric: string; promised: string; target?: string; note?: string; delivered: string; delta: string;
  status: "met" | "missed" | "partially met" | "not yet measurable"; guidance_cite: Cite | null; actual_cite: Cite | null;
}
export interface ResearchDoc { doc_id: string; kind: string; title: string; period: string; url: string; n_pages: number; total_pages?: number; status: string }
export interface ResearchResult {
  symbol: string; name: string; as_of: string; source: string; models: { tier1: string; tier2: string };
  documents: ResearchDoc[]; sections: Section[]; modules: string[];
  scorecard: null | {
    quarters: string[]; rows: ScoreRow[]; takeaways: Claim[]; gaps: string[];
    delivered?: { quarter: string; revenue_yoy: number | null; ebitda_margin: number | null; pat_yoy: number | null; cite: Cite }[];
  };
  gaps: string[];
}
export interface JobT {
  id: string; symbol: string; status: "queued" | "running" | "complete" | "failed"; stage: string; progress: number;
  error?: string | null; result?: ResearchResult | null;
}
export interface InventoryT {
  symbol: string; name: string | null; resolved_url: string | null; seeded: boolean; cached_analysis: boolean;
  annual_reports: { title: string; year: number; url: string }[];
  concalls: { period: string; transcript?: string; ppt?: string }[];
  results_filings: { title: string; ts: string; url: string }[];
  source: string;
}
