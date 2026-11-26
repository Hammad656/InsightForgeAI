"use client";

import Link from "next/link";
import { CircleCheck, CloudUpload } from "lucide-react";

import { usePathname } from "next/navigation";
import { useWorkspace } from "@/components/app-frame";
import { GlassModal, StatusBadge } from "@/components/ui/glass";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { api, roles, type Organization, type Role } from "@/lib/workspace-api";

type Member = { id: string; email: string; role: Role };
type Domain = "sales" | "inventory" | "payments";
type MappingSuggestion = { mapping: Record<string, string>; missing: string[]; ambiguous: string[] };
type Preview = { id: string; headers: string[]; rows: string[][]; row_count: number; duplicate_rows: number;
  detected_types: Record<string, string>; fields: Record<Domain, string[]>;
  suggested_domain: Domain | null; suggestions: Record<Domain, MappingSuggestion> };
type ImportJob = { id: string; domain: Domain; status: "queued" | "failed" | "published" | "cancelled"; row_count: number | null;
  created_at: string; errors?: { row: number; message: string }[] | null; duplicate?: boolean };
type Dataset = { domain: Domain; active_version: string };
type Upload = { id: string; filename: string; file_type: string; size_bytes: number; uploaded_by: string;
  created_at: string; row_count: number | null; domain: Domain | null; job_id: string | null;
  version_id: string | null; status: string; deletable: boolean };
type Snapshot = { id: string; filename: string; domain: Domain; status: string; row_count: number;
  published_at: string; can_restore: boolean };
type Confirmation = { title: string; impact: string; action: () => Promise<void> };

const input = "glass-input";
const button = "glass-button glass-primary";
const panel = "glass-card grid gap-4";
const domains: Domain[] = ["sales", "inventory", "payments"];

async function datasetHistory(path: string) {
  return Promise.all([api<Upload[]>(`${path}/datasets/uploads`),
    api<Snapshot[]>(`${path}/datasets/versions`), api<ImportJob[]>(`${path}/datasets/imports`)]);
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Request failed";
}

function AdminTools({ organization, teamOnly }: { organization: Organization; teamOnly: boolean }) {
  const path = `/organizations/${organization.id}`;
  const [members, setMembers] = useState<Member[]>([]);
  const [history, setHistory] = useState<ImportJob[]>([]);
  const [preview, setPreview] = useState<Preview>();
  const [domain, setDomain] = useState<Domain | "">("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File>();
  const [replacement, setReplacement] = useState<Upload>();
  const [replacementDomain, setReplacementDomain] = useState<Domain>();
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [versions, setVersions] = useState<Snapshot[]>([]);
  const [confirmation, setConfirmation] = useState<Confirmation>();
  const [toast, setToast] = useState("");
  const uploadPanel = useRef<HTMLElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [job, setJob] = useState<ImportJob>();
  const [invitation, setInvitation] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [team, [files, snapshots, jobs]] = await Promise.all([
          api<Member[]>(`${path}/members`), datasetHistory(path),
        ]);
        if (active) { setMembers(team); setHistory(jobs); setUploads(files); setVersions(snapshots); }
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
        const updated = result.status !== "queued" ? await datasetHistory(path) : undefined;
        if (active) {
          setJob(result);
          if (updated) { setUploads(updated[0]); setVersions(updated[1]); setHistory(updated[2]); }
        }
      } catch (failure) { if (active) setError(errorMessage(failure)); }
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [jobId, jobStatus, path]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function refresh() {
    const [files, snapshots, jobs] = await datasetHistory(path);
    setUploads(files);
    setVersions(snapshots);
    setHistory(jobs);
  }

  function openPreview(result: Preview, target?: Domain) {
    const selected = target ?? result.suggested_domain ?? "";
    setPreview(result);
    setDomain(selected);
    setMapping(selected ? result.suggestions[selected].mapping : {});
    setJob(undefined);
  }

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
    if (!file) return;
    const body = new FormData();
    body.set("file", file);
    const suffix = replacement ? `?replace_upload_id=${replacement.id}` : "";
    const action = async () => {
      openPreview(await api<Preview>(`${path}/datasets/uploads${suffix}`, { method: "POST", body }), replacementDomain);
      setFile(undefined);
      if (fileInput.current) fileInput.current.value = "";
      setReplacement(undefined);
      setToast("File uploaded and ready to review.");
      await refresh();
    };
    if (replacement) setConfirmation({ title: `Replace ${replacement.filename}?`,
      impact: "The unused file will be removed when the new file passes preview validation. Published snapshots stay available.",
      action });
    else void run(action);
  }

  const fields = preview && domain ? preview.fields[domain] : [];
  const selectedColumns = fields.map((field) => mapping[field]).filter(Boolean);
  const duplicateColumns = selectedColumns.length !== new Set(selectedColumns).size;
  const incomplete = selectedColumns.length !== fields.length || duplicateColumns;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!preview || !domain || incomplete) return;
    void run(async () => {
      const result = await api<ImportJob>(`${path}/datasets/imports`, {
        method: "POST", body: JSON.stringify({ upload_id: preview.id, domain, mapping }),
      });
      setJob(result);
      setToast(result.duplicate ? "This import already has a history record." : "Import submitted.");
      setPreview(undefined);
      setReplacementDomain(undefined);
      await refresh();
    });
  }

  return (
    <div className="grid gap-5">
      {toast && <div className="workspace-toast flex items-center gap-2" role="status"><CircleCheck size={20} aria-hidden />{toast}</div>}
      {error && <p role="alert" className="text-red-700">{error}</p>}
      {teamOnly ? <section className={panel}>
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
      </section> : <>
      <section className={panel} ref={uploadPanel}>
        <h2 className="text-xl font-semibold">Upload a dataset</h2>
        <ol aria-label="Upload progress" className="flex flex-wrap gap-3 text-sm text-slate-600">
          {["Select file", "Preview", "Review mapping", "Validate and import", "Import status"].map((step, index) =>
            <li key={step} aria-current={index === (job ? 4 : preview ? (incomplete ? 2 : 3) : file ? 1 : 0) ? "step" : undefined}>
              {index + 1}. {step}</li>)}
        </ol>
        <div className="flex flex-wrap gap-4 text-sm text-indigo-700">
          {domains.map((value) => <a key={value} href={`/samples/${value}.csv`} download>Download {value} sample</a>)}
        </div>
        <form className="upload-drop" onSubmit={upload}
          onDragOver={(event) => event.preventDefault()} onDrop={(event) => {
            event.preventDefault();
            if (!pending) setFile(event.dataTransfer.files[0]);
          }}>
          <span className="metric-icon"><CloudUpload size={20} aria-hidden /></span>
          <label className="grid min-w-0 gap-1">Drop a CSV/XLSX file or choose one (up to 5 MiB)
            <input ref={fileInput} className={input} type="file" name="file" accept=".csv,.xlsx"
              onChange={(event) => setFile(event.target.files?.[0])} />
          </label>
          <button className={button} disabled={pending || !file}>{pending ? "Working…" : "Upload and preview"}</button>
          {file && <span className="break-all text-sm">{file.name}</span>}
          {(replacement || replacementDomain) && <p className="w-full">
            Replacing {replacement?.filename ?? `the ${replacementDomain} snapshot`}
            <button className="ml-2 underline" type="button" onClick={() => {
              setReplacement(undefined); setReplacementDomain(undefined);
            }}>Cancel replacement</button>
          </p>}
        </form>
        {preview && <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-semibold">Preview of {preview.row_count} rows</h3>
            <button className={input} disabled={pending} onClick={() => setPreview(undefined)}>Cancel preview</button>
          </div>
          <p>{preview.suggested_domain ? `Detected ${preview.suggested_domain} from matching headers.`
            : "No unique full header match. Select a dataset type and confirm the mappings."}</p>
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
            <select className={input} value={domain} disabled={pending || !!replacementDomain} onChange={(event) => {
              const next = event.target.value as Domain | "";
              setDomain(next);
              setMapping(next ? preview.suggestions[next].mapping : {});
            }}>
              <option value="">Select a dataset type</option>
              {domains.map((value) => <option key={value}>{value}</option>)}
            </select>
          </label>
          {domain && <form className="grid gap-3" onSubmit={submit}>
            <p>{Object.keys(preview.suggestions[domain].mapping).length} of {fields.length} fields automatically matched.</p>
            {preview.suggestions[domain].ambiguous.length > 0 && <p>Confirm ambiguous matches for {preview.suggestions[domain].ambiguous.join(", ")}.</p>}
            {fields.some((field) => !mapping[field]) && <p>Missing required mappings for {fields.filter((field) => !mapping[field]).join(", ")}.</p>}
            {duplicateColumns && <p role="alert">Assign each field to a different source column.</p>}
            <div className="grid gap-3 sm:grid-cols-2">
              {fields.map((field) => <label key={field} className="grid gap-1">{field}
                <select className={input} name={field} value={mapping[field] ?? ""} required
                  onChange={(event) => setMapping({ ...mapping, [field]: event.target.value })}>
                  <option value="">Select a column</option>
                  {preview.headers.map((header) => <option key={header}>{header}</option>)}
                </select>
              </label>)}
            </div>
            <button className={button} disabled={pending || incomplete}>Validate and import snapshot</button>
          </form>}
        </>}
      </section>
      <section className={panel}>
        <div className="flex flex-wrap justify-between gap-3">
          <h2 className="text-xl font-semibold">Uploaded files</h2>
          <button className={input} disabled={pending} onClick={() => void run(refresh)}>Refresh upload history</button>
        </div>
        {uploads.length === 0 && <p>No uploads yet.</p>}
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead><tr>{["File", "Uploader and date", "Dataset", "Status", "Actions"].map((label) =>
              <th className="p-2" key={label}>{label}</th>)}</tr></thead>
            <tbody>{uploads.map((item) => <tr key={item.id} className="border-t">
              <td className="p-2"><strong>{item.filename}</strong><br />{item.file_type.toUpperCase()} · {item.size_bytes.toLocaleString()} bytes</td>
              <td className="p-2">{item.uploaded_by}<br />{new Date(item.created_at).toLocaleString()}</td>
              <td className="p-2">{item.domain ?? "Unassigned"} · {item.row_count ?? "Unknown"} rows
                {item.version_id && <div className="break-all text-xs">{item.version_id}</div>}</td>
              <td className="p-2"><StatusBadge status={item.status === "staged" ? "uploaded" : item.status} /></td>
              <td className="p-2"><div className="flex flex-wrap gap-2">
                <button className={input} disabled={pending} onClick={() => void run(async () => {
                  setReplacementDomain(undefined);
                  openPreview(await api<Preview>(`${path}/datasets/uploads/${item.id}/preview`));
                  uploadPanel.current?.scrollIntoView();
                })}>Preview {item.filename}</button>
                {item.job_id && <button className={input} disabled={pending} onClick={() => void run(async () => {
                  setJob(await api<ImportJob>(`${path}/jobs/${item.job_id}`));
                })}>Import details</button>}
                {item.deletable && <>
                  <button className={input} disabled={pending} onClick={() => {
                    setReplacement(item); setReplacementDomain(undefined); uploadPanel.current?.scrollIntoView();
                    fileInput.current?.focus();
                  }}>Replace file</button>
                  <button className={input} disabled={pending} onClick={() => setConfirmation({
                    title: `Delete ${item.filename}?`,
                    impact: "This removes the unused staging file and frees its storage quota. Analytics and history stay available.",
                    action: async () => {
                      await api(`${path}/datasets/uploads/${item.id}`, { method: "DELETE" });
                      if (preview?.id === item.id) setPreview(undefined);
                      if (replacement?.id === item.id) setReplacement(undefined);
                      setToast("Upload deleted and staging quota released.");
                      await refresh();
                    },
                  })}>Delete file</button>
                </>}
              </div></td>
            </tr>)}</tbody>
          </table>
        </div>
      </section>
      <section className={panel}>
        <h2 className="text-xl font-semibold">Dataset management</h2>
        {versions.length === 0 && <p>No published datasets yet.</p>}
        {versions.map((version) => <article key={version.id} className="grid gap-2 rounded border p-3">
          <h3 className="font-semibold">{version.domain} · {version.status}</h3>
          <p className="break-all text-sm">{version.filename} · {version.row_count} rows · {new Date(version.published_at).toLocaleString()}<br />Version {version.id}</p>
          <div className="flex flex-wrap gap-2">
            <button className={input} disabled={pending} onClick={() => void run(async () => {
              setJob(await api<ImportJob>(`${path}/jobs/${version.id}`));
            })}>Import details</button>
            {version.status === "active" && <button className={input} disabled={pending} onClick={() => {
              setReplacement(undefined); setReplacementDomain(version.domain); setPreview(undefined);
              uploadPanel.current?.scrollIntoView(); fileInput.current?.focus();
            }}>Upload replacement version</button>}
            {(version.status === "active" || (version.status === "archived" && version.can_restore)) &&
              <button className={input} disabled={pending} onClick={() => {
                const archive = version.status === "active";
                const action = archive ? "archive" : "restore";
                setConfirmation({ title: `${archive ? "Archive" : "Restore"} ${version.filename}?`,
                  impact: archive ? "The snapshot leaves current analytics. No previous version takes its place. Historical evidence stays available."
                    : "This snapshot becomes the current dataset for new analytics. Historical evidence stays available.",
                  action: async () => {
                    await api(`${path}/datasets/versions/${version.id}/${action}`, { method: "POST" });
                    setToast(archive ? "Dataset archived." : "Dataset restored.");
                    await refresh();
                  } });
              }}>{version.status === "active" ? "Archive version" : "Restore version"}</button>}
          </div>
        </article>)}
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
          {job.status === "queued" && <div className="flex flex-wrap gap-2">
            <button className="mt-2 rounded border bg-white px-3 py-2" disabled={pending}
            onClick={() => void run(async () => setJob(await api<ImportJob>(`${path}/jobs/${job.id}/retry`, {
              method: "POST",
            })))}>Retry queued import</button>
            <button className={input} disabled={pending} onClick={() => setConfirmation({
              title: `Cancel queued ${job.domain} import?`,
              impact: "If the worker has already published this job, it cannot be cancelled. The source file and import record are retained.",
              action: async () => {
                setJob(await api<ImportJob>(`${path}/jobs/${job.id}/cancel`, { method: "POST" }));
                await refresh();
              },
            })}>Cancel queued import</button>
          </div>}
          {job.status === "failed" && <p>Review the validation report, then correct the source file or choose a different mapping and submit it again.</p>}
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
      <GlassModal open={!!confirmation} titleId="dataset-confirmation-title" onClose={() => setConfirmation(undefined)}>
        {confirmation && <div className="grid gap-4">
          <h2 id="dataset-confirmation-title" className="text-lg font-semibold">{confirmation.title}</h2>
          <p>{confirmation.impact}</p>
          <div className="flex justify-end gap-2">
            <button className={input} autoFocus onClick={() => setConfirmation(undefined)}>Cancel</button>
            <button className={button} onClick={() => {
              const action = confirmation.action;
              setConfirmation(undefined);
              void run(action);
            }}>Confirm</button>
          </div>
        </div>}
      </GlassModal></>}
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

function DatasetQuality({ orgId }: { orgId: string }) {
  const [quality, setQuality] = useState<{ state: string; message: string }>();
  useEffect(() => {
    if (!orgId) return;
    const controller = new AbortController();
    void api<{ state: string; message: string } | null>("/reconciliation-summary?organization_id=" + orgId,
      { signal: controller.signal }).then((result) => { if (result) setQuality(result); }).catch(() => {});
    return () => controller.abort();
  }, [orgId]);
  return quality ? <section className="glass-card flex flex-wrap items-center justify-between gap-3">
    <div><h2 className="font-semibold">Data quality</h2><p className="text-sm text-slate-600">{quality.message}</p></div>
    <Link href="/reconciliation"><StatusBadge status={quality.state} /></Link>
  </section> : null;
}

export default function Workspace() {
  const { identity, orgId, selectOrg, reload } = useWorkspace();
  const teamOnly = usePathname() === "/workspace/team";
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function run(action: () => Promise<void>) {
    setPending(true);
    setError("");
    try { await action(); }
    catch (failure) { setError(errorMessage(failure)); }
    finally { setPending(false); }
  }

  const organization = identity.organizations.find((item) => item.id === orgId);

  return <main className="module-page">
    <header className="module-header"><h1>{teamOnly ? "Team & settings" : "Data sources"}</h1>
      {!teamOnly && <p>Upload, validate, and manage the snapshots used in your analytics.</p>}</header>
    {error && <p role="alert">{error}</p>}
    {!teamOnly && <DatasetQuality orgId={orgId} key={orgId} />}
    {(teamOnly || !organization) && <section className={panel}>
      <h2 className="text-xl font-semibold">Organizations</h2>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const body = JSON.stringify(Object.fromEntries(new FormData(form)));
        void run(async () => {
          const created = await api<Organization>("/organizations", { method: "POST", body });
          await reload();
          selectOrg(created.id);
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
          await reload();
          selectOrg(id);
          form.reset();
        });
      }}>
        <label className="grid gap-1">Invitation code<input name="code" className={input} maxLength={140} required /></label>
        <button className={button} disabled={pending}>Accept invitation</button>
      </form>
    </section>}
    {organization?.role === "admin" && <AdminTools key={organization.id + teamOnly} organization={organization} teamOnly={teamOnly} />}
    {organization && !teamOnly && organization.role !== "admin" && <PublishedDatasets key={organization.id} organization={organization} />}
  </main>;
}
