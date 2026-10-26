import { afterEach, describe, expect, it, vi } from "vitest";
import { API_TOKEN_KEY } from "./storage";
import { createApiServices } from "./services";

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fusedAsset(name: string) {
  return {
    id: "fused-asset-1",
    series_id: "series-1",
    member_type: "prop",
    members: [{ kind: "asset", id: "prop-1", name: "旧车票" }],
    name,
    image_url: "/media/fused/asset-1.png",
    size: "1024x1024",
    status: "completed",
    error_message: null,
    created_at: "2026-10-26T00:00:00Z",
    updated_at: "2026-10-26T00:00:00Z",
  };
}

describe("fused asset HTTP services", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("lists, updates only the name, and accepts the DELETE 204 contract", async () => {
    window.localStorage.setItem(API_TOKEN_KEY, "fused-assets-session");
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      calls.push({ url, init });
      if (init.method === "GET") {
        return jsonResponse([fusedAsset("更新后的名称")]);
      }
      if (init.method === "PUT") {
        return jsonResponse(fusedAsset("更新后的名称"));
      }
      if (init.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      throw new Error("Unexpected method.");
    });
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher,
    });
    const signal = new AbortController().signal;

    const listed = await services.listFusedAssets!("series-1", signal);
    const updated = await services.updateFusedAsset!(
      "series-1",
      "fused-asset-1",
      { name: "更新后的名称" },
      signal,
    );
    await services.deleteFusedAsset!("fused-asset-1", signal);

    expect(listed[0]?.name).toBe("更新后的名称");
    expect(updated).toMatchObject({ id: "fused-asset-1", name: "更新后的名称" });
    expect(calls.map(({ url, init }) => [init.method, url.pathname])).toEqual([
      ["GET", "/api/series/series-1/fused-assets"],
      ["PUT", "/api/fused-assets/fused-asset-1"],
      ["DELETE", "/api/fused-assets/fused-asset-1"],
    ]);
    expect(JSON.parse(String(calls[1]?.init.body))).toEqual({ name: "更新后的名称" });
    expect(new Headers(calls[1]?.init.headers).get("Authorization"))
      .toBe("Bearer fused-assets-session");
    expect(calls[2]?.init.body).toBeUndefined();
  });

  it("does not replay a DELETE after a transport error", async () => {
    window.localStorage.setItem(API_TOKEN_KEY, "fused-assets-session");
    const fetcher = vi.fn(async () => {
      throw new TypeError("connection lost");
    });
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher,
    });

    await expect(services.deleteFusedAsset!("fused-asset-1", new AbortController().signal))
      .rejects.toMatchObject({ kind: "network" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
