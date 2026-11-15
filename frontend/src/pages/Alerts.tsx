import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import FeedItem from "../components/FeedItem";
import type { AlertT } from "../types";
import { dayLabel, fmtDay, timeAgo } from "../lib";
import { Bars, Delta, Empty, ErrorBox, Skeleton, Spark, StatusChip } from "../ui";

function MarketAlert({ a }: { a: AlertT }) {
  const price = a.kind === "price";
  return (
    <article className="px-4 py-3.5">
      <div className="flex items-center gap-2 text-[12px]">
        {!a.read && <span className="h-2 w-2 rounded-full bg-accent" aria-label="Unread" />}
        <span className="text-[13px] font-semibold tracking-tight">{a.symbol}</span>
        <span className="rounded-md bg-line-2 px-1.5 py-0.5 text-[11px] font-medium leading-none text-mute">{price ? "Price" : "Volume"}</span>
        <span className="num ml-auto text-faint">{a.ts ? fmtDay(a.ts) : ""}</span>
      </div>
      <div className="mt-1.5 flex items-end justify-between gap-4">
        <div>
          <p className="num text-[22px] font-semibold tracking-tight">
            {price ? <Delta v={a.value} /> : <span>{a.value?.toFixed(1)}×</span>}
          </p>
          <p className="mt-0.5 text-[12px] text-mute">{a.reason}</p>
        </div>
        {price ? <Spark data={a.history ?? []} w={84} h={32} /> : <Bars data={a.history ?? []} w={84} h={32} />}
      </div>
      <p className="mt-1.5 text-[12px] text-faint">{a.company}{a.is_stale ? " · snapshot data" : ""}</p>
    </article>
  );
}

export default function Alerts({ onSettings }: { onSettings: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["alerts"], queryFn: api.alerts, refetchInterval: 60_000 });
  const readAll = useMutation({ mutationFn: () => api.readAlerts([], true), onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }) });
  const d = q.data;
  const s = d?.settings;
  return (
    <div>
      <header className="sticky top-0 z-20 bg-paper/95 px-4 pb-3 pt-3 backdrop-blur safe-t">
        <div className="flex items-center justify-between">
          <h1 className="text-[28px] font-semibold tracking-tight">Alerts</h1>
          <div className="flex items-center gap-2">
            {d && <StatusChip stale={d.is_stale} asOf={timeAgo(d.as_of)} />}
            <button onClick={onSettings} aria-label="Thresholds" className="grid h-9 w-9 place-items-center rounded-full bg-surface ring-1 ring-line">
              <svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <path d="M3 6h9M16 6h1M3 14h1M8 14h9" /><circle cx="14" cy="6" r="2" /><circle cx="6" cy="14" r="2" />
              </svg>
            </button>
          </div>
        </div>
        {s && (
          <button onClick={onSettings} className="num mt-2 flex w-full flex-wrap gap-x-3 gap-y-1 text-left text-[12px] text-mute">
            <span>Price ±{s.price_move_pct}%</span><span>Volume {s.volume_ratio}×</span><span>Filing ≥ {s.materiality}</span>
            <span className="text-accent">Edit</span>
          </button>
        )}
        {d && d.unread > 0 && (
          <div className="mt-3 flex items-center justify-between">
            <p className="text-[13px]"><span className="font-semibold">{d.unread} unread</span> <span className="text-mute">in the last 7 days</span></p>
            <button onClick={() => readAll.mutate()} className="text-[13px] font-medium text-accent">Mark all read</button>
          </div>
        )}
      </header>

      {q.isLoading && <Skeleton rows={4} />}
      {q.isError && <ErrorBox message={(q.error as Error).message} retry={() => q.refetch()} />}
      {d && d.alerts.length === 0 && <Empty title="All quiet" hint="Nothing has crossed your thresholds. Loosen them if you want more signal." />}
      <div className="mx-3 space-y-3 pb-6">
        {d?.alerts.map((a) => (
          <div key={a.id} className={`overflow-hidden rounded-2xl bg-surface ring-1 ring-line ${a.read ? "opacity-70" : ""}`}
            onClick={() => !a.read && api.readAlerts([a.id]).then(() => qc.invalidateQueries({ queryKey: ["alerts"] }))}>
            {a.kind === "announcement" && a.item ? (
              <>
                <div className="flex items-center gap-2 border-b border-line-2 bg-accent-soft/50 px-4 py-2 text-[12px] text-accent">
                  {!a.read && <span className="h-2 w-2 rounded-full bg-accent" />}
                  <span className="font-medium">{a.reason}</span>
                  <span className="ml-auto text-faint">{dayLabel(a.ts)}</span>
                </div>
                <FeedItem item={a.item} />
              </>
            ) : <MarketAlert a={a} />}
          </div>
        ))}
      </div>
    </div>
  );
}
