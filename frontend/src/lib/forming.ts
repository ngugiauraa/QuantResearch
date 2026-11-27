import type { Candle } from "@/components/PriceChart";

const TF_SECONDS: Record<string, number> = { m1: 60, m5: 300, m15: 900, m30: 1800, h1: 3600, h4: 14400, d1: 86400 };

/** Open time of the chart bar containing a time, exactly as the warehouse buckets it (UTC floors; calendar months). */
export function bucketStart(time: number, tf: string): number {
  if (tf === "mn1") {
    const d = new Date(time * 1000);
    return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1) / 1000;
  }
  const s = TF_SECONDS[tf] ?? 60;
  return Math.floor(time / s) * s;
}

/**
 * The chart's last candle with the minute forming now merged in. If that minute is inside the last closed-bar
 * bucket from the server, the candle keeps the server's open and widens its high/low; if it starts a new bucket,
 * a new candle begins. Closed candles are never changed here.
 */
export function mergeForming(candles: Candle[], forming: Candle | null, tf: string): Candle | null {
  if (!forming || !candles.length) return null;
  const last = candles[candles.length - 1];
  const start = bucketStart(forming.time, tf);
  if (start < last.time) return null;
  if (start === last.time) {
    if (tf === "m1") return { ...forming, time: start }; // The server's last M1 is this forming minute only if it is still open.
    return { time: start, open: last.open, high: Math.max(last.high, forming.high), low: Math.min(last.low, forming.low), close: forming.close };
  }
  return { time: start, open: forming.open, high: forming.high, low: forming.low, close: forming.close };
}
