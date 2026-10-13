import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import { createAdminApi, type AdminFetcher } from "./adminApi";
import type { AdminModelInput } from "./adminTypes";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const modelInput: AdminModelInput = {
  category: "text",
  name: "推理模型",
  provider: "openai",
  model: "gpt-4",
  api_key: "",
  base_url: "",
  cost_per_call: 0,
  description: "",
  is_active: true,
  sort_order: 0,
  params: null,
};

describe("admin API client", () => {
  it("sends admin identity, filters and pagination while preserving the server total", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ data: [], total: 27 }));
    const api = createAdminApi(fetcher, () => "admin-session");

    const page = await api.listModels({
      skip: 10,
      limit: 10,
      category: "image",
      provider: "museai",
      is_active: false,
    });

    expect(page).toEqual({ data: [], total: 27 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/admin/system-models?skip=10&limit=10&category=image&provider=museai&is_active=false");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer admin-session");
  });

  it("adds the requested identity to a message-only delete response", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ message: "删除成功" }));
    const api = createAdminApi(fetcher, () => "admin-session");

    await expect(api.deleteModel("model/7")).resolves.toEqual({
      id: "model/7",
      message: "删除成功",
    });
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/admin/system-models/model%2F7");
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe("DELETE");
  });

  it("keeps a failed create to one request so an uncertain write is never replayed", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => {
      throw new TypeError("connection closed after sending");
    });
    const api = createAdminApi(fetcher, () => "admin-session");

    await expect(api.createModel(modelInput)).rejects.toMatchObject({
      kind: "network",
      status: null,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("posts the legacy login payload without an admin bearer token and rejects a non-admin identity", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({
      access_token: "creative-account-token",
      user: { id: "member-1", username: "member", is_superuser: false },
    }));
    const api = createAdminApi(fetcher, () => "existing-admin-token");

    await expect(api.login("member", "password")).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0]!;
    expect(url).toBe("/api/auth/login");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ username: "member", password: "password" });
    expect(new Headers(init?.headers).get("Authorization")).toBeNull();
  });
});
