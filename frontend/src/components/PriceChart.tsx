"use client";
import { useEffect, useRef } from "react";
import { createChart, ColorType, CrosshairMode, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";

export interface Candle { time: number; open: number; high: number; low: number; close: number }
export interface Line { name: string; color: string; points: { time: number; value: number | null }[] }
export interface Level { price: number; color: string; title: string; dashed?: boolean }
export interface Marker { time: number; position: "aboveBar" | "belowBar"; color: string; shape: "arrowUp" | "arrowDown" | "circle" | "square"; text?: string }

const theme = { layout: { background: { type: ColorType.Solid, color: "#161c22" }, textColor: "#8b98a5" }, grid: { vertLines: { color: "#20282f" }, horzLines: { color: "#20282f" } } };

/** Candlesticks with overlays, levels and markers. onClick supports the drawing tools on the Charts page. */
export function PriceChart({ candles, lines = [], levels = [], markers = [], onPick, height, follow = false }: {
  candles: Candle[]; lines?: Line[]; levels?: Level[]; markers?: Marker[]; onPick?: (time: number, price: number) => void; height?: number; follow?: boolean;
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
    const c = createChart(box.current, { ...theme, autoSize: true, crosshair: { mode: CrosshairMode.Normal }, timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#2a343d" }, rightPriceScale: { borderColor: "#2a343d" } });
    main.current = c.addCandlestickSeries({ upColor: "#3fb68b", downColor: "#e0605e", wickUpColor: "#3fb68b", wickDownColor: "#e0605e", borderVisible: false });
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

  return <div ref={box} className="chart" style={height ? { height } : undefined} data-testid="chart" />;
}

export function ValueChart({ points, color = "#5b9dd9", height = 240 }: { points: { time: number; value: number }[]; color?: string; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, { ...theme, autoSize: true, timeScale: { timeVisible: true, borderColor: "#2a343d" }, rightPriceScale: { borderColor: "#2a343d" } });
    const s = c.addLineSeries({ color, lineWidth: 2, priceLineVisible: false });
    const seen = new Set<number>();
    s.setData([...points].sort((a, b) => a.time - b.time).filter((p) => !seen.has(p.time) && seen.add(p.time)).map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    c.timeScale().fitContent();
    return () => c.remove();
  }, [points, color]);
  return <div ref={box} style={{ height, width: "100%" }} />;
}
