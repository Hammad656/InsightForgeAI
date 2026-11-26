"use client";

import { type FormEvent, useEffect, useState } from "react";
import { StatusBadge } from "@/components/ui/glass";
import { button, field, OrgPage, panel, primary } from "@/components/m6c-workspace";
import { api, type Organization } from "@/lib/workspace-api";

type Profile = { key: string; host: string; port: number; database: string; security: string };
type Options = { profiles: Profile[]; analytics_fields: Record<string, string[]>; reconciliation_fields: Record<string, string[]> };
type Connector = { id: string; label: string; profile: string; enabled: boolean; last_sync_at: string | null };
type Source = { schema_name: string; table: string };
type Column = { name: string; type: string };
type Sample = { headers: string[]; rows: string[][] };
type Job = { id: string; status: string; error_code: string | null; row_count: number | null;
  version_id: string | null; recon_source_id: string | null; created_at: string; finished_at: string | null; recoverable: boolean };
const json = (method: string, value?: unknown) => ({ method, body: value === undefined ? undefined : JSON.stringify(value) });
const sourceLabel = (item: Source) => item.schema_name + "." + item.table;

function Mapping({ root, connector, options, onUpdate }: {
  root: string; connector: Connector; options: Options; onUpdate: () => Promise<void>;
}) {
  const endpoint = root + "/" + connector.id;
  const [sources, setSources] = useState<Source[]>();
  const [source, setSource] = useState<Source>();
  const [columns, setColumns] = useState<Column[]>([]);
  const [purpose, setPurpose] = useState<"analytics" | "reconciliation">("analytics");
  const [domain, setDomain] = useState("sales");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Sample>();
  const [jobs, setJobs] = useState<Job[]>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const pending = jobs?.some((job) => job.status === "queued" || job.status === "running") ?? false;
  const lastSync = [connector.last_sync_at, ...(jobs ?? []).filter((job) => job.status === "completed")
    .map((job) => job.finished_at)].filter((value): value is string => value !== null).sort().at(-1);
  const available = purpose === "analytics" ? options.analytics_fields : options.reconciliation_fields;
  const fields = available[domain] ?? [];
  const ready = !!source && fields.length > 0 && fields.every((name) => mapping[name]) &&
    new Set(fields.map((name) => mapping[name])).size === fields.length;
  const manage = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError(""); setNotice("");
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message.replaceAll("_", " ") : "Connector request failed."); }
    finally { setBusy(false); }
  };

  useEffect(() => {
    let current = true;
    const load = () => api<Job[]>(endpoint + "/syncs").then((value) => { if (current) setJobs(value); })
      .catch((e: Error) => { if (current) setError(e.message); });
    void load();
    const lifetime = pending ? setInterval(() => void load(), 2000) : undefined;
    return () => { current = false; if (lifetime) clearInterval(lifetime); };
  }, [endpoint, pending]);

  const test = () => void manage(async () => {
    const result = await api<{ status: string; objects: Source[] }>(endpoint + "/test", json("POST"));
    setSources(result.objects); setNotice("Connection verified. Choose an approved source.");
  });
  const select = (key: string) => void manage(async () => {
    const item = sources?.find((candidate) => sourceLabel(candidate) === key);
    setSource(item); setColumns([]); setMapping({}); setPreview(undefined);
    if (!item) return;
    const metadata = await api<Column[]>(endpoint + "/columns", json("POST", item));
    setColumns(metadata);
    setMapping(Object.fromEntries(fields.filter((name) => metadata.some((column) => column.name === name)).map((name) => [name, name])));
  });
  const selection = () => ({ ...source, domain, purpose, mapping });
  const refresh = async () => { setJobs(await api<Job[]>(endpoint + "/syncs")); await onUpdate(); };
  return <div className="space-y-4">
    <section className={panel + " space-y-3"}>
      <h2 className="text-xl font-semibold">{connector.label}</h2>
      <p className="text-sm text-slate-600">{connector.enabled ? "Enabled" : "Disabled"} · Last successful sync {lastSync ? new Date(lastSync).toLocaleString() : "Unavailable"}</p>
      <div className="flex flex-wrap gap-2">
        <button className={primary} disabled={busy || !connector.enabled} onClick={test}>Test connection</button>
        <button className={button} disabled={busy} onClick={() => void manage(async () => {
          await api(endpoint, json("PATCH", { enabled: !connector.enabled })); setPreview(undefined); await onUpdate();
        })}>{connector.enabled ? "Disable" : "Enable"}</button>
        <button className={button} disabled={busy} onClick={() => {
          if (window.confirm("Remove this connector? Published snapshots are retained.")) void manage(async () => {
            await api(endpoint, json("DELETE")); await onUpdate();
          });
        }}>Remove</button>
      </div>
      {error && <p role="alert" className="rounded bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      {(busy || notice) && <p role="status" className="text-sm">{busy ? "Working..." : notice}</p>}
      {sources && connector.enabled && <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-sm">Approved table<select className={field} value={source ? sourceLabel(source) : ""} disabled={busy}
          onChange={(event) => select(event.target.value)}>
          <option value="">Choose a table</option>{sources.map((item) => <option key={sourceLabel(item)}>{sourceLabel(item)}</option>)}
        </select></label>
        <label className="text-sm">Import destination<select className={field} value={purpose} onChange={(event) => {
          setPurpose(event.target.value === "reconciliation" ? "reconciliation" : "analytics");
          setDomain("sales"); setMapping({}); setPreview(undefined);
        }}><option value="analytics">Business analytics</option><option value="reconciliation">Reconciliation</option></select></label>
        <label className="text-sm">Business domain<select className={field} value={domain} onChange={(event) => {
          setDomain(event.target.value); setMapping({}); setPreview(undefined);
        }}>{Object.keys(available).map((key) => <option value={key} key={key}>{key}</option>)}</select></label>
      </div>}
      {source && connector.enabled && <div className="space-y-3">
        <h3 className="font-semibold">Field mapping</h3>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">{fields.map((name) => <label className="text-sm" key={name}>
          {name.replaceAll("_", " ")}<select className={field} value={mapping[name] ?? ""} onChange={(event) => {
            setMapping({ ...mapping, [name]: event.target.value }); setPreview(undefined);
          }}><option value="">Choose a column</option>
            {columns.map((column) => <option key={column.name} value={column.name}>{column.name} · {column.type}</option>)}
          </select></label>)}</div>
        <div className="flex flex-wrap gap-2">
          <button className={button} disabled={busy || !ready} onClick={() => void manage(async () => {
            setPreview(await api<Sample>(endpoint + "/preview", json("POST", selection())));
          })}>Preview up to 10 rows</button>
          <button className={primary} disabled={busy || !ready} onClick={() => void manage(async () => {
            await api(endpoint + "/syncs", json("POST", { ...selection(), request_key: crypto.randomUUID() }));
            await refresh(); setNotice("Synchronization queued.");
          })}>Synchronize</button>
        </div>
      </div>}
      {preview && connector.enabled && <div className="max-h-64 overflow-auto">
        {preview.rows.length === 0 ? <p>No sample rows are available.</p> : <table aria-label="Source preview" className="w-full text-left text-sm">
          <thead><tr>{preview.headers.map((header) => <th scope="col" key={header} className="p-2">{header}</th>)}</tr></thead>
          <tbody>{preview.rows.map((row, index) => <tr className="border-t" key={index}>{row.map((value, col) =>
            <td className="max-w-48 break-all p-2" key={col}>{value}</td>)}</tr>)}</tbody>
        </table>}
      </div>}
    </section>
    <section className={panel + " space-y-3"} aria-label="Synchronization history">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Synchronization history</h2>
        <button className={button} disabled={busy} onClick={() => void manage(refresh)}>Refresh history</button>
      </div>
      {!jobs && <p role="status">Loading history...</p>}
      {jobs?.length === 0 && <p>No synchronization jobs yet.</p>}
      <ul className="divide-y">{jobs?.map((job) => <li key={job.id} className="space-y-1 py-2 text-sm">
        <p><StatusBadge status={job.status} /> · {new Date(job.created_at).toLocaleString()}{job.row_count != null ? " · " + job.row_count + " rows" : ""}</p>
        {job.error_code && <p>{job.error_code.replaceAll("_", " ")}</p>}
        {(job.version_id || job.recon_source_id) && <p className="break-all text-slate-600">{job.version_id ? "Dataset version " + job.version_id : "Reconciliation source " + job.recon_source_id}</p>}
        {(job.recoverable || job.status === "failed") && <button className={button} disabled={busy || !connector.enabled}
          onClick={() => void manage(async () => { await api(endpoint + "/syncs/" + job.id + "/retry", json("POST")); await refresh(); })}>Retry sync</button>}
      </li>)}</ul>
    </section>
  </div>;
}

function Connectors({ org }: { org: Organization }) {
  const endpoint = "/organizations/" + org.id + "/connectors";
  const [options, setOptions] = useState<Options>();
  const [connectors, setConnectors] = useState<Connector[]>();
  const [selected, setSelected] = useState("");
  const [profile, setProfile] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const active = connectors?.find((item) => item.id === selected);
  const profileInfo = options?.profiles.find((item) => item.key === profile);
  useEffect(() => {
    if (org.role !== "admin") return;
    let current = true;
    Promise.all([api<Options>(endpoint + "/profiles"), api<Connector[]>(endpoint)]).then(([data, list]) => {
      if (current) { setOptions(data); setConnectors(list); }
    }).catch((e: Error) => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [endpoint, org.role]);
  const refresh = async () => { setConnectors(await api<Connector[]>(endpoint)); };
  const create = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const saved = await api<Connector>(endpoint, json("POST", { label, profile }));
      await refresh(); setSelected(saved.id); setLabel("");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not create connector."); }
    finally { setBusy(false); }
  };
  if (org.role !== "admin") return <p className={panel}>An organization administrator is required to manage connectors.</p>;
  return <div className="space-y-4">
    {error && <p role="alert" className="rounded bg-red-50 p-3 text-red-800">{error}</p>}
    <div className="grid items-start gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <aside className={panel + " space-y-4"}>
        <h2 className="font-semibold">PostgreSQL connections</h2>
        {!options && !error && <p role="status">Loading approved profiles...</p>}
        {options?.profiles.length === 0 && <p className="text-sm">No profiles are approved for this organization.</p>}
        {!!options?.profiles.length && <form className="space-y-2" onSubmit={(event) => void create(event)}>
          <label className="block text-sm">Connection name<input className={field} required maxLength={100} value={label} onChange={(event) => setLabel(event.target.value)} /></label>
          <label className="block text-sm">Approved profile<select className={field} required value={profile} onChange={(event) => setProfile(event.target.value)}>
            <option value="">Choose a profile</option>{options.profiles.map((item) => <option key={item.key}>{item.key}</option>)}
          </select></label>
          {profileInfo && <p className="break-words text-sm text-slate-600">{profileInfo.host}:{profileInfo.port} · {profileInfo.database} · {profileInfo.security}</p>}
          <button className={primary} disabled={busy}>Create connection</button>
        </form>}
        <ul className="space-y-2">{connectors?.map((item) => <li key={item.id}>
          <button className={button + " w-full text-left"} aria-current={selected === item.id} onClick={() => setSelected(item.id)}>
            {item.label}{!item.enabled && " · Disabled"}
          </button>
        </li>)}</ul>
      </aside>
      {active && options ? <Mapping key={active.id} root={endpoint} connector={active} options={options} onUpdate={refresh} />
        : <p className={panel}>Select a connection to manage source tables and synchronization.</p>}
    </div>
  </div>;
}

export default function ConnectorsPage() {
  return <OrgPage title="Data connectors">{(org) => <Connectors key={org.id} org={org} />}</OrgPage>;
}
