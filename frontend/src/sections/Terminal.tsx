"use client";
import { useState } from "react";
import { api, fmt, useFetch, when } from "@/lib/api";
import { AlertTriangle, PlugZap } from "lucide-react";
import { Card, ErrorBox, Loading, Pill, Stat, StatusDot, Table } from "@/components/ui";

export function Terminal() {
  const { data, error, loading, reload } = useFetch("/api/terminal", 5000);
  const [form, setForm] = useState({ side: 1, lots: "0.10", stop: "", target: "" });
  const [result, setResult] = useState<any>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reply, setReply] = useState("");
  const [modal, setModal] = useState(0);
  const [ack, setAck] = useState(false);
  const [phrase, setPhrase] = useState("");

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
      {data?.simulated ? (
        <div className="mb-4 flex items-start gap-3 rounded-card border border-warn/40 bg-warn/10 p-4">
          <PlugZap className="mt-0.5 shrink-0 text-warn" size={20} />
          <div><div className="font-semibold text-warn">Paper account: MT5 is not connected</div>
            <div className="text-sm text-mute">Fills are simulated at stored or supplied prices. To attach MetaTrader 5: open the terminal on this PC, set <code>QR_BROKER=mt5</code>, and restart the API.</div></div>
        </div>
      ) : !data?.connected && (
        <div className="mb-4 flex items-start gap-3 rounded-card border border-warn/50 bg-warn/10 p-4">
          <AlertTriangle className="mt-0.5 shrink-0 text-warn" size={20} />
          <div><div className="font-semibold text-warn">MT5 is not connected</div><div className="text-sm text-mute">The MetaTrader 5 terminal must be running and logged in on this PC.</div></div>
        </div>
      )}
      <div className="grid">
        <Stat label="Connection" value={<span className="flex items-center gap-2"><StatusDot tone={data?.connected ? "ok" : "bad"} pulse={!!data?.connected} />{`${data?.broker}: ${data?.connected ? "connected" : "disconnected"}`}</span>} tone={data?.connected ? "up" : "down"} />
        <Stat label="Balance" value={fmt(a?.balance)} />
        <Stat label="Equity" value={fmt(a?.equity)} />
        <Stat label="Bid / ask" value={data?.quote ? `${fmt(data.quote.bid)} / ${fmt(data.quote.ask)}` : "No quote"} />
      </div>
      <Card title="Trading mode">
        <div className="row">
          {(data?.modes ?? []).map((m: string) => <button key={m} className={`btn ${data.mode === m ? "primary" : ""}`} onClick={() => act(() => api(`/api/terminal/mode/${m}`, { method: "POST" }))}>{m}</button>)}
          <Pill tone={data?.live_active ? "bad" : "ok"}>Live trading {data?.live_active ? "ACTIVE" : "off"}</Pill>
          {!data?.live_active && <button className="btn danger" onClick={() => { setAck(false); setPhrase(""); setModal(1); }}>Enable live trading…</button>}
          {data?.live_active && <button className="btn danger" onClick={() => act(() => api("/api/terminal/deactivate-live", { method: "POST" }))}>Deactivate live</button>}
        </div>
        <div className="muted">Research and simulation modes send nothing. Assisted needs a typed yes for each order. Real-money modes require the QR_LIVE_ENABLED switch plus an activation phrase, and never survive a restart. All routes pass through the risk engine.</div>
      </Card>
      <Card title="New order">
        <div className="row form-row">
          <div className="flex w-full gap-2" role="group" aria-label="Side">
            <button type="button" aria-pressed={form.side === 1} onClick={() => setForm({ ...form, side: 1 })} className={`flex-1 rounded-card border py-3 text-lg font-bold transition ${form.side === 1 ? "border-up bg-up text-black shadow-[0_0_20px_rgba(34,197,94,0.35)]" : "border-up/40 text-up hover:bg-up/10"}`}>Buy</button>
            <button type="button" aria-pressed={form.side === -1} onClick={() => setForm({ ...form, side: -1 })} className={`flex-1 rounded-card border py-3 text-lg font-bold transition ${form.side === -1 ? "border-down bg-down text-white shadow-[0_0_20px_rgba(239,68,68,0.35)]" : "border-down/40 text-down hover:bg-down/10"}`}>Sell</button>
          </div>
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
            <div key={t.fingerprint} className="row form-row">
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
      {modal > 0 && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" role="dialog" aria-modal="true" aria-label="Enable live trading">
          <div className="w-full max-w-md space-y-4 rounded-card border border-down/60 bg-surface p-5 shadow-[0_0_40px_rgba(239,68,68,0.25)]">
            <div className="flex items-center gap-2 text-lg font-bold text-down"><AlertTriangle size={20} />Enable live trading</div>
            {modal === 1 ? (
              <>
                <p className="text-sm text-mute">This lets the platform send <b className="text-ink">real-money orders</b> through your broker. Every order still passes the risk engine, and live mode does not survive a restart.</p>
                <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" />I understand orders will use real money.</label>
                <div className="flex justify-end gap-2"><button className="btn" onClick={() => setModal(0)}>Cancel</button><button className="btn danger" disabled={!ack} onClick={() => setModal(2)}>Continue</button></div>
              </>
            ) : (
              <>
                <p className="text-sm text-mute">Second confirmation: type the live activation phrase exactly. The API also requires the QR_LIVE_ENABLED switch, and refuses otherwise.</p>
                <input aria-label="Activation phrase" value={phrase} onChange={(e) => setPhrase(e.target.value)} className="w-full" autoFocus />
                <div className="flex justify-end gap-2"><button className="btn" onClick={() => setModal(1)}>Back</button><button className="btn danger" disabled={!phrase} onClick={() => act(async () => { await api("/api/terminal/activate-live", { method: "POST", body: { phrase } }); setModal(0); })}>Activate live trading</button></div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
