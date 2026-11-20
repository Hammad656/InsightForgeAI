"use client";

import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";
import { api, type Identity, type Organization } from "@/lib/workspace-api";

export const field = "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm";
export const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-semibold hover:bg-teal-50 disabled:opacity-50";
export const primary = "rounded-lg bg-teal-800 px-4 py-2 text-sm font-semibold text-white hover:bg-teal-900 disabled:opacity-50";
export const panel = "rounded-xl border border-slate-200 bg-white p-5 shadow-sm";

export function OrgPage({ title, children }: { title: string; children: (org: Organization, actor: string) => ReactNode }) {
  const [identity, setIdentity] = useState<Identity>();
  const [org, setOrg] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<Identity>("/auth/me").then((value) => {
      if (active) { setIdentity(value); setOrg(value.organizations[0]?.id ?? ""); }
    }).catch(() => { if (active) setError("Sign in to open your organization workspace."); });
    return () => { active = false; };
  }, []);
  const selected = identity?.organizations.find((item) => item.id === org);
  return <main className="mx-auto max-w-7xl space-y-5 px-4 py-7 sm:px-8">
    <nav className="flex flex-wrap gap-5 text-sm font-semibold text-teal-800" aria-label="Workspace navigation">
      <Link href="/workspace">Workspace</Link><Link href="/analytics">Business analytics</Link>
      <Link href="/dashboards">Custom dashboards</Link><Link href="/reports">Reports</Link>
      <Link href="/schedules">Schedules</Link>
    </nav>
    <header className="flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 pb-4">
      <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
      {identity?.organizations.length ? <label className="text-sm font-semibold">Organization
        <select className={field} value={org} onChange={(event) => setOrg(event.target.value)}>
          {identity.organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label> : null}
    </header>
    {error ? <p role="alert">{error} <Link href="/login" className="underline">Sign in</Link></p>
      : !identity ? <p role="status">Loading workspace…</p>
      : selected ? children(selected, identity.id) : <p>Create an organization in the workspace to get started.</p>}
  </main>;
}
