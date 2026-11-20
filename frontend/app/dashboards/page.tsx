"use client";

import { type FormEvent, useEffect, useState } from "react";
import { button, field, OrgPage, panel, primary } from "@/components/m6c-workspace";
import { api, type Organization } from "@/lib/workspace-api";

type Widget = { id: string; kind: "kpi" | "chart" | "table"; metric: string; dimension?: string | null;
  width?: number; height?: number; filters?: Record<string, string>; dataset_version?: string | null };
type Metric = { metric_id: string; label: string; definition: string; sql: string | null; filters: string[] };
type Item = { id: string; title: string; owner_id: string; revision: number };
type Dashboard = Item & { widgets: Widget[]; editable: boolean; shares: { user_id: string; editor: boolean }[] };
type Value = string | number | null;
type Result = { id: string; status: string; columns?: string[]; rows?: Record<string, Value>[];
  unit?: string; stale?: boolean; source?: { version: string; published_at: string; active: boolean } };
type Refresh = { widgets: Result[]; revision: number; refreshed_at: string; data_quality?: { message: string } };
const labels: Record<string, string> = { sku: "Product SKU", branch: "Branch", customer: "Customer",
  start: "Start", end: "End (exclusive)", as_of: "As of", threshold: "Stock threshold" };

function dimensions(metric: string) {
  if (metric === "ageing") return ["ageing_bucket"];
  if (metric === "stock_on_hand") return ["sku", "branch"];
  if (["gross_sales", "net_revenue", "cogs", "gross_profit", "order_count", "average_order_value"].includes(metric)) {
    return ["day", "branch", "sku", "customer"];
  }
  return [];
}

function WidgetResult({ widget, result }: { widget: Widget; result?: Result }) {
  if (!result) return <p className="text-sm text-slate-600">Refresh saved widgets to load their results.</p>;
  if (!result.columns || !result.rows) return <p>No authorized source is available for this widget.</p>;
  const { columns, rows } = result;
  const value = columns.at(-1) ?? "";
  const scale = Math.max(1, ...rows.map((row) => Math.abs(Number(row[value] ?? 0))));
  return <div className="space-y-3">
    {widget.kind === "kpi" ? <p className="text-3xl font-bold tabular-nums">{rows[0]?.[value] ?? "Unavailable"} <small className="text-sm">{result.unit}</small></p>
      : <>
        {widget.kind === "chart" && <ul aria-label="Bar chart" className="max-h-48 space-y-2 overflow-auto">
          {rows.map((row, index) => <li key={index} className="grid grid-cols-[minmax(3rem,1fr)_2fr_auto] items-center gap-2 text-sm">
            <span className="break-all">{row[columns[0]]}</span>
            <span aria-hidden="true" className="h-2 rounded bg-teal-50"><span className="block h-2 rounded bg-teal-700" style={{ width: Math.abs(Number(row[value] ?? 0)) / scale * 100 + "%" }} /></span>
            <span>{row[value] ?? "Unavailable"}</span>
          </li>)}
        </ul>}
        <div className="max-h-56 overflow-auto"><table className="w-full text-left text-sm" aria-label="Widget results">
          <thead><tr>{columns.map((column) => <th key={column} scope="col" className="bg-slate-100 p-2">{column.replaceAll("_", " ")}</th>)}</tr></thead>
          <tbody>{rows.map((row, index) => <tr key={index} className="border-b">{columns.map((column) => <td key={column} className="p-2">{row[column] ?? "Unavailable"}</td>)}</tr>)}</tbody>
        </table>{!rows.length && <p className="py-2">No matching rows.</p>}</div>
      </>}
    {result.source && <details className="text-sm"><summary className="cursor-pointer font-semibold">Source freshness</summary>
      <p>{new Date(result.source.published_at).toLocaleString()} · {result.source.active ? "Active version" : "Pinned historical version"}</p>
      <p className="break-all">{result.source.version}</p>
    </details>}
    {result.stale && <p className="text-sm font-semibold text-amber-800">This source was published over seven days ago.</p>}
  </div>;
}

function Workspace({ org, actor }: { org: Organization; actor: string }) {
  const query = "?organization_id=" + org.id;
  const [list, setList] = useState<Item[]>();
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [defaultId, setDefaultId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Dashboard>();
  const [result, setResult] = useState<Refresh>();
  const [title, setTitle] = useState("");
  const [member, setMember] = useState("");
  const [editor, setEditor] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const endpoint = (id = "") => "/dashboards" + (id ? "/" + id : "") + query;
  const body = (method: string, value?: unknown) => ({ method, body: value === undefined ? undefined : JSON.stringify(value) });
  const refresh = async (id: string) => { setResult(undefined); setResult(await api<Refresh>(endpoint(id + "/refresh"), body("POST"))); };
  const open = async (id: string) => {
    setSelected(undefined); setResult(undefined);
    const view = await api<Dashboard>(endpoint(id)); setSelected(view); setDirty(false);
    if (view.widgets.length) await refresh(id);
  };
  const listing = async () => {
    const value = await api<{ dashboards: Item[]; default_id: string | null }>(endpoint());
    setList(value.dashboards); setDefaultId(value.default_id);
    return value.default_id;
  };
  const run = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : "Dashboard request failed."); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    let active = true;
    Promise.all([api<{ dashboards: Item[]; default_id: string | null }>("/dashboards" + query),
      api<Metric[]>("/organizations/" + org.id + "/metrics")]).then(async ([value, catalog]) => {
      if (!active) return;
      setList(value.dashboards); setDefaultId(value.default_id); setMetrics(catalog.filter((item) => item.sql));
      if (value.default_id) {
        const id = value.default_id;
        const view = await api<Dashboard>("/dashboards/" + id + query);
        if (!active) return;
        setSelected(view);
        if (view.widgets.length) {
          const data = await api<Refresh>("/dashboards/" + id + "/refresh" + query, { method: "POST" });
          if (active) setResult(data);
        }
      }
    }).catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [org.id, query]);
  const update = (widgets: Widget[]) => {
    if (selected) setSelected({ ...selected, widgets });
    setResult(undefined); setDirty(true);
  };
  const change = (index: number, widget: Widget) => update(selected!.widgets.map((item, position) => position === index ? widget : item));
  const move = (index: number, offset: number) => {
    const widgets = [...selected!.widgets];
    [widgets[index], widgets[index + offset]] = [widgets[index + offset], widgets[index]];
    update(widgets);
  };
  const create = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const value = await api<{ id: string }>(endpoint(), body("POST", { title, widgets: [] }));
      setTitle(""); await listing(); await open(value.id);
    });
  };

  return <div className="grid gap-5 lg:grid-cols-[16rem_minmax(0,1fr)]">
    <aside className={panel + " space-y-4 self-start"}>
      <h2 className="font-semibold">Your dashboards</h2>
      {org.role !== "viewer" && <form onSubmit={create} className="space-y-2">
        <label className="block text-sm font-medium">Dashboard name<input className={field} required maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} /></label>
        <button className={primary} disabled={busy}>Create dashboard</button>
      </form>}
      {!list && !error && <p role="status">Loading dashboards…</p>}
      {list?.length === 0 && <p className="text-sm text-slate-600">Created and shared dashboards appear here.</p>}
      <ul className="space-y-1">{list?.map((item) => <li key={item.id}>
        <button className={button + " w-full break-words text-left"} disabled={busy} aria-current={selected?.id === item.id}
          onClick={() => void run(() => open(item.id))}>{item.title}{defaultId === item.id ? " · Default" : ""}</button>
      </li>)}</ul>
    </aside>
    <section className="min-w-0 space-y-4" aria-label="Dashboard editor">
      {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-800">{error}</p>}
      {busy && <p role="status">Working…</p>}
      {!selected ? <p className={panel}>Select a dashboard to view its widgets.</p> : <>
        <div className={panel + " space-y-3"}>
          {selected.editable ? <label className="block text-sm font-semibold">Dashboard title
            <input className={field} maxLength={120} value={selected.title} onChange={(e) => setSelected({ ...selected, title: e.target.value })} />
          </label> : <h2 className="text-xl font-semibold">{selected.title}</h2>}
          <div className="flex flex-wrap gap-2">
            {selected.editable && <button className={primary} disabled={busy} onClick={() => void run(async () => {
              await api(endpoint(selected.id), body("PUT", { title: selected.title, widgets: selected.widgets, revision: selected.revision }));
              await listing(); await open(selected.id);
            })}>Save dashboard</button>}
            <button className={button} disabled={busy || dirty} onClick={() => void run(() => refresh(selected.id))}>Refresh saved data</button>
            <button className={button} disabled={busy} onClick={() => void run(async () => { await api(endpoint(selected.id + "/default"), body("PUT")); await listing(); })}>Set as default</button>
            {org.role !== "viewer" && <button className={button} disabled={busy} onClick={() => void run(async () => {
              const copy = await api<{ id: string }>(endpoint(selected.id + "/duplicate"), body("POST"));
              await listing(); await open(copy.id);
            })}>Duplicate</button>}
            {selected.editable && selected.owner_id === actor && <button className={button} disabled={busy} onClick={() => {
              if (window.confirm("Delete this dashboard?")) void run(async () => { await api(endpoint(selected.id), body("DELETE")); setSelected(undefined); await listing(); });
            }}>Delete</button>}
          </div>
          {result && <p className="text-sm text-slate-600">Refreshed {new Date(result.refreshed_at).toLocaleString()}</p>}
          {dirty && <p className="text-sm text-slate-600">Save widget changes before refreshing.</p>}
          {result?.data_quality && <p className="rounded bg-amber-50 p-2 text-sm text-amber-900">{result.data_quality.message}</p>}
          {selected.editable && selected.owner_id === actor && <details>
            <summary className="cursor-pointer text-sm font-semibold">Sharing</summary>
            <form className="mt-2 flex flex-wrap items-end gap-2" onSubmit={(event) => {
              event.preventDefault(); void run(async () => {
                await api(endpoint(selected.id + "/shares/" + encodeURIComponent(member.trim())), body("PUT", { editor }));
                setMember(""); await open(selected.id);
              });
            }}>
              <label className="text-sm">Organization member ID<input className={field} value={member} required onChange={(e) => setMember(e.target.value)} /></label>
              <label className="text-sm"><input type="checkbox" checked={editor} onChange={(e) => setEditor(e.target.checked)} /> Can edit</label>
              <button className={button} disabled={busy}>Share</button>
            </form>
            {selected.shares.map((share) => <p className="mt-2 flex flex-wrap items-center gap-2 break-all text-sm" key={share.user_id}>{share.user_id} · {share.editor ? "Editor" : "Viewer"}
              <button className={button} disabled={busy} onClick={() => void run(async () => {
                await api(endpoint(selected.id + "/shares/" + share.user_id), body("DELETE")); await open(selected.id);
              })}>Revoke</button></p>)}
          </details>}
        </div>
        {selected.editable && <button className={button} disabled={busy || selected.widgets.length >= 12} onClick={() => update([...selected.widgets,
          { id: crypto.randomUUID(), kind: "kpi", metric: "net_revenue", width: 1, height: 1 }])}>Add widget</button>}
        {!selected.widgets.length && <p className={panel}>This dashboard has no widgets yet.</p>}
        <div className="grid auto-rows-min grid-cols-1 gap-4 xl:grid-cols-2">
          {selected.widgets.map((widget, index) => {
            const metric = metrics.find((item) => item.metric_id === widget.metric);
            const supported = dimensions(widget.metric);
            return <article key={widget.id} className={panel + " min-w-0 space-y-3" + (widget.width === 2 ? " xl:col-span-2" : "") + (widget.height === 2 ? " min-h-80 xl:row-span-2" : "")}>
              <h3 className="text-lg font-semibold">{metric?.label ?? widget.metric}</h3>
              {metric && <p className="text-sm text-slate-600">{metric.definition}</p>}
              {selected.editable && <details>
                <summary className="cursor-pointer text-sm font-semibold">Configure widget {index + 1}</summary>
                <div className="mt-2 grid gap-2 sm:grid-cols-2">
                  <label className="text-sm">Metric<select className={field} value={widget.metric} onChange={(e) => {
                    const key = e.target.value;
                    change(index, { ...widget, metric: key, dimension: key === "ageing" ? "ageing_bucket" : null,
                      kind: key === "ageing" ? "table" : "kpi", filters: {}, dataset_version: null });
                  }}>{metrics.map((item) => <option key={item.metric_id} value={item.metric_id}>{item.label}</option>)}</select></label>
                  <label className="text-sm">Display<select className={field} value={widget.kind} onChange={(e) => {
                    const kind = e.target.value as Widget["kind"];
                    change(index, { ...widget, kind, dimension: kind === "kpi" ? null : widget.dimension ?? supported[0] ?? null });
                  }}><option value="kpi" disabled={widget.metric === "ageing"}>KPI</option><option value="chart" disabled={!supported.length}>Chart</option><option value="table">Table</option></select></label>
                  {widget.kind !== "kpi" && <label className="text-sm">Dimension<select className={field} value={widget.dimension ?? ""} onChange={(e) => change(index, { ...widget, dimension: e.target.value || null })}>
                    {widget.kind === "table" && widget.metric !== "ageing" && <option value="">Total</option>}
                    {supported.map((name) => <option key={name} value={name}>{labels[name] ?? name.replaceAll("_", " ")}</option>)}
                  </select></label>}
                  {metric?.filters.map((name) => <label className="text-sm" key={name}>{labels[name] ?? name}<input className={field}
                    type={["start", "end", "as_of"].includes(name) ? "date" : name === "threshold" ? "number" : "text"}
                    min={name === "threshold" ? "0" : undefined} step={name === "threshold" ? "0.0001" : undefined}
                    value={widget.filters?.[name] ?? ""} onChange={(e) => {
                      const filters = { ...widget.filters }; if (e.target.value) filters[name] = e.target.value; else delete filters[name];
                      change(index, { ...widget, filters });
                    }} /></label>)}
                  <label className="text-sm">Pinned dataset version<input className={field} placeholder="Use active version" value={widget.dataset_version ?? ""} onChange={(e) => change(index, { ...widget, dataset_version: e.target.value.trim() || null })} /></label>
                  {(["width", "height"] as const).map((size) => <label className="text-sm capitalize" key={size}>{size}<select className={field} value={widget[size] ?? 1} onChange={(e) => change(index, { ...widget, [size]: Number(e.target.value) })}><option value="1">Standard</option><option value="2">Expanded</option></select></label>)}
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button className={button} disabled={index === 0} onClick={() => move(index, -1)}>Move earlier</button>
                  <button className={button} disabled={index === selected.widgets.length - 1} onClick={() => move(index, 1)}>Move later</button>
                  <button className={button} onClick={() => update(selected.widgets.filter((item) => item.id !== widget.id))}>Remove widget</button>
                </div>
              </details>}
              <WidgetResult widget={widget} result={result?.widgets.find((item) => item.id === widget.id)} />
            </article>;
          })}
        </div>
      </>}
    </section>
  </div>;
}

export default function DashboardsPage() {
  return <OrgPage title="Custom dashboards">{(org, actor) => <Workspace key={org.id} org={org} actor={actor} />}</OrgPage>;
}
