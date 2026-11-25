"use client";
import { useEffect, useState, type ReactNode } from "react";
import { BarChart3, BookOpen, Bell, Brain, CandlestickChart, ChevronsLeft, ChevronsRight, Clock, Compass, Cpu, FlaskConical, Globe, Home, LineChart, Settings, Shield, Signal, SquareTerminal, Wifi } from "lucide-react";
import { fmt, useFetch } from "@/lib/api";
import { FlashNumber, StatusDot } from "@/components/ui";

export interface NavItem { key: string; label: string; icon: ReactNode }
const I = 16;
export const GROUPS: { title: string; icon: ReactNode; items: NavItem[] }[] = [
  { title: "Live", icon: <Globe size={12} />, items: [
    { key: "overview", label: "Overview", icon: <Home size={I} /> },
    { key: "terminal", label: "Trading Terminal", icon: <SquareTerminal size={I} /> },
    { key: "charts", label: "Market Charts", icon: <CandlestickChart size={I} /> },
  ] },
  { title: "Research", icon: <FlaskConical size={12} />, items: [
    { key: "discovery", label: "Strategy Discovery", icon: <Compass size={I} /> },
    { key: "research", label: "Quantitative Research", icon: <LineChart size={I} /> },
    { key: "ml", label: "Machine Learning", icon: <Cpu size={I} /> },
    { key: "timemachine", label: "Time Machine", icon: <Clock size={I} /> },
  ] },
  { title: "Market", icon: <Signal size={12} />, items: [
    { key: "intel", label: "Market Intelligence", icon: <Brain size={I} /> },
    { key: "skills", label: "Trading Skills", icon: <BookOpen size={I} /> },
  ] },
  { title: "System", icon: <Settings size={12} />, items: [
    { key: "risk", label: "Risk Management", icon: <Shield size={I} /> },
    { key: "performance", label: "Performance and Audit", icon: <BarChart3 size={I} /> },
  ] },
];

const SESSION_TONE: Record<string, string> = {
  london: "text-sky-300 border-sky-400/40 bg-sky-400/10", new_york: "text-up border-up/40 bg-up/10",
  tokyo: "text-pink-300 border-pink-400/40 bg-pink-400/10", sydney: "text-warn border-warn/40 bg-warn/10", closed: "text-mute border-line bg-surface2",
};

export function Sidebar({ active, onOpen, collapsed, onToggle, health }: { active: string; onOpen: (k: string) => void; collapsed: boolean; onToggle: () => void; health: any }) {
  const issues = health ? [!health.warehouse_ready, !health.broker_connected, !health.audit_chain_intact].filter(Boolean).length : 0;
  return (
    <nav aria-label="Sections" className={`fixed bottom-0 left-0 right-0 z-30 flex h-14 flex-row items-center gap-1 overflow-x-auto border-t border-line bg-side px-2 md:sticky md:top-0 md:h-screen md:shrink-0 md:flex-col md:items-stretch md:gap-0 md:overflow-y-auto md:overflow-x-hidden md:border-r md:border-t-0 md:px-2 md:py-3 ${collapsed ? "md:w-[60px]" : "md:w-[220px]"} transition-[width] duration-150`}>
      <div className="hidden items-center justify-between px-2 pb-3 md:flex">
        {!collapsed && <span className="text-[15px] font-bold tracking-tight">Quant<span className="text-brand-soft">Research</span></span>}
        <button aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={onToggle} className="rounded-input p-1.5 text-mute transition hover:bg-white/5 hover:text-ink">
          {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
        </button>
      </div>
      {GROUPS.map((g) => (
        <div key={g.title} className="flex flex-row md:mb-2 md:block">
          {!collapsed && (
            <div className="hidden items-center gap-1.5 px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-mute md:flex">{g.icon}{g.title}</div>
          )}
          {collapsed && <div className="mx-2 my-1 hidden border-t border-line md:block" />}
          {g.items.map((it) => (
            <button key={it.key} aria-label={it.label} title={it.label} aria-current={active === it.key ? "page" : undefined} onClick={() => onOpen(it.key)}
              className={`group flex shrink-0 items-center gap-3 rounded-input border-l-2 px-3 py-2 text-left text-[13px] transition-colors duration-150 md:w-full ${collapsed ? "md:justify-center md:px-0" : ""} ${active === it.key ? "border-brand bg-brand/15 text-ink" : "border-transparent text-mute hover:bg-white/[0.04] hover:text-ink"}`}>
              <span className={active === it.key ? "text-brand-soft" : ""}>{it.icon}</span>
              {!collapsed && <span className="hidden md:inline">{it.label}</span>}
            </button>
          ))}
        </div>
      ))}
      <div className="mt-auto hidden space-y-2 border-t border-line px-2 pt-3 md:block">
        <div className={`flex items-center gap-2 text-xs ${collapsed ? "justify-center" : ""}`} title="System status">
          <StatusDot tone={!health ? "off" : issues ? "bad" : "ok"} pulse={!!health && !issues} />
          {!collapsed && <span className="text-mute">{!health ? "Connecting…" : issues ? `${issues} issue${issues > 1 ? "s" : ""}` : "System OK"}</span>}
        </div>
        {!collapsed && health && !health.warehouse_ready && <div className="text-[11px] text-mute">Warehouse not built</div>}
      </div>
    </nav>
  );
}

export function TopBar({ title, overview, health }: { title: string; overview: any; health: any }) {
  const q = overview?.quote ?? {};
  const [pulse, setPulse] = useState(false);
  useEffect(() => {
    if (!q.bid) return;
    setPulse(true);
    const id = setTimeout(() => setPulse(false), 400);
    return () => clearTimeout(id);
  }, [q.bid, q.ask]);
  const spread = q.bid && q.ask ? q.ask - q.bid : null;
  const session = String(overview?.session ?? "closed").toLowerCase();
  const stale = q.source === "stored_history_not_live";
  return (
    <header className="sticky top-0 z-20 grid h-[60px] grid-cols-[1fr_auto_1fr] items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur md:px-6">
      <div className="truncate text-sm font-semibold text-ink">{title}</div>
      <div className={`flex items-baseline gap-3 rounded-full border border-line bg-surface px-4 py-1 font-mono transition-shadow ${pulse ? "shadow-glow" : ""}`} title={stale ? "Last stored bar, not a live quote" : "Live quote"}>
        <span className="hidden font-sans text-[11px] font-medium text-mute sm:inline">XAUUSD</span>
        {q.bid ? (
          <>
            <FlashNumber value={q.bid} className="text-lg font-semibold tabular-nums text-ink">{fmt(q.bid)}</FlashNumber>
            <span className="text-xs tabular-nums text-mute">{fmt(spread)}</span>
            <FlashNumber value={q.ask} className="text-lg font-semibold tabular-nums text-ink">{fmt(q.ask)}</FlashNumber>
            {stale && <span className="font-sans text-[10px] uppercase text-warn">stored</span>}
          </>
        ) : <span className="text-sm text-mute">No quote</span>}
      </div>
      <div className="flex items-center justify-end gap-2 md:gap-3">
        <span className={`hidden rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide sm:inline ${SESSION_TONE[session] ?? SESSION_TONE.closed}`}>{session.replace("_", " ")}</span>
        <Bell size={16} className="text-mute" aria-label="Notifications" />
        <Settings size={16} className="hidden text-mute sm:block" aria-label="Settings" />
        <span className="flex items-center gap-1.5" title={health?.broker_connected ? "Broker connected" : "Broker not connected"}>
          <Wifi size={16} className="text-mute" /><StatusDot tone={!health ? "off" : health.broker_connected ? "ok" : "warn"} pulse={!!health?.broker_connected} />
        </span>
      </div>
    </header>
  );
}

export function useShellData() {
  const overview = useFetch("/api/overview", 10000);
  const health = useFetch("/api/health", 30000);
  return { overview: overview.data, health: health.data };
}
