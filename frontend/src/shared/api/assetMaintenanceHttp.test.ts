// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  parseAssetDuplicateList,
  parseAssetIgnoredDuplicateList,
  parseAssetOrphanCheckResponse,
  parseMergedAssetReceipt,
  parseSetAssetDuplicateIgnoredReceipt,
  toAssetMaintenanceType,
} from "./assetMaintenance";
import { createAssetMaintenanceHttpPort } from "./assetMaintenanceHttp";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

const duplicateList = {
  type: "character",
  total: 12,
  ignored_total: 1,
  groups: [{
    index: 0,
    items: [{
      id: "character-a",
      name: "林简",
      description: "旧城档案员",
      image_url: null,
      ref_count: 4,
      aliases: ["小林"],
      canonical_key: null,
    }],
  }],
};

describe("asset maintenance HTTP port", () => {
  it("calls all five routes with the exact wire contract and does not invent merge aliases", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "session-token");
    const calls: Array<{ method: string; url: URL; body: unknown; authorization: string | null }> = [];
    const fetcher: typeof fetch = vi.fn(async (input, init) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? undefined : JSON.parse(String(init.body)) as unknown;
      calls.push({
        method,
        url,
        body,
        authorization: new Headers(init?.headers).get("Authorization"),
      });
      if (url.pathname.endsWith("/orphan-check")) return jsonResponse({ orphans: [{ type: "character", id: "character-a" }] });
      if (url.pathname.endsWith("/duplicates/ignore")) {
        return jsonResponse(body !== null && typeof body === "object" && "ignored" in body && body.ignored === true
          ? { ignored: true, pairs_added: 1, pairs_total: 2 }
          : { ignored: false, pairs_removed: 1, pairs_total: 1 });
      }
      if (url.pathname.endsWith("/duplicates/ignored")) {
        return jsonResponse({
          type: "character",
          total: 1,
          items: [{
            id: "ignored-pair",
            asset_id_a: "character-a",
            asset_id_b: "character-b",
            name_a: "林简",
            name_b: "小林",
            image_a: null,
            image_b: "http://127.0.0.1:4175/media/character-b.jpg",
            created_at: null,
          }],
        });
      }
      if (url.pathname.endsWith("/duplicates")) return jsonResponse(duplicateList);
      return jsonResponse({
        message: "合并完成",
        keep_id: "character-a",
        merged_count: 1,
        redirected_frames: 3,
        fused_updated: 2,
        aliases_added: 1,
        dropped_images: [{ id: "character-b", name: "小林" }],
        aliases: ["小林"],
      });
    });
    const port = createAssetMaintenanceHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const signal = new AbortController().signal;
    const seriesId = "series /?雪";

    await expect(port.checkOrphans(seriesId, {
      items: [{ type: "character", id: "character-a" }],
    }, signal)).resolves.toEqual({ orphans: [{ type: "character", id: "character-a" }] });
    await port.listDuplicates(seriesId, "character", signal);
    await port.listIgnoredDuplicates(seriesId, "character", signal);
    await port.setDuplicateIgnored(seriesId, { type: "character", ids: ["character-a", "character-b"], ignored: true }, signal);
    await port.setDuplicateIgnored(seriesId, { type: "character", ids: ["character-a", "character-b"], ignored: false }, signal);
    await port.mergeAssets(seriesId, { type: "character", keep_id: "character-a", merge_ids: ["character-b"] }, signal);
    await port.mergeAssets(seriesId, {
      type: "character",
      keep_id: "character-a",
      merge_ids: ["character-b"],
      keep_aliases: [],
    }, signal);

    expect(calls.map(({ method, url }) => `${method} ${url.pathname}`)).toEqual([
      "POST /api/series/series%20%2F%3F%E9%9B%AA/assets/orphan-check",
      "GET /api/series/series%20%2F%3F%E9%9B%AA/assets/duplicates",
      "GET /api/series/series%20%2F%3F%E9%9B%AA/assets/duplicates/ignored",
      "POST /api/series/series%20%2F%3F%E9%9B%AA/assets/duplicates/ignore",
      "POST /api/series/series%20%2F%3F%E9%9B%AA/assets/duplicates/ignore",
      "POST /api/series/series%20%2F%3F%E9%9B%AA/assets/merge",
      "POST /api/series/series%20%2F%3F%E9%9B%AA/assets/merge",
    ]);
    expect(calls[1]?.url.searchParams.get("type")).toBe("character");
    expect(calls[1]?.url.searchParams.get("threshold")).toBe("0.82");
    expect(calls[2]?.url.searchParams.get("type")).toBe("character");
    expect(calls[0]?.body).toEqual({ items: [{ type: "character", id: "character-a" }] });
    expect(calls[5]?.body).toEqual({ type: "character", keep_id: "character-a", merge_ids: ["character-b"] });
    expect(calls[6]?.body).toEqual({
      type: "character",
      keep_id: "character-a",
      merge_ids: ["character-b"],
      keep_aliases: [],
    });
    expect(calls.every((call) => call.authorization === "Bearer session-token")).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(7);
  });

  it("preserves response distinctions and rejects malformed or mismatched receipts", () => {
    expect((["characters", "scenes", "props"] as const).map(toAssetMaintenanceType))
      .toEqual(["character", "scene", "prop"]);
    const parsed = parseAssetDuplicateList(duplicateList, "character");
    expect(parsed.total).toBe(12);
    expect(parsed.groups).toHaveLength(1);
    expect(parsed.groups[0]?.items[0]?.ref_count).toBe(4);
    expect(parsed.groups[0]?.items[0]?.image_url).toBeNull();

    expect(parseAssetOrphanCheckResponse({ orphans: [] })).toEqual({ orphans: [] });
    expect(parseAssetIgnoredDuplicateList({
      type: "character",
      total: 0,
      items: [],
    }, "character")).toEqual({ type: "character", total: 0, items: [] });
    expect(parseSetAssetDuplicateIgnoredReceipt({ ignored: false, pairs_removed: 0, pairs_total: 0 }, false))
      .toEqual({ ignored: false, pairs_removed: 0, pairs_total: 0 });
    expect(() => parseAssetDuplicateList({ ...duplicateList, type: "prop" }, "character")).toThrow();
    expect(() => parseAssetOrphanCheckResponse({ orphans: [{ type: "character", id: "" }] })).toThrow();
    expect(() => parseSetAssetDuplicateIgnoredReceipt({ ignored: false, pairs_added: 1, pairs_total: 1 }, true)).toThrow();
    expect(() => parseMergedAssetReceipt({
      message: "ok",
      keep_id: "other-id",
      merged_count: 1,
      redirected_frames: 0,
      fused_updated: 0,
      aliases_added: 0,
      dropped_images: [],
      aliases: [],
    }, "expected-id")).toThrow();
  });

  it("returns a single unknown write error on a server failure without retrying", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "session-token");
    const fetcher = vi.fn(async () => jsonResponse({ detail: "commit outcome unavailable" }, 503)) as typeof fetch;
    const port = createAssetMaintenanceHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(port.mergeAssets("series-a", {
      type: "prop",
      keep_id: "prop-a",
      merge_ids: ["prop-b"],
    }, new AbortController().signal)).rejects.toMatchObject({ status: 503, kind: "http" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("discards a late result after the bearer session changes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    const fetcher: typeof fetch = async () => {
      storage.setItem(API_TOKEN_KEY, "new-token");
      return jsonResponse({ orphans: [] });
    };
    const port = createAssetMaintenanceHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(port.checkOrphans("series-a", { items: [] }, new AbortController().signal))
      .rejects.toMatchObject({ kind: "aborted" });
  });
});
