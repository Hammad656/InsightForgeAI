"use client";

import Link from "next/link";
import { ChartNoAxesCombined, Database, Eye, EyeOff, ShieldCheck, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api, ApiError } from "@/lib/workspace-api";

import { Brand } from "@/components/app-frame";
import { GlassButton, GlassInput } from "@/components/ui/glass";

export function AuthForm({ register = false }: { register?: boolean }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [visible, setVisible] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const body = Object.fromEntries(new FormData(event.currentTarget));
    setPending(true);
    setError("");
    try {
      await api(register ? "/auth/register" : "/auth/login", { method: "POST", body: JSON.stringify(body) });
      router.replace("/workspace");
    } catch (failure) {
      setError(failure instanceof ApiError && failure.status < 500 ? failure.message :
        "Authentication is temporarily unavailable. Wait a moment, then try again.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="auth-layout">
      <aside className="auth-story">
        <Brand />
        <div className="auth-intro">
          <p className="auth-kicker"><Sparkles size={17} aria-hidden /> AI-powered business intelligence</p>
          <h1>See the whole <em>business.</em></h1>
          <p>Bring sales, inventory, and receivables together. Ask questions and trace each answer to its source.</p>
          <div className="auth-visual" aria-hidden="true">
            <svg viewBox="0 0 560 190" preserveAspectRatio="none">
              <defs>
                <linearGradient id="auth-curve"><stop stopColor="#8b5cf6"/><stop offset="1" stopColor="#22d3ee"/></linearGradient>
                <linearGradient id="auth-area" x2="0" y2="1"><stop stopColor="#8b5cf6" stopOpacity=".42"/><stop offset="1" stopColor="#22d3ee" stopOpacity="0"/></linearGradient>
              </defs>
              <path d="M0 39H560 M0 83H560 M0 127H560 M0 171H560 M70 0V190 M175 0V190 M280 0V190 M385 0V190 M490 0V190" stroke="#94a3b8" strokeOpacity=".17" fill="none"/>
              <path d="M0 152 C55 168 66 103 114 117 S171 146 218 98 S278 101 317 93 S372 23 410 54 S469 91 510 38 S544 24 560 18 V190H0Z" fill="url(#auth-area)"/>
              <path d="M0 152 C55 168 66 103 114 117 S171 146 218 98 S278 101 317 93 S372 23 410 54 S469 91 510 38 S544 24 560 18" stroke="url(#auth-curve)" strokeWidth="3" fill="none"/>
              <circle cx="317" cy="93" r="5" fill="#c4b5fd"/>
              <circle cx="510" cy="38" r="5" fill="#67e8f9"/>
            </svg>
            <span><ChartNoAxesCombined size={19} /> Business analytics</span>
          </div>
        </div>
        <div className="auth-features">
          <div><Database size={18} aria-hidden /><span>Versioned source data</span></div>
          <div><ShieldCheck size={18} aria-hidden /><span>Evidence-backed answers</span></div>
        </div>
      </aside>
      <div className="auth-stage">
      <section className="glass-card auth-panel" aria-labelledby="auth-title">
      <div className="metric-icon"><ShieldCheck size={20} aria-hidden /></div>
      <h2 id="auth-title">{register ? "Create your account" : "Sign in"}</h2>
      <p className="auth-caption">{register ? "Set up your InsightForge workspace." : "Access your InsightForge workspace."}</p>
      <form onSubmit={submit} aria-busy={pending} className="grid gap-4">
        <label className="grid gap-1.5 text-sm font-medium text-slate-800">Email
          <GlassInput className="w-full" name="email" type="email" autoComplete="email" disabled={pending} required />
        </label>
        <div className="grid gap-1.5">
          <label htmlFor="auth-password" className="text-sm font-medium text-slate-800">Password</label>
          <div className="relative">
            <GlassInput id="auth-password" className="w-full pr-12" name="password" type={visible ? "text" : "password"}
              autoComplete={register ? "new-password" : "current-password"} minLength={register ? 12 : undefined}
              maxLength={128} disabled={pending} aria-describedby={register ? "password-hint" : undefined} required />
            <button type="button" className="absolute inset-y-1 right-1 rounded-md px-3 text-slate-600 hover:bg-slate-100"
              aria-label={visible ? "Hide password" : "Show password"} onClick={() => setVisible(!visible)}>
              {visible ? <EyeOff aria-hidden="true" size={18} /> : <Eye aria-hidden="true" size={18} />}
            </button>
          </div>
          {register && <p id="password-hint" className="text-sm text-slate-600">Use at least 12 characters.</p>}
        </div>
        {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
        <GlassButton type="submit" disabled={pending}>
          {pending ? (register ? "Creating account…" : "Signing in…") : register ? "Create account" : "Sign in"}
        </GlassButton>
      </form>
      <Link className="mt-5 inline-block text-sm font-semibold text-teal-800 underline decoration-teal-300 underline-offset-4"
        href={register ? "/login" : "/register"}>
        {register ? "Sign in to an existing account" : "Create an account"}
      </Link>
      </section>
      </div>
    </main>
  );
}
