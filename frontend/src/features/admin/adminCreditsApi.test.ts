import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "./adminApi";
import { createAdminCreditsApi } from "./adminCreditsApi";
import type { AdminCreditLog, AdminUserCredit } from "./adminCreditsTypes";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const credit: AdminUserCredit = {
  id: "credit/甲",
  user_id: "user-1",
  credits: 0,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-02T00:00:00Z",
};

const log: AdminCreditLog = {
  id: "log-1",
  user_id: "user-1",
  amount: -7,
  balance_after: 0,
  type: "manual_adjustment",
  description: "管理员调整",
  task_id: null,
  created_at: "2026-10-02T00:00:00Z",
};

describe("admin credits API client", () => {
  it("lists user credits with exact paging, sorting, bearer token and abort signal", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [credit], total: 23 }));
    const api = createAdminCreditsApi(fetcher, () => "admin-token", "https://admin.example/api");
    const controller = new AbortController();

    await expect(api.listUserCredits({
      skip: 10,
      limit: 10,
      sort_field: "credits",
      sort_order: "asc",
    }, controller.signal)).resolves.toEqual({ data: [credit], total: 23 });

    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("https://admin.example/api/admin/user-credits?skip=10&limit=10&sort_field=credits&sort_order=asc");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer admin-token");
    expect(init?.signal).toBe(controller.signal);
  });

  it("gets and updates by encoded credit row id with only an absolute integer balance", async () => {
    const updated = { ...credit, credits: -7 };
    const fetcher = vi.fn<AdminFetcher>()
      .mockResolvedValueOnce(jsonResponse(credit))
      .mockResolvedValueOnce(jsonResponse(updated));
    const api = createAdminCreditsApi(fetcher, () => "admin-token");
    const controller = new AbortController();

    await expect(api.getUserCredit("credit/甲", controller.signal)).resolves.toEqual(credit);
    await expect(api.updateUserCredit("credit/甲", -7, controller.signal)).resolves.toEqual(updated);

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"])).toEqual([
      ["/api/admin/user-credits/credit%2F%E7%94%B2", "GET"],
      ["/api/admin/user-credits/credit%2F%E7%94%B2", "PUT"],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({ credits: -7 });
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get("Content-Type")).toBe("application/json");
    expect(fetcher.mock.calls.map(([, init]) => init?.signal)).toEqual([controller.signal, controller.signal]);
  });

  it("lists logs with an optional user filter and keeps filtering out of user-credit lists", async () => {
    const fetcher = vi.fn<AdminFetcher>()
      .mockResolvedValueOnce(jsonResponse({ data: [log], total: 1 }))
      .mockResolvedValueOnce(jsonResponse({ data: [credit], total: 1 }));
    const api = createAdminCreditsApi(fetcher, () => "admin-token");
    const controller = new AbortController();

    await expect(api.listCreditLogs({
      skip: 0,
      limit: 10,
      sort_field: "created_at",
      sort_order: "desc",
      user_id: "user/甲",
    }, controller.signal)).resolves.toEqual({ data: [log], total: 1 });
    await api.listUserCredits({
      skip: 0,
      limit: 10,
      sort_field: "updated_at",
      sort_order: "desc",
    }, controller.signal);

    const logUrl = new URL(String(fetcher.mock.calls[0]?.[0]), "http://localhost");
    expect(logUrl.pathname).toBe("/api/admin/credit-logs");
    expect(logUrl.searchParams.get("user_id")).toBe("user/甲");
    expect(logUrl.searchParams.get("sort_field")).toBe("created_at");
    const creditUrl = new URL(String(fetcher.mock.calls[1]?.[0]), "http://localhost");
    expect(creditUrl.pathname).toBe("/api/admin/user-credits");
    expect(creditUrl.searchParams.has("user_id")).toBe(false);
    expect(fetcher.mock.calls.map(([, init]) => init?.signal)).toEqual([controller.signal, controller.signal]);

    const unfilteredFetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [], total: 0 }));
    await createAdminCreditsApi(unfilteredFetcher, () => "admin-token").listCreditLogs({
      skip: 0,
      limit: 10,
      sort_field: "created_at",
      sort_order: "desc",
      user_id: "   ",
    });
    expect(new URL(String(unfilteredFetcher.mock.calls[0]?.[0]), "http://localhost").searchParams.has("user_id")).toBe(false);
  });

  it.each([401, 403, 422])("preserves HTTP %i and the response detail", async (status) => {
    const api = createAdminCreditsApi(
      vi.fn<AdminFetcher>(async () => jsonResponse({ detail: "请求被拒绝" }, status)),
      () => "admin-token",
    );
    await expect(api.listCreditLogs({ skip: 0, limit: 10, sort_field: "created_at", sort_order: "desc" }))
      .rejects.toEqual(new ApiError("http", "请求被拒绝", status, "请求被拒绝"));
  });

  it("rejects missing tokens, malformed payloads, unreadable responses, network failures, and non-integer writes", async () => {
    const noTokenFetcher = vi.fn<AdminFetcher>();
    await expect(createAdminCreditsApi(noTokenFetcher, () => null).listUserCredits({
      skip: 0,
      limit: 10,
      sort_field: "updated_at",
      sort_order: "desc",
    })).rejects.toMatchObject({ kind: "http", status: 401 });
    expect(noTokenFetcher).not.toHaveBeenCalled();

    const malformedPage = createAdminCreditsApi(
      vi.fn<AdminFetcher>(async () => jsonResponse({ data: [{ ...credit, credits: "0" }], total: 1 })),
      () => "admin-token",
    );
    await expect(malformedPage.listUserCredits({
      skip: 0,
      limit: 10,
      sort_field: "updated_at",
      sort_order: "desc",
    })).rejects.toMatchObject({ kind: "invalid-response" });

    const malformedJson = createAdminCreditsApi(
      vi.fn<AdminFetcher>(async () => new Response("not-json")),
      () => "admin-token",
    );
    await expect(malformedJson.getUserCredit("credit-1")).rejects.toMatchObject({ kind: "invalid-response" });

    const network = createAdminCreditsApi(
      vi.fn<AdminFetcher>(async () => { throw new TypeError("offline"); }),
      () => "admin-token",
    );
    await expect(network.getUserCredit("credit-1")).rejects.toMatchObject({ kind: "network" });

    const invalidWriteFetcher = vi.fn<AdminFetcher>();
    await expect(createAdminCreditsApi(invalidWriteFetcher, () => "admin-token").updateUserCredit("credit-1", Number.NaN))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(invalidWriteFetcher).not.toHaveBeenCalled();
  });
});
