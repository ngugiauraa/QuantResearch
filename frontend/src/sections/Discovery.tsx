"use client";
import { useEffect, useState } from "react";
import { fmt, pct, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice, Pill, Stat, Table } from "@/components/ui";
import { ValueChart } from "@/components/PriceChart";

const GROUPS = ["Experimental", "Screened", "Statistically validated", "Qualified", "Paper-trading"] as const;
const STAGE_ORDER = ["experimental", "screened", "selected", "walk_forward", "statistically_validated", "robust", "qualified", "paper_trading"];

const tone = (stage?: string): "ok" | "warn" | "bad" | undefined =>
  stage === "qualified" || stage === "paper_trading" ? "ok" : stage === "statistically_validated" || stage === "robust" ? "warn" : undefined;

const hours = (s?: number) => (typeof s === "number" ? `${(s / 3600).toFixed(1)} h` : "–");

function Histogram({ real, nul }: { real?: any; nul?: any }) {
  if (!real?.n && !nul?.n) return <div className="muted">No out-of-sample results yet.</div>;
  const edges: number[] = real?.edges ?? nul?.edges ?? [];
  const max = Math.max(1, ...(real?.histogram ?? []), ...(nul?.histogram ?? []));
  const bins = (real?.histogram ?? nul?.histogram ?? []).map((_: number, i: number) => i);
  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 120 }} aria-label="Out-of-sample Sharpe distribution">
        {bins.map((i: number) => (
          <div key={i} style={{ flex: 1, display: "flex", gap: 1, alignItems: "flex-end", height: "100%" }} title={`${edges[i]?.toFixed(2)} to ${edges[i + 1]?.toFixed(2)}`}>
            <div style={{ flex: 1, background: "var(--accent)", height: `${((real?.histogram?.[i] ?? 0) / max) * 100}%` }} />
            <div style={{ flex: 1, background: "var(--muted)", opacity: 0.6, height: `${((nul?.histogram?.[i] ?? 0) / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="row muted" style={{ justifyContent: "space-between", marginTop: 4 }}><span>Sharpe −3</span><span>0</span><span>+3</span></div>
      <div className="muted"><span style={{ color: "var(--accent)" }}>■</span> real data ({real?.n ?? 0}, median {fmt(real?.median)}) <span style={{ marginLeft: 10 }}>■</span> null market ({nul?.n ?? 0}, median {fmt(nul?.median)}). If the two look alike, the search is finding noise.</div>
    </div>
  );
}

function CandidateDetail({ cid, run, onClose }: { cid: string; run: string; onClose: () => void }) {
  const d = useFetch(`/api/discovery/candidate/${cid}?run=${run}`);
  if (d.loading) return <Card title="Candidate"><Loading /></Card>;
  if (d.error) return <Card title="Candidate"><ErrorBox error={d.error} /></Card>;
  const c = d.data;
  const o = c.out_of_sample ?? {};
  const tests = c.robustness_and_adversarial?.tests ?? {};
  return (
    <Card title={`Candidate ${c.cid}`}>
      <div className="row"><Pill tone={tone(c.stage)}>{c.stage_label}</Pill> <span className="muted">{c.timeframe.toUpperCase()}, frozen at {c.vintage_cutoff}, from {c.origin}{c.operator ? ` (${c.operator})` : ""}, complexity {c.complexity}</span> <button className="btn" onClick={onClose}>Close</button></div>
      <Notice>{c.not_proven_note}</Notice>
      <div className="label">Logic</div>
      <p><code>{c.logic}</code></p>
      {c.rejected_at && <p className="warn">Stopped at {c.rejected_at}: {c.reject_reason}</p>}
      <div className="grid">
        <Stat label="OOS trades" value={fmt(o.trades, 0)} />
        <Stat label="OOS expectancy (R)" value={fmt(o.exp_r, 3)} tone={(o.exp_r ?? 0) > 0 ? "up" : "down"} />
        <Stat label="OOS Sharpe" value={fmt(o.sharpe)} />
        <Stat label="Max drawdown (fraction of equity)" value={pct(o.max_dd)} />
        <Stat label="Positive years" value={pct(o.positive_year_share, 0)} />
        <Stat label="Corr. with existing engines" value={fmt(c.max_abs_correlation_with_existing_engines)} />
      </div>
      {c.equity_r?.length > 1 && <><div className="label">Out-of-sample cumulative return (fraction of equity, 0.5 % risk per trade)</div><ValueChart points={c.equity_r} height={200} /></>}
      <div className="grid wide" style={{ marginTop: 12 }}>
        <div>
          <div className="label">Walk-forward folds (calendar years, frozen rule)</div>
          <Table columns={["Year", "Trades", "Total R", "Exp R"]} rows={(c.folds ?? []).map((f: any) => [f.fold, fmt(f.trades, 0), fmt(f.total_r), fmt(f.exp_r, 3)])} empty="Not evaluated out of sample." />
        </div>
        <div>
          <div className="label">Long and short</div>
          <Table columns={["Side", "Trades", "Exp R", "Total R"]} rows={Object.entries(o.sides ?? {}).map(([k, v]: any) => [k, fmt(v.trades, 0), fmt(v.exp_r, 3), fmt(v.total_r)])} />
          <div className="label" style={{ marginTop: 8 }}>Training windows (search and selection)</div>
          <Table columns={["Window", "Trades", "Exp R", "Sharpe"]} rows={[["Inner train", c.training?.inner_train], ["Inner validation", c.training?.inner_validation]].map(([k, v]: any) => [k, fmt(v?.trades, 0), fmt(v?.exp_r, 3), fmt(v?.sharpe)])} />
        </div>
      </div>
      {c.statistics && (
        <>
          <div className="label">Statistical validation (accounts for {fmt(c.statistics.oos_looks, 0)} out-of-sample looks)</div>
          <Table columns={["NW t", "p (BH)", "Deflated Sharpe", "Null q95 Sharpe", "PBO", "Sharpe 95 % CI", "Result"]} rows={[[fmt(c.statistics.t_nw), fmt(c.statistics.p_bh, 4), fmt(c.statistics.dsr, 3), fmt(c.statistics.null_sharpe_q95), fmt(c.statistics.pbo_group), c.statistics.sharpe_ci95 ? `${fmt(c.statistics.sharpe_ci95[0])} to ${fmt(c.statistics.sharpe_ci95[1])}` : "–",
            c.statistics.passed ? <Pill key="p" tone="ok">Passed</Pill> : <span key="p" className="warn">{(c.statistics.reasons ?? []).join(", ")}</span>]]} />
        </>
      )}
      {Object.keys(tests).length > 0 && (
        <>
          <div className="label" style={{ marginTop: 8 }}>Robustness and adversarial tests</div>
          <Table columns={["Test", "Stage", "Result", "Detail"]} rows={Object.entries(tests).map(([k, t]: any) => [k.replaceAll("_", " "), t.stage === 5 ? "adversarial" : "robustness",
            t.passed === null ? <span key="r" className="muted">info</span> : t.passed ? <Pill key="r" tone="ok">pass</Pill> : <Pill key="r" tone="bad">fail</Pill>,
            <span key="d" className="muted">{t.note} {t.value && typeof t.value === "object" ? JSON.stringify(t.value).slice(0, 160) : ""}</span>])} />
        </>
      )}
      {c.engine_confirmation && <><div className="label" style={{ marginTop: 8 }}>Event-engine confirmation</div><pre className="log">{JSON.stringify(c.engine_confirmation, null, 1)}</pre></>}
      <div className="label" style={{ marginTop: 8 }}>Features used</div>
      <Table columns={["Feature", "Family", "Known at", "Quality", "Definition"]} rows={(c.features ?? []).map((f: any) => [f.name, f.family, f.availability, f.quality, f.definition])} />
      <div className="label" style={{ marginTop: 8 }}>Ancestry</div>
      <Table columns={["Generation back", "Spec hash", "Origin", "Operator"]} rows={(c.ancestry ?? []).map((a: any) => [a.level, a.spec_hash, a.origin ?? a.note, a.operator ?? ""])} empty="No parents: generated directly." />
      <details style={{ marginTop: 8 }}><summary>Complete strategy definition (JSON)</summary><pre className="log">{JSON.stringify(c.definition, null, 1)}</pre></details>
    </Card>
  );
}

export function Discovery() {
  const status = useFetch("/api/discovery/status", 15000);
  const run: string | null = status.data?.run_id ?? null;
  const dist = useFetch(run ? `/api/discovery/distribution?run=${run}` : null, 60000);
  const [group, setGroup] = useState<string>("Screened");
  const cands = useFetch(run ? `/api/discovery/candidates?run=${run}&group=${encodeURIComponent(group)}&limit=60` : null, 30000);
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => setSelected(null), [group]);

  if (status.loading) return <><h1>Strategy Discovery</h1><Loading /></>;
  const s = status.data;
  if (!s?.run_id) return <><h1>Strategy Discovery</h1><ErrorBox error={status.error} /><Notice>{s?.note ?? "No discovery run yet."}</Notice></>;
  const p = s.progress ?? {};
  const counts = p.counts ?? {};
  const last = s.resources?.[s.resources.length - 1] ?? {};
  const rounds = (s.rounds ?? []).filter((r: any) => r.ended_at).map((r: any) => ({ time: Math.floor(new Date(r.ended_at).getTime() / 1000), value: r.cumulative }));
  const cpu = (s.resources ?? []).map((r: any) => ({ time: Math.floor(new Date(r.ts).getTime() / 1000), value: r.cpu_pct ?? 0 }));
  const mem = (s.resources ?? []).map((r: any) => ({ time: Math.floor(new Date(r.ts).getTime() / 1000), value: (r.sys_avail_mb ?? 0) / 1000 }));
  return (
    <>
      <h1>Strategy Discovery</h1>
      <ErrorBox error={status.error} />
      <Notice>Research only. Nothing here can place an order. A candidate is a hypothesis until it passes every gate, and even a qualified candidate may only be paper traded. {s.holdout_note}</Notice>
      <div className="grid">
        <Stat label="Run" value={<span>{s.run_id} <Pill tone={s.status === "running" ? "warn" : undefined}>{s.status}</Pill></span>} />
        <Stat label="Elapsed / limit" value={`${hours(p.elapsed_s)} / ${fmt(p.max_hours, 0)} h`} />
        <Stat label="Experiments" value={fmt(counts.experiments, 0)} />
        <Stat label="Hypotheses generated" value={fmt(counts.hypotheses, 0)} />
        <Stat label="Candidates rejected (real)" value={fmt(s.rejected, 0)} />
        <Stat label="Distinct strategy families" value={fmt(s.families, 0)} />
        <Stat label="Current stage" value={<span style={{ fontSize: 15 }}>{p.current_stage ?? "–"} (round {fmt(p.round, 0)})</span>} />
        <Stat label="CPU / free memory" value={`${fmt(last.cpu_pct, 0)} % / ${fmt((last.sys_avail_mb ?? 0) / 1000, 1)} GB`} />
        <Stat label="Last checkpoint" value={<span style={{ fontSize: 15 }}>{s.checkpoint ? `round ${s.checkpoint.round}, ${when(s.checkpoint.created_at)}` : "none"} · session {s.sessions}</span>} />
        <Stat label="Final qualification" value={<span style={{ fontSize: 15 }}>{s.qualification}</span>} tone={s.qualification?.startsWith("No") ? "warn" : "up"} />
      </div>
      {s.stop_reason && <Notice>Stopped: {s.stop_reason}</Notice>}
      <div className="grid wide">
        <Card title="Candidates surviving each stage">
          <Table columns={["Stage", "Real data", "Null market (false-positive check)"]} rows={STAGE_ORDER.map((k) => [s.stage_labels?.[k] ?? k, fmt(s.reached?.real?.[k] ?? 0, 0), fmt(s.reached?.null?.[k] ?? 0, 0)])} />
          <div className="muted" style={{ marginTop: 6 }}>Counts reached at least that stage. The null market has no possible edge, so anything surviving there shows how often the pipeline is fooled.</div>
        </Card>
        <Card title="Out-of-sample performance distribution">
          {dist.loading ? <Loading /> : <Histogram real={dist.data?.oos_sharpe?.real} nul={dist.data?.oos_sharpe?.null} />}
        </Card>
      </div>
      <div className="grid wide">
        <Card title="Search progress (cumulative experiments)">{rounds.length > 1 ? <ValueChart points={rounds} height={200} /> : <div className="muted">Waiting for rounds.</div>}</Card>
        <Card title="Resources: CPU % and free memory (GB)">
          {cpu.length > 1 ? <><ValueChart points={cpu} height={95} /><ValueChart points={mem} color="#3fb68b" height={95} /></> : <div className="muted">Waiting for samples.</div>}
        </Card>
      </div>
      <div className="grid wide">
        <Card title="Walk-forward folds across frozen candidates (real)">
          <Table columns={["Year", "Candidates", "Mean exp R", "Positive"]} rows={(dist.data?.folds ?? []).map((f: any) => [f.fold, fmt(f.candidates, 0), fmt(f.mean_exp_r, 3), fmt(f.positive, 0)])} empty="No out-of-sample folds yet." />
        </Card>
        <Card title="Search methods">
          <Table columns={["Method", "Data", "Tasks", "Evaluated", "Frozen"]} rows={(dist.data?.methods ?? []).map((m: any) => [m.kind, m.dataset, fmt(m.tasks, 0), fmt(m.evaluated, 0), fmt(m.selected, 0)])} />
        </Card>
      </div>
      <Card title="Candidates">
        <div className="row" role="tablist">
          {GROUPS.map((g) => <button key={g} className={`btn ${group === g ? "primary" : ""}`} onClick={() => setGroup(g)}>{g} ({fmt(s.groups?.[g] ?? 0, 0)})</button>)}
        </div>
        {cands.loading ? <Loading /> : (
          <Table columns={["Stage", "TF", "Frozen", "Logic", "OOS trades", "OOS exp R", "OOS Sharpe", "DSR", ""]} empty={`No ${group.toLowerCase()} candidates.`}
            rows={(cands.data?.candidates ?? []).map((c: any) => [<Pill key="s" tone={tone(c.stage)}>{c.stage_label}</Pill>, c.tf.toUpperCase(), c.vintage, <code key="l" style={{ fontSize: 12 }}>{c.logic.slice(0, 140)}</code>,
              fmt(c.oos_trades, 0), fmt(c.oos_exp_r, 3), fmt(c.oos_sharpe), fmt(c.dsr, 3), <button key="b" className="btn" onClick={() => setSelected(c.cid)}>Inspect</button>])} />
        )}
        {cands.data && <div className="muted" style={{ marginTop: 6 }}>Showing {cands.data.candidates.length} of {fmt(cands.data.total, 0)}, ordered by stage, then score.</div>}
      </Card>
      {selected && run && <CandidateDetail cid={selected} run={run} onClose={() => setSelected(null)} />}
      <Card title="Research log">
        <div className="log">{(s.events ?? []).map((e: any) => `${e.ts.slice(0, 19)} [${e.level}] ${e.message}`).join("\n")}</div>
      </Card>
    </>
  );
}
