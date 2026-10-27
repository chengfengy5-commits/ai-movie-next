import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";
import type { FusedGenerationInput } from "./fusedGeneration";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const input: FusedGenerationInput = {
  series_id: "series-1",
  member_type: "character",
  members: [
    { kind: "asset", id: "character-1", name: "林原" },
    { kind: "image", url: "https://images.example.test/ref.png", name: "参考图" },
  ],
  model_id: "image-model-1",
  prompt_id: "fuse-prompt-1",
  prompt_system: "系统提示",
  prompt_user: "角色分支",
  size: "1024x1024",
  chapter_id: null,
  frame_index: null,
  name: null,
};

describe("fused generation HTTP adapter", () => {
  it("posts the standalone wire contract with the current bearer and accepts a null message id", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fused-session-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "actor-1" }));
    const fetcher = vi.fn<typeof fetch>(async (url) => {
      expect(String(url)).toBe("http://127.0.0.1:4175/api/fused-assets/generate");
      return jsonResponse({ task_id: "task-1", message_id: null, private_value: "ignored" });
    });
    const port = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher }).fusedGeneration;
    if (port === undefined) throw new Error("Expected a fused generation service.");

    const receipt = await port.submit(input, new AbortController().signal);

    expect(receipt).toEqual({ taskId: "task-1", messageId: null });
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("http://127.0.0.1:4175/api/fused-assets/generate");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer fused-session-token");
    expect(JSON.parse(String(init?.body))).toEqual(input);
    expect(JSON.stringify(receipt)).not.toContain("private_value");
  });

  it("does not retry a not-wired 503 and rejects an unverified response", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fused-session-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "actor-1" }));
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ detail: "融合生成服务尚未接线" }, 503));
    const port = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher }).fusedGeneration;
    if (port === undefined) throw new Error("Expected a fused generation service.");

    await expect(port.submit(input, new AbortController().signal)).rejects.toMatchObject({
      kind: "http",
      status: 503,
    });
    expect(fetcher).toHaveBeenCalledOnce();

    const malformed = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => jsonResponse({ task_id: "task-2" })),
    }).fusedGeneration;
    if (malformed === undefined) throw new Error("Expected a fused generation service.");
    await expect(malformed.submit(input, new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
  });
});
