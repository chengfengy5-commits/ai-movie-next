import { describe, expect, it, vi } from "vitest";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminSeriesApi } from "./adminSeriesApi";

function record(overrides: Record<string, unknown> = {}) {
  return {
    id: "series/a b",
    user_id: "admin-1",
    name: "Original name",
    description: "Original description",
    image_url: "cover.png",
    created_at: "2026-10-16T08:00:00",
    updated_at: "2026-10-16T08:30:00",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("admin series API adapter", () => {
  it("uses all four existing methods, raw query parameters, encoded IDs, and the current admin token", async () => {
    let token = "synthetic-admin-token-one";
    const calls: Array<{ method: string; url: URL; authorization: string | null; body: string | undefined }> = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      calls.push({
        method,
        url,
        authorization: new Headers(init?.headers).get("Authorization"),
        body: typeof init?.body === "string" ? init.body : undefined,
      });
      if (url.pathname === "/api/admin/series" && method === "GET") {
        return jsonResponse({ data: [record()], total: 1 });
      }
      if (url.pathname.endsWith("series%2Fa%20b") || url.pathname.endsWith("series/a%20b")) {
        if (method === "GET") return jsonResponse(record());
        if (method === "PUT") return jsonResponse(record({ name: "" }));
      }
      if (url.pathname === "/api/admin/series" && method === "POST") {
        return jsonResponse(record({ user_id: "admin-1", name: "", description: null }));
      }
      throw new Error("Unexpected request: " + method + " " + url.pathname + url.search);
    });
    const api = createAdminSeriesApi(fetcher, () => token, () => "admin-1", "/api", "http://localhost/");

    await api.listSeries({
      skip: 20, limit: 10, sort_field: "name", sort_order: "desc", user_id: "writer /?", name: " %_ " ,
    });
    token = "synthetic-admin-token-two";
    await api.getSeries("series/a b");
    await api.createSeries({ name: "", description: null });
    await api.updateSeries("series/a b", { name: "" });

    expect(calls.map(({ method, url }) => method + " " + url.pathname)).toEqual([
      "GET /api/admin/series",
      "GET /api/admin/series/series%2Fa%20b",
      "POST /api/admin/series",
      "PUT /api/admin/series/series%2Fa%20b",
    ]);
    expect(calls[0]?.url.searchParams.get("name")).toBe(" %_ ");
    expect(calls[0]?.url.searchParams.get("user_id")).toBe("writer /?");
    expect(calls[0]?.url.searchParams.get("sort_field")).toBe("name");
    expect(calls[0]?.url.searchParams.get("sort_order")).toBe("desc");
    expect(calls[0]?.url.searchParams.get("skip")).toBe("20");
    expect(calls[0]?.url.searchParams.get("limit")).toBe("10");
    expect(calls.map(({ authorization }) => authorization)).toEqual([
      "Bearer synthetic-admin-token-one",
      "Bearer synthetic-admin-token-two",
      "Bearer synthetic-admin-token-two",
      "Bearer synthetic-admin-token-two",
    ]);
    expect(JSON.parse(String(calls[2]?.body))).toEqual({ name: "", description: null });
    expect(JSON.parse(String(calls[3]?.body))).toEqual({ name: "" });
    expect(calls).toHaveLength(4);
  });

  it.each([201, 204, 500])("preserves HTTP %s as an unconfirmed create result", async (status) => {
    const fetcher = vi.fn<AdminFetcher>(async () => new Response(null, { status }));
    const api = createAdminSeriesApi(fetcher, () => "synthetic-admin-token", () => "admin-1", "/api", "http://localhost/");
    await expect(api.createSeries({ name: "Synthetic", description: null })).rejects.toMatchObject({
      status,
      kind: status < 400 ? "invalid-response" : "http",
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed 200 records, mismatched IDs, owners, and submitted fields", async () => {
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input));
      if (init?.method === "POST") return jsonResponse(record({ user_id: "admin-2" }));
      if (init?.method === "PUT") return jsonResponse(record({ name: "Original name" }));
      if (url.pathname.endsWith("series%2Fa%20b")) return jsonResponse(record({ id: "other" }));
      return jsonResponse({ ...record(), hidden_relation: [] });
    });
    const api = createAdminSeriesApi(fetcher, () => "synthetic-admin-token", () => "admin-1", "/api", "http://localhost/");

    await expect(api.getSeries("series/a b")).rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    await expect(api.createSeries({ name: "New name", description: null }))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    await expect(api.updateSeries("series/a b", { name: "Submitted name" }))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    await expect(api.listSeries({ skip: 0, limit: 10, sort_field: "id", sort_order: "asc" }))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
  });

  it("sends only editable update fields even if a caller supplies expanded record data", async () => {
    let body = "";
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      body = String(init?.body);
      return jsonResponse(record({ name: "New name" }));
    });
    const api = createAdminSeriesApi(fetcher, () => "synthetic-admin-token", () => "admin-1", "/api", "http://localhost/");
    const patch = {
      name: "New name", user_id: "other-user", id: "other-id", created_at: "changed",
      team_id: "team-1", claimed_by: "member-1",
    } as unknown as { name: string };

    await api.updateSeries("series/a b", patch);

    expect(JSON.parse(body)).toEqual({ name: "New name" });
  });

  it("drops a response from a prior token instead of returning its data", async () => {
    let token = "synthetic-admin-token-one";
    let finishResponse!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { finishResponse = resolve; });
    const fetcher = vi.fn<AdminFetcher>(async () => response);
    const api = createAdminSeriesApi(fetcher, () => token, () => "admin-1", "/api", "http://localhost/");
    const read = api.listSeries({ skip: 0, limit: 10, sort_field: "id", sort_order: "asc" });
    token = "synthetic-admin-token-two";
    finishResponse(jsonResponse({ data: [record()], total: 1 }));
    await expect(read).rejects.toMatchObject({ kind: "aborted" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
