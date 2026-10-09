// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import type { MyTeam } from "./teams";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));
const teamFieldOrder = ["id", "name", "owner_id", "created_at", "member_count", "my_role"];

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

interface FixtureProcess {
  baseUrl: string;
  output(): string;
  stop(): Promise<void>;
}

async function startFixture(overrides: Record<string, string> = {}): Promise<FixtureProcess> {
  const inheritedEnvironment = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("FIXTURE_")),
  );
  const child = spawn(process.execPath, [fixturePath], {
    env: { ...inheritedEnvironment, ...overrides, FIXTURE_PORT: "0" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let captured = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => { captured += chunk; });
  child.stderr.on("data", (chunk: string) => { captured += chunk; });

  let baseUrl: string;
  try {
    baseUrl = await new Promise<string>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`Fixture startup timed out: ${captured}`)), 5_000);
      const onData = (chunk: string) => {
        const match = chunk.match(/http:\/\/127\.0\.0\.1:(\d+)\/api/);
        if (match?.[1]) {
          clearTimeout(timer);
          child.stdout.off("data", onData);
          resolve(`http://127.0.0.1:${match[1]}/api`);
        }
      };
      child.stdout.on("data", onData);
      child.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once("close", (code) => {
        clearTimeout(timer);
        reject(new Error(`Fixture exited before startup (${code}): ${captured}`));
      });
    });
  } catch (error) {
    child.kill("SIGTERM");
    throw error;
  }

  return {
    baseUrl,
    output: () => captured,
    async stop() {
      if (child.exitCode !== null || child.signalCode !== null) {
        return;
      }
      child.kill("SIGTERM");
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 1_000);
        child.once("close", () => {
          clearTimeout(timer);
          resolve();
        });
      });
    },
  };
}

interface RequestRecord {
  method: string;
  target: string;
  authorization: string | null;
}

function recordedFetcher(records: RequestRecord[]): typeof fetch {
  return async (input, init) => {
    const url = input instanceof URL
      ? input
      : input instanceof Request
        ? new URL(input.url)
        : new URL(input);
    records.push({
      method: init?.method ?? (input instanceof Request ? input.method : "GET"),
      target: `${url.pathname}${url.search}`,
      authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
        .get("Authorization"),
    });
    return globalThis.fetch(input, init);
  };
}

function createServices(
  baseUrl: string,
  records: RequestRecord[] = [],
  options: { timeoutMs?: number; token?: string } = {},
) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, options.token ?? "fixture-demo-token");
  const services = createApiServices(baseUrl, {
    storage,
    fetcher: recordedFetcher(records),
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  });
  return { services, storage };
}

function signal(): AbortSignal {
  return new AbortController().signal;
}

function fixtureRequests(output: string): Array<{ method: string; pathname: string; status: number }> {
  return output.split("\n")
    .filter((line) => line.startsWith("{"))
    .map((line) => JSON.parse(line) as { method: string; pathname: string; status: number });
}

const statusCases = [401, 403, 404, 422, 500] as const;

describe("my teams API over real HTTP", () => {
  it("uses the eleventh allowed route with current Bearer auth and preserves six fields in source order", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);

    try {
      const signedIn = await services.login({ username: "demo", password: "demo123" }, signal());
      const restored = await services.restore(signal());
      const series = await services.listSeries(signal());
      const firstSeries = series[0];
      if (!firstSeries) {
        throw new Error("Fixture did not return its first series.");
      }
      const chapters = await services.listChapters(firstSeries.id, signal());
      const firstChapter = chapters[0];
      if (!firstChapter) {
        throw new Error("Fixture did not return its first chapter.");
      }
      await services.listStoryboardAssets(firstSeries.id, firstChapter.id, signal());
      await services.listCharacters(firstSeries.id, signal());
      await services.listScenes(firstSeries.id, signal());
      await services.listProps(firstSeries.id, signal());
      await services.getPersonalProductionNotes(firstChapter.id, signal());
      await services.listMyTasks(1, signal());
      const teams: MyTeam[] = await services.listMyTeams(signal());

      expect(signedIn.id).toBe("demo-user");
      expect(restored?.id).toBe("demo-user");
      expect(teams.map(({ id }) => id)).toEqual([
        "fixture-team",
        "fixture-empty-team",
        "all",
        "fixture-team-alt",
        "__proto__",
      ]);
      expect(teams.map(({ name }) => name)).toEqual([
        "本地协作组",
        "暂无剧集团队",
        "同名团队",
        "同名团队",
        "",
      ]);
      expect(teams.every((team) => Object.keys(team).join(",") === teamFieldOrder.join(","))).toBe(true);
      expect(teams[0]).toMatchObject({
        id: "fixture-team",
        name: "本地协作组",
        owner_id: "demo-user",
        created_at: "2026-09-30T08:00:00Z",
        member_count: 12,
        my_role: "owner",
      });
      expect(teams[1]).toMatchObject({ member_count: 0, my_role: "member" });
      expect(teams[2]).toMatchObject({ id: "all", my_role: "observer" });
      expect(teams[3]).toMatchObject({ my_role: "legacy_reviewer" });
      expect(teams[4]).toMatchObject({ id: "__proto__", name: "", my_role: "future-role" });

      const expectedTargets = [
        ["POST", "/api/auth/login"],
        ["GET", "/api/auth/me"],
        ["GET", "/api/series"],
        ["GET", "/api/series/fixture-series-01/chapters"],
        ["GET", "/api/series/fixture-series-01/storyboard-assets?chapter_id=fixture-series-01-chapter-02"],
        ["GET", "/api/series/fixture-series-01/characters"],
        ["GET", "/api/series/fixture-series-01/scenes"],
        ["GET", "/api/series/fixture-series-01/props"],
        ["GET", "/api/chapters/fixture-series-01-chapter-02/personal-production-notes"],
        ["GET", "/api/chat/tasks/list?page=1&page_size=10"],
        ["GET", "/api/teams/my"],
      ];
      expect(requests.map(({ method, target }) => [method, target])).toEqual(expectedTargets);
      expect(requests[0]?.authorization).toBeNull();
      expect(requests.slice(1).every(({ authorization }) => authorization === "Bearer fixture-demo-token"))
        .toBe(true);
      const logs = fixtureRequests(fixture.output());
      expect(logs.map(({ method, pathname }) => [method, pathname])).toEqual(expectedTargets);
      expect(logs.every((entry) => Object.keys(entry).sort().join(",") === "method,pathname,status")).toBe(true);
      for (const secret of ["demo123", "fixture-demo-token", "demo-two123", "fixture-demo-token-02"]) {
        expect(fixture.output()).not.toContain(secret);
      }
    } finally {
      await fixture.stop();
    }
  });

  it("returns a different directory for each authenticated account", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const first = createServices(fixture.baseUrl, requests);
    const second = createServices(fixture.baseUrl, requests);
    try {
      const firstUser = await first.services.login({ username: "demo", password: "demo123" }, signal());
      const firstTeams = await first.services.listMyTeams(signal());
      const secondUser = await second.services.login({ username: "demo-two", password: "demo-two123" }, signal());
      const secondTeams: MyTeam[] = await second.services.listMyTeams(signal());

      expect(firstUser.id).toBe("demo-user");
      expect(secondUser.id).toBe("demo-user-02");
      expect(firstTeams.map(({ id }) => id)).toEqual([
        "fixture-team", "fixture-empty-team", "all", "fixture-team-alt", "__proto__",
      ]);
      expect(secondTeams.map(({ id }) => id)).toEqual(["fixture-team-02", "fixture-team-02-empty"]);
      expect(secondTeams[0]).toMatchObject({
        name: "第二账号协作组",
        owner_id: "demo-user-02",
      });
      expect(requests.filter(({ target }) => target === "/api/teams/my")
        .map(({ authorization }) => authorization)).toEqual([
        "Bearer fixture-demo-token",
        "Bearer fixture-demo-token-02",
      ]);
      expect(requests.some(({ target }) => /(?:user_id|team_id|page|page_size)=/.test(target))).toBe(false);
      expect(fixture.output()).not.toContain("demo-two123");
      expect(fixture.output()).not.toContain("fixture-demo-token-02");
    } finally {
      await fixture.stop();
    }
  });

  it("returns a successful empty directory as an empty array", async () => {
    const fixture = await startFixture({ FIXTURE_TEAMS_MODE: "empty" });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.listMyTeams(signal())).resolves.toEqual([]);
      expect(fixtureRequests(fixture.output())).toEqual([
        { method: "GET", pathname: "/api/teams/my", status: 200 },
      ]);
    } finally {
      await fixture.stop();
    }
  });

  it("removes the selected default team by restarting with removed mode", async () => {
    const fixture = await startFixture({ FIXTURE_TEAMS_MODE: "removed" });
    const { services } = createServices(fixture.baseUrl);
    try {
      const teams: MyTeam[] = await services.listMyTeams(signal());
      expect(teams.map(({ id }) => id)).toEqual([
        "fixture-empty-team", "all", "fixture-team-alt", "__proto__",
      ]);
    } finally {
      await fixture.stop();
    }
  });

  it("requires Bearer auth and rejects user, team, and paging query parameters", async () => {
    const fixture = await startFixture();
    try {
      const unauthenticated = await globalThis.fetch(`${fixture.baseUrl}/teams/my`);
      expect(unauthenticated.status).toBe(401);
      const filtered = await globalThis.fetch(
        `${fixture.baseUrl}/teams/my?user_id=private-user&team_id=other-team&page=2&page_size=10`,
        { headers: { Authorization: "Bearer fixture-demo-token" } },
      );
      expect(filtered.status).toBe(422);
      const logs = fixtureRequests(fixture.output());
      expect(logs.map(({ pathname, status }) => [pathname, status])).toEqual([
        ["/api/teams/my", 401],
        ["/api/teams/my", 422],
      ]);
      for (const secret of ["private-user", "other-team", "fixture-demo-token", "user_id", "team_id", "page_size"]) {
        expect(fixture.output()).not.toContain(secret);
      }
    } finally {
      await fixture.stop();
    }
  });

  it.each(statusCases)("preserves real HTTP %s from the teams endpoint", async (status) => {
    const fixture = await startFixture({ FIXTURE_TEAMS_STATUS: String(status) });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.listMyTeams(signal())).rejects.toMatchObject({ kind: "http", status });
    } finally {
      await fixture.stop();
    }
  });

  it.each([
    { mode: "invalid-json", expected: "invalid-response" },
    { mode: "invalid-shape", expected: "invalid-response" },
  ] as const)("rejects the real teams $mode response without turning it into an empty directory", async ({ mode, expected }) => {
    const fixture = await startFixture({ FIXTURE_TEAMS_MODE: mode });
    const { services } = createServices(fixture.baseUrl);
    try {
      const result = services.listMyTeams(signal());
      if (mode === "invalid-shape") {
        await expect(result).rejects.toBeInstanceOf(InvalidResponseError);
      } else {
        await expect(result).rejects.toMatchObject({ kind: expected });
      }
    } finally {
      await fixture.stop();
    }
  });

  it("classifies a delayed teams response as a timeout", async () => {
    const fixture = await startFixture({ FIXTURE_TEAMS_DELAY_MS: "300" });
    const { services } = createServices(fixture.baseUrl, [], { timeoutMs: 60 });
    try {
      await expect(services.listMyTeams(signal())).rejects.toMatchObject({ kind: "timeout" });
    } finally {
      await fixture.stop();
    }
  });

  it("classifies cancellation of a delayed teams request as aborted", async () => {
    const fixture = await startFixture({ FIXTURE_TEAMS_DELAY_MS: "500" });
    const { services } = createServices(fixture.baseUrl);
    const controller = new AbortController();
    try {
      const pending = services.listMyTeams(controller.signal);
      await new Promise((resolve) => setTimeout(resolve, 30));
      controller.abort();
      await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    } finally {
      await fixture.stop();
    }
  });
});
