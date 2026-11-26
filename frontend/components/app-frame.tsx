"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Activity, BarChart3, Bell, Boxes, CalendarClock, ChartNoAxesCombined, ChevronDown, ChevronRight,
  Database, FileText, FlaskConical, GitCompare, LayoutDashboard, LogOut, Menu, PanelLeftClose, Plug,
  Receipt, Sparkles, Users, Workflow, X } from "lucide-react";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, type Identity } from "@/lib/workspace-api";
import { EmptyState, GlassButton, GlassSelect, LoadingState, StatusBadge } from "@/components/ui/glass";

const groups = [
  { label: "Business intelligence", links: [
    { title: "Overview", href: "/analytics", icon: LayoutDashboard },
    { title: "Sales analytics", href: "/analytics/sales", icon: BarChart3 },
    { title: "Inventory", href: "/analytics/inventory", icon: Boxes },
    { title: "Receivables", href: "/analytics/receivables", icon: Receipt },
    { title: "Custom dashboards", href: "/dashboards", icon: ChartNoAxesCombined },
  ] },
  { label: "Intelligence", links: [
    { title: "Ask AI", href: "/ask", icon: Sparkles },
    { title: "Multi-domain analysis", href: "/analyses", icon: Workflow },
    { title: "Predictive analytics", href: "/predictive", icon: Activity },
    { title: "AI evaluation", href: "/evaluations", icon: FlaskConical },
  ] },
  { label: "Operations", links: [
    { title: "Data sources", href: "/workspace", icon: Database },
    { title: "Data reconciliation", href: "/reconciliation", icon: GitCompare },
    { title: "PostgreSQL connectors", href: "/connectors", icon: Plug },
    { title: "Executive reports", href: "/reports", icon: FileText },
    { title: "Scheduled reports", href: "/schedules", icon: CalendarClock },
    { title: "Team & settings", href: "/workspace/team", icon: Users },
  ] },
];

type Workspace = { identity: Identity; orgId: string; selectOrg: (value: string) => void; reload: () => Promise<void> };
const WorkspaceContext = createContext<Workspace | null>(null);
export function useWorkspace() {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) throw new Error("Workspace session unavailable");
  return workspace;
}

export function Brand() {
  return <Link href="/" className="brand"><span><Activity size={21} aria-hidden /></span><strong>InsightForge <b>AI</b></strong></Link>;
}

type Notice = { kind: string; id?: string; status?: string; domain?: string; message?: string; finished_at?: string };

function Shell({ identity, reload, children }: { identity: Identity; reload: () => Promise<void>; children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [selection, setSelection] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [mobile, setMobile] = useState(false);
  const mobileToggle = useRef<HTMLButtonElement>(null);
  const mobileClose = useRef<HTMLButtonElement>(null);
  const [service, setService] = useState("Checking API");
  const [activity, setActivity] = useState<{ org: string; notices?: Notice[]; error?: string }>({ org: "" });
  const [signout, setSignout] = useState("");
  const org = identity.organizations.find((item) => item.id === selection) ?? identity.organizations[0];
  const orgId = org?.id ?? "";
  const title = groups.flatMap((group) => group.links).find((link) => link.href === path)?.title ?? "Workspace";

  useEffect(() => {
    if (!mobile) return;
    const toggle = mobileToggle.current;
    mobileClose.current?.focus();
    const viewport = window.matchMedia("(min-width: 901px)");
    const closeOnDesktop = () => { if (viewport.matches) setMobile(false); };
    viewport.addEventListener("change", closeOnDesktop);
    return () => {
      viewport.removeEventListener("change", closeOnDesktop);
      toggle?.focus();
    };
  }, [mobile]);

  useEffect(() => {
    const controller = new AbortController();
    api<{status: string}>("/ready", { signal: controller.signal }).then(({ status }) => setService(status === "ok" ? "API healthy" : "API unavailable"))
      .catch(() => { if (!controller.signal.aborted) setService("API unavailable"); });
    return () => controller.abort();
  }, [path]);

  async function notices() {
    if (!orgId) return;
    setActivity({ org: orgId });
    try {
      setActivity({ org: orgId, notices: await api<Notice[]>("/notifications?organization_id=" + orgId) });
    } catch {
      setActivity({ org: orgId, error: "Notifications are temporarily unavailable." });
    }
  }

  return <WorkspaceContext.Provider value={{ identity, orgId, selectOrg: setSelection, reload }}>
    <div className="app-frame" data-collapsed={collapsed} onKeyDown={(event) => { if (event.key === "Escape") setMobile(false); }}>
      <a className="skip-link" href="#workspace-content">Skip to content</a>
      {mobile && <button aria-label="Close navigation" className="mobile-overlay" onClick={() => setMobile(false)} />}
      <aside className="app-sidebar" data-open={mobile} aria-label="Application sidebar">
        <div className="sidebar-brand"><Brand />
          <button ref={mobileClose} className="icon-button sidebar-close" aria-label="Close navigation" onClick={() => setMobile(false)}><X size={19} /></button>
        </div>
        <nav aria-label="Main navigation" className="sidebar-nav">
          {groups.map((group) => <div className="nav-group" key={group.label}>
            <p>{group.label}</p>
            {group.links.filter(({ href }) => (href !== "/connectors" || org?.role === "admin") &&
              (href !== "/evaluations" || (org && org.role !== "viewer"))).map(({ title, href, icon: Icon }) => <Link key={href} href={href} title={title}
              aria-label={title}
              aria-current={href === path ? "page" : undefined} className="nav-link" onClick={() => setMobile(false)}>
              <Icon size={18} aria-hidden /><span>{title}</span></Link>)}
          </div>)}
        </nav>
        <div className="sidebar-foot"><span className="status-dot" /> <span>InsightForge workspace</span></div>
      </aside>
      <div className="app-content" inert={mobile}>
        <header className="top-navbar">
          <div className="flex min-w-0 items-center gap-3">
            <button className="icon-button desktop-toggle" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}><PanelLeftClose size={19} /></button>
            <button ref={mobileToggle} className="icon-button mobile-toggle" aria-label="Open navigation" aria-expanded={mobile}
              onClick={() => setMobile(!mobile)}><Menu size={21} /></button>
            <nav aria-label="Breadcrumb" className="breadcrumbs"><Link href="/analytics">Workspace</Link>
              <ChevronRight size={14} aria-hidden /><span aria-current="page">{title}</span></nav>
          </div>
          <div className="top-actions">
            <span className="api-indicator" data-offline={service !== "API healthy"}><i />{service}</span>
            {org && <GlassSelect aria-label="Active organization" value={orgId} onChange={(event) => setSelection(event.target.value)}>
              {identity.organizations.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </GlassSelect>}
            {org && <details key={orgId} className="top-popover" onToggle={(event) => { if (event.currentTarget.open) void notices(); }}>
              <summary className="icon-button" aria-label="Notifications"><Bell size={19} aria-hidden /></summary>
              <div className="popover-panel"><h2>Notifications</h2>
                {activity.org !== orgId || (!activity.error && !activity.notices) ? <p role="status">Loading notifications…</p> :
                  activity.error ? <p role="alert">{activity.error}</p> : !activity.notices?.length ? <p>No notifications.</p> :
                  <ul>{activity.notices.map((entry, index) => <li key={index}>
                    {entry.kind === "data_quality" ? entry.message : entry.kind === "stale_dataset" ?
                      <Link href="/workspace">{entry.domain} dataset is more than 7 days old</Link> :
                      <Link href={entry.kind === "schedule" ? "/schedules" : "/reports"}>
                        {entry.kind === "schedule" ? "Scheduled report" : "Executive report"} <StatusBadge status={entry.status ?? "updated"} />
                      </Link>}
                    {entry.finished_at && <time dateTime={entry.finished_at}>{new Date(entry.finished_at).toLocaleString()}</time>}
                  </li>)}</ul>}
              </div>
            </details>}
            <details className="top-popover">
              <summary className="profile-button" aria-label="Account menu"><span>{identity.email.slice(0, 1).toUpperCase()}</span>
                <ChevronDown size={14} aria-hidden /></summary>
              <div className="popover-panel"><strong className="break-all">{identity.email}</strong>
                <p className="capitalize">{org?.role ?? "Account"}</p>
                <button className="glass-button flex items-center gap-2" onClick={() => {
                  setSignout("");
                  void api("/auth/logout", { method: "POST" }).then(() => router.replace("/login"))
                    .catch(() => setSignout("Sign out failed. Try again."));
                }}><LogOut size={16} aria-hidden /> Sign out</button>
                {signout && <p role="alert">{signout}</p>}
              </div>
            </details>
          </div>
        </header>
        <div id="workspace-content" tabIndex={-1}>{children}</div>
      </div>
    </div>
  </WorkspaceContext.Provider>;
}

function Guard({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [identity, setIdentity] = useState<Identity>();
  const [failure, setFailure] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    api<Identity>("/auth/me", { signal: abort.signal }).then(setIdentity).catch((error: unknown) => {
      if (abort.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) router.replace("/login");
      else setFailure(true);
    });
    return () => abort.abort();
  }, [attempt, router]);
  if (!identity) return <main className="session-screen">{failure ?
    <EmptyState title="Workspace unavailable"><p role="alert">We couldn’t verify your session.</p>
      <GlassButton onClick={() => { setFailure(false); setAttempt(attempt + 1); }}>Retry</GlassButton></EmptyState>
    : <LoadingState label="Opening workspace…" />}</main>;
  return <Shell identity={identity} reload={async () => setIdentity(await api<Identity>("/auth/me"))}>{children}</Shell>;
}

export function AppFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return ["/", "/login", "/register"].includes(pathname) ? children : <Guard>{children}</Guard>;
}
