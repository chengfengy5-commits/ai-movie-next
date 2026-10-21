import { afterEach, describe, expect, it, vi } from "vitest";
import { createAssetReplacementHttpPort } from "./assetReplacementHttp";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function response(payload: unknown, status = 200): Response {
  return { status, ok: status >= 200 && status < 300, json: async () => payload } as Response;
}

describe("asset replacement HTTP port", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends the existing single-chapter endpoint and exact request body once", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "run-token");
    const requests: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      requests.push({ url: input as URL, init: init ?? {} });
      return response({ message: "已替换章节素材" });
    };
    const run = createAssetReplacementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher }).beginRun();

    await expect(run.replaceChapter("chapter/one", {
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "character",
    }, new AbortController().signal)).resolves.toEqual({ message: "已替换章节素材" });

    expect(requests).toHaveLength(1);
    expect(requests[0]?.url.pathname).toBe("/api/chapters/chapter%2Fone/replace-asset");
    expect(requests[0]?.init.method).toBe("POST");
    expect((requests[0]?.init.headers as Headers).get("Authorization")).toBe("Bearer run-token");
    expect(JSON.parse(String(requests[0]?.init.body))).toEqual({
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "character",
    });
  });

  it("treats a malformed success response and server failures as unknown", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "run-token");
    const fetcher: typeof fetch = vi.fn(async () => response({ detail: "missing receipt" }, 200));
    const run = createAssetReplacementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher }).beginRun();

    await expect(run.replaceChapter("chapter-1", {
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "prop",
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    expect(fetcher).toHaveBeenCalledTimes(1);

    const serverFailure = createAssetReplacementHttpPort("http://127.0.0.1:4175/api", {
      storage,
      fetcher: async () => response({}, 500),
    }).beginRun();
    await expect(serverFailure.replaceChapter("chapter-2", {
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "scene",
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown", status: 500 });
  });

  it("classifies a definitive authorization rejection without retrying", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "run-token");
    const fetcher: typeof fetch = vi.fn(async () => response({}, 403));
    const run = createAssetReplacementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher }).beginRun();

    await expect(run.replaceChapter("chapter-1", {
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "scene",
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "rejected", status: 403 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("keeps a run bound to the token captured when it began", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    const fetcher: typeof fetch = vi.fn(async () => response({ message: "ok" }));
    const run = createAssetReplacementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher }).beginRun();
    storage.setItem(API_TOKEN_KEY, "new-token");

    expect(run.isCurrent()).toBe(false);
    await expect(run.replaceChapter("chapter-1", {
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "character",
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("classifies invalid authorization header data as not sent", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "invalid\r\ntoken");
    const fetcher: typeof fetch = vi.fn(async () => response({ message: "should not be called" }));
    const run = createAssetReplacementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher }).beginRun();

    await expect(run.replaceChapter("chapter-1", {
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "character",
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("keeps an abort after dispatch in the unknown result class", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "run-token");
    const fetcher: typeof fetch = (_input, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    });
    const run = createAssetReplacementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher }).beginRun();
    const controller = new AbortController();
    const pending = run.replaceChapter("chapter-1", {
      old_asset_id: "old-id",
      new_asset_id: "new-id",
      asset_type: "character",
    }, controller.signal);
    controller.abort();

    await expect(pending).rejects.toMatchObject({ kind: "unknown" });
  });
});
