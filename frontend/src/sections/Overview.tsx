"use client";
import { fmt, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice, Pill, Stat, Table } from "@/components/ui";

export function Overview() {
  const { data, error, loading } = useFetch("/api/overview", 15000);
  const health = useFetch("/api/health", 30000);
  if (loading && !data) return <Loading what="Loading overview" />;
  const q = data?.quote ?? {};
  const external = health.data?.external?.status ?? {};
  const failed = Object.entries(external).filter(([, v]) => String(v).startsWith("failed"));
  return (
    <>
      <h1>Overview</h1>
      <ErrorBox error={error} />
      {q.source === "stored_history_not_live" && <Notice>No live feed is connected. The price shown is the last stored bar, from {when(q.time)}. It is not a live quote.</Notice>}
      {data?.account_error && <div className="error">{data.account_error}</div>}
      <div className="grid">
        <Stat label="XAUUSD bid / ask" value={q.bid ? `${fmt(q.bid)} / ${fmt(q.ask)}` : "No data"} />
        <Stat label="Equity" value={fmt(data?.account?.equity)} />
        <Stat label="Balance" value={fmt(data?.account?.balance)} />
        <Stat label="Trading mode" value={data?.mode ?? "–"} />
        <Stat label="Market status" value={data?.market_open_by_schedule ? "Open by schedule" : "Closed by schedule"} tone={data?.market_open_by_schedule ? "up" : "warn"} />
        <Stat label="Session" value={data?.session ?? "–"} />
        <Stat label="Regime" value={data?.regime ? `${data.regime.regime} (model fit ${data.regime.confidence > 0.99 ? ">99" : Math.round(data.regime.confidence * 100)}%)` : "Unavailable"} />
        <Stat label="Daily loss" value={data?.risk?.daily_loss_pct !== undefined ? `${fmt(data.risk.daily_loss_pct)}%` : "–"} />
      </div>
      <div className="grid wide">
        <Card title="Risk status">
          <div className="row">
            {data?.risk?.emergency_stop ? <Pill tone="bad">Emergency stop active</Pill> : <Pill tone="ok">No emergency stop</Pill>}
            {data?.risk?.lockout_until ? <Pill tone="bad">Locked out until {when(data.risk.lockout_until)}</Pill> : <Pill tone="ok">No lockout</Pill>}
          </div>
          <div className="muted">Drawdown {fmt(data?.risk?.drawdown_pct)}%. Entries today {fmt(data?.risk?.trades_today, 0)}.</div>
        </Card>
        <Card title="System health">
          <div className="row">
            <Pill tone={health.data?.warehouse_ready ? "ok" : "bad"}>Warehouse {health.data?.warehouse_ready ? "ready" : "not built"}</Pill>
            <Pill tone={health.data?.broker_connected ? "ok" : "bad"}>Broker: {health.data?.broker ?? "–"}</Pill>
            <Pill tone={health.data?.audit_chain_intact ? "ok" : "bad"}>Audit chain {health.data?.audit_chain_intact ? "intact" : "broken"}</Pill>
            <Pill tone={health.data?.live_active ? "warn" : "ok"}>Live trading {health.data?.live_active ? "ACTIVE" : "off"}</Pill>
          </div>
          {failed.length > 0 && <div className="warn">External sources failing: {failed.map(([k]) => k).join(", ")}</div>}
        </Card>
      </div>
      <Card title={`Recent strategy signals, last 48 hourly bars (${data?.active_strategies?.length ?? 0} engines)`}>
        <Table columns={["Strategy", "Bar closed", "Side"]} empty="No signals in the last 48 bars." rows={(data?.recent_signals ?? []).map((s: any) => [s.strategy, when(s.time), <span key="s" className={s.side === "long" ? "up" : "down"}>{s.side}</span>])} />
        <div className="muted" style={{ marginTop: 8 }}>A signal is a rule firing, not a recommendation. Engines needing cross-market data or fitted cells are not shown here.</div>
      </Card>
    </>
  );
}
