"use client";

import { type FormEvent, useEffect, useState } from "react";
import { button, field, OrgPage, panel, primary } from "@/components/m6c-workspace";
import { api, downloadPdf, type Organization } from "@/lib/workspace-api";

type Config = { name: string; cadence: "daily" | "weekly" | "monthly"; timezone: string; hour: number; minute: number;
  weekday: number; month_day: number; period: "previous_day" | "previous_week" | "previous_month" };
type Schedule = { id: string; config: Config; paused: boolean; next_at: string };
type Run = { due_at: string; status: string; error_code: string | null; report_id: string | null; expires_at: string | null };
type Notice = { kind: string; status?: string; error_code?: string; finished_at?: string; domain?: string; published_at?: string; message?: string };
const initial: Config = { name: "", cadence: "monthly", timezone: "Asia/Karachi", hour: 9, minute: 0,
  weekday: 0, month_day: 1, period: "previous_month" };
const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const human = (value: string) => value.replaceAll("_", " ");

function Schedules({ org }: { org: Organization }) {
  const query = "?organization_id=" + org.id;
  const endpoint = "/report-schedules";
  const allowed = org.role !== "viewer";
  const [items, setItems] = useState<Schedule[]>();
  const [notices, setNotices] = useState<Notice[]>();
  const [history, setHistory] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string>();
  const [config, setConfig] = useState(initial);
  const [editing, setEditing] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const action = (method: string, value?: unknown) => ({ method, body: value === undefined ? undefined : JSON.stringify(value) });
  const reload = async () => {
    const [schedules, messages] = await Promise.all([
      allowed ? api<Schedule[]>(endpoint + query) : Promise.resolve([]),
      api<Notice[]>("/notifications" + query),
    ]);
    setItems(schedules); setNotices(messages);
    if (selected) setHistory(await api<Run[]>(endpoint + "/" + selected + "/runs" + query));
  };
  useEffect(() => {
    let active = true;
    Promise.all([allowed ? api<Schedule[]>(endpoint + query) : Promise.resolve([]), api<Notice[]>("/notifications" + query)])
      .then(([schedules, messages]) => { if (active) { setItems(schedules); setNotices(messages); } })
      .catch((e: Error) => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [allowed, query]);
  const run = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError(""); setSaved("");
    try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed."); }
    finally { setBusy(false); }
  };
  const change = <K extends keyof Config>(key: K, value: Config[K]) => setConfig((previous) => ({ ...previous, [key]: value }));
  const save = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      const path = endpoint + (editing ? "/" + editing : "") + query;
      await api(path, action(editing ? "PUT" : "POST", config));
      setConfig(initial); setEditing(undefined); await reload(); setSaved("Schedule saved.");
    });
  };
  const download = (id: string) => void run(async () => {
    const report = "/reports/" + id;
    const grant = await api<{ token: string }>(report + "/download-grant" + query, action("POST"));
    const blob = await downloadPdf(report + "/download" + query, grant.token);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = "scheduled-business-performance.pdf"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  return <div className="space-y-4">
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-red-800">{error}</p>}
    {(busy || saved) && <p role="status">{busy ? "Working…" : saved}</p>}
    <button className={button} disabled={busy} onClick={() => void run(reload)}>Refresh history and notifications</button>
    <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
      {allowed && <form onSubmit={save} className={panel + " grid gap-3 sm:grid-cols-2"}>
        <h2 className="text-lg font-semibold sm:col-span-2">{editing ? "Edit schedule" : "New executive report schedule"}</h2>
        <label className="text-sm sm:col-span-2">Schedule name<input className={field} required maxLength={120} value={config.name} onChange={(e) => change("name", e.target.value)} /></label>
        <label className="text-sm">Frequency<select className={field} value={config.cadence} onChange={(e) => change("cadence", e.target.value as Config["cadence"])}><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></label>
        <label className="text-sm">Reporting period<select className={field} value={config.period} onChange={(e) => change("period", e.target.value as Config["period"])}><option value="previous_day">Previous day</option><option value="previous_week">Previous week</option><option value="previous_month">Previous month</option></select></label>
        <label className="text-sm">Business timezone<input className={field} required value={config.timezone} onChange={(e) => change("timezone", e.target.value)} /></label>
        <label className="text-sm">Local delivery time<input className={field} type="time" required value={[config.hour, config.minute].map((x) => String(x).padStart(2, "0")).join(":")} onChange={(e) => {
          const [hour, minute] = e.target.value.split(":").map(Number);
          if (Number.isFinite(minute)) setConfig({ ...config, hour, minute });
        }} /></label>
        {config.cadence === "weekly" && <label className="text-sm">Day of week<select className={field} value={config.weekday} onChange={(e) => change("weekday", Number(e.target.value))}>{weekdays.map((day, i) => <option value={i} key={day}>{day}</option>)}</select></label>}
        {config.cadence === "monthly" && <label className="text-sm">Day of month<input className={field} type="number" min={1} max={28} required value={config.month_day} onChange={(e) => change("month_day", Number(e.target.value))} /></label>}
        <div className="flex flex-wrap gap-2 sm:col-span-2"><button className={primary} disabled={busy}>Save schedule</button>
          {editing && <button type="button" className={button} onClick={() => { setConfig(initial); setEditing(undefined); }}>Cancel edit</button>}</div>
      </form>}
      {allowed && <section className={panel + " min-w-0 space-y-3"}>
        <h2 className="text-lg font-semibold">Report schedules</h2>
        {!items && !error && <p role="status">Loading schedules…</p>}
        {items?.length === 0 && <p>No schedules yet.</p>}
        {items?.map((item) => <article key={item.id} className="space-y-2 rounded-lg border border-slate-200 p-3">
          <h3 className="break-words font-semibold">{item.config.name}</h3>
          <p className="text-sm capitalize">{item.config.cadence} · {item.paused ? "Paused" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: item.config.timezone }).format(new Date(item.next_at))} · {item.config.timezone}</p>
          <div className="flex flex-wrap gap-2">
            <button className={button} disabled={busy} onClick={() => { setConfig(item.config); setEditing(item.id); }}>Edit</button>
            <button className={button} disabled={busy} onClick={() => void run(async () => {
              await api(endpoint + "/" + item.id + query, action("PATCH", { paused: !item.paused })); await reload();
            })}>{item.paused ? "Resume" : "Pause"}</button>
            <button className={button} disabled={busy} aria-expanded={selected === item.id} onClick={() => void run(async () => {
              setSelected(item.id); setHistory(await api<Run[]>(endpoint + "/" + item.id + "/runs" + query));
            })}>Execution history</button>
          </div>
          {selected === item.id && <ul className="space-y-2 text-sm" aria-label="Execution history">
            {!history.length && <li>No executions yet.</li>}
            {history.map((entry) => <li className="flex flex-wrap items-center gap-2 border-t pt-2" key={entry.due_at}>
              <time dateTime={entry.due_at}>{new Date(entry.due_at).toLocaleString()}</time>
              <span className="capitalize">{human(entry.status)}{entry.error_code ? " · " + human(entry.error_code) : ""}</span>
              {entry.report_id && entry.status === "completed" && entry.expires_at && new Date(entry.expires_at) > new Date() &&
                <button className={button} disabled={busy} onClick={() => download(entry.report_id!)}>Download PDF</button>}
              {entry.report_id && entry.status === "failed" && <button className={button} disabled={busy} onClick={() => void run(async () => {
                await api("/reports/" + entry.report_id + "/retry" + query, action("POST"));
                await reload();
              })}>Retry report</button>}
            </li>)}
          </ul>}
        </article>)}
      </section>}
      <section className={panel + " space-y-2 lg:col-span-2"} aria-label="In-app notifications">
        <h2 className="text-lg font-semibold">Notifications</h2>
        {!notices && !error && <p role="status">Loading notifications…</p>}
        {notices?.length === 0 && <p>You have no reporting notifications.</p>}
        <ul className="divide-y">{notices?.map((notice, i) => <li className="py-2 text-sm" key={i}>
          {notice.kind === "data_quality" ? notice.message
            : notice.kind === "stale_dataset" ? "Stale " + notice.domain + " dataset · " + new Date(notice.published_at!).toLocaleDateString()
            : (notice.kind === "schedule" ? "Scheduled execution" : "Report") + " " + notice.status +
              (notice.error_code ? " · " + human(notice.error_code) : "") + " · " + new Date(notice.finished_at!).toLocaleString()}
        </li>)}</ul>
      </section>
    </div>
  </div>;
}

export default function SchedulesPage() {
  return <OrgPage title="Report schedules">{(org) => <Schedules key={org.id} org={org} />}</OrgPage>;
}
