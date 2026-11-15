import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Company } from "../types";
import { CATEGORY_LABEL } from "../types";
import { fmtDay, inr, mcap, timeAgo } from "../lib";
import { Chip, Delta, Empty, ErrorBox, Sheet, Skeleton, Spark, StatusChip } from "../ui";
import { useSettings } from "../components/SettingsSheet";

type Sort = "move" | "volume" | "sector" | "cap";

export default function Portfolio() {
  const q = useQuery({ queryKey: ["portfolio"], queryFn: api.portfolio, refetchInterval: 120_000 });
  const { settings } = useSettings();
  const [sort, setSort] = useState<Sort>("move");
  const [open, setOpen] = useState<Company | null>(null);
  const nav = useNavigate();
  const list = useMemo(() => {
    const l = [...(q.data?.companies ?? [])];
    const cap = { Large: 0, Mid: 1, Small: 2 } as Record<string, number>;
    if (sort === "move") l.sort((a, b) => Math.abs(b.change_pct ?? 0) - Math.abs(a.change_pct ?? 0));
    if (sort === "volume") l.sort((a, b) => (b.volume_ratio ?? 0) - (a.volume_ratio ?? 0));
    if (sort === "sector") l.sort((a, b) => a.sector.localeCompare(b.sector));
    if (sort === "cap") l.sort((a, b) => (cap[a.cap_band] ?? 3) - (cap[b.cap_band] ?? 3) || (b.market_cap_cr ?? 0) - (a.market_cap_cr ?? 0));
    return l;
  }, [q.data, sort]);
  const stale = q.data?.companies.some((c) => c.is_stale);
  const lastDate = q.data?.companies.find((c) => c.last_date)?.last_date;
  const avg = useMemo(() => {
    const v = (q.data?.companies ?? []).map((c) => c.change_pct).filter((x): x is number => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  }, [q.data]);

  return (
    <div>
      <header className="sticky top-0 z-20 bg-paper/95 px-4 pb-3 pt-3 backdrop-blur safe-t">
        <div className="flex items-center justify-between">
          <h1 className="text-[28px] font-semibold tracking-tight">Portfolio</h1>
          {q.data && <StatusChip stale={stale} asOf={lastDate ? `close ${fmtDay(lastDate)}` : timeAgo(q.data.as_of)} />}
        </div>
        {q.data && <p className="num mt-1 text-[13px] text-mute">{q.data.count} stocks · equal-weight day move <Delta v={avg} className="font-semibold" /></p>}
        <div className="no-scrollbar -mx-4 mt-3 flex gap-2 overflow-x-auto px-4">
          {([["move", "Biggest move"], ["volume", "Volume spike"], ["sector", "Sector"], ["cap", "Market cap"]] as [Sort, string][]).map(([k, l]) =>
            <Chip key={k} active={sort === k} onClick={() => setSort(k)}>{l}</Chip>)}
        </div>
      </header>
      {q.isLoading && <Skeleton rows={8} />}
      {q.isError && <ErrorBox message={(q.error as Error).message} retry={() => q.refetch()} />}
      {q.data && list.length === 0 && <Empty title="No holdings" />}
      <div className="mx-3 mb-6 divide-y divide-line-2 overflow-hidden rounded-2xl bg-surface ring-1 ring-line">
        {list.map((c) => {
          const spike = settings && c.volume_ratio != null && c.volume_ratio >= settings.volume_ratio;
          const big = settings && c.change_pct != null && Math.abs(c.change_pct) >= settings.price_move_pct;
          return (
            <button key={c.symbol} onClick={() => setOpen(c)} className="flex w-full items-center gap-3 px-4 py-3 text-left active:bg-line-2">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-[15px] font-semibold tracking-tight">{c.symbol}</span>
                  <span className="rounded bg-line-2 px-1.5 py-0.5 text-[10px] font-medium leading-none text-mute">{c.cap_band}</span>
                  {spike && <span className="num rounded bg-warn-soft px-1.5 py-0.5 text-[10px] font-semibold leading-none text-warn">{c.volume_ratio?.toFixed(1)}× vol</span>}
                </div>
                <p className="truncate text-[12px] text-mute">{c.sector}</p>
                {c.latest_event && <p className="mt-0.5 truncate text-[12px] text-accent">{CATEGORY_LABEL[c.latest_event.category]} · {c.latest_event.summary}</p>}
              </div>
              <Spark data={c.spark} w={56} h={24} />
              <div className="w-[4.6rem] text-right">
                <p className="num text-[14px] font-medium">{inr(c.last_close)}</p>
                <p className={`num text-[12px] ${big ? "font-semibold" : ""}`}><Delta v={c.change_pct} digits={2} /></p>
              </div>
            </button>
          );
        })}
      </div>

      <Sheet open={!!open} onClose={() => setOpen(null)} title={open?.symbol ?? ""}>
        {open && (
          <div>
            <p className="text-[15px]">{open.name}</p>
            <p className="text-[13px] text-mute">{open.sector} · {open.cap_band} cap · {mcap(open.market_cap_cr)}</p>
            <div className="mt-4 flex items-end justify-between">
              <div>
                <p className="num text-[30px] font-semibold tracking-tight">{inr(open.last_close)}</p>
                <Delta v={open.change_pct} digits={2} className="text-[15px] font-medium" />
              </div>
              <Spark data={open.spark} w={120} h={44} />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2 text-[13px]">
              <div className="rounded-xl bg-surface p-3 ring-1 ring-line"><p className="text-mute">Volume vs 20d</p><p className="num text-[17px] font-semibold">{open.volume_ratio?.toFixed(2) ?? "–"}×</p></div>
              <div className="rounded-xl bg-surface p-3 ring-1 ring-line"><p className="text-mute">Last close</p><p className="num text-[17px] font-semibold">{open.last_date ? fmtDay(open.last_date) : "–"}</p></div>
            </div>
            {open.latest_event && <p className="mt-3 rounded-xl bg-accent-soft p-3 text-[13px] text-accent"><b>{CATEGORY_LABEL[open.latest_event.category]}</b> · {open.latest_event.summary}</p>}
            <button onClick={() => nav(`/research/${open.symbol}`)} className="mt-4 h-12 w-full rounded-2xl bg-ink text-[15px] font-semibold text-paper">Research {open.symbol}</button>
          </div>
        )}
      </Sheet>
    </div>
  );
}
