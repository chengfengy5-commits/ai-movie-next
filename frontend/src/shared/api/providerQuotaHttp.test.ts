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
  it("uses authenticated quota routes and keeps account credentials out of read projections", async () => {
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
      const method = init?.method ?? "GET";
      if (path.endsWith("/provider-quota/accounts") && method === "GET") return jsonResponse(accountPayload);
      if (path.endsWith("/provider-quota/accounts") && method === "POST") {
        return jsonResponse({ ...accountPayload[0], id: "quota-created", name: "新增账号", is_active: true });
      }
      if (path.endsWith("/provider-quota/accounts/quota-a") && method === "PUT") {
        return jsonResponse({ ...accountPayload[1], name: "更新账号" });
      }
      if (path.endsWith("/provider-quota/accounts/quota-a") && method === "DELETE") return jsonResponse({ ok: true });
      if (path.endsWith("/provider-quota/probe")) {
        return jsonResponse({ id: "", ok: true, errors: [], balance: 0, unit: "$" });
      }
      return jsonResponse(balancePayload);
    });
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const service = services.providerQuota;
    if (service === undefined) throw new Error("Expected the provider quota service.");

    const controller = new AbortController();
    const directory = await service.listAccountDirectory(controller.signal);
    expect(directory.accounts.map(({ id }) => id)).toEqual(["quota-a", "quota-b"]);
    expect(directory.accounts[0]).toEqual({
      id: "quota-a",
      name: "新 API",
      providerType: "new_api",
      isActive: true,
      sortOrder: 10,
      consoleUrl: "https://console.example/account",
    });
    expect(directory.accounts[1]?.consoleUrl).toBeNull();
    expect(directory.editableAccounts[0]).toMatchObject({
      id: "quota-a",
      baseUrl: "https://gateway.example",
      hasUsername: true,
    });
    expect(directory.editableAccounts[0]).not.toHaveProperty("username");
    expect(directory.editableAccounts[0]).not.toHaveProperty("password");

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
    expect(JSON.stringify({ directory, balance })).not.toContain("do-not-render");
    expect(JSON.stringify({ directory, balance })).not.toContain("must-not-project");

    const input = {
      name: "新增账号",
      providerType: "deepseek" as const,
      baseUrl: "https://gateway.example/v1/",
      username: "fresh-user",
      password: "fresh-secret",
      isActive: true,
    };
    const created = await service.createAccount(input, controller.signal);
    expect(created).toMatchObject({
      id: "quota-created",
      name: "新增账号",
      baseUrl: "https://gateway.example",
      hasUsername: true,
    });
    expect(JSON.stringify(created)).not.toContain("fresh-secret");
    const updated = await service.updateAccount("quota-a", { ...input, name: "更新账号", username: "", password: "" }, controller.signal);
    expect(updated).toMatchObject({ id: "quota-a", name: "更新账号" });
    await service.deleteAccount("quota-a", controller.signal);
    await expect(service.probeConnection({
      providerType: input.providerType,
      baseUrl: input.baseUrl,
      username: input.username,
      password: input.password,
    }, controller.signal))
      .resolves.toMatchObject({ ok: true, balance: 0, unit: "$" });

    expect(fetcher).toHaveBeenCalledTimes(6);
    expect(fetcher.mock.calls.map(([requestInput, init]) => ({
      url: String(requestInput),
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
      {
        url: "http://127.0.0.1:4175/api/provider-quota/accounts",
        method: "POST",
        authorization: "Bearer admin-session-token",
        body: JSON.stringify({
          name: input.name,
          provider_type: input.providerType,
          base_url: input.baseUrl,
          username: input.username,
          password: input.password,
          is_active: input.isActive,
        }),
      },
      {
        url: "http://127.0.0.1:4175/api/provider-quota/accounts/quota-a",
        method: "PUT",
        authorization: "Bearer admin-session-token",
        body: JSON.stringify({
          name: "更新账号",
          provider_type: input.providerType,
          base_url: input.baseUrl,
          username: "",
          password: "",
          is_active: input.isActive,
        }),
      },
      {
        url: "http://127.0.0.1:4175/api/provider-quota/accounts/quota-a",
        method: "DELETE",
        authorization: "Bearer admin-session-token",
        body: undefined,
      },
      {
        url: "http://127.0.0.1:4175/api/provider-quota/probe",
        method: "POST",
        authorization: "Bearer admin-session-token",
        body: JSON.stringify({
          provider_type: "deepseek",
          base_url: "https://gateway.example/v1/",
          username: "fresh-user",
          password: "fresh-secret",
        }),
      },
    ]);

    vi.useFakeTimers();
    try {
      hangRequests = true;
      const listRequest = service.listAccountDirectory(new AbortController().signal);
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

      const probeRequest = service.probeConnection({
        providerType: "deepseek",
        baseUrl: "https://gateway.example/v1/",
        username: "fresh-user",
        password: "fresh-secret",
      }, new AbortController().signal);
      const probeTimeout = expect(probeRequest).rejects.toMatchObject({ kind: "timeout" });
      const probeSignal = fetcher.mock.calls.at(-1)?.[1]?.signal;
      await vi.advanceTimersByTimeAsync(59_999);
      expect(probeSignal?.aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);
      await probeTimeout;
    } finally {
      vi.useRealTimers();
    }
  });
});
