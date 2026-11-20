"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, type Identity, type Organization } from "@/lib/workspace-api";

type Source = { id: string; source: string; domain: string; revision: number; filename: string; row_count: number; created_at: string };
type Alias = { id: string; source: string; kind: string; source_key: string; canonical_key: string; revision: number };
type Comparison = { invoice: string; currency: string; amount: string; paid: string; balance: string; partial: boolean };
type Run = {
  id: string; actor_id?: string; as_of: string; status: string; created_at: string; issue_count?: number;
  summary?: { issue_count?: number; duplicate_count: number; distinct_transactions: number;
    comparison: Comparison[]; comparison_count: number; error?: string };
};
type Issue = {
  id: string; rule: string; severity: string; status: string; reviewer: string | null; expected: string | null;
  observed: string | null; refs: { version_id: string; source: string; row: number | null }[];
};
type Review = { id: string; status: string; previous_status: string; actor_id: string; note: string; correction_alias_id: string | null; created_at: string };
type Quality = { state: string; message: string; blocking: number; warnings: number; acknowledged: number };

const section = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";
const field = "min-w-0 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm";
const button = "rounded-md bg-teal-800 px-4 py-2 text-sm font-medium text-white hover:bg-teal-900 disabled:opacity-50";
const text = (name: string) => name.replaceAll("_", " ");

function QualityWorkspace({ organization, actorId }: { organization: Organization; actorId: string }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [quality, setQuality] = useState<Quality | null>(null);
  const [run, setRun] = useState<Run>();
  const [issues, setIssues] = useState<Issue[]>([]);
  const [offset, setOffset] = useState(0);
  const [comparisonPage, setComparisonPage] = useState<{ offset: number; rows: Comparison[] }>();
  const [reviewed, setReviewed] = useState<{ issue: string; entries: Review[] }>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const scope = "?organization_id=" + organization.id;
  const admin = organization.role === "admin";
  const reviewer = organization.role !== "viewer";

  const refresh = useCallback(async () => {
    const [sourceList, versionList, history, assessment] = await Promise.all([
      api<Source[]>("/reconciliation-sources" + scope), api<Alias[]>("/reconciliation-mappings" + scope),
      api<Run[]>("/reconciliation-runs" + scope), api<Quality | null>("/reconciliation-summary" + scope),
    ]);
    setSources(sourceList);
    setAliases(versionList);
    setRuns(history);
    setQuality(assessment);
  }, [scope]);

  useEffect(() => {
    void refresh().catch((failure: Error) => setError(failure.message));
  }, [refresh]);

  const selectRun = useCallback(async (id: string, page = 0) => {
    const root = "/reconciliation-runs/" + id;
    const [current, exceptions] = await Promise.all([
      api<Run>(root + scope), api<Issue[]>(root + "/issues" + scope + "&offset=" + page),
    ]);
    setRun(current);
    setIssues(exceptions);
    setOffset(page);
    setComparisonPage(undefined);
    if (current.status !== "queued") await refresh();
  }, [scope, refresh]);

  useEffect(() => {
    if (run?.status !== "queued") return;
    const timer = setTimeout(() => {
      void selectRun(run.id).catch((failure: Error) => setError(failure.message));
    }, 1800);
    return () => clearTimeout(timer);
  }, [run, selectRun]);

  async function perform(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await work(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to complete the action."); }
    finally { setBusy(false); }
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (!data.get("invoice_source")) data.delete("invoice_source");
    data.set("organization_id", organization.id);
    void perform(async () => {
      await api("/reconciliation-sources", { method: "POST", body: data });
      form.reset();
      await refresh();
    });
  }

  function mapping(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    void perform(async () => {
      await api("/reconciliation-mappings" + scope, {
        method: "POST", body: JSON.stringify({ ...data, expected_revision: Number(data.expected_revision) }),
      });
      form.reset();
      await refresh();
    });
  }

  function submitRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    void perform(async () => {
      const created = await api<Run>("/reconciliation-runs", { method: "POST", body: JSON.stringify({
        organization_id: organization.id, request_key: crypto.randomUUID(),
        input_ids: data.getAll("inputs"), as_of: data.get("as_of"),
      }) });
      await selectRun(created.id);
      await refresh();
    });
  }

  function review(event: FormEvent<HTMLFormElement>, issue: Issue) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    void perform(async () => {
      const root = "/reconciliation-issues/" + issue.id;
      const body = { ...data, reviewer: data.reviewer || null, correction_alias_id: data.correction_alias_id || null };
      await api(root + scope, { method: "PATCH", body: JSON.stringify(body) });
      form.reset();
      if (run) await selectRun(run.id, offset);
      setReviewed({ issue: issue.id, entries: await api<Review[]>(root + "/reviews" + scope) });
    });
  }

  return <>
    <section className={section}>
      <h2 className="font-semibold">Data quality overview</h2>
      <p className="mt-2 text-sm">{quality?.message ?? "Run reconciliation to compare independent sources."}</p>
      {quality && <p className="mt-1 text-sm text-slate-600">{quality.blocking} blocking · {quality.warnings} warnings · {quality.acknowledged} acknowledged</p>}
    </section>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {admin && <div className="grid gap-4 lg:grid-cols-2">
      <form className={section + " grid content-start gap-3"} onSubmit={upload}>
        <h2 className="font-semibold">Import source version</h2>
        <label className="grid gap-1 text-sm">Source namespace<input className={field} name="source" pattern="[a-z0-9][a-z0-9_\-]{0,39}" required /></label>
        <label className="grid gap-1 text-sm">Dataset domain<select className={field} name="domain">
          <option value="sales">Sales</option><option value="inventory">Inventory</option>
          <option value="invoices">Invoices</option><option value="payments">Payments</option>
        </select></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-sm">Default currency<input className={field} name="currency" defaultValue="PKR" pattern="[A-Z]{3}" required /></label>
          <label className="grid gap-1 text-sm">Tax basis<select className={field} name="tax_basis"><option value="gross">Tax included</option><option value="net">Tax added explicitly</option></select></label>
        </div>
        <label className="grid gap-1 text-sm">Invoice reference namespace<input className={field} name="invoice_source" maxLength={40} /></label>
        <label className="grid gap-1 text-sm">CSV or XLSX<input className={field} type="file" name="file" accept=".csv,.xlsx" required /></label>
        <button className={button + " justify-self-start"} disabled={busy}>Import source</button>
      </form>
      <form className={section + " grid content-start gap-3"} onSubmit={mapping}>
        <h2 className="font-semibold">Approve an identifier mapping</h2>
        <label className="grid gap-1 text-sm">Source namespace<input className={field} name="source" required maxLength={40} /></label>
        <label className="grid gap-1 text-sm">Entity type<select className={field} name="kind">
          {["customer", "product", "branch", "invoice", "payment", "transaction"].map((kind) => <option key={kind}>{kind}</option>)}
        </select></label>
        <label className="grid gap-1 text-sm">Source identifier<input className={field} name="source_key" required maxLength={120} /></label>
        <label className="grid gap-1 text-sm">Canonical identifier<input className={field} name="canonical_key" required maxLength={120} /></label>
        <label className="grid gap-1 text-sm">Expected revision (0 for a new mapping)<input className={field} name="expected_revision" type="number" min="0" defaultValue="0" required /></label>
        <button className={button + " justify-self-start"} disabled={busy}>Save mapping</button>
      </form>
    </div>}
    <form className={section + " grid gap-3"} onSubmit={submitRun}>
      <h2 className="font-semibold">Source versions and freshness</h2>
      {sources.length === 0 && <p className="text-sm text-slate-600">No source versions imported.</p>}
      <ul className="grid gap-2">
        {sources.map((source) => <li key={source.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 py-2 text-sm">
          <label className="flex items-center gap-2">
            {reviewer && <input type="checkbox" name="inputs" value={source.id} />}
            <span>{source.source} · {source.domain} · v{source.revision} · {source.filename}</span>
          </label>
          <span className="text-slate-600">{source.row_count} rows · {new Date(source.created_at).toLocaleString()}</span>
        </li>)}
      </ul>
      {reviewer && sources.length > 0 && <div className="flex flex-wrap items-end gap-3">
        <label className="grid gap-1 text-sm">As-of date<input className={field} type="date" name="as_of" required /></label>
        <button className={button} disabled={busy}>Run reconciliation</button>
      </div>}
    </form>
    <details className={section}>
      <summary className="cursor-pointer font-semibold">Mapping history</summary>
      <ul className="mt-3 max-h-72 space-y-2 overflow-auto text-sm">
        {aliases.map((alias) => <li key={alias.id} className="break-words">{alias.source} · {alias.kind} · {alias.source_key} → {alias.canonical_key} · v{alias.revision} · {alias.id}</li>)}
      </ul>
      {aliases.length === 0 && <p className="text-sm text-slate-600">No approved mappings.</p>}
    </details>
    <section className={section}>
      <h2 className="font-semibold">Reconciliation history</h2>
      {runs.length === 0 && <p className="mt-2 text-sm text-slate-600">No reconciliations yet.</p>}
      <ul className="mt-2 space-y-2 text-sm">
        {runs.map((item) => <li key={item.id}>
          <button type="button" aria-pressed={run?.id === item.id} disabled={busy} className="text-left text-teal-800 underline"
            onClick={() => void perform(async () => { setReviewed(undefined); await selectRun(item.id); })}>
            {item.as_of} · {item.status} · {new Date(item.created_at).toLocaleString()}
          </button>
        </li>)}
      </ul>
    </section>
    {run && <section className={section + " grid gap-4"}>
      <h2 className="font-semibold">Run detail · {run.as_of}</h2>
      <p role="status" className="text-sm capitalize">{run.status}</p>
      {run.summary?.error && <p className="text-sm text-red-700">The run failed. Verify its source access and retry.</p>}
      {reviewer && (admin || run.actor_id === actorId) && ["queued", "failed"].includes(run.status) &&
        <button className={button + " justify-self-start"} disabled={busy} onClick={() => void perform(async () => {
          await api("/reconciliation-runs/" + run.id + "/retry" + scope, { method: "POST" });
          await selectRun(run.id);
        })}>Retry run</button>}
      {run.summary && run.status === "completed" && <>
        <p className="text-sm">{run.summary.issue_count} exceptions · {run.summary.duplicate_count} duplicates excluded</p>
        <div className="overflow-auto">
          <table className="w-full text-left text-sm" aria-label="Invoice and payment comparison">
            <thead className="bg-slate-100"><tr>{["Invoice", "Currency", "Face value", "Paid", "Balance", "Partial"].map((label) => <th key={label} scope="col" className="p-2">{label}</th>)}</tr></thead>
            <tbody>{(comparisonPage?.rows ?? run.summary.comparison).map((row) => <tr key={row.invoice} className="border-b border-slate-100">
              {[row.invoice, row.currency, row.amount, row.paid, row.balance, row.partial ? "Yes" : "No"].map((value, index) => <td key={index} className="p-2">{value}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
        {run.summary.comparison_count > 100 && <div className="flex gap-2">
          {[-100, 100].map((step) => {
            const start = (comparisonPage?.offset ?? 0) + step;
            return <button key={step} className={field} disabled={busy || start < 0 || start >= run.summary!.comparison_count}
              onClick={() => void perform(async () => setComparisonPage({
                offset: start,
                rows: await api<Comparison[]>("/reconciliation-runs/" + run.id + "/comparison" + scope + "&offset=" + start),
              }))}>{step < 0 ? "Previous comparisons" : "Next comparisons"}</button>;
          })}
        </div>}
      </>}
      <h3 className="font-semibold">Exceptions</h3>
      {issues.map((issue) => <article key={issue.id} className="grid gap-2 rounded-lg border border-slate-200 p-3 text-sm">
        <h4 className="font-medium capitalize">{text(issue.rule)} · {issue.severity} · {issue.status}</h4>
        <p>{issue.refs.map((ref) => ref.source + (ref.row ? " row " + ref.row : "") + " (" + ref.version_id.slice(0, 8) + ")").join(" · ")}</p>
        {(issue.expected || issue.observed) && <p>Expected {issue.expected ?? "—"} · Observed {issue.observed ?? "—"}</p>}
        {issue.reviewer && <p className="break-all">Reviewer {issue.reviewer}</p>}
        {reviewer && !["resolved", "acknowledged"].includes(issue.status) && <form className="flex flex-wrap gap-2" onSubmit={(event) => review(event, issue)}>
          <select aria-label="Review action" className={field} name="status">
            <option value="reviewed">Record review</option>
            {admin && issue.status === "reviewed" && <><option value="resolved">Resolve</option><option value="acknowledged">Acknowledge limitation</option></>}
          </select>
          <input aria-label="Review note" className={field} name="note" minLength={5} maxLength={500} placeholder="Reason or correction" required />
          {admin && <><input aria-label="Reviewer ID" className={field} name="reviewer" maxLength={36} placeholder="Reviewer UUID" />
            <input aria-label="Correction mapping ID" className={field} name="correction_alias_id" maxLength={36} placeholder="Mapping revision UUID" /></>}
          <button className={button} disabled={busy}>Save review</button>
        </form>}
        <button type="button" className="justify-self-start text-teal-800 underline" onClick={() => void perform(async () => setReviewed({
          issue: issue.id, entries: await api<Review[]>("/reconciliation-issues/" + issue.id + "/reviews" + scope),
        }))}>Review history</button>
        {reviewed?.issue === issue.id && <ul className="space-y-1 break-words">
          {reviewed.entries.map((entry) => <li key={entry.id}>{entry.previous_status} → {entry.status} · {entry.note} · {entry.actor_id} · {new Date(entry.created_at).toLocaleString()}{entry.correction_alias_id && " · Mapping " + entry.correction_alias_id}</li>)}
          {reviewed.entries.length === 0 && <li>No reviews yet.</li>}
        </ul>}
      </article>)}
      {(run.summary?.issue_count ?? 0) > 100 && <div className="flex gap-3">
        <button disabled={offset === 0 || busy} className={field} onClick={() => void perform(() => selectRun(run.id, offset - 100))}>Previous</button>
        <button disabled={offset + 100 >= (run.summary?.issue_count ?? 0) || busy} className={field} onClick={() => void perform(() => selectRun(run.id, offset + 100))}>Next</button>
      </div>}
    </section>}
  </>;
}

export default function ReconciliationPage() {
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity>();
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Identity>("/auth/me").then((user) => { if (active) setIdentity(user); }).catch((failure: Error) => {
      if (!active) return;
      if (failure instanceof ApiError && failure.status === 401) router.replace("/login");
      else setError(failure.message);
    });
    return () => { active = false; };
  }, [router]);
  const organization = identity?.organizations.find((org) => org.id === selected) ?? identity?.organizations[0];
  return <main className="mx-auto grid max-w-6xl gap-5 px-4 py-8 sm:px-8">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div><Link href="/analytics" className="text-sm font-semibold text-teal-800 hover:underline">Business analytics</Link>
        <h1 className="mt-1 text-3xl font-bold tracking-tight">Data Quality &amp; Reconciliation</h1></div>
      {organization && <label className="grid gap-1 text-sm">Organization<select value={organization.id} className={field}
        onChange={(event) => setSelected(event.target.value)}>
        {identity?.organizations.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
      </select></label>}
    </header>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {!identity && !error && <p role="status">Loading workspace…</p>}
    {identity && !organization && <p>Join or create an organization in the <Link className="underline" href="/workspace">workspace</Link>.</p>}
    {organization && identity && <QualityWorkspace key={organization.id} organization={organization} actorId={identity.id} />}
  </main>;
}
