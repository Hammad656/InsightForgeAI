import { afterEach, expect, it, vi } from "vitest";

import { api, ApiError } from "../lib/workspace-api";

afterEach(() => vi.unstubAllGlobals());

it("includes credentials, protects mutations, and keeps multipart encoding browser-managed", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  const body = new FormData();
  body.set("file", "synthetic");
  await api("/organizations/example/datasets/uploads", { method: "POST", body });
  const [, init] = fetchMock.mock.calls[0];
  expect(init.credentials).toBe("include");
  expect(init.headers.get("X-CSRF")).toBe("1");
  expect(init.headers.has("Content-Type")).toBe(false);
});

it("refreshes an expired session and retries the original request", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(null, { status: 401 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json({ email: "example@example.test" }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(api("/auth/me")).resolves.toEqual({ email: "example@example.test" });
  expect(fetchMock.mock.calls.map(([url]) => new URL(url).pathname)).toEqual([
    "/api/v1/auth/me", "/api/v1/auth/refresh", "/api/v1/auth/me",
  ]);
});

it("surfaces registration errors without attempting a session refresh", async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ detail: "Account unavailable" }, { status: 409 }));
  vi.stubGlobal("fetch", fetchMock);
  await expect(api("/auth/register", { method: "POST", body: "{}" })).rejects.toEqual(
    new ApiError("Account unavailable", 409),
  );
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
