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

describe("provider quota HTTP projection", () => {
  it("uses authenticated read routes, safe DTOs, and a narrow balance timeout", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "admin-session-token");
    const accountPayload = [
      {
        id: "quota-b",
        name: "停用账户",
        provider_type: "deepseek",
        base_url: "https://gateway.example",
        username: "must-not-project-user",
        password: "must-not-project-password",
        is_active: false,
        sort_order: 20,
        console_url: "https://operator:secret@console.example/usage",
      },
      {
        id: "quota-a",
        name: "新 API",
        provider_type: "new_api",
        base_url: "https://gateway.example",
        username: "must-not-project-user",
        password: "must-not-project-password",
        is_active: true,
        sort_order: 10,
        console_url: "https://console.example/account",
      },
    ];
    const balancePayload = {
      id: "quota-a",
      name: "新 API",
      provider_type: "new_api",
      base_url: "https://gateway.example",
      username: "must-not-project-user",
      password: "must-not-project-password",
      configured: true,
      disabled: false,
      is_active: true,
      unit: "$",
      unit_suffix: "",
      decimals: 2,
      balance_label: "剩余额度",
      warn_below: 10,
      balance: 0,
      used: null,
      total: 0,
      topped_up: null,
      granted: null,
      credits: null,
      balance_cny: null,
      available: true,
      extra_meta: ["现金 ¥0.00", "信控 $2.50", "unexpected error with password=do-not-render"],
      updated_at: "2026-10-27 10:30:00",
      session_reused: false,
      console_url: "javascript:alert(1)",
      errors: ["upstream echoed password=do-not-render"],
    };
    let hangRequests = false;
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      if (hangRequests) {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
        });
      }

      const path = new URL(String(input)).pathname;
      return jsonResponse(path.endsWith("/provider-quota/accounts") ? accountPayload : balancePayload);
    });
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const service = services.providerQuota;
    if (service === undefined) throw new Error("Expected the provider quota service.");

    const controller = new AbortController();
    const accounts = await service.listAccounts(controller.signal);
    expect(accounts.map(({ id }) => id)).toEqual(["quota-a", "quota-b"]);
    expect(accounts[0]).toEqual({
      id: "quota-a",
      name: "新 API",
      providerType: "new_api",
      isActive: true,
      sortOrder: 10,
      consoleUrl: "https://console.example/account",
    });
    expect(accounts[1]?.consoleUrl).toBeNull();

    const balance = await service.getBalance("quota-a", controller.signal);
    expect(balance).toMatchObject({
      id: "quota-a",
      configured: true,
      isActive: true,
      balance: 0,
      used: null,
      total: 0,
      hasErrors: true,
      consoleUrl: null,
      extraMeta: [
        { label: "现金", unit: "¥", amount: 0 },
        { label: "信控", unit: "$", amount: 2.5 },
      ],
    });
    expect("username" in balance).toBe(false);
    expect("password" in balance).toBe(false);
    expect("errors" in balance).toBe(false);
    expect(JSON.stringify({ accounts, balance })).not.toContain("do-not-render");
    expect(JSON.stringify({ accounts, balance })).not.toContain("must-not-project");
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls.map(([input, init]) => ({
      url: String(input),
      method: init?.method,
      authorization: new Headers(init?.headers).get("Authorization"),
      body: init?.body,
    }))).toEqual([
      {
        url: "http://127.0.0.1:4175/api/provider-quota/accounts",
        method: "GET",
        authorization: "Bearer admin-session-token",
        body: undefined,
      },
      {
        url: "http://127.0.0.1:4175/api/provider-quota/accounts/quota-a/balance",
        method: "GET",
        authorization: "Bearer admin-session-token",
        body: undefined,
      },
    ]);

    vi.useFakeTimers();
    try {
      hangRequests = true;
      const listRequest = service.listAccounts(new AbortController().signal);
      const listTimeout = expect(listRequest).rejects.toMatchObject({ kind: "timeout" });
      await vi.advanceTimersByTimeAsync(15_000);
      await listTimeout;

      const balanceRequest = service.getBalance("quota-a", new AbortController().signal);
      const balanceTimeout = expect(balanceRequest).rejects.toMatchObject({ kind: "timeout" });
      const balanceSignal = fetcher.mock.calls.at(-1)?.[1]?.signal;
      await vi.advanceTimersByTimeAsync(15_000);
      expect(balanceSignal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(44_999);
      expect(balanceSignal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await balanceTimeout;
    } finally {
      vi.useRealTimers();
    }
  });
});
