"use client";
import { useEffect, useRef } from "react";
import { createChart, ColorType, CrosshairMode, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";

export interface Candle { time: number; open: number; high: number; low: number; close: number }
export interface Line { name: string; color: string; points: { time: number; value: number | null }[] }
export interface Level { price: number; color: string; title: string; dashed?: boolean }
export interface Marker { time: number; position: "aboveBar" | "belowBar"; color: string; shape: "arrowUp" | "arrowDown" | "circle" | "square"; text?: string }

const theme = { layout: { background: { type: ColorType.Solid, color: "#111118" }, textColor: "#64748b" }, grid: { vertLines: { color: "#1a1a28" }, horzLines: { color: "#1a1a28" } } };

/** Candlesticks with overlays, levels and markers. onClick supports the drawing tools on the Charts page. */
/**
 * `live` is the forming (last) candle. It is drawn with series.update(), which replaces the last bar in place or
 * appends a new one, without resetting the other bars, the overlays or the user's zoom. Closed bars come only
 * from `candles`.
 */
export function PriceChart({ candles, lines = [], levels = [], markers = [], onPick, height, follow = false, live = null }: {
  candles: Candle[]; lines?: Line[]; levels?: Level[]; markers?: Marker[]; onPick?: (time: number, price: number) => void; height?: number; follow?: boolean;
  live?: Candle | null;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const main = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const overlays = useRef<ISeriesApi<"Line">[]>([]);
  const priceLines = useRef<any[]>([]);
  const pick = useRef(onPick);
  pick.current = onPick;

  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, { ...theme, autoSize: true, crosshair: { mode: CrosshairMode.Normal }, timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#1e1e2e" }, rightPriceScale: { borderColor: "#1e1e2e" } });
    main.current = c.addCandlestickSeries({ upColor: "#22c55e", downColor: "#ef4444", wickUpColor: "#22c55e", wickDownColor: "#ef4444", borderVisible: false });
    chart.current = c;
    c.subscribeClick((p) => {
      if (!p.time || !p.point || !main.current || !pick.current) return;
      const price = main.current.coordinateToPrice(p.point.y);
      if (price !== null) pick.current(p.time as number, price as number);
    });
    return () => {
      c.remove();
      chart.current = null;
      main.current = null;
      overlays.current = [];
      priceLines.current = [];
    };
  }, []);

  useEffect(() => {
    if (!main.current || !chart.current) return;
    main.current.setData(candles.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
    overlays.current.forEach((s) => chart.current!.removeSeries(s));
    overlays.current = lines.map((l) => {
      const s = chart.current!.addLineSeries({ color: l.color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, title: l.name });
      s.setData(l.points.filter((p) => p.value !== null).map((p) => ({ time: p.time as UTCTimestamp, value: p.value as number })));
      return s;
    });
    main.current.setMarkers([...markers].sort((a, b) => a.time - b.time).map((m) => ({ ...m, time: m.time as UTCTimestamp })));
    priceLines.current.forEach((l) => main.current!.removePriceLine(l));
    priceLines.current = levels.map((l) => main.current!.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.dashed === false ? 0 : 2, axisLabelVisible: true, title: l.title }));
    if (follow) chart.current.timeScale().scrollToRealTime();
  }, [candles, lines, levels, markers, follow]);

  useEffect(() => {
    if (!main.current || !live || !candles.length) return;
    // update() refuses a bar older than the last one; a stale forming bar is simply skipped.
    if (live.time < candles[candles.length - 1].time) return;
    main.current.update({ ...live, time: live.time as UTCTimestamp });
  }, [live, candles]);

  return <div ref={box} className="chart" style={height ? { height } : undefined} data-testid="chart" />;
}

export function ValueChart({ points, color = "#7c3aed", height = 240 }: { points: { time: number; value: number }[]; color?: string; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, { ...theme, autoSize: true, timeScale: { timeVisible: true, borderColor: "#1e1e2e" }, rightPriceScale: { borderColor: "#1e1e2e" } });
    const s = c.addAreaSeries({ lineColor: color, topColor: "rgba(124,58,237,0.45)", bottomColor: "rgba(124,58,237,0)", lineWidth: 2, priceLineVisible: false });
    const seen = new Set<number>();
    s.setData([...points].sort((a, b) => a.time - b.time).filter((p) => !seen.has(p.time) && seen.add(p.time)).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    c.timeScale().fitContent();
    return () => c.remove();
  }, [points, color]);
  return <div ref={box} style={{ height, width: "100%", filter: "drop-shadow(0 0 8px rgba(124,58,237,0.45))" }} />;
}
