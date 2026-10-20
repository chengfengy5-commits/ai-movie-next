// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import {
  parseTeamMemberTasksPage,
  parseTeamUsageExport,
  type TeamReportingDateRange,
} from "./teamReporting";
import { createTeamReportingHttpPort } from "./teamReportingHttp";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function taskPage() {
  return {
    total: 1,
    page: 2,
    page_size: 15,
    credits: -3,
    tasks: [{
      id: "task-1",
      type: "future-report-task",
      message_id: null,
      status: "completed",
      result: null,
      request_data: "{\"truncated\":true}",
      credit_cost: 0,
      progress: 100,
      progress_message: null,
      created_at: "2026-10-01T00:00:00",
      updated_at: null,
      asset_type: null,
      asset_id: null,
      asset_name: null,
      chapter_title: null,
      chapter_id: null,
      frame_index: null,
      frame_count: null,
      frame_text: null,
    }],
  };
}

function usage() {
  return {
    items: [{
      series_id: "series-1",
      series_name: "北境来信",
      updated_at: "2026-10-02T12:00:00",
      total_calls: 2,
      total_credits: -3,
      failed_calls: 1,
      by_model: [{ model_name: "chat-model", calls: 2, credits: -3, failed_calls: 1 }],
      by_member: [{ user_id: "member-1", username: "小林", avatar_url: null, calls: 2, credits: -3, failed_calls: 1, chapter_count: 1 }],
    }],
    total_calls: 2,
    total_credits: -3,
    total_failed: 1,
  };
}

function seriesUsage() {
  return {
    series_id: "series-1",
    series_name: "北境来信",
    series_total: {
      calls: 2,
      credits: -3,
      failed_calls: 1,
      by_model: [{ model_name: "chat-model", calls: 2, credits: -3, failed_calls: 1 }],
      by_member: [{ user_id: "member-1", username: "小林", avatar_url: null, calls: 2, credits: -3, failed_calls: 1 }],
    },
    chapters: [],
  };
}

function modelUsage() {
  return {
    model_name: "模型 / ?",
    total_calls: 2,
    total_credits: -3,
    failed_calls: 1,
    by_series: [{ series_id: "series-1", series_name: "北境来信", calls: 2, credits: -3, failed_calls: 1 }],
    by_member: [{ user_id: "member-1", username: "小林", avatar_url: null, calls: 2, credits: -3, failed_calls: 1 }],
  };
}

function emptyExport() {
  return { rows: [], members: [], total_calls: 0, total_credits: 0, total_failed: 0 };
}

describe("team reporting HTTP adapter", () => {
  it("parses unknown task types, nullable task fields, negative credits and an export without model_order", () => {
    const page = parseTeamMemberTasksPage(taskPage());
    expect(page.tasks[0]).toMatchObject({
      type: "future-report-task",
      message_id: null,
      updated_at: null,
      frame_index: null,
    });
    expect(page.credits).toBe(-3);
    expect(parseTeamUsageExport(emptyExport()).model_order).toEqual([]);
    expect(() => parseTeamMemberTasksPage({ ...taskPage(), credits: Number.POSITIVE_INFINITY }))
      .toThrow(/积分余额/);
  });

  it("calls all five registered routes with bearer auth, encoded IDs and the applied ISO range", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "reporting-session");
    const requests: Array<{ path: string; params: URLSearchParams; authorization: string | null }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      requests.push({
        path: url.pathname,
        params: url.searchParams,
        authorization: new Headers(init?.headers).get("Authorization"),
      });
      if (url.pathname.endsWith("/tasks")) return response(200, taskPage());
      if (url.pathname.endsWith("/usage/export")) return response(200, emptyExport());
      if (url.pathname.endsWith("/usage/model")) return response(200, modelUsage());
      if (url.pathname.endsWith("/series/series%2F1/usage")) return response(200, seriesUsage());
      if (url.pathname.endsWith("/usage")) return response(200, usage());
      return response(404, { detail: "unexpected route" });
    }) as typeof fetch;
    const port = createTeamReportingHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const signal = new AbortController().signal;
    const range: TeamReportingDateRange = {
      startTime: "2026-10-01T00:00:00.000Z",
      endTime: "2026-10-31T23:59:59.999Z",
    };

    expect((await port.listMemberTasks("team/1", "member/1", 2, 15, signal)).credits).toBe(-3);
    expect((await port.getTeamUsage("team/1", range, signal)).items[0]?.total_credits).toBe(-3);
    expect((await port.getSeriesUsage("team/1", "series/1", range, signal)).series_total.credits).toBe(-3);
    expect((await port.getModelUsage("team/1", "模型 / ?", range, signal)).model_name).toBe("模型 / ?");
    expect((await port.exportTeamUsage("team/1", range, signal)).model_order).toEqual([]);

    expect(requests.map(({ path }) => path)).toEqual([
      "/api/teams/team%2F1/members/member%2F1/tasks",
      "/api/teams/team%2F1/usage",
      "/api/teams/team%2F1/series/series%2F1/usage",
      "/api/teams/team%2F1/usage/model",
      "/api/teams/team%2F1/usage/export",
    ]);
    expect(requests[0]?.params.toString()).toBe("page=2&page_size=15");
    for (const request of requests.slice(1)) {
      expect(request.params.get("start_time")).toBe(range.startTime);
      expect(request.params.get("end_time")).toBe(range.endTime);
      expect(request.authorization).toBe("Bearer reporting-session");
    }
    expect(requests[3]?.params.get("model_name")).toBe("模型 / ?");
    expect(requests[0]?.authorization).toBe("Bearer reporting-session");
  });

  it("preserves HTTP permission/status failures and rejects malformed successful bodies", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "reporting-session");
    for (const status of [401, 403, 404, 422, 503]) {
      const port = createTeamReportingHttpPort("http://127.0.0.1:4175/api", {
        storage,
        fetcher: vi.fn(async () => response(status, { detail: `status-${status}` })) as typeof fetch,
      });
      await expect(port.getTeamUsage("team-1", {}, new AbortController().signal)).rejects.toMatchObject({
        kind: "http",
        status,
        detail: `status-${status}`,
      });
    }

    const malformed = createTeamReportingHttpPort("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => response(200, { items: [], total_calls: 0 })) as typeof fetch,
    });
    await expect(malformed.getTeamUsage("team-1", {}, new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });

    const missingToken = new MemoryStorage();
    const unauthenticated = createTeamReportingHttpPort("http://127.0.0.1:4175/api", { storage: missingToken });
    await expect(unauthenticated.getTeamUsage("team-1", {}, new AbortController().signal))
      .rejects.toBeInstanceOf(ApiError);
  });

  it("treats a late old-session 401 as an abort after token rotation", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "reporting-session-before-switch");
    let resolveResponse!: (value: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveResponse = resolve; })) as typeof fetch;
    const port = createTeamReportingHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const request = port.getTeamUsage("team-1", {}, new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "reporting-session-after-switch");
    resolveResponse(response(401, { detail: "old session" }));
    await expect(request).rejects.toMatchObject({ kind: "aborted", status: null });
  });
});
