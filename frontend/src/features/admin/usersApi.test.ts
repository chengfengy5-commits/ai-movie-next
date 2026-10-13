import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "./adminApi";
import { createAdminUsersApi } from "./usersApi";
import type { AdminUserCreateInput, AdminUserUpdateInput } from "./usersTypes";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const user = {
  id: "user-1",
  username: "writer",
  email: "writer@example.test",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  credits: 8,
  created_at: "2026-10-01T00:00:00Z",
};

const createInput: AdminUserCreateInput = {
  username: "writer",
  email: "writer@example.test",
  password: "initial-secret",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  credits: 8,
};

const updateInput: AdminUserUpdateInput = {
  username: "writer-renamed",
  email: "writer@example.test",
  is_superuser: true,
  membership_type: "premium",
  membership_expires_at: "2027-03-04T05:06:07.000Z",
  credits: 18,
};

describe("admin users API client", () => {
  it("sends list filters, sorting, pagination, bearer token, and an explicit API base URL", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [user], total: 17 }));
    const api = createAdminUsersApi(fetcher, () => "admin-token", "http://localhost:8000/api");

    const result = await api.listUsers({
      skip: 10,
      limit: 10,
      sort_field: "created_at",
      sort_order: "desc",
      username: "陈 立",
      email: "writer+test@example.test",
      membership_type: "premium",
      is_superuser: false,
    });

    expect(result).toEqual({ data: [user], total: 17 });
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe(
      "http://localhost:8000/api/admin/users?skip=10&limit=10&sort_field=created_at&sort_order=desc&username=%E9%99%88+%E7%AB%8B&email=writer%2Btest%40example.test&membership_type=premium&is_superuser=false",
    );
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer admin-token");
    expect(init?.signal).toBeUndefined();
  });

  it("loads a user and uses the legacy create and update routes and payloads", async () => {
    const fetcher = vi.fn<AdminFetcher>()
      .mockResolvedValueOnce(jsonResponse(user))
      .mockResolvedValueOnce(jsonResponse(user, 201))
      .mockResolvedValueOnce(jsonResponse({ message: "用户更新成功" }));
    const api = createAdminUsersApi(fetcher, () => "admin-token", "/api");

    await expect(api.getUser("user/1")).resolves.toEqual(user);
    await expect(api.createUser(createInput)).resolves.toEqual(user);
    await expect(api.updateUser("user/1", updateInput)).resolves.toEqual({ message: "用户更新成功" });

    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"])).toEqual([
      ["/api/admin/users/user%2F1", "GET"],
      ["/api/admin/users", "POST"],
      ["/api/admin/users/user%2F1", "PUT"],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual(createInput);
    expect(JSON.parse(String(fetcher.mock.calls[2]?.[1]?.body))).toEqual(updateInput);
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get("Content-Type")).toBe("application/json");
  });

  it("preserves custom membership strings and a null credit patch in the update wire payload", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ message: "用户更新成功" }));
    const api = createAdminUsersApi(fetcher, () => "admin-token", "/api");
    const input: AdminUserUpdateInput = {
      ...updateInput,
      membership_type: "gold",
      membership_expires_at: "2027-04-05T06:07",
      credits: null,
    };

    await api.updateUser("user-1", input);

    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/admin/users/user-1");
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual(input);
  });

  it("omits empty filters but preserves a false administrator filter", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [], total: 0 }));
    const api = createAdminUsersApi(fetcher, () => "admin-token", "/api");

    await api.listUsers({
      skip: 0,
      limit: 10,
      sort_field: "created_at",
      sort_order: "desc",
      username: "",
      email: "",
      is_superuser: false,
    });

    expect(fetcher.mock.calls[0]?.[0]).toBe(
      "/api/admin/users?skip=0&limit=10&sort_field=created_at&sort_order=desc&is_superuser=false",
    );
  });

  it("rejects a missing token without making a request", async () => {
    const fetcher = vi.fn<AdminFetcher>();
    const api = createAdminUsersApi(fetcher, () => null, "/api");

    await expect(api.listUsers({
      skip: 0,
      limit: 10,
      sort_field: "created_at",
      sort_order: "desc",
    })).rejects.toMatchObject({ kind: "http", status: 401, detail: "未登录" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([401, 403, 422])("preserves HTTP %i and response detail", async (status) => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ detail: "拒绝访问" }, status));
    const api = createAdminUsersApi(fetcher, () => "admin-token", "/api");

    await expect(api.getUser("missing")).rejects.toEqual(
      new ApiError("http", "拒绝访问", status, "拒绝访问"),
    );
  });

  it("reports unreadable success responses and network failures as public API errors", async () => {
    const invalidResponseApi = createAdminUsersApi(
      vi.fn<AdminFetcher>(async () => new Response("not-json")),
      () => "admin-token",
      "/api",
    );
    const networkApi = createAdminUsersApi(
      vi.fn<AdminFetcher>(async () => { throw new TypeError("offline"); }),
      () => "admin-token",
      "/api",
    );

    await expect(invalidResponseApi.getUser("user-1")).rejects.toMatchObject({
      kind: "invalid-response",
      status: null,
    });
    await expect(networkApi.getUser("user-1")).rejects.toMatchObject({
      kind: "network",
      status: null,
    });
  });
});
