"use client";

import { useEffect } from "react";
import { useReportWebVitals } from "next/web-vitals";

const api = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const performanceMetrics = new Set(["FCP", "LCP", "INP", "TTFB"]);

function send(name: string, value: number) {
  if (!Number.isFinite(value) || value < 0 || value > 120_000) return;
  void fetch(`${api}/api/v1/telemetry/browser`, {
    method: "POST",
    credentials: "omit",
    referrerPolicy: "no-referrer",
    headers: { "Content-Type": "application/json", "X-CSRF": "1" },
    body: JSON.stringify({ name, value: Math.round(value) }),
    keepalive: true,
  }).catch(() => { /* A telemetry outage does not interrupt the application. */ });
}

export default function BrowserTelemetry() {
  useReportWebVitals(({ name, value }) => {
    if (performanceMetrics.has(name)) send(name, value);
  });
  useEffect(() => {
    const runtime = () => send("runtime_error", 1);
    const rejected = () => send("unhandled_rejection", 1);
    window.addEventListener("error", runtime, { once: true });
    window.addEventListener("unhandledrejection", rejected, { once: true });
    return () => {
      window.removeEventListener("error", runtime);
      window.removeEventListener("unhandledrejection", rejected);
    };
  }, []);
  return null;
}
