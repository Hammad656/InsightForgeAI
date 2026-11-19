"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { api, ApiError, type Identity, type Organization } from "@/lib/workspace-api";

type Domain = "sales" | "inventory" | "receivables";
type View = "overview" | Domain;
type Cell = string | number | boolean | null;
type Row = Record<string, Cell>;
type Definition = { metric_id: string; label: string; definition: string; version: number; unit: string; domain: Domain };
type Report = {
  source: { domain: string; version: string; published_at: string } | null;
  metrics: Row | null;
  trend?: Row[];
  breakdown?: Row[];
  positions?: Row[];
  movements?: Row[];
  ageing?: Row[];
  invoices?: Row[];
};
type Response = {
  filters: { start: string; end: string; as_of: string };
  reports: Partial<Record<Domain, Report>>;
};

const views: Record<View, string> = {
  overview: "Overview", sales: "Sales", inventory: "Inventory", receivables: "Receivables",
};
const field = "rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900";
const surface = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";
const primary = "rounded-md bg-teal-800 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-900";
const notes: Record<Domain, string> = {
  sales: "Completed sales in PKR. Returns, refunds, tax, and shipping are outside the imported data contract.",
  inventory: "Stock uses movement history through the as-of date. Include opening stock as a movement. Reservations are unavailable.",
  receivables: "Balances cover invoices represented in the payments dataset. Standalone unpaid invoices and credits are outside the imported data contract.",
};

function display(value: Cell | undefined, unit?: string) {
  if (value === null || value === undefined) return "Unavailable";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  const text = String(value);
  const numeric = /^(-?)(\d+)(?:\.(\d+))?$/.exec(text);
  if (!numeric) return text;
  const [, sign, integer, fraction = ""] = numeric;
  const decimals = fraction.replace(/0+$/, "").padEnd(unit === "PKR" ? 2 : 0, "0");
  const formatted = sign + integer.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (decimals ? "." + decimals : "");
  return unit === "PKR" ? "PKR " + formatted : unit === "percent" ? formatted + "%" : formatted;
}

function freshness(timestamp: string) {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Karachi",
  }).format(new Date(timestamp));
}

function Bars({ title, rows, category, value, unit }: {
  title: string; rows: Row[]; category: string; value: string; unit?: string;
}) {
  const max = Math.max(1, ...rows.map((row) => Math.abs(Number(row[value]))));
  return <section className={surface}>
    <h3 className="font-semibold">{title}</h3>
    {rows.length === 0 ? <p className="mt-3 text-sm text-slate-600">No activity in this period.</p> :
      <ul className="mt-4 grid max-h-80 gap-3 overflow-y-auto">
        {rows.map((row) => <li key={String(row[category])} className="grid grid-cols-[6rem_1fr_auto] items-center gap-3 text-sm">
          <span>{String(row[category])}</span>
          <div aria-hidden="true" className="h-2 rounded bg-slate-100">
            <div className="h-full rounded bg-teal-700" style={{ width: Math.abs(Number(row[value])) / max * 100 + "%" }} />
          </div>
          <span className="tabular-nums">{display(row[value], unit)}</span>
        </li>)}
      </ul>}
  </section>;
}

type Column = [key: string, label: string, unit?: string];

function Table({ title, rows, columns }: { title: string; rows: Row[]; columns: Column[] }) {
  return <section className={surface}>
    <h3 className="font-semibold">{title}</h3>
    {rows.length === 0 ? <p className="mt-3 text-sm text-slate-600">No matching records.</p> :
      <div className="mt-3 overflow-x-auto">
        <table className="w-full whitespace-nowrap text-left text-sm" aria-label={title}>
          <thead className="bg-slate-50 text-slate-700"><tr>
            {columns.map(([key, label]) => <th scope="col" className="px-3 py-2" key={key}>{label}</th>)}
          </tr></thead>
          <tbody>{rows.map((row, index) => <tr key={index} className="border-t border-slate-100">
            {columns.map(([key, , unit]) => <td className="px-3 py-2 tabular-nums" key={key}>
              {unit === "identifier" ? String(row[key]) : display(row[key], unit)}
            </td>)}
          </tr>)}</tbody>
        </table>
      </div>}
    {rows.length === 100 && <p className="mt-3 text-sm text-slate-600">Showing up to 100 records. Refine the filters for a narrower view.</p>}
  </section>;
}

function ReportSection({ domain, report, definitions }: { domain: Domain; report: Report; definitions: Definition[] }) {
  const metrics = report.metrics;
  return <section className="grid gap-4">
    <div>
      <h2 className="text-xl font-semibold">{views[domain]}</h2>
      {report.source && <p className="mt-1 text-sm text-slate-600" title={report.source.version}>
        Published {freshness(report.source.published_at)} PKT · Snapshot {report.source.version.slice(0, 8)}
      </p>}
    </div>
    {metrics ? <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {definitions.filter((item) => item.metric_id in metrics).map((item) => <article className={surface} key={item.metric_id}>
        <h3 className="text-sm font-medium text-slate-600">{item.label}</h3>
        <p className="mt-2 break-words text-xl font-semibold tabular-nums">{display(metrics[item.metric_id], item.unit)}</p>
      </article>)}
    </div> : <p className={surface}>No published dataset. <Link className="font-semibold text-teal-800 underline" href="/workspace">Open data workspace</Link></p>}
    {report.source && <p className="text-sm text-slate-600">{notes[domain]}</p>}
    {report.trend && <Bars title="Daily net revenue" rows={report.trend} category="day" value="net_revenue" unit="PKR" />}
    {report.breakdown && <Table title="Sales by branch and SKU" rows={report.breakdown} columns={[
      ["branch", "Branch", "identifier"], ["sku", "SKU", "identifier"], ["order_count", "Orders"],
      ["net_revenue", "Net revenue", "PKR"], ["gross_profit", "Gross profit", "PKR"],
    ]} />}
    {report.positions && <Table title="Stock by SKU and branch" rows={report.positions} columns={[
      ["sku", "SKU", "identifier"], ["branch", "Branch", "identifier"], ["stock_on_hand", "On hand"], ["low_stock", "Low stock"],
    ]} />}
    {report.movements && <Table title="Movements by type" rows={report.movements} columns={[
      ["movement_type", "Type"], ["movement_count", "Records"], ["net_quantity", "Net quantity"],
    ]} />}
    {report.movements && <Bars title="Movement counts by type" rows={report.movements} category="movement_type" value="movement_count" />}
    {report.ageing && <Bars title="Outstanding balance by days past due" rows={report.ageing} category="bucket" value="balance" unit="PKR" />}
    {report.invoices && <Table title="Open invoices" rows={report.invoices} columns={[
      ["invoice", "Invoice", "identifier"], ["customer", "Customer", "identifier"], ["due_on", "Due date"],
      ["paid", "Paid", "PKR"], ["balance", "Balance", "PKR"], ["bucket", "Days past due"],
    ]} />}
  </section>;
}

function Dashboard({ organization }: { organization: Organization }) {
  const [view, setView] = useState<View>("overview");
  const [query, setQuery] = useState("");
  const [reportState, setReportState] = useState<{ request: string; data?: Response; error?: string }>();
  const [definitions, setDefinitions] = useState<Definition[]>();
  const [catalogError, setCatalogError] = useState("");
  const router = useRouter();
  const root = "/organizations/" + organization.id;
  const request = view + "?" + query;
  useEffect(() => {
    let active = true;
    api<Definition[]>(root + "/metrics").then((data) => {
      if (active) setDefinitions(data);
    }).catch((error: Error) => {
      if (active) setCatalogError(error.message);
    });
    return () => { active = false; };
  }, [root]);
  useEffect(() => {
    const controller = new AbortController();
    api<Response>(root + "/analytics/" + request, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) setReportState({ request, data });
    }).catch((error: Error) => {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) router.replace("/login");
      setReportState({ request, error: error.message });
    });
    return () => controller.abort();
  }, [request, root, router]);

  const current = reportState?.request === request ? reportState : undefined;
  const data = current?.data;
  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new URLSearchParams();
    new FormData(event.currentTarget).forEach((value, key) => {
      if (typeof value === "string" && value.trim()) values.set(key, value.trim());
    });
    setQuery(values.toString());
  }

  return <>
    <nav aria-label="Analytics views" className="flex flex-wrap gap-2 border-b border-slate-200 pb-3">
      {(Object.keys(views) as View[]).map((item) => <button key={item} aria-pressed={view === item}
        className={view === item ? primary : "rounded-md px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-200"}
        onClick={() => { if (view !== item) { setView(item); setQuery(""); } }}>{views[item]}</button>)}
    </nav>
    <form key={view} className={surface + " flex flex-wrap items-end gap-3"} onSubmit={apply}>
      {view !== "receivables" && <>
        <label className="grid gap-1 text-sm">Start date<input type="date" name="start" className={field} /></label>
        <label className="grid gap-1 text-sm">End date (exclusive)<input type="date" name="end" className={field} /></label>
      </>}
      {view !== "sales" && <label className="grid gap-1 text-sm">As-of date<input type="date" name="as_of" className={field} /></label>}
      {(view === "sales" || view === "inventory") && <>
        <label className="grid gap-1 text-sm">Branch ID<input name="branch" maxLength={120} className={field} /></label>
        <label className="grid gap-1 text-sm">SKU<input name="sku" maxLength={120} className={field} /></label>
      </>}
      {(view === "sales" || view === "receivables") &&
        <label className="grid gap-1 text-sm">Customer ID<input name="customer" maxLength={120} className={field} /></label>}
      {view === "inventory" && <label className="grid gap-1 text-sm">Low-stock threshold
        <input name="threshold" type="number" min="0" step="0.0001" placeholder="5" className={field + " w-36"} />
      </label>}
      <button className={primary}>Apply filters</button>
      <button className="px-2 py-2 text-sm underline" type="reset" onClick={() => setQuery("")}>Reset</button>
    </form>
    {catalogError && <p role="alert" className="text-red-700">{catalogError}</p>}
    {current?.error && <p role="alert" className="text-red-700">{current.error}</p>}
    {(!current || (!definitions && !catalogError && !current.error)) && <p role="status">Loading analytics…</p>}
    {data && definitions && <>
      <p className="text-sm text-slate-600">
        {view !== "receivables" && <>Period {data.filters.start} to {data.filters.end} (exclusive). </>}
        {view !== "sales" && <>As of {data.filters.as_of}. </>}Asia/Karachi
      </p>
      {Object.entries(data.reports).map(([domain, report]) => <ReportSection key={domain} domain={domain as Domain}
        report={report} definitions={definitions.filter((item) => item.domain === domain)} />)}
      <details className={surface}>
        <summary className="cursor-pointer font-semibold">Metric definitions</summary>
        <dl className="mt-3 grid gap-3 text-sm">
          {definitions.filter((item) => view === "overview" || item.domain === view).map((item) => <div key={item.metric_id}>
            <dt className="font-medium">{item.label} · v{item.version}</dt><dd className="text-slate-600">{item.definition}</dd>
          </div>)}
        </dl>
      </details>
    </>}
  </>;
}

export default function AnalyticsPage() {
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
  return <main className="mx-auto grid max-w-7xl gap-5 px-4 py-8 sm:px-8">
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <nav aria-label="Workspace navigation" className="flex gap-4 text-sm font-semibold text-teal-800">
          <Link href="/workspace" className="hover:underline">Workspace</Link>
          <Link href="/ask" className="hover:underline">Ask AI</Link>
        </nav>
        <h1 className="mt-1 text-3xl font-bold tracking-tight text-slate-900">Business analytics</h1>
      </div>
      {organization && <label className="grid gap-1 text-sm">Organization
        <select value={organization.id} onChange={(event) => setSelected(event.target.value)} className={field}>
          {identity?.organizations.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
        </select>
      </label>}
    </header>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {!identity && !error && <p role="status">Loading workspace…</p>}
    {identity && !organization && <p className={surface}>Create or join an organization in the <Link href="/workspace" className="underline">workspace</Link>.</p>}
    {organization && <Dashboard organization={organization} key={organization.id} />}
  </main>;
}
