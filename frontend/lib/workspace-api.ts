export type Role = "admin" | "analyst" | "viewer";
export const roles: Role[] = ["admin", "analyst", "viewer"];
export type Organization = { id: string; name: string; role: Role };
export type Identity = { id: string; email: string; organizations: Organization[] };

const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

let refresh: Promise<Response> | undefined;

function request(path: string, init: RequestInit) {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== "GET") headers.set("X-CSRF", "1");
  if (typeof init.body === "string") headers.set("Content-Type", "application/json");
  return fetch(`${base}/api/v1${path}`, { ...init, headers, cache: "no-store", credentials: "include" });
}

async function responseFor(path: string, init: RequestInit): Promise<Response> {
  let response = await request(path, init);
  if (response.status === 401 && !/^\/auth\/(login|register|logout|refresh)$/.test(path)) {
    refresh ??= request("/auth/refresh", { method: "POST" }).finally(() => { refresh = undefined; });
    if ((await refresh).ok) response = await request(path, init);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.[0]?.msg;
    throw new ApiError(message ?? "Request could not be completed", response.status);
  }
  return response;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await responseFor(path, init);
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

export async function downloadPdf(path: string, token: string): Promise<Blob> {
  const response = await responseFor(path, { method: "POST", body: JSON.stringify({ token }) });
  if (!response.headers.get("Content-Type")?.startsWith("application/pdf")) {
    throw new Error("The response was not a PDF.");
  }
  return response.blob();
}
