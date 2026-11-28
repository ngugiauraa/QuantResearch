"use client";
import { useState } from "react";
import { api, fmt, useFetch, when } from "@/lib/api";
import { ValueChart } from "@/components/PriceChart";
import { Card, ErrorBox, Loading, Notice, Pill, Stat, Table } from "@/components/ui";

const CRITERIA: Record<string, string> = {
  net_positive: "Net positive after real demo costs",
  beats_buy_and_hold: "Beats risk-matched long-only gold",
  drawdown_under_10: "Max drawdown under 10%",
  costs_within_model: "Slippage and spread within the backtest's cost model",
};
const SHORT = (name: string) => name.replace(/^cat_/, "");
const points = (rows?: [string, number][]) => (rows ?? []).map(([t, v]) => ({ time: Math.floor(Date.parse(t) / 1000), value: v }));

function criterionValue(key: string, c: any): string {
  if (!c) return "–";
  if (key === "net_positive") return fmt(c.value, 2);
  if (key === "drawdown_under_10") return `${fmt(c.value, 2)}%`;
  if (key === "beats_buy_and_hold") return c.value?.available ? `Sharpe ${fmt(c.value.strategy_sharpe)} vs gold ${fmt(c.value.buy_hold_sharpe)} (${c.value.days} days)` : (c.value?.reason ?? "–");
  if (key === "costs_within_model") return c.value?.fills ? `slippage ${fmt(c.value.mean_slippage, 3)}, spread ${fmt(c.value.mean_spread, 3)} over ${c.value.fills} fills` : "no fills yet";
  return "–";
}

/**
 * Forward test 001: the seven catalogue near-misses as one portfolio on the MT5 demo account, pre-registered in
 * docs/forward_test_001.md. Live demo results sit next to what the backtest predicts for the same period. Orders are only
 * ever sent while MT5 reports a demo account; anything else raises the red alert below and nothing is sent.
 */
export function ForwardTest() {
  const f = useFetch("/api/forward", 15000);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const d = f.data;
  const post = async (path: string, body?: unknown) => {
    setError(null);
    setBusy(true);
    try {
      await api(path, { method: "POST", body });
      await f.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const live = d?.live, pred = d?.prediction, ev = d?.evaluation;
  if (f.loading && !d) return <><h1>Forward Test</h1><Loading /></>;

  return (
    <>
      <h1>Forward Test</h1>
      <ErrorBox error={f.error ?? error} />
      {d?.alert && (
        <div className="error" role="alert" style={{ fontWeight: 600 }}>
          Orders refused: MT5 reported a {d.alert.account_type ?? "non-demo"} account{d.alert.server ? ` (${d.alert.server})` : ""} at {when(d.alert.at)}.
          Nothing was sent and the test is paused. Switch the terminal back to the demo account, then clear this alert.
          <div style={{ marginTop: 8 }}><button className="btn" disabled={busy} onClick={() => post("/api/forward/clear-alert", { note: "cleared from the dashboard" })}>Clear alert</button></div>
        </div>
      )}
      <Notice>
        Pre-registered in docs/forward_test_001.md before the start: the seven catalogue near-misses run as one portfolio on the MT5 <b>demo</b> account, 0.25% risk
        per trade, at most 1% of equity at risk in one direction. One decision for the whole portfolio after at least six months (and 30 closed trades, at most
        twelve months): net positive after real costs, beats long-only gold at the same risk, drawdown under 10%, and slippage and spreads within the backtest&apos;s
        stressed allowances. Individual strategies are reported but cannot pass on their own. Live trading stays off.
      </Notice>

      {!d?.start ? (
        <Card title="Not started">
          <div className="muted" style={{ marginBottom: 8 }}>The test starts once, by hand, when every check passes. After that it runs with the API and resumes after restarts.</div>
          <Table columns={["Check", "OK", "Detail"]} rows={Object.entries(d?.readiness?.checks ?? {}).map(([k, c]: [string, any]) => [
            k.replace(/_/g, " "), c.ok ? <Pill key="o" tone="ok">yes</Pill> : <Pill key="o" tone="bad">no</Pill>, c.detail])} />
          <div style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={busy || !d?.readiness?.ready} onClick={() => post("/api/forward/start")}>Start forward test 001</button>
          </div>
        </Card>
      ) : (
        <>
          <div className="grid">
            <Stat label="Started" value={when(d.start.at)} sub={`equity ${fmt(d.start.equity, 0)}`} />
            <Stat label="Evaluation date" value={ev ? when(ev.eval_date) : "–"} sub={ev?.due ? "decision due" : `needs ${ev?.min_trades ?? 30} closed trades, at most ${when(ev?.hard_end)}`} />
            <Stat label="Closed trades" value={fmt(ev?.closed_trades, 0)} sub={`${fmt(ev?.open_trades, 0)} open`} />
            <Stat label="Net (live demo)" value={fmt(ev?.net, 2)} tone={(ev?.net ?? 0) >= 0 ? "up" : "down"} sub={pred ? `backtest predicts ${fmt(pred.net, 2)}` : "prediction pending"} />
            <Stat label="Result" value={ev?.frozen ? (ev.passed ? "PASS" : "FAIL") : "Interim"} tone={ev?.frozen ? (ev.passed ? "up" : "down") : "warn"} />
          </div>
          <Card title="Pass or fail (all four must hold)">
            <Table columns={["Rule", "Live demo now", "Backtest prediction", "Holds"]} rows={Object.keys(CRITERIA).map((k) => {
              const c = ev?.criteria?.[k], pc = pred?.evaluation?.criteria?.[k];
              return [CRITERIA[k], criterionValue(k, c), criterionValue(k, pc), c ? (c.pass ? <Pill key="p" tone="ok">yes</Pill> : <Pill key="p" tone="bad">no</Pill>) : "–"];
            })} />
            <div className="muted" style={{ marginTop: 8 }}>{ev?.note}</div>
          </Card>
          <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
            <Card title="Live demo equity (this test only)">{live?.equity?.length > 1 ? <ValueChart points={points(live.equity)} height={220} /> : <div className="muted">Waiting for samples.</div>}</Card>
            <Card title={`Backtest prediction, same period${pred ? ` (computed ${when(pred.computed_at)})` : ""}`}>{pred?.equity?.length > 1 ? <ValueChart points={points(pred.equity)} color="#22c55e" height={220} /> : <div className="muted">Computed hourly once bars exist after the start.</div>}</Card>
          </div>
          <Card title="By strategy (reported, cannot pass alone)">
            <Table columns={["Strategy", "Live trades", "Live open", "Live net", "Predicted trades", "Predicted net"]} rows={(d.strategies ?? []).map((n: string) => {
              const l = live?.per_strategy?.[n], p = pred?.per_strategy?.[n];
              return [SHORT(n), fmt(l?.trades, 0), fmt(l?.open, 0), fmt(l?.net, 2), fmt(p?.trades, 0), fmt(p?.net, 2)];
            })} />
          </Card>
          <Card title="Missed signals">
            <Table columns={["Reason", "Live", "Prediction"]} rows={Array.from(new Set([...Object.keys(live?.missed ?? {}), ...Object.keys(pred?.missed ?? {})])).map((r) => [
              r.replace(/_/g, " "), fmt(live?.missed?.[r] ?? 0, 0), fmt(pred?.missed?.[r] ?? 0, 0)])} empty="None so far." />
            <div className="muted" style={{ marginTop: 8 }}>Late means the PC, API or terminal was off at the bar close (more than 10% of the bar late); those entries are not chased.</div>
          </Card>
          <Card title="Trades (live demo)">
            <Table columns={["Strategy", "Side", "Lots", "Filled", "Fill", "Exit", "Exit price", "Reason", "Profit"]} rows={(live?.trades ?? []).slice().reverse().slice(0, 100).map((t: any) => [
              SHORT(t.strategy), t.side > 0 ? "long" : "short", fmt(t.lots), when(t.fill_at), fmt(t.fill_price), t.exit_at ? when(t.exit_at) : "open", fmt(t.exit_price), t.exit_reason ?? "–", fmt(t.profit)])} empty="No trades yet." />
          </Card>
        </>
      )}
      <Card title="Journal (latest first)">
        <Table columns={["Time", "Bar", "Event", "Strategy", "Detail"]} rows={(d?.events ?? []).slice(0, 80).map((e: any) => [
          when(e.at), e.bar_time ? when(e.bar_time) : "–", e.kind, e.strategy ? SHORT(e.strategy) : "–",
          e.reason ?? e.message ?? (e.price ? `price ${fmt(e.price)}` : e.side ? (e.side > 0 ? "long" : "short") : "")])} empty="Nothing recorded yet." />
        {d?.last_error && <div className="muted" style={{ marginTop: 8 }}>Last broker problem: {d.last_error}</div>}
      </Card>
    </>
  );
}
