"use client";
import type { ReactNode } from "react";

export const Card = ({ title, children }: { title?: string; children: ReactNode }) => (
  <section className="card">
    {title && <h2>{title}</h2>}
    {children}
  </section>
);

export const Stat = ({ label, value, tone }: { label: string; value: ReactNode; tone?: "up" | "down" | "warn" }) => (
  <section className="card">
    <div className="label">{label}</div>
    <div className={`stat ${tone ?? ""}`}>{value}</div>
  </section>
);

export const Pill = ({ tone, children }: { tone?: "ok" | "bad" | "warn"; children: ReactNode }) => <span className={`pill ${tone ?? ""}`}>{children}</span>;

export const Loading = ({ what = "Loading" }: { what?: string }) => <div className="skeleton">{what}…</div>;
export const ErrorBox = ({ error }: { error: string | null }) => (error ? <div className="error" role="alert">{error}</div> : null);
export const Notice = ({ children }: { children: ReactNode }) => <div className="notice">{children}</div>;

export function Table({ columns, rows, empty = "Nothing to show." }: { columns: string[]; rows: ReactNode[][]; empty?: string }) {
  if (!rows.length) return <div className="muted">{empty}</div>;
  return (
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
