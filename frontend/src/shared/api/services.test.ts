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

});
