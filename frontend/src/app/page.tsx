"use client";
import { useEffect, useState } from "react";
import { Overview } from "@/sections/Overview";
import { Charts } from "@/sections/Charts";
import { Intelligence } from "@/sections/Intelligence";
import { Research } from "@/sections/Research";
import { Discovery } from "@/sections/Discovery";
import { MachineLearning } from "@/sections/MachineLearning";
import { TimeMachine } from "@/sections/TimeMachine";
import { Skills } from "@/sections/Skills";
import { Terminal } from "@/sections/Terminal";
import { Risk } from "@/sections/Risk";
import { Performance } from "@/sections/Performance";

const SECTIONS = [
  ["overview", "Overview", Overview], ["charts", "Market Charts", Charts], ["intel", "Market Intelligence", Intelligence],
  ["research", "Quantitative Research", Research], ["discovery", "Strategy Discovery", Discovery], ["ml", "Machine Learning", MachineLearning], ["timemachine", "Time Machine", TimeMachine],
  ["skills", "Trading Skills", Skills], ["terminal", "Trading Terminal", Terminal], ["risk", "Risk Management", Risk], ["performance", "Performance and Audit", Performance],
] as const;

export default function Home() {
  const [active, setActive] = useState("overview");
  useEffect(() => {
    const hash = window.location.hash.slice(1);
    if (SECTIONS.some(([k]) => k === hash)) setActive(hash);
  }, []);
  const open = (key: string) => {
    setActive(key);
    window.history.replaceState(null, "", `#${key}`);
  };
  const Section = SECTIONS.find(([k]) => k === active)![2];
  return (
    <div className="shell">
      <nav aria-label="Sections">
        <div className="brand">QuantResearch</div>
        {SECTIONS.map(([key, label]) => (
          <button key={key} className={active === key ? "active" : ""} onClick={() => open(key)}>{label}</button>
        ))}
      </nav>
      <main><Section /></main>
    </div>
  );
}
