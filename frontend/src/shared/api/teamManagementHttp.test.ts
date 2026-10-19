// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./errors";
import { createApiServices, createDemoServices } from "./services";
import { createTeamManagementHttpPort } from "./teamManagementHttp";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

const stamp = "2026-10-17T10:00:00Z";
const team = { id: "t/1", name: "协作组", owner_id: "owner-1", created_at: stamp };
const member = {
  user_id: "member-1", username: "小林", avatar_url: null, role: "member",
  permissions: [], joined_at: stamp,
};
const detail = {
  ...team, members: [member], my_role: "owner", my_permissions: [],
};

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("team management HTTP adapter", () => {
  it("is exposed only by API services and reuses the current session token getter", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "api-session-current");
    const requests: Array<{ path: string; authorization: string | null }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      requests.push({ path: url.pathname, authorization: new Headers(init?.headers).get("Authorization") });
      return response(200, []);
    }) as typeof fetch;
    const apiServices = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const demoServices = createDemoServices(storage);
    expect(apiServices.teamManagement).toBeDefined();
    expect(demoServices.teamManagement).toBeUndefined();
    await apiServices.teamManagement!.listTeams(new AbortController().signal);
    expect(requests).toEqual([{ path: "/api/teams/my", authorization: "Bearer api-session-current" }]);
  });

  it("uses all thirteen routes, exact statuses, encoded targets, bodies, and current Bearer auth", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "business-session-1");
    const requests: Array<{ method: string; path: string; body: unknown; headers: HeadersInit; credentials: RequestCredentials | undefined; status: number | null }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      let body: unknown = null;
      if (typeof init?.body === "string") body = JSON.parse(init.body);
      requests.push({
        method: init?.method ?? "GET",
        path: url.pathname,
        body,
        headers: init?.headers ?? {},
        credentials: init?.credentials,
        status: null,
      });
      const reply = (status: number, payload: unknown) => {
        const result = response(status, payload);
        requests[requests.length - 1]!.status = result.status;
        return result;
      };
      const path = url.pathname;
      if (path.endsWith("/teams/my")) return reply(200, [{ ...team, member_count: 2, my_role: "owner" }]);
      if (path === "/api/teams") return reply(201, team);
      if (path === "/api/teams/t%2F1" && init?.method === "GET") return reply(200, detail);
      if (path === "/api/teams/t%2F1" && init?.method === "PUT") return reply(200, { id: "t/1", name: "新名字" });
      if (path === "/api/teams/t%2F1" && init?.method === "DELETE") return reply(200, { message: "团队已解散" });
      if (path.endsWith("/teams/t%2F1/leave")) return reply(200, { message: "已退出团队" });
      if (path.endsWith("/members/member%2F1") && init?.method === "DELETE") return reply(200, { message: "成员已移除" });
      if (path.endsWith("/members/member%2F1/role")) {
        return reply(200, { message: "角色已更新", role: "admin", permissions: ["view_tasks", "manage_invites"] });
      }
      if (path.endsWith("/members/member%2F1/permissions")) {
        return reply(200, { message: "权限已更新", permissions: ["view_tasks", "manage_invites"] });
      }
      if (path.endsWith("/teams/t%2F1/invites") && init?.method === "POST") {
        return reply(201, { invites: [1, 2].map((id) => ({ id: `i${id}`, code: `ABCD000${id}`, expires_at: stamp })), ttl_hours: 24 });
      }
      if (path.endsWith("/teams/t%2F1/invites") && init?.method === "GET") {
        return reply(200, [{ id: "i1", code: "ABCD0001", status: "active", created_at: stamp, expires_at: stamp, used_by: null, used_at: null }]);
      }
      if (path.endsWith("/invites/i%2F1")) return reply(200, { message: "邀请码已作废" });
      if (path === "/api/teams/join") return reply(200, { message: "已加入团队", team_id: "t/1", team_name: "协作组" });
      throw new Error(`Unexpected request ${init?.method} ${path}`);
    }) as typeof fetch;
    const port = createTeamManagementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const signal = new AbortController().signal;

    expect(await port.listTeams(signal)).toHaveLength(1);
    expect(await port.createTeam(" 协作组 ", "owner-1", signal)).toMatchObject({ owner_id: "owner-1" });
    expect(await port.getTeam("t/1", signal)).toMatchObject({ id: "t/1" });
    expect(await port.updateTeam("t/1", { name: "新名字" }, "新名字", signal)).toEqual({ id: "t/1", name: "新名字" });
    await port.deleteTeam("t/1", signal);
    await port.leaveTeam("t/1", signal);
    await port.removeMember("t/1", "member/1", signal);
    await port.setMemberRole("t/1", "member/1", "admin", "member", [], signal);
    await port.setMemberPermissions("t/1", "member/1", ["view_tasks", "manage_invites"], signal);
    expect((await port.createInvites("t/1", 2, signal)).invites).toHaveLength(2);
    expect(await port.listInvites("t/1", signal)).toHaveLength(1);
    await port.revokeInvite("t/1", "i/1", signal);
    await port.joinTeam("ABCDEF", signal);

    expect(requests.map(({ method, path }) => [method, path])).toEqual([
      ["GET", "/api/teams/my"], ["POST", "/api/teams"], ["GET", "/api/teams/t%2F1"],
      ["PUT", "/api/teams/t%2F1"], ["DELETE", "/api/teams/t%2F1"],
      ["POST", "/api/teams/t%2F1/leave"], ["DELETE", "/api/teams/t%2F1/members/member%2F1"],
      ["PUT", "/api/teams/t%2F1/members/member%2F1/role"],
      ["PUT", "/api/teams/t%2F1/members/member%2F1/permissions"],
      ["POST", "/api/teams/t%2F1/invites"], ["GET", "/api/teams/t%2F1/invites"],
      ["DELETE", "/api/teams/t%2F1/invites/i%2F1"], ["POST", "/api/teams/join"],
    ]);
    expect(requests.map(({ status }) => status)).toEqual(Array.from({ length: 13 }, (_, index) => index === 1 || index === 9 ? 201 : 200));
    expect(requests[1]?.body).toEqual({ name: " 协作组 " });
    expect(requests[3]?.body).toEqual({ name: "新名字" });
    expect(requests[7]?.body).toEqual({ role: "admin" });
    expect(requests[8]?.body).toEqual({ permissions: ["view_tasks", "manage_invites"] });
    expect(requests[9]?.body).toEqual({ count: 2 });
    expect(requests[12]?.body).toEqual({ code: "ABCDEF" });
    expect(requests.every(({ headers, credentials }) => new Headers(headers).get("Authorization") === "Bearer business-session-1" && credentials === "omit")).toBe(true);
  });

  it("keeps unexpected success status and malformed target acknowledgements unconfirmed", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "business-session-1");
    let next = response(201, []);
    const fetcher = vi.fn(async () => next) as typeof fetch;
    const port = createTeamManagementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const signal = new AbortController().signal;
    await expect(port.listTeams(signal)).rejects.toMatchObject({ kind: "invalid-response", status: 201 });

    next = response(200, { id: "someone-else", name: "同名" });
    await expect(port.updateTeam("target", { name: "同名" }, "同名", signal))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });

    next = response(201, { ...team, owner_id: "someone-else" });
    await expect(port.createTeam(" 协作组 ", "owner-1", signal))
      .rejects.toMatchObject({ kind: "invalid-response", status: 201 });

    next = response(403, { detail: "forbidden" });
    await expect(port.listTeams(signal)).rejects.toMatchObject({ kind: "http", status: 403 });
    await expect(port.listTeams(signal)).rejects.toBeInstanceOf(ApiError);
  });

  it("ignores a late old-session 401 and preserves the join quota fail-closed response", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "session-before-login-switch");
    let resolveFetch!: (value: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; })) as typeof fetch;
    const port = createTeamManagementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const request = port.listTeams(new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "session-after-login-switch");
    resolveFetch(response(401, { detail: "old session" }));
    await expect(request).rejects.toMatchObject({ kind: "aborted", status: null });

    const quotaPort = createTeamManagementHttpPort("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => response(503, { detail: "join quota is unavailable" })) as typeof fetch,
    });
    await expect(quotaPort.joinTeam("ABCDEF", new AbortController().signal)).rejects.toMatchObject({
      kind: "http",
      status: 503,
      detail: "join quota is unavailable",
    });
  });

  it("accepts invitation batch sizes one through five and rejects malformed counts or receipts", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "business-session-1");
    const requests: unknown[] = [];
    let duplicateIds = false;
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const body = typeof init?.body === "string" ? JSON.parse(init.body) as { count: number } : null;
      if (body === null) throw new Error("Expected a count body.");
      requests.push(body);
      return response(201, {
        invites: Array.from({ length: body.count }, (_, index) => ({
          id: duplicateIds ? "invite-duplicate" : `invite-${index + 1}`,
          code: `SYNTH${String(index + 1).padStart(3, "0")}`,
          expires_at: stamp,
        })),
        ttl_hours: 0,
      });
    }) as typeof fetch;
    const port = createTeamManagementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const signal = new AbortController().signal;

    for (const count of [1, 2, 3, 4, 5]) {
      expect((await port.createInvites("team", count, signal)).invites).toHaveLength(count);
    }
    expect(() => port.createInvites("team", 0, signal)).toThrow("邀请码数量必须为 1 到 5 个。");
    expect(() => port.createInvites("team", 6, signal)).toThrow("邀请码数量必须为 1 到 5 个。");
    expect(() => port.createInvites("team", 2.5, signal)).toThrow("邀请码数量必须为 1 到 5 个。");
    expect(requests).toEqual([1, 2, 3, 4, 5].map((count) => ({ count })));

    duplicateIds = true;
    await expect(port.createInvites("team", 2, signal)).rejects.toMatchObject({
      kind: "invalid-response",
      status: 201,
    });
  });

  it("preserves legal empty names, null no-op updates, same-role permissions, and past invite expiry", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "business-session-1");
    const requests: Array<{ method: string; body: unknown }> = [];
    const payloads = [
      response(201, { ...team, name: "", owner_id: "owner-1" }),
      response(200, { id: "team-opaque", name: "现有名称" }),
      response(200, { message: "角色未变化", role: "admin", permissions: ["view_tasks", "view_tasks"] }),
      response(201, { invites: [{ id: "invite-old", code: "OLD12345", expires_at: "2020-01-01T00:00:00" }], ttl_hours: -2 }),
    ];
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requests.push({ method: init?.method ?? "GET", body: typeof init?.body === "string" ? JSON.parse(init.body) : null });
      const next = payloads.shift();
      if (!next) throw new Error("Unexpected call.");
      return next;
    }) as typeof fetch;
    const port = createTeamManagementHttpPort("http://127.0.0.1:4175/api", { storage, fetcher });
    const signal = new AbortController().signal;

    expect((await port.createTeam("   ", "owner-1", signal)).name).toBe("");
    await port.updateTeam("team-opaque", { name: null }, "现有名称", signal);
    const role = await port.setMemberRole(
      "team-opaque", "member-1", "admin", " ADMIN ", ["view_tasks", "view_tasks"], signal,
    );
    expect(role.permissions).toEqual(["view_tasks", "view_tasks"]);
    const invite = await port.createInvites("team-opaque", 1, signal);
    expect(invite.ttl_hours).toBe(-2);
    expect(invite.invites[0]?.expires_at).toBe("2020-01-01T00:00:00");
    expect(requests).toEqual([
      { method: "POST", body: { name: "   " } },
      { method: "PUT", body: { name: null } },
      { method: "PUT", body: { role: "admin" } },
      { method: "POST", body: { count: 1 } },
    ]);
  });
});
