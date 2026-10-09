// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getDemoChapterData } from "../../features/chapters/demoChapters";
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
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
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
});
