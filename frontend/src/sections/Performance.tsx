"use client";
import { fmt, pct, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Pill, Table, verdictText, verdictTone } from "@/components/ui";
import { ValueChart } from "@/components/PriceChart";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { EmptyState } from "@/components/ui";

function PnlHistogram({ values }: { values: number[] }) {
  if (values.length < 2) return <EmptyState text="Needs at least two closed paper trades." />;
  const lo = Math.min(...values), hi = Math.max(...values), n = Math.min(12, Math.max(4, Math.ceil(Math.sqrt(values.length))));
  const w = (hi - lo) / n || 1;
  const bins = Array.from({ length: n }, (_, i) => ({ from: lo + i * w, count: 0 }));
  values.forEach((v) => { bins[Math.min(n - 1, Math.floor((v - lo) / w))].count++; });
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer>
        <BarChart data={bins.map((b) => ({ label: fmt(b.from + w / 2, 0), count: b.count, mid: b.from + w / 2 }))}>
          <XAxis dataKey="label" stroke="#64748b" fontSize={11} tickLine={false} axisLine={{ stroke: "#1e1e2e" }} />
          <YAxis stroke="#64748b" fontSize={11} allowDecimals={false} tickLine={false} axisLine={false} width={28} />
          <Tooltip cursor={{ fill: "rgba(124,58,237,0.08)" }} contentStyle={{ background: "#111118", border: "1px solid #1e1e2e", borderRadius: 8 }} labelFormatter={(l) => `PnL near ${l}`} />
          <Bar dataKey="count" radius={[4, 4, 0, 0]}>{bins.map((b, i) => <Cell key={i} fill={b.from + w / 2 >= 0 ? "#22c55e" : "#ef4444"} />)}</Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

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
      <Card title="PnL distribution (paper trades)"><PnlHistogram values={(perf.data?.paper_trades ?? []).map((t: any) => t.net).filter((n: any) => typeof n === "number")} /></Card>
      <Card title="Trade log (paper)">
        <Table columns={["Closed", "Ticket", "Side", "Lots", "Entry", "Exit", "Reason", "Net"]} empty="No paper trades yet." rows={(perf.data?.paper_trades ?? []).slice(0, 50).map((t: any) => [when(t.closed_at), t.ticket, t.side > 0 ? "Long" : "Short", t.lots, fmt(t.entry), fmt(t.exit), t.reason, <span key="n" className={t.net >= 0 ? "up" : "down"}>{fmt(t.net)}</span>])} />
      </Card>
      <Card title="Model decisions">
        <Table columns={["ID", "Model", "Trained through", "Beat baseline", "Promoted"]} empty="No models yet." rows={(models.data?.models ?? []).slice(0, 10).map((m: any) => [m.id, m.name, when(m.train_end), m.beats_baseline ? "Yes" : "No", m.promoted ? "Yes" : "No"])} />
      </Card>
      <Card title="Audit log">
        <div className="row">{audit.data?.chain_intact ? <Pill tone="ok">Hash chain intact</Pill> : <Pill tone="bad">Hash chain broken</Pill>}</div>
        {(audit.data?.rows ?? []).length === 0 ? <div className="muted">Nothing logged yet.</div> : (
          <ol className="relative ml-2 space-y-3 border-l border-line pl-5">
            {(audit.data?.rows ?? []).slice(0, 40).map((r: any) => (
              <li key={r.id} className="relative">
                <span className="absolute -left-[26px] top-1.5 h-2.5 w-2.5 rounded-full bg-brand shadow-glow" />
                <div className="flex flex-wrap items-baseline gap-x-3"><b>{r.event}</b><span className="muted text-xs">#{r.id} · {when(r.at)}</span></div>
                <div className="muted text-xs break-all">{JSON.stringify(r.data).slice(0, 160)}</div>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </>
  );
}
