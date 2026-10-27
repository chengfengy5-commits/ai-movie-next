import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";
import {
  parseZhangyugeBalanceSnapshot,
  parseZhangyugePatSaveReceipt,
  parseZhangyugePatStatus,
} from "./zhangyugePatHttp";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => payload } as Response;
}

describe("Zhangyuge PAT HTTP adapter", () => {
  it("reads saved balance through the authenticated route with a narrow timeout and safe fields", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "admin-session-token");
    const payload = {
      balance_usd: 0,
      balance_cny: 0,
      used_usd: null,
      used_cny: null,
      quota_per_unit: 500_000,
      usd_exchange_rate: 7.2,
      pat_configured: true,
      api_key_configured: false,
      errors: ["upstream detail containing a private credential"],
      updated_at: "2026-10-27 09:10:11",
      private_extra: "must-not-project",
    };
    const fetcher = vi.fn<typeof fetch>(async (input) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/zhangyuge/balance") return jsonResponse(payload);
      if (url.pathname === "/api/zhangyuge/pat") return jsonResponse({ configured: true });
      throw new Error("Unexpected Zhangyuge route.");
    });
    const service = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher }).zhangyugePat;
    if (service === undefined) throw new Error("Expected the Zhangyuge service.");

    const timeoutSpy = vi.spyOn(globalThis, "setTimeout");
    try {
      const snapshot = await service.getBalance(new AbortController().signal);
      const status = await service.getStatus(new AbortController().signal);

      expect(snapshot).toEqual({
        balanceUsd: 0,
        balanceCny: 0,
        usedUsd: null,
        usedCny: null,
        quotaPerUnit: 500_000,
        usdExchangeRate: 7.2,
        patConfigured: true,
        apiKeyConfigured: false,
        hasErrors: true,
        updatedAt: "2026-10-27 09:10:11",
      });
      expect(status).toEqual({ configured: true });
      expect(JSON.stringify(snapshot)).not.toContain("private credential");
      expect(JSON.stringify(snapshot)).not.toContain("must-not-project");
      expect(fetcher.mock.calls.map(([input, init]) => ({
        url: String(input),
        method: init?.method ?? "GET",
        authorization: new Headers(init?.headers).get("Authorization"),
        body: init?.body,
      }))).toEqual([
        {
          url: "http://127.0.0.1:4175/api/zhangyuge/balance",
          method: "GET",
          authorization: "Bearer admin-session-token",
          body: undefined,
        },
        {
          url: "http://127.0.0.1:4175/api/zhangyuge/pat",
          method: "GET",
          authorization: "Bearer admin-session-token",
          body: undefined,
        },
      ]);
      expect(timeoutSpy.mock.calls.map(([, delay]) => delay)).toEqual([60_000, 15_000]);
      expect(() => parseZhangyugeBalanceSnapshot({ ...payload, used_usd: Number.POSITIVE_INFINITY })).toThrow(ApiError);
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  it("uses the authenticated status and write routes with strict receipts", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "admin-session-token");
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      if (url.pathname !== "/api/zhangyuge/pat") throw new Error("Unexpected Zhangyuge PAT route.");
      const method = init?.method ?? "GET";
      if (method === "GET") return jsonResponse({ configured: false, pat: "must-not-project-secret" });
      return jsonResponse({ ok: true, pat: "server-echo-secret", detail: "ignored" });
    });
    const service = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher }).zhangyugePat;
    if (service === undefined) throw new Error("Expected the Zhangyuge PAT service.");

    const status = await service.getStatus(new AbortController().signal);
    await service.save("fresh-pat-value", new AbortController().signal);

    expect(status).toEqual({ configured: false });
    expect(JSON.stringify(status)).not.toContain("must-not-project-secret");
    expect(fetcher.mock.calls.map(([input, init]) => ({
      url: String(input),
      method: init?.method ?? "GET",
      authorization: new Headers(init?.headers).get("Authorization"),
      body: init?.body,
    }))).toEqual([
      {
        url: "http://127.0.0.1:4175/api/zhangyuge/pat",
        method: "GET",
        authorization: "Bearer admin-session-token",
        body: undefined,
      },
      {
        url: "http://127.0.0.1:4175/api/zhangyuge/pat",
        method: "POST",
        authorization: "Bearer admin-session-token",
        body: JSON.stringify({ pat: "fresh-pat-value" }),
      },
    ]);

    expect(() => parseZhangyugePatStatus({ configured: "false" })).toThrow(ApiError);
    expect(() => parseZhangyugePatSaveReceipt({ ok: false })).toThrow(ApiError);

    const accepted202 = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => jsonResponse({ ok: true }, 202)),
    }).zhangyugePat;
    if (accepted202 === undefined) throw new Error("Expected the Zhangyuge PAT service.");
    await expect(accepted202.save("fresh-pat-value", new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
  });

  it("ignores a late status response after the authenticated session changes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-session-token");
    let resolveResponse!: (response: Response) => void;
    const fetcher = vi.fn<typeof fetch>((_input, _init) => new Promise<Response>((resolve) => { resolveResponse = resolve; }));
    const service = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher }).zhangyugePat;
    if (service === undefined) throw new Error("Expected the Zhangyuge PAT service.");

    const pending = service.getStatus(new AbortController().signal);
    expect(fetcher).toHaveBeenCalledOnce();
    const authorization = new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization");
    storage.setItem(API_TOKEN_KEY, "new-session-token");
    resolveResponse(jsonResponse({ configured: true }));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(authorization).toBe("Bearer old-session-token");
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-session-token");
  });
});
