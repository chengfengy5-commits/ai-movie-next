// @vitest-environment node
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError, type Chapter, type StoryboardAsset } from "./contracts";
import type { PersonalProductionSnapshot } from "./personalProductionNotes";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));
const defaultChapterId = "fixture-series-01-chapter-02";

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

function rawSha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function frameAssetId(frame: unknown): string | null {
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) {
    return null;
  }
  const storyboard = (frame as Record<string, unknown>).storyboard;
  return Array.isArray(storyboard) && typeof storyboard[0] === "string" ? storyboard[0] : null;
}

function framePreview(frame: unknown): string | null {
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) {
    return null;
  }
  const preview = (frame as Record<string, unknown>).preview;
  return typeof preview === "string" ? preview : null;
}

function parseFixtureRequests(output: string): Array<{ method: string; pathname: string; status: number }> {
  return output.split("\n")
    .filter((line) => line.startsWith("{"))
    .map((line) => JSON.parse(line) as { method: string; pathname: string; status: number });
}

const statusCases = [401, 403, 404, 422, 500] as const;

describe("personal production notes API over real HTTP", () => {
  it("uses the ninth allowed route with Bearer auth and preserves the complete snapshot", async () => {
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
      const chapters: Chapter[] = await services.listChapters(firstSeries.id, signal());
      const currentChapter = chapters[0];
      if (!currentChapter) {
        throw new Error("Fixture did not return its first chapter.");
      }
      const assets: StoryboardAsset[] = await services.listStoryboardAssets(firstSeries.id, currentChapter.id, signal());
      await services.listCharacters(firstSeries.id, signal());
      await services.listScenes(firstSeries.id, signal());
      await services.listProps(firstSeries.id, signal());
      const snapshot: PersonalProductionSnapshot = await services.getPersonalProductionNotes(
        currentChapter.id,
        signal(),
      );

      expect(signedIn.id).toBe("demo-user");
      expect(restored?.id).toBe("demo-user");
      expect(snapshot).toMatchObject({
        chapter_id: currentChapter.id,
        revision: 7,
        media_state: "ready",
        resume_frame_id: frameAssetId(currentChapter.content?.[0]),
      });
      expect(Object.keys(snapshot).sort()).toEqual([
        "chapter_id",
        "frame_notes",
        "frames",
        "media_state",
        "resume_frame_id",
        "revision",
      ]);
      expect(snapshot.frames).toHaveLength(2);
      expect(snapshot.frames.map(({ frame_index }) => frame_index)).toEqual([0, 1]);
      expect(snapshot.frames.map(({ storyboard_asset_id }) => storyboard_asset_id)).toEqual(
        currentChapter.content?.map(frameAssetId) ?? [],
      );
      expect(snapshot.frames.map(({ media_revision }) => media_revision)).toEqual([3, 4]);
      expect(snapshot.frames.every(({ source_valid, invalid_reason }) => source_valid && invalid_reason === null))
        .toBe(true);
      for (const serverFrame of snapshot.frames) {
        const imageUrl = assets.find(({ id }) => id === serverFrame.storyboard_asset_id)?.image_url;
        const previewUrl = framePreview(currentChapter.content?.[serverFrame.frame_index]);
        if (typeof imageUrl !== "string" || previewUrl === null) {
          throw new Error("Fixture did not provide the captured local media identities.");
        }
        expect(serverFrame.asset_image_digest).toBe(rawSha256(imageUrl));
        expect(serverFrame.preview_digest).toBe(rawSha256(previewUrl));
      }

      expect(snapshot.frame_notes).toBeInstanceOf(Map);
      const firstFrameId = snapshot.frames[0]?.storyboard_asset_id;
      const secondFrameId = snapshot.frames[1]?.storyboard_asset_id;
      expect(typeof firstFrameId).toBe("string");
      expect(typeof secondFrameId).toBe("string");
      expect(snapshot.frame_notes.get(firstFrameId as string)).toMatchObject({
        status: "approved",
        approved_media_revision: 3,
        needs_reconfirmation: false,
      });
      expect(snapshot.frame_notes.get(secondFrameId as string)).toMatchObject({
        status: "approved",
        approved_media_revision: 2,
      });
      expect(snapshot.frame_notes.has(`${currentChapter.id}-orphan-shot`)).toBe(true);
      expect(snapshot.frame_notes.has("__proto__")).toBe(true);

      const expectedTargets = [
        ["POST", "/api/auth/login"],
        ["GET", "/api/auth/me"],
        ["GET", "/api/series"],
        ["GET", "/api/series/fixture-series-01/chapters"],
        ["GET", `/api/series/fixture-series-01/storyboard-assets?chapter_id=${currentChapter.id}`],
        ["GET", "/api/series/fixture-series-01/characters"],
        ["GET", "/api/series/fixture-series-01/scenes"],
        ["GET", "/api/series/fixture-series-01/props"],
        ["GET", `/api/chapters/${currentChapter.id}/personal-production-notes`],
      ];
      expect(requests.map(({ method, target }) => [method, target])).toEqual(expectedTargets);
      expect(requests[0]?.authorization).toBeNull();
      expect(requests.slice(1).every(({ authorization }) => authorization === "Bearer fixture-demo-token"))
        .toBe(true);

      const fixtureRequests = parseFixtureRequests(fixture.output());
      expect(fixtureRequests.map(({ method, pathname }) => [method, pathname])).toEqual(expectedTargets);
      expect(fixtureRequests.every(({ method, pathname }) => (
        method === "GET" || (method === "POST" && pathname === "/api/auth/login")
      ))).toBe(true);
      expect(fixture.output()).not.toContain("demo123");
      expect(fixture.output()).not.toContain("fixture-demo-token");
      expect(fixture.output()).not.toContain("demo-two123");
      expect(fixture.output()).not.toContain("fixture-demo-token-02");
    } finally {
      await fixture.stop();
    }
  });

  it("keeps two synthetic users' notes private for the same chapter", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const first = createServices(fixture.baseUrl, requests);
    const second = createServices(fixture.baseUrl, requests);

    try {
      const firstUser = await first.services.login({ username: "demo", password: "demo123" }, signal());
      const firstSnapshot = await first.services.getPersonalProductionNotes(defaultChapterId, signal());
      const secondUser = await second.services.login({ username: "demo-two", password: "demo-two123" }, signal());
      const secondSnapshot = await second.services.getPersonalProductionNotes(defaultChapterId, signal());

      expect(firstUser.id).toBe("demo-user");
      expect(secondUser.id).toBe("demo-user-02");
      expect(firstSnapshot.chapter_id).toBe(secondSnapshot.chapter_id);
      expect(firstSnapshot.revision).not.toBe(secondSnapshot.revision);
      const sharedFrameId = firstSnapshot.frames[0]?.storyboard_asset_id;
      if (!sharedFrameId) {
        throw new Error("Fixture did not return a stable storyboard asset id.");
      }
      expect(firstSnapshot.frame_notes.get(sharedFrameId)).toMatchObject({
        note: "已核对该镜头的构图与对白衔接。",
      });
      expect(secondSnapshot.frame_notes.get(sharedFrameId)).toMatchObject({
        note: "仅属于第二演示账号的镜头备注。",
      });
      expect(firstSnapshot.frame_notes.get(sharedFrameId)).not.toEqual(secondSnapshot.frame_notes.get(sharedFrameId));
      expect(requests.filter(({ target }) => target.endsWith("personal-production-notes"))
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

  it("encodes the chapter id as one path segment and rejects empty or dot ids before fetch", async () => {
    const fixture = await startFixture();
    const requests: RequestRecord[] = [];
    const { services } = createServices(fixture.baseUrl, requests);
    const specialChapterId = "chapter /?&雪#";

    try {
      await expect(services.getPersonalProductionNotes(specialChapterId, signal()))
        .rejects.toMatchObject({ kind: "http", status: 404 });
      expect(requests[0]?.target).toBe(
        "/api/chapters/chapter%20%2F%3F%26%E9%9B%AA%23/personal-production-notes",
      );

      const initialRequestCount = requests.length;
      for (const invalidChapterId of ["", ".", ".."] as const) {
        await expect(services.getPersonalProductionNotes(invalidChapterId, signal()))
          .rejects.toMatchObject({ kind: "invalid-response" });
      }
      expect(requests).toHaveLength(initialRequestCount);
    } finally {
      await fixture.stop();
    }
  });

  it("rejects an attempted user query and requires a valid Bearer token", async () => {
    const fixture = await startFixture();
    const origin = fixture.baseUrl.replace(/\/api$/, "");
    try {
      const withoutBearer = await globalThis.fetch(
        `${fixture.baseUrl}/chapters/${defaultChapterId}/personal-production-notes`,
      );
      expect(withoutBearer.status).toBe(401);

      const selectedUser = await globalThis.fetch(
        `${origin}/api/chapters/${defaultChapterId}/personal-production-notes?user_id=demo-user-02`,
        { headers: { Authorization: "Bearer fixture-demo-token" } },
      );
      expect(selectedUser.status).toBe(422);
      expect(parseFixtureRequests(fixture.output()).map(({ pathname }) => pathname)).toEqual([
        `/api/chapters/${defaultChapterId}/personal-production-notes`,
        `/api/chapters/${defaultChapterId}/personal-production-notes`,
      ]);
    } finally {
      await fixture.stop();
    }
  });

  it.each(statusCases)("preserves real HTTP %s from the notes endpoint", async (status) => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_STATUS: String(status) });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.getPersonalProductionNotes(defaultChapterId, signal()))
        .rejects.toMatchObject({ kind: "http", status });
    } finally {
      await fixture.stop();
    }
  });

  it("rejects an unknown chapter and a chapter denied to the current user", async () => {
    const fixture = await startFixture();
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.getPersonalProductionNotes("does-not-exist", signal()))
        .rejects.toMatchObject({ kind: "http", status: 404 });
      await expect(services.getPersonalProductionNotes("fixture-series-15-chapter-02", signal()))
        .rejects.toMatchObject({ kind: "http", status: 403 });
    } finally {
      await fixture.stop();
    }
  });

  it.each(["timeout", "body-timeout"] as const)("classifies a real notes %s as timeout", async (mode) => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_MODE: mode });
    const { services } = createServices(fixture.baseUrl, [], { timeoutMs: 60 });
    try {
      await expect(services.getPersonalProductionNotes(defaultChapterId, signal()))
        .rejects.toMatchObject({ kind: "timeout" });
    } finally {
      await fixture.stop();
    }
  });

  it.each([
    { mode: "invalid-json", expected: "invalid-response" },
    { mode: "invalid-structure", expected: "invalid-response" },
  ] as const)("rejects a real notes response with $mode", async ({ mode, expected }) => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_MODE: mode });
    const { services } = createServices(fixture.baseUrl);
    try {
      const response = services.getPersonalProductionNotes(defaultChapterId, signal());
      if (mode === "invalid-structure") {
        await expect(response).rejects.toBeInstanceOf(InvalidResponseError);
      } else {
        await expect(response).rejects.toMatchObject({ kind: expected });
      }
    } finally {
      await fixture.stop();
    }
  });

  it("provides a valid mismatch snapshot for browser verification", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_MODE: "mismatch" });
    const { services } = createServices(fixture.baseUrl);
    try {
      const snapshot = await services.getPersonalProductionNotes(defaultChapterId, signal());
      const firstDigest = snapshot.frames[0]?.asset_image_digest;
      expect(firstDigest).toMatch(/^[0-9a-f]{64}$/);
      expect(firstDigest).not.toBe(rawSha256(`/media/${defaultChapterId}-shot-b.png`));
    } finally {
      await fixture.stop();
    }
  });

  it("keeps default media snapshots consistent and offers a coherent double-empty mode", async () => {
    const fixture = await startFixture();
    const { services } = createServices(fixture.baseUrl);
    try {
      const firstChapter = "fixture-series-01-chapter-01";
      const chapters: Chapter[] = await services.listChapters("fixture-series-01", signal());
      const defaultChapter = chapters.find(({ id }) => id === firstChapter);
      const defaultAssets: StoryboardAsset[] = await services.listStoryboardAssets(
        "fixture-series-01",
        firstChapter,
        signal(),
      );
      const defaultSnapshot: PersonalProductionSnapshot = await services.getPersonalProductionNotes(
        firstChapter,
        signal(),
      );
      const defaultPreview = framePreview(defaultChapter?.content?.[0]);
      const defaultImage = defaultAssets[0]?.image_url;
      if (defaultPreview === null || typeof defaultImage !== "string") {
        throw new Error("Default chapter-one media fixture must include its original and preview.");
      }
      expect(defaultSnapshot.frames[0]?.asset_image_digest)
        .toBe(rawSha256(defaultImage));
      expect(defaultSnapshot.frames[0]?.preview_digest)
        .toBe(rawSha256(defaultPreview));
      const defaultFrameId = defaultSnapshot.frames[0]?.storyboard_asset_id;
      expect(typeof defaultFrameId).toBe("string");
      expect(defaultSnapshot.frame_notes.get(defaultFrameId as string)).toMatchObject({
        status: "approved",
        approved_media_revision: 4,
        needs_reconfirmation: true,
      });

      const unreadable = await services.getPersonalProductionNotes("fixture-series-01-chapter-03", signal());
      expect(unreadable).toMatchObject({ revision: 2, media_state: "unreadable", frames: [] });
      expect(unreadable.frame_notes.size).toBe(1);

      const empty = await services.getPersonalProductionNotes("fixture-series-01-chapter-04", signal());
      expect(empty).toMatchObject({ revision: 0, media_state: "empty", frames: [], resume_frame_id: null });
      expect(empty.frame_notes.size).toBe(0);
    } finally {
      await fixture.stop();
    }

    const doubleEmptyFixture = await startFixture({ FIXTURE_PERSONAL_NOTES_MODE: "double-empty" });
    const { services: doubleEmptyServices } = createServices(doubleEmptyFixture.baseUrl);
    try {
      const firstChapter = "fixture-series-01-chapter-01";
      const chapters: Chapter[] = await doubleEmptyServices.listChapters("fixture-series-01", signal());
      const chapter = chapters.find(({ id }) => id === firstChapter);
      const assets: StoryboardAsset[] = await doubleEmptyServices.listStoryboardAssets(
        "fixture-series-01",
        firstChapter,
        signal(),
      );
      const snapshot: PersonalProductionSnapshot = await doubleEmptyServices.getPersonalProductionNotes(
        firstChapter,
        signal(),
      );
      expect(framePreview(chapter?.content?.[0])).toBeNull();
      expect(assets[0]?.image_url).toBeNull();
      expect(snapshot.frames).toHaveLength(1);
      expect(snapshot.frames[0]).toMatchObject({
        source_valid: true,
        asset_image_digest: null,
        preview_digest: null,
        invalid_reason: null,
      });
      const doubleEmptyId = snapshot.frames[0]?.storyboard_asset_id;
      expect(typeof doubleEmptyId).toBe("string");
      expect(snapshot.frame_notes.get(doubleEmptyId as string)).toMatchObject({
        status: "approved",
        needs_reconfirmation: true,
      });
    } finally {
      await doubleEmptyFixture.stop();
    }
  });

  it("classifies cancellation of a real in-flight notes request as aborted", async () => {
    const fixture = await startFixture({ FIXTURE_PERSONAL_NOTES_DELAY_MS: "500" });
    const { services } = createServices(fixture.baseUrl);
    const controller = new AbortController();
    try {
      const pending = services.getPersonalProductionNotes(defaultChapterId, controller.signal);
      await new Promise((resolve) => setTimeout(resolve, 50));
      controller.abort();
      await expect(pending).rejects.toMatchObject({ kind: "aborted" });
    } finally {
      await fixture.stop();
    }
  });
});
