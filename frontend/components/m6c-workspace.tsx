"use client";

import Link from "next/link";
import { type ReactNode } from "react";
import { useWorkspace } from "@/components/app-frame";
import { EmptyState, glass } from "@/components/ui/glass";
import { type Organization } from "@/lib/workspace-api";

export const field = glass.input + " w-full";
export const button = glass.button;
export const primary = glass.primary;
export const panel = glass.card;

export function OrgPage({ title, children }: { title: string; children: (org: Organization, actor: string) => ReactNode }) {
  const { identity, orgId } = useWorkspace();
  const selected = identity.organizations.find((item) => item.id === orgId);
  return <main className="module-page">
    <header className="module-header"><h1>{title}</h1></header>
    {selected ? children(selected, identity.id) : <EmptyState title="Create or join an organization">
      <Link className="underline" href="/workspace/team">Open team settings</Link></EmptyState>}
  </main>;
}
