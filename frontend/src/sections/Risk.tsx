"use client";
import { useEffect, useState } from "react";
import { api, fmt, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Pill, Stat, Table } from "@/components/ui";

export function Risk() {
  const { data, error, loading, reload } = useFetch("/api/risk", 8000);
  const [limits, setLimits] = useState<Record<string, number | boolean> | null>(null);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [sizing, setSizing] = useState({ entry: "2000", risk: "10", equity: "100000", max_loss: "500" });
  const [sizeResult, setSizeResult] = useState<any>(null);

  useEffect(() => {
    if (data && !dirty) setLimits(data.limits);
  }, [data, dirty]);

  const act = async (fn: () => Promise<any>, done?: string) => {
    setActionError(null);
    setMessage(null);
    try {
      await fn();
      if (done) setMessage(done);
      await reload();
    } catch (e) {
      setActionError((e as Error).message);
    }
  };
  if (loading && !data) return <Loading />;
  const s = data?.snapshot ?? {};
  const x = data?.exposure ?? {};
  return (
    <>
      <h1>Risk Management</h1>
      <ErrorBox error={error ?? actionError ?? data?.error ?? null} />
      {message && <div className="notice">{message}</div>}
      <div className="grid">
        <Stat label="Equity" value={fmt(x.equity)} />
        <Stat label="Daily loss" value={`${fmt(s.daily_loss_pct)}%`} tone={(s.daily_loss_pct ?? 0) > 0 ? "warn" : undefined} />
        <Stat label="Drawdown" value={`${fmt(s.drawdown_pct)}%`} />
        <Stat label="Open lots" value={fmt(x.open_lots)} />
        <Stat label="Net lots" value={fmt(x.net_lots)} />
        <Stat label="Entries today" value={fmt(s.trades_today, 0)} />
      </div>
      <Card title="Emergency stop">
        <div className="row">
          {data?.emergency_stop ? <Pill tone="bad">ACTIVE: {data.emergency_reason}</Pill> : <Pill tone="ok">Not active</Pill>}
          <button className="btn danger" onClick={() => act(() => api("/api/risk/emergency-stop", { method: "POST", body: { flatten: true, reason: "dashboard" } }), "Emergency stop engaged. New entries are blocked and positions were closed.")}>EMERGENCY STOP and close all</button>
          <button className="btn" onClick={() => act(() => api("/api/risk/emergency-stop", { method: "POST", body: { flatten: false, reason: "dashboard, positions kept" } }), "New entries are blocked. Positions were kept.")}>Block entries only</button>
          {data?.emergency_stop && <button className="btn" onClick={() => act(() => api("/api/risk/emergency-clear", { method: "POST" }), "Emergency stop cleared.")}>Clear</button>}
        </div>
        <div className="row">
          {data?.lockout_until ? <><Pill tone="bad">Locked out until {when(data.lockout_until)}: {data.lockout_reason}</Pill><button className="btn" onClick={() => act(() => api("/api/risk/clear-lockout", { method: "POST" }), "Lockout cleared.")}>Clear lockout</button></> : <Pill tone="ok">No lockout</Pill>}
        </div>
        <div className="muted">Closing a position is always allowed, even during an emergency stop.</div>
      </Card>
      <Card title="Limits">
        {limits && (
          <>
            <div className="grid">
              {Object.entries(limits).map(([k, v]) => (
                <label key={k} style={{ display: "block" }}>
                  <div className="label">{k.replaceAll("_", " ")}</div>
                  {typeof v === "boolean" ? <input type="checkbox" checked={v} onChange={(e) => { setLimits({ ...limits, [k]: e.target.checked }); setDirty(true); }} />
                    : <input aria-label={k} type="number" step="any" min="0" value={v as number} onChange={(e) => { setLimits({ ...limits, [k]: +e.target.value }); setDirty(true); }} style={{ width: "100%" }} />}
                </label>
              ))}
            </div>
            <div className="row" style={{ marginTop: 10 }}>
              <button className="btn primary" disabled={!dirty} onClick={() => act(async () => { await api("/api/risk/limits", { method: "PUT", body: limits }); setDirty(false); }, "Limits saved. The change is recorded in the audit log.")}>Save limits</button>
              {dirty && <button className="btn" onClick={() => { setDirty(false); setLimits(data.limits); }}>Discard</button>}
            </div>
          </>
        )}
      </Card>
      <Card title="Position sizing">
        <div className="row">
          {([["entry", "Entry"], ["risk", "Risk per oz"], ["equity", "Equity"], ["max_loss", "Max loss USD"]] as const).map(([k, label]) => <label key={k}>{label} <input type="number" value={sizing[k]} onChange={(e) => setSizing({ ...sizing, [k]: e.target.value })} style={{ width: 100 }} /></label>)}
          <button className="btn" onClick={() => act(async () => setSizeResult(await api("/api/risk/size", { method: "POST", body: { plan: { entry: +sizing.entry, risk_per_oz: +sizing.risk }, equity: +sizing.equity, max_loss_usd: +sizing.max_loss } })))}>Size it</button>
        </div>
        {sizeResult && <div><b>{sizeResult.lots} lots</b>, decided by the {sizeResult.decided_by.replaceAll("_", " ")} limit. Notional {fmt(sizeResult.notional)}. Max loss at the stop {fmt(sizeResult.max_loss_at_stop)}. <span className="muted">{sizeResult.note}</span></div>}
      </Card>
      <Card title="Risk-engine decisions">
        <Table columns={["When", "Result", "Lots", "Source", "Side", "Entry", "Stop", "Reasons"]} empty="No decisions yet." rows={(data?.decisions ?? []).slice(0, 30).map((d: any) => [when(d.at), d.approved ? <Pill key="a" tone="ok">Approved</Pill> : <Pill key="a" tone="bad">Rejected</Pill>, fmt(d.lots), d.source, d.proposal.side > 0 ? "Buy" : "Sell", fmt(d.proposal.entry), fmt(d.proposal.stop), d.reasons.join("; ")])} />
      </Card>
    </>
  );
}
