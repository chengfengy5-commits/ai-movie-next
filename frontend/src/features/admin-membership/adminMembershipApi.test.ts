import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminMembershipApi } from "./adminMembershipApi";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const code = {
  id: "code/one", code: "SYNTHETIC-CODE", type: "membership", value: 30, is_used: false,
  used_by: null, used_at: null, created_by: "admin-current", expires_at: null, created_at: "2026-10-01T00:00:00Z",
};
const packageRecord = {
  id: "package/one", category: "legacy", name: "Legacy package", price: -2,
  credits: 0, days: null, sort_order: 0, is_active: false, created_at: "created", updated_at: "updated",
};

describe("admin membership API", () => {
  it("calls all nine methods with exact routes, query fields, bodies, bearer token, and signals", async () => {
    const fetcher = vi.fn<AdminFetcher>()
      .mockResolvedValueOnce(response({ data: [code], total: 1 }))
      .mockResolvedValueOnce(response({ ...code, id: "code/甲" }))
      .mockResolvedValueOnce(response({ ...code, _generated_count: 2 }))
      .mockResolvedValueOnce(response({ message: "删除成功" }))
      .mockResolvedValueOnce(response({ data: [packageRecord], total: 1 }))
      .mockResolvedValueOnce(response({ ...packageRecord, id: "package/甲" }))
      .mockResolvedValueOnce(response({ ...packageRecord, category: "credits", name: "New", price: -1, credits: 0, days: null, sort_order: 0, is_active: false }))
      .mockResolvedValueOnce(response({ ...packageRecord, id: "package/one", price: 0, credits: null, is_active: false }))
      .mockResolvedValueOnce(response({ message: "删除成功" }));
    const api = createAdminMembershipApi(fetcher, () => "synthetic-admin-token", "http://localhost/api", "http://localhost:5173/");
    const controller = new AbortController();

    await api.listRedemptionCodes({
      skip: 25, limit: 25, sort_field: "created_at", sort_order: "desc",
      code: "", type: "membership", is_used: false, value: 0,
    }, controller.signal);
    await api.getRedemptionCode("code/甲", controller.signal);
    await api.generateRedemptionCodes({ type: "membership", value: 30, count: 2, expires_at: null }, "admin-current", controller.signal);
    await api.deleteRedemptionCode("code/one", controller.signal);
    await api.listPurchasePackages({ skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc" }, controller.signal);
    await api.getPurchasePackage("package/甲", controller.signal);
    await api.createPurchasePackage({
      category: "credits", name: "New", price: -1, credits: 0, days: null, sort_order: 0, is_active: false,
    }, controller.signal);
    await api.updatePurchasePackage("package/one", { price: 0, credits: null, is_active: false }, controller.signal);
    await api.deletePurchasePackage("package/one", controller.signal);

    const calls = fetcher.mock.calls;
    expect(calls).toHaveLength(9);
    const first = new URL(String(calls[0]?.[0]));
    expect(first.pathname).toBe("/api/admin/redemption-codes");
    expect(first.searchParams.get("skip")).toBe("25");
    expect(first.searchParams.get("limit")).toBe("25");
    expect(first.searchParams.get("is_used")).toBe("false");
    expect(first.searchParams.get("value")).toBe("0");
    expect(first.searchParams.has("code")).toBe(false);
    expect(String(calls[1]?.[0])).toBe("http://localhost/api/admin/redemption-codes/code%2F%E7%94%B2");
    expect(calls[2]?.[1]?.method).toBe("POST");
    expect(JSON.parse(String(calls[2]?.[1]?.body))).toEqual({
      type: "membership", value: 30, count: 2, expires_at: null,
    });
    expect(calls[3]?.[1]?.method).toBe("DELETE");
    const packageListUrl = new URL(String(calls[4]?.[0]));
    expect(packageListUrl.searchParams.get("sort_field")).toBe("sort_order");
    expect(packageListUrl.searchParams.has("category")).toBe(false);
    expect(String(calls[5]?.[0])).toBe("http://localhost/api/admin/purchase-packages/package%2F%E7%94%B2");
    expect(JSON.parse(String(calls[6]?.[1]?.body))).toEqual({
      category: "credits", name: "New", price: -1, credits: 0, days: null, sort_order: 0, is_active: false,
    });
    expect(JSON.parse(String(calls[7]?.[1]?.body))).toEqual({ price: 0, credits: null, is_active: false });
    expect(calls[8]?.[1]?.method).toBe("DELETE");
    for (const [, init] of calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer synthetic-admin-token");
      expect(init?.signal).toBe(controller.signal);
    }
  });

  it.each([201, 204, 500])("does not accept HTTP %i as a successful response", async (status) => {
    const fetcher = vi.fn<AdminFetcher>(async () => new Response(null, { status }));
    const api = createAdminMembershipApi(fetcher, () => "token", "http://localhost/api", "http://localhost/");
    await expect(api.listPurchasePackages({
      skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc",
    })).rejects.toMatchObject({ kind: status >= 200 && status < 300 ? "invalid-response" : "http", status });
  });

  it("rejects malformed JSON and malformed records at HTTP 200", async () => {
    const badJson = vi.fn<AdminFetcher>(async () => new Response("{", { status: 200 }));
    const api1 = createAdminMembershipApi(badJson, () => "token", "http://localhost/api", "http://localhost/");
    await expect(api1.listPurchasePackages({
      skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc",
    })).rejects.toMatchObject({ kind: "invalid-response" });

    const malformed = vi.fn<AdminFetcher>(async () => response({ data: [{ id: "missing" }], total: 1 }));
    const api2 = createAdminMembershipApi(malformed, () => "token", "http://localhost/api", "http://localhost/");
    await expect(api2.listPurchasePackages({
      skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc",
    })).rejects.toBeInstanceOf(ApiError);
  });

  it("does not fetch without a token and discards responses from an older token", async () => {
    const noTokenFetcher = vi.fn<AdminFetcher>(async () => response({ data: [], total: 0 }));
    const noToken = createAdminMembershipApi(noTokenFetcher, () => null, "http://localhost/api", "http://localhost/");
    await expect(noToken.listPurchasePackages({
      skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc",
    })).rejects.toMatchObject({ status: 401 });
    expect(noTokenFetcher).not.toHaveBeenCalled();

    let token = "first";
    const fetcher = vi.fn<AdminFetcher>(async () => {
      token = "second";
      return response({ data: [], total: 0 });
    });
    const api = createAdminMembershipApi(fetcher, () => token, "http://localhost/api", "http://localhost/");
    await expect(api.listPurchasePackages({
      skip: 0, limit: 10, sort_field: "sort_order", sort_order: "asc",
    })).rejects.toMatchObject({ kind: "aborted" });
  });
});



describe("admin membership write status classification", () => {
  const operations: Array<{
    name: string;
    call(api: import("./adminMembershipTypes").AdminMembershipApi): Promise<unknown>;
  }> = [
    { name: "redemption create", call: (api) => api.generateRedemptionCodes({ type: "credits", value: 1, count: 1 }, "admin-current") },
    { name: "redemption delete", call: (api) => api.deleteRedemptionCode("code/one") },
    { name: "package create", call: (api) => api.createPurchasePackage({
      category: "credits", name: "", price: 0, credits: null, days: null, sort_order: 0, is_active: true,
    }) },
    { name: "package update", call: (api) => api.updatePurchasePackage("package/one", { price: 0 }) },
    { name: "package delete", call: (api) => api.deletePurchasePackage("package/one") },
  ];

  it.each(operations.flatMap((operation) => [201, 202, 204].map((status) => ({ operation, status }))))(
    "$operation.name classifies HTTP $status as invalid-response and preserves status",
    async ({ operation, status }) => {
      const fetcher = vi.fn<AdminFetcher>(async () => status === 204
        ? new Response(null, { status })
        : response({}, status));
      const api = createAdminMembershipApi(fetcher, () => "synthetic-token", "http://localhost/api", "http://localhost/");
      await expect(operation.call(api)).rejects.toMatchObject({
        kind: "invalid-response", status,
      });
    },
  );
});
