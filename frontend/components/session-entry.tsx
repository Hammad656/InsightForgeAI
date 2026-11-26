"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/workspace-api";

export function SessionEntry({ children }: { children?: ReactNode }) {
  const router = useRouter();
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<"checking" | "guest" | "unavailable">("checking");
  const hasForm = children != null;

  useEffect(() => {
    let active = true;
    api("/auth/me", { signal: AbortSignal.timeout(8000) }).then(() => {
      if (active) router.replace("/workspace");
    }).catch((error: unknown) => {
      if (!active) return;
      if (error instanceof ApiError && error.status === 401) {
        if (hasForm) setStatus("guest");
        else router.replace("/login");
      } else setStatus("unavailable");
    });
    return () => { active = false; };
  }, [attempt, hasForm, router]);

  if (status === "guest") return children;
  return <main className="session-screen">
    <section className="glass-card w-full max-w-sm">
      <h1 className="text-xl font-semibold text-slate-900">InsightForge AI</h1>
      {status === "unavailable" ? <>
        <p role="alert" className="mt-3 text-sm text-slate-700">We couldn’t check your session. Make sure the API is running and try again.</p>
        <button className="glass-button glass-primary mt-4"
          onClick={() => { setStatus("checking"); setAttempt((previous) => previous + 1); }}>Retry session check</button>
      </> : <p role="status" className="mt-3 text-sm text-slate-600">Checking your session…</p>}
    </section>
  </main>;
}
