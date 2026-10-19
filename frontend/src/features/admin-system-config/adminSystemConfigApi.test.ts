import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../shared/api/errors";
import type { AdminFetcher } from "../admin/adminApi";
import { createAdminSystemConfigApi } from "./adminSystemConfigApi";

function config(overrides: Record<string, unknown> = {}) {
  return {
    id: "config/a b",
    muse_token: "synthetic-muse-value",
    site_title: "Synthetic title",
    site_tagline: "Synthetic tagline",
    storyboard_cost_multiplier: 2,
    membership_benefits: "Synthetic benefits",
    payments_enabled: false,
    tutorial_url: "",
    purchase_qr_code_url: "",
    purchase_contact_info: "",
    team_created_limit: 3,
    team_joined_limit: 3,
    team_member_limit: 20,
    invite_code_ttl_hours: 24,
    chapter_lock_idle_minutes: 15,
    aistarslab_credit_multiplier: 1,
    frontend_url: "",
    allowed_origins: "",
    upload_dir: "",
    worker_max_concurrency: "0",
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("admin system config API adapter", () => {
  it("uses the three exact routes, current Bearer token, encoded IDs, and sparse JSON updates", async () => {
    let token = "synthetic-admin-token-one";
    const calls: Array<{ url: string; method: string; authorization: string | null; body: string | undefined }> = [];
    const fetcher = vi.fn<AdminFetcher>(async (input, init) => {
      calls.push({
        url: String(input),
        method: init?.method ?? "GET",
        authorization: new Headers(init?.headers).get("Authorization"),
        body: typeof init?.body === "string" ? init.body : undefined,
      });
      const method = init?.method ?? "GET";
      if (method === "GET" && String(input).endsWith("/admin/system-config")) {
        return jsonResponse({ data: [config()], total: 1 });
      }
      if (method === "GET") return jsonResponse(config());
      const patch = JSON.parse(String(init?.body)) as Record<string, unknown>;
      const returned = config(patch);
      if (patch.team_created_limit === 0) returned.team_created_limit = 3;
      return jsonResponse(returned);
    });
    const api = createAdminSystemConfigApi(fetcher, () => token, "/api", "http://localhost/admin");

    await api.list();
    token = "synthetic-admin-token-two";
    await api.get("config/a b");
    await api.update("config/a b", { site_title: "", payments_enabled: false, team_created_limit: 0 });

    expect(calls.map(({ method, url }) => method + " " + new URL(url).pathname)).toEqual([
      "GET /api/admin/system-config",
      "GET /api/admin/system-config/config%2Fa%20b",
      "PUT /api/admin/system-config/config%2Fa%20b",
    ]);
    expect(calls.map(({ authorization }) => authorization)).toEqual([
      "Bearer synthetic-admin-token-one",
      "Bearer synthetic-admin-token-two",
      "Bearer synthetic-admin-token-two",
    ]);
    expect(calls[2]?.body).toBe(JSON.stringify({ site_title: "", payments_enabled: false, team_created_limit: 0 }));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("rejects an exact-200 record that does not reflect the submitted patch", async () => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse(config()));
    const api = createAdminSystemConfigApi(fetcher, () => "synthetic-admin-token", "/api", "http://localhost/");

    await expect(api.update("config/a b", {
      site_title: "Submitted title",
      payments_enabled: true,
    })).rejects.toMatchObject({ kind: "invalid-response", status: 200 });

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toEqual({
      site_title: "Submitted title",
      payments_enabled: true,
    });
  });

  it("confirms projected patch fields while allowing unrelated server values to change", async () => {
    const patch = {
      site_title: "Submitted title",
      payments_enabled: true,
      team_created_limit: 0,
      team_joined_limit: 0,
      team_member_limit: 0,
      invite_code_ttl_hours: 0,
      chapter_lock_idle_minutes: 0,
      aistarslab_credit_multiplier: 0,
      frontend_url: " https://frontend.test ",
      allowed_origins: " https://frontend.test ",
      upload_dir: " /private/uploads ",
      worker_max_concurrency: "",
    };
    const returned = config({
      id: "config-1",
      ...patch,
      site_title: "Submitted title",
      membership_benefits: "Concurrent unpatched update",
      team_created_limit: 3,
      team_joined_limit: 3,
      team_member_limit: 20,
      invite_code_ttl_hours: 24,
      chapter_lock_idle_minutes: 15,
      aistarslab_credit_multiplier: 1,
      frontend_url: "https://frontend.test",
      allowed_origins: "https://frontend.test",
      upload_dir: "/private/uploads",
      worker_max_concurrency: "0",
    });
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse(returned));
    const api = createAdminSystemConfigApi(fetcher, () => "synthetic-admin-token", "/api", "http://localhost/");

    await expect(api.update("config-1", patch)).resolves.toEqual(returned);
  });

  it.each([
    [" 0009 ", "9"],
    ["", "0"],
    ["not-decimal", ""],
    ["1e2", ""],
    ["0x10", ""],
    ["90071992547409931234567890", "90071992547409931234567890"],
  ])("checks worker concurrency's decimal projection for %j", async (submitted, projected) => {
    const fetcher = vi.fn<AdminFetcher>(async () => jsonResponse(config({
      id: "config-1",
      worker_max_concurrency: projected,
    })));
    const api = createAdminSystemConfigApi(fetcher, () => "synthetic-admin-token", "/api", "http://localhost/");

    await expect(api.update("config-1", { worker_max_concurrency: submitted })).resolves.toMatchObject({
      worker_max_concurrency: projected,
    });
  });

  it.each([201, 204, 503])("does not accept HTTP %s as a confirmed exact-200 response", async (status) => {
    const fetcher = vi.fn<AdminFetcher>(async () => new Response(null, { status }));
    const api = createAdminSystemConfigApi(fetcher, () => "synthetic-admin-token", "/api", "http://localhost/");
    let failure: unknown;
    try {
      await api.update("config-1", { site_title: "changed" });
    } catch (error) {
      failure = error;
    }
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status, kind: status < 400 ? "invalid-response" : "http" });
  });

  it("discards a malformed old-session response when the token changes during JSON parsing", async () => {
    let token = "synthetic-admin-token-one";
    const response = {
      status: 200,
      ok: true,
      json: async () => {
        token = "synthetic-admin-token-two";
        throw new SyntaxError("synthetic malformed response");
      },
    } as unknown as Response;
    const fetcher = vi.fn<AdminFetcher>(async () => response);
    const api = createAdminSystemConfigApi(fetcher, () => token, "/api", "http://localhost/");

    await expect(api.update("config-1", { site_title: "Changed" }))
      .rejects.toMatchObject({ kind: "aborted" });
  });

  it("rejects mismatched IDs and malformed exact-200 records", async () => {
    const fetcher = vi.fn<AdminFetcher>(async (input) => {
      if (String(input).includes("broken")) return new Response("not-json", { status: 200 });
      return jsonResponse(config({ id: "different-id" }));
    });
    const api = createAdminSystemConfigApi(fetcher, () => "synthetic-admin-token", "/api", "http://localhost/");
    await expect(api.get("config-1")).rejects.toMatchObject({ kind: "invalid-response" });
    await expect(api.get("broken")).rejects.toMatchObject({ kind: "invalid-response" });
  });
});
