"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Result, type Answer } from "@/components/analysis-result";
import { type FormEvent, useEffect, useState } from "react";
import { api, ApiError, type Identity, type Organization } from "@/lib/workspace-api";


type Entry = { query_id: string; question: string; created_at: string; status: string };
const endpoint = "/ai/queries";
const surface = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";
const action = "rounded-md bg-teal-800 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-900 disabled:opacity-50";
const suggestions = [
  "What was net revenue in October 2026?",
  "Stock on hand by SKU as of October 31, 2026",
  "Show receivables ageing as of October 31, 2026",
];
const label = (value: string) => value.replaceAll("_", " ");

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
        <nav className="flex gap-4 text-sm font-semibold text-teal-800" aria-label="Workspace navigation">
          <Link href="/analytics" className="hover:underline">Business analytics</Link>
          <Link href="/reports" className="hover:underline">Reports</Link>
          <Link href="/evaluations" className="hover:underline">AI Quality</Link>
          <Link href="/analyses" className="hover:underline">Multi-domain analysis</Link>
        </nav>
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
