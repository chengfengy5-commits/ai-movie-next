import { describe, expect, it, vi } from "vitest";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminTasksApi } from "./adminTasksApi";

function task() {
  return {
    id: "task-1", user_id: "writer-1", type: "future-type", status: "queued",
    credit_cost: 0, message_id: null, model_name: "", created_at: null, updated_at: null,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("admin tasks API adapter", () => {
  it("sends the raw four filters, paged query, sort whitelist, and current bearer token", async () => {
    let token = "synthetic-token-1";
    const calls: Array<{ url: URL; authorization: string | null; method: string }> = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      calls.push({
        url: new URL(String(input)),
        authorization: new Headers(init?.headers).get("Authorization"),
        method: init?.method ?? "GET",
      });
      return jsonResponse({ data: [task()], total: 1 });
    });
    const api = createAdminTasksApi(fetcher, () => token, "/api", "http://localhost/");
    await api.listTasks({
      skip: 25, limit: 25, sort_field: "created_at", sort_order: "desc",
      user_id: " writer /?", type: "chat", status: "failed", model_name: " %_ ",
    });
    token = "synthetic-token-2";
    await api.listTasks({ skip: 0, limit: 25, sort_field: "type", sort_order: "asc" });

    expect(calls[0]?.method).toBe("GET");
    expect(calls[0]?.url.pathname).toBe("/api/admin/tasks");
    expect(calls[0]?.url.searchParams.get("skip")).toBe("25");
    expect(calls[0]?.url.searchParams.get("limit")).toBe("25");
    expect(calls[0]?.url.searchParams.get("sort_field")).toBe("created_at");
    expect(calls[0]?.url.searchParams.get("sort_order")).toBe("desc");
    expect(calls[0]?.url.searchParams.get("user_id")).toBe(" writer /?");
    expect(calls[0]?.url.searchParams.get("type")).toBe("chat");
    expect(calls[0]?.url.searchParams.get("status")).toBe("failed");
    expect(calls[0]?.url.searchParams.get("model_name")).toBe(" %_ ");
    expect(calls[0]?.authorization).toBe("Bearer synthetic-token-1");
    expect(calls[1]?.authorization).toBe("Bearer synthetic-token-2");
    expect(calls).toHaveLength(2);
  });

  it.each([201, 204, 500])("does not accept HTTP %s as the list response", async (status) => {
    const fetcher = vi.fn<AdminFetcher>(async () => new Response(null, { status }));
    const api = createAdminTasksApi(fetcher, () => "synthetic-token", "/api", "http://localhost/");
    await expect(api.listTasks({ skip: 0, limit: 25, sort_field: "created_at", sort_order: "desc" }))
      .rejects.toMatchObject({ status, kind: status < 400 ? "invalid-response" : "http" });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("does not pass an unsupported sort field or offset to the backend", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [], total: 0 }));
    const api = createAdminTasksApi(fetcher, () => "synthetic-token", "/api", "http://localhost/");
    const baseQuery = { skip: 0, limit: 25, sort_field: "created_at" as const, sort_order: "desc" as const };

    await expect(api.listTasks({ ...baseQuery, sort_field: "password" as "created_at" }))
      .rejects.toMatchObject({ kind: "invalid-response" });
    await expect(api.listTasks({ ...baseQuery, skip: -25 }))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects an old token response and malformed JSON without returning rows", async () => {
    let token = "synthetic-token-1";
    let resolveResponse!: (response: Response) => void;
    const response = new Promise<Response>((resolve) => { resolveResponse = resolve; });
    const fetcher = vi.fn<AdminFetcher>(async () => response);
    const api = createAdminTasksApi(fetcher, () => token, "/api", "http://localhost/");
    const read = api.listTasks({ skip: 0, limit: 25, sort_field: "created_at", sort_order: "desc" });
    token = "synthetic-token-2";
    resolveResponse(jsonResponse({ data: [task()], total: 1 }));
    await expect(read).rejects.toMatchObject({ kind: "aborted" });

    const invalid = createAdminTasksApi(vi.fn<AdminFetcher>(async () => new Response("not-json", { status: 200 })),
      () => "synthetic-token", "/api", "http://localhost/");
    await expect(invalid.listTasks({ skip: 0, limit: 25, sort_field: "created_at", sort_order: "desc" }))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
  });
});
