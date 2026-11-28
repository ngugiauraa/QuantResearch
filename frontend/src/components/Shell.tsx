"use client";
import { useEffect, useState, type ReactNode } from "react";
import { Activity, BarChart3, BookOpen, Bell, Brain, CandlestickChart, ChevronsLeft, ChevronsRight, Clock, Compass, Cpu, FlaskConical, Globe, Home, Library, LineChart, Menu, Settings, Shield, Signal, SquareTerminal, TrendingUp, Wifi, X } from "lucide-react";
import { fmt, useFetch } from "@/lib/api";
import { FlashNumber, StatusDot } from "@/components/ui";

export interface NavItem { key: string; label: string; icon: ReactNode }
const I = 16;
export const GROUPS: { title: string; icon: ReactNode; items: NavItem[] }[] = [
  { title: "Live", icon: <Globe size={12} />, items: [
    { key: "overview", label: "Overview", icon: <Home size={I} /> },
    { key: "terminal", label: "Trading Terminal", icon: <SquareTerminal size={I} /> },
    { key: "charts", label: "Market Charts", icon: <CandlestickChart size={I} /> },
    { key: "tradingview", label: "TradingView", icon: <TrendingUp size={I} /> },
  ] },
  { title: "Research", icon: <FlaskConical size={12} />, items: [
    { key: "discovery", label: "Strategy Discovery", icon: <Compass size={I} /> },
    { key: "research", label: "Quantitative Research", icon: <LineChart size={I} /> },
    { key: "catalogue", label: "Strategy Catalogue", icon: <Library size={I} /> },
    { key: "forward", label: "Forward Test", icon: <Activity size={I} /> },
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

/**
 * Below md (768px) the sidebar is a slide-in drawer, always expanded, opened from the top bar's menu button.
 * From md up it is the sticky, collapsible column it has always been.
 */
export function Sidebar({ active, onOpen, collapsed, onToggle, health, drawerOpen, onCloseDrawer }: { active: string; onOpen: (k: string) => void; collapsed: boolean; onToggle: () => void; health: any; drawerOpen: boolean; onCloseDrawer: () => void }) {
  const issues = health ? [!health.warehouse_ready, !health.broker_connected, !health.audit_chain_intact].filter(Boolean).length : 0;
  const hideWhenCollapsed = collapsed ? "md:hidden" : "";
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCloseDrawer(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen, onCloseDrawer]);
  return (
    <>
      <div aria-hidden onClick={onCloseDrawer} className={`fixed inset-0 z-30 bg-black/60 transition-opacity duration-150 md:hidden ${drawerOpen ? "opacity-100" : "pointer-events-none opacity-0"}`} />
      <nav id="sidebar" aria-label="Sections" className={`fixed inset-y-0 left-0 z-40 flex w-[260px] max-w-[85vw] flex-col overflow-y-auto overflow-x-hidden border-r border-line bg-side px-2 py-3 transition-[width,transform] duration-150 md:sticky md:top-0 md:z-auto md:h-screen md:max-w-none md:shrink-0 md:translate-x-0 ${drawerOpen ? "translate-x-0" : "-translate-x-full"} ${collapsed ? "md:w-[60px]" : "md:w-[220px]"}`}>
        <div className="flex items-center justify-between px-2 pb-3">
          <span className={`text-[15px] font-bold tracking-tight ${hideWhenCollapsed}`}>Quant<span className="text-brand-soft">Research</span></span>
          <button aria-label="Close menu" onClick={onCloseDrawer} className="flex h-11 w-11 items-center justify-center rounded-input text-mute transition hover:bg-white/5 hover:text-ink md:hidden">
            <X size={18} />
          </button>
          <button aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={onToggle} className="hidden rounded-input p-1.5 text-mute transition hover:bg-white/5 hover:text-ink md:block">
            {collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
          </button>
        </div>
        {GROUPS.map((g) => (
          <div key={g.title} className="mb-2">
            <div className={`flex items-center gap-1.5 px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-mute ${hideWhenCollapsed}`}>{g.icon}{g.title}</div>
            {collapsed && <div className="mx-2 my-1 hidden border-t border-line md:block" />}
            {g.items.map((it) => (
              <button key={it.key} aria-label={it.label} title={it.label} aria-current={active === it.key ? "page" : undefined} onClick={() => onOpen(it.key)}
                className={`group flex min-h-11 w-full items-center gap-3 rounded-input border-l-2 px-3 py-2 text-left text-[14px] transition-colors duration-150 md:min-h-0 md:text-[13px] ${collapsed ? "md:justify-center md:px-0" : ""} ${active === it.key ? "border-brand bg-brand/15 text-ink" : "border-transparent text-mute hover:bg-white/[0.04] hover:text-ink"}`}>
                <span className={active === it.key ? "text-brand-soft" : ""}>{it.icon}</span>
                <span className={hideWhenCollapsed}>{it.label}</span>
              </button>
            ))}
          </div>
        ))}
        <div className="mt-auto space-y-2 border-t border-line px-2 pt-3">
          <div className={`flex items-center gap-2 text-xs ${collapsed ? "md:justify-center" : ""}`} title="System status">
            <StatusDot tone={!health ? "off" : issues ? "bad" : "ok"} pulse={!!health && !issues} />
            <span className={`text-mute ${hideWhenCollapsed}`}>{!health ? "Connecting…" : issues ? `${issues} issue${issues > 1 ? "s" : ""}` : "System OK"}</span>
          </div>
          {health && !health.warehouse_ready && <div className={`text-[11px] text-mute ${hideWhenCollapsed}`}>Warehouse not built</div>}
        </div>
      </nav>
    </>
  );
}

export function TopBar({ title, overview, terminal, health, onMenu, menuOpen }: { title: string; overview: any; terminal: any; health: any; onMenu: () => void; menuOpen: boolean }) {
  // Same quote the Trading Terminal shows: /api/terminal reads it from the broker. /api/overview only carries the
  // paper broker's refreshed quote, which is empty when MT5 is attached, so it is just the fallback here.
  const q = (terminal?.quote?.bid ? terminal.quote : overview?.quote) ?? {};
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
    <header className="sticky top-0 z-20 flex flex-wrap items-center gap-x-2 gap-y-2 border-b border-line bg-bg/85 px-3 py-2 backdrop-blur md:grid md:h-[60px] md:grid-cols-[1fr_auto_1fr] md:gap-3 md:px-6 md:py-0">
      <div className="flex min-w-0 flex-1 items-center gap-1">
        <button aria-label="Open menu" aria-controls="sidebar" aria-expanded={menuOpen} onClick={onMenu} className="-ml-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-input text-mute transition hover:bg-white/5 hover:text-ink md:hidden">
          <Menu size={20} />
        </button>
        <div className="truncate text-sm font-semibold text-ink">{title}</div>
      </div>
      <div className={`order-last flex w-full min-w-0 items-baseline justify-center gap-2 rounded-full border border-line bg-surface px-3 py-1 font-mono transition-shadow md:order-none md:w-auto md:gap-3 md:px-4 ${pulse ? "shadow-glow" : ""}`} title={stale ? "Last stored bar, not a live quote" : "Live quote"}>
        <span className="font-sans text-[11px] font-medium text-mute">XAUUSD</span>
        {q.bid ? (
          <>
            <FlashNumber value={q.bid} className="text-base font-semibold tabular-nums text-ink md:text-lg">{fmt(q.bid)}</FlashNumber>
            <span className="text-xs tabular-nums text-mute">{fmt(spread)}</span>
            <FlashNumber value={q.ask} className="text-base font-semibold tabular-nums text-ink md:text-lg">{fmt(q.ask)}</FlashNumber>
            {stale && <span className="font-sans text-[10px] uppercase text-warn">stored</span>}
          </>
        ) : <span className="text-sm text-mute">No quote</span>}
      </div>
      <div className="flex shrink-0 items-center justify-end gap-2 md:gap-3">
        <span className={`whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide md:px-2.5 md:text-[11px] ${SESSION_TONE[session] ?? SESSION_TONE.closed}`}>{session.replace("_", " ")}</span>
        <Bell size={16} className="shrink-0 text-mute" aria-label="Notifications" />
        <Settings size={16} className="hidden shrink-0 text-mute sm:block" aria-label="Settings" />
        <span className="flex shrink-0 items-center gap-1.5" title={health?.broker_connected ? "Broker connected" : "Broker not connected"}>
          <Wifi size={16} className="text-mute" /><StatusDot tone={!health ? "off" : health.broker_connected ? "ok" : "warn"} pulse={!!health?.broker_connected} />
        </span>
      </div>
    </header>
  );
}

export function useShellData() {
  const overview = useFetch("/api/overview", 10000);
  const health = useFetch("/api/health", 30000);
  // The same endpoint and refresh rate the Trading Terminal uses for its bid/ask.
  const terminal = useFetch("/api/terminal", 5000);
  return { overview: overview.data, health: health.data, terminal: terminal.data };
}
