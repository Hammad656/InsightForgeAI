"use client";
import { useEffect, useState } from "react";
import { getHealth } from "@/lib/api";
export function ApiStatus() {
  const [status, setStatus] = useState("Checking API");
  useEffect(() => { let active = true; getHealth().then(() => { if (active) setStatus("API connected"); }).catch(() => { if (active) setStatus("API unavailable"); }); return () => { active = false; }; }, []);
  return <span role="status" className="text-xs font-medium text-slate-500">{status}</span>;
}
