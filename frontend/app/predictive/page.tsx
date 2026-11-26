"use client";

import { ChartCard, DataTable, StatusBadge } from "@/components/ui/glass";
import { type FormEvent, useEffect, useState } from "react";
import { button, field, OrgPage, panel, primary } from "@/components/m6c-workspace";
import { api, type Organization } from "@/lib/workspace-api";

type Point = {week: string; point: number; lower: number; upper: number};
type Observation = {week: string; value: number};
type Metric = {mae: number; wape: number | null; bias: number; n: number};
type Anomaly = {week: string; observed: number; expected: number; lower: number; upper: number; score: number; severity: string};
type Result = {
  model?: string; model_version?: string; train_start?: string; train_end?: string;
  evaluation_start?: string; evaluation_end?: string; cutoff_exclusive?: string;
  history?: Observation[]; forecast?: Point[]; backtest?: Record<string, Metric>;
  interval?: {target: number; coverage: number; hits: number; samples: number};
  limits?: string[]; missing_weeks?: number; required_weeks?: number; observed_weeks?: number;
  stockout?: {status: string; risk?: string; stock_on_hand?: number; demand?: number; lower?: number;
    upper?: number; coverage_days?: number | null; confirmed_replenishment?: number | null;
    reorder_threshold_without_inbound?: number; daily_velocity?: number; limits: string[]};
  anomalies?: Record<string, Anomaly[]>;
};
type Run = {id: string; status: string; created_at: string; error_code?: string; recoverable?: boolean;
  config: {metric: string; sku?: string; branch?: string; category?: string};
  sources: Record<string, {version: string; published_at: string}>; result?: Result;
  quality?: {message: string}; evidence?: {publication_quality?: {message: string}}};
const display = (value: string) => value.replaceAll("_", " ");
const fmt = (value?: number | null) => value == null ? "Unavailable" : value.toLocaleString(undefined, {maximumFractionDigits: 3});

function Chart({history, forecast}: {history: Observation[]; forecast: Point[]}) {
  const observed = history.slice(-16);
  const values = [...observed.map((row) => row.value), ...forecast.flatMap((row) => [row.lower, row.upper, row.point])];
  const floor = Math.min(0, ...values);
  const ceiling = Math.max(1, ...values);
  const width = 640;
  const height = 210;
  const x = (index: number) => 20 + index * (width - 40) / Math.max(1, observed.length + forecast.length - 1);
  const y = (value: number) => height - 20 - (value - floor) * (height - 40) / (ceiling - floor);
  const historyLine = observed.map((row, index) => `${x(index)},${y(row.value)}`);
  const pointLine = forecast.map((row, index) => `${x(observed.length + index)},${y(row.point)}`);
  const interval = forecast.map((row, index) => `${x(observed.length + index)},${y(row.upper)}`)
    .concat(forecast.map((row, index) => `${x(observed.length + index)},${y(row.lower)}`).reverse());
  return <figure className="space-y-3">
    <figcaption className="text-sm text-slate-600">Slate shows observed demand. Cyan shows the estimate and its shaded prediction interval.</figcaption>
    <svg viewBox={`0 0 ${width} ${height}`} className="forecast-chart" aria-hidden="true">
      {[.25, .5, .75, 1].map((fraction) => <line key={fraction} x1={20} x2={width - 20}
        y1={y(floor + (ceiling - floor) * fraction)} y2={y(floor + (ceiling - floor) * fraction)} stroke="#405273" />)}
      <polygon points={interval.join(" ")} fill="#22d3ee" opacity=".2" />
      <polyline points={historyLine.join(" ")} fill="none" stroke="#a8b6d4" strokeWidth="3" />
      <polyline points={[...historyLine.slice(-1), ...pointLine].join(" ")} fill="none" stroke="#67e8f9" strokeWidth="3" strokeDasharray="6 4" />
      {forecast.map((row, index) => <circle key={row.week} cx={x(observed.length + index)} cy={y(row.point)} r={3.5} fill="#a5f3fc">
        <title>{row.week} ? {fmt(row.point)} estimated</title></circle>)}
    </svg>
    <details><summary className="cursor-pointer text-sm font-semibold">Historical observations</summary>
      <ul className="grid gap-1 pt-2 text-sm sm:grid-cols-3">{history.map((row) => <li key={row.week}>{row.week} · {fmt(row.value)}</li>)}</ul>
    </details>
  </figure>;
}

function Results({run}: {run: Run}) {
  const result = run.result;
  const quality = run.evidence?.publication_quality ?? run.quality;
  return <div className="space-y-4">
    <section className={panel + " space-y-2"}>
      <h2 className="text-xl font-semibold">Forecast run <StatusBadge status={run.status} /></h2>
      {run.error_code && <p role="alert">{display(run.error_code)}</p>}
      {quality && <p className="rounded bg-amber-50 p-2 text-sm text-amber-900">{quality.message}</p>}
      {result?.required_weeks && <p>Needs {result.required_weeks} contiguous weeks. Observed {result.observed_weeks}.</p>}
      {result?.cutoff_exclusive && <p className="text-sm">Business cutoff {result.cutoff_exclusive} · {result.missing_weeks} missing weeks</p>}
      <ul className="space-y-1 text-sm text-slate-600">{result?.limits?.map((limit) => <li key={limit}>{limit}</li>)}</ul>
      <details><summary className="cursor-pointer font-semibold">Source versions</summary>
        <ul className="break-all text-sm">{Object.entries(run.sources).map(([source, item]) => <li key={source}>{source} · {item.version} · {item.published_at}</li>)}</ul>
      </details>
    </section>
    {result?.forecast && result.history && <ChartCard title="Weekly forecast estimates">
      <p className="text-sm capitalize">{display(run.config.metric)} · {run.config.sku || run.config.category || "All products"} · {run.config.branch || "All branches"}</p>
      <Chart history={result.history} forecast={result.forecast} />
      <DataTable className="w-full text-left text-sm"><thead><tr>{["Week of", "Lower", "Estimate", "Upper"].map((label) => <th scope="col" className="p-2" key={label}>{label}</th>)}</tr></thead>
        <tbody>{result.forecast.map((row) => <tr className="border-t" key={row.week}><td className="p-2">{row.week}</td><td>{fmt(row.lower)}</td><td>{fmt(row.point)}</td><td>{fmt(row.upper)}</td></tr>)}</tbody>
      </DataTable>
    </ChartCard>}
    {result?.backtest && <section className={panel + " space-y-3"}>
      <h2 className="text-xl font-semibold">Chronological holdout</h2>
      <p className="text-sm">{result.model && display(result.model)} · {result.model_version} · Training {result.train_start} to {result.train_end}</p>
      <p className="text-sm">Evaluation {result.evaluation_start} to {result.evaluation_end}. These errors measure historical holdout performance.</p>
      {result.interval && <p className="text-sm">Target interval {fmt(result.interval.target * 100)}% · Observed coverage {fmt(result.interval.coverage * 100)}% ({result.interval.hits}/{result.interval.samples})</p>}
      <div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr>{["Model", "MAE", "WAPE", "Bias", "Count"].map((label) => <th scope="col" className="p-2" key={label}>{label}</th>)}</tr></thead>
        <tbody>{Object.entries(result.backtest).map(([key, metric]) => <tr className="border-t" key={key}><td className="p-2 capitalize">{display(key)}</td><td>{fmt(metric.mae)}</td><td>{metric.wape === null ? "Undefined" : fmt(metric.wape * 100) + "%"}</td><td>{fmt(metric.bias)}</td><td>{metric.n}</td></tr>)}</tbody></table></div>
    </section>}
    {result?.stockout && <section className={panel + " space-y-2"}>
      <h2 className="text-xl font-semibold">Stockout advisory</h2>
      <p className="capitalize">{display(result.stockout.risk ?? result.stockout.status)}</p>
      {result.stockout.stock_on_hand != null && <p className="text-sm">On hand {fmt(result.stockout.stock_on_hand)} · Lead-time demand {fmt(result.stockout.demand)} ({fmt(result.stockout.lower)} to {fmt(result.stockout.upper)}) · Coverage {fmt(result.stockout.coverage_days)} days</p>}
      {result.stockout.reorder_threshold_without_inbound != null && <p className="text-sm">Reorder threshold without inbound {fmt(result.stockout.reorder_threshold_without_inbound)} · Confirmed replenishment {fmt(result.stockout.confirmed_replenishment)} · Daily velocity {fmt(result.stockout.daily_velocity)}</p>}
      <ul className="text-sm text-slate-600">{result.stockout.limits.map((limit) => <li key={limit}>{limit}</li>)}</ul>
    </section>}
    {result?.anomalies && <section className={panel + " space-y-3"}>
      <h2 className="text-xl font-semibold">Seasonal anomaly review</h2>
      <p className="text-sm text-slate-600">Signals use same-phase historical observations. Review source coverage before acting.</p>
      {Object.entries(result.anomalies).map(([metric, rows]) => <div key={metric}><h3 className="font-semibold capitalize">{display(metric)}</h3>
        {rows.length ? <ul className="text-sm">{rows.map((item) => <li key={item.week}>
          {item.week} · Observed {fmt(item.observed)} · Expected {fmt(item.expected)} ({fmt(item.lower)} to {fmt(item.upper)}) · {item.severity} {fmt(item.score)}
        </li>)}</ul> : <p className="text-sm">No flags in eligible weeks.</p>}</div>)}
    </section>}
  </div>;
}

function Workspace({org}: {org: Organization}) {
  const root = "/forecasts";
  const query = "?organization_id=" + org.id;
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState("");
  const [active, setActive] = useState<Run>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const history = await api<Run[]>(root + query);
        if (alive) setRuns(history);
        if (selected) {
          const record = await api<Run>(root + "/" + selected + query);
          if (alive) setActive(record);
        }
      } catch (e) { if (alive) setError(e instanceof Error ? e.message : "Could not load forecasts."); }
    };
    void load();
    const timer = setInterval(() => { void load(); }, 3500);
    return () => { alive = false; clearInterval(timer); };
  }, [query, selected]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const optional = (key: string) => String(data.get(key) ?? "").trim() || null;
    setBusy(true); setError("");
    try {
      const deliveries = optional("replenishments");
      const replenishments = deliveries?.split(/\r?\n/).filter((line) => line.trim()).map((line) => {
        const [due_on, units, extra] = line.split(",").map((part) => part.trim());
        if (!/^\d{4}-\d{2}-\d{2}$/.test(due_on) || !units || !Number.isFinite(Number(units)) || Number(units) <= 0 || extra !== undefined) {
          throw new Error("Enter deliveries as YYYY-MM-DD, units on separate lines.");
        }
        return {due_on, units: Number(units)};
      });
      const value = await api<{id: string}>(root, {method: "POST", body: JSON.stringify({
        organization_id: org.id, request_key: crypto.randomUUID(), metric: data.get("metric"),
        horizon: Number(data.get("horizon")), season: Number(data.get("season")), as_of: optional("as_of"),
        sku: optional("sku"), branch: optional("branch"), category: optional("category"),
        category_skus: (optional("category_skus") ?? "").split(",").map((sku) => sku.trim()).filter(Boolean),
        lead_days: optional("lead_days") ? Number(data.get("lead_days")) : null,
        replenishments: replenishments ?? null,
        complete_sales_feed: data.has("complete_sales_feed"),
      })});
      setSelected(value.id);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not start forecast."); }
    finally { setBusy(false); }
  }
  return <div className="grid gap-5 lg:grid-cols-[19rem_minmax(0,1fr)]">
    <aside className="space-y-4">
      {org.role !== "viewer" && <form className={panel + " grid gap-3"} onSubmit={(event) => void submit(event)}>
        <h2 className="font-semibold">New forecast</h2>
        <label className="text-sm">Measure<select className={field} name="metric"><option value="demand">Demand units</option><option value="net_revenue">Net revenue</option></select></label>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-sm">Horizon<select className={field} name="horizon" defaultValue="4">{[1,2,4].map((n) => <option key={n} value={n}>{n} weeks</option>)}</select></label>
          <label className="text-sm">Season<select className={field} name="season">{[4,13,52].map((n) => <option key={n} value={n}>{n} weeks</option>)}</select></label>
        </div>
        <label className="text-sm">Business cutoff<input className={field} type="date" name="as_of" /></label>
        <label className="text-sm">Product SKU<input className={field} name="sku" /></label>
        <label className="text-sm">Branch<input className={field} name="branch" /></label>
        <label className="text-sm">Category name<input className={field} name="category" /></label>
        <label className="text-sm">Category SKUs, comma separated<input className={field} name="category_skus" /></label>
        <label className="text-sm">Supplier lead days<input className={field} type="number" min={1} max={28} name="lead_days" /></label>
        <label className="text-sm">Confirmed deliveries, date and units<textarea className={field} name="replenishments" rows={2} placeholder="YYYY-MM-DD, units" /></label>
        <label className="text-sm"><input name="complete_sales_feed" type="checkbox" /> Confirm scope is complete in observed weeks</label>
        <button className={primary} disabled={busy}>Train forecast</button>
      </form>}
      <section className={panel + " space-y-2"}><h2 className="font-semibold">Forecast history</h2>
        {!runs.length && <p className="text-sm">No forecast runs yet.</p>}
        {runs.map((run) => <button type="button" key={run.id} aria-current={selected === run.id} className={button + " w-full text-left"}
          onClick={() => setSelected(run.id)}>{new Date(run.created_at).toLocaleString()} · {display(run.status)}</button>)}
      </section>
    </aside>
    <section className="min-w-0 space-y-4" aria-label="Forecast results">
      {error && <p role="alert" className="rounded bg-red-50 p-2 text-red-800">{error}</p>}
      {active?.id === selected ? <>
        <Results run={active} />
        {org.role !== "viewer" && (active.status === "failed" || runs.find((run) => run.id === selected)?.recoverable) &&
          <button className={button} disabled={busy} onClick={async () => {
            setBusy(true); setError("");
            try { await api(root + "/" + selected + "/retry" + query, {method: "POST"}); }
            catch (e) { setError(e instanceof Error ? e.message : "Could not retry forecast."); }
            finally { setBusy(false); }
          }}>Retry forecast</button>}
      </> : <p className={panel}>{selected ? "Loading forecast..." : "Select a run to inspect forecasts and evidence."}</p>}
    </section>
  </div>;
}

export default function PredictivePage() {
  return <OrgPage title="Predictive analytics">{(org) => <Workspace key={org.id} org={org} />}</OrgPage>;
}
