"use client";
import { useCallback, useEffect, useState } from "react";
import { Overview } from "@/sections/Overview";
import { Charts } from "@/sections/Charts";
import { TradingView } from "@/sections/TradingView";
import { Intelligence } from "@/sections/Intelligence";
import { Research } from "@/sections/Research";
import { Discovery } from "@/sections/Discovery";
import { MachineLearning } from "@/sections/MachineLearning";
import { TimeMachine } from "@/sections/TimeMachine";
import { Skills } from "@/sections/Skills";
import { Terminal } from "@/sections/Terminal";
import { Risk } from "@/sections/Risk";
import { Performance } from "@/sections/Performance";
import { GROUPS, Sidebar, TopBar, useShellData } from "@/components/Shell";

const SECTIONS: Record<string, () => React.JSX.Element> = {
  overview: Overview, charts: Charts, tradingview: TradingView, intel: Intelligence, research: Research, discovery: Discovery, ml: MachineLearning,
  timemachine: TimeMachine, skills: Skills, terminal: Terminal, risk: Risk, performance: Performance,
};
const LABELS: Record<string, string> = Object.fromEntries(GROUPS.flatMap((g) => g.items.map((i) => [i.key, i.label])));

export default function Home() {
  const [active, setActive] = useState("overview");
  const [collapsed, setCollapsed] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const { overview, health, terminal } = useShellData();
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (hash in SECTIONS) setActive(hash);
    setCollapsed(localStorage.getItem("qr-sidebar") === "1");
  }, []);
  const open = (key: string) => {
    setActive(key);
    setDrawerOpen(false);
    window.history.replaceState(null, "", `#${key}`);
  };
  const closeDrawer = useCallback(() => setDrawerOpen(false), []);
  const toggle = () => {
    setCollapsed((c) => {
      localStorage.setItem("qr-sidebar", c ? "0" : "1");
      return !c;
    });
  };
  const Section = SECTIONS[active];
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <Sidebar active={active} onOpen={open} collapsed={collapsed} onToggle={toggle} health={health} drawerOpen={drawerOpen} onCloseDrawer={closeDrawer} />
      <div className="min-w-0 flex-1">
        <TopBar title={LABELS[active]} overview={overview} terminal={terminal} health={health} onMenu={() => setDrawerOpen(true)} menuOpen={drawerOpen} />
        <main className="px-4 pb-12 pt-5 md:px-6"><Section /></main>
      </div>
    </div>
  );
}
