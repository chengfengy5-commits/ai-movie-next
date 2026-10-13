import { describe, expect, it, vi } from "vitest";
import type { AdminFetcher } from "./adminApi";
import { createSystemPromptsApi } from "./systemPromptsApi";
import type { SystemPrompt, SystemPromptInput } from "./systemPromptsTypes";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function prompt(overrides: Partial<SystemPrompt> = {}): SystemPrompt {
  return {
    id: "prompt-1",
    category: "storyboard",
    name: "分镜提示词",
    system_prompt: "system",
    user_prompt: "user",
    sort_order: 3,
    is_active: true,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
    ...overrides,
  };
}

function requestUrl(input: RequestInfo | URL | undefined): string {
  return input === undefined ? "" : String(input);
}

describe("system prompts API client", () => {
  it("sends the legacy list filters and authenticated CRUD requests through the injected client", async () => {
    const saved = prompt();
    const fetcher = vi.fn<AdminFetcher>()
      .mockResolvedValueOnce(jsonResponse({ data: [saved], total: 1 }))
      .mockResolvedValueOnce(jsonResponse(saved))
      .mockResolvedValueOnce(jsonResponse(saved))
      .mockResolvedValueOnce(jsonResponse(saved))
      .mockResolvedValueOnce(jsonResponse({ message: "删除成功" }));
    const api = createSystemPromptsApi(fetcher, () => "system-admin-token", "https://api.example.invalid/api");
    const input: SystemPromptInput = {
      category: "storyboard",
      name: "分镜提示词",
      system_prompt: "system",
      user_prompt: "user",
      sort_order: 3,
      is_active: true,
    };

    await expect(api.list({
      skip: 20,
      limit: 10,
      category: "storyboard",
      is_active: false,
    })).resolves.toEqual({ data: [saved], total: 1 });
    await expect(api.get("prompt/一")).resolves.toEqual(saved);
    await expect(api.create(input)).resolves.toEqual(saved);
    await expect(api.update("prompt-1", input)).resolves.toEqual(saved);
    await expect(api.delete("prompt-1")).resolves.toEqual({ message: "删除成功" });

    expect(fetcher.mock.calls.map(([request]) => requestUrl(request))).toEqual([
      "https://api.example.invalid/api/admin/system-prompts?skip=20&limit=10&category=storyboard&is_active=false",
      "https://api.example.invalid/api/admin/system-prompts/prompt%2F%E4%B8%80",
      "https://api.example.invalid/api/admin/system-prompts",
      "https://api.example.invalid/api/admin/system-prompts/prompt-1",
      "https://api.example.invalid/api/admin/system-prompts/prompt-1",
    ]);
    expect(fetcher.mock.calls.map(([, init]) => new Headers(init?.headers).get("Authorization"))).toEqual(
      Array(5).fill("Bearer system-admin-token"),
    );
    expect(fetcher.mock.calls[2]?.[1]?.method).toBe("POST");
    expect(fetcher.mock.calls[2]?.[1]?.body).toBe(JSON.stringify(input));
    expect(fetcher.mock.calls[3]?.[1]?.method).toBe("PUT");
    expect(fetcher.mock.calls[3]?.[1]?.body).toBe(JSON.stringify(input));
    expect(fetcher.mock.calls[4]?.[1]?.method).toBe("DELETE");
    expect(new Headers(fetcher.mock.calls[2]?.[1]?.headers).get("Content-Type")).toBe("application/json");
  });

  it("keeps missing-token and HTTP errors observable without issuing a session from window", async () => {
    const noTokenFetcher = vi.fn<AdminFetcher>();
    const noTokenApi = createSystemPromptsApi(noTokenFetcher, () => null);
    await expect(noTokenApi.list({ skip: 0, limit: 10 })).rejects.toMatchObject({
      kind: "http",
      status: 401,
      detail: "未登录",
    });
    expect(noTokenFetcher).not.toHaveBeenCalled();

    const forbiddenFetcher = vi.fn<AdminFetcher>().mockResolvedValue(
      jsonResponse({ detail: "权限不足，仅管理员可执行此操作" }, 403),
    );
    const forbiddenApi = createSystemPromptsApi(forbiddenFetcher, () => "expired-or-member-token");
    await expect(forbiddenApi.list({ skip: 0, limit: 10 })).rejects.toEqual(
      expect.objectContaining({
        name: "ApiError",
        kind: "http",
        status: 403,
        detail: "权限不足，仅管理员可执行此操作",
      }),
    );
    expect(forbiddenFetcher).toHaveBeenCalledTimes(1);
    expect(new Headers(forbiddenFetcher.mock.calls[0]?.[1]?.headers).get("Authorization"))
      .toBe("Bearer expired-or-member-token");
  });

  it("converts network failures and unparseable response bodies into shared ApiError values", async () => {
    const rejectedFetcher = vi.fn<AdminFetcher>().mockRejectedValue(new Error("offline"));
    const rejectedApi = createSystemPromptsApi(rejectedFetcher, () => "token");
    await expect(rejectedApi.list({ skip: 0, limit: 10 })).rejects.toEqual(
      expect.objectContaining({ name: "ApiError", kind: "network" }),
    );

    const invalidResponseFetcher = vi.fn<AdminFetcher>().mockResolvedValue(new Response("not-json"));
    const invalidResponseApi = createSystemPromptsApi(invalidResponseFetcher, () => "token");
    await expect(invalidResponseApi.list({ skip: 0, limit: 10 })).rejects.toMatchObject({
      name: "ApiError",
      kind: "invalid-response",
    });
  });
});
