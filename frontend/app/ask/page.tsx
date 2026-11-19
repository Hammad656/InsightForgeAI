"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { api, ApiError, type Identity, type Organization } from "@/lib/workspace-api";

type Cell = string | number | null;
type Entry = { query_id: string; question: string; created_at: string; status: string };
type Answer = {
  query_id: string;
  status: string;
  answer: string;
  question?: string;
  result?: { columns: string[]; rows: Record<string, Cell>[] };
  visualization?: { type: string; x: string; y: string };
  evidence?: {
    metric_id: string; metric_version: number; unit: string; dataset_versions: string[];
    published_at: string; generated_at: string; filters: Record<string, Cell>;
    query_hash: string; row_count: number; timezone: string; warnings: string[];
  };
  usage?: { latency_ms: number; llm_tokens: number };
};

const endpoint = "/ai/queries";
const surface = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";
const action = "rounded-md bg-teal-800 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-900 disabled:opacity-50";
const suggestions = [
  "What was net revenue in October 2026?",
  "Stock on hand by SKU as of October 31, 2026",
  "Show receivables ageing as of October 31, 2026",
];
const label = (value: string) => value.replaceAll("_", " ");

function Result({ answer }: { answer: Answer }) {
  const [selected, setSelected] = useState<number>();
  const chart = answer.visualization;
  const rows = answer.result?.rows ?? [];
  const columns = answer.result?.columns ?? [];
  const max = chart ? Math.max(1, ...rows.map((row) => Math.abs(Number(row[chart.y])))) : 1;
  return <article className={surface + " grid gap-4"}>
    <div>
      <p className="text-sm font-medium capitalize text-teal-800">{label(answer.status)}</p>
      <h2 className="mt-1 text-lg font-semibold">{answer.question ?? "Analysis result"}</h2>
      <p className="mt-2 text-slate-700">{answer.answer}</p>
    </div>
    {chart && chart.type !== "table" && rows.length > 0 && <section aria-label="Results chart">
      <h3 className="mb-2 font-semibold capitalize">{label(chart.y)} by {label(chart.x)}</h3>
      <ul className="grid max-h-64 gap-1 overflow-auto">
        {rows.map((row, index) => <li key={index}>
          <button type="button" aria-pressed={selected === index}
            onClick={() => setSelected(selected === index ? undefined : index)}
            className="grid w-full grid-cols-[minmax(4rem,8rem)_1fr_auto] items-center gap-3 rounded px-2 py-1 text-left text-sm hover:bg-teal-50 aria-pressed:bg-teal-100">
            <span className="truncate" title={String(row[chart.x])}>{row[chart.x]}</span>
            <span className="h-2 rounded bg-slate-100" aria-hidden="true">
              <span className="block h-full rounded bg-teal-700" style={{ width: Math.abs(Number(row[chart.y])) / max * 100 + "%" }} />
            </span>
            <span className="tabular-nums">{row[chart.y] ?? "Unavailable"}</span>
          </button>
        </li>)}
      </ul>
    </section>}
    {rows.length > 0 && <div className="max-h-80 overflow-auto">
      <table aria-label="Verified results" className="w-full text-left text-sm">
        <thead className="sticky top-0 bg-slate-100"><tr>
          {columns.map((column) => <th scope="col" className="p-2 capitalize" key={column}>{label(column)}</th>)}
        </tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index} className={selected === index ? "bg-teal-100" : "border-b border-slate-100"}>
          {columns.map((column) => <td className="break-all p-2 tabular-nums" key={column}>{row[column] ?? "Unavailable"}</td>)}
        </tr>)}</tbody>
      </table>
    </div>}
    <details className="rounded-lg border border-slate-200 p-4">
      <summary className="cursor-pointer font-semibold">Evidence and usage</summary>
      <dl className="mt-3 grid gap-3 break-words text-sm sm:grid-cols-2">
        <div><dt className="font-medium">Query ID</dt><dd>{answer.query_id}</dd></div>
        {answer.usage && <div><dt className="font-medium">Processing</dt><dd>{answer.usage.latency_ms} ms · {answer.usage.llm_tokens} tokens</dd></div>}
        {answer.evidence && <>
          <div><dt className="font-medium">Metric</dt><dd>{label(answer.evidence.metric_id)} · v{answer.evidence.metric_version} · {answer.evidence.unit}</dd></div>
          <div><dt className="font-medium">Source version</dt><dd>{answer.evidence.dataset_versions.join(", ")}</dd></div>
          <div><dt className="font-medium">Published</dt><dd>{answer.evidence.published_at}</dd></div>
          <div><dt className="font-medium">Executed</dt><dd>{answer.evidence.generated_at}</dd></div>
          <div><dt className="font-medium">Filters · {answer.evidence.timezone}</dt><dd>
            {Object.entries(answer.evidence.filters).filter(([, value]) => value !== null)
              .map(([key, value]) => <span className="mr-2 inline-block" key={key}>{label(key)}: {value}</span>)}
          </dd></div>
          <div><dt className="font-medium">Query hash</dt><dd>{answer.evidence.query_hash}</dd></div>
          <div className="sm:col-span-2"><dt className="font-medium">Source coverage</dt><dd>{answer.evidence.warnings.join(" ")}</dd></div>
        </>}
      </dl>
    </details>
  </article>;
}

function AskOrganization({ org }: { org: Organization }) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<Entry[]>();
  const [answer, setAnswer] = useState<Answer>();
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [pending, setPending] = useState(false);
  const scope = "?organization_id=" + org.id;
  useEffect(() => {
    let active = true;
    api<Entry[]>(endpoint + scope).then((entries) => { if (active) setHistory(entries); })
      .catch((failure: Error) => { if (active) setHistoryError(failure.message); });
    return () => { active = false; };
  }, [scope]);

  async function run(action: () => Promise<Answer>) {
    setPending(true);
    setError("");
    try { setAnswer(await action()); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to run the analysis."); }
    finally { setPending(false); }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submitted = question.trim();
    if (submitted.length < 3) return;
    void run(async () => {
      const result = await api<Answer>(endpoint, {
        method: "POST", body: JSON.stringify({ organization_id: org.id, question: submitted }),
      });
      setHistory((entries) => [{
        query_id: result.query_id, status: result.status, question: submitted, created_at: new Date().toISOString(),
      }, ...(entries ?? [])].slice(0, 20));
      return { ...result, question: submitted };
    });
  }

  return <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_18rem]">
    <section className="grid min-w-0 content-start gap-4">
      <form onSubmit={submit} className={surface + " grid gap-3"}>
        <label htmlFor="question" className="font-semibold">Business question</label>
        <textarea id="question" rows={3} value={question} onChange={(event) => setQuestion(event.target.value)}
          minLength={3} maxLength={500} required placeholder="Ask about sales, inventory, or receivables"
          className="rounded-md border border-slate-300 px-3 py-2" />
        <p className="text-xs text-slate-600">Keep confidential details out of questions. Questions are sent to the configured AI provider.</p>
        <div className="flex flex-wrap gap-2">
          {suggestions.map((item) => <button key={item} type="button" onClick={() => setQuestion(item)}
            className="rounded-full border border-slate-300 px-3 py-1 text-left text-xs hover:bg-slate-100">{item}</button>)}
        </div>
        <button type="submit" className={action + " justify-self-start"} disabled={pending}>Ask</button>
      </form>
      {pending && <p role="status" className="text-sm text-slate-600">Loading analysis…</p>}
      {error && <p role="alert" className="text-red-700">{error}</p>}
      {answer && !pending && <Result answer={answer} key={answer.query_id} />}
    </section>
    <aside className={surface + " self-start"}>
      <h2 className="font-semibold">Your query history</h2>
      {historyError && <p className="mt-2 text-sm text-red-700" role="alert">{historyError}</p>}
      {!history && !historyError && <p className="mt-2 text-sm text-slate-600">Loading history…</p>}
      {history?.length === 0 && <p className="mt-2 text-sm text-slate-600">No queries yet.</p>}
      <ul className="mt-2 grid divide-y divide-slate-100">
        {history?.map((entry) => <li key={entry.query_id}>
          <button type="button" disabled={pending}
            className="w-full break-words rounded p-2 text-left text-sm hover:bg-teal-50 disabled:opacity-60"
            onClick={() => void run(() => api<Answer>(endpoint + "/" + entry.query_id + scope))}>
            {entry.question}
            <span className="block text-xs capitalize text-slate-600">{label(entry.status)}</span>
          </button>
        </li>)}
      </ul>
    </aside>
  </div>;
}

export default function AskPage() {
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity>();
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Identity>("/auth/me").then((data) => { if (active) setIdentity(data); }).catch((failure: Error) => {
      if (!active) return;
      if (failure instanceof ApiError && failure.status === 401) router.replace("/login");
      else setError(failure.message);
    });
    return () => { active = false; };
  }, [router]);
  const org = identity?.organizations.find((item) => item.id === selected) ?? identity?.organizations[0];
  return <main className="mx-auto grid max-w-6xl gap-5 px-4 py-8 sm:px-8">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <Link href="/analytics" className="text-sm font-semibold text-teal-800 hover:underline">Business analytics</Link>
        <h1 className="mt-1 text-3xl font-bold tracking-tight">Ask AI</h1>
      </div>
      {org && <label className="grid gap-1 text-sm">Organization
        <select value={org.id} onChange={(event) => setSelected(event.target.value)} className="rounded-md border border-slate-300 bg-white px-3 py-2">
          {identity?.organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}
    </header>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {!identity && !error && <p role="status">Loading workspace…</p>}
    {identity && !org && <p className={surface}>Create or join an organization in the <Link className="underline" href="/workspace">workspace</Link>.</p>}
    {org && <AskOrganization key={org.id} org={org} />}
  </main>;
}
