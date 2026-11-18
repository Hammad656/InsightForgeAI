export type HealthResponse = { status: "ok" | "not_ready" };
export async function getHealth(): Promise<HealthResponse> {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  const response = await fetch(`${base}/api/v1/health`, { cache: "no-store" });
  if (!response.ok) throw new Error("API unavailable");
  return response.json() as Promise<HealthResponse>;
}
