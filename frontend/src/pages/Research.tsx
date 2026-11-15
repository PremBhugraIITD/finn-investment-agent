import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import type { Cite, Claim, JobT, ResearchResult, ScoreRow, Section } from "../types";
import { Chip, Empty, ErrorBox, Ext } from "../ui";
import { pct } from "../lib";

const MODULES: { id: string; label: string; hint: string }[] = [
  { id: "business", label: "Business", hint: "What it does, segments" },
  { id: "financial", label: "Financials", hint: "Headline numbers" },
  { id: "trajectory", label: "Trajectory", hint: "Multi-year direction" },
  { id: "balance_sheet", label: "Balance sheet", hint: "Debt, leverage, WC" },
  { id: "cash_quality", label: "Cash quality", hint: "Profit to cash" },
  { id: "narrative", label: "Narrative vs numbers", hint: "Promised vs delivered" },
  { id: "guidance", label: "Guidance", hint: "What management guided" },
  { id: "bull_bear", label: "Bull vs bear", hint: "A case each way" },
];

// ------------------------------------------------------------------ small pieces
function CiteChip({ c }: { c: Cite }) {
  const [open, setOpen] = useState(false);
  const href = c.url ? (c.url.includes("#") ? c.url : `${c.url}#page=${c.page}`) : undefined;
  return (
    <span className="inline-block align-baseline">
      <button onClick={() => setOpen(!open)} aria-expanded={open}
        className="num mr-1 mt-1 inline-flex items-center gap-1 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent">
        {c.label} · p.{c.page}
      </button>
      {open && (
        <span className="fade-in my-1 block rounded-lg bg-paper p-2.5 text-[12px] leading-snug text-ink-2 ring-1 ring-line">
          {c.quote ? <em>“{c.quote}”</em> : <span className="text-mute">Figure read from the table on this page.</span>}
          <span className="mt-1.5 block"><Ext href={href}>Open source PDF at page {c.page}</Ext></span>
        </span>
      )}
    </span>
  );
}

function uniqCites(cites: Cite[]): Cite[] {
  const seen = new Set<string>();
  return cites.filter((c) => { const k = `${c.label}|${c.page}`; if (seen.has(k)) return false; seen.add(k); return true; });
}

function Claims({ claims }: { claims: Claim[] }) {
  return (
    <ul className="space-y-3">
      {claims.map((c, i) => (
        <li key={i} className="text-[14px] leading-snug text-ink">
          <span>{c.text}</span>
          <div className="mt-0.5">{uniqCites(c.cites).map((x, j) => <CiteChip key={j} c={x} />)}</div>
        </li>
      ))}
    </ul>
  );
}

function MetricGrid({ section }: { section: Section }) {
  if (!section.metrics.length) return null;
  return (
    <div className="mb-3 grid grid-cols-2 gap-2">
      {section.metrics.map((m, i) => (
        <div key={i} className="rounded-xl bg-paper p-3 ring-1 ring-line">
          <p className="text-[11px] leading-tight text-mute">{m.label}</p>
          <p className="num mt-0.5 text-[17px] font-semibold tracking-tight">{m.value}</p>
          {m.note && <p className="num text-[11px] text-faint">{m.note}</p>}
          {m.cite && <div className="mt-0.5"><CiteChip c={m.cite} /></div>}
        </div>
      ))}
    </div>
  );
}

const STATUS_STYLE: Record<string, string> = {
  met: "bg-gain-soft text-gain", missed: "bg-loss-soft text-loss",
  "partially met": "bg-warn-soft text-warn", "not yet measurable": "bg-line-2 text-mute",
};

function Scorecard({ sc }: { sc: NonNullable<ResearchResult["scorecard"]> }) {
  const byQ = useMemo(() => {
    const m = new Map<string, ScoreRow[]>();
    sc.rows.forEach((r) => m.set(r.quarter, [...(m.get(r.quarter) ?? []), r]));
    return Array.from(m.entries());
  }, [sc]);
  return (
    <div className="mt-4 space-y-4">
      {sc.delivered && sc.delivered.length > 0 && (
        <div>
          <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">Delivered, from filed results</p>
          <div className="no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
            {sc.delivered.map((d) => (
              <div key={d.quarter} className="min-w-[8.2rem] shrink-0 rounded-xl bg-paper p-3 ring-1 ring-line">
                <p className="text-[12px] font-semibold">{d.quarter}</p>
                <dl className="num mt-1.5 space-y-0.5 text-[12px]">
                  <div className="flex justify-between"><dt className="text-mute">Rev YoY</dt><dd className={d.revenue_yoy != null && d.revenue_yoy < 0 ? "text-loss" : "text-gain"}>{pct(d.revenue_yoy)}</dd></div>
                  <div className="flex justify-between"><dt className="text-mute">EBITDA %</dt><dd>{d.ebitda_margin != null ? `${d.ebitda_margin.toFixed(1)}%` : "–"}</dd></div>
                  <div className="flex justify-between"><dt className="text-mute">PAT YoY</dt><dd className={d.pat_yoy != null && d.pat_yoy < 0 ? "text-loss" : "text-gain"}>{pct(d.pat_yoy)}</dd></div>
                </dl>
                <div className="mt-1"><CiteChip c={d.cite} /></div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div>
        <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">Promised on each call</p>
        <div className="space-y-3">
          {byQ.map(([q, rows]) => (
            <div key={q} className="overflow-hidden rounded-xl ring-1 ring-line">
              <p className="bg-line-2 px-3 py-1.5 text-[12px] font-semibold">{q} call</p>
              <div className="divide-y divide-line-2 bg-surface">
                {rows.map((r, i) => (
                  <div key={i} className="px-3 py-2.5 text-[13px]">
                    <div className="flex items-start justify-between gap-2">
                      <p className="leading-snug text-ink">{r.metric}</p>
                      <span className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold leading-none ${STATUS_STYLE[r.status]}`}>{r.status}</span>
                    </div>
                    {(r.promised !== "qualitative" || r.delivered !== "–") && (
                      <p className="num mt-1 text-[12px] text-mute">
                        Promised <b className="text-ink-2">{r.promised}</b>{r.target ? ` for ${r.target}` : ""}
                        {r.delivered !== "–" && <> · Delivered <b className="text-ink-2">{r.delivered}</b> · {r.delta}</>}
                      </p>
                    )}
                    {r.note && r.status === "not yet measurable" && <p className="mt-0.5 text-[11px] text-faint">{r.note}</p>}
                    <div className="mt-0.5">
                      {r.guidance_cite && <CiteChip c={r.guidance_cite} />}
                      {r.actual_cite && <CiteChip c={r.actual_cite} />}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
      {sc.takeaways.length > 0 && (
        <div>
          <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">Takeaways</p>
          <Claims claims={sc.takeaways} />
        </div>
      )}
      {sc.gaps.map((g, i) => <p key={i} className="rounded-lg bg-warn-soft px-3 py-2 text-[12px] text-warn">{g}</p>)}
    </div>
  );
}

function SectionView({ s, sc }: { s: Section; sc: ResearchResult["scorecard"] }) {
  const bull = s.claims.filter((c) => c.stance === "bull");
  const bear = s.claims.filter((c) => c.stance === "bear");
  return (
    <section id={`sec-${s.id}`} className="scroll-mt-28 rounded-2xl bg-surface p-4 ring-1 ring-line">
      <h2 className="mb-3 text-[17px] font-semibold tracking-tight">{s.title}</h2>
      <MetricGrid section={s} />
      {s.id === "bull_bear" && (bull.length > 0 || bear.length > 0) ? (
        <div className="grid gap-4 md:grid-cols-2">
          <div><p className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-gain">Bull</p><Claims claims={bull} /></div>
          <div><p className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-loss">Bear</p><Claims claims={bear} /></div>
        </div>
      ) : s.claims.length > 0 ? <Claims claims={s.claims} /> : null}
      {s.id === "narrative" && sc && <Scorecard sc={sc} />}
      {s.gap && <p className="mt-3 text-[12px] text-faint">Evidence gap: {s.gap}</p>}
      {!s.claims.length && !s.metrics.length && !s.gap && <p className="text-[13px] text-mute">Not found in available primary documents.</p>}
    </section>
  );
}

function AskBox({ symbol }: { symbol: string }) {
  const [q, setQ] = useState("");
  const m = useMutation({ mutationFn: (question: string) => api.ask(symbol, question) });
  const suggestions = ["What is management's capex plan?", "What are analysts pressing on in the calls?", "What risks does the annual report flag?"];
  return (
    <section className="rounded-2xl bg-surface p-4 ring-1 ring-line">
      <h2 className="text-[17px] font-semibold tracking-tight">Ask this company</h2>
      <p className="mb-3 text-[12px] text-mute">Answers come only from the annual report, transcripts and filed results above.</p>
      <form onSubmit={(e) => { e.preventDefault(); if (q.trim()) m.mutate(q.trim()); }} className="flex gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about guidance, risks, segments…" aria-label="Question"
          className="h-11 min-w-0 flex-1 rounded-xl bg-paper px-3 text-[14px] outline-none ring-1 ring-line focus:ring-accent" />
        <button disabled={m.isPending || !q.trim()} className="h-11 rounded-xl bg-ink px-4 text-[14px] font-semibold text-paper disabled:opacity-40">Ask</button>
      </form>
      {!m.data && !m.isPending && (
        <div className="mt-3 flex flex-wrap gap-2">{suggestions.map((s) => <Chip key={s} onClick={() => { setQ(s); m.mutate(s); }}>{s}</Chip>)}</div>
      )}
      {m.isPending && <div className="mt-3 space-y-2"><div className="skel h-4 w-full" /><div className="skel h-4 w-3/4" /></div>}
      {m.isError && <p className="mt-3 text-[13px] text-loss">{(m.error as Error).message}</p>}
      {m.data && <div className="mt-3"><Claims claims={m.data.claims} /></div>}
    </section>
  );
}

// ------------------------------------------------------------------ progress
function Progress({ job }: { job: JobT | undefined }) {
  const p = Math.round((job?.progress ?? 0.02) * 100);
  return (
    <div className="rounded-2xl bg-surface p-4 ring-1 ring-line" role="status" aria-live="polite">
      <div className="flex items-baseline justify-between text-[13px]">
        <span className="font-medium">{job?.stage ?? "Starting"}</span><span className="num text-mute">{p}%</span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line-2"><div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${p}%` }} /></div>
      <p className="mt-2 text-[12px] text-faint">Reading primary documents, extracting figures, then reasoning. First run on a new company takes about a minute.</p>
    </div>
  );
}

// ------------------------------------------------------------------ company view
function CompanyView({ symbol }: { symbol: string }) {
  const nav = useNavigate();
  const inv = useQuery({ queryKey: ["inventory", symbol], queryFn: () => api.inventory(symbol), retry: false });
  const [mods, setMods] = useState<string[]>(MODULES.map((m) => m.id));
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<ResearchResult | null>(null);
  const started = useRef(false);
  const start = useMutation({
    mutationFn: (refresh: boolean) => api.analyze(symbol, mods, undefined, refresh),
    onSuccess: (r) => { setResult(null); setJobId(r.job_id); },
  });
  const job = useQuery({
    queryKey: ["job", jobId], queryFn: () => api.job(jobId!), enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && ["complete", "failed"].includes(q.state.data.status) ? false : 900),
  });
  useEffect(() => { if (job.data?.status === "complete" && job.data.result) setResult(job.data.result); }, [job.data]);
  // seeded / cached analyses open instantly
  useEffect(() => {
    if (inv.data?.cached_analysis && !started.current) { started.current = true; start.mutate(false); }
  }, [inv.data]); // eslint-disable-line

  const running = !!jobId && !result && job.data?.status !== "failed";
  const failed = job.data?.status === "failed";

  if (inv.isError) {
    return (
      <div className="p-4">
        <button onClick={() => nav("/research")} className="mb-3 text-[13px] font-medium text-accent">← Research</button>
        <ErrorBox message={(inv.error as Error).message} retry={() => inv.refetch()} />
      </div>
    );
  }
  const toggle = (id: string) => setMods((m) => (m.includes(id) ? m.filter((x) => x !== id) : [...m, id]));
  return (
    <div className="pb-6">
      <header className="sticky top-0 z-20 bg-paper/95 px-4 pb-2 pt-3 backdrop-blur safe-t">
        <button onClick={() => nav("/research")} className="text-[13px] font-medium text-accent">← Research</button>
        <h1 className="mt-1 text-[26px] font-semibold leading-tight tracking-tight">{symbol}</h1>
        <p className="text-[13px] text-mute">{inv.data?.name ?? (inv.isLoading ? "Checking ticker…" : "")}</p>
        {result && (
          <div className="no-scrollbar -mx-4 mt-2 flex gap-1.5 overflow-x-auto px-4">
            {result.sections.map((s) => (
              <button key={s.id} onClick={() => document.getElementById(`sec-${s.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
                className="h-8 shrink-0 rounded-full bg-surface px-3 text-[12.5px] font-medium text-ink-2 ring-1 ring-line">{s.title}</button>
            ))}
          </div>
        )}
      </header>

      <div className="mx-3 space-y-3">
        {inv.isLoading && <div className="skel h-24" />}
        {inv.data && !result && (
          <section className="rounded-2xl bg-surface p-4 ring-1 ring-line">
            <p className="text-[12px] font-semibold uppercase tracking-wider text-faint">Primary sources found</p>
            <p className="num mt-1 text-[14px]">
              {inv.data.annual_reports.length} annual reports · {inv.data.concalls.length} concall transcripts · {inv.data.results_filings.length || "up to 5"} results filings
            </p>
            <ul className="mt-2 space-y-1 text-[12px] text-mute">
              {inv.data.annual_reports.slice(0, 1).map((a) => <li key={a.url}>Annual report {a.year} <Ext href={a.url}>PDF</Ext></li>)}
              {inv.data.concalls.slice(0, 4).map((c, i) => <li key={i}>Concall {c.period} {c.transcript && <Ext href={c.transcript}>Transcript</Ext>}</li>)}
            </ul>
            <p className="mt-2 text-[11px] text-faint">No news, broker notes or outside knowledge is used.</p>
          </section>
        )}

        {!result && !running && inv.data && (
          <section className="rounded-2xl bg-surface p-4 ring-1 ring-line">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-[14px] font-medium">What do you want to know?</p>
              <button className="text-[12px] font-medium text-accent" onClick={() => setMods(mods.length === MODULES.length ? [] : MODULES.map((m) => m.id))}>
                {mods.length === MODULES.length ? "Clear" : "Select all"}
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {MODULES.map((m) => {
                const on = mods.includes(m.id);
                return (
                  <button key={m.id} onClick={() => toggle(m.id)} aria-pressed={on}
                    className={`rounded-xl p-2.5 text-left ring-1 transition-colors ${on ? "bg-ink text-paper ring-ink" : "bg-paper ring-line"}`}>
                    <span className="block text-[13px] font-medium leading-tight">{m.label}</span>
                    <span className={`block text-[11px] ${on ? "text-paper/60" : "text-faint"}`}>{m.hint}</span>
                  </button>
                );
              })}
            </div>
            <button disabled={!mods.length || start.isPending} onClick={() => start.mutate(false)}
              className="mt-3 h-12 w-full rounded-2xl bg-accent text-[15px] font-semibold text-white disabled:opacity-40">
              {mods.length === MODULES.length ? "Run full research" : `Run ${mods.length} module${mods.length === 1 ? "" : "s"}`}
            </button>
          </section>
        )}

        {start.isError && <ErrorBox message={(start.error as Error).message} />}
        {running && <Progress job={job.data} />}
        {failed && <ErrorBox message={job.data?.error ?? "Research failed."} retry={() => start.mutate(false)} />}

        {result && (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11px] text-faint">
              <span className="rounded-full bg-accent-soft px-2 py-1 font-medium text-accent">Primary documents only</span>
              <span>{result.documents.length} documents · {result.source === "seed" ? "prepared snapshot" : result.source === "cache" ? "cached run" : "fresh run"}</span>
              <span>{result.models.tier1} extract · {result.models.tier2} reason</span>
            </div>
            {result.sections.map((s) => <SectionView key={s.id} s={s} sc={result.scorecard} />)}
            <AskBox symbol={symbol} />
            <section className="rounded-2xl bg-surface p-4 ring-1 ring-line">
              <h2 className="text-[15px] font-semibold">Sources</h2>
              <ul className="mt-2 space-y-1.5 text-[12px]">
                {result.documents.map((d) => (
                  <li key={d.doc_id} className="flex items-baseline justify-between gap-2">
                    <span className="min-w-0 truncate text-ink-2">{d.kind.replace("_", " ")} · {d.period}</span>
                    <span className="num shrink-0 text-faint">{d.total_pages && d.total_pages > d.n_pages ? `first ${d.n_pages} of ${d.total_pages}` : d.n_pages} pp</span><Ext href={d.url}>Open</Ext>
                  </li>
                ))}
              </ul>
              {result.gaps.map((g, i) => <p key={i} className="mt-2 text-[12px] text-warn">{g}</p>)}
              <button onClick={() => start.mutate(true)} className="mt-3 text-[12px] font-medium text-accent">Re-run from scratch</button>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ landing
export default function Research() {
  const { symbol } = useParams();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const uni = useQuery({ queryKey: ["universe"], queryFn: api.universe });
  if (symbol) return <CompanyView key={symbol.toUpperCase()} symbol={symbol.toUpperCase()} />;
  const go = (s: string) => { const t = s.trim().toUpperCase().replace(/\.(NS|BO)$/, ""); if (t) nav(`/research/${encodeURIComponent(t)}`); };
  return (
    <div>
      <header className="sticky top-0 z-20 bg-paper/95 px-4 pb-3 pt-3 backdrop-blur safe-t">
        <h1 className="text-[28px] font-semibold tracking-tight">Research</h1>
        <p className="text-[13px] text-mute">Grounded in annual reports, concall transcripts and filed results. Works for any NSE ticker.</p>
        <form onSubmit={(e) => { e.preventDefault(); go(q); }} className="mt-3 flex gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="NSE ticker, e.g. ASIANPAINT" aria-label="NSE ticker"
            autoCapitalize="characters" autoCorrect="off" spellCheck={false}
            className="h-12 min-w-0 flex-1 rounded-2xl bg-surface px-4 text-[15px] uppercase outline-none ring-1 ring-line placeholder:normal-case focus:ring-accent" />
          <button className="h-12 rounded-2xl bg-ink px-5 text-[15px] font-semibold text-paper">Go</button>
        </form>
      </header>
      <div className="mx-3 space-y-5 pb-6">
        <section>
          <h2 className="px-1 pb-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">Try these</h2>
          <div className="divide-y divide-line-2 overflow-hidden rounded-2xl bg-surface ring-1 ring-line">
            {(uni.data?.suggested ?? []).map((c) => (
              <button key={c.symbol} onClick={() => go(c.symbol)} className="flex w-full items-center justify-between px-4 py-3.5 text-left active:bg-line-2">
                <div><p className="text-[15px] font-semibold tracking-tight">{c.symbol}</p><p className="text-[12px] text-mute">{c.name} · {c.sector}</p></div>
                <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" className="text-faint"><path d="M5 2l5 5-5 5" /></svg>
              </button>
            ))}
            {uni.isLoading && <div className="p-4"><div className="skel h-10" /></div>}
          </div>
        </section>
        <section>
          <h2 className="px-1 pb-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">From your portfolio</h2>
          <div className="flex flex-wrap gap-2">{(uni.data?.portfolio ?? []).map((c) => <Chip key={c.symbol} onClick={() => go(c.symbol)}>{c.symbol}</Chip>)}</div>
        </section>
        {uni.isError && <Empty title="Couldn't reach the server" hint="Check your connection and reload." />}
      </div>
    </div>
  );
}
