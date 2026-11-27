"use client";
import { useMemo, useState } from "react";
import { Check, Copy, Database, Radar } from "lucide-react";
import { fmt, useFetch, when } from "@/lib/api";
import { adx, bbWidthPercentile } from "@/lib/indicators";
import { Card, EmptyState, ErrorBox, FlashNumber, Notice, Skeleton, Stat, StatusDot } from "@/components/ui";
import { PriceChart } from "@/components/PriceChart";

const TFS = ["m5", "m15", "h1", "h4"] as const;
const BOOTSTRAP = "PYTHONPATH=src python -m quantresearch.warehouse.fetch_months m1 --workers 2 && PYTHONPATH=src python -m quantresearch.warehouse.build --start 2003-05";

const REGIME_STYLE: Record<string, string> = {
  trending_up: "bg-up/15 text-up border-up/40", trending_down: "bg-down/15 text-down border-down/40",
  ranging: "bg-warn/15 text-warn border-warn/40", volatile: "bg-brand/20 text-brand-soft border-brand/50",
  normal: "bg-up/15 text-up border-up/40", quiet: "bg-warn/15 text-warn border-warn/40",
};

function Copyable({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="btn mono flex w-full items-center justify-between gap-2 text-left text-[11px]" onClick={() => { navigator.clipboard?.writeText(text); setDone(true); setTimeout(() => setDone(false), 1500); }} aria-label="Copy command">
      <span className="break-all text-brand-soft">{text}</span>{done ? <Check size={14} className="shrink-0 text-up" /> : <Copy size={14} className="shrink-0 text-mute" />}
    </button>
  );
}

function Badge({ ok, warn, children }: { ok?: boolean; warn?: boolean; children: React.ReactNode }) {
  const tone = ok ? "ok" : warn ? "warn" : "bad";
  return <span className={`pill ${tone}`}><StatusDot tone={tone} pulse={ok} />{children}</span>;
}

function HeroChart({ ready }: { ready: boolean }) {
  const [tf, setTf] = useState<(typeof TFS)[number]>("h1");
  const bars = useFetch(ready ? `/api/bars?tf=${tf}&limit=48` : null, 30000);
  const candles = useMemo(() => (bars.data?.bars ?? []).map((b: any) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })), [bars.data]);
  return (
    <div className="card !p-0" style={{ boxShadow: "0 0 32px rgba(124,58,237,0.18)" }}>
      <div className="flex items-center justify-between px-4 pt-3">
        <div className="label">XAUUSD · last 48 bars</div>
        <div className="flex gap-1 rounded-full border border-line bg-surface2 p-1" role="group" aria-label="Timeframe">
          {TFS.map((t) => (
            <button key={t} onClick={() => setTf(t)} className={`rounded-full px-3 py-0.5 text-xs font-semibold transition-colors duration-150 ${t === tf ? "bg-brand text-white shadow-glow" : "text-mute hover:text-ink"}`}>{t.toUpperCase()}</button>
          ))}
        </div>
      </div>
      <div className="p-2">
        {!ready ? <Skeleton className="h-[360px]" />
          : bars.loading && !candles.length ? <Skeleton className="h-[360px]" />
          : candles.length ? <PriceChart candles={candles} height={360} />
          : <EmptyState className="h-[360px]" text={bars.error ?? `No ${tf.toUpperCase()} bars in the warehouse yet.`} />}
      </div>
    </div>
  );
}

export function Overview() {
  const { data, error, loading } = useFetch("/api/overview", 15000);
  const health = useFetch("/api/health", 30000);
  const risk = useFetch("/api/risk", 30000);
  const ready = !!health.data?.warehouse_ready;
  const h1 = useFetch(ready ? "/api/bars?tf=h1&limit=300" : null, 60000);
  const q = data?.quote ?? {};
  const external = health.data?.external?.status ?? {};
  const failed = Object.entries(external).filter(([, v]) => String(v).startsWith("failed"));
  const regime = data?.regime;
  const ind = useMemo(() => {
    const b: any[] = h1.data?.bars ?? [];
    return { adx: adx(b), bb: bbWidthPercentile(b) };
  }, [h1.data]);
  const dailyLoss: number | undefined = data?.risk?.daily_loss_pct;
  const dailyLimit: number | undefined = risk.data?.limits?.max_daily_loss_pct;
  const ddLimit: number = risk.data?.limits?.max_drawdown_pct ?? 10;
  const dd: number = data?.risk?.drawdown_pct ?? 0;
  const ddUse = Math.min(1, dd / ddLimit);
  const equity = data?.account?.equity, start = data?.risk?.day_start_equity;
  const dayPnl = typeof equity === "number" && typeof start === "number" ? equity - start : null;
  const signals: any[] = data?.recent_signals ?? [];
  const regimeKey = String(regime?.regime ?? "").toLowerCase();

  return (
    <div className="space-y-4">
      <h1>Overview</h1>
      <ErrorBox error={error} />
      {q.source === "stored_history_not_live" && <Notice>No live feed is connected. The price shown is the last stored bar, from {when(q.time)}. It is not a live quote.</Notice>}
      {data?.account_error && <div className="error">{data.account_error}</div>}

      <div className="relative">
        <HeroChart ready={ready} />
        <div className="pointer-events-none absolute left-6 top-14 z-10">
          <div className="label">XAUUSD bid / ask</div>
          <div className="glow-num mono text-3xl font-bold tabular-nums text-brand-soft">
            {q.bid ? <><FlashNumber value={q.bid}>{fmt(q.bid)}</FlashNumber> <span className="text-mute">/</span> <FlashNumber value={q.ask}>{fmt(q.ask)}</FlashNumber></> : <span className="text-lg text-mute">No data</span>}
          </div>
        </div>
        {q.bid && <div className="pointer-events-none absolute bottom-8 right-20 z-10 rounded-full border border-line bg-surface/80 px-3 py-0.5 text-xs text-mute">spread <span className="mono text-ink">{fmt(q.ask - q.bid)}</span></div>}
      </div>

      <div className="grid-4">
        <Stat label="Equity" value={fmt(equity)} numeric={equity} sub={start ? `Day start ${fmt(start)}` : "No account data"} />
        <Stat label="Balance" value={fmt(data?.account?.balance)} numeric={data?.account?.balance} sub={`Mode: ${data?.mode ?? "–"}`} />
        <Stat label="Daily P&L" value={dayPnl === null ? "–" : `${dayPnl >= 0 ? "+" : ""}${fmt(dayPnl)}`} numeric={dayPnl} tone={dayPnl === null ? undefined : dayPnl >= 0 ? "up" : "down"} sub={dailyLoss !== undefined ? `Daily loss ${fmt(dailyLoss)}%` : undefined} />
        <Stat label="Daily loss limit left" value={dailyLimit !== undefined && dailyLoss !== undefined ? `${fmt(Math.max(0, dailyLimit - dailyLoss))}%` : "–"} tone={dailyLimit !== undefined && dailyLoss !== undefined && dailyLoss > dailyLimit * 0.75 ? "warn" : undefined} sub={dailyLimit !== undefined ? `Limit ${fmt(dailyLimit)}%` : undefined} />
      </div>

      <div className="grid-3">
        <Card title="System health">
          {loading && !data ? <Skeleton className="h-24" /> : (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2">
                <Badge ok={ready}>Warehouse {ready ? "ready" : "not built"}</Badge>
                <Badge ok={!!health.data?.broker_connected} warn={!health.data?.broker_connected && data?.mode === "paper"}>Broker: {health.data?.broker ?? "–"}</Badge>
                <Badge ok={health.data?.mt5?.status === "connected"} warn>MT5 {health.data?.mt5?.status === "connected" ? "connected" : health.data?.mt5?.status === "not_configured" ? "not configured" : "not connected"}</Badge>
                <Badge ok={!!health.data?.audit_chain_intact}>Audit chain {health.data?.audit_chain_intact ? "intact" : "broken"}</Badge>
                <Badge ok={!health.data?.live_active} warn={!!health.data?.live_active}>Live trading {health.data?.live_active ? "ACTIVE" : "off"}</Badge>
              </div>
              {failed.length > 0 && <div className="text-xs text-warn">External sources failing: {failed.map(([k]) => k).join(", ")}</div>}
              {health.data && !ready && (
                <div className="space-y-2 rounded-card border border-brand/50 bg-brand/10 p-3 shadow-glow">
                  <div className="flex items-center gap-2 text-sm font-semibold"><Database size={16} className="text-brand-soft" />Run data bootstrap</div>
                  <div className="text-xs text-mute">The warehouse holds the price history every chart and strategy reads. Run this, then reload.</div>
                  <Copyable text={BOOTSTRAP} />
                </div>
              )}
            </div>
          )}
        </Card>

        <Card title="Regime status">
          {regime ? (
            <div className="space-y-3">
              <span className={`inline-block rounded-full border px-4 py-1.5 text-lg font-bold uppercase tracking-wide ${REGIME_STYLE[regimeKey] ?? "border-line bg-surface2 text-ink"}`}>{regime.regime}</span>
              <div className="grid-3 !gap-2">
                <div><div className="label">ADX (H1)</div><div className="mono text-lg font-semibold">{ind.adx === null ? "–" : fmt(ind.adx, 1)}</div></div>
                <div><div className="label">BB width pct</div><div className="mono text-lg font-semibold">{ind.bb ? `${Math.round(ind.bb.percentile)}` : "–"}</div></div>
                <div><div className="label">Model fit</div><div className="mono text-lg font-semibold">{regime.confidence > 0.99 ? ">99%" : `${Math.round(regime.confidence * 100)}%`}</div></div>
              </div>
              <div className="text-[11px] text-mute">ADX and BB width percentile are computed in the browser from the last 300 H1 bars. Regime duration is not published by the API.</div>
            </div>
          ) : <EmptyState icon={<Radar size={22} />} text={ready ? "Not enough H1 history to fit a regime." : "Regime needs the warehouse."} />}
        </Card>

        <Card title="Risk status">
          <div className="space-y-3">
            <div>
              <div className="mb-1 flex justify-between text-xs"><span className="label">Drawdown</span><span className="mono">{fmt(dd)}% <span className="text-mute">/ {fmt(ddLimit, 1)}%</span></span></div>
              <div className="h-2 overflow-hidden rounded-full bg-surface2"><div className="h-full rounded-full transition-all duration-300" style={{ width: `${ddUse * 100}%`, background: ddUse > 0.8 ? "#ef4444" : "#7c3aed", boxShadow: `0 0 10px ${ddUse > 0.8 ? "rgba(239,68,68,.6)" : "rgba(124,58,237,.6)"}` }} /></div>
            </div>
            <div className="flex items-baseline justify-between"><span className="label">Entries today</span><span className="mono text-xl font-bold">{fmt(data?.risk?.trades_today, 0)}</span></div>
            <div className="flex flex-wrap gap-2">
              {data?.risk?.emergency_stop ? <Badge>Emergency stop active</Badge> : <Badge ok>No emergency stop</Badge>}
              {data?.risk?.lockout_until ? <Badge>Locked out until {when(data.risk.lockout_until)}</Badge> : <Badge ok>No lockout</Badge>}
            </div>
          </div>
        </Card>
      </div>

      <Card title={`Recent strategy signals · last 48 bars (${data?.active_strategies?.length ?? 0} engines)`}>
        {signals.length ? (
          <div className="table-scroll">
            <table>
              <thead><tr><th>Time</th><th>Engine</th><th>Timeframe</th><th>Signal</th><th>Regime (now)</th></tr></thead>
              <tbody>
                {signals.map((s, i) => (
                  <tr key={i} style={s.side === "long" ? { boxShadow: "inset 3px 0 0 #7c3aed" } : undefined}>
                    <td className="mono">{when(s.time)}</td><td>{s.strategy}</td><td className="uppercase">{s.timeframe ?? "–"}</td>
                    <td className={s.side === "long" ? "up font-semibold" : "down font-semibold"}>{s.side}</td><td className="muted">{regime?.regime ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState icon={<Radar size={22} />} text="Waiting for market data" />}
        <div className="mt-2 text-xs text-mute">A signal is a rule firing, not a recommendation. Engines needing cross-market data or fitted cells are not shown here.</div>
      </Card>
    </div>
  );
}
