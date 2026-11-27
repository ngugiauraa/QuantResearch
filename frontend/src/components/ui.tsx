"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Inbox } from "lucide-react";

export const Card = ({ title, children, glow }: { title?: string; children: ReactNode; glow?: boolean }) => (
  <section className="card" style={glow ? { boxShadow: "0 0 24px rgba(124,58,237,0.22)", borderColor: "rgba(124,58,237,0.45)" } : undefined}>
    {title && <h2>{title}</h2>}
    {children}
  </section>
);

/** A number that flashes green or red for 300 ms when its value rises or falls. */
export function FlashNumber({ value, children, className = "" }: { value?: number | null; children: ReactNode; className?: string }) {
  const prev = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState("");
  useEffect(() => {
    if (typeof value === "number" && typeof prev.current === "number" && value !== prev.current) {
      setFlash(value > prev.current ? "flash-up" : "flash-down");
      const id = setTimeout(() => setFlash(""), 320);
      prev.current = value;
      return () => clearTimeout(id);
    }
    prev.current = value;
  }, [value]);
  return <span className={`${className} ${flash}`}>{children}</span>;
}

export const Stat = ({ label, value, tone, sub, numeric }: { label: string; value: ReactNode; tone?: "up" | "down" | "warn"; sub?: ReactNode; numeric?: number | null }) => (
  <section className="card">
    <div className="label">{label}</div>
    <div className={`stat ${tone ?? ""}`}><FlashNumber value={numeric}>{value}</FlashNumber></div>
    {sub && <div className="text-xs text-mute mt-1">{sub}</div>}
  </section>
);

export const Pill = ({ tone, children }: { tone?: "ok" | "bad" | "warn"; children: ReactNode }) => <span className={`pill ${tone ?? ""}`}>{children}</span>;

export const StatusDot = ({ tone = "ok", pulse = false }: { tone?: "ok" | "bad" | "warn" | "off"; pulse?: boolean }) => {
  const c = { ok: "bg-up", bad: "bg-down", warn: "bg-warn", off: "bg-mute" }[tone];
  return <span className={`inline-block h-2 w-2 rounded-full ${c} ${pulse ? "animate-pulse-slow" : ""}`} aria-hidden />;
};

export const Skeleton = ({ className = "h-6 w-full" }: { className?: string }) => <div className={`skel ${className}`} aria-hidden />;

export const Loading = ({ what = "Loading" }: { what?: string }) => (
  <div className="space-y-3" role="status" aria-label={what}>
    <Skeleton className="h-6 w-48" />
    <div className="grid"><Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
    <Skeleton className="h-56" />
    <span className="sr-only">{what}…</span>
  </div>
);

/** One consistent empty state: dark card, subtle grid, icon, one line, optional action. */
export function EmptyState({ icon, text, children, className = "" }: { icon?: ReactNode; text: string; children?: ReactNode; className?: string }) {
  return (
    <div className={`gridbg flex flex-col items-center justify-center gap-2 rounded-card border border-dashed border-line bg-surface2 px-4 py-8 text-center ${className}`}>
      <div className="text-mute">{icon ?? <Inbox size={22} />}</div>
      <div className="text-sm text-mute">{text}</div>
      {children}
    </div>
  );
}

export const ErrorBox = ({ error }: { error: string | null }) => (error ? <div className="error" role="alert">{error}</div> : null);
export const Notice = ({ children }: { children: ReactNode }) => <div className="notice">{children}</div>;

export function Table({ columns, rows, empty = "Nothing to show." }: { columns: string[]; rows: ReactNode[][]; empty?: string }) {
  if (!rows.length) return <div className="muted">{empty}</div>;
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const verdictTone = (label?: string): "ok" | "bad" | "warn" =>
  label === "survives_tests_needs_paper_trading" ? "ok" : label === "no_edge_found" ? "bad" : "warn";
export const verdictText: Record<string, string> = {
  survives_tests_needs_paper_trading: "Survives tests; paper trade next",
  weak_or_not_significant: "Weak or not significant",
  no_edge_found: "No edge found",
  insufficient_evidence: "Insufficient evidence",
};
