import { describe, expect, it, vi } from "vitest";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function jsonResponse(payload: unknown): Response {
  return { ok: true, status: 200, json: async () => payload } as Response;
}

describe("user preferences API port", () => {
  it("reads the account dictionary and updates only through the authenticated preferences route", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "account-token");
    const saved = {
      batch_gen_prefs: {
        character: { model: "character-model", prompt: "character-prompt", size: "1:1" },
        checks: { character: false, keepUnknown: true },
        extension: { retained: true },
      },
      another_preference: "untouched",
    };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => (
      init?.method === "PUT"
        ? jsonResponse({ ok: true, updated_keys: ["batch_gen_prefs"] })
        : jsonResponse(saved)
    ));
    const port = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher: fetcher as typeof fetch }).userPreferences;
    if (port === undefined) throw new Error("Expected the account preference port.");

    const controller = new AbortController();
    const preferences = await port.get(controller.signal);
    const receipt = await port.update({ batch_gen_prefs: saved.batch_gen_prefs }, controller.signal);

    expect(preferences).toEqual(saved);
    expect(receipt.updatedKeys).toEqual(["batch_gen_prefs"]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(String(fetcher.mock.calls[0]?.[0])).toBe("http://127.0.0.1:4175/api/user/preferences");
    expect(fetcher.mock.calls[0]?.[1]?.method).toBe("GET");
    expect(String(fetcher.mock.calls[1]?.[0])).toBe("http://127.0.0.1:4175/api/user/preferences");
    expect(fetcher.mock.calls[1]?.[1]?.method).toBe("PUT");
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization")).toBe("Bearer account-token");
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toEqual({
      preferences: { batch_gen_prefs: saved.batch_gen_prefs },
    });
  });
});
