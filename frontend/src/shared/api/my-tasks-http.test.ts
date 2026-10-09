// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import { ApiError } from "./errors";
import type { MyTaskPage, MyTaskRecord } from "./myTasks";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));
const firstChapterId = "fixture-series-01-chapter-02";
const expectedTaskNumbers = [7, 3, 10, 1, 9, 2, 8, 4, 6, 5, 18, 12, 20, 11, 17, 13, 19, 14, 16, 15, 21, 22, 23];
const expectedTaskTypes = [
  "chat", "image", "image-single", "batch-image", "video", "video-single", "extract", "storyboard",
  "optimize-frame", "batch-optimize", "ai-review", "batch-optimize", "chat", "image", "image-single",
  "batch-image", "video", "video-single", "extract", "storyboard", "fused", "batch-optimize", "ai-review",
];

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

function taskWithNumber(tasks: MyTaskRecord[], number: number): MyTaskRecord {
  const task = tasks.find(({ id }) => id.endsWith(`-task-${String(number).padStart(2, "0")}`));
  if (!task) {
    throw new Error(`Fixture did not return task ${number}.`);
  }
  return task;
}

const statusCases = [401, 403, 404, 422, 500] as const;
const invalidResponseModes = ["invalid-json", "invalid-structure", "page-mismatch", "page-size-mismatch", "duplicate-id"] as const;

describe("my tasks API over real HTTP", () => {
  it("uses the tenth allowed route with Bearer auth and preserves the complete task page", async () => {
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
      const chapter = chapters[0];
      if (!chapter) {
        throw new Error("Fixture did not return its first chapter.");
      }
      await services.listStoryboardAssets(firstSeries.id, chapter.id, signal());
      await services.listCharacters(firstSeries.id, signal());
      await services.listScenes(firstSeries.id, signal());
      await services.listProps(firstSeries.id, signal());
      await services.getPersonalProductionNotes(chapter.id, signal());
      const page: MyTaskPage = await services.listMyTasks(1, signal());

      expect(signedIn.id).toBe("demo-user");
      expect(restored?.id).toBe("demo-user");
      expect(page).toMatchObject({ total: 23, page: 1, page_size: 10 });
      expect(page.tasks.map(({ id }) => Number(id.slice(-2)))).toEqual(expectedTaskNumbers.slice(0, 10));
      expect(page.tasks.map(({ type }) => type)).toEqual(expectedTaskTypes.slice(0, 10));
      expect(page.tasks).toHaveLength(10);

      const completedWithoutResult = taskWithNumber(page.tasks, 7);
      expect(completedWithoutResult).toMatchObject({
        status: "completed",
        result: "",
        progress: 100,
        credit_cost: -2,
        chapter_id: null,
        frame_index: null,
        frame_count: null,
        frame_text: null,
      });
      expect(Object.hasOwn(completedWithoutResult, "prompt_id")).toBe(false);
      const unknownStatus = taskWithNumber(page.tasks, 3);
      expect(unknownStatus).toMatchObject({
        status: "legacy_waiting_for_review",
        progress: -4,
        credit_cost: -9,
        chapter_id: null,
        frame_index: null,
        frame_count: null,
        frame_text: null,
      });
      expect(Object.hasOwn(unknownStatus, "prompt_id")).toBe(false);
      const emptyStatus = taskWithNumber(page.tasks, 10);
      expect(emptyStatus).toMatchObject({
        status: "",
        progress: 101,
        result: null,
        credit_cost: -1,
        chapter_id: null,
        frame_index: null,
        frame_count: null,
        frame_text: null,
        request_data: '{"prompt":"截断的请求片段🙂',
        progress_message: "<strong>历史HTML</strong> https://example.invalid/task-note?source=fixture 中文🙂",
      });
      expect(taskWithNumber(page.tasks, 1)).toMatchObject({
        result: "<b>失败记录文本</b> https://example.invalid/result 中文🙂",
        request_data: '{"prompt":"截断的旧request_data',
        chapter_id: null,
        frame_count: null,
        frame_text: null,
      });
      expect(taskWithNumber(page.tasks, 9)).toMatchObject({
        chapter_id: null,
        frame_index: null,
        frame_count: null,
        frame_text: null,
        progress: -7,
        updated_at: "2026-10-05T03:59:00Z",
      });
      const batchOptimize = taskWithNumber(page.tasks, 5);
      expect(batchOptimize).toMatchObject({
        type: "batch-optimize",
        chapter_id: true,
        frame_index: null,
        frame_count: ["unexpected", 9],
        frame_text: null,
      });
      expect(JSON.parse(batchOptimize.request_data ?? "null")).toEqual({
        chapter_id: true,
        frame_count: ["unexpected", 9],
      });
      expect(Object.hasOwn(batchOptimize, "prompt_id")).toBe(false);
      const ordinaryMessageTasks = page.tasks.filter(({ type }) => type !== "batch-optimize" && type !== "ai-review");
      expect(ordinaryMessageTasks.every(({ chapter_id, frame_index, frame_count, frame_text }) => (
        chapter_id === null && frame_index === null && frame_count === null && frame_text === null
      ))).toBe(true);
      expect(ordinaryMessageTasks.every((task) => !Object.hasOwn(task, "prompt_id"))).toBe(true);
      const omittedPrompt = taskWithNumber(page.tasks, 2);
      expect(omittedPrompt).toMatchObject({
        result: null,
        created_at: null,
        updated_at: "2026-10-05T12:00:00+08:00",
      });
      expect(Object.hasOwn(omittedPrompt, "prompt_id")).toBe(false);
      expect(Object.keys(completedWithoutResult)).toEqual(expect.arrayContaining([
        "id", "type", "message_id", "status", "result", "request_data", "progress_message",
        "credit_cost", "progress", "created_at", "updated_at", "asset_type", "asset_id",
        "asset_name", "chapter_title", "chapter_id", "frame_index", "frame_count", "frame_text",
      ]));

      const expectedTargets = [
        ["POST", "/api/auth/login"],
        ["GET", "/api/auth/me"],
        ["GET", "/api/series"],
        ["GET", "/api/series/fixture-series-01/chapters"],
        ["GET", "/api/series/fixture-series-01/storyboard-assets?chapter_id=fixture-series-01-chapter-02"],
        ["GET", "/api/series/fixture-series-01/characters"],
        ["GET", "/api/series/fixture-series-01/scenes"],
        ["GET", "/api/series/fixture-series-01/props"],
        ["GET", `/api/chapters/${firstChapterId}/personal-production-notes`],
        ["GET", "/api/chat/tasks/list?page=1&page_size=10"],
      ];
      expect(requests.map(({ method, target }) => [method, target])).toEqual(expectedTargets);
      expect(requests[0]?.authorization).toBeNull();
      expect(requests.slice(1).every(({ authorization }) => authorization === "Bearer fixture-demo-token"))
        .toBe(true);
      expect(fixtureRequests(fixture.output()).map(({ method, pathname }) => [method, pathname]))
        .toEqual(expectedTargets);
      expect(fixtureRequests(fixture.output()).every(({ method, pathname }) => (
        method === "GET" || (method === "POST" && pathname === "/api/auth/login")
      ))).toBe(true);
      for (const secret of [
        "demo123",
        "fixture-demo-token",
        "demo-two123",
        "fixture-demo-token-02",
        "截断的请求片段",
        "截断的旧request_data",
      ]) {
        expect(fixture.output()).not.toContain(secret);
      }
    } finally {
      await fixture.stop();
    }
  });

  it("returns each page in fixture order and allows a correct short final page", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);
    try {
      const second: MyTaskPage = await services.listMyTasks(2, signal());
      const third: MyTaskPage = await services.listMyTasks(3, signal());
      expect(second).toMatchObject({ total: 23, page: 2, page_size: 10 });
      expect(second.tasks.map(({ id }) => Number(id.slice(-2)))).toEqual(expectedTaskNumbers.slice(10, 20));
      expect(second.tasks.map(({ type }) => type)).toEqual(expectedTaskTypes.slice(10, 20));
      expect(taskWithNumber(second.tasks, 18)).toMatchObject({
        type: "ai-review",
        chapter_id: 42,
        frame_index: 1,
        frame_count: null,
        frame_text: null,
        prompt_id: { prompt: "ai-review-dynamic-value" },
      });
      expect(JSON.parse(taskWithNumber(second.tasks, 18).request_data ?? "null")).toEqual({
        chapter_id: 42,
        frame_index: 0,
        prompt_id: { prompt: "ai-review-dynamic-value" },
      });
      const unhashableChapterId = taskWithNumber(second.tasks, 12);
      expect(unhashableChapterId).toMatchObject({
        type: "batch-optimize",
        chapter_id: { legacy_chapter: "chapter-twelve" },
        frame_index: null,
        frame_count: null,
        frame_text: null,
      });
      expect(JSON.parse(unhashableChapterId.request_data ?? "null")).toEqual({
        chapter_id: { legacy_chapter: "chapter-twelve" },
        frame_count: 4,
        prompt_id: { prompt: "unreached" },
      });
      expect(unhashableChapterId.frame_count).toBeNull();
      expect(Object.hasOwn(unhashableChapterId, "prompt_id")).toBe(false);
      expect(third).toMatchObject({ total: 23, page: 3, page_size: 10 });
      expect(third.tasks.map(({ id }) => Number(id.slice(-2)))).toEqual(expectedTaskNumbers.slice(20));
      expect(third.tasks.map(({ type }) => type)).toEqual(expectedTaskTypes.slice(20));
      expect(requests.map(({ target }) => target)).toEqual([
        "/api/chat/tasks/list?page=2&page_size=10",
        "/api/chat/tasks/list?page=3&page_size=10",
      ]);
    } finally {
      await fixture.stop();
    }
  });

  it("keeps tasks private to the current Bearer account without a user filter", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const first = createServices(fixture.baseUrl, requests);
    const second = createServices(fixture.baseUrl, requests);
    try {
      const firstUser = await first.services.login({ username: "demo", password: "demo123" }, signal());
      const firstPage = await first.services.listMyTasks(1, signal());
      const secondUser = await second.services.login({ username: "demo-two", password: "demo-two123" }, signal());
      const secondPage = await second.services.listMyTasks(1, signal());

      expect(firstUser.id).toBe("demo-user");
      expect(secondUser.id).toBe("demo-user-02");
      expect(firstPage.tasks).toHaveLength(10);
      expect(secondPage.tasks).toHaveLength(10);
      expect(firstPage.tasks.every(({ id }) => id.startsWith("demo-user-task-"))).toBe(true);
      expect(secondPage.tasks.every(({ id }) => id.startsWith("demo-user-02-task-"))).toBe(true);
      const secondTaskIds = new Set(secondPage.tasks.map(({ id }) => id));
      expect(firstPage.tasks.every(({ id }) => !secondTaskIds.has(id))).toBe(true);
      expect(requests.filter(({ target }) => target.startsWith("/api/chat/tasks/list"))
        .map(({ authorization, target }) => [authorization, target])).toEqual([
        ["Bearer fixture-demo-token", "/api/chat/tasks/list?page=1&page_size=10"],
        ["Bearer fixture-demo-token-02", "/api/chat/tasks/list?page=1&page_size=10"],
      ]);
      expect(requests.some(({ target }) => /(?:user_id|team_id|series_id)=/.test(target))).toBe(false);
      expect(fixture.output()).not.toContain("demo-two123");
      expect(fixture.output()).not.toContain("fixture-demo-token-02");
    } finally {
      await fixture.stop();
    }
  });

  it("returns an empty out-of-range page and models a shrinking total without auto-correction", async () => {
    const emptyPageFixture = await startFixture({ FIXTURE_TASKS_MODE: "empty-page" });
    const emptyPageServices = createServices(emptyPageFixture.baseUrl).services;
    try {
      const page = await emptyPageServices.listMyTasks(2, signal());
      expect(page).toEqual({ total: 23, page: 2, page_size: 10, tasks: [] });
    } finally {
      await emptyPageFixture.stop();
    }

    const shrinkingFixture = await startFixture({ FIXTURE_TASKS_MODE: "total-shrink" });
    const shrinkingServices = createServices(shrinkingFixture.baseUrl).services;
    try {
      const first = await shrinkingServices.listMyTasks(1, signal());
      const second = await shrinkingServices.listMyTasks(2, signal());
      expect(first).toMatchObject({ total: 7, page: 1, page_size: 10 });
      expect(first.tasks).toHaveLength(7);
      expect(second).toEqual({ total: 7, page: 2, page_size: 10, tasks: [] });
    } finally {
      await shrinkingFixture.stop();
    }
  });

  it("requires Bearer auth and rejects unsupported filters without logging them", async () => {
    const fixture = await startFixture();
    const origin = fixture.baseUrl.replace(/\/api$/, "");
    try {
      const unauthenticated = await globalThis.fetch(`${fixture.baseUrl}/chat/tasks/list?page=1&page_size=10`);
      expect(unauthenticated.status).toBe(401);
      const filtered = await globalThis.fetch(
        `${fixture.baseUrl}/chat/tasks/list?page=1&page_size=10&user_id=private-user&team_id=secret-team`,
        { headers: { Authorization: "Bearer fixture-demo-token" } },
      );
      expect(filtered.status).toBe(422);
      const duplicate = await globalThis.fetch(
        `${origin}/api/chat/tasks/list?page=1&page=2&page_size=10`,
        { headers: { Authorization: "Bearer fixture-demo-token" } },
      );
      expect(duplicate.status).toBe(422);
      expect(fixtureRequests(fixture.output()).map(({ pathname }) => pathname)).toEqual([
        "/api/chat/tasks/list?page=1&page_size=10",
        "/api/chat/tasks/list",
        "/api/chat/tasks/list",
      ]);
      for (const secret of ["private-user", "secret-team", "fixture-demo-token", "user_id", "team_id"]) {
        expect(fixture.output()).not.toContain(secret);
      }
    } finally {
      await fixture.stop();
    }
  });

  it.each(statusCases)("preserves real HTTP %s from the tasks endpoint", async (status) => {
    const fixture = await startFixture({ FIXTURE_TASKS_STATUS: String(status) });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.listMyTasks(1, signal())).rejects.toMatchObject({ kind: "http", status });
    } finally {
      await fixture.stop();
    }
  });

  it.each(["timeout", "body-timeout"] as const)("classifies a real tasks %s as timeout", async (mode) => {
    const fixture = await startFixture({ FIXTURE_TASKS_MODE: mode });
    const { services } = createServices(fixture.baseUrl, [], { timeoutMs: 60 });
    try {
      await expect(services.listMyTasks(1, signal())).rejects.toMatchObject({ kind: "timeout" });
    } finally {
      await fixture.stop();
    }
  });

  it.each(invalidResponseModes)("rejects a real tasks response with %s", async (mode) => {
    const fixture = await startFixture({ FIXTURE_TASKS_MODE: mode });
    const { services } = createServices(fixture.baseUrl);
    try {
      const pending = services.listMyTasks(1, signal());
      if (mode === "invalid-structure" || mode === "page-mismatch" || mode === "page-size-mismatch" || mode === "duplicate-id") {
        await expect(pending).rejects.toBeInstanceOf(InvalidResponseError);
      } else {
        await expect(pending).rejects.toMatchObject({ kind: "invalid-response" });
      }
    } finally {
      await fixture.stop();
    }
  });

  it("rejects invalid page numbers before making an HTTP request", async () => {
    const requests: RequestRecord[] = [];
    const { services } = createServices("http://127.0.0.1:1/api", requests);
    for (const page of [0, -1, 1.5, Number.NaN, Number.POSITIVE_INFINITY, Number.MAX_SAFE_INTEGER + 1]) {
      const pending = services.listMyTasks(page, signal());
      await expect(pending).rejects.toBeInstanceOf(ApiError);
    }
    expect(requests).toHaveLength(0);
  });

  it("classifies cancellation of an in-flight task-list request as aborted", async () => {
    const fixture = await startFixture({ FIXTURE_TASKS_DELAY_MS: "500" });
    const { services } = createServices(fixture.baseUrl);
    const controller = new AbortController();
    try {
      const pending = services.listMyTasks(1, controller.signal);
      await new Promise((resolve) => setTimeout(resolve, 50));
      controller.abort();
      await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    } finally {
      await fixture.stop();
    }
  });
});
