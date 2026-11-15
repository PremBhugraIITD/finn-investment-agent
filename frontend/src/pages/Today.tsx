import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import FeedItem from "../components/FeedItem";
import type { FeedItemT } from "../types";
import { CATEGORY_LABEL } from "../types";
import { dayKey, dayLabel, fmtDay, fmtTime, timeAgo } from "../lib";
import { Chip, Empty, ErrorBox, Sheet, Skeleton, StatusChip } from "../ui";

const PRESETS = [{ l: "6h", h: 6 }, { l: "24h", h: 24 }, { l: "3d", h: 72 }, { l: "7d", h: 168 }];

export default function Today({ onSettings }: { onSettings: () => void }) {
  const [hours, setHours] = useState(24);
  const [custom, setCustom] = useState(false);
  const [cat, setCat] = useState<string>("all");
  const [showRoutine, setShowRoutine] = useState(false);
  const [symFilter, setSymFilter] = useState<string[]>([]);
  const [filterOpen, setFilterOpen] = useState(false);
  const q = useQuery({ queryKey: ["digest", hours], queryFn: () => api.digest(hours), refetchInterval: 90_000, placeholderData: (p) => p });
  const d = q.data;

  const symbols = useMemo(() => Array.from(new Set((d?.items ?? []).map((i) => i.symbol))).sort(), [d]);
  const items = useMemo(() => (d?.items ?? []).filter((i) =>
    (cat === "all" || i.category === cat) && (symFilter.length === 0 || symFilter.includes(i.symbol))), [d, cat, symFilter]);
  const main = items.filter((i) => i.tier !== "routine");
  const routine = items.filter((i) => i.tier === "routine");
  const cats = useMemo(() => {
    const c: Record<string, number> = {};
    (d?.items ?? []).forEach((i) => { c[i.category] = (c[i.category] ?? 0) + 1; });
    return Object.entries(c).sort((a, b) => b[1] - a[1]);
  }, [d]);

  const groups = useMemo(() => {
    const g: { key: string; label: string; items: FeedItemT[] }[] = [];
    for (const it of main) {
      const k = dayKey(it.ts);
      let grp = g.find((x) => x.key === k);
      if (!grp) { grp = { key: k, label: dayLabel(it.ts), items: [] }; g.push(grp); }
      grp.items.push(it);
    }
    return g;
  }, [main]);

  const attention = d?.counts?.high ?? 0;
  const windowLabel = hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`;

  return (
    <div>
      <header className="sticky top-0 z-20 bg-paper/95 px-4 pb-3 pt-3 backdrop-blur safe-t">
        <div className="flex items-center justify-between">
          <h1 className="text-[28px] font-semibold tracking-tight">Today</h1>
          <div className="flex items-center gap-2">
            {d && <StatusChip stale={d.is_stale} refreshing={d.refreshing} source={JSON.stringify(d.source_status)}
              asOf={d.is_stale && d.snapshot_as_of ? fmtDay(d.snapshot_as_of) : timeAgo(d.as_of)} />}
            <button onClick={onSettings} aria-label="Thresholds" className="grid h-9 w-9 place-items-center rounded-full bg-surface ring-1 ring-line">
              <svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M3 6h9M16 6h1M3 14h1M8 14h9" /><circle cx="14" cy="6" r="2" /><circle cx="6" cy="14" r="2" />
              </svg>
            </button>
          </div>
        </div>
        <div className="no-scrollbar -mx-4 mt-3 flex gap-2 overflow-x-auto px-4">
          {PRESETS.map((p) => <Chip key={p.h} active={!custom && hours === p.h} onClick={() => { setCustom(false); setHours(p.h); }}>{p.l}</Chip>)}
          <Chip active={custom || !PRESETS.some((p) => p.h === hours)} onClick={() => setCustom(true)}>
            {PRESETS.some((p) => p.h === hours) ? "Custom" : windowLabel}
          </Chip>
          <span className="mx-1 w-px shrink-0 bg-line" />
          <Chip active={symFilter.length > 0} onClick={() => setFilterOpen(true)}>
            {symFilter.length ? `${symFilter.length} stocks` : "All stocks"}
          </Chip>
        </div>
        {d && (
          <p className="num mt-3 text-[13px] text-mute">
            <span className="font-semibold text-ink">{attention} need attention</span> · {d.total} filings in the last {windowLabel}
            {d.counts?.routine ? ` · ${d.counts.routine} routine` : ""}
          </p>
        )}
        {cats.length > 1 && (
          <div className="no-scrollbar -mx-4 mt-2.5 flex gap-1.5 overflow-x-auto px-4">
            <Chip active={cat === "all"} onClick={() => setCat("all")}>All</Chip>
            {cats.map(([c, n]) => <Chip key={c} active={cat === c} onClick={() => setCat(c)} count={n}>{CATEGORY_LABEL[c] ?? c}</Chip>)}
          </div>
        )}
      </header>

      {q.isLoading && <Skeleton rows={6} />}
      {q.isError && !d && <ErrorBox message={(q.error as Error).message} retry={() => q.refetch()} />}
      {d && main.length === 0 && routine.length === 0 && (
        <Empty title="Nothing filed in this window" hint="Try a longer window. Weekends and market holidays are quiet." />
      )}

      <div className="mx-3 space-y-4 pb-6">
        {groups.map((g) => (
          <section key={g.key}>
            <h2 className="px-1 pb-1.5 text-[12px] font-semibold uppercase tracking-wider text-faint">{g.label}</h2>
            <div className="divide-y divide-line-2 overflow-hidden rounded-2xl bg-surface ring-1 ring-line">
              {g.items.map((it) => <FeedItem key={it.id} item={it} />)}
            </div>
          </section>
        ))}
        {routine.length > 0 && (
          <section>
            <button onClick={() => setShowRoutine(!showRoutine)} aria-expanded={showRoutine}
              className="flex w-full items-center justify-between rounded-2xl bg-surface px-4 py-3 text-[14px] ring-1 ring-line">
              <span className="font-medium">{routine.length} routine filings <span className="font-normal text-mute">collapsed</span></span>
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
                className={`transition-transform ${showRoutine ? "rotate-180" : ""}`}><path d="M3 5l4 4 4-4" /></svg>
            </button>
            {showRoutine && (
              <div className="fade-in mt-2 divide-y divide-line-2 overflow-hidden rounded-2xl bg-surface ring-1 ring-line">
                {routine.map((it) => <FeedItem key={it.id} item={it} compact />)}
              </div>
            )}
          </section>
        )}
      </div>

      <Sheet open={custom} onClose={() => setCustom(false)} title="Custom window">
        <p className="text-[13px] text-mute">Show every filing across the portfolio for the last…</p>
        <p className="num my-2 text-[34px] font-semibold tracking-tight">{hours < 48 ? `${hours} hours` : `${Math.round(hours / 24)} days`}</p>
        <input type="range" min={1} max={30} value={hours < 48 ? Math.min(Math.max(Math.round(hours / 24), 1), 30) : Math.round(hours / 24)}
          onChange={(e) => setHours(Number(e.target.value) * 24)} className="h-8 w-full accent-[var(--color-accent)]" aria-label="Days" />
        <div className="mt-1 flex justify-between text-[11px] text-faint"><span>1 day</span><span>30 days</span></div>
        <div className="mt-3 flex gap-2">
          {[3, 12, 36].map((h) => <Chip key={h} onClick={() => setHours(h)} active={hours === h}>{h}h</Chip>)}
        </div>
        <button onClick={() => setCustom(false)} className="mt-5 h-12 w-full rounded-2xl bg-ink text-[15px] font-semibold text-paper">Apply</button>
      </Sheet>

      <Sheet open={filterOpen} onClose={() => setFilterOpen(false)} title="Filter stocks">
        <div className="mb-3 flex gap-2">
          <Chip onClick={() => setSymFilter([])} active={symFilter.length === 0}>All</Chip>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {symbols.map((s) => {
            const on = symFilter.includes(s);
            return (
              <button key={s} aria-pressed={on} onClick={() => setSymFilter(on ? symFilter.filter((x) => x !== s) : [...symFilter, s])}
                className={`flex h-11 items-center justify-between rounded-xl px-3 text-[14px] font-medium ring-1 ${on ? "bg-ink text-paper ring-ink" : "bg-surface ring-line"}`}>
                {s}
              </button>
            );
          })}
        </div>
        <button onClick={() => setFilterOpen(false)} className="mt-5 h-12 w-full rounded-2xl bg-ink text-[15px] font-semibold text-paper">Done</button>
      </Sheet>
    </div>
  );
}
