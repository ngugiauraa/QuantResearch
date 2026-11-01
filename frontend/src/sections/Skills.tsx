"use client";
import { useState } from "react";
import { api, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice, Pill, Table } from "@/components/ui";

const EXAMPLES: Record<string, { action: string; args?: unknown[]; kwargs?: object; label: string }[]> = {
  scanner: [{ action: "timeframes", kwargs: { frames: ["d1ny", "h4", "h1"] }, label: "Scan gold across timeframes" }],
  entry: [{ action: "check", kwargs: { session: "new_york_comex", timeframe: "m5" }, label: "Check today's New York opening range" }],
  exit: [{ action: "plan", args: [2000], kwargs: { stop_rule: "pct", stop_param: 1, target_r: 2 }, label: "Example plan: 2000, 1% stop, 2R" }],
  budget: [{ action: "size", args: [{ entry: 2000, risk_per_oz: 10 }, 100000, 500], kwargs: { leverage: 20 }, label: "Example size: $500 max loss" }],
  alerts: [{ action: "from_plan", args: [{ side: 1, stop: 1990, target: 2030 }, "2026-11-02"], kwargs: { flat_by: "16:55" }, label: "Alerts from an example plan" }],
  analyst: [{ action: "tool", args: [["NEM", "--quarters", "4"]], label: "Newmont filings (needs SEC identity)" }],
  copycat: [{ action: "tool", args: [["find", "paulson"]], label: "Find a filer" }],
  risk: [{ action: "tool", args: [["--help"]], label: "Show the original tool's options" }],
  broker: [{ action: "tool", args: [["--help"]], label: "Show the original tool's options" }],
  news: [{ action: "tool", args: [["--help"]], label: "Show the original tool's options" }],
  screener: [{ action: "screen", args: [[{ ticker: "trend", sharpe: 1.2, trades: 200 }, { ticker: "seasonal", sharpe: 0.1, trades: 500 }], { name: "Example", rules: [{ metric: "sharpe", op: ">=", value: 1 }], missing: "fail" }], label: "Screen example strategy metrics" }],
};

export function Skills() {
  const list = useFetch("/api/skills");
  const [key, setKey] = useState("scanner");
  const detail = useFetch(`/api/skills/${key}`);
  const [output, setOutput] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showDoc, setShowDoc] = useState(false);

  const run = async (example: (typeof EXAMPLES)[string][number]) => {
    setBusy(true);
    setError(null);
    setOutput(null);
    try {
      setOutput(await api(`/api/skills/${key}/run`, { method: "POST", body: { action: example.action, args: example.args ?? [], kwargs: example.kwargs ?? {} } }));
      await detail.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const d = detail.data;
  return (
    <>
      <h1>Trading Skills</h1>
      <ErrorBox error={list.error ?? detail.error ?? error} />
      {list.loading ? <Loading /> : (
        <Card title="The eleven skills">
          <Table columns={["Skill", "Job", "Status", "Source", ""]} rows={(list.data?.skills ?? []).map((s: any) => [s.name, s.job, s.ready ? <Pill key="r" tone="ok">Ready</Pill> : <Pill key="r" tone="warn">Needs {s.missing.join(", ")}</Pill>, <span key="u" className="muted">{s.upstream_version}</span>,
            <button key="b" className={`btn ${s.key === key ? "primary" : ""}`} onClick={() => { setKey(s.key); setOutput(null); setShowDoc(false); }}>Open</button>])} />
        </Card>
      )}
      {d && (
        <>
          <Card title={d.name}>
            <div className="grid wide" style={{ marginBottom: 0 }}>
              <div><div className="label">Kept from the original</div><ul className="plain">{d.kept.map((x: string) => <li key={x}>{x}</li>)}</ul></div>
              <div><div className="label">Adapted for XAUUSD</div><ul className="plain">{d.adapted.map((x: string) => <li key={x}>{x}</li>)}</ul></div>
              <div><div className="label">Not available</div><ul className="plain">{d.unavailable.map((x: string) => <li key={x}>{x}</li>)}</ul></div>
            </div>
          </Card>
          {!d.ready && <Notice>This skill needs {d.missing.join(", ")}. Set SEC_USER_AGENT to your own name and email before starting the server. SEC asks for this, and it is never invented.</Notice>}
          <Card title="Run">
            <div className="row">
              {(EXAMPLES[key] ?? []).map((e) => <button className="btn" key={e.label} disabled={busy} onClick={() => run(e)}>{e.label}</button>)}
              <button className="btn" onClick={() => setShowDoc(!showDoc)}>{showDoc ? "Hide" : "Show"} original instructions</button>
            </div>
            {output && <div className="log">{typeof output.output?.stdout === "string" ? output.output.stdout || output.output.stderr : JSON.stringify(output.status === "refused" ? output : output.output, null, 1)}</div>}
            {showDoc && <div className="log" style={{ maxHeight: 500, marginTop: 10 }}>{d.instructions}</div>}
          </Card>
          <Card title="Run log">
            <Table columns={["When", "Action", "Status"]} empty="No runs yet." rows={(d.runs ?? []).slice(0, 15).map((r: any) => [when(r.at), r.action, r.status])} />
          </Card>
        </>
      )}
    </>
  );
}
