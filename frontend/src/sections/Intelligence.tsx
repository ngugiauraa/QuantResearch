"use client";
import { useState } from "react";
import { api, fmt, pct, useFetch, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice, Pill, Table } from "@/components/ui";

export function Intelligence() {
  const macro = useFetch("/api/intel/macro");
  const cross = useFetch("/api/intel/cross");
  const calendar = useFetch("/api/intel/calendar");
  const news = useFetch("/api/intel/news");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = async () => {
    setBusy(true);
    setError(null);
    try {
      await api("/api/intel/news/refresh", { method: "POST" });
      await news.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <h1>Market Intelligence</h1>
      <ErrorBox error={macro.error ?? cross.error ?? calendar.error ?? news.error ?? error} />
      <Card title="Macro and positioning">
        {macro.loading ? <Loading /> : (
          <Table columns={["Series", "Description", "Latest", "Previous", "Observation date", "Publication time basis"]} rows={(macro.data?.series ?? []).map((s: any) => s.available
            ? [s.series, s.description, fmt(s.value), fmt(s.previous), when(s.as_of), s.basis === "assumed_lag" ? <Pill key="b" tone="warn">assumed lag</Pill> : s.basis]
            : [s.series, s.description, "No data", "–", "–", "–"])} />
        )}
        <div className="muted" style={{ marginTop: 8 }}>Macro levels come from FRED&apos;s latest revised data, so they are context, not first-release figures. No surprise measure is shown because no reliable free source was available.</div>
      </Card>
      <div className="grid wide">
        <Card title="Cross-market">
          {cross.loading ? <Loading /> : (
            <Table columns={["Market", "Last", "Approx. 5-day change", "10-day correlation with gold", "As of"]} rows={(cross.data?.symbols ?? []).map((s: any) => s.available
              ? [s.label, fmt(s.last, 3), pct(s.change_5d), fmt(s.corr_10d), when(s.as_of)] : [s.label, "No data", "–", "–", "–"])} />
          )}
        </Card>
        <Card title="Calendar">
          {calendar.loading ? <Loading /> : <Table columns={["Event", "Time"]} empty="No FOMC events in this window." rows={(calendar.data?.events ?? []).map((e: any) => [e.series, when(e.time)])} />}
          {(calendar.data?.gaps ?? []).map((g: string) => <Notice key={g}>{g}</Notice>)}
        </Card>
      </div>
      <Card title="News">
        <div className="row">
          <button className="btn" onClick={refresh} disabled={busy}>{busy ? "Fetching…" : "Fetch latest headlines"}</button>
          {news.data?.fetched_at && <span className="muted">Fetched {when(news.data.fetched_at)} from {news.data.source}. {news.data.note}</span>}
        </div>
        <Table columns={["Published", "Headline", "Source", "Tags"]} empty="No headlines yet. Fetch them above. The platform works without news." rows={(news.data?.items ?? []).map((n: any) => [
          when(n.published_at), <a key="l" href={n.url} target="_blank" rel="noreferrer" style={{ color: "inherit" }}>{n.title}</a>, n.domain, <span key="t">{n.gold_relevant && <Pill tone="ok">gold</Pill>} {(n.tags ?? []).join(", ")}</span>])} />
      </Card>
    </>
  );
}
