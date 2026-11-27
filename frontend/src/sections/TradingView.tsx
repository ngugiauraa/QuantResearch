"use client";
import { useEffect, useRef } from "react";
import { Notice } from "@/components/ui";

/**
 * TradingView's free embeddable "Advanced Real-Time Chart" widget (tradingview.com/widget-docs), loaded from
 * TradingView's own script. It is NOT the Advanced Charts / Trading Platform library, whose licence excludes
 * private and personal use. The widget draws TradingView's data; nothing here reads or writes this project's API.
 */
const CONFIG = {
  autosize: true,
  symbol: "OANDA:XAUUSD",
  interval: "60",
  timezone: "Etc/UTC",
  theme: "dark",
  style: "1",
  locale: "en",
  backgroundColor: "#111118",
  allow_symbol_change: true,
  hide_side_toolbar: false, // Drawing tools.
  hide_top_toolbar: false, // Interval, chart type and Indicators menus.
  withdateranges: true,
  save_image: true,
  calendar: false,
  details: false,
  hotlist: false,
  support_host: "https://www.tradingview.com",
};

export function TradingView() {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = box.current;
    if (!host) return;
    host.innerHTML = '<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>';
    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.type = "text/javascript";
    script.async = true;
    script.innerHTML = JSON.stringify(CONFIG);
    host.appendChild(script);
    return () => {
      host.innerHTML = "";
    };
  }, []);
  return (
    <>
      <h1>TradingView</h1>
      <Notice>This chart uses TradingView&apos;s data (OANDA:XAUUSD by default), not the Exness feed this platform trades on. Prices, spreads and candle times can differ from Market Charts. Drawings and indicators stay in TradingView&apos;s widget and are not used by this project.</Notice>
      {/* The widget sets its container to height 100%, so the fixed height lives on this wrapper. */}
      <div style={{ height: "max(480px, calc(100vh - 220px))", width: "100%" }}>
        <div className="tradingview-widget-container" ref={box} style={{ height: "100%", width: "100%" }} />
      </div>
      <div className="muted" style={{ marginTop: 6, fontSize: 11 }}>
        <a href="https://www.tradingview.com/" rel="noopener nofollow" target="_blank">Chart by TradingView</a>
      </div>
    </>
  );
}
