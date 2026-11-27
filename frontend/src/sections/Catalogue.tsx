"use client";
import { useMemo, useState } from "react";
import { api, fmt, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice, Pill, Stat, Table, verdictText, verdictTone } from "@/components/ui";

const LABELS: Record<string, string> = { ...verdictText, promising_needs_paper_trading: "Promising; paper trade next" };
const tone = (label?: string) => (label === "promising_needs_paper_trading" ? "ok" : verdictTone(label));
const STATUS_TONE: Record<string, "ok" | "warn" | "bad"> = { running: "ok", pending: "warn", queued: "warn", failed: "bad", done: "ok" };

/**
 * The strategy catalogue: documented, fixed-parameter strategies run through the same research as the 8 engines.
 * "Run" runs one next. Tick strategies (or "Queue all not yet run") to queue them, then "Run all queued" works through
 * the queue one at a time in the API's background worker, so it can run overnight with the page closed.
 */
export function Catalogue() {
  const cat = useFetch("/api/catalogue", 10000);
  const [family, setFamily] = useState("all");
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const report = useFetch(open ? `/api/catalogue/report/${open}` : null);
  const rows: any[] = useMemo(() => (cat.data?.strategies ?? []).filter((s: any) => family === "all" || s.family === family), [cat.data, family]);
  const families: string[] = useMemo(() => Array.from(new Set((cat.data?.strategies ?? []).map((s: any) => s.family))), [cat.data]);

  const post = async (path: string, body?: unknown) => {
    setError(null);
    try {
      await api(path, { method: "POST", body });
      await cat.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const queued = (cat.data?.strategies ?? []).filter((s: any) => s.queue?.status === "queued").length;
  const pending = (cat.data?.strategies ?? []).filter((s: any) => ["pending", "running"].includes(s.queue?.status)).length;
  const trials = cat.data?.trials;

  return (
    <>
      <h1>Strategy Catalogue</h1>
      <ErrorBox error={cat.error ?? error} />
      <Notice>
        Each strategy runs with fixed parameters through the same walk-forward split, untouched two-year holdout and stressed costs as the 8 engines. Every run is
        recorded and nothing is deleted. &quot;Promising&quot; needs 30+ out-of-sample trades, positive results out of sample, in the holdout and under stressed costs, and a
        deflated Sharpe above 0.95 counted against every variant tested ({trials ? `${trials.variants} so far` : "all of them"}). Promising is not a recommendation: paper trading comes next.
      </Notice>
      <div className="grid">
        <Stat label="Strategies" value={fmt(cat.data?.strategies?.length, 0)} />
        <Stat label="With results" value={fmt((cat.data?.strategies ?? []).filter((s: any) => s.report).length, 0)} />
        <Stat label="Promising" value={fmt((cat.data?.strategies ?? []).filter((s: any) => s.report?.catalogue_verdict?.promising).length, 0)} />
        <Stat label="Variants counted" value={fmt(trials?.variants, 0)} />
      </div>
      <Card title="Catalogue">
        <div className="row form-row">
          <label>Family <select value={family} onChange={(e) => setFamily(e.target.value)}><option value="all">All</option>{families.map((f) => <option key={f}>{f}</option>)}</select></label>
          <button className="btn" onClick={() => post("/api/catalogue/queue", { all_unrun: true })}>Queue all not yet run</button>
          <button className="btn primary" onClick={() => post("/api/catalogue/run-queued")} disabled={!queued}>Run all queued ({queued})</button>
          <span className="muted">{cat.data?.worker?.current ? `Running ${cat.data.worker.current}` : pending ? `${pending} waiting` : "Idle"}</span>
        </div>
        {cat.loading ? <Loading /> : (
          <Table columns={["", "Strategy", "Family", "TF", "Result", "OOS trades", "OOS Sharpe", "Deflated Sharpe (own)", "Deflated Sharpe (all trials)", "Holdout net", "Stressed net", "Status", ""]}
            rows={rows.map((s: any) => {
              const v = s.report?.catalogue_verdict;
              const status = s.queue?.status;
              return [
                <input key="q" type="checkbox" aria-label={`Queue ${s.name}`} checked={status === "queued"} disabled={status === "running" || status === "pending"}
                  onChange={(e) => post(e.target.checked ? "/api/catalogue/queue" : "/api/catalogue/dequeue", { names: [s.name] })} />,
                <button key="n" style={{ background: "none", border: 0, padding: 0, color: "inherit", textDecoration: "underline", cursor: "pointer", textAlign: "left" }} onClick={() => setOpen(s.name)} title={s.logic}>{s.title}{s.missing_data?.length ? " *" : ""}</button>,
                s.family, s.timeframe.toUpperCase(),
                v ? <Pill key="v" tone={tone(v.label)}>{LABELS[v.label] ?? v.label}</Pill> : <span key="v" className="muted">Not run</span>,
                fmt(v?.oos_trades, 0), fmt(v?.oos_sharpe), fmt(v?.deflated_sharpe_own), fmt(v?.deflated_sharpe_project), fmt(v?.holdout_net), fmt(v?.holdout_net_stress),
                status ? <Pill key="s" tone={STATUS_TONE[status] ?? "warn"}>{status}</Pill> : s.report ? <span key="s" className="muted">{when(s.report.created_at)}</span> : "–",
                <button key="r" className="btn" onClick={() => post(`/api/catalogue/run/${s.name}`)} disabled={status === "running" || status === "pending"}>Run</button>,
              ];
            })} />
        )}
        <div className="muted" style={{ marginTop: 8 }}>* needs data the project does not have; it cannot trade until that data exists. Click a name for its rules and latest report.</div>
      </Card>
      {open && <StrategyDetail name={open} entry={(cat.data?.strategies ?? []).find((s: any) => s.name === open)} report={report} onClose={() => setOpen(null)} />}
    </>
  );
}

function StrategyDetail({ name, entry, report, onClose }: { name: string; entry: any; report: any; onClose: () => void }) {
  const r = report.data;
  return (
    <Card title={entry?.title ?? name}>
      <div className="row"><button className="btn" onClick={onClose}>Close</button></div>
      {entry && (
        <dl className="space-y-1 text-sm">
          {([["Source", entry.source], ["Logic", entry.logic], ["Entry", entry.entry], ["Exit", entry.exit], ["Stop", entry.stop], ["Timeframe", entry.timeframe.toUpperCase()],
            ["Data", entry.data.join("; ")], ["Parameters (fixed)", JSON.stringify(entry.params)], ...(entry.missing_data?.length ? [["Needs data we don't have", entry.missing_data.join("; ")]] : [])] as [string, string][]).map(([k, v]) => (
            <div key={k}><dt className="label inline">{k}: </dt><dd className="inline">{v}</dd></div>
          ))}
        </dl>
      )}
      {report.loading ? <Loading /> : report.error ? <div className="muted">No report yet: run it above.</div> : r && (
        <>
          <h3>Verdict: {LABELS[r.catalogue_verdict?.label] ?? r.catalogue_verdict?.label}</h3>
          <Table columns={["Check", "Passed"]} rows={Object.entries(r.catalogue_verdict?.checks ?? {}).map(([k, ok]: any) => [k.replaceAll("_", " "), ok ? <Pill key="p" tone="ok">yes</Pill> : <Pill key="p" tone="bad">no</Pill>])} />
          <div className="muted">Project-wide deflation: {fmt(r.project_multiple_testing?.trials, 0)} variants counted, expected best daily Sharpe by luck {fmt(r.project_multiple_testing?.expected_max_sr, 4)}. Period {r.period?.first} to {r.period?.last}, holdout from {r.period?.holdout_start}.</div>
          <Table columns={["Holdout scenario", "Trades", "Net", "Sharpe", "Profit factor"]} rows={Object.entries(r.holdout?.scenarios ?? {}).map(([k, v]: any) => [k, fmt(v.trades, 0), fmt(v.net_profit), fmt(v.sharpe), fmt(v.profit_factor)])} />
          <Table columns={["Train from", "Test to", "Test trades", "Test net"]} rows={(r.walk_forward?.folds ?? []).map((f: any) => [f.train_start, f.test_end, f.skipped ? f.skipped : fmt(f.test_trades, 0), f.skipped ? "–" : fmt(f.test_net)])} />
        </>
      )}
    </Card>
  );
}
