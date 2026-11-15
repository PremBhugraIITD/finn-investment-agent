import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import Today from "./pages/Today";
import Alerts from "./pages/Alerts";
import Research from "./pages/Research";
import Portfolio from "./pages/Portfolio";
import SettingsSheet from "./components/SettingsSheet";

const Icon = {
  today: <path d="M4 5h12M4 10h12M4 15h7" />,
  alerts: <><path d="M10 3a5 5 0 0 0-5 5v3l-1.5 3h13L15 11V8a5 5 0 0 0-5-5z" /><path d="M8 16.5a2 2 0 0 0 4 0" /></>,
  research: <><circle cx="9" cy="9" r="5" /><path d="M13 13l4 4" /></>,
  portfolio: <path d="M3 15l4-5 3 3 6-8" />,
};

function Tab({ to, label, icon, badge }: { to: string; label: string; icon: keyof typeof Icon; badge?: number }) {
  return (
    <NavLink to={to} className={({ isActive }) =>
      `relative flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px] font-medium md:flex-none md:flex-row md:gap-3 md:rounded-xl md:px-4 md:py-2.5 md:text-[14px] ${isActive ? "text-ink md:bg-surface md:ring-1 md:ring-line" : "text-faint hover:text-ink-2"}`}>
      {({ isActive }) => (
        <>
          <span className="relative">
            <svg width="22" height="22" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={isActive ? 1.9 : 1.5} strokeLinecap="round" strokeLinejoin="round">{Icon[icon]}</svg>
            {!!badge && <span className="num absolute -right-2 -top-1.5 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-semibold text-white">{badge > 9 ? "9+" : badge}</span>}
          </span>
          {label}
        </>
      )}
    </NavLink>
  );
}

export default function App() {
  const [settings, setSettings] = useState(false);
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: api.alerts, refetchInterval: 60_000 });
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-5xl flex-col md:flex-row md:gap-6 md:px-6">
      <nav aria-label="Primary"
        className="safe-b fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-paper/95 backdrop-blur md:static md:z-0 md:w-52 md:flex-col md:gap-1 md:border-0 md:bg-transparent md:pt-8">
        <div className="hidden px-4 pb-4 text-[22px] font-bold tracking-tight md:block">FINN</div>
        <Tab to="/today" label="Today" icon="today" />
        <Tab to="/alerts" label="Alerts" icon="alerts" badge={alerts.data?.unread} />
        <Tab to="/research" label="Research" icon="research" />
        <Tab to="/portfolio" label="Portfolio" icon="portfolio" />
      </nav>
      <main className="min-w-0 flex-1 pb-24 md:max-w-2xl md:pb-10 md:pt-4">
        <Routes>
          <Route path="/" element={<Navigate to="/today" replace />} />
          <Route path="/today" element={<Today onSettings={() => setSettings(true)} />} />
          <Route path="/alerts" element={<Alerts onSettings={() => setSettings(true)} />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:symbol" element={<Research />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="*" element={<Navigate to="/today" replace />} />
        </Routes>
      </main>
      <SettingsSheet open={settings} onClose={() => setSettings(false)} />
    </div>
  );
}
