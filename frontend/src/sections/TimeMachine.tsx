"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { API, api, fmt, pct, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Notice, Pill, Stat, Table } from "@/components/ui";
import { PriceChart, ValueChart, type Level, type Marker } from "@/components/PriceChart";

const GATES: Record<string, string> = { fomc_block: "Stand down near FOMC", scanner_filter: "Scanner: skip RSI extremes", wide_spread_block: "Stand down on wide spread" };

export function TimeMachine() {
  const strategies = useFetch("/api/research/strategies");
  const [form, setForm] = useState({ timeframe: "h1", start: "2024-01-01", end: "2024-07-01", execution: "m5", chosen: ["trend"], gates: [] as string[], use_ml: false, conflict: "stand_down", use_risk_engine: true });
  const [id, setId] = useState<string | null>(null);
  const [state, setState] = useState<any>(null);
  const [report, setReport] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [seekTo, setSeekTo] = useState("");
  const socket = useRef<WebSocket | null>(null);
  const [plan, setPlan] = useState({ entry: "", stop: "", target: "", entry_time: "" });
  const [odds, setOdds] = useState<any>(null);

  useEffect(() => {
    if (!id) return;
    const base = API || window.location.origin;
    const ws = new WebSocket(base.replace(/^http/, "ws") + `/ws/replay/${id}`);
    ws.onmessage = (m) => setState(JSON.parse(m.data));
    socket.current = ws;
    return () => ws.close();
  }, [id]);

  const start = async () => {
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      const out = await api("/api/replay", { method: "POST", body: { timeframe: form.timeframe, start: form.start, end: form.end, execution: form.execution || null, strategies: form.chosen, skills: form.gates, use_ml: form.use_ml, conflict: form.conflict, use_risk_engine: form.use_risk_engine } });
      setId(out.id);
      setState(out.state);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const control = async (action: string, extra: object = {}) => {
    if (!id) return;
    setError(null);
    try {
      setState(await api(`/api/replay/${id}/control`, { method: "POST", body: { action, ...extra } }));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const loadReport = async () => {
    try {
      setReport(await api(`/api/replay/${id}/report`));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const simulate = async () => {
    setError(null);
    setOdds(null);
    try {
      setOdds(await api("/api/outcome", { method: "POST", body: { entry: +plan.entry, stop: +plan.stop, target: +plan.target, entry_time: plan.entry_time } }));
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const toggle = (key: "chosen" | "gates", value: string) => setForm((f) => ({ ...f, [key]: f[key].includes(value) ? f[key].filter((v) => v !== value) : [...f[key], value] }));

  const candles = useMemo(() => (state?.candles ?? []).map((c: any) => ({ time: c.t, open: c.o, high: c.h, low: c.l, close: c.c })), [state]);
  const markers = useMemo(() => {
    const out: Marker[] = [];
    const first = state?.candles?.[0]?.t ?? 0;
    const snap = (t: number) => {
      const list = state?.candles ?? [];
      let hit = first;
      for (const c of list) {
        if (c.t <= t) hit = c.t;
        else break;
      }
      return hit;
    };
    for (const t of state?.trades ?? []) {
      if (t.entry_time >= first) out.push({ time: snap(t.entry_time), position: t.side > 0 ? "belowBar" : "aboveBar", color: "#5b9dd9", shape: t.side > 0 ? "arrowUp" : "arrowDown", text: "entry" });
      if (t.exit_time >= first) out.push({ time: snap(t.exit_time), position: t.side > 0 ? "aboveBar" : "belowBar", color: t.net >= 0 ? "#3fb68b" : "#e0605e", shape: "circle", text: t.reason });
    }
    for (const p of state?.positions ?? []) {
      if (p.entry_time >= first) out.push({ time: snap(p.entry_time), position: p.side > 0 ? "belowBar" : "aboveBar", color: "#5b9dd9", shape: p.side > 0 ? "arrowUp" : "arrowDown", text: "entry" });
    }
    return out;
  }, [state]);
  const levels = useMemo(() => {
    const out: Level[] = [];
    for (const p of state?.positions ?? []) {
      out.push({ price: p.entry, color: "#5b9dd9", title: "entry" });
      if (p.stop) out.push({ price: p.stop, color: "#e0605e", title: "stop" });
      if (p.target) out.push({ price: p.target, color: "#3fb68b", title: "target" });
    }
    return out;
  }, [state]);

  return (
    <>
      <h1>Time Machine</h1>
      <ErrorBox error={error ?? strategies.error} />
      <Card title="Experiment">
        <div className="row">
          <label>Signals <select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>{["s1", "m1", "m5", "m15", "m30", "h1", "h4", "d1"].map((t) => <option key={t}>{t}</option>)}</select></label>
          <label>Fills on <select value={form.execution} onChange={(e) => setForm({ ...form, execution: e.target.value })}><option value="tick">tick (where downloaded)</option><option value="m1">m1</option><option value="m5">m5</option><option value="">signal bars</option></select></label>
          <label>From <input type="date" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></label>
          <label>To <input type="date" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></label>
          <label>When strategies disagree <select value={form.conflict} onChange={(e) => setForm({ ...form, conflict: e.target.value })}><option value="stand_down">Stand down</option><option value="majority">Majority</option></select></label>
        </div>
        <div className="row"><span className="label">Strategies</span>{(strategies.data?.strategies ?? []).map((s: any) => <label className="check" key={s.name}><input type="checkbox" checked={form.chosen.includes(s.name)} onChange={() => toggle("chosen", s.name)} />{s.title}</label>)}</div>
        <div className="row"><span className="label">Skill gates</span>{Object.entries(GATES).map(([k, label]) => <label className="check" key={k}><input type="checkbox" checked={form.gates.includes(k)} onChange={() => toggle("gates", k)} />{label}</label>)}
          <label className="check"><input type="checkbox" checked={form.use_ml} onChange={(e) => setForm({ ...form, use_ml: e.target.checked })} />ML veto (promoted model only)</label>
          <label className="check"><input type="checkbox" checked={form.use_risk_engine} onChange={(e) => setForm({ ...form, use_risk_engine: e.target.checked })} />Risk engine</label></div>
        <button className="btn primary" onClick={start} disabled={busy || !form.chosen.length}>{busy ? "Preparing…" : "Load replay"}</button>
      </Card>
      {state && (
        <>
          <div className="row">
            <button className="btn primary" onClick={() => control("play")}>Play</button>
            <button className="btn" onClick={() => control("pause")}>Pause</button>
            <button className="btn" onClick={() => control("step", { value: 1 })}>Step 1</button>
            <button className="btn" onClick={() => control("step", { value: 50 })}>Step 50</button>
            <button className="btn" onClick={() => control("reset")}>Reset</button>
            <label>Speed <select value={state.speed} onChange={(e) => control("speed", { value: +e.target.value })}>{[1, 5, 20, 100, 500, 2000].map((n) => <option key={n}>{n}</option>)}</select></label>
            <input placeholder="2024-03-01 10:00" value={seekTo} onChange={(e) => setSeekTo(e.target.value)} />
            <button className="btn" onClick={() => seekTo && control("seek", { when: seekTo })}>Go to</button>
            <button className="btn" onClick={() => control("end")}>Run to end</button>
            <button className="btn" onClick={loadReport}>Results</button>
          </div>
          <div className="grid">
            <Stat label="Replay time" value={when(state.time)} />
            <Stat label="Progress" value={`${state.cursor} / ${state.total}${state.playing ? " ▶" : ""}`} />
            <Stat label="Bid / ask" value={`${fmt(state.bid)} / ${fmt(state.ask)}`} />
            <Stat label="Equity" value={fmt(state.equity)} />
          </div>
          <Card><PriceChart candles={candles} markers={markers} levels={levels} follow={state.playing} /></Card>
          <div className="grid wide">
            <Card title="Open simulated positions"><Table columns={["Side", "Lots", "Entry", "Stop", "Target"]} rows={state.positions.map((p: any) => [p.side > 0 ? "Long" : "Short", p.lots, fmt(p.entry), fmt(p.stop), fmt(p.target)])} /></Card>
            <Card title="Decision and event log"><div className="log">{[...state.events].reverse().map((e: any) => `${e.time.slice(0, 16)} ${e.event} ${e.reasons ?? e.reason ?? e.reasons ?? ""} ${e.price ? `@${e.price.toFixed?.(2)}` : ""}`).join("\n")}</div></Card>
          </div>
        </>
      )}
      {report && (
        <>
          <Card title="Results of this replay">
            <Table columns={["Trades", "Net", "Win rate", "Profit factor", "Sharpe", "Max drawdown", "Digest"]} rows={[[fmt(report.report.trades, 0), fmt(report.report.net_profit), pct(report.report.win_rate, 1), fmt(report.report.profit_factor), fmt(report.report.sharpe), pct(report.report.max_drawdown), <code key="d">{report.record.digest.slice(0, 12)}</code>]]} />
            {report.equity?.length > 1 && <ValueChart points={report.equity} />}
          </Card>
          <Card title="Decision reconstruction">
            <Table columns={["Time", "Action", "Votes", "Reasons"]} empty="No decisions in the replayed period." rows={[...report.decisions].reverse().slice(0, 40).map((d: any) => [when(d.time), d.action, d.votes.map((v: any) => `${v.source} ${v.side > 0 ? "long" : "short"}`).join(", "), d.reasons.join("; ")])} />
          </Card>
        </>
      )}
      <Card title="Outcome Simulator (the original Time Machine's method)">
        <div className="row">
          {(["entry", "stop", "target"] as const).map((k) => <label key={k}>{k} <input type="number" step="0.01" value={plan[k]} onChange={(e) => setPlan({ ...plan, [k]: e.target.value })} style={{ width: 100 }} /></label>)}
          <label>Entry time (UTC) <input placeholder="2024-03-01 14:00" value={plan.entry_time} onChange={(e) => setPlan({ ...plan, entry_time: e.target.value })} /></label>
          <button className="btn" onClick={simulate} disabled={!plan.entry || !plan.stop || !plan.target || !plan.entry_time}>Simulate</button>
        </div>
        {odds && (
          <>
            <Table columns={["Target first", "Stop first", "Neither", "Average R", "After spread", "Pool", "Seed"]} rows={[[pct(odds.mc.p_target, 1), pct(odds.mc.p_stop, 1), pct(odds.mc.p_neither, 1), fmt(odds.mc.avg_R, 3), fmt(odds.avg_R_after_spread, 3), `${odds.pool_sessions} sessions`, odds.mc.seed]]} />
            <p><b>{odds.verdict}</b> {odds.verdict_notes.join(" ")}</p>
            <div className="muted">History: fired {odds.history.triggered} times, {odds.history.target} target, {odds.history.stop} stop, {odds.history.close} neither. {odds.note}</div>
          </>
        )}
      </Card>
      <Notice>Every view shows only what was knowable at the replay time. A look-ahead integrity test guards this. Replays are simulations and send no orders.</Notice>
    </>
  );
}
