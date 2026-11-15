import { useState } from "react";
import type { BioCard, FeedItemT, ResultsCard } from "../types";
import { fmtTime, shortNum } from "../lib";
import { CatPill, Delta, Ext } from "../ui";

export function ResultsBlock({ r }: { r: ResultsCard }) {
  const rows: [string, keyof ResultsCard["metrics"]][] = [["Revenue", "revenue"], ["EBITDA", "ebitda"], ["PBT", "pbt"], ["PAT", "pat"]];
  return (
    <div className="mt-3 overflow-hidden rounded-xl bg-paper ring-1 ring-line">
      <div className="flex items-center justify-between px-3 py-2 text-[11px] text-mute">
        <span className="font-medium text-ink-2">{r.quarter}</span>
        <span>{r.unit} · {r.basis}</span>
      </div>
      <div className="grid grid-cols-[1fr_auto_3.5rem_3.5rem] items-baseline gap-x-3 px-3 pb-1 text-[10px] uppercase tracking-wide text-faint">
        <span /><span className="text-right">This Q</span><span className="text-right">QoQ</span><span className="text-right">YoY</span>
      </div>
      {rows.map(([label, k]) => {
        const m = r.metrics[k];
        return (
          <div key={k} className="grid grid-cols-[1fr_auto_3.5rem_3.5rem] items-baseline gap-x-3 border-t border-line px-3 py-1.5 text-[13px]">
            <span className="text-ink-2">{label}{m.derived && <span className="ml-1 text-[10px] text-faint">derived</span>}</span>
            <span className="num text-right font-medium">{shortNum(m.current)}</span>
            <Delta v={m.qoq_pct} className="text-right text-[12px]" />
            <Delta v={m.yoy_pct} className="text-right text-[12px]" />
          </div>
        );
      })}
    </div>
  );
}

export function BioBlock({ b }: { b: BioCard }) {
  return (
    <div className="mt-3 rounded-xl bg-paper p-3 ring-1 ring-line">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[13px] font-medium">{b.name}</p>
        {b.confidence && <span className="text-[10px] uppercase tracking-wide text-faint">{b.confidence} confidence</span>}
      </div>
      <p className="text-[12px] text-mute">{b.role}</p>
      {b.bio ? <p className="mt-1.5 text-[13px] leading-snug text-ink-2">{b.bio}</p>
        : <p className="mt-1.5 text-[13px] text-mute">No background in the filing.</p>}
      {b.previous_roles.length > 0 && (
        <ul className="mt-1.5 space-y-0.5 text-[12px] text-mute">
          {b.previous_roles.slice(0, 3).map((p) => <li key={p}>· {p}</li>)}
        </ul>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        {b.sources.filter((s) => s.url).map((s) => <Ext key={s.url!} href={s.url}>{s.label}</Ext>)}
        {b.note && <span className="text-[11px] text-faint">{b.note}</span>}
      </div>
    </div>
  );
}

export default function FeedItem({ item, compact = false, hideCompany = false }: { item: FeedItemT; compact?: boolean; hideCompany?: boolean }) {
  const [why, setWhy] = useState(false);
  if (compact) {
    return (
      <div className="flex items-baseline gap-2 px-4 py-2.5 text-[13px]">
        <span className="w-[4.5rem] shrink-0 truncate font-semibold tracking-tight">{item.symbol}</span>
        <span className="min-w-0 flex-1 truncate text-mute">{item.summary}</span>
        <Ext href={item.attachment}>PDF</Ext>
      </div>
    );
  }
  const hot = item.tier === "high";
  return (
    <article className="relative px-4 py-3.5">
      {hot && <span className="absolute inset-y-3.5 left-0 w-[3px] rounded-r-full bg-accent" />}
      <div className="flex items-center gap-2 text-[12px]">
        {!hideCompany && <span className="text-[13px] font-semibold tracking-tight">{item.symbol}</span>}
        <CatPill cat={item.category} />
        <span className="num text-faint">{fmtTime(item.ts)}</span>
        <button onClick={() => setWhy(!why)} aria-expanded={why} aria-label={`Materiality ${item.materiality}, tap for why`}
          className="num ml-auto flex h-6 min-w-[2rem] items-center justify-center rounded-md bg-line-2 px-1.5 text-[11px] font-semibold text-ink-2">
          {item.materiality}
        </button>
      </div>
      <p className="mt-1.5 text-[15px] leading-snug text-ink">{item.summary}</p>
      {item.results && <ResultsBlock r={item.results} />}
      {item.bios.map((b) => <BioBlock key={b.name} b={b} />)}
      {why && (
        <ul className="fade-in mt-2.5 space-y-1 rounded-xl bg-paper px-3 py-2 text-[12px] ring-1 ring-line">
          {item.factors.map((f, i) => (
            <li key={i} className="flex justify-between"><span className="text-ink-2">{f.label}</span>
              <span className={`num ${f.points < 0 ? "text-loss" : "text-mute"}`}>{f.points > 0 ? "+" : ""}{f.points}</span></li>
          ))}
          <li className="flex justify-between border-t border-line pt-1 font-medium"><span>Score</span><span className="num">{item.materiality}</span></li>
        </ul>
      )}
      <div className="mt-2 flex items-center gap-3">
        <Ext href={item.attachment}>Filing</Ext>
        {!hideCompany && <span className="truncate text-[12px] text-faint">{item.company}</span>}
      </div>
    </article>
  );
}
