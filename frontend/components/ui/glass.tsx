"use client";

import { useEffect, useRef, type ComponentProps, type ReactNode } from "react";
import { Database, LoaderCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export const glass = {
  card: "glass-card",
  input: "glass-input",
  button: "glass-button",
  primary: "glass-button glass-primary",
};

export function GlassCard({ className, ...props }: ComponentProps<"section">) {
  return <section className={cn(glass.card, className)} {...props} />;
}
export function GlassButton({ className, ...props }: ComponentProps<"button">) {
  return <button className={cn(glass.primary, className)} {...props} />;
}
export function GlassInput({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(glass.input, className)} {...props} />;
}
export function GlassSelect({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(glass.input, className)} {...props} />;
}
export function StatusBadge({ status }: { status: string }) {
  const tone = /failed|error|blocked|critical/.test(status) ? "error" :
    /queued|pending|running|warning|stale|high/.test(status) ? "warning" :
    /published|completed|active|healthy|connected|passed/.test(status) ? "success" : "neutral";
  return <span className="status-badge" data-tone={tone}>{status.replaceAll("_", " ")}</span>;
}
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return <GlassCard className="empty-state"><Database size={22} aria-hidden />
    <h2>{title}</h2>{children && <div>{children}</div>}</GlassCard>;
}
export function LoadingState({ label }: { label: string }) {
  return <GlassCard role="status" className="flex items-center gap-3">
    <LoaderCircle aria-hidden className="size-5 animate-spin text-cyan-300 motion-reduce:animate-none" />{label}
  </GlassCard>;
}
export function DataTable({ children, ...props }: ComponentProps<"table">) {
  return <div className="glass-table"><table {...props}>{children}</table></div>;
}
export function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return <GlassCard><h3 className="mb-4 font-semibold">{title}</h3>{children}</GlassCard>;
}
export function MetricCard({ label, value, icon, detail }: { label: string; value: ReactNode; icon: ReactNode; detail?: ReactNode }) {
  return <GlassCard className="metric-card"><div className="metric-icon">{icon}</div>
    <h3>{label}</h3><p className="metric-value">{value}</p>{detail && <div className="text-sm">{detail}</div>}
  </GlassCard>;
}

export function GlassModal({ open, titleId, onClose, children }: {
  open: boolean; titleId: string; onClose: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  return <dialog ref={dialog} aria-labelledby={titleId} onCancel={onClose}
    className="glass-modal">{children}</dialog>;
}
