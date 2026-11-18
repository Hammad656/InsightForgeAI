"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { api } from "@/lib/workspace-api";

export function AuthForm({ register = false }: { register?: boolean }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.currentTarget));
    setPending(true);
    setError("");
    try {
      await api(register ? "/auth/register" : "/auth/login", { method: "POST", body: JSON.stringify(body) });
      router.replace("/workspace");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Sign-in failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <Link className="text-indigo-700" href="/">InsightForge AI</Link>
      <h1 className="mb-6 mt-3 text-3xl font-semibold">{register ? "Create your account" : "Sign in"}</h1>
      <form onSubmit={submit} className="grid gap-4 rounded-xl border bg-white p-6">
        <label className="grid gap-1">Email
          <input className="rounded border p-2" name="email" type="email" autoComplete="email" required />
        </label>
        <label className="grid gap-1">Password
          <input className="rounded border p-2" name="password" type="password"
            autoComplete={register ? "new-password" : "current-password"} minLength={12} maxLength={128} required />
        </label>
        {register && <p className="text-sm text-slate-600">Use at least 12 characters.</p>}
        {error && <p role="alert" className="text-red-700">{error}</p>}
        <button className="rounded bg-indigo-700 p-2 text-white disabled:opacity-50" disabled={pending}>
          {register ? "Create account" : "Sign in"}
        </button>
      </form>
      <Link className="mt-4 inline-block text-indigo-700" href={register ? "/login" : "/register"}>
        {register ? "Sign in to an existing account" : "Create an account"}
      </Link>
    </main>
  );
}
