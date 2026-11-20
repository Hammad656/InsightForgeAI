"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, ApiError, downloadPdf, type Identity, type Organization } from "@/lib/workspace-api";

type Source = { version: string; published_at: string };
type Report = {
  id: string;
  status: "queued" | "processing" | "completed" | "failed";
  filters: { start: string; end: string; as_of: string; data_quality?: { message: string } };
  sources: Partial<Record<"sales" | "inventory" | "payments", Source>>;
  expires_at: string | null;
  retry_available: boolean;
  error_code: string | null;
};

const box = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";
const action = "rounded-md bg-teal-800 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-900 disabled:opacity-50";
const field = "rounded-md border border-slate-300 bg-white px-3 py-2 text-sm";
const labels = { sales: "Sales", inventory: "Inventory", payments: "Receivables" };
const failures: Record<string, string> = {
  temporary_failure: "The worker encountered a temporary failure. Retry this report from history.",
  permission_revoked: "The original requester needs report generation permission.",
  generation_failed: "Report generation failed. Retry or contact an administrator.",
  data_quality_blocked: "Material reconciliation exceptions block publication. Review the exceptions before retrying.",
};

function nextMonth(month: string) {
  const [year, number] = month.split("-").map(Number);
  return (number === 12 ? year + 1 : year).toString().padStart(4, "0") +
    "-" + (number % 12 + 1).toString().padStart(2, "0") + "-01";
}

function reportDate(timestamp: string) {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Karachi" })
    .format(new Date(timestamp));
}

function Reports({ org }: { org: Organization }) {
  const [reports, setReports] = useState<Report[]>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef<{ key: string; signature: string } | undefined>(undefined);
  const scope = "?organization_id=" + org.id;
  const root = "/reports";
  const canCreate = org.role !== "viewer";

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const items = await api<Report[]>(root + scope);
        if (active) setReports(items);
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : "Unable to load report history.");
      } finally {
        if (active) timer = setTimeout(refresh, 2500);
      }
    }
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [scope]);

  function record(report: Report) {
    setReports((items) => [report, ...(items ?? []).filter((item) => item.id !== report.id)]);
  }

  async function run(operation: () => Promise<void>) {
    setBusy(true);
    setError("");
    try { await operation(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "Unable to complete the report action."); }
    finally { setBusy(false); }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const month = String(form.get("month"));
    const payload = {
      organization_id: org.id, template: String(form.get("template")),
      start: month + "-01", end: nextMonth(month),
      branch: String(form.get("branch")).trim() || null,
      sku: String(form.get("sku")).trim() || null,
      customer: String(form.get("customer")).trim() || null,
      query_id: String(form.get("query")).trim() || null,
    };
    const signature = JSON.stringify(payload);
    if (request.current?.signature !== signature) request.current = { key: crypto.randomUUID(), signature };
    const requestKey = request.current.key;
    void run(async () => {
      const report = await api<Report>(root, {
        method: "POST", body: JSON.stringify({ ...payload, request_key: requestKey }),
      });
      request.current = undefined;
      record(report);
    });
  }

  async function download(id: string) {
    const grant = await api<{ token: string }>(root + "/" + id + "/download-grant" + scope, { method: "POST" });
    const artifact = await downloadPdf(root + "/" + id + "/download" + scope, grant.token);
    const url = URL.createObjectURL(artifact);
    const link = document.createElement("a");
    link.href = url;
    link.download = "monthly-business-performance.pdf";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <>
    {canCreate && <form className={box + " grid gap-4"} onSubmit={submit}>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1 text-sm font-medium">Template
          <select name="template" className={field}><option value="monthly-performance">Monthly Business Performance</option></select>
        </label>
        <label className="grid gap-1 text-sm font-medium">Reporting month
          <input name="month" type="month" className={field} required />
        </label>
      </div>
      <details>
        <summary className="cursor-pointer text-sm font-medium">Filters and evidence reference</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="grid gap-1 text-sm">Branch ID<input name="branch" maxLength={120} className={field} /></label>
          <label className="grid gap-1 text-sm">SKU<input name="sku" maxLength={120} className={field} /></label>
          <label className="grid gap-1 text-sm">Customer ID<input name="customer" maxLength={120} className={field} /></label>
          <label className="grid gap-1 text-sm">Ask AI query ID<input name="query" maxLength={36} className={field} /></label>
        </div>
      </details>
      <button type="submit" className={action + " justify-self-start"} disabled={busy}>Generate report</button>
    </form>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    <section className={box} aria-label="Report history">
      <h2 className="text-lg font-semibold">Report history</h2>
      {!reports && !error && <p role="status" className="mt-2 text-sm">Loading reports…</p>}
      {reports?.length === 0 && <p className="mt-2 text-sm text-slate-600">No reports yet.</p>}
      <ul className="mt-2 grid divide-y divide-slate-200">
        {reports?.map((report) => {
          const expired = !!report.expires_at && Date.parse(report.expires_at) <= Date.now();
          const status = expired ? "Expired" : report.status;
          return <li key={report.id} className="grid gap-3 py-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="font-medium">Monthly Business Performance</h3>
                <p className="text-sm text-slate-600">{report.filters.start} to {report.filters.end} (end exclusive)</p>
                <p className="mt-1 text-sm font-medium capitalize text-teal-800" role="status">{status}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {report.status === "completed" && !expired && <button disabled={busy} className={action}
                  onClick={() => void run(() => download(report.id))}>Download PDF</button>}
                {report.retry_available && canCreate && <button disabled={busy} className={field}
                  onClick={() => void run(async () => record(await api<Report>(
                    root + "/" + report.id + "/retry" + scope, { method: "POST" },
                  )))}>Retry</button>}
              </div>
            </div>
            {report.error_code && <p className="text-sm text-red-700">{failures[report.error_code] ?? "The report is temporarily unavailable."}</p>}
            {report.filters.data_quality && <p className="text-sm text-amber-900">{report.filters.data_quality.message}</p>}
            <details className="text-sm">
              <summary className="cursor-pointer text-slate-700">Source freshness and coverage</summary>
              <ul className="mt-2 grid gap-1 text-slate-600">
                {(Object.keys(labels) as (keyof typeof labels)[]).map((key) => {
                  const source = report.sources[key];
                  return <li key={key}>{labels[key]} · {source ? reportDate(source.published_at) + " PKT · " + source.version : "Unavailable"}</li>;
                })}
              </ul>
              <p className="mt-2 text-slate-600">Sales exclude returns and credits. Inventory reservations are unavailable. Receivables cover only invoices in the payment snapshot.</p>
              {report.expires_at && <p className="mt-1 text-slate-600">Download expires {reportDate(report.expires_at)} PKT.</p>}
            </details>
          </li>;
        })}
      </ul>
    </section>
  </>;
}

export default function ReportsPage() {
  const [identity, setIdentity] = useState<Identity>();
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const router = useRouter();
  useEffect(() => {
    let active = true;
    api<Identity>("/auth/me").then((user) => { if (active) setIdentity(user); }).catch((failure: Error) => {
      if (!active) return;
      if (failure instanceof ApiError && failure.status === 401) router.replace("/login");
      else setError(failure.message);
    });
    return () => { active = false; };
  }, [router]);
  const org = identity?.organizations.find((item) => item.id === selected) ?? identity?.organizations[0];
  return <main className="mx-auto grid max-w-5xl gap-5 px-4 py-8 sm:px-8">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <nav className="flex gap-4 text-sm font-semibold text-teal-800" aria-label="Workspace navigation">
          <Link href="/schedules" className="hover:underline">Report schedules</Link>
          <Link href="/analytics" className="hover:underline">Business analytics</Link>
          <Link href="/ask" className="hover:underline">Ask AI</Link>
          <Link href="/reconciliation" className="hover:underline">Data quality</Link>
        </nav>
        <h1 className="mt-1 text-3xl font-bold tracking-tight text-slate-900">Reports</h1>
      </div>
      {org && <label className="grid gap-1 text-sm">Organization
        <select value={org.id} className={field} onChange={(event) => setSelected(event.target.value)}>
          {identity?.organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}
    </header>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {!identity && !error && <p role="status">Loading workspace…</p>}
    {identity && !org && <p className={box}>Create or join an organization in the <Link className="underline" href="/workspace">workspace</Link>.</p>}
    {org && <Reports org={org} key={org.id} />}
  </main>;
}
