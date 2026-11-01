"use client";
import { useState } from "react";
import { api, fmt, pct, useFetch, waitForJob, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Pill, Stat, Table, verdictText, verdictTone } from "@/components/ui";
import { ValueChart } from "@/components/PriceChart";

export function Research() {
  const strategies = useFetch("/api/research/strategies");
  const experiments = useFetch("/api/research/experiments");
  const analysis = useFetch("/api/research/analysis?days=730");
  const [selected, setSelected] = useState("mean_reversion");
  const report = useFetch(`/api/research/report/${selected}`);
  const [job, setJob] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any>(null);

  const run = async () => {
    setRunError(null);
    setJob("Running. A full walk-forward can take several minutes.");
    try {
      const { job: id } = await api(`/api/research/run/${selected}`, { method: "POST" });
      const done = await waitForJob(id);
      if (done.status === "failed") setRunError(done.error);
      await Promise.all([strategies.reload(), report.reload(), experiments.reload()]);
    } catch (e) {
      setRunError((e as Error).message);
    } finally {
      setJob(null);
    }
  };
  const search = async () => {
    if (!query.trim()) return;
    try {
      setResults(await api(`/api/research/search?q=${encodeURIComponent(query)}`));
    } catch (e) {
      setRunError((e as Error).message);
    }
  };
  const r = report.data;
  const oos = r?.walk_forward?.out_of_sample;
  return (
    <>
      <h1>Quantitative Research</h1>
      <ErrorBox error={runError ?? strategies.error} />
      <Card title="Seven strategy engines">
        {strategies.loading ? <Loading /> : (
          <Table columns={["Strategy", "Timeframe", "Result", "OOS trades", "OOS Sharpe", "Deflated Sharpe", ""]} rows={(strategies.data?.strategies ?? []).map((s: any) => [
            s.title, s.timeframe.toUpperCase(),
            s.verdict ? <Pill key="v" tone={verdictTone(s.verdict.label)}>{verdictText[s.verdict.label] ?? s.verdict.label}</Pill> : <span className="muted">Not run</span>,
            fmt(s.verdict?.oos_trades, 0), fmt(s.verdict?.oos_sharpe), fmt(s.verdict?.deflated_sharpe),
            <button key="b" className={`btn ${selected === s.name ? "primary" : ""}`} onClick={() => setSelected(s.name)}>View</button>])} />
        )}
        <div className="muted" style={{ marginTop: 8 }}>A good result does not qualify a strategy for live trading. A negative result from a sound test is a valid outcome.</div>
      </Card>
      <div className="row">
        <button className="btn primary" onClick={run} disabled={!!job}>Run full research for {selected}</button>
        {job && <span className="warn">{job}</span>}
      </div>
      {report.error && <div className="muted">{report.error}.</div>}
      {r && (
        <>
          <Card title={r.title}>
            <p>{r.methodology}</p>
            <div className="label">Limitations</div>
            <ul className="plain">{r.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
            <div className="muted">Dataset {r.dataset_version}. Data {when(r.period.first)} to {when(r.period.last)}. Holdout from {r.period.holdout_start}. {r.walk_forward.trial_count} variants tried across {r.walk_forward.fold_count} folds.</div>
          </Card>
          <div className="grid">
            <Stat label="Out-of-sample net" value={fmt(oos?.net_profit)} tone={(oos?.net_profit ?? 0) > 0 ? "up" : "down"} />
            <Stat label="Profit factor" value={fmt(oos?.profit_factor)} />
            <Stat label="Expectancy per trade" value={fmt(oos?.expectancy)} />
            <Stat label="Max drawdown" value={pct(oos?.max_drawdown)} />
            <Stat label="Win rate" value={pct(oos?.win_rate, 1)} />
            <Stat label="Probability of positive Sharpe" value={pct(r.walk_forward.psr, 1)} />
          </div>
          <Card title="Hypothesis tests (before holdout, FDR corrected)">
            <Table columns={["ID", "Statement", "Events", "Excess mean", "p", "Adjusted p", "Result"]} rows={r.hypotheses.map((h: any) => [h.id, h.statement, fmt(h.events, 0), h.tested ? h.excess_mean.toExponential(2) : "–", fmt(h.p_one_sided, 4), fmt(h.p_bh, 4),
              h.tested ? (h.significant_fdr5 ? <Pill key="p" tone="ok">Significant</Pill> : <Pill key="p">Not significant</Pill>) : <span key="p" className="muted">{h.reason}</span>])} />
            <div className="muted" style={{ marginTop: 8 }}>Statistical significance is not profitability: these do not include costs.</div>
          </Card>
          <Card title="Holdout under cost scenarios">
            <Table columns={["Scenario", "Trades", "Net", "Sharpe", "Max drawdown", "Profit factor"]} rows={Object.entries(r.holdout.scenarios).map(([k, v]: any) => [k, fmt(v.trades, 0), fmt(v.net_profit), fmt(v.sharpe), pct(v.max_drawdown), fmt(v.profit_factor)])} />
          </Card>
          <Card title="Walk-forward folds">
            <Table columns={["Train from", "Train to", "Test to", "Train Sharpe", "Test trades", "Test net", "Selected"]} rows={r.walk_forward.folds.map((f: any) => f.skipped ? [f.train_start, "–", f.test_end, "–", "–", "–", f.skipped] : [f.train_start, f.train_end, f.test_end, fmt(f.train_sharpe), fmt(f.test_trades, 0), fmt(f.test_net), JSON.stringify(f.params)])} />
          </Card>
          <Card title="Parameter sensitivity (mean training Sharpe)">
            <Table columns={["Parameters", "Mean", "Min", "Max"]} rows={Object.entries(r.walk_forward.parameter_sensitivity ?? {}).map(([k, v]: any) => [k, fmt(v.mean_train_sharpe), fmt(v.min), fmt(v.max)])} />
          </Card>
        </>
      )}
      <Card title="Return statistics, H1, last two years">
        {analysis.loading ? <Loading /> : analysis.error ? <div className="muted">{analysis.error}</div> : (
          <>
            <Table columns={["Bars", "Mean", "Std", "Skew", "Excess kurtosis", "|Return| autocorrelation", "Price stationarity", "Return stationarity", "Coint. p with silver"]} rows={[[fmt(analysis.data?.bars, 0), analysis.data?.returns?.mean?.toExponential(2), fmt(analysis.data?.returns?.std, 5), fmt(analysis.data?.returns?.skew), fmt(analysis.data?.returns?.excess_kurtosis), fmt(analysis.data?.returns?.abs_autocorr_1, 3), analysis.data?.stationarity_price?.verdict, analysis.data?.stationarity_returns?.verdict, fmt(analysis.data?.cointegration?.p, 3)]]} />
            {analysis.data?.rolling_corr?.length > 0 && <><div className="label" style={{ marginTop: 10 }}>Rolling 10-day correlation with silver</div><ValueChart points={analysis.data.rolling_corr} /></>}
          </>
        )}
      </Card>
      <Card title="Research search">
        <div className="row">
          <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} placeholder="Search research, reports and documentation" style={{ flex: 1, minWidth: 220 }} />
          <button className="btn" onClick={search}>Search</button>
          <button className="btn" onClick={() => api("/api/research/reindex", { method: "POST" }).then(search)}>Rebuild index</button>
        </div>
        {results && (results.found === 0 ? <div className="muted">{results.note}</div> : Object.entries(results.evidence).map(([kind, items]: any) => (
          <div key={kind} style={{ marginBottom: 10 }}>
            <Pill>{kind.replaceAll("_", " ")}</Pill>
            {items.map((e: any, i: number) => <div key={i} style={{ margin: "6px 0" }}><b>{e.title}</b> <span className="muted">({e.source}{e.published_at ? `, ${when(e.published_at)}` : ""})</span><div className="muted">{e.snippet}</div></div>)}
          </div>
        )))}
      </Card>
      <Card title="Experiment ledger">
        <Table columns={["ID", "When", "Strategy", "Dataset", "Result"]} empty="No experiments yet." rows={(experiments.data?.experiments ?? []).slice(0, 25).map((e: any) => [e.id, when(e.created_at), e.strategy, e.dataset_version, JSON.parse(e.verdict).label])} />
      </Card>
    </>
  );
}
