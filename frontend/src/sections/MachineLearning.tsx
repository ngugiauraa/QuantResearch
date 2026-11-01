"use client";
import { useState } from "react";
import { api, fmt, pct, useFetch, waitForJob, when } from "@/lib/api";
import { Card, ErrorBox, Loading, Notice, Pill, Stat, Table } from "@/components/ui";
import { ValueChart } from "@/components/PriceChart";

export function MachineLearning() {
  const models = useFetch("/api/ml/models");
  const live = useFetch("/api/ml/inference");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const train = async () => {
    setError(null);
    setStatus("Training with walk-forward validation. This can take several minutes.");
    try {
      const { job } = await api("/api/ml/train", { method: "POST" });
      const done = await waitForJob(job);
      if (done.status === "failed") setError(done.error);
      else setStatus(`Finished. Best: ${done.result.best}. ${done.result.beats_baseline ? "Beat the constant baseline." : "Did NOT beat the constant baseline."} ${done.result.promoted ? "Promoted." : "Not promoted."}`);
      await Promise.all([models.reload(), live.reload()]);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const promote = async (id: number) => {
    setError(null);
    try {
      await api(`/api/ml/promote/${id}`, { method: "POST" });
      await Promise.all([models.reload(), live.reload()]);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const d = live.data;
  const latest = d?.latest;
  const rows = models.data?.models ?? [];
  return (
    <>
      <h1>Machine Learning</h1>
      <ErrorBox error={error ?? models.error ?? live.error} />
      <div className="row">
        <button className="btn primary" onClick={train}>Train and validate</button>
        {status && <span className="warn">{status}</span>}
      </div>
      {d?.available && d?.note && <Notice>{d.note}{d.is_promoted ? "" : " This is the newest model, which has not been promoted: it did not earn that status."}</Notice>}
      {live.loading ? <Loading /> : d?.available ? (
        <>
          <div className="grid">
            <Stat label="Model" value={d.model} />
            <Stat label="Probability up" value={pct(latest?.prob_up, 1)} />
            <Stat label="Confidence" value={pct(latest?.confidence, 1)} />
            <Stat label="Stance" value={latest?.abstain ? `Abstains: ${latest.abstain_reason}` : "Has a view"} tone={latest?.abstain ? "warn" : undefined} />
            <Stat label="Regime" value={d.regime ? `${d.regime.regime} (model fit ${d.regime.confidence > 0.99 ? ">99" : Math.round(d.regime.confidence * 100)}%)` : "–"} />
            <Stat label="Trained through" value={when(d.train_end)} />
          </div>
          <Card title="Recent probabilities">
            <ValueChart points={(d.predictions ?? []).map((p: any) => ({ time: Math.floor(new Date(p.time).getTime() / 1000), value: p.prob_up }))} />
          </Card>
          <Card title="Input drift against training">
            <div className="row">{d.drift?.retrain_suggested ? <Pill tone="warn">Retraining suggested</Pill> : <Pill tone="ok">Inputs within range</Pill>} <span className="muted">{pct(d.drift?.share_drifted, 0)} of inputs drifted.</span></div>
            <Table columns={["Input", "PSI"]} rows={Object.entries(d.drift?.psi ?? {}).sort((a: any, b: any) => b[1] - a[1]).slice(0, 8).map(([k, v]: any) => [k, fmt(v, 3)])} />
          </Card>
        </>
      ) : <div className="muted">{d?.note}</div>}
      <Card title="Model registry">
        <Table columns={["ID", "Created", "Model", "Dataset", "Trained through", "Log loss", "Baseline log loss", "AUC", "Beat baseline", ""]} empty="No models yet." rows={rows.map((m: any) => {
          const wf = m.metrics.walk_forward ?? {};
          return [m.id, when(m.created_at), m.name, m.dataset_version, when(m.train_end), fmt(wf[m.name]?.log_loss, 4), fmt(wf.baseline_constant?.log_loss, 4), fmt(wf[m.name]?.auc, 3),
            m.beats_baseline ? <Pill key="b" tone="ok">Yes</Pill> : <Pill key="b" tone="bad">No</Pill>,
            m.promoted ? <Pill key="p" tone="ok">Promoted</Pill> : <button key="p" className="btn" disabled={!m.beats_baseline} onClick={() => promote(m.id)}>Promote</button>];
        })} />
        <div className="muted" style={{ marginTop: 8 }}>Only a model that beat the constant baseline out of sample can be promoted. ML can veto a strategy idea, but it cannot start a trade or bypass the risk engine.</div>
      </Card>
    </>
  );
}
