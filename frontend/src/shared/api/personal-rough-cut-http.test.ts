// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError, type StoryboardAsset } from "./contracts";
import type { PersonalRoughCutSnapshot } from "./personalRoughCut";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));
const defaultChapterId = "fixture-series-01-chapter-02";
const legacyIdReason = "缺少唯一有效的稳定分镜 ID，暂不可编排";
const missingVideoReason = "当前分镜没有可用视频";
const videoUrlPattern = /\.(mp4|webm|mov|avi|mkv|m4v)(?:\?|$)/i;

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
  options: { timeoutMs?: number; token?: string | null } = {},
) {
  const storage = new MemoryStorage();
  if (options.token !== null) {
    storage.setItem(API_TOKEN_KEY, options.token ?? "fixture-demo-token");
  }
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

function parseFixtureRequests(output: string): Array<{ method: string; pathname: string; status: number }> {
  return output.split("\n")
    .filter((line) => line.startsWith("{"))
    .map((line) => JSON.parse(line) as { method: string; pathname: string; status: number });
}

function sourceValue(frame: unknown, key: string): unknown {
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) {
    return undefined;
  }
  return (frame as Record<string, unknown>)[key];
}

function sourceAssetCandidate(frame: unknown): string | null {
  const storyboard = sourceValue(frame, "storyboard");
  if (!Array.isArray(storyboard) || typeof storyboard[0] !== "string" || storyboard[0].length === 0) {
    return null;
  }
  return storyboard[0];
}

const statusCases = [401, 403, 404, 422, 500] as const;

describe("personal rough-cut API over real HTTP", () => {
  it("uses the twelfth allowed route with current Bearer auth and does not request preview media", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests, { token: null });

    try {
      const signedIn = await services.login({ username: "demo", password: "demo123" }, signal());
      const restored = await services.restore(signal());
      const series = await services.listSeries(signal());
      const teams = await services.listMyTeams(signal());
      const chapters = await services.listChapters("fixture-series-01", signal());
      const chapter = chapters.find(({ id }) => id === defaultChapterId);
      if (!chapter) {
        throw new Error("Fixture did not return the default chapter.");
      }
      const sourceAssets: StoryboardAsset[] = await services.listStoryboardAssets(
        "fixture-series-01",
        chapter.id,
        signal(),
      );
      await services.listCharacters("fixture-series-01", signal());
      await services.listScenes("fixture-series-01", signal());
      await services.listProps("fixture-series-01", signal());
      await services.getPersonalProductionNotes(chapter.id, signal());
      await services.listMyTasks(1, signal());
      const snapshot: PersonalRoughCutSnapshot = await services.getPersonalRoughCut(chapter.id, signal());

      expect(signedIn.id).toBe("demo-user");
      expect(restored?.id).toBe("demo-user");
      expect(series).toHaveLength(24);
      expect(teams.length).toBeGreaterThan(0);
      expect(snapshot).toMatchObject({
        chapter_id: chapter.id,
        revision: 6,
        saved: true,
      });
      expect(Object.keys(snapshot).sort()).toEqual([
        "chapter_id",
        "frames",
        "removed_asset_ids",
        "revision",
        "saved",
      ]);
      expect(snapshot.frames.length).toBeGreaterThan(0);
      expect(snapshot.frames.map(({ frame_index }) => frame_index)).toEqual(
        snapshot.frames.map(({ frame_index }) => frame_index).slice().sort((left, right) => right - left),
      );
      for (const frame of snapshot.frames) {
        expect(Object.keys(frame).sort()).toEqual([
          "asset_id",
          "frame_index",
          "included",
          "missing_reason",
          "pending",
          "preview_url",
          "text",
        ]);
      }
      const chapterFrames = chapter.content ?? [];
      const candidates = chapterFrames.map(sourceAssetCandidate);
      const candidateCounts = new Map<string, number>();
      for (const candidate of candidates) {
        if (candidate !== null) {
          candidateCounts.set(candidate, (candidateCounts.get(candidate) ?? 0) + 1);
        }
      }
      for (const roughCutFrame of snapshot.frames) {
        const sourceFrame = chapterFrames[roughCutFrame.frame_index];
        const candidate = sourceAssetCandidate(sourceFrame);
        const hasUniqueAsset = candidate !== null
          && candidateCounts.get(candidate) === 1
          && sourceAssets.some((asset) => asset.id === candidate && asset.chapter_id === chapter.id);
        const expectedAssetId = hasUniqueAsset ? candidate : "";
        expect(roughCutFrame.asset_id).toBe(expectedAssetId);

        const rawPreview = sourceValue(sourceFrame, "preview");
        const expectedPreview = typeof rawPreview === "string" && videoUrlPattern.test(rawPreview)
          ? rawPreview
          : null;
        expect(roughCutFrame.preview_url).toBe(expectedPreview);

        const rawText = sourceValue(sourceFrame, "text") || sourceValue(sourceFrame, "original_text") || "";
        expect(roughCutFrame.text).toBe(typeof rawText === "string" ? rawText : String(rawText));

        const expectedReasons = [
          ...(expectedAssetId === "" ? [legacyIdReason] : []),
          ...(expectedPreview === null ? [missingVideoReason] : []),
        ];
        expect(roughCutFrame.missing_reason).toBe(
          expectedReasons.length > 0 ? expectedReasons.join("；") : null,
        );
        if (expectedAssetId === "") {
          expect(roughCutFrame.included).toBe(false);
          expect(roughCutFrame.pending).toBe(false);
        }
      }
      expect(snapshot.removed_asset_ids).toHaveLength(2);
      expect(snapshot.removed_asset_ids[0]).toBe(snapshot.removed_asset_ids[1]);

      const expectedTargets = [
        ["POST", "/api/auth/login"],
        ["GET", "/api/auth/me"],
        ["GET", "/api/series"],
        ["GET", "/api/teams/my"],
        ["GET", "/api/series/fixture-series-01/chapters"],
        ["GET", `/api/series/fixture-series-01/storyboard-assets?chapter_id=${chapter.id}`],
        ["GET", "/api/series/fixture-series-01/characters"],
        ["GET", "/api/series/fixture-series-01/scenes"],
        ["GET", "/api/series/fixture-series-01/props"],
        ["GET", `/api/chapters/${chapter.id}/personal-production-notes`],
        ["GET", "/api/chat/tasks/list?page=1&page_size=10"],
        ["GET", `/api/chapters/${chapter.id}/rough-cut`],
      ];
      expect(requests.map(({ method, target }) => [method, target])).toEqual(expectedTargets);
      expect(requests[0]?.authorization).toBeNull();
      expect(requests.slice(1).every(({ authorization }) => authorization === "Bearer fixture-demo-token"))
        .toBe(true);
      expect(requests.some(({ target }) => target.startsWith("/media/"))).toBe(false);

      const fixtureRequests = parseFixtureRequests(fixture.output());
      expect(fixtureRequests.map(({ method, pathname }) => [method, pathname])).toEqual(expectedTargets);
      expect(fixtureRequests.every(({ method, pathname }) => (
        method === "GET" || (method === "POST" && pathname === "/api/auth/login")
      ))).toBe(true);
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
      expect(fixture.output()).not.toContain("demo-two123");
      expect(fixture.output()).not.toContain("fixture-demo-token-02");
      for (const frame of snapshot.frames) {
        if (frame.asset_id !== "") {
          expect(fixture.output()).not.toContain(frame.asset_id);
        }
        if (frame.text !== "") {
          expect(fixture.output()).not.toContain(frame.text);
        }
        if (frame.preview_url !== null) {
          expect(fixture.output()).not.toContain(frame.preview_url);
        }
      }
    } finally {
      await fixture.stop();
    }
  });

  it("keeps two synthetic users' same-chapter drafts private", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const first = createServices(fixture.baseUrl, requests, { token: null });
    const second = createServices(fixture.baseUrl, requests, { token: null });

    try {
      const firstUser = await first.services.login({ username: "demo", password: "demo123" }, signal());
      const firstSnapshot = await first.services.getPersonalRoughCut(defaultChapterId, signal());
      const secondUser = await second.services.login({ username: "demo-two", password: "demo-two123" }, signal());
      const secondSnapshot = await second.services.getPersonalRoughCut(defaultChapterId, signal());

      expect(firstUser.id).toBe("demo-user");
      expect(secondUser.id).toBe("demo-user-02");
      expect(firstSnapshot.chapter_id).toBe(secondSnapshot.chapter_id);
      expect(firstSnapshot.revision).not.toBe(secondSnapshot.revision);
      expect(firstSnapshot.frames.map(({ frame_index }) => frame_index))
        .not.toEqual(secondSnapshot.frames.map(({ frame_index }) => frame_index));
      expect(firstSnapshot.frames.map(({ included }) => included))
        .not.toEqual(secondSnapshot.frames.map(({ included }) => included));
      expect(firstSnapshot.frames.some(({ pending }) => pending)).toBe(false);
      expect(secondSnapshot.frames.some(({ pending }) => pending)).toBe(true);
      expect(firstSnapshot.removed_asset_ids).toHaveLength(2);
      expect(secondSnapshot.removed_asset_ids).toHaveLength(1);
      expect(requests.filter(({ target }) => target.endsWith(`/chapters/${defaultChapterId}/rough-cut`))
        .map(({ authorization }) => authorization)).toEqual([
        "Bearer fixture-demo-token",
        "Bearer fixture-demo-token-02",
      ]);
      expect(requests.some(({ target }) => target.includes("user_id="))).toBe(false);
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("demo-two123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
    } finally {
      await fixture.stop();
    }
  });

  it.each([
    { mode: "unsaved", revision: 0, saved: false },
    { mode: "unsaved-no-video", revision: 0, saved: false },
    { mode: "empty", revision: 0, saved: false },
    { mode: "removed", revision: 5, saved: true },
    { mode: "legacy", revision: 9, saved: true },
    { mode: "pending", revision: 7, saved: true },
  ] as const)("preserves the $mode rough-cut projection", async ({ mode, revision, saved }) => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_MODE: mode });
    const { services } = createServices(fixture.baseUrl);
    try {
      const snapshot = await services.getPersonalRoughCut(defaultChapterId, signal());
      expect(snapshot).toMatchObject({ chapter_id: defaultChapterId, revision, saved });

      if (mode === "empty") {
        expect(snapshot.frames).toEqual([]);
        expect(snapshot.removed_asset_ids).toEqual([]);
      } else if (mode === "unsaved-no-video") {
        const noVideoFrame = snapshot.frames.find(({ asset_id, preview_url }) => (
          asset_id !== "" && preview_url === null
        ));
        expect(noVideoFrame).toMatchObject({
          included: true,
          pending: false,
          preview_url: null,
          missing_reason: missingVideoReason,
        });
      } else if (mode === "legacy") {
        expect(snapshot.frames).toHaveLength(501);
        const emptyIdFrames = snapshot.frames.filter(({ asset_id }) => asset_id === "");
        expect(emptyIdFrames.length).toBeGreaterThan(0);
        expect(emptyIdFrames.every(({ included, pending }) => !included && !pending)).toBe(true);
        expect(snapshot.removed_asset_ids).toHaveLength(2);
        expect(snapshot.removed_asset_ids[0]).toBe(snapshot.removed_asset_ids[1]);
      } else if (mode === "removed") {
        expect(snapshot.removed_asset_ids).toHaveLength(2);
        expect(snapshot.removed_asset_ids[0]).toBe(snapshot.removed_asset_ids[1]);
        expect(snapshot.frames.every(({ asset_id, included, pending }) => (
          !included && pending === (asset_id !== "")
        ))).toBe(true);
      } else if (mode === "pending") {
        const pendingFrames = snapshot.frames.filter(({ pending }) => pending);
        expect(pendingFrames.length).toBeGreaterThan(0);
        expect(pendingFrames.every(({ asset_id }) => asset_id !== "")).toBe(true);
      } else {
        expect(snapshot.frames.length).toBeGreaterThan(0);
        expect(snapshot.frames.every(({ asset_id, included, pending }) => (
          included === (asset_id !== "") && !pending
        ))).toBe(true);
      }
    } finally {
      await fixture.stop();
    }
  });

  it.each(statusCases)("preserves a real HTTP %s rough-cut error", async (status) => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_STATUS: String(status) });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.getPersonalRoughCut(defaultChapterId, signal()))
        .rejects.toMatchObject({ kind: "http", status });
    } finally {
      await fixture.stop();
    }
  });

  it.each([
    { forbidden: "membership", detail: "会员资格" },
    { forbidden: "ordinary", detail: "没有权限" },
  ] as const)("preserves the $forbidden 403 detail", async ({ forbidden, detail }) => {
    const fixture = await startFixture({
      FIXTURE_ROUGH_CUT_STATUS: "403",
      FIXTURE_ROUGH_CUT_FORBIDDEN: forbidden,
    });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.getPersonalRoughCut(defaultChapterId, signal()))
        .rejects.toMatchObject({ kind: "http", status: 403, detail: expect.stringContaining(detail) });
    } finally {
      await fixture.stop();
    }
  });

  it("requires Bearer auth, rejects user selection, and rejects unknown or inaccessible chapters", async () => {
    const fixture = await startFixture();
    const origin = fixture.baseUrl.replace(/\/api$/, "");
    try {
      const withoutBearer = await globalThis.fetch(`${fixture.baseUrl}/chapters/${defaultChapterId}/rough-cut`);
      expect(withoutBearer.status).toBe(401);

      const selectedUser = await globalThis.fetch(
        `${origin}/api/chapters/${defaultChapterId}/rough-cut?user_id=demo-user-02`,
        { headers: { Authorization: "Bearer fixture-demo-token" } },
      );
      expect(selectedUser.status).toBe(422);

      const { services } = createServices(fixture.baseUrl);
      await expect(services.getPersonalRoughCut("does-not-exist", signal()))
        .rejects.toMatchObject({ kind: "http", status: 404 });
      await expect(services.getPersonalRoughCut("fixture-series-15-chapter-02", signal()))
        .rejects.toMatchObject({ kind: "http", status: 403 });

      expect(parseFixtureRequests(fixture.output()).map(({ method, pathname }) => [method, pathname]))
        .toEqual([
          ["GET", `/api/chapters/${defaultChapterId}/rough-cut`],
          ["GET", `/api/chapters/${defaultChapterId}/rough-cut`],
          ["GET", "/api/chapters/does-not-exist/rough-cut"],
          ["GET", "/api/chapters/fixture-series-15-chapter-02/rough-cut"],
        ]);
    } finally {
      await fixture.stop();
    }
  });

  it("encodes the chapter ID as one path segment and rejects empty or dot IDs before fetch", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);
    const specialChapterId = "chapter /?&雪#";

    try {
      await expect(services.getPersonalRoughCut(specialChapterId, signal()))
        .rejects.toMatchObject({ kind: "http", status: 404 });
      expect(requests[0]?.target).toBe(
        "/api/chapters/chapter%20%2F%3F%26%E9%9B%AA%23/rough-cut",
      );
      expect(requests[0]?.target.includes("?")).toBe(false);

      const initialRequestCount = requests.length;
      for (const invalidChapterId of ["", ".", ".."] as const) {
        await expect(services.getPersonalRoughCut(invalidChapterId, signal()))
          .rejects.toMatchObject({ kind: "invalid-response" });
      }
      expect(requests).toHaveLength(initialRequestCount);
    } finally {
      await fixture.stop();
    }
  });

  it.each(["timeout", "body-timeout"] as const)("classifies a real %s response as timeout", async (mode) => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_MODE: mode });
    const { services } = createServices(fixture.baseUrl, [], { timeoutMs: 60 });
    try {
      await expect(services.getPersonalRoughCut(defaultChapterId, signal()))
        .rejects.toMatchObject({ kind: "timeout" });
    } finally {
      await fixture.stop();
    }
  });

  it.each([
    { mode: "invalid-json", error: "invalid-response" },
    { mode: "invalid-shape", error: "invalid-structure" },
  ] as const)("rejects a real $mode response", async ({ mode, error }) => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_MODE: mode });
    const { services } = createServices(fixture.baseUrl);
    try {
      const response = services.getPersonalRoughCut(defaultChapterId, signal());
      if (error === "invalid-structure") {
        await expect(response).rejects.toBeInstanceOf(InvalidResponseError);
      } else {
        await expect(response).rejects.toMatchObject({ kind: error });
      }
    } finally {
      await fixture.stop();
    }
  });

  it("classifies cancellation of a delayed real HTTP request as aborted", async () => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_DELAY_MS: "500" });
    const { services } = createServices(fixture.baseUrl);
    const controller = new AbortController();
    try {
      const pending = services.getPersonalRoughCut(defaultChapterId, controller.signal);
      await new Promise((resolve) => setTimeout(resolve, 50));
      controller.abort();
      await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    } finally {
      await fixture.stop();
    }
  });
});
