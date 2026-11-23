"use client";

import { useEffect, useState } from "react";
import { button, field, OrgPage, panel, primary } from "@/components/m6c-workspace";
import { api, type Organization } from "@/lib/workspace-api";

type Count = { passed: number; total: number };
type Scores = Record<string, Count> & {
  domains: Record<string, Count>; latency_p50_ms: number | null; latency_p95_ms: number | null;
  tokens: number; token_samples: number; estimated_cost_usd: number | null;
};
type Case = { id: string; domain: string; category: string; metric: string | null; passed: boolean;
  expected_status: string; actual_status: string; failure: string | null; latency_ms: number; estimated_cost_usd: number | null };
type Run = { id: string; suite: string; model: string; status: string; created_at: string; finished_at: string | null;
  recoverable?: boolean; scores?: Scores; results?: Case[]; error_code?: string | null;
  manifest: { prompt_version: string; semantic_version: number; mode: string; fixture: string } };
type Comparison = { paired: number; regressions: string[]; improvements: string[]; current_model: string;
  baseline_model: string; current_prompt: string; baseline_prompt: string };
const labels: Record<string, string> = {
  passed: "Overall", execution_valid: "Query execution", result_correct: "Result accuracy",
  metric_correct: "Metric selection", faithful: "Explanation faithfulness", decision_correct: "Safe decisions",
  tenant_isolated: "Tenant isolation", sql_denied: "SQL denials", no_unsupported_claim: "Grounded claims",
};

function count(value: Count) {
  return value.total ? value.passed + " / " + value.total : "No applicable cases";
}

function Quality({ organization }: { organization: Organization }) {
  const query = "?organization_id=" + organization.id;
  const path = "/evaluations/runs";
  const allowed = organization.role !== "viewer";
  const [runs, setRuns] = useState<Run[]>();
  const [baseline, setBaseline] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [active, setActive] = useState<Run>();
  const [against, setAgainst] = useState("");
  const [comparison, setComparison] = useState<Comparison>();
  const [onlyFailed, setOnlyFailed] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = runs?.some((run) => ["queued", "running"].includes(run.status)) ?? false;

  useEffect(() => {
    if (!allowed) return;
    let current = true;
    const load = async () => {
      try {
        const data = await api<{ runs: Run[]; baseline_id: string | null }>(path + query);
        if (!current) return;
        setRuns(data.runs); setBaseline(data.baseline_id);
        if (selected) {
          const run = await api<Run>(path + "/" + selected + query);
          if (current) setActive(run);
        }
      } catch (e) {
        if (current) setError(e instanceof Error ? e.message : "Could not load evaluations.");
      }
    };
    void load();
    const timer = pending ? setInterval(() => void load(), 2000) : undefined;
    return () => { current = false; if (timer) clearInterval(timer); };
  }, [allowed, query, selected, pending]);
  const manage = async (operation: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await operation(); }
    catch (e) { setError(e instanceof Error ? e.message : "Evaluation request failed."); }
    finally { setBusy(false); }
  };
  const create = () => void manage(async () => {
    const result = await api<{ id: string }>(path + query, {
      method: "POST", body: JSON.stringify({ request_key: crypto.randomUUID(), suite: "retail-holdout-v1" }),
    });
    setComparison(undefined); setActive(undefined); setSelected(result.id);
  });
  if (!allowed) return <p className={panel}>An analyst or administrator is required to access AI Quality.</p>;
  const current = active?.id === selected ? active : undefined;
  const scores = current?.scores;
  const recent = runs?.find((run) => run.id === selected);
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="max-w-2xl text-sm text-slate-600">Offline benchmark runs use held-out synthetic data and replayed model responses. Latency measures the application path.</p>
      <button className={primary} disabled={busy} onClick={create}>Run offline benchmark</button>
    </div>
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-800">{error}</p>}
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(14rem,1fr)_minmax(0,3fr)]">
      <aside className={panel + " space-y-3"}>
        <h2 className="font-semibold">Run history</h2>
        {!runs && <p role="status">Loading runs...</p>}
        {runs?.length === 0 && <p className="text-sm text-slate-600">Run the benchmark to see results here.</p>}
        <ul className="space-y-2">{runs?.map((run) => <li key={run.id}>
          <button className={button + " w-full text-left"} aria-current={run.id === selected}
            onClick={() => { setComparison(undefined); setAgainst(""); setSelected(run.id); }}>
            {new Date(run.created_at).toLocaleString()} <span className="capitalize">· {run.status}</span>
            {baseline === run.id && <span className="block text-teal-800">Pinned baseline</span>}
          </button>
        </li>)}</ul>
      </aside>
      <section className="min-w-0 space-y-4" aria-label="Evaluation results">
        {!selected && <p className={panel}>Select an evaluation run to inspect its results.</p>}
        {selected && !current && !error && <p role="status">Loading results...</p>}
        {current && <div className={panel + " space-y-3"}>
          <h2 className="text-xl font-semibold capitalize">{current.status}</h2>
          <p className="text-sm font-semibold text-teal-800">{current.manifest.mode === "live_provider" ? "Live provider" : "Mocked offline"}</p>
          <p className="break-words text-sm">{current.model} · {current.manifest.prompt_version} · Semantic v{current.manifest.semantic_version} · {current.suite}</p>
          {current.error_code && <p role="alert">{current.error_code.replaceAll("_", " ")}</p>}
          {((current.status === "failed" && current.manifest.mode !== "live_provider") || recent?.recoverable) &&
            <button className={button} disabled={busy} onClick={() => void manage(async () => {
              await api(path + "/" + current.id + "/retry" + query, { method: "POST" });
              setActive({ ...current, status: "queued" });
              setRuns((items) => items?.map((run) => run.id === current.id ? { ...run, status: "queued" } : run));
            })}>Retry run</button>}
          {scores && <div className="flex flex-wrap gap-2">
            {organization.role === "admin" && <button className={button} disabled={busy || baseline === current.id} onClick={() => void manage(async () => {
              await api(path + "/" + current.id + "/baseline" + query, { method: "PUT" }); setBaseline(current.id);
            })}>Pin baseline</button>}
            <button className={button} disabled={busy} onClick={() => void manage(async () => {
              const data = await api<Run>(path + "/" + current.id + "/export" + query);
              const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
              const link = document.createElement("a"); link.href = url; link.download = "evaluation-run.json"; link.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            })}>Export authorized JSON</button>
          </div>}
        </div>}
        {scores && <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {Object.entries(labels).map(([key, label]) => <article key={key} className={panel}>
              <h3 className="text-sm font-semibold text-slate-600">{label}</h3><p className="text-xl font-bold tabular-nums">{count(scores[key])}</p>
            </article>)}
          </div>
          <div className={panel + " grid gap-4 md:grid-cols-2"}>
            <div><h3 className="font-semibold">Accuracy by domain</h3>
              <ul className="space-y-2">{Object.entries(scores.domains).map(([domain, score]) => <li key={domain} className="capitalize">
                <label className="flex justify-between text-sm">{domain}<span>{count(score)}</span></label>
                <progress className="h-2 w-full accent-teal-800" aria-label={domain} value={score.passed} max={score.total} />
              </li>)}</ul>
            </div>
            <div><h3 className="font-semibold">Latency and cost</h3>
              {[["p50", scores.latency_p50_ms], ["p95", scores.latency_p95_ms]].map(([label, value]) => <div className="mt-2" key={label}>
                <p className="text-sm">{label} · {value ?? "Unavailable"} ms</p>
                {typeof value === "number" && <progress aria-label={String(label) + " latency"} className="h-2 w-full accent-teal-800"
                  value={value} max={Math.max(1, scores.latency_p95_ms ?? 1)} />}
              </div>)}
              <p className="mt-2 text-sm">Recorded tokens {scores.tokens} across {scores.token_samples} cases</p>
              <p className="text-sm">Estimated cost {scores.estimated_cost_usd ?? "Unavailable for offline runs"}</p>
            </div>
          </div>
          <div className={panel + " space-y-2"}>
            <h3 className="font-semibold">Compare runs</h3>
            <label className="block text-sm">Baseline or prior run<select className={field} value={against} onChange={(e) => setAgainst(e.target.value)}>
              <option value="">Use pinned baseline</option>
              {runs?.filter((run) => run.status === "completed" && run.id !== current.id).map((run) => <option key={run.id} value={run.id}>{run.model} · {new Date(run.created_at).toLocaleString()}</option>)}
            </select></label>
            <button className={button} disabled={busy || !(against || baseline)} onClick={() => void manage(async () => {
              setComparison(await api<Comparison>(path + "/" + current.id + "/compare" + query + "&baseline_id=" + (against || baseline)));
            })}>Compare</button>
            {comparison && <div className="space-y-1 text-sm">
              <p>{comparison.paired} paired cases · {comparison.regressions.length} regressions · {comparison.improvements.length} improvements</p>
              <p>{comparison.current_model} ({comparison.current_prompt}) compared with {comparison.baseline_model} ({comparison.baseline_prompt})</p>
              {comparison.regressions.length > 0 && <p>Regressed cases {comparison.regressions.join(", ")}</p>}
            </div>}
          </div>
          <div className={panel + " space-y-2"}>
            <h3 className="font-semibold">Case explorer</h3>
            <label className="text-sm"><input type="checkbox" checked={onlyFailed} onChange={(e) => setOnlyFailed(e.target.checked)} /> Failed cases only</label>
            {(current.results ?? []).filter((item) => !onlyFailed || !item.passed).length === 0 ? <p className="text-sm">No cases match this filter.</p> :
              <div className="max-h-96 overflow-auto"><table aria-label="Evaluation cases" className="w-full text-left text-sm">
                <thead><tr>{["Case", "Domain and category", "Expected and observed", "Elapsed", "Est. cost"].map((label) => <th className="p-2" key={label} scope="col">{label}</th>)}</tr></thead>
                <tbody>{current.results?.filter((item) => !onlyFailed || !item.passed).map((item) => <tr key={item.id} className="border-t">
                  <td className="p-2">{item.id}</td><td className="p-2">{item.domain} · {item.category.replaceAll("_", " ")}</td>
                  <td className="p-2">{item.passed ? "Pass" : "Fail"} · {item.expected_status.replaceAll("_", " ")} → {item.actual_status.replaceAll("_", " ")}{item.failure ? " · " + item.failure.replaceAll("_", " ") : ""}</td>
                  <td className="p-2 tabular-nums">{item.latency_ms} ms</td>
                  <td className="p-2 tabular-nums">{item.estimated_cost_usd == null ? "Unavailable" : "$" + item.estimated_cost_usd.toFixed(6)}</td>
                </tr>)}</tbody>
              </table></div>}
          </div>
        </>}
      </section>
    </div>
  </div>;
}

export default function EvaluationsPage() {
  return <OrgPage title="AI Quality">{(org) => <Quality key={org.id} organization={org} />}</OrgPage>;
}
