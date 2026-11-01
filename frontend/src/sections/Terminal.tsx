"use client";
import { useState } from "react";
import { api, fmt, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice, Pill, Stat, Table } from "@/components/ui";

export function Terminal() {
  const { data, error, loading, reload } = useFetch("/api/terminal", 5000);
  const [form, setForm] = useState({ side: 1, lots: "0.10", stop: "", target: "" });
  const [result, setResult] = useState<any>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reply, setReply] = useState("");

  const act = async (fn: () => Promise<any>) => {
    setActionError(null);
    try {
      const out = await fn();
      await reload();
      return out;
    } catch (e) {
      setActionError((e as Error).message);
    }
  };
  const submit = () => act(async () => setResult(await api("/api/terminal/order", { method: "POST", body: { side: form.side, lots: +form.lots, stop: form.stop ? +form.stop : null, target: form.target ? +form.target : null } })));
  if (loading && !data) return <Loading />;
  const a = data?.account;
  return (
    <>
      <h1>Trading Terminal</h1>
      <ErrorBox error={error ?? actionError ?? data?.error ?? null} />
      {data?.simulated ? <Notice>Paper account: simulated fills at stored or supplied prices, with no broker connected. Set QR_BROKER=mt5 and restart to attach MetaTrader 5.</Notice> : !data?.connected && <div className="error">MT5 is not connected. The terminal must be running on this PC.</div>}
      <div className="grid">
        <Stat label="Connection" value={`${data?.broker}: ${data?.connected ? "connected" : "disconnected"}`} tone={data?.connected ? "up" : "down"} />
        <Stat label="Balance" value={fmt(a?.balance)} />
        <Stat label="Equity" value={fmt(a?.equity)} />
        <Stat label="Bid / ask" value={data?.quote ? `${fmt(data.quote.bid)} / ${fmt(data.quote.ask)}` : "No quote"} />
      </div>
      <Card title="Trading mode">
        <div className="row">
          {(data?.modes ?? []).map((m: string) => <button key={m} className={`btn ${data.mode === m ? "primary" : ""}`} onClick={() => act(() => api(`/api/terminal/mode/${m}`, { method: "POST" }))}>{m}</button>)}
          <Pill tone={data?.live_active ? "bad" : "ok"}>Live trading {data?.live_active ? "ACTIVE" : "off"}</Pill>
          {data?.live_active && <button className="btn danger" onClick={() => act(() => api("/api/terminal/deactivate-live", { method: "POST" }))}>Deactivate live</button>}
        </div>
        <div className="muted">Research and simulation modes send nothing. Assisted needs a typed yes for each order. Real-money modes require the QR_LIVE_ENABLED switch plus an activation phrase, and never survive a restart. All routes pass through the risk engine.</div>
      </Card>
      <Card title="New order">
        <div className="row">
          <select aria-label="Side" value={form.side} onChange={(e) => setForm({ ...form, side: +e.target.value })}><option value={1}>Buy</option><option value={-1}>Sell</option></select>
          <label>Lots <input aria-label="Lots" type="number" step="0.01" min="0.01" value={form.lots} onChange={(e) => setForm({ ...form, lots: e.target.value })} style={{ width: 80 }} /></label>
          <label>Stop <input aria-label="Stop" type="number" step="0.01" value={form.stop} onChange={(e) => setForm({ ...form, stop: e.target.value })} style={{ width: 100 }} /></label>
          <label>Target <input aria-label="Target" type="number" step="0.01" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} style={{ width: 100 }} /></label>
          <button className="btn primary" onClick={submit}>Send to risk check</button>
        </div>
        {result && (
          <div data-testid="order-result">
            <Pill tone={result.status === "sent" ? "ok" : result.status === "awaiting_approval" ? "warn" : "bad"}>{result.status.replaceAll("_", " ")}</Pill> {result.message} {result.status === "sent" && `${result.lots} lots`}
            {result.reasons && <ul className="plain">{result.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>}
          </div>
        )}
      </Card>
      {(data?.awaiting_approval ?? []).length > 0 && (
        <Card title="Waiting for your yes">
          {data.awaiting_approval.map((t: any) => (
            <div key={t.fingerprint} className="row">
              <b>{t.ticket}</b>
              <input aria-label="Reply" placeholder='Type "yes" for this one order' value={reply} onChange={(e) => setReply(e.target.value)} />
              <button className="btn primary" onClick={() => act(async () => { setResult(await api("/api/terminal/approve", { method: "POST", body: { fingerprint: t.fingerprint, reply } })); setReply(""); })}>Reply</button>
              <button className="btn" onClick={() => act(() => api(`/api/terminal/decline/${t.fingerprint}`, { method: "POST" }))}>Decline</button>
            </div>
          ))}
        </Card>
      )}
      <Card title="Open positions">
        <Table columns={["Ticket", "Side", "Lots", "Entry", "Stop", "Target", "Profit", ""]} empty="No open positions." rows={(data?.positions ?? []).map((p: any) => [p.ticket, p.side > 0 ? "Long" : "Short", p.lots, fmt(p.entry), fmt(p.stop), fmt(p.target),
          <span key="p" className={p.profit >= 0 ? "up" : "down"}>{fmt(p.profit)}</span>, <button key="c" className="btn" onClick={() => act(() => api(`/api/terminal/close/${p.ticket}`, { method: "POST" }))}>Close</button>])} />
      </Card>
      <Card title="Pending orders">
        <Table columns={["Ticket", "Side", "Lots", "Type", "Price"]} empty="No pending orders." rows={(data?.pending ?? []).map((o: any) => [o.ticket, o.side > 0 ? "Buy" : "Sell", o.lots, o.kind, fmt(o.price)])} />
      </Card>
      <Card title="Trade history">
        <Table columns={["Closed", "Side", "Lots", "Entry", "Exit", "Reason", "Net"]} empty="No closed trades." rows={(data?.history ?? []).slice(0, 30).map((t: any) => [when(t.closed_at), t.side > 0 ? "Long" : "Short", t.lots, fmt(t.entry), fmt(t.exit), t.reason, <span key="n" className={t.net >= 0 ? "up" : "down"}>{fmt(t.net)}</span>])} />
      </Card>
    </>
  );
}
