"use client";

import { useWorkspace } from "@/components/app-frame";

import Link from "next/link";
import { Sparkles } from "lucide-react";
import { LoadingState, StatusBadge } from "@/components/ui/glass";
import { Result, type Answer } from "@/components/analysis-result";
import { type FormEvent, useEffect, useState } from "react";
import { api, type Organization } from "@/lib/workspace-api";


type Entry = { query_id: string; question: string; created_at: string; status: string };
const endpoint = "/ai/queries";
const surface = "glass-card";
const action = "glass-button glass-primary";
const suggestions = [
  "What was net revenue in October 2026?",
  "Stock on hand by SKU as of October 31, 2026",
  "Show receivables ageing as of October 31, 2026",
];

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
      <form onSubmit={submit} className={surface + " ai-composer grid gap-4"}>
        <label htmlFor="question" className="flex items-center gap-2 font-semibold"><Sparkles size={19} aria-hidden /> Business question</label>
        <textarea id="question" rows={3} value={question} onChange={(event) => setQuestion(event.target.value)}
          minLength={3} maxLength={500} required placeholder="Ask about sales, inventory, or receivables"
          className="glass-input" />
        <p className="text-xs text-slate-600">Keep confidential details out of questions. Questions are sent to the configured AI provider.</p>
        <div className="flex flex-wrap gap-2">
          {suggestions.map((item) => <button key={item} type="button" onClick={() => setQuestion(item)}
            className="rounded-full border border-slate-300 px-3 py-1 text-left text-xs hover:bg-slate-100">{item}</button>)}
        </div>
        <button type="submit" className={action + " justify-self-start"} disabled={pending}>Ask</button>
      </form>
      {pending && <LoadingState label="Running governed analysis…" />}
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
            <span className="block text-xs capitalize text-slate-600"><StatusBadge status={entry.status} /></span>
          </button>
        </li>)}
      </ul>
    </aside>
  </div>;
}

export default function AskPage() {
  const { identity, orgId } = useWorkspace();
  const org = identity.organizations.find((item) => item.id === orgId);
  return <main className="module-page ai-workspace">
    <header className="module-header"><h1>Ask AI</h1></header>
    {org ? <AskOrganization key={org.id} org={org} /> : <p className="glass-card">Create or join an organization in <Link href="/workspace/team" className="underline">Team &amp; settings</Link>.</p>}
  </main>;
}
