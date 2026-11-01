"use client";
import { useEffect, useMemo, useState } from "react";
import { fmt, useFetch } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice } from "@/components/ui";
import { PriceChart, type Level, type Line, type Marker } from "@/components/PriceChart";

const TFS = ["m1", "m5", "m15", "m30", "h1", "h4", "d1", "mn1"];
const OVERLAYS: Record<string, string> = { sma_20: "#e0a84e", sma_50: "#5b9dd9", sma_200: "#b28ad6", ema_20: "#58c7c7", bb_upper: "#6b7a87", bb_lower: "#6b7a87" };
const LAYERS = { structure: "Structure breaks", sweeps: "Liquidity sweeps", fvg: "FVG and iFVG zones", session: "Session levels", swings: "Swing levels" };
const STORE = "qr-drawings";

interface Drawing { kind: "level" | "trend"; price: number; a?: { time: number; price: number }; b?: { time: number; price: number } }

function loadDrawings(): Drawing[] {
  try {
    return JSON.parse(localStorage.getItem(STORE) ?? "[]");
  } catch {
    return [];
  }
}

export function Charts() {
  const [tf, setTf] = useState("h1");
  const [limit, setLimit] = useState(500);
  const [overlays, setOverlays] = useState<string[]>(["sma_50"]);
  const [layers, setLayers] = useState<string[]>(["structure", "session"]);
  const [tool, setTool] = useState<"none" | "level" | "trend">("none");
  const [pending, setPending] = useState<{ time: number; price: number } | null>(null);
  const [drawings, setDrawings] = useState<Drawing[]>([]);
  const { data, error, loading } = useFetch(`/api/bars?tf=${tf}&limit=${limit}`);

  useEffect(() => setDrawings(loadDrawings()), []);
  const save = (next: Drawing[]) => {
    setDrawings(next);
    try {
      localStorage.setItem(STORE, JSON.stringify(next));
    } catch {
      /* Drawings simply last for this page view if storage is blocked. */
    }
  };

  const bars: any[] = useMemo(() => data?.bars ?? [], [data]);
  const candles = useMemo(() => bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close })), [bars]);
  const last = bars[bars.length - 1];

  const lines = useMemo(() => {
    const out: Line[] = overlays.map((key) => ({ name: key, color: OVERLAYS[key], points: bars.map((b) => ({ time: b.time, value: b[key] })) }));
    drawings.filter((d) => d.kind === "trend" && d.a && d.b).forEach((d, i) => {
      const [a, b] = d.a!.time <= d.b!.time ? [d.a!, d.b!] : [d.b!, d.a!];
      if (a.time !== b.time) out.push({ name: `trend ${i + 1}`, color: "#f0f0f0", points: [{ time: a.time, value: a.price }, { time: b.time, value: b.price }] });
    });
    return out;
  }, [bars, overlays, drawings]);

  const markers = useMemo(() => {
    const out: Marker[] = [];
    for (const b of bars) {
      if (layers.includes("structure")) {
        if (b.bos_up === 1) out.push({ time: b.time, position: "belowBar", color: "#3fb68b", shape: "arrowUp", text: "BOS" });
        if (b.bos_down === 1) out.push({ time: b.time, position: "aboveBar", color: "#e0605e", shape: "arrowDown", text: "BOS" });
        if (b.choch_up === 1) out.push({ time: b.time, position: "belowBar", color: "#58c7c7", shape: "arrowUp", text: "CHoCH" });
        if (b.choch_down === 1) out.push({ time: b.time, position: "aboveBar", color: "#e0a84e", shape: "arrowDown", text: "CHoCH" });
      }
      if (layers.includes("sweeps")) {
        if (b.sweep_high === 1) out.push({ time: b.time, position: "aboveBar", color: "#b28ad6", shape: "circle", text: "sweep" });
        if (b.sweep_low === 1) out.push({ time: b.time, position: "belowBar", color: "#b28ad6", shape: "circle", text: "sweep" });
      }
      if (layers.includes("fvg")) {
        if (b.ifvg_up === 1) out.push({ time: b.time, position: "belowBar", color: "#58c7c7", shape: "square", text: "iFVG" });
        if (b.ifvg_down === 1) out.push({ time: b.time, position: "aboveBar", color: "#e0a84e", shape: "square", text: "iFVG" });
      }
    }
    return out;
  }, [bars, layers]);

  const levels = useMemo(() => {
    const out: Level[] = drawings.filter((d) => d.kind === "level").map((d) => ({ price: d.price, color: "#f0f0f0", title: "level", dashed: false }));
    if (!last) return out;
    const add = (price: number | null, color: string, title: string) => price !== null && price !== undefined && out.push({ price, color, title });
    if (layers.includes("session")) {
      add(last.pdh, "#e0a84e", "PDH");
      add(last.pdl, "#e0a84e", "PDL");
      add(last.day_high, "#7f8d99", "day high");
      add(last.day_low, "#7f8d99", "day low");
    }
    if (layers.includes("swings")) {
      add(last.swing_high, "#b28ad6", "swing high");
      add(last.swing_low, "#b28ad6", "swing low");
    }
    if (layers.includes("fvg")) {
      add(last.fvg_up_low, "#3fb68b", "FVG up low");
      add(last.fvg_up_high, "#3fb68b", "FVG up high");
      add(last.fvg_down_low, "#e0605e", "FVG down low");
      add(last.fvg_down_high, "#e0605e", "FVG down high");
    }
    return out;
  }, [last, layers, drawings]);

  const onPick = (time: number, price: number) => {
    if (tool === "level") {
      save([...drawings, { kind: "level", price }]);
      setTool("none");
    } else if (tool === "trend") {
      if (!pending) setPending({ time, price });
      else {
        save([...drawings, { kind: "trend", price, a: pending, b: { time, price } }]);
        setPending(null);
        setTool("none");
      }
    }
  };
  const toggle = (list: string[], set: (v: string[]) => void, key: string) => set(list.includes(key) ? list.filter((k) => k !== key) : [...list, key]);

  return (
    <>
      <h1>Market Charts</h1>
      <ErrorBox error={error} />
      <div className="row">
        {TFS.map((t) => <button key={t} className={`btn ${t === tf ? "primary" : ""}`} onClick={() => setTf(t)}>{t.toUpperCase()}</button>)}
        <label>Bars <select value={limit} onChange={(e) => setLimit(+e.target.value)}>{[200, 500, 1000, 2000, 5000].map((n) => <option key={n}>{n}</option>)}</select></label>
        {last && <span className="muted">Close {fmt(last.close)}, spread {fmt(last.spread)}, RSI {fmt(last.rsi_14, 1)}, ATR {fmt(last.atr_14)}. Mid prices.</span>}
      </div>
      <div className="row">
        {Object.keys(OVERLAYS).map((k) => <label className="check" key={k}><input type="checkbox" checked={overlays.includes(k)} onChange={() => toggle(overlays, setOverlays, k)} />{k}</label>)}
      </div>
      <div className="row">
        {Object.entries(LAYERS).map(([k, label]) => <label className="check" key={k}><input type="checkbox" checked={layers.includes(k)} onChange={() => toggle(layers, setLayers, k)} />{label}</label>)}
      </div>
      <div className="row">
        <button className={`btn ${tool === "level" ? "primary" : ""}`} onClick={() => setTool(tool === "level" ? "none" : "level")}>Draw level</button>
        <button className={`btn ${tool === "trend" ? "primary" : ""}`} onClick={() => { setPending(null); setTool(tool === "trend" ? "none" : "trend"); }}>Draw trend line</button>
        <button className="btn" onClick={() => save([])} disabled={!drawings.length}>Clear drawings ({drawings.length})</button>
        {tool !== "none" && <span className="warn">{tool === "level" ? "Click the chart to place the level." : pending ? "Click the second point." : "Click the first point."}</span>}
      </div>
      {loading && !bars.length ? <Loading what="Loading bars" /> : <Card><PriceChart candles={candles} lines={lines} levels={levels} markers={markers} onPick={onPick} /></Card>}
      <Notice>History only: no live feed is connected to this chart. Structure and FVG rules are this project&apos;s own definitions; see the README for each.</Notice>
    </>
  );
}
