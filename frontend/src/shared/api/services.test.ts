// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getDemoChapterData } from "../../features/chapters/demoChapters";
import { getDemoPersonalProductionNotes } from "../../features/chapters/demoPersonalProductionNotes";
import { getDemoPersonalRoughCut } from "../../features/chapters/demoPersonalRoughCut";
import {
  capturePersonalProductionMedia,
  projectPersonalProductionSnapshot,
} from "../../features/chapters/personal-production/projection";
import { demoSeries } from "../../features/series/demoSeries";
import { demoTeams } from "../../features/series/demoTeams";
import { InvalidResponseError } from "./contracts";
import { ApiError } from "./errors";
import type { PersonalRoughCutSnapshot } from "./personalRoughCut";
import {
  createApiServices,
  createDemoServices,
  normalizeApiError,
  type ServiceOptions,
} from "./services";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
  type StorageLike,
} from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));

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

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

function requireServiceMethod<T>(method: T | undefined, name: string): NonNullable<T> {
  if (method === undefined) throw new Error(`Expected ${name} to be available.`);
  return method as NonNullable<T>;
}

function responseForUser() {
  return {
    id: "fixture-user",
    username: "演示创作者",
    email: "fixture@example.invalid",
    created_at: "2026-10-01T00:00:00",
  };
}

interface FixtureProcess {
  baseUrl: string;
  output(): string;
  stop(): Promise<void>;
}

async function startFixture(overrides: Record<string, string> = {}): Promise<FixtureProcess> {
  const child = spawn(process.execPath, [fixturePath], {
    env: { FIXTURE_PORT: "0", ...overrides },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let captured = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk: string) => { captured += chunk; });
  child.stderr.on("data", (chunk: string) => { captured += chunk; });

  const baseUrl = await new Promise<string>((resolve, reject) => {
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

  return {
    baseUrl,
    output: () => captured,
    async stop() {
      if (child.exitCode !== null || child.signalCode !== null) {
        return;
      }
      child.kill("SIGTERM");
      await new Promise<void>((resolve) => child.once("close", () => resolve()));
    },
  };
}

describe("API services", () => {
  afterEach(() => vi.restoreAllMocks());

  it("wires read-only team reporting only in API mode", () => {
    const storage = new MemoryStorage();
    const fetcher = vi.fn(async () => jsonResponse({})) as typeof fetch;
    const apiServices = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const demoServices = createDemoServices(storage);

    expect(apiServices.teamReporting).toBeDefined();
    expect(apiServices.assetMaintenance).toBeDefined();
    expect(apiServices.assetReplacement).toBeDefined();
    expect(apiServices.assetImageUpload).toBeDefined();
    expect(demoServices.teamReporting).toBeUndefined();
    expect(demoServices.assetMaintenance).toBeUndefined();
    expect(demoServices.assetReplacement).toBeUndefined();
    expect(demoServices.assetImageUpload).toBeUndefined();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not claim a releasable lease when POST may have renewed another window's lock", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
    const requests: string[] = [];
    let lockExists = false;
    const currentLock = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2098-12-31T23:00:00Z",
      last_active_at: "2098-12-31T23:00:00Z",
      expires_at: "2099-01-01T00:15:00Z",
    };
    const fetcher: typeof fetch = async (input, init) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      const method = init?.method ?? (input instanceof Request ? input.method : "GET");
      requests.push(method + " " + url.pathname);
      if (method === "POST") {
        lockExists = true;
        // Legacy renewal response has no owner or acquired_at creation proof.
        return jsonResponse({ locked: true, username: "演示创作者", expires_at: currentLock.expires_at, idle_minutes: 15 });
      }
      if (method === "GET") return jsonResponse(lockExists ? currentLock : { locked: false });
      return jsonResponse({ message: "锁已释放" });
    };
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const acquireLock = requireServiceMethod(services.acquireChapterEditLock, "acquireChapterEditLock");
    const getLock = requireServiceMethod(services.getChapterEditLock, "getChapterEditLock");
    const signal = new AbortController().signal;
    expect((await getLock("chapter-lock-race", signal)).locked).toBe(false);
    const acquisition = await acquireLock("chapter-lock-race", signal);
    expect(acquisition.lease).toBeNull();
    const verified = await getLock("chapter-lock-race", signal);
    expect(verified).toMatchObject({ locked: true, user_id: "demo-user", acquired_at: currentLock.acquired_at });
    expect(requests).toEqual([
      "GET /api/chapters/chapter-lock-race/lock",
      "POST /api/chapters/chapter-lock-race/lock",
      "GET /api/chapters/chapter-lock-race/lock",
    ]);
  });

  it("uses only the compatible login, session and series contracts over real HTTP", async () => {
    const fixture = await startFixture();
    const storage = new MemoryStorage();
    storage.setItem(MOCK_SESSION_KEY, "untouched-demo-session");
    storage.setItem(MOCK_USER_KEY, "untouched-demo-user");
    const services = createApiServices(fixture.baseUrl, { storage, fetcher: globalThis.fetch });

    try {
      const loggedIn = await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
      expect(loggedIn.id).toBe("demo-user");
      expect(storage.getItem(API_TOKEN_KEY)).toBe("fixture-demo-token");

      const restored = await services.restore(new AbortController().signal);
      expect(restored?.id).toBe("demo-user");
      const rows = await services.listSeries(new AbortController().signal);

      expect(rows).toHaveLength(24);
      expect(rows.every((row) => row.image_url === null)).toBe(true);
      expect(storage.getItem(MOCK_SESSION_KEY)).toBe("untouched-demo-session");
      expect(storage.getItem(MOCK_USER_KEY)).toBe("untouched-demo-user");
      const requests = fixture.output().split("\n").filter((line) => line.startsWith("{"))
        .map((line) => JSON.parse(line) as { method: string; pathname: string; status: number });
      expect(requests).toEqual([
        { method: "POST", pathname: "/api/auth/login", status: 200 },
        { method: "GET", pathname: "/api/auth/me", status: 200 },
        { method: "GET", pathname: "/api/series", status: 200 },
      ]);
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
    } finally {
      await fixture.stop();
    }
  });

  it("uses the backend account-entry contracts through the shared request client", async () => {
    const storage = new MemoryStorage();
    const requests: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      requests.push({ url, init: init ?? {} });
      if (url.pathname === "/api/auth/register") return jsonResponse(responseForUser(), 201);
      if (url.pathname === "/api/auth/send-code") return jsonResponse({ message: "验证码已发送", expire_in: 300 });
      if (url.pathname === "/api/auth/verify-code") return jsonResponse({ message: "验证成功" });
      if (url.pathname === "/api/auth/forgot-password") return jsonResponse({ message: "如果信息正确，重置链接已发送" });
      if (url.pathname === "/api/auth/reset-password") return jsonResponse({ message: "密码重置成功" });
      return jsonResponse({ detail: "Unexpected account request" }, 404);
    };
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const accountAccess = services.accountAccess;
    if (accountAccess === undefined) throw new Error("API account access is unavailable.");
    const signal = new AbortController().signal;

    const user = await accountAccess.register({
      username: "new-user",
      email: "new@example.invalid",
      password: "secret123",
      email_code: "123456",
    }, signal);
    const sent = await accountAccess.sendEmailCode({ email: "new@example.invalid", username: "new-user" }, signal);
    const verified = await accountAccess.verifyEmailCode({ email: "new@example.invalid", code: "123456" }, signal);
    const resetRequest = await accountAccess.requestPasswordReset({
      username: "new-user",
      email: "new@example.invalid",
    }, signal);
    const reset = await accountAccess.resetPassword({ token: "one-time-token", new_password: "newSecret123" }, signal);

    expect(user.username).toBe("演示创作者");
    expect(sent.expireInSeconds).toBe(300);
    expect(verified.message).toBe("验证成功");
    expect(resetRequest.message).toBe("如果信息正确，重置链接已发送");
    expect(reset.message).toBe("密码重置成功");
    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(API_USER_KEY)).toBeNull();
    expect(requests.map(({ url }) => `${url.pathname} ${url.search}`)).toEqual([
      "/api/auth/register ",
      "/api/auth/send-code ",
      "/api/auth/verify-code ",
      "/api/auth/forgot-password ",
      "/api/auth/reset-password ",
    ]);
    expect(requests.map(({ init }) => init.method)).toEqual(["POST", "POST", "POST", "POST", "POST"]);
    for (const { init } of requests) {
      expect(init.credentials).toBe("omit");
      expect(init.redirect).toBe("error");
      expect(init.referrerPolicy).toBe("no-referrer");
      expect(new Headers(init.headers).has("Authorization")).toBe(false);
    }
    expect(JSON.parse(String(requests[0]?.init.body))).toEqual({
      username: "new-user",
      email: "new@example.invalid",
      password: "secret123",
      email_code: "123456",
    });
    expect(JSON.parse(String(requests[4]?.init.body))).toEqual({
      token: "one-time-token",
      new_password: "newSecret123",
    });
    expect(createDemoServices(new MemoryStorage()).accountAccess).toBeUndefined();
  });

  it("preserves the backend rate-limit message and never retries account creation", async () => {
    const storage = new MemoryStorage();
    let calls = 0;
    const fetcher: typeof fetch = async () => {
      calls += 1;
      return jsonResponse({ error: "发送太频繁，请 42 秒后再试" }, 429);
    };
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const accountAccess = services.accountAccess;
    if (accountAccess === undefined) throw new Error("API account access is unavailable.");

    await expect(accountAccess.sendEmailCode({ email: "new@example.invalid" }, new AbortController().signal))
      .rejects.toMatchObject({ status: 429, detail: "发送太频繁，请 42 秒后再试" });
    expect(calls).toBe(1);

    let registrationCalls = 0;
    const registrationServices = createApiServices("http://127.0.0.1:4175/api", {
      storage: new MemoryStorage(),
      fetcher: async () => {
        registrationCalls += 1;
        return jsonResponse({ detail: "temporary failure" }, 503);
      },
    });
    const registration = registrationServices.accountAccess;
    if (registration === undefined) throw new Error("API account access is unavailable.");
    await expect(registration.register({
      username: "new-user",
      email: "new@example.invalid",
      password: "secret123",
      email_code: "123456",
    }, new AbortController().signal)).rejects.toMatchObject({ status: 503 });
    expect(registrationCalls).toBe(1);
  });

  it("classifies an actual HTTP response timeout as a timeout", async () => {
    const fixture = await startFixture({ FIXTURE_SERIES_STATUS: "timeout" });
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
    const services = createApiServices(fixture.baseUrl, {
      storage,
      fetcher: globalThis.fetch,
      timeoutMs: 40,
    });

    try {
      await expect(services.listSeries(new AbortController().signal))
        .rejects.toMatchObject({ kind: "timeout" });
    } finally {
      await fixture.stop();
    }
  });

  it("classifies a timeout while reading the actual HTTP response body", async () => {
    const fixture = await startFixture({ FIXTURE_SERIES_STATUS: "body-timeout" });
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
    const services = createApiServices(fixture.baseUrl, {
      storage,
      fetcher: globalThis.fetch,
      timeoutMs: 40,
    });

    try {
      await expect(services.listSeries(new AbortController().signal))
        .rejects.toMatchObject({ kind: "timeout" });
    } finally {
      await fixture.stop();
    }
  });

  it("rejects a real HTTP response that contains invalid JSON", async () => {
    const fixture = await startFixture({ FIXTURE_SERIES_MODE: "invalid-json" });
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
    const services = createApiServices(fixture.baseUrl, { storage, fetcher: globalThis.fetch });

    try {
      await expect(services.listSeries(new AbortController().signal))
        .rejects.toMatchObject({ kind: "invalid-response" });
      expect(storage.getItem(API_TOKEN_KEY)).toBe("fixture-demo-token");
    } finally {
      await fixture.stop();
    }
  });

  it("rejects a late login response after cancellation without saving credentials", async () => {
    const storage = new MemoryStorage();
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const options: ServiceOptions = { storage, fetcher };
    const services = createApiServices("http://127.0.0.1:4175/api", options);
    const controller = new AbortController();
    const pending = services.login({ username: "demo", password: "demo123" }, controller.signal);

    controller.abort();
    resolveFetch(jsonResponse({
      access_token: "late-token",
      token_type: "bearer",
      user: responseForUser(),
    }));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(API_USER_KEY)).toBeNull();
  });

  it("does not let a stale restore 401 clear a newer session", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    storage.setItem(API_USER_KEY, "old-user");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const pending = services.restore(new AbortController().signal);

    storage.setItem(API_TOKEN_KEY, "new-token");
    storage.setItem(API_USER_KEY, "new-user");
    resolveFetch(jsonResponse({ detail: "expired" }, 401));

    await expect(pending).resolves.toBeNull();
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
    expect(storage.getItem(API_USER_KEY)).toBe("new-user");
  });

  it("preserves API and mock storage separation after logout", async () => {
    const storage = new MemoryStorage();
    storage.setItem(MOCK_SESSION_KEY, "demo-session");
    storage.setItem(MOCK_USER_KEY, "demo-user");
    storage.setItem(API_TOKEN_KEY, "api-token");
    storage.setItem(API_USER_KEY, "api-user");
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: globalThis.fetch,
    });

    services.logout();

    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(API_USER_KEY)).toBeNull();
    expect(storage.getItem(MOCK_SESSION_KEY)).toBe("demo-session");
    expect(storage.getItem(MOCK_USER_KEY)).toBe("demo-user");
  });

  it("surfaces 403 and invalid response failures without discarding the session", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "preserved-token");
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const pathname = new URL(String(input)).pathname;
      if (pathname.endsWith("/auth/me")) {
        return jsonResponse(responseForUser());
      }
      return jsonResponse({ detail: "membership unavailable" }, 403);
    });
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(services.listSeries(new AbortController().signal))
      .rejects.toMatchObject({ kind: "http", status: 403 });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("preserved-token");

    const malformed = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => jsonResponse([{ ...demoSeries[0], id: 3 }])),
    });
    await expect(malformed.listSeries(new AbortController().signal))
      .rejects.toBeInstanceOf(InvalidResponseError);
  });


  it("serves private demo notes from the selected chapter without network or media requests", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const fetcher = vi.spyOn(globalThis, "fetch");
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const data = getDemoChapterData(demoSeries[0]!.id);
    if (data === null) {
      throw new Error("Expected demo chapter data.");
    }
    const opening = data.chapters[0]!;
    const notes = await services.getPersonalProductionNotes(opening.id, new AbortController().signal);
    const captured = await capturePersonalProductionMedia(opening, data.assetsByChapter[opening.id] ?? []);
    const readout = projectPersonalProductionSnapshot(opening, captured, notes);

    expect(notes.chapter_id).toBe(opening.id);
    expect(readout.frames.map((frame) => frame.status)).toEqual(["needs_reconfirmation", "needs_revision"]);
    expect(readout.resumePosition).toBe(1);
    expect(readout.orphanNotes).toHaveLength(1);
    expect(notes.frame_notes.has("constructor")).toBe(false);

    const noNotesChapter = data.chapters[1]!;
    const noNotes = await services.getPersonalProductionNotes(noNotesChapter.id, new AbortController().signal);
    expect(noNotes.revision).toBe(0);
    expect(noNotes.frame_notes.size).toBe(0);
    expect(fetcher).not.toHaveBeenCalled();

    services.logout();
    await expect(services.getPersonalProductionNotes(opening.id, new AbortController().signal))
      .rejects.toMatchObject({ status: 401 });
    fetcher.mockRestore();
  });

  it("serves a source-faithful demo rough-cut projection without fetching or changing chapter samples", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const fetcher = vi.spyOn(globalThis, "fetch");
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const data = getDemoChapterData(demoSeries[0]!.id);
    if (data === null) {
      throw new Error("Expected demo chapter data.");
    }
    const originalChapters = structuredClone(data.chapters);
    const originalSeries = await services.listSeries(new AbortController().signal);
    expect(originalSeries).toHaveLength(24);
    expect(data.chapters).toHaveLength(2);
    const openingChapter = data.chapters[0]!;
    const letterChapter = data.chapters[1]!;

    const opening = await services.getPersonalRoughCut(openingChapter.id, new AbortController().signal);
    const letter = await services.getPersonalRoughCut(letterChapter.id, new AbortController().signal);

    expect(opening).toMatchObject({
      revision: 4,
      saved: true,
      removed_asset_ids: [openingChapter.id + "-retired-asset", openingChapter.id + "-retired-asset"],
    });
    expect(opening.frames.map((frame) => frame.frame_index)).toEqual([1, 0]);
    const secondStoryboard = openingChapter.content?.[1]?.storyboard;
    const secondSourceId = Array.isArray(secondStoryboard) && typeof secondStoryboard[0] === "string"
      ? secondStoryboard[0]
      : null;
    expect(opening.frames[0]?.asset_id).toBe(secondSourceId);
    expect(opening.frames[1]).toMatchObject({ included: false, pending: true });
    expect(opening.frames.every((frame) => frame.preview_url === null)).toBe(true);
    expect(letter).toMatchObject({ revision: 0, saved: false, removed_asset_ids: [] });
    expect(letter.frames).toHaveLength(1);
    expect(letter.frames[0]).toMatchObject({ included: true, pending: false, frame_index: 0 });

    opening.frames[0]!.text = "改动的调用方副本";
    opening.removed_asset_ids.pop();
    const openingAgain = await services.getPersonalRoughCut(openingChapter.id, new AbortController().signal);
    const seriesAgain = await services.listSeries(new AbortController().signal);
    expect(openingAgain.frames[0]?.text).not.toBe("改动的调用方副本");
    expect(openingAgain.removed_asset_ids).toHaveLength(2);
    expect(data.chapters).toEqual(originalChapters);
    expect(seriesAgain).toHaveLength(originalSeries.length);
    expect(fetcher).not.toHaveBeenCalled();

    services.logout();
    await expect(services.getPersonalRoughCut(openingChapter.id, new AbortController().signal))
      .rejects.toMatchObject({ status: 401 });
    fetcher.mockRestore();
  });

  it("serves paginated demo tasks locally and never fetches task or result media", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const fetcher = vi.spyOn(globalThis, "fetch");
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);

    const first = await services.listMyTasks(1, new AbortController().signal);
    const second = await services.listMyTasks(2, new AbortController().signal);

    expect(first).toMatchObject({ total: 12, page: 1, page_size: 10 });
    expect(first.tasks).toHaveLength(10);
    expect(second).toMatchObject({ total: 12, page: 2, page_size: 10 });
    expect(second.tasks.map((task) => task.id)).toEqual(["task-demo-11", "task-demo-12"]);
    expect(first.tasks.find((task) => task.id === "task-demo-10")).toMatchObject({
      type: "ai-review",
      chapter_id: "demo-chapter-01",
      frame_index: 2,
      frame_count: null,
      prompt_id: { legacy: "unknown" },
      request_data: JSON.stringify({
        chapter_id: "demo-chapter-01",
        frame_index: 1,
        prompt_id: { legacy: "unknown" },
      }),
    });
    expect(fetcher).not.toHaveBeenCalled();
    await expect(services.listMyTasks(0, new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    fetcher.mockRestore();
  });

  it("serves the team directory locally without changing the single-user series demo", async () => {
    const storage = new MemoryStorage();
    const services = createDemoServices(storage);
    const fetcher = vi.spyOn(globalThis, "fetch");
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);

    const teams = await services.listMyTeams(new AbortController().signal);
    const rows = await services.listSeries(new AbortController().signal);

    expect(teams).toEqual(demoTeams);
    expect(teams.map((team) => team.id)).toContain("all");
    expect(teams.map((team) => team.id)).toContain("__proto__");
    expect(rows).toHaveLength(24);
    expect(fetcher).not.toHaveBeenCalled();
    fetcher.mockRestore();
  });

  it("uses the current bearer for the static team directory route without a query", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-team-token");
    const team = {
      id: "team-one",
      name: "工作室",
      owner_id: "owner-one",
      created_at: "2026-10-05T10:00:00",
      member_count: 1,
      my_role: "writer",
    };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse([team]));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(services.listMyTeams(new AbortController().signal)).resolves.toEqual([team]);

    const [input, init] = fetcher.mock.calls[0] ?? [];
    const requestUrl = new URL(String(input));
    expect(requestUrl.pathname).toBe("/api/teams/my");
    expect(requestUrl.search).toBe("");
    expect(init?.method).toBe("GET");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-team-token");
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-team-token");
  });

  it("reads real account credits and sends only the bio field for profile updates", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-account-token");
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ credits: -7 }))
      .mockResolvedValueOnce(jsonResponse({
        id: "account-user",
        username: "创作者",
        avatar_url: null,
        bio: "",
      }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const accountProfile = services.accountProfile;
    if (accountProfile === undefined) throw new Error("Expected the API account profile port.");
    const signal = new AbortController().signal;

    await expect(accountProfile.getCredits(signal)).resolves.toEqual({ credits: -7 });
    await expect(accountProfile.updateProfile({ bio: "" }, signal)).resolves.toEqual({
      id: "account-user",
      username: "创作者",
      avatar_url: null,
      bio: "",
    });

    const [creditsInput, creditsInit] = fetcher.mock.calls[0] ?? [];
    expect(new URL(String(creditsInput)).pathname).toBe("/api/auth/credits/me");
    expect(creditsInit?.method).toBe("GET");
    expect(new Headers(creditsInit?.headers).get("Authorization")).toBe("Bearer current-account-token");
    expect(creditsInit?.credentials).toBe("omit");
    expect(creditsInit?.cache).toBe("no-store");

    const [profileInput, profileInit] = fetcher.mock.calls[1] ?? [];
    expect(new URL(String(profileInput)).pathname).toBe("/api/users/me/profile");
    expect(profileInit?.method).toBe("PUT");
    expect(profileInit?.body).toBe(JSON.stringify({ bio: "" }));
    expect(new Headers(profileInit?.headers).get("Authorization")).toBe("Bearer current-account-token");
    expect(profileInit?.credentials).toBe("omit");
    expect(profileInit?.cache).toBe("no-store");

    expect(createDemoServices(storage).accountProfile).toBeUndefined();
  });

  it("uses the three account membership routes with anonymous reads, exact fields, and safe request options", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "membership-token");
    const config = {
      site_title: "创作平台",
      site_tagline: "",
      membership_benefits: "权益一\n权益二",
      tutorial_url: "",
      purchase_qr_code_url: "",
      purchase_contact_info: "联系管理员",
      storyboard_cost_multiplier: 1,
      payments_enabled: true,
    };
    const membershipPackages = [
      { id: "later", category: "membership", name: "后置顺序", price: 88, credits: null, days: 30, sort_order: 9 },
      { id: "first", category: "membership", name: "服务端首项", price: 29, credits: null, days: null, sort_order: 1 },
    ];
    const creditPackages = [
      { id: "credits-one", category: "credits", name: "积分包", price: 10, credits: 1000, days: null, sort_order: 2 },
    ];
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse(config))
      .mockResolvedValueOnce(jsonResponse(membershipPackages))
      .mockResolvedValueOnce(jsonResponse(creditPackages))
      .mockResolvedValueOnce(jsonResponse({ message: "兑换成功", type: "credits", value: 1000, detail: null }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const membership = services.accountMembership;
    if (membership === undefined) throw new Error("Expected the API account membership port.");
    const signal = new AbortController().signal;

    await expect(membership.getPublicConfig(signal)).resolves.toEqual(config);
    await expect(membership.listPurchasePackages("membership", signal)).resolves.toMatchObject([
      { id: "later", price: 88, credits: null, days: 30, sort_order: 9 },
      { id: "first", price: 29, credits: null, days: null, sort_order: 1 },
    ]);
    await expect(membership.listPurchasePackages("credits", signal)).resolves.toMatchObject([
      { id: "credits-one", price: 10, credits: 1000, days: null, sort_order: 2 },
    ]);
    await expect(membership.redeem({ code: "  one-time-code  " }, signal)).resolves.toEqual({
      message: "兑换成功",
      type: "credits",
      value: 1000,
      detail: null,
    });

    const calls = fetcher.mock.calls.map(([input, init]) => ({ url: new URL(String(input)), init }));
    expect(calls.map(({ url, init }) => [url.pathname, url.search, init?.method])).toEqual([
      ["/api/site-config", "", "GET"],
      ["/api/purchase-packages", "?category=membership", "GET"],
      ["/api/purchase-packages", "?category=credits", "GET"],
      ["/api/redemption-codes/redeem", "", "POST"],
    ]);
    for (const { init } of calls) {
      expect(init?.credentials).toBe("omit");
      expect(init?.cache).toBe("no-store");
      expect(init?.redirect).toBe("error");
      expect(init?.referrerPolicy).toBe("no-referrer");
      expect(init?.signal).toBeInstanceOf(AbortSignal);
    }
    expect(new Headers(calls[0]?.init?.headers).has("Authorization")).toBe(false);
    expect(new Headers(calls[1]?.init?.headers).has("Authorization")).toBe(false);
    expect(new Headers(calls[2]?.init?.headers).has("Authorization")).toBe(false);
    expect(new Headers(calls[3]?.init?.headers).get("Authorization")).toBe("Bearer membership-token");
    expect(calls[3]?.init?.body).toBe(JSON.stringify({ code: "  one-time-code  " }));
    expect(storage.getItem(API_TOKEN_KEY)).toBe("membership-token");
    expect(createDemoServices(storage).accountMembership).toBeUndefined();
  });

  it.each([201, 202, 204])("requires exact HTTP 200 for redemption, including successful status %i", async (status) => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "membership-token");
    const fetcher = vi.fn(async () => jsonResponse({ message: "accepted", type: "credits", value: 10, detail: null }, status));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const membership = services.accountMembership;
    if (membership === undefined) throw new Error("Expected the API account membership port.");

    await expect(membership.redeem({ code: "one-time-code" }, new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("membership-token");
  });

  it("requires exact HTTP 200 for both public membership reads and rejects unsupported categories locally", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({}, 201))
      .mockResolvedValueOnce(jsonResponse([], 202));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage: new MemoryStorage(), fetcher });
    const membership = services.accountMembership;
    if (membership === undefined) throw new Error("Expected the API account membership port.");
    const signal = new AbortController().signal;

    await expect(membership.getPublicConfig(signal)).rejects.toMatchObject({ kind: "invalid-response" });
    await expect(membership.listPurchasePackages("credits", signal)).rejects.toMatchObject({ kind: "invalid-response" });
    await expect(membership.listPurchasePackages("admin" as never, signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("keeps an anonymous 401 in-session and converts malformed membership payloads to invalid-response", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "preserved-token");
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({
        message: "兑换成功",
        type: "unknown",
        value: 10,
        detail: null,
      }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const membership = services.accountMembership;
    if (membership === undefined) throw new Error("Expected the API account membership port.");

    await expect(membership.getPublicConfig(new AbortController().signal))
      .rejects.toMatchObject({ kind: "http", status: 401 });
    await expect(membership.redeem({ code: "one-time-code" }, new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).has("Authorization")).toBe(false);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("preserved-token");
  });

  it("ignores a late redemption 401 after the API bearer changes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-membership-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const membership = services.accountMembership;
    if (membership === undefined) throw new Error("Expected the API account membership port.");
    const pending = membership.redeem({ code: "one-time-code" }, new AbortController().signal);

    storage.setItem(API_TOKEN_KEY, "new-membership-token");
    resolveFetch(jsonResponse({ detail: "expired" }, 401));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(new Headers(fetcher.mock.calls[0]?.[1]?.headers).get("Authorization"))
      .toBe("Bearer old-membership-token");
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-membership-token");
  });

  it("uses authenticated, exact-status session and password endpoints with scoped request fields", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-security-token");
    const sessionId = "session / α?#";
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse([{
        id: sessionId,
        jti: "private-jwt-id",
        device_name: "桌面浏览器",
        ip_address: "127.0.0.1",
        user_agent: "Example browser",
        created_at: "2026-10-01T09:00:00",
        last_seen_at: "2026-10-01T10:00:00+08:00",
        revoked_at: null,
        is_active: true,
        is_current: false,
      }]))
      .mockResolvedValueOnce(jsonResponse({ message: "设备已下线", session_id: sessionId }))
      .mockResolvedValueOnce(jsonResponse({ message: "密码修改成功，请重新登录" }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const security = services.accountSecurity;
    if (security === undefined) throw new Error("Expected the API account security port.");
    const signal = new AbortController().signal;

    await expect(security.listSessions(signal)).resolves.toMatchObject([
      { id: sessionId, deviceName: "桌面浏览器", isCurrent: false },
    ]);
    await expect(security.revokeSession(sessionId, signal)).resolves.toEqual({
      message: "设备已下线",
      session_id: sessionId,
    });
    await expect(security.changePassword({ old_password: "  current  ", new_password: " new pass " }, signal))
      .resolves.toEqual({ message: "密码修改成功，请重新登录" });

    const calls = fetcher.mock.calls.map(([input, init]) => ({
      url: new URL(String(input)),
      init,
    }));
    expect(calls.map(({ url, init }) => [url.pathname, init?.method])).toEqual([
      ["/api/auth/sessions", "GET"],
      ["/api/auth/sessions/session%20%2F%20%CE%B1%3F%23", "DELETE"],
      ["/api/auth/password", "PUT"],
    ]);
    expect(calls[0]?.url.search).toBe("");
    expect(calls[1]?.init?.body).toBeUndefined();
    expect(calls[2]?.init?.body).toBe(JSON.stringify({ old_password: "  current  ", new_password: " new pass " }));
    for (const { init } of calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-security-token");
      expect(init?.credentials).toBe("omit");
      expect(init?.cache).toBe("no-store");
      expect(init?.redirect).toBe("error");
      expect(init?.referrerPolicy).toBe("no-referrer");
    }
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-security-token");
    expect(createDemoServices(storage).accountSecurity).toBeUndefined();
  });

  it.each([401, 403])("preserves the current session after account security HTTP %i", async (status) => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "preserved-security-token");
    const fetcher = vi.fn(async () => jsonResponse({ detail: "denied" }, status));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const security = services.accountSecurity;
    if (security === undefined) throw new Error("Expected the API account security port.");

    await expect(security.listSessions(new AbortController().signal))
      .rejects.toMatchObject({ kind: "http", status });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("preserved-security-token");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each(["list", "revoke", "password"] as const)("requires exact 200 for the account security %s endpoint", async (operation) => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-security-token");
    const fetcher = vi.fn(async () => jsonResponse({ message: "accepted" }, 201));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const security = services.accountSecurity;
    if (security === undefined) throw new Error("Expected the API account security port.");

    const signal = new AbortController().signal;
    const request = operation === "list"
      ? security.listSessions(signal)
      : operation === "revoke"
        ? security.revokeSession("session-one", signal)
        : security.changePassword({ old_password: "old", new_password: "new password" }, signal);
    await expect(request)
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-security-token");
  });

  it("treats an unconfirmed password receipt as one unknown write", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-security-token");
    const fetcher = vi.fn(async () => jsonResponse({ message: "密码已受理" }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const security = services.accountSecurity;
    if (security === undefined) throw new Error("Expected the API account security port.");

    await expect(security.changePassword(
      { old_password: "old", new_password: "new password" }, new AbortController().signal,
    )).rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("current-security-token");
  });

  it("ignores a session-list response after the API bearer changes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-security-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const security = services.accountSecurity;
    if (security === undefined) throw new Error("Expected the API account security port.");
    const pending = security.listSessions(new AbortController().signal);

    storage.setItem(API_TOKEN_KEY, "new-security-token");
    resolveFetch(jsonResponse([]));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-security-token");
  });

  it("uses the current bearer and a once-encoded chapter path for the dedicated rough-cut GET", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-rough-cut-token");
    const chapterId = "chapter/α ?#";
    const response: PersonalRoughCutSnapshot = {
      chapter_id: chapterId,
      revision: 0,
      saved: false,
      frames: [],
      removed_asset_ids: [],
    };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse(response));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(services.getPersonalRoughCut(chapterId, new AbortController().signal)).resolves.toEqual(response);

    const [input, init] = fetcher.mock.calls[0] ?? [];
    const requestUrl = new URL(String(input));
    expect(requestUrl.pathname).toBe("/api/chapters/chapter%2F%CE%B1%20%3F%23/rough-cut");
    expect(requestUrl.search).toBe("");
    expect(requestUrl.hash).toBe("");
    expect(init?.method).toBe("GET");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-rough-cut-token");
  });

  it("rejects an absent rough-cut session and invalid path before issuing a request", async () => {
    const storage = new MemoryStorage();
    const fetcher = vi.fn(async () => jsonResponse({}));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });

    await expect(services.getPersonalRoughCut("chapter-one", new AbortController().signal))
      .rejects.toMatchObject({ status: 401 });
    storage.setItem(API_TOKEN_KEY, "current-token");
    await expect(services.getPersonalRoughCut("..", new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not start a rough-cut request after its lifecycle signal has been aborted", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-token");
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({}));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const controller = new AbortController();
    controller.abort();

    await expect(services.getPersonalRoughCut("chapter-one", controller.signal))
      .rejects.toMatchObject({ kind: "aborted" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("ignores a rough-cut response after the bearer session has changed", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const controller = new AbortController();
    const pending = services.getPersonalRoughCut("chapter-one", controller.signal);

    storage.setItem(API_TOKEN_KEY, "new-token");
    resolveFetch(jsonResponse({
      chapter_id: "chapter-one",
      revision: 0,
      saved: false,
      frames: [],
      removed_asset_ids: [],
    }));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
  });

  it("reads rough-cut media through the configured API origin with the current bearer", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "current-media-token");
    const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      if (new URL(String(input)).pathname.endsWith("/rough-cut")) {
        return jsonResponse({
          chapter_id: "chapter-one",
          revision: 1,
          saved: true,
          frames: [],
          removed_asset_ids: [],
        });
      }
      return new Response("video-bytes", { headers: { "Content-Type": "video/mp4" } });
    });
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const readForPlayback = services.getPersonalRoughCutForPlayback;
    if (readForPlayback === undefined) throw new Error("Expected the API playback snapshot reader.");

    const { snapshot, reader } = await readForPlayback("chapter-one", new AbortController().signal);
    expect(snapshot.revision).toBe(1);
    expect(reader).not.toBeNull();
    if (reader === null) throw new Error("Expected a session-bound media reader.");
    await expect(reader.read("/uploads/storyboard/clip.mp4", new AbortController().signal))
      .resolves.toBeInstanceOf(Blob);

    const [snapshotInput, snapshotInit] = fetcher.mock.calls[0] ?? [];
    expect(String(snapshotInput)).toBe("http://127.0.0.1:4175/api/chapters/chapter-one/rough-cut");
    expect(new Headers(snapshotInit?.headers).get("Authorization")).toBe("Bearer current-media-token");
    const [input, init] = fetcher.mock.calls[1] ?? [];
    expect(String(input)).toBe("http://127.0.0.1:4175/uploads/storyboard/clip.mp4");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer current-media-token");
  });

  it("keeps the canonical rough-cut session anchor after the bearer changes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-media-token");
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => jsonResponse({
      chapter_id: "chapter-one",
      revision: 1,
      saved: true,
      frames: [],
      removed_asset_ids: [],
    }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const readForPlayback = services.getPersonalRoughCutForPlayback;
    if (readForPlayback === undefined) throw new Error("Expected the API playback snapshot reader.");
    const { reader } = await readForPlayback("chapter-one", new AbortController().signal);
    if (reader === null) throw new Error("Expected a session-bound media reader.");
    expect(reader.isCurrent()).toBe(true);

    storage.setItem(API_TOKEN_KEY, "new-media-token");

    expect(reader.isCurrent()).toBe(false);
    await expect(reader.read("/uploads/storyboard/clip.mp4", new AbortController().signal))
      .rejects.toMatchObject({ kind: "aborted" });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-media-token");
  });

  it("clears only API session data for an unauthorized series response", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "expired-token");
    storage.setItem(API_USER_KEY, "cached-user");
    storage.setItem(MOCK_SESSION_KEY, "mock-session");
    storage.setItem(MOCK_USER_KEY, "mock-user");
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn(async () => jsonResponse({ detail: "expired" }, 401)),
    });

    try {
      await services.listSeries(new AbortController().signal);
      throw new Error("Expected an authorization error.");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ status: 401 });
    }
    expect(storage.getItem(API_TOKEN_KEY)).toBe("expired-token");
    services.logout();
    expect(storage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(storage.getItem(MOCK_SESSION_KEY)).toBe("mock-session");
  });

  it("uses exact series CRUD routes, isolated bearer auth, style catalogue and multipart cover upload", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "creative-token");
    storage.setItem("admin_token", "admin-token-must-not-be-used");
    const id = "series/α ?#";
    const baseRow = {
      ...demoSeries[0]!,
      id,
      name: "服务端剧集",
      description: "原简介",
      image_url: null,
      style_prompt_id: null,
      style_prompt_name: null,
      style_prompt_owner_name: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    };
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      calls.push({ url, init });
      if (url.pathname === "/api/prompts") {
        return jsonResponse([{
          id: "style-one",
          name: "悬疑",
          category: "style",
          is_system: true,
          system_prompt: "不得在选择器展示的提示词正文",
        }]);
      }
      if (url.pathname === "/api/upload/image") {
        return jsonResponse({ url: "http://127.0.0.1:4175/uploads/cover.webp" });
      }
      if (init.method === "DELETE") {
        return new Response(null, { status: 204 });
      }
      if (init.method === "POST") {
        return jsonResponse({ ...baseRow, ...(JSON.parse(String(init.body)) as object) }, 201);
      }
      if (init.method === "PUT") {
        return jsonResponse({ ...baseRow, ...(JSON.parse(String(init.body)) as object) }, 200);
      }
      return jsonResponse(baseRow);
    });
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const signal = new AbortController().signal;
    const create = services.createSeries;
    const get = services.getSeries;
    const update = services.updateSeries;
    const remove = services.deleteSeries;
    const prompts = services.listSeriesStylePrompts;
    const upload = services.uploadSeriesCover;
    if (!create || !get || !update || !remove || !prompts || !upload) {
      throw new Error("Series management API is unavailable.");
    }

    const detail = await get(id, signal);
    expect(detail.id).toBe(id);
    expect(calls[0]?.url.pathname).toBe("/api/series/series%2F%CE%B1%20%3F%23");
    expect(calls[0]?.init.method).toBe("GET");
    expect(new Headers(calls[0]?.init.headers).get("Authorization")).toBe("Bearer creative-token");

    const created = await create({
      name: "新剧",
      description: "",
      image_url: null,
      style_prompt_id: "style-one",
    }, signal);
    expect(created.name).toBe("新剧");
    expect(calls[1]?.url.pathname).toBe("/api/series");
    expect(calls[1]?.init.method).toBe("POST");
    expect(calls[1]?.init.body).toBe(JSON.stringify({
      name: "新剧",
      description: "",
      image_url: null,
      style_prompt_id: "style-one",
    }));

    const updated = await update(id, { image_url: "", style_prompt_id: "" }, signal);
    expect(updated.image_url).toBeNull();
    expect(updated.style_prompt_id).toBeNull();
    expect(calls[2]?.url.pathname).toBe("/api/series/series%2F%CE%B1%20%3F%23");
    expect(calls[2]?.init.method).toBe("PUT");
    expect(calls[2]?.init.body).toBe(JSON.stringify({ image_url: "", style_prompt_id: "" }));

    expect(await prompts(signal)).toEqual([{
      id: "style-one",
      name: "悬疑",
      is_system: true,
      user_id: null,
    }]);
    expect(calls[3]?.url.pathname).toBe("/api/prompts");
    expect(calls[3]?.url.search).toBe("?category=style");

    const file = new File(["cover"], "cover.webp", { type: "image/webp" });
    expect(await upload(file, signal)).toBe("http://127.0.0.1:4175/uploads/cover.webp");
    const coverCall = calls[4]!;
    expect(coverCall.url.pathname).toBe("/api/upload/image");
    expect(coverCall.init.method).toBe("POST");
    expect(new Headers(coverCall.init.headers).get("Authorization")).toBe("Bearer creative-token");
    expect(new Headers(coverCall.init.headers).has("Content-Type")).toBe(false);
    expect(coverCall.init.body).toBeInstanceOf(FormData);
    expect([...(coverCall.init.body as FormData).keys()]).toEqual(["file"]);

    await remove(id, signal);
    expect(calls[5]?.url.pathname).toBe("/api/series/series%2F%CE%B1%20%3F%23");
    expect(calls[5]?.init.method).toBe("DELETE");
    expect(new Headers(calls[5]?.init.headers).get("Authorization")).toBe("Bearer creative-token");
    expect(storage.getItem(API_TOKEN_KEY)).toBe("creative-token");
    expect(storage.getItem("admin_token")).toBe("admin-token-must-not-be-used");
  });

  it("rejects a detail response bound to a different requested series id", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "creative-token");
    const fetcher = vi.fn(async () => jsonResponse({ ...demoSeries[0]!, id: "another-series" }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const get = services.getSeries;
    if (!get) throw new Error("Series detail API is unavailable.");

    let failure: unknown;
    try {
      await get("requested-series", new AbortController().signal);
    } catch (error) {
      failure = error;
    }

    expect(failure).toBeInstanceOf(InvalidResponseError);
    expect(failure).toMatchObject({ message: "服务器返回了其他剧集的详情。" });
    expect(normalizeApiError(failure)).toMatchObject({
      kind: "invalid-response",
      message: "服务器返回了其他剧集的详情。",
      status: null,
      detail: null,
    });
  });

  it("does not let a late 401 from an older creative token affect the current session", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-creative-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const listStyles = services.listSeriesStylePrompts;
    if (!listStyles) throw new Error("Series style API is unavailable.");

    const pending = listStyles(new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "new-creative-token");
    resolveFetch(jsonResponse({ detail: "old session expired" }, 401));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-creative-token");
  });

  it("sends chapter create, title-only update, and empty 204 delete to the bound routes", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "creative-token");
    const chapterId = "chapter/α ?#";
    const seriesId = "series/β ?#";
    const record = (title: string) => ({
      id: chapterId,
      series_id: seriesId,
      title,
      content: [{ text: "" }],
      order: 4,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
      lock: null,
    });
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const responses = [
      jsonResponse(record("新章节"), 201),
      jsonResponse(record("修改名称"), 200),
      { ok: true, status: 204, json: vi.fn(async () => { throw new Error("204 has no JSON body"); }) } as unknown as Response,
    ];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      calls.push({ url, init });
      const next = responses.shift();
      if (next === undefined) {
        throw new Error("Unexpected extra chapter request.");
      }
      return next;
    };
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const create = services.createChapter;
    const update = services.updateChapter;
    const remove = services.deleteChapter;
    if (!create || !update || !remove) {
      throw new Error("Chapter management API is unavailable.");
    }
    const signal = new AbortController().signal;

    await expect(create(seriesId, { title: "新章节", content: [{ text: "" }], order: 0 }, signal))
      .resolves.toMatchObject({ id: chapterId, series_id: seriesId, title: "新章节" });
    await expect(update(seriesId, chapterId, { title: "修改名称" }, signal))
      .resolves.toMatchObject({ id: chapterId, series_id: seriesId, title: "修改名称" });
    await expect(remove(seriesId, chapterId, signal)).resolves.toBeUndefined();

    expect(calls.map(({ url, init }) => [init.method, url.pathname])).toEqual([
      ["POST", "/api/series/series%2F%CE%B2%20%3F%23/chapters"],
      ["PUT", "/api/chapters/chapter%2F%CE%B1%20%3F%23"],
      ["DELETE", "/api/chapters/chapter%2F%CE%B1%20%3F%23"],
    ]);
    expect(calls.map(({ init }) => new Headers(init.headers).get("Authorization")))
      .toEqual(["Bearer creative-token", "Bearer creative-token", "Bearer creative-token"]);
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      title: "新章节",
      content: [{ text: "" }],
      order: 0,
    });
    expect(JSON.parse(String(calls[1]?.init.body))).toEqual({ title: "修改名称" });
    expect(calls[2]?.init.body).toBeUndefined();
  });

  it("deletes a frame through the dedicated chapter route and returns the refreshed chapter", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "creative-token");
    const chapterId = "chapter/α ?#";
    const seriesId = "series/β ?#";
    const savedChapter = {
      id: chapterId,
      series_id: seriesId,
      title: "删除后章节",
      content: [{ text: "保留镜头", storyboard: ["asset-1"] }],
      order: 4,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-02T00:00:00Z",
      lock: null,
    };
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      calls.push({ url, init });
      return jsonResponse(savedChapter);
    };
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const removeFrame = requireServiceMethod(services.deleteFrame, "deleteFrame");

    await expect(removeFrame(seriesId, chapterId, 0, new AbortController().signal))
      .resolves.toMatchObject({ id: chapterId, series_id: seriesId, content: savedChapter.content });

    expect(calls).toHaveLength(1);
    expect(calls[0]?.init.method).toBe("PUT");
    expect(calls[0]?.url.pathname).toBe("/api/chapters/chapter%2F%CE%B1%20%3F%23/delete-frame");
    expect(new Headers(calls[0]?.init.headers).get("Authorization")).toBe("Bearer creative-token");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({ frame_index: 0 });
  });

  it("keeps demo storyboard assets aligned when a frame is deleted", async () => {
    const services = createDemoServices(new MemoryStorage());
    const signal = new AbortController().signal;
    await services.login({ username: "demo", password: "demo123" }, signal);
    const create = requireServiceMethod(services.createChapter, "createChapter");
    const update = requireServiceMethod(services.updateChapter, "updateChapter");
    const removeFrame = requireServiceMethod(services.deleteFrame, "deleteFrame");
    const saveNote = requireServiceMethod(services.savePersonalProductionNote, "savePersonalProductionNote");
    const saveResume = requireServiceMethod(services.savePersonalProductionResume, "savePersonalProductionResume");
    const saveRoughCut = requireServiceMethod(services.savePersonalRoughCut, "savePersonalRoughCut");
    const seriesId = demoSeries[0]!.id;
    const created = await create(seriesId, {
      title: "第一章删除镜头的演示章节",
      content: [
        { text: "保留一", original_text: "原文一", preview: "preview-ready", fused: { keep: true } },
        { text: "待删除", original_text: "原文二", preview: "preview-ready", fused: { keep: true } },
        { text: "保留三", original_text: "原文三", preview: "preview-ready", fused: { keep: true } },
      ],
      order: 0,
    }, signal);
    await update(seriesId, created.id, { content: created.content ?? [] }, signal);
    const beforeAssets = await services.listStoryboardAssets(seriesId, created.id, signal);
    const firstId = beforeAssets.find((asset) => asset.frame_index === 0)?.id;
    const deletedId = beforeAssets.find((asset) => asset.frame_index === 1)?.id;
    const thirdId = beforeAssets.find((asset) => asset.frame_index === 2)?.id;
    if (firstId === undefined || deletedId === undefined || thirdId === undefined) {
      throw new Error("Expected demo frame assets.");
    }

    const initialNotes = await services.getPersonalProductionNotes(created.id, signal);
    const targetNoteFrame = initialNotes.frames.find((frame) => frame.storyboard_asset_id === deletedId);
    if (targetNoteFrame?.media_revision === null || targetNoteFrame === undefined) {
      throw new Error("Expected the target frame to have a readable media revision.");
    }
    const noted = await saveNote(created.id, {
      expected_revision: initialNotes.revision,
      frames: [{
        storyboard_asset_id: deletedId,
        expected_media_revision: targetNoteFrame.media_revision,
        status: "approved",
        note: "保留为历史记录",
      }],
    }, signal);
    const savedNotes = await saveResume(created.id, {
      expected_revision: noted.revision,
      resume_frame_id: deletedId,
    }, signal);
    expect(savedNotes.resume_frame_id).toBe(deletedId);
    const initialRoughCut = await services.getPersonalRoughCut(created.id, signal);
    const savedRoughCut = await saveRoughCut(created.id, {
      expected_revision: initialRoughCut.revision,
      frames: [
        { asset_id: thirdId, included: true },
        { asset_id: firstId, included: false },
        { asset_id: deletedId, included: true },
      ],
    }, signal);
    const result = await removeFrame(seriesId, created.id, 1, signal);
    const assets = await services.listStoryboardAssets(seriesId, created.id, signal);
    const notes = await services.getPersonalProductionNotes(created.id, signal);
    const roughCut = await services.getPersonalRoughCut(created.id, signal);

    expect(result.content).toHaveLength(2);
    expect(result.content).toEqual([
      expect.objectContaining({ text: "保留一", original_text: "原文一", storyboard: [firstId] }),
      expect.objectContaining({ text: "保留三", original_text: "原文三", storyboard: [thirdId] }),
    ]);
    expect(assets).toEqual([
      expect.objectContaining({ id: firstId, frame_index: 0 }),
      expect.objectContaining({ id: thirdId, frame_index: 1 }),
    ]);
    expect(assets.some((asset) => asset.id === deletedId)).toBe(false);
    expect(notes.frames).toEqual([
      expect.objectContaining({ frame_index: 0, storyboard_asset_id: firstId }),
      expect.objectContaining({ frame_index: 1, storyboard_asset_id: thirdId }),
    ]);
    expect(notes.resume_frame_id).toBe(deletedId);
    expect(notes.frame_notes.get(deletedId)).toMatchObject({
      status: "unmarked",
      approved_media_revision: null,
      needs_reconfirmation: true,
    });
    expect(roughCut.frames).toEqual([
      expect.objectContaining({ asset_id: thirdId, frame_index: 1, included: true }),
      expect.objectContaining({ asset_id: firstId, frame_index: 0, included: false }),
    ]);
    expect(roughCut.frames.some((frame) => frame.asset_id === deletedId)).toBe(false);
    expect(roughCut.removed_asset_ids).toEqual([deletedId]);
    expect(roughCut.revision).toBe(savedRoughCut.revision);
    await removeFrame(seriesId, created.id, 1, signal);
    await expect(removeFrame(seriesId, created.id, 0, signal)).rejects.toMatchObject({ status: 422 });
  });

  it("assigns a stable demo asset to an inserted frame and shifts notes and rough-cut indices", async () => {
    const services = createDemoServices(new MemoryStorage());
    const signal = new AbortController().signal;
    await services.login({ username: "demo", password: "demo123" }, signal);
    const create = requireServiceMethod(services.createChapter, "createChapter");
    const update = requireServiceMethod(services.updateChapter, "updateChapter");
    const seriesId = demoSeries[0]!.id;
    const created = await create(seriesId, {
      title: "插入镜头的演示章节",
      content: [{ text: "前镜", original_text: "前镜原文", preview: "preview-ready", fused: { keep: true } }],
      order: 0,
    }, signal);
    await update(seriesId, created.id, { content: created.content ?? [] }, signal);
    const baseAssets = await services.listStoryboardAssets(seriesId, created.id, signal);
    const baseId = baseAssets[0]?.id;
    if (baseId === undefined) throw new Error("Expected a stable base-frame identity.");

    const result = await update(seriesId, created.id, {
      content: [
        { text: "前镜", original_text: "前镜原文", preview: "preview-ready", fused: { keep: true }, storyboard: [baseId] },
        { text: "新镜", character: ["character-a"], scene: [], prop: [], storyboard: [], preview: null },
      ],
    }, signal);
    const assets = await services.listStoryboardAssets(seriesId, created.id, signal);
    const notes = await services.getPersonalProductionNotes(created.id, signal);
    const roughCut = await services.getPersonalRoughCut(created.id, signal);
    const insertedReferences = result.content?.[1]?.storyboard;
    const insertedId = Array.isArray(insertedReferences) && typeof insertedReferences[0] === "string"
      ? insertedReferences[0]
      : null;

    expect(insertedId).toEqual(expect.any(String));
    expect(result.content?.[0]).toMatchObject({
      text: "前镜", original_text: "前镜原文", preview: "preview-ready", fused: { keep: true }, storyboard: [baseId],
    });
    expect(assets.map((asset) => [asset.id, asset.frame_index])).toEqual([[baseId, 0], [insertedId, 1]]);
    expect(notes.frames.map((frame) => [frame.storyboard_asset_id, frame.frame_index]))
      .toEqual([[baseId, 0], [insertedId, 1]]);
    expect(roughCut.frames.map((frame) => [frame.asset_id, frame.frame_index]))
      .toEqual([[baseId, 0], [insertedId, 1]]);
  });

  it("UCHAP-03 preserves explicit demo order while auto-assigning order zero", async () => {
    const first = createDemoServices(new MemoryStorage());
    const second = createDemoServices(new MemoryStorage());
    const signal = new AbortController().signal;
    await first.login({ username: "demo", password: "demo123" }, signal);
    await second.login({ username: "demo", password: "demo123" }, signal);
    const firstSeries = demoSeries[0]!;
    const seed = getDemoChapterData(firstSeries.id);
    if (seed === null) throw new Error("Expected a seeded chapter fixture.");
    const original = seed.chapters.find((chapter) => chapter.title.startsWith("第一章"));
    if (original === undefined) throw new Error("Expected the first seeded chapter.");
    const expectedNotes = getDemoPersonalProductionNotes(
      original.id,
      original,
      seed.assetsByChapter[original.id] ?? [],
    );
    const expectedRoughCut = getDemoPersonalRoughCut(original, seed.assetsByChapter[original.id] ?? []);
    const update = first.updateChapter;
    const create = first.createChapter;
    const remove = first.deleteChapter;
    if (!update || !create || !remove) throw new Error("Demo chapter management is unavailable.");

    await update(firstSeries.id, original.id, { title: "已改名章节" }, signal);
    expect(await first.getPersonalProductionNotes(original.id, signal)).toEqual(expectedNotes);
    expect(await first.getPersonalRoughCut(original.id, signal)).toEqual(expectedRoughCut);

    const created = await create(firstSeries.id, {
      title: "新建空白章",
      content: [{ text: "" }],
      order: 0,
    }, signal);
    expect(created.content).toEqual([{ text: "" }]);
    expect(created.order).toBe(3);
    expect((await first.listChapters(firstSeries.id, signal))[0]?.id).toBe(created.id);
    const explicitlyOrdered = await create(firstSeries.id, {
      title: "保留显式顺序",
      content: [{ text: "" }],
      order: 7,
    }, signal);
    expect(explicitlyOrdered.order).toBe(7);
    expect((await first.listChapters(firstSeries.id, signal))[0]?.id).toBe(explicitlyOrdered.id);
    expect((await second.listChapters(firstSeries.id, signal)).some((chapter) => chapter.id === created.id))
      .toBe(false);
    expect(await first.listStoryboardAssets(firstSeries.id, created.id, signal)).toEqual([]);
    await remove(firstSeries.id, explicitlyOrdered.id, signal);

    await remove(firstSeries.id, created.id, signal);
    await expect(first.listStoryboardAssets(firstSeries.id, created.id, signal)).rejects.toMatchObject({ status: 404 });
    await expect(first.getPersonalProductionNotes(created.id, signal)).rejects.toMatchObject({ status: 404 });
    await expect(first.getPersonalRoughCut(created.id, signal)).rejects.toMatchObject({ status: 404 });
    expect((await first.listChapters(firstSeries.id, signal)).some((chapter) => chapter.id === created.id)).toBe(false);
  });

  it("keeps demo chapter reorder aligned with its descending directory view", async () => {
    const first = createDemoServices(new MemoryStorage());
    const second = createDemoServices(new MemoryStorage());
    const signal = new AbortController().signal;
    await first.login({ username: "demo", password: "demo123" }, signal);
    await second.login({ username: "demo", password: "demo123" }, signal);
    const seriesId = demoSeries[0]!.id;
    const originalOrder = (await second.listChapters(seriesId, signal)).map((chapter) => chapter.id);
    const visualOrder = [...originalOrder].reverse();
    const reorder = first.reorderChapters;
    if (reorder === undefined) throw new Error("Demo chapter reorder is unavailable.");

    await reorder(seriesId, {
      chapters: visualOrder.map((id, index) => ({ id, order: visualOrder.length - index })),
    }, signal);

    expect((await first.listChapters(seriesId, signal)).map((chapter) => chapter.id)).toEqual(visualOrder);
    expect((await second.listChapters(seriesId, signal)).map((chapter) => chapter.id)).toEqual(originalOrder);
  });

  it("persists demo frame edits and generated storyboard assets within one service instance", async () => {
    const first = createDemoServices(new MemoryStorage());
    const second = createDemoServices(new MemoryStorage());
    const signal = new AbortController().signal;
    await first.login({ username: "demo", password: "demo123" }, signal);
    await second.login({ username: "demo", password: "demo123" }, signal);
    const seriesId = demoSeries[0]!.id;
    const create = first.createChapter;
    const update = first.updateChapter;
    if (!create || !update) throw new Error("Demo chapter editing is unavailable.");
    const created = await create(seriesId, {
      title: "仅本实例的章节",
      content: [{ text: "初始文字", future_data: { retained: [1, 2] } }],
      order: 0,
    }, signal);

    await update(seriesId, created.id, {
      content: [{ text: "更新文字", future_data: { retained: [1, 2] } }],
    }, signal);

    const saved = (await first.listChapters(seriesId, signal)).find((chapter) => chapter.id === created.id);
    const assets = await first.listStoryboardAssets(seriesId, created.id, signal);
    const savedFrame = saved?.content?.[0];
    const generatedId = Array.isArray(savedFrame?.storyboard) ? savedFrame.storyboard[0] : null;
    expect(saved?.content?.[0]).toMatchObject({
      text: "更新文字",
      future_data: { retained: [1, 2] },
      storyboard: [expect.any(String)],
    });
    expect(assets).toHaveLength(1);
    expect(assets[0]).toMatchObject({
      id: generatedId,
      series_id: seriesId,
      chapter_id: created.id,
      frame_index: 0,
    });
    expect((await second.listChapters(seriesId, signal)).some((chapter) => chapter.id === created.id)).toBe(false);
  });

  it("keeps demo series mutations local to one service instance and gives new series empty production data", async () => {
    const storage = new MemoryStorage();
    const first = createDemoServices(storage);
    const second = createDemoServices(storage);
    const fetcher = vi.spyOn(globalThis, "fetch");
    await first.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const create = first.createSeries;
    const get = first.getSeries;
    const update = first.updateSeries;
    const remove = first.deleteSeries;
    const listStyles = first.listSeriesStylePrompts;
    if (!create || !get || !update || !remove || !listStyles) {
      throw new Error("Demo series management is unavailable.");
    }
    const signal = new AbortController().signal;
    const before = await first.listSeries(signal);
    const created = await create({
      name: "演示新剧",
      description: "",
      image_url: null,
      style_prompt_id: null,
    }, signal);
    expect((await first.listSeries(signal))).toHaveLength(before.length + 1);
    expect(await second.listSeries(signal)).toHaveLength(before.length);
    expect(await get(created.id, signal)).toMatchObject({ name: "演示新剧", team_id: null });
    expect(await first.listChapters(created.id, signal)).toEqual([]);
    const createChapter = first.createChapter;
    if (createChapter === undefined) throw new Error("Demo chapter creation is unavailable.");
    const blankChapter = await createChapter(created.id, {
      title: "第一章",
      content: [{ text: "" }],
      order: 0,
    }, signal);
    expect((await first.listChapters(created.id, signal)).map((chapter) => chapter.id)).toEqual([blankChapter.id]);
    expect(await first.listStoryboardAssets(created.id, blankChapter.id, signal)).toEqual([]);
    const blankNotes = await first.getPersonalProductionNotes(blankChapter.id, signal);
    expect(blankNotes.revision).toBe(0);
    expect(blankNotes.frame_notes.size).toBe(0);
    const blankRoughCut = await first.getPersonalRoughCut(blankChapter.id, signal);
    expect(blankRoughCut.revision).toBe(0);
    expect(blankRoughCut.saved).toBe(false);
    await expect(first.listStoryboardAssets(created.id, "new-chapter", signal))
      .rejects.toMatchObject({ status: 404 });
    await update(created.id, { name: "演示修改" }, signal);
    expect((await get(created.id, signal)).name).toBe("演示修改");
    expect((await listStyles(signal)).length).toBeGreaterThan(0);
    await remove(created.id, signal);
    await expect(get(created.id, signal)).rejects.toMatchObject({ status: 404 });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("uses the registered frame review routes for capability, submit, observation, and cancellation", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "creative-token");
    const taskId = "review/task-1";
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const responses = [
      jsonResponse({
        operation: "frame.review",
        enabled: true,
        reason: null,
        supported_providers: ["openai", "deepseek", "custom"],
      }),
      jsonResponse({ task_id: taskId, model_name: "Review model", is_system: false, credit_cost: 3 }),
      jsonResponse({
        id: taskId,
        type: "ai-review",
        status: "queued",
        progress: 0,
        progress_message: null,
        result: null,
        credit_cost: 0,
        model_name: "Review model",
      }),
      jsonResponse({ task_id: taskId, status: "cancelling" }),
    ];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      calls.push({ url, init });
      const next = responses.shift();
      if (next === undefined) throw new Error("Unexpected extra frame review request.");
      return next;
    };
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const port = services.frameReview;
    if (port === undefined) throw new Error("Frame review API is unavailable.");
    const signal = new AbortController().signal;
    await expect(port.getCapabilities(signal)).resolves.toMatchObject({ enabled: true });
    await expect(port.submit({
      chapter_id: "chapter-1",
      frame_index: 2,
      prompt_id: "prompt-1",
      model_id: "model-1",
      message_id: "",
      error_reason: "检查动作方向",
      asset_context: "",
      prev_context: "",
      enable_thinking: false,
    }, signal)).resolves.toMatchObject({ task_id: taskId, credit_cost: 3 });
    await expect(port.getTask(taskId, signal)).resolves.toMatchObject({ id: taskId, status: "queued" });
    await expect(port.cancel(taskId, signal)).resolves.toEqual({ task_id: taskId, status: "cancelling" });

    expect(calls.map(({ url, init }) => [init.method, url.pathname])).toEqual([
      ["GET", "/api/chat/frame-review/capabilities"],
      ["POST", "/api/chat/frame-review"],
      ["GET", "/api/chat/ai-review/review%2Ftask-1"],
      ["POST", "/api/chat/batch-optimize/review%2Ftask-1/cancel"],
    ]);
    expect(calls.every(({ init }) => new Headers(init.headers).get("Authorization") === "Bearer creative-token"))
      .toBe(true);
    expect(JSON.parse(String(calls[1]?.init.body))).toMatchObject({
      chapter_id: "chapter-1",
      frame_index: 2,
      model_id: "model-1",
      prompt_id: "prompt-1",
    });
  });

  it("routes frame optimize submission and observation through their distinct endpoints", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "creative-token");
    const taskId = "optimize/task-1";
    const calls: Array<{ url: URL; init: RequestInit }> = [];
    const responses = [
      jsonResponse({
        operation: "frame.optimize",
        enabled: true,
        reason: null,
        supported_providers: ["openai", "deepseek", "custom"],
      }),
      jsonResponse({ task_id: taskId, chapter_id: "chapter-1", frame_count: 1 }),
      jsonResponse({
        task: {
          task_id: taskId,
          status: "reconciling",
          progress: 80,
          progress_message: "等待账务核验",
          created_at: null,
        },
      }),
      jsonResponse({
        task_id: taskId,
        status: "failed",
        progress: 100,
        progress_message: "partial",
        result: { partial: [{ index: 0, optimized_text: "优化结果" }], error: "one_item_failed" },
      }),
      jsonResponse({ task_id: taskId, status: "cancelling" }),
    ];
    const fetcher: typeof fetch = async (input, init = {}) => {
      const url = input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
      calls.push({ url, init });
      const next = responses.shift();
      if (next === undefined) throw new Error("Unexpected extra frame optimize request.");
      return next;
    };
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const port = services.frameOptimize;
    if (port === undefined) throw new Error("Frame optimize API is unavailable.");
    const signal = new AbortController().signal;
    const input = {
      chapter_id: "chapter-1",
      model_id: "model-1",
      prompt_id: "",
      enable_thinking: false,
      concurrency: 5,
      chapter_texts: ["【剧情片段】\n原文"],
      frames: [{ index: 0, text: "原文", asset_context: "角色：小林" }],
    };
    await expect(port.getCapabilities(signal)).resolves.toMatchObject({ enabled: true });
    await expect(port.submit(input, signal)).resolves.toEqual({
      task_id: taskId, chapter_id: "chapter-1", frame_count: 1,
    });
    await expect(port.getRunning("chapter/one", signal)).resolves.toMatchObject({ status: "reconciling" });
    await expect(port.getTaskStatus(taskId, signal)).resolves.toMatchObject({ status: "failed" });
    await expect(port.cancel(taskId, signal)).resolves.toEqual({ task_id: taskId, status: "cancelling" });

    expect(calls.map(({ url, init }) => [init.method, url.pathname])).toEqual([
      ["GET", "/api/chat/frame-optimize/capabilities"],
      ["POST", "/api/chat/frame-optimize"],
      ["GET", "/api/chat/batch-optimize/running"],
      ["GET", "/api/chat/batch-optimize/optimize%2Ftask-1/status"],
      ["POST", "/api/chat/batch-optimize/optimize%2Ftask-1/cancel"],
    ]);
    expect(calls[2]?.url.searchParams.get("chapter_id")).toBe("chapter/one");
    expect(calls.every(({ init }) => new Headers(init.headers).get("Authorization") === "Bearer creative-token"))
      .toBe(true);
    expect(JSON.parse(String(calls[1]?.init.body))).toEqual(input);
  });

  it("does not allow a stale session response to attach a frame optimize capability", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const port = services.frameOptimize;
    if (port === undefined) throw new Error("Frame optimize API is unavailable.");

    const pending = port.getCapabilities(new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "new-token");
    resolveFetch(jsonResponse({
      operation: "frame.optimize",
      enabled: true,
      reason: null,
      supported_providers: ["openai"],
    }, 401));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
  });

  it("ignores a late 401 from an old token while reading frame review capabilities", async () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "old-token");
    let resolveFetch!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveFetch = resolve; }));
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const port = services.frameReview;
    if (port === undefined) throw new Error("Frame review API is unavailable.");

    const pending = port.getCapabilities(new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "new-token");
    resolveFetch(jsonResponse({ detail: "old session expired" }, 401));

    await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
  });

});
