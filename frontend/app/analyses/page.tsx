"use client";

import { useWorkspace } from "@/components/app-frame";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { StatusBadge } from "@/components/ui/glass";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Result, type Answer } from "@/components/analysis-result";
import { api, ApiError, type Organization } from "@/lib/workspace-api";

type Planned = { id: number; domain: string; metric_id: string | null; depends_on: number[] };
type TaskResult = Omit<Answer, "query_id"> & {
  attempts?: number; latency_ms?: number; cost_units?: number;
  comparison?: { start: string; end: string; value: string | number | null };
};
type Entry = { id: string; question: string; status: string; created_at: string };
type Analysis = Entry & {
  correlation_id: string; plan?: { tasks: Planned[] }; tasks: Record<string, TaskResult>;
  planner_tokens: number; business_day: string;
  data_quality?: { message: string; scope: string };
  summary?: { message: string; findings?: string[]; recommendations?: string[]; warnings?: string[] };
};

const BOX = "glass-card min-w-0";
const BUTTON = "glass-button glass-primary";
const ROOT = "/ai/analyses";
const suggested = [
  "Compare October 2026 net revenue with September, list overdue customers, and identify low-stock products.",
  "Show order counts by branch and outstanding balances by customer as of October 31, 2026.",
  "Show net revenue by day, stock on hand, and whether forecasts are available.",
];
const pending = (state: string) => state === "queued" || state === "running";
const label = (value: string) => value.replaceAll("_", " ");

function Workspace({ org }: { org: Organization }) {
  const scope = "?organization_id=" + org.id;
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Entry[]>();
  const [analysis, setAnalysis] = useState<Analysis>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef<{ question: string; key: string } | undefined>(undefined);
  const router = useRouter();
  const allowed = org.role !== "viewer";
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const rows = await api<Entry[]>(ROOT + scope, { signal });
    if (!signal?.aborted) setHistory(rows);
  }, [scope]);

  useEffect(() => {
    if (!allowed) return;
    const controller = new AbortController();
    void refresh(controller.signal).catch((failure: Error) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [refresh, allowed]);

  const id = analysis?.id;
  const running = analysis ? pending(analysis.status) : false;
  useEffect(() => {
    if (!id || !running) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<Analysis>(ROOT + "/" + id + scope, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setAnalysis((item) => item?.id === value.id && pending(item.status) ? value : item);
        if (pending(value.status)) timer = setTimeout(poll, 1100);
        else await refresh(controller.signal);
      } catch (failure) {
        if (!controller.signal.aborted) {
          setError(failure instanceof Error ? failure.message : "Unable to check progress.");
          if (failure instanceof ApiError && failure.status === 401) router.replace("/login");
        }
      }
    }
    timer = setTimeout(poll, 700);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [id, running, refresh, router, scope]);

  async function perform(operation: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await operation(); }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) router.replace("/login");
      else setError(failure instanceof Error ? failure.message : "Unable to complete the action.");
    } finally { setBusy(false); }
  }

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitted = question.trim();
    if (submitted.length < 3) return;
    if (request.current?.question !== submitted) request.current = { question: submitted, key: crypto.randomUUID() };
    const key = request.current.key;
    void perform(async () => {
      const result = await api<Analysis>(ROOT, {
        method: "POST", body: JSON.stringify({ organization_id: org.id, question: submitted, request_key: key }),
      });
      setAnalysis(result);
      request.current = undefined;
      await refresh();
    });
  }

  if (!allowed) return <p className={BOX}>Multi-domain analyses require an analyst or administrator role.</p>;
  return <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
    <section className="grid min-w-0 content-start gap-4">
      <form className={BOX + " ai-composer grid gap-3"} onSubmit={start}>
        <label className="font-semibold" htmlFor="analysis-question">Business question</label>
        <textarea id="analysis-question" className="glass-input"
          rows={3} minLength={3} maxLength={500} required value={question} onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about sales, receivables and inventory together" />
        <p className="text-xs text-slate-600">Questions are sent to the configured AI provider. Keep confidential details out of the text.</p>
        <div className="flex flex-wrap gap-2">
          {suggested.map((item) => <button type="button" key={item} onClick={() => setQuestion(item)}
            className="rounded-full border border-slate-300 px-3 py-1 text-left text-xs hover:bg-slate-100">{item}</button>)}
        </div>
        <button className={BUTTON + " justify-self-start"} type="submit" disabled={busy}>Run analysis</button>
      </form>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      {analysis && <>
        <section className={BOX + " grid gap-3"}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p role="status" className="text-sm font-semibold capitalize text-teal-800">{label(analysis.status)}</p>
              <h2 className="mt-1 break-words text-lg font-semibold">{analysis.question}</h2>
              <p className="text-sm text-slate-600">Business date {analysis.business_day} · Asia/Karachi</p>
            </div>
            {running && <button type="button" className="rounded-md border border-slate-300 px-3 py-2 text-sm" disabled={busy}
              onClick={() => void perform(async () => {
                setAnalysis(await api<Analysis>(ROOT + "/" + analysis.id + "/cancel" + scope, { method: "POST" }));
                await refresh();
              })}>Cancel analysis</button>}
          </div>
          {analysis.summary && <div>
            <h3 className="font-semibold">Executive summary</h3>
            <p className="mt-1 text-sm">{analysis.summary.message}</p>
            {!!analysis.summary.findings?.length && <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
              {analysis.summary.findings.map((item, index) => <li key={index}>{item}</li>)}
            </ul>}
            {!!analysis.summary.warnings?.length && <ul className="mt-2 space-y-1 rounded bg-amber-50 p-2 text-sm text-amber-900">
              {analysis.summary.warnings.map((item, index) => <li key={index}>{item}</li>)}
            </ul>}
            {!!analysis.summary.recommendations?.length && <div className="mt-3">
              <h3 className="font-semibold">Recommended follow-ups</h3>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                {analysis.summary.recommendations.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </div>}
          </div>}
          {analysis.data_quality && <p className="rounded bg-amber-50 p-2 text-sm text-amber-900">{analysis.data_quality.message}
            {" "}<Link href="/reconciliation" className="font-semibold underline">Review data quality</Link></p>}
          {running && !analysis.plan && <p className="text-sm text-slate-600">Waiting for the task plan…</p>}
          {analysis.plan?.tasks && <ol className="grid gap-1 text-sm sm:grid-cols-2">
            {analysis.plan.tasks.map((task) => <li className="rounded bg-slate-50 px-3 py-2 capitalize" key={task.id}>
              {task.domain} <StatusBadge status={analysis.tasks[task.id]?.status ?? (running ? "queued" : "incomplete")} />
            </li>)}
          </ol>}
          <details className="break-words text-sm">
            <summary className="cursor-pointer font-medium">Analysis trace</summary>
            <p className="mt-2">Correlation ID {analysis.correlation_id} · {analysis.planner_tokens} planner tokens</p>
          </details>
        </section>
        {analysis.plan?.tasks.map((task) => {
          const value = analysis.tasks[task.id];
          if (!value) return null;
          return <div className="min-w-0" key={task.id}>
            <Result answer={{ ...value, question: task.domain[0].toUpperCase() + task.domain.slice(1),
              query_id: analysis.id + "/" + task.id }} referenceLabel="Task reference" />
            {value.comparison && <p className="px-4 py-2 text-sm text-slate-600">
              Preceding month {value.comparison.start} to {value.comparison.end} (exclusive) · {value.comparison.value ?? "Unavailable"}
            </p>}
            {value.latency_ms !== undefined && <p className="px-4 py-1 text-xs text-slate-600">
              {value.latency_ms} ms · {value.attempts ?? 0} execution attempts
            </p>}
          </div>;
        })}
      </>}
    </section>
    <aside className={BOX + " self-start"}>
      <h2 className="font-semibold">Your analysis history</h2>
      {!history && !error && <p className="mt-2 text-sm" role="status">Loading history…</p>}
      {history?.length === 0 && <p className="mt-2 text-sm text-slate-600">No analyses yet.</p>}
      <ul className="mt-2 divide-y divide-slate-100">
        {history?.map((item) => <li key={item.id}>
          <button type="button" aria-pressed={analysis?.id === item.id} disabled={busy}
            className="w-full break-words rounded px-2 py-2 text-left text-sm hover:bg-teal-50"
            onClick={() => void perform(async () => setAnalysis(await api<Analysis>(ROOT + "/" + item.id + scope)))}>
            {item.question}<span className="block text-xs capitalize text-slate-600">{label(item.status)}</span>
          </button>
        </li>)}
      </ul>
    </aside>
  </div>;
}

export default function AnalysesPage() {
  const { identity, orgId } = useWorkspace();
  const org = identity.organizations.find((item) => item.id === orgId);
  return <main className="module-page ai-workspace">
    <header className="module-header"><h1>Multi-domain analysis</h1></header>
    {org ? <Workspace org={org} key={org.id} /> : <p className="glass-card">Create or join an organization in <Link href="/workspace/team" className="underline">Team &amp; settings</Link>.</p>}
  </main>;
}
