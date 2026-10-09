// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvalidResponseError } from "./contracts";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";
import { projectStoryboardFrames } from "../../features/chapters/storyboardProjection";

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
        child.once("close", () => resolve());
        setTimeout(resolve, 1_000);
      });
    },
  };
}

function createServices(baseUrl: string, options: { timeoutMs?: number; fetcher?: typeof fetch } = {}) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
  const services = createApiServices(baseUrl, {
    storage,
    fetcher: options.fetcher ?? globalThis.fetch,
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  });
  return { services, storage };
}

function recordedFetcher(records: Array<{
  method: string;
  pathname: string;
  chapterId: string | null;
  authorization: string | null;
}>): typeof fetch {
  return async (input, init) => {
    const url = input instanceof URL
      ? input
      : input instanceof Request
        ? new URL(input.url)
        : new URL(input);
    records.push({
      method: init?.method ?? (input instanceof Request ? input.method : "GET"),
      pathname: url.pathname,
      chapterId: url.searchParams.get("chapter_id"),
      authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
        .get("Authorization"),
    });
    return globalThis.fetch(input, init);
  };
}

const endpointCases = [
  {
    name: "chapters",
    env: "FIXTURE_CHAPTERS_STATUS",
    invoke: (services: ReturnType<typeof createApiServices>) =>
      services.listChapters("fixture-series-01", new AbortController().signal),
  },
  {
    name: "assets",
    env: "FIXTURE_ASSETS_STATUS",
    invoke: (services: ReturnType<typeof createApiServices>) =>
      services.listStoryboardAssets(
        "fixture-series-01",
        "fixture-series-01-chapter-02",
        new AbortController().signal,
      ),
  },
] as const;

const statusCases = [401, 403, 404, 422, 500] as const;
const malformedCases = [
  { mode: "invalid-json", expected: "invalid JSON" },
  { mode: "invalid-structure", expected: "invalid structure" },
] as const;

describe("chapter and storyboard API services over real HTTP", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses the expected five-route request sequence with Bearer auth and no secret logging", async () => {
    const fixture = await startFixture();
    const requests: Array<{
      method: string;
      pathname: string;
      chapterId: string | null;
      authorization: string | null;
    }> = [];
    const { services } = createServices(fixture.baseUrl, { fetcher: recordedFetcher(requests) });

    try {
      await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
      const restored = await services.restore(new AbortController().signal);
      const series = await services.listSeries(new AbortController().signal);
      const firstSeries = series[0];
      if (!firstSeries) {
        throw new Error("Fixture did not return its expected first series.");
      }
      const chapters = await services.listChapters(firstSeries.id, new AbortController().signal);
      const firstChapter = chapters[0];
      if (!firstChapter) {
        throw new Error("Fixture did not return its expected first chapter.");
      }
      const assets = await services.listStoryboardAssets(
        firstSeries.id,
        firstChapter.id,
        new AbortController().signal,
      );

      expect(restored?.id).toBe("demo-user");
      expect(series).toHaveLength(24);
      expect(chapters).toHaveLength(4);
      expect(chapters.map((chapter) => chapter.order)).toEqual([2, 1, 3, 4]);
      expect(chapters[2]?.content).toBeNull();
      expect(chapters[3]?.content).toEqual([]);
      expect(chapters[0]?.lock?.is_mine).toBe(true);
      expect(chapters[1]?.lock?.locked_by_username).toBe("周编剧");
      expect(assets.map((asset) => asset.frame_index)).toEqual([1, 2]);
      expect(assets.every((asset) => asset.series_id === firstSeries.id && asset.chapter_id === firstChapter.id)).toBe(true);
      expect(projectStoryboardFrames(firstChapter, assets, fixture.baseUrl).map((frame) => frame.referenceCount))
        .toEqual([2, 2]);

      expect(requests.map(({ method, pathname }) => [method, pathname])).toEqual([
        ["POST", "/api/auth/login"],
        ["GET", "/api/auth/me"],
        ["GET", "/api/series"],
        ["GET", "/api/series/fixture-series-01/chapters"],
        ["GET", "/api/series/fixture-series-01/storyboard-assets"],
      ]);
      expect(requests.at(0)?.authorization).toBeNull();
      expect(requests.slice(1).every(({ authorization }) => authorization === "Bearer fixture-demo-token")).toBe(true);
      expect(requests.at(4)?.chapterId).toBe(firstChapter.id);

      const fixtureRequests = fixture.output().split("\n")
        .filter((line) => line.startsWith("{"))
        .map((line) => JSON.parse(line) as { method: string; pathname: string; status: number });
      expect(fixtureRequests.map(({ method, pathname }) => [method, pathname])).toEqual([
        ["POST", "/api/auth/login"],
        ["GET", "/api/auth/me"],
        ["GET", "/api/series"],
        ["GET", "/api/series/fixture-series-01/chapters"],
        ["GET", `/api/series/fixture-series-01/storyboard-assets?chapter_id=${firstChapter.id}`],
      ]);
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
    } finally {
      await fixture.stop();
    }
  });

  it("encodes reserved series path characters and round-trips reserved chapter query characters", async () => {
    const fixture = await startFixture();
    const requests: Array<{
      method: string;
      pathname: string;
      chapterId: string | null;
      authorization: string | null;
    }> = [];
    const { services } = createServices(fixture.baseUrl, { fetcher: recordedFetcher(requests) });
    const specialSeriesId = "series /?#&雪";
    const specialChapterId = "chapter /?&雪#";

    try {
      await expect(services.listChapters(specialSeriesId, new AbortController().signal))
        .rejects.toMatchObject({ kind: "http", status: 404 });
      await expect(services.listStoryboardAssets(
        "fixture-series-01",
        specialChapterId,
        new AbortController().signal,
      )).resolves.toEqual([]);

      expect(requests.at(0)?.pathname).toBe(
        "/api/series/series%20%2F%3F%23%26%E9%9B%AA/chapters",
      );
      expect(requests.at(1)?.pathname).toBe("/api/series/fixture-series-01/storyboard-assets");
      expect(requests.at(1)?.chapterId).toBe(specialChapterId);
      const loggedAssetRequest = fixture.output().split("\n")
        .filter((line) => line.startsWith("{"))
        .map((line) => JSON.parse(line) as { pathname: string })
        .find(({ pathname }) => pathname.includes("storyboard-assets"));
      expect(loggedAssetRequest?.pathname).toBe(
        "/api/series/fixture-series-01/storyboard-assets?chapter_id=chapter%20%2F%3F%26%E9%9B%AA%23",
      );

      const initialRequestCount = requests.length;
      for (const invalidId of ["", ".", ".."] as const) {
        await expect(services.listChapters(invalidId, new AbortController().signal))
          .rejects.toMatchObject({ kind: "invalid-response" });
        await expect(services.listStoryboardAssets(
          "fixture-series-01",
          invalidId,
          new AbortController().signal,
        )).rejects.toMatchObject({ kind: "invalid-response" });
      }
      await expect(services.listStoryboardAssets(
        "..",
        "fixture-series-01-chapter-02",
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "invalid-response" });
      expect(requests).toHaveLength(initialRequestCount);
    } finally {
      await fixture.stop();
    }
  });

  it.each(endpointCases.flatMap((endpoint) => statusCases.map((status) => ({ endpoint, status }))))(
    "preserves HTTP $status from the real $endpoint endpoint",
    async ({ endpoint, status }) => {
      const fixture = await startFixture({ [`${endpoint.env}`]: String(status) });
      const { services, storage } = createServices(fixture.baseUrl);
      try {
        await expect(endpoint.invoke(services)).rejects.toMatchObject({ kind: "http", status });
        expect(storage.getItem(API_TOKEN_KEY)).toBe("fixture-demo-token");
      } finally {
        await fixture.stop();
      }
    },
  );

  it.each(endpointCases.flatMap((endpoint) => ["timeout", "body-timeout"].map((mode) => ({ endpoint, mode }))))(
    "classifies a real $endpoint $mode as timeout",
    async ({ endpoint, mode }) => {
      const fixture = await startFixture({ [`${endpoint.env.replace("_STATUS", "_MODE")}`]: mode });
      const { services } = createServices(fixture.baseUrl, { timeoutMs: 50 });
      try {
        await expect(endpoint.invoke(services)).rejects.toMatchObject({ kind: "timeout" });
      } finally {
        await fixture.stop();
      }
    },
  );

  it.each(endpointCases.flatMap((endpoint) => malformedCases.map(({ mode, expected }) => ({ endpoint, mode, expected }))))(
    "rejects a real $endpoint response with $expected",
    async ({ endpoint, mode }) => {
      const fixture = await startFixture({ [`${endpoint.env.replace("_STATUS", "_MODE")}`]: mode });
      const { services } = createServices(fixture.baseUrl);
      try {
        const pending = endpoint.invoke(services);
        if (mode === "invalid-json") {
          await expect(pending).rejects.toMatchObject({ kind: "invalid-response" });
        } else {
          await expect(pending).rejects.toBeInstanceOf(InvalidResponseError);
        }
      } finally {
        await fixture.stop();
      }
    },
  );

  it("treats successful empty chapter and asset lists as valid empty arrays", async () => {
    const fixture = await startFixture({
      FIXTURE_CHAPTERS_EMPTY: "true",
      FIXTURE_ASSETS_EMPTY: "true",
    });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.listChapters("fixture-series-01", new AbortController().signal))
        .resolves.toEqual([]);
      await expect(services.listStoryboardAssets(
        "fixture-series-01",
        "fixture-series-01-chapter-02",
        new AbortController().signal,
      )).resolves.toEqual([]);
    } finally {
      await fixture.stop();
    }
  });

  it("classifies cancellation of a real in-flight chapter request as aborted", async () => {
    const fixture = await startFixture({ FIXTURE_CHAPTERS_DELAY_MS: "500" });
    const requests: Array<{
      method: string;
      pathname: string;
      chapterId: string | null;
      authorization: string | null;
    }> = [];
    const { services } = createServices(fixture.baseUrl, { fetcher: recordedFetcher(requests) });
    const controller = new AbortController();
    try {
      const pending = services.listChapters("fixture-series-01", controller.signal);
      expect(requests).toHaveLength(1);
      await new Promise((resolve) => setTimeout(resolve, 50));
      controller.abort();
      await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    } finally {
      await fixture.stop();
    }
  });
});
