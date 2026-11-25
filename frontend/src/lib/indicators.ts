// Client-side indicators for display only. The backend serves SMA20/50/200, EMA20, ATR14, RSI14 and Bollinger bands.
export interface Ohlc { high: number; low: number; close: number }

export function ema(values: number[], period: number): (number | null)[] {
  const k = 2 / (period + 1);
  const out: (number | null)[] = [];
  let prev: number | null = null;
  values.forEach((v, i) => {
    if (i < period - 1) return void out.push(null);
    prev = prev === null ? values.slice(0, period).reduce((a, b) => a + b, 0) / period : v * k + prev * (1 - k);
    out.push(prev);
  });
  return out;
}

/** Wilder ADX. Returns the last value, or null when there are too few bars. */
export function adx(bars: Ohlc[], period = 14): number | null {
  if (bars.length < period * 2 + 1) return null;
  let tr = 0, pdm = 0, ndm = 0;
  const dx: number[] = [];
  let adxVal: number | null = null;
  for (let i = 1; i < bars.length; i++) {
    const up = bars[i].high - bars[i - 1].high, down = bars[i - 1].low - bars[i].low;
    const t = Math.max(bars[i].high - bars[i].low, Math.abs(bars[i].high - bars[i - 1].close), Math.abs(bars[i].low - bars[i - 1].close));
    const p = up > down && up > 0 ? up : 0, n = down > up && down > 0 ? down : 0;
    if (i <= period) { tr += t; pdm += p; ndm += n; if (i < period) continue; }
    else { tr = tr - tr / period + t; pdm = pdm - pdm / period + p; ndm = ndm - ndm / period + n; }
    const pdi = tr ? (100 * pdm) / tr : 0, ndi = tr ? (100 * ndm) / tr : 0;
    const d = pdi + ndi ? (100 * Math.abs(pdi - ndi)) / (pdi + ndi) : 0;
    dx.push(d);
    if (dx.length === period) adxVal = dx.reduce((a, b) => a + b, 0) / period;
    else if (dx.length > period && adxVal !== null) adxVal = (adxVal * (period - 1) + d) / period;
  }
  return adxVal;
}

/** Bollinger width as a fraction of the middle band, and where the latest width ranks among the series (0 to 100). */
export function bbWidthPercentile(bars: { bb_upper: number | null; bb_lower: number | null; bb_mid: number | null }[]): { width: number; percentile: number } | null {
  const widths = bars.map((b) => (b.bb_upper != null && b.bb_lower != null && b.bb_mid ? (b.bb_upper - b.bb_lower) / b.bb_mid : null)).filter((w): w is number => w !== null);
  if (widths.length < 20) return null;
  const last = widths[widths.length - 1];
  return { width: last, percentile: (100 * widths.filter((w) => w <= last).length) / widths.length };
}
