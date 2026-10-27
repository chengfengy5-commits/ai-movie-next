import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";
import { parseZhangyugePatSaveReceipt, parseZhangyugePatStatus } from "./zhangyugePatHttp";

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
