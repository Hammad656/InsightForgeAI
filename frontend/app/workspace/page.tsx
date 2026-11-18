"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError, roles, type Identity, type Organization, type Role } from "@/lib/workspace-api";

type Member = { id: string; email: string; role: Role };
type Domain = "sales" | "inventory" | "payments";
type Preview = { id: string; headers: string[]; rows: string[][]; row_count: number; duplicate_rows: number; detected_types: Record<string, string>; fields: Record<Domain, string[]> };
type ImportJob = { id: string; domain: Domain; status: "queued" | "failed" | "published"; row_count: number | null;
  created_at: string; errors?: { row: number; message: string }[] | null; duplicate?: boolean };
type Dataset = { domain: Domain; active_version: string };

const input = "rounded border border-slate-300 bg-white px-3 py-2";
const button = "rounded bg-indigo-700 px-4 py-2 text-white disabled:opacity-50";
const panel = "grid gap-4 rounded-xl border border-slate-200 bg-white p-5";
const domains: Domain[] = ["sales", "inventory", "payments"];

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Request failed";
}

function AdminTools({ organization }: { organization: Organization }) {
  const path = `/organizations/${organization.id}`;
  const [members, setMembers] = useState<Member[]>([]);
  const [history, setHistory] = useState<ImportJob[]>([]);
  const [preview, setPreview] = useState<Preview>();
  const [domain, setDomain] = useState<Domain>("sales");
  const [job, setJob] = useState<ImportJob>();
  const [invitation, setInvitation] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [team, jobs] = await Promise.all([
          api<Member[]>(`${path}/members`), api<ImportJob[]>(`${path}/datasets/imports`),
        ]);
        if (active) { setMembers(team); setHistory(jobs); }
      } catch (failure) { if (active) setError(errorMessage(failure)); }
    }
    void load();
    return () => { active = false; };
  }, [path]);

  const jobId = job?.id;
  const jobStatus = job?.status;
  useEffect(() => {
    if (!jobId || jobStatus !== "queued") return;
    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const result = await api<ImportJob>(`${path}/jobs/${jobId}`);
        if (active) {
          setJob(result);
          setHistory((previous) => previous.map((item) => item.id === result.id ? result : item));
        }
      } catch (failure) { if (active) setError(errorMessage(failure)); }
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [jobId, jobStatus, path]);

  async function run(action: () => Promise<void>) {
    setError("");
    setPending(true);
    try { await action(); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setPending(false); }
  }

  function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = JSON.stringify(Object.fromEntries(new FormData(form)));
    void run(async () => {
      const result = await api<{ token: string }>(`${path}/invitations`, { method: "POST", body });
      setInvitation(`${organization.id}.${result.token}`);
      form.reset();
    });
  }

  function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = new FormData(event.currentTarget);
    void run(async () => {
      setPreview(undefined);
      setPreview(await api<Preview>(`${path}/datasets/uploads`, { method: "POST", body }));
    });
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preview) return;
    const mapping = Object.fromEntries(new FormData(event.currentTarget));
    void run(async () => {
      const result = await api<ImportJob>(`${path}/datasets/imports`, {
        method: "POST", body: JSON.stringify({ upload_id: preview.id, domain, mapping }),
      });
      setJob(result);
      setHistory((previous) => [result, ...previous.filter((item) => item.id !== result.id)]);
    });
  }

  return (
    <div className="grid gap-5">
      {error && <p role="alert" className="text-red-700">{error}</p>}
      <section className={panel}>
        <h2 className="text-xl font-semibold">Team and roles</h2>
        <form onSubmit={invite} className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1">Invite by email<input className={input} name="email" type="email" required /></label>
          <label className="grid gap-1">Role<select className={input} name="role" defaultValue="viewer">
            {roles.map((role) => <option key={role}>{role}</option>)}
          </select></label>
          <button className={button} disabled={pending}>Create invitation</button>
        </form>
        {invitation && <label className="grid gap-1">Share this invitation code privately with the recipient
          <input className={input} readOnly value={invitation} onFocus={(event) => event.target.select()} />
        </label>}
        <ul className="grid gap-2">
          {members.map((member) => <li key={member.id} className="flex flex-wrap items-center gap-2 border-t pt-2">
            <span className="mr-auto break-all">{member.email}</span>
            <select aria-label={`Role for ${member.email}`} className={input} value={member.role} disabled={pending}
              onChange={(event) => {
                const role = event.target.value;
                void run(async () => {
                  await api(`${path}/members/${member.id}`, { method: "PATCH", body: JSON.stringify({ role }) });
                  setMembers(await api<Member[]>(`${path}/members`));
                });
              }}>
              {roles.map((role) => <option key={role}>{role}</option>)}
            </select>
            <button className="rounded border px-3 py-2 text-red-700" disabled={pending}
              aria-label={`Remove ${member.email}`} onClick={() => void run(async () => {
                await api(`${path}/members/${member.id}`, { method: "DELETE" });
                setMembers(await api<Member[]>(`${path}/members`));
              })}>Remove</button>
          </li>)}
        </ul>
      </section>
      <section className={panel}>
        <h2 className="text-xl font-semibold">Upload a dataset</h2>
        <div className="flex flex-wrap gap-4 text-sm text-indigo-700">
          {domains.map((value) => <a key={value} href={`/samples/${value}.csv`} download>Download {value} sample</a>)}
        </div>
        <form className="flex flex-wrap items-end gap-3" onSubmit={upload}>
          <label className="grid gap-1">CSV or XLSX file, up to 5 MiB
            <input className={input} type="file" name="file" accept=".csv,.xlsx" required />
          </label>
          <button className={button} disabled={pending}>Upload and preview</button>
        </form>
        {preview && <>
          <h3 className="font-semibold">Preview of {preview.row_count} rows</h3>
          {preview.duplicate_rows > 0 && <p>{preview.duplicate_rows} repeated source rows detected.</p>}
          <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm">
              <thead><tr>{preview.headers.map((header) => <th className="p-2" key={header}>{header} <span className="font-normal text-slate-600">({preview.detected_types[header]})</span></th>)}</tr></thead>
              <tbody>{preview.rows.map((row, index) => <tr className="border-t" key={index}>
                {row.map((cell, column) => <td className="p-2" key={column}>{cell}</td>)}
              </tr>)}</tbody>
            </table>
          </div>
          <label className="grid gap-1">Dataset type
            <select className={input} value={domain} onChange={(event) => setDomain(event.target.value as Domain)}>
              {domains.map((value) => <option key={value}>{value}</option>)}
            </select>
          </label>
          <form key={`${preview.id}:${domain}`} className="grid gap-3" onSubmit={submit}>
            <div className="grid gap-3 sm:grid-cols-2">
              {preview.fields[domain].map((field) => <label key={field} className="grid gap-1">{field}
                <select className={input} name={field} defaultValue={preview.headers.includes(field) ? field : ""} required>
                  <option value="">Select a column</option>
                  {preview.headers.map((header) => <option key={header}>{header}</option>)}
                </select>
              </label>)}
            </div>
            <button className={button} disabled={pending}>Validate and import snapshot</button>
          </form>
        </>}
      </section>
      <section className={panel}>
        <h2 className="text-xl font-semibold">Import history</h2>
        {history.length === 0 && <p className="text-slate-600">No imports yet.</p>}
        {history.map((item) => <button key={item.id} className="rounded border p-3 text-left"
          disabled={pending} onClick={() => void run(async () => setJob(await api<ImportJob>(`${path}/jobs/${item.id}`)))}>
          {item.domain} · {item.status} · {new Date(item.created_at).toLocaleString()}
        </button>)}
        {job && <div aria-live="polite" className="rounded bg-slate-100 p-4">
          <p>{job.domain} import {job.status}{job.row_count !== null && ` · ${job.row_count} published rows`}</p>
          {job.status === "queued" && <button className="mt-2 rounded border bg-white px-3 py-2" disabled={pending}
            onClick={() => void run(async () => setJob(await api<ImportJob>(`${path}/jobs/${job.id}/retry`, {
              method: "POST",
            })))}>Retry queued import</button>}
          {job.duplicate && <p>This file and mapping already have an import record.</p>}
          {job.errors && <details open>
            <summary>Validation report ({job.errors.length})</summary>
            <a className="text-indigo-700" download={`import-${job.id}-errors.json`}
              href={`data:application/json,${encodeURIComponent(JSON.stringify(job.errors, null, 2))}`}>Download report</a>
            <ul className="max-h-64 overflow-auto">{job.errors.map((item) =>
              <li key={item.row}>Row {item.row} · {item.message}</li>)}</ul>
          </details>}
        </div>}
      </section>
    </div>
  );
}

function PublishedDatasets({ organization }: { organization: Organization }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Dataset[]>(`/organizations/${organization.id}/datasets`)
      .then((result) => { if (active) setDatasets(result); })
      .catch((failure) => { if (active) setError(errorMessage(failure)); });
    return () => { active = false; };
  }, [organization.id]);
  return <section className={panel}>
    <h2 className="text-xl font-semibold">Published datasets</h2>
    {error && <p role="alert">{error}</p>}
    {datasets.length === 0 && <p>No published datasets yet.</p>}
    {datasets.map((dataset) => <p key={dataset.domain}>{dataset.domain} · {dataset.active_version}</p>)}
  </section>;
}

export default function Workspace() {
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity>();
  const [selected, setSelected] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    api<Identity>("/auth/me").then((result) => { if (active) setIdentity(result); }).catch((failure) => {
      if (!active) return;
      if (failure instanceof ApiError && failure.status === 401) router.replace("/login");
      else setError(errorMessage(failure));
    });
    return () => { active = false; };
  }, [router]);

  async function run(action: () => Promise<void>) {
    setPending(true);
    setError("");
    try { await action(); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setPending(false); }
  }

  const organization = identity?.organizations.find((item) => item.id === selected) ?? identity?.organizations[0];

  return <main className="mx-auto grid max-w-5xl gap-5 px-6 py-10">
    <header className="flex flex-wrap items-center justify-between gap-3">
      <div><h1 className="text-3xl font-semibold">Workspace</h1><p>{identity?.email}</p></div>
      {identity && <button className={input} disabled={pending} onClick={() => void run(async () => {
        await api("/auth/logout", { method: "POST" });
        router.replace("/login");
      })}>Sign out</button>}
    </header>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {identity && <section className={panel}>
      <h2 className="text-xl font-semibold">Organizations</h2>
      {organization && <label className="grid gap-1">Active organization
        <select className={input} value={organization.id} onChange={(event) => setSelected(event.target.value)}>
          {identity.organizations.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.role}</option>)}
        </select>
      </label>}
      <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const body = JSON.stringify(Object.fromEntries(new FormData(form)));
        void run(async () => {
          const created = await api<Organization>("/organizations", { method: "POST", body });
          setIdentity(await api<Identity>("/auth/me"));
          setSelected(created.id);
          form.reset();
        });
      }}>
        <label className="grid gap-1">New organization name<input name="name" className={input} maxLength={120} required /></label>
        <button className={button} disabled={pending}>Create organization</button>
      </form>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const code = String(new FormData(form).get("code")).trim();
        const [id, token] = code.split(".");
        if (!/^[\da-f-]{36}$/i.test(id) || !token) { setError("Enter a valid invitation code."); return; }
        void run(async () => {
          await api(`/organizations/${id}/invitations/accept`, { method: "POST", body: JSON.stringify({ token }) });
          setIdentity(await api<Identity>("/auth/me"));
          setSelected(id);
          form.reset();
        });
      }}>
        <label className="grid gap-1">Invitation code<input name="code" className={input} maxLength={140} required /></label>
        <button className={button} disabled={pending}>Accept invitation</button>
      </form>
    </section>}
    {organization?.role === "admin" && <AdminTools key={organization.id} organization={organization} />}
    {organization && organization.role !== "admin" && <PublishedDatasets key={organization.id} organization={organization} />}
  </main>;
}
