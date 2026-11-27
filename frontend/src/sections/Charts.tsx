"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmt, useFetch, when } from "@/lib/api";
import { Card, EmptyState, ErrorBox, Notice, Skeleton } from "@/components/ui";
import { adx, bbWidthPercentile, ema } from "@/lib/indicators";
import { PriceChart, type Candle, type Level, type Line, type Marker } from "@/components/PriceChart";
import { mergeForming } from "@/lib/forming";

const TFS = ["m1", "m5", "m15", "m30", "h1", "h4", "d1", "mn1"];
const OVERLAYS: Record<string, string> = { sma_20: "#f59e0b", sma_50: "#7c3aed", sma_200: "#a78bfa", ema_20: "#58c7c7", bb_upper: "#6b7a87", bb_lower: "#6b7a87" };
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
  const [feedLive, setFeedLive] = useState(false);
  // Closed bars: refreshed every minute while a live feed is attached (and right after each new minute, below).
  const { data, error, loading, reload } = useFetch(`/api/bars?tf=${tf}&limit=${limit}`, feedLive ? 60000 : undefined);
  useEffect(() => setFeedLive(Boolean(data?.feed?.live)), [data]);
  const forming = useFormingBar(feedLive, reload);
  const overview = useFetch("/api/overview", 30000);

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
  const live = useMemo(() => mergeForming(candles, forming, tf), [candles, forming, tf]);
  const last = bars[bars.length - 1];
  const panel = useMemo(() => {
    if (!bars.length) return null;
    const e50 = ema(bars.map((b) => b.close), 50);
    const bb = bbWidthPercentile(bars);
    return { adx: adx(bars), ema50: e50[e50.length - 1], bbWidth: bb?.width ?? null, bbPct: bb?.percentile ?? null };
  }, [bars]);

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
        if (b.bos_up === 1) out.push({ time: b.time, position: "belowBar", color: "#22c55e", shape: "arrowUp", text: "BOS" });
        if (b.bos_down === 1) out.push({ time: b.time, position: "aboveBar", color: "#ef4444", shape: "arrowDown", text: "BOS" });
        if (b.choch_up === 1) out.push({ time: b.time, position: "belowBar", color: "#58c7c7", shape: "arrowUp", text: "CHoCH" });
        if (b.choch_down === 1) out.push({ time: b.time, position: "aboveBar", color: "#f59e0b", shape: "arrowDown", text: "CHoCH" });
      }
      if (layers.includes("sweeps")) {
        if (b.sweep_high === 1) out.push({ time: b.time, position: "aboveBar", color: "#a78bfa", shape: "circle", text: "sweep" });
        if (b.sweep_low === 1) out.push({ time: b.time, position: "belowBar", color: "#a78bfa", shape: "circle", text: "sweep" });
      }
      if (layers.includes("fvg")) {
        if (b.ifvg_up === 1) out.push({ time: b.time, position: "belowBar", color: "#58c7c7", shape: "square", text: "iFVG" });
        if (b.ifvg_down === 1) out.push({ time: b.time, position: "aboveBar", color: "#f59e0b", shape: "square", text: "iFVG" });
      }
    }
    return out;
  }, [bars, layers]);

  const levels = useMemo(() => {
    const out: Level[] = drawings.filter((d) => d.kind === "level").map((d) => ({ price: d.price, color: "#f0f0f0", title: "level", dashed: false }));
    if (!last) return out;
    const add = (price: number | null, color: string, title: string) => price !== null && price !== undefined && out.push({ price, color, title });
    if (layers.includes("session")) {
      add(last.pdh, "#f59e0b", "PDH");
      add(last.pdl, "#f59e0b", "PDL");
      add(last.day_high, "#7f8d99", "day high");
      add(last.day_low, "#7f8d99", "day low");
    }
    if (layers.includes("swings")) {
      add(last.swing_high, "#a78bfa", "swing high");
      add(last.swing_low, "#a78bfa", "swing low");
    }
    if (layers.includes("fvg")) {
      add(last.fvg_up_low, "#22c55e", "FVG up low");
      add(last.fvg_up_high, "#22c55e", "FVG up high");
      add(last.fvg_down_low, "#ef4444", "FVG down low");
      add(last.fvg_down_high, "#ef4444", "FVG down high");
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
        <div className="flex gap-1 rounded-full border border-line bg-surface2 p-1" role="group" aria-label="Timeframe">{TFS.map((t) => <button key={t} className={`rounded-full px-3 py-0.5 text-xs font-semibold transition-colors duration-150 ${t === tf ? "bg-brand text-white shadow-glow" : "text-mute hover:text-ink"}`} onClick={() => setTf(t)}>{t.toUpperCase()}</button>)}</div><span className="rounded-full border border-line bg-surface px-3 py-0.5 text-xs font-semibold">XAUUSD</span>
        <label>Bars <select value={limit} onChange={(e) => setLimit(+e.target.value)}>{[200, 500, 1000, 2000, 5000].map((n) => <option key={n}>{n}</option>)}</select></label>
        {last && <span className="muted">{live ? <>Live {fmt(live.close)} (forming), last closed {fmt(last.close)}</> : <>Close {fmt(last.close)}</>}, spread {fmt(last.spread)}, RSI {fmt(last.rsi_14, 1)}, ATR {fmt(last.atr_14)}. Mid prices.</span>}
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
      <div className="grid-chart">
        <div className="min-w-0">
          {loading && !bars.length ? <Skeleton className="h-[560px]" /> : !bars.length ? <EmptyState className="h-[560px]" text="No bars for this timeframe. Build the warehouse first." /> : <Card><PriceChart candles={candles} lines={lines} levels={levels} markers={markers} onPick={onPick} height={560} live={live} /></Card>}
        </div>
        <Card title="Current bar">
          {!last ? <Skeleton className="h-48" /> : (
            <dl className="space-y-2.5 text-sm">
              {([["Close", fmt(last.close)], ["ADX (14)", panel?.adx == null ? "–" : fmt(panel.adx, 1)], ["EMA 20", fmt(last.ema_20)], ["EMA 50", panel?.ema50 == null ? "–" : fmt(panel.ema50)], ["ATR (14)", fmt(last.atr_14)], ["BB width", panel?.bbWidth == null ? "–" : `${fmt(panel.bbWidth * 100)}%`], ["BB width pct", panel?.bbPct == null ? "–" : String(Math.round(panel.bbPct))], ["RSI (14)", fmt(last.rsi_14, 1)], ["Spread", fmt(last.spread)]] as const).map(([k, v]) => (
                <div key={k} className="flex items-baseline justify-between border-b border-line pb-2"><dt className="label">{k}</dt><dd className="mono font-semibold">{v}</dd></div>
              ))}
              <div className="flex items-baseline justify-between"><dt className="label">Regime</dt><dd className="font-semibold uppercase text-brand-soft">{overview.data?.regime?.regime ?? "–"}</dd></div>
              <div className="text-[11px] text-mute">ADX, EMA 50 and BB width are computed in the browser from the bars loaded here. Regime is the latest H1 fit.</div>
            </dl>
          )}
        </Card>
      </div>
      <FeedBanner feed={data?.feed} />
    </>
  );
}

/** States what the chart is really drawing, from the API's own feed status (GET /api/bars -> feed). */
function FeedBanner({ feed }: { feed?: any }) {
  const rules = " Structure and FVG rules are this project's own definitions; see the README for each.";
  if (!feed) return <Notice>{"Feed status unknown until the bars load." + rules}</Notice>;
  if (feed.state === "live")
    return <Notice>{`Live: stored history to ${when(feed.warehouse_end)}, then ${String(feed.source).toUpperCase()}'s own closed 1-minute bars to ${when(feed.broker_end)}. Bars after the stored history are the broker's bid plus its spread.` + rules}</Notice>;
  if (feed.state === "error")
    return <Notice>{`${String(feed.source).toUpperCase()} is attached but its bar feed failed (${feed.error}). Showing stored history to ${when(feed.warehouse_end)}.` + rules}</Notice>;
  if (feed.state === "waiting")
    return <Notice>{`${String(feed.source).toUpperCase()} is attached; it has no closed bars newer than the stored history (${when(feed.warehouse_end)}) yet, for example while the market is closed.` + rules}</Notice>;
  return <Notice>{`History only: ${feed.reason ?? "no live feed is connected to this chart"}.` + rules}</Notice>;
}

const FORMING_POLL_MS = 1500;

/** Polls the forming minute every 1.5 s while live; asks for closed bars again shortly after each minute closes. */
function useFormingBar(enabled: boolean, reloadClosed: () => void): Candle | null {
  const [bar, setBar] = useState<Candle | null>(null);
  const busy = useRef(false);
  const minute = useRef<number | null>(null);
  const reload = useRef(reloadClosed);
  reload.current = reloadClosed;
  useEffect(() => {
    if (!enabled) {
      setBar(null);
      return;
    }
    let alive = true;
    const tick = async () => {
      if (busy.current) return;
      busy.current = true;
      try {
        const r = await api<any>("/api/bars/forming", { timeoutMs: 8000 });
        if (!alive) return;
        if (!r?.live || !r.bar) {
          setBar(null);
          return;
        }
        if (minute.current !== null && r.bar.time > minute.current) setTimeout(() => reload.current(), 2500); // A minute just closed.
        minute.current = r.bar.time;
        setBar({ time: r.bar.time, open: r.bar.open, high: r.bar.high, low: r.bar.low, close: r.bar.close });
      } catch {
        /* A missed poll is retried on the next tick; the closed bars stay as they are. */
      } finally {
        busy.current = false;
      }
    };
    tick();
    const id = setInterval(tick, FORMING_POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [enabled]);
  return bar;
}
