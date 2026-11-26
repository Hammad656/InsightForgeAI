"use client";

import { useWorkspace } from "@/components/app-frame";

import Link from "next/link";
import { Download, FileBarChart2 } from "lucide-react";
import { StatusBadge } from "@/components/ui/glass";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { api, downloadPdf, type Organization } from "@/lib/workspace-api";

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

const box = "glass-card";
const action = "glass-button glass-primary";
const field = "glass-input";
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
    <div className="report-cover"><span className="metric-icon"><FileBarChart2 size={20} aria-hidden /></span><div><h2>Monthly Business Performance</h2><p>Executive PDF with KPIs, charts, and source evidence.</p></div></div>
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
                <p className="mt-1 text-sm font-medium capitalize text-teal-800" role="status"><StatusBadge status={status} /></p>
              </div>
              <div className="flex flex-wrap gap-2">
                {report.status === "completed" && !expired && <button disabled={busy} className={action}
                  onClick={() => void run(() => download(report.id))}><Download size={16} aria-hidden /> Download PDF</button>}
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
  const { identity, orgId } = useWorkspace();
  const org = identity.organizations.find((item) => item.id === orgId);
  return <main className="module-page">
    <header className="module-header"><h1>Executive reports</h1></header>
    {org ? <Reports org={org} key={org.id} /> : <p className="glass-card">Create or join an organization in <Link href="/workspace/team" className="underline">Team &amp; settings</Link>.</p>}
  </main>;
}
