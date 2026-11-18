import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth } from "../lib/api";
afterEach(() => vi.unstubAllGlobals());
describe("API client", () => {
  it("reads health from the versioned endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "ok" }) });
    vi.stubGlobal("fetch", fetchMock);
    await expect(getHealth()).resolves.toEqual({ status: "ok" });
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v1/health");
  });
  it("rejects unsuccessful responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false }));
    await expect(getHealth()).rejects.toThrow("API unavailable");
  });
});
