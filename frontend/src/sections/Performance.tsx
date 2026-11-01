"use client";
import { fmt, pct, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Pill, Table, verdictText, verdictTone } from "@/components/ui";
import { ValueChart } from "@/components/PriceChart";

export function Performance() {
  const perf = useFetch("/api/performance", 15000);
  const audit = useFetch("/api/audit?limit=200", 15000);
  const models = useFetch("/api/ml/models");
  if (perf.loading && !perf.data) return <Loading />;
  const equity = (perf.data?.equity ?? []).map((p: any) => ({ time: Math.floor(new Date(p.time).getTime() / 1000), value: p.equity }));
  return (
    <>
      <h1>Performance and Audit</h1>
      <ErrorBox error={perf.error ?? audit.error ?? models.error} />
      <Card title="Paper account equity">
        {equity.length > 1 ? <ValueChart points={equity} /> : <div className="muted">Needs at least two closed paper trades.</div>}
      </Card>
      <Card title="Strategy comparison (walk-forward out of sample, plus holdout)">
        <Table columns={["Strategy", "Result", "Trades", "Net", "Sharpe", "Deflated Sharpe", "Profit factor", "Expectancy", "Win rate", "Max DD", "Holdout net", "Holdout under stress"]} empty="No strategy reports yet. Run them in Quantitative Research."
          rows={(perf.data?.strategy_comparison ?? []).map((r: any) => [r.title, <Pill key="v" tone={verdictTone(r.verdict)}>{verdictText[r.verdict] ?? r.verdict}</Pill>, fmt(r.trades, 0), fmt(r.net), fmt(r.sharpe), fmt(r.deflated_sharpe), fmt(r.profit_factor), fmt(r.expectancy), pct(r.win_rate, 1), pct(r.max_dd), fmt(r.holdout_net), fmt(r.holdout_stress)])} />
        <div className="muted" style={{ marginTop: 8 }}>Win rate alone says little. Compare expectancy, drawdown and results after stressed costs. None of these results qualifies a strategy for live trading.</div>
      </Card>
      <Card title="Trade journal (paper)">
        <Table columns={["Closed", "Ticket", "Side", "Lots", "Entry", "Exit", "Reason", "Net"]} empty="No paper trades yet." rows={(perf.data?.paper_trades ?? []).slice(0, 50).map((t: any) => [when(t.closed_at), t.ticket, t.side > 0 ? "Long" : "Short", t.lots, fmt(t.entry), fmt(t.exit), t.reason, <span key="n" className={t.net >= 0 ? "up" : "down"}>{fmt(t.net)}</span>])} />
      </Card>
      <Card title="Model decisions">
        <Table columns={["ID", "Model", "Trained through", "Beat baseline", "Promoted"]} empty="No models yet." rows={(models.data?.models ?? []).slice(0, 10).map((m: any) => [m.id, m.name, when(m.train_end), m.beats_baseline ? "Yes" : "No", m.promoted ? "Yes" : "No"])} />
      </Card>
      <Card title="Audit log">
        <div className="row">{audit.data?.chain_intact ? <Pill tone="ok">Hash chain intact</Pill> : <Pill tone="bad">Hash chain broken</Pill>}</div>
        <Table columns={["ID", "When", "Event", "Detail"]} empty="Nothing logged yet." rows={(audit.data?.rows ?? []).slice(0, 80).map((r: any) => [r.id, when(r.at), r.event, <span key="d" className="muted">{JSON.stringify(r.data).slice(0, 160)}</span>])} />
      </Card>
    </>
  );
}
