import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { SettingsT } from "../types";
import { CATEGORY_LABEL } from "../types";
import { Sheet } from "../ui";

const LS = "finn.settings.v1";
const ALL = ["results", "M&A", "fund_raise", "management_change", "litigation", "credit_rating", "capex", "pledging",
  "dividend", "bonus_split", "insider_trading", "press_release", "earnings_call", "investor_meetings", "agm_egm", "postal_ballot"];

export function useSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 60_000 });
  // Restore the browser backup if the server lost its state (ephemeral disk) or differs.
  useEffect(() => {
    if (!q.data) return;
    try {
      const raw = localStorage.getItem(LS);
      if (raw && JSON.stringify(JSON.parse(raw)) !== JSON.stringify(q.data)) {
        api.putSettings(JSON.parse(raw)).then((s) => qc.setQueryData(["settings"], s)).catch(() => {});
      }
    } catch { /* ignore */ }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data === undefined]);
  const m = useMutation({
    mutationFn: api.putSettings,
    onSuccess: (s) => {
      localStorage.setItem(LS, JSON.stringify(s));
      qc.setQueryData(["settings"], s);
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["digest"] });
    },
  });
  return { settings: q.data, save: m.mutate, saving: m.isPending };
}

function Slider({ label, hint, value, min, max, step, fmt, onChange }: {
  label: string; hint: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void;
}) {
  return (
    <div className="py-3">
      <div className="flex items-baseline justify-between">
        <label className="text-[14px] font-medium">{label}</label>
        <span className="num text-[15px] font-semibold text-accent">{fmt(value)}</span>
      </div>
      <p className="mb-2 text-[12px] text-mute">{hint}</p>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))}
        className="h-8 w-full accent-[var(--color-accent)]" aria-label={label} />
    </div>
  );
}

export default function SettingsSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { settings, save } = useSettings();
  const [draft, setDraft] = useState<SettingsT | null>(null);
  useEffect(() => { if (open && settings) setDraft(settings); }, [open, settings]);
  if (!draft) return <Sheet open={open} onClose={onClose} title="What counts as unusual"><div className="skel h-40" /></Sheet>;
  const toggle = (c: string) =>
    setDraft({ ...draft, categories: draft.categories.includes(c) ? draft.categories.filter((x) => x !== c) : [...draft.categories, c] });
  return (
    <Sheet open={open} onClose={onClose} title="What counts as unusual">
      <p className="text-[13px] text-mute">You set the thresholds. FINN watches end-of-day data and every filing against them.</p>
      <div className="divide-y divide-line">
        <Slider label="Price move" hint="Alert when the daily move is beyond ±" value={draft.price_move_pct} min={1} max={10} step={0.5}
          fmt={(v) => `±${v}%`} onChange={(v) => setDraft({ ...draft, price_move_pct: v })} />
        <Slider label="Volume spike" hint="Alert when volume is above N × the 20-day average" value={draft.volume_ratio} min={1} max={5} step={0.25}
          fmt={(v) => `${v}×`} onChange={(v) => setDraft({ ...draft, volume_ratio: v })} />
        <Slider label="Filing materiality" hint="Alert when a filing scores at least" value={draft.materiality} min={30} max={95} step={5}
          fmt={(v) => `${v}`} onChange={(v) => setDraft({ ...draft, materiality: v })} />
      </div>
      <p className="mb-2 mt-3 text-[14px] font-medium">Filing types I care about</p>
      <div className="flex flex-wrap gap-2">
        {ALL.map((c) => {
          const on = draft.categories.includes(c);
          return (
            <button key={c} onClick={() => toggle(c)} aria-pressed={on}
              className={`h-8 rounded-full px-3 text-[13px] font-medium ring-1 ${on ? "bg-ink text-paper ring-ink" : "bg-surface text-ink-2 ring-line"}`}>
              {CATEGORY_LABEL[c]}
            </button>
          );
        })}
      </div>
      <button onClick={() => { save(draft); onClose(); }}
        className="mt-5 h-12 w-full rounded-2xl bg-ink text-[15px] font-semibold text-paper active:opacity-90">Save thresholds</button>
    </Sheet>
  );
}
