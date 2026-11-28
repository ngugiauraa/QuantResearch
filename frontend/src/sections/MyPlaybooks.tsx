"use client";
import { useEffect, useMemo, useState } from "react";
import { api, fmt, useFetch } from "@/lib/api";
import { PriceChart, type Candle, type Level, type Marker } from "@/components/PriceChart";
import { Card, ErrorBox, Loading, Notice, Pill, Table } from "@/components/ui";

const TITLES: Record<string, string> = {
  ny_sweep_ifvg: "Setup 1: NY Open Sweep + iFVG",
  london_asian_sweep: "Setup 2: London Open Asian-Range Sweep",
  fvg_continuation: "Setup 3: 15m/1H FVG Trend Continuation",
};
const sec = (iso: string) => Math.floor(Date.parse(iso) / 1000);

/** The chart of one example: M5 candles around the trade, with entry, stop, target and the setup's own levels. */
function ExampleChart({ ex }: { ex: any }) {
  const [candles, setCandles] = useState<Candle[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const end = new Date(Math.min(sec(ex.exit_ts), sec(ex.fill_ts) + 10 * 3600) * 1000 + 3600 * 1000).toISOString();
    setCandles([]);
    setError(null);
    api(`/api/bars?tf=m5&limit=240&end=${encodeURIComponent(end)}`)
      .then((d: any) => setCandles((d.bars ?? []).map((b: any) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }))))
      .catch((e) => setError((e as Error).message));
  }, [ex]);
  const c = ex.context ?? {};
  const levels: Level[] = [
    { price: ex.entry, color: "#60a5fa", title: "entry" },
    { price: ex.stop, color: "#ef4444", title: "stop" },
    { price: ex.target, color: "#22c55e", title: `target (${ex.target_kind})` },
  ];
  if (c.swept_level) levels.push({ price: c.swept_level, color: "#f59e0b", title: `swept ${c.swept}`, dashed: true });
  if (c.asian_high) levels.push({ price: c.asian_high, color: "#f59e0b", title: "Asian high", dashed: true }, { price: c.asian_low, color: "#f59e0b", title: "Asian low", dashed: true });
  if (c.zone) levels.push({ price: c.zone.hi, color: "#a78bfa", title: `${c.zone.tf} ${c.zone.kind} top`, dashed: true }, { price: c.zone.lo, color: "#a78bfa", title: `${c.zone.tf} ${c.zone.kind} bottom`, dashed: true });
  if (c.trigger_level) levels.push({ price: c.trigger_level, color: "#94a3b8", title: c.trigger, dashed: true });
  const long = ex.side === "long";
  const markers: Marker[] = [
    { time: sec(ex.signal_ts) - 300, position: long ? "belowBar" : "aboveBar", color: "#60a5fa", shape: long ? "arrowUp" : "arrowDown", text: "entry" },
    { time: Math.floor(sec(ex.exit_ts) / 300) * 300, position: long ? "aboveBar" : "belowBar", color: ex.result_r > 0 ? "#22c55e" : ex.result_r < 0 ? "#ef4444" : "#94a3b8", shape: "circle", text: ex.exit_reason },
  ];
  if (c.sweep_ts) markers.push({ time: sec(c.sweep_ts), position: long ? "belowBar" : "aboveBar", color: "#f59e0b", shape: "square", text: "sweep" });
  if (c.touch_ts) markers.push({ time: sec(c.touch_ts), position: long ? "belowBar" : "aboveBar", color: "#a78bfa", shape: "square", text: "touch" });
  markers.sort((a, b) => a.time - b.time);
  if (error) return <ErrorBox error={error} />;
  if (!candles.length) return <Loading what="Loading M5 bars" />;
  return <PriceChart candles={candles} levels={levels} markers={markers} height={420} />;
}

export function MyPlaybooks() {
  const ex = useFetch("/api/playbooks/examples");
  const [setup, setSetup] = useState("ny_sweep_ifvg");
  const [open, setOpen] = useState<number | null>(null);
  const rows: any[] = useMemo(() => ex.data?.setups?.[setup] ?? [], [ex.data, setup]);
  if (ex.loading) return <><h1>My Playbooks</h1><Loading /></>;
  return (
    <>
      <h1>My Playbooks</h1>
      <ErrorBox error={ex.error} />
      <Notice>
        Fifteen example trades per setup, one per year where possible, drawn at random within each year, so you can check the code trades your
        rules the way you do. The exact definitions are in docs/my_playbooks.md. Click a row to load its M5 chart (chart times are UTC; the table is New
        York time). Nothing is tested further until you confirm. No orders.
      </Notice>
      <div className="row form-row">
        {Object.keys(TITLES).map((k) => <button key={k} className={`btn ${k === setup ? "primary" : ""}`} onClick={() => { setSetup(k); setOpen(null); }}>{TITLES[k]}</button>)}
      </div>
      <Card title={TITLES[setup]}>
        <Table columns={["#", "Date", "Entry (NY)", "Side", "Entry", "Stop", "Target", "Risk $", "Result R", "Exit", "Exit (NY)", "Trigger", ""]}
          rows={rows.map((e, i) => [i + 1, e.date, e.entry_time_ny.slice(11), e.side, fmt(e.entry), fmt(e.stop), `${fmt(e.target)} (${e.target_kind})`, fmt(e.risk_usd),
            <Pill key="r" tone={e.result_r > 0 ? "ok" : e.result_r < 0 ? "bad" : "warn"}>{fmt(e.result_r)}</Pill>, e.exit_reason, e.exit_time_ny,
            e.context?.trigger ?? "", <button key="c" className="btn" onClick={() => setOpen(i)}>Chart</button>])} />
      </Card>
      {open !== null && rows[open] && (
        <Card title={`${TITLES[setup]}: ${rows[open].date} ${rows[open].side}`}>
          <ExampleChart ex={rows[open]} />
          <pre className="muted" style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{JSON.stringify(rows[open].context, null, 1)}</pre>
        </Card>
      )}
    </>
  );
}
