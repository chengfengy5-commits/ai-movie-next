import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminSmtpAccountsApi } from "./adminSmtpAccountsApi";

function account(password = "synthetic-wire-secret-253") {
  return {
    id: "smtp/one", host: "smtp.example.test", port: 465, user: "sender@example.test", password,
    daily_sent: 0, is_active: true, created_at: null, updated_at: null,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("SMTP account API adapter", () => {
  it("uses exact paths, bearer auth, unpaginated list, allowlisted bodies, and method acknowledgements", async () => {
    const calls: Array<{ url: URL; method: string; body: unknown; authorization: string | null }> = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined,
        authorization: new Headers(init?.headers).get("Authorization") });
      if (method === "GET" && url.pathname === "/api/admin/smtp-accounts") return jsonResponse({ data: [account()], total: 1 });
      if (method === "GET") return jsonResponse(account());
      if (method === "POST") return jsonResponse({ id: "created-1", user: "new@example.test", message: "添加成功" });
      if (method === "PUT") return jsonResponse({ message: "更新成功" });
      return jsonResponse({ message: "删除成功" });
    });
    const api = createAdminSmtpAccountsApi(fetcher, () => "synthetic-admin-token", () => "admin-253", "/api", "http://localhost/");

    const list = await api.listAccounts();
    const detail = await api.getAccount("smtp/one");
    await api.createAccount({ user: "new@example.test", password: "synthetic-create-secret", host: "smtp.example.test", port: 465, is_active: false });
    await api.updateAccount("smtp/one", { host: "smtp-new.example.test", is_active: false, password: "" });
    await api.deleteAccount("smtp/one");

    expect(list.total).toBe(1);
    expect(JSON.stringify([list, detail])).not.toContain("synthetic-wire-secret-253");
    expect(calls.map(({ method, url }) => [method, url.pathname, url.search])).toEqual([
      ["GET", "/api/admin/smtp-accounts", ""],
      ["GET", "/api/admin/smtp-accounts/smtp%2Fone", ""],
      ["POST", "/api/admin/smtp-accounts", ""],
      ["PUT", "/api/admin/smtp-accounts/smtp%2Fone", ""],
      ["DELETE", "/api/admin/smtp-accounts/smtp%2Fone", ""],
    ]);
    expect(calls.every((call) => call.authorization === "Bearer synthetic-admin-token")).toBe(true);
    expect(calls[2]?.body).toEqual({ user: "new@example.test", password: "synthetic-create-secret", host: "smtp.example.test", port: 465, is_active: false });
    expect(calls[3]?.body).toEqual({ host: "smtp-new.example.test", is_active: false });
    expect(calls[4]?.body).toBeUndefined();
  });

  it("rejects non-200 success statuses, malformed JSON, unsafe patches, and four-star replacement before a request", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse({ message: "添加成功" }, 201));
    const api = createAdminSmtpAccountsApi(fetcher, () => "token", () => "admin-253", "/api", "http://localhost/");
    await expect(api.createAccount({ user: "a@example.test", password: "synthetic-secret", host: "smtp.example.test", port: 465, is_active: true }))
      .rejects.toMatchObject({ kind: "invalid-response", status: 201 });
    expect(fetcher).toHaveBeenCalledOnce();
    await expect(api.updateAccount("smtp-1", { password: "contains-****-mask" })).rejects.toBeInstanceOf(ApiError);
    await expect(api.updateAccount("smtp-1", {})).rejects.toBeInstanceOf(ApiError);
    await expect(api.updateAccount("smtp-1", { host: "smtp.example.test", password: null } as never)).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledOnce();

    const malformedFetcher = vi.fn<AdminFetcher>(async () => new Response("not-json", { status: 200 }));
    const malformedApi = createAdminSmtpAccountsApi(malformedFetcher, () => "token", () => "admin-253", "/api", "http://localhost/");
    await expect(malformedApi.listAccounts()).rejects.toMatchObject({ kind: "invalid-response", status: 200 });
  });

  it("rejects a detail record whose ID does not match the requested account", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => {
      const mismatched = account("synthetic-mismatch-secret");
      mismatched.id = "smtp/other";
      return jsonResponse(mismatched);
    });
    const api = createAdminSmtpAccountsApi(fetcher, () => "token", () => "admin-265", "/api", "http://localhost/");

    await expect(api.getAccount("smtp/one")).rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("preserves whitespace and four-star sequences in an explicitly submitted create secret", async () => {
    let submittedBody: unknown;
    const fetcher = vi.fn<AdminFetcher>(async (_input, init) => {
      submittedBody = JSON.parse(String(init?.body));
      return jsonResponse({ id: "created-raw", user: "sender@example.test", message: "添加成功" });
    });
    const api = createAdminSmtpAccountsApi(fetcher, () => "token", () => "admin-265", "/api", "http://localhost/");
    const password = "  synthetic****create-secret  ";

    await api.createAccount({ user: "sender@example.test", password, host: "smtp.example.test", port: 465, is_active: true });

    expect(submittedBody).toMatchObject({ password });
  });

  it("requires exact HTTP 200 for each implemented SMTP method", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => new Response(null, { status: 204 }));
    const api = createAdminSmtpAccountsApi(fetcher, () => "token", () => "admin-253", "/api", "http://localhost/");
    const calls: Array<Promise<unknown>> = [
      api.listAccounts(),
      api.getAccount("smtp-1"),
      api.createAccount({ user: "new@example.test", password: "synthetic-secret", host: "smtp.example.test", port: 465, is_active: true }),
      api.updateAccount("smtp-1", { host: "smtp-new.example.test" }),
      api.deleteAccount("smtp-1"),
    ];

    for (const call of calls) await expect(call).rejects.toMatchObject({ kind: "invalid-response", status: 204 });
    expect(fetcher).toHaveBeenCalledTimes(5);
  });

  it("ignores a response after the administrator token changes in flight", async () => {
    let token: string | null = "old-token";
    let resolveResponse!: (response: Response) => void;
    const pendingResponse = new Promise<Response>((resolve) => { resolveResponse = resolve; });
    const fetcher = vi.fn<AdminFetcher>(async () => pendingResponse);
    const api = createAdminSmtpAccountsApi(fetcher, () => token, () => "admin-253", "/api", "http://localhost/");
    const pending = api.listAccounts();
    token = "new-token";
    resolveResponse(jsonResponse({ data: [account()], total: 1 }));
    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
  });
});
