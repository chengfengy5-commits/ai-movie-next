// @vitest-environment node
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { InvalidResponseError } from "./contracts";
import type { PersonalRoughCutUpdate } from "./personalRoughCut";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";

const fixturePath = fileURLToPath(new URL("../../../../tools/api-fixture/server.mjs", import.meta.url));
const seriesId = "fixture-series-01";
const chapterId = seriesId + "-chapter-02";
const emptyChapterId = seriesId + "-chapter-04";
const nullContentChapterId = seriesId + "-chapter-03";

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
      const timer = setTimeout(() => reject(new Error("Fixture startup timed out: " + captured)), 5_000);
      const onData = (chunk: string) => {
        const match = chunk.match(/http:\/\/127\.0\.0\.1:(\d+)\/api/);
        if (match?.[1]) {
          clearTimeout(timer);
          child.stdout.off("data", onData);
          resolve("http://127.0.0.1:" + match[1] + "/api");
        }
      };
      child.stdout.on("data", onData);
      child.once("error", (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once("close", (code) => {
        clearTimeout(timer);
        reject(new Error("Fixture exited before startup (" + code + "): " + captured));
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
      target: url.pathname + url.search,
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

function sourceAssetId(frame: unknown): string | null {
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) {
    return null;
  }
  const storyboard = (frame as Record<string, unknown>).storyboard;
  return Array.isArray(storyboard) && typeof storyboard[0] === "string"
    ? storyboard[0]
    : null;
}

function sourceText(frame: unknown): string {
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) {
    return "";
  }
  const record = frame as Record<string, unknown>;
  const value = record.text || record.original_text || "";
  return typeof value === "string" ? value : String(value);
}

function updateFor(
  expectedRevision: number,
  frames: PersonalRoughCutUpdate["frames"],
): PersonalRoughCutUpdate {
  return { expected_revision: expectedRevision, frames };
}

function rawPut(
  baseUrl: string,
  targetChapterId: string,
  body: unknown,
  options: { token?: string | null; query?: string } = {},
): Promise<Response> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (options.token !== null) {
    headers.set("Authorization", "Bearer " + (options.token ?? "fixture-demo-token"));
  }
  return globalThis.fetch(
    baseUrl + "/chapters/" + encodeURIComponent(targetChapterId) + "/rough-cut" + (options.query ?? ""),
    { method: "PUT", headers, body: JSON.stringify(body) },
  );
}

function fixtureLogs(output: string): Array<Record<string, unknown>> {
  return output.split("\n")
    .filter((line) => line.startsWith("{"))
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

const forcedStatuses = [401, 403, 404, 422, 500] as const;

describe("personal rough-cut save over real HTTP", () => {
  it("uses same-path PUT, exact full source order, current Bearer auth, and per-user private revisions", async () => {
    const fixture = await startFixture();
    const records: RequestRecord[] = [];
    const first = createServices(fixture.baseUrl, records);
    const second = createServices(fixture.baseUrl, records, { token: "fixture-demo-token-02" });

    try {
      const chapters = await first.services.listChapters(seriesId, new AbortController().signal);
      const chapter = chapters.find((candidate) => candidate.id === chapterId);
      if (!chapter || !Array.isArray(chapter.content)) {
        throw new Error("Expected the default two-frame chapter.");
      }
      const sourceIds = chapter.content.map(sourceAssetId);
      if (sourceIds.length !== 2 || sourceIds.some((id) => id === null)) {
        throw new Error("Expected two stable source asset IDs.");
      }

      const initial = await first.services.getPersonalRoughCut(chapterId, new AbortController().signal);
      expect(initial.revision).toBe(6);
      expect(initial.frames.map(({ asset_id }) => asset_id)).not.toEqual(sourceIds);
      const submittedFrames = sourceIds.map((asset_id, index) => ({
        asset_id: asset_id!,
        included: index === 0,
      }));
      const saved = await first.services.savePersonalRoughCut!(
        chapterId,
        updateFor(initial.revision, submittedFrames),
        new AbortController().signal,
      );

      expect(saved).toMatchObject({
        chapter_id: chapterId,
        revision: 7,
        saved: true,
        removed_asset_ids: [],
      });
      expect(saved.frames.map(({ asset_id }) => asset_id)).toEqual(sourceIds);
      expect(saved.frames.map(({ frame_index }) => frame_index)).toEqual([0, 1]);
      expect(saved.frames.map(({ included, pending }) => ({ included, pending }))).toEqual([
        { included: true, pending: false },
        { included: false, pending: false },
      ]);
      expect(saved.frames.map(({ text }) => text)).toEqual(chapter.content.map(sourceText));

      const putRecord = records.find((record) => record.method === "PUT");
      expect(putRecord).toMatchObject({
        target: "/api/chapters/" + chapterId + "/rough-cut",
        authorization: "Bearer fixture-demo-token",
      });
      expect(records.filter(({ method }) => method === "PUT")).toHaveLength(1);

      const secondInitial = await second.services.getPersonalRoughCut(chapterId, new AbortController().signal);
      expect(secondInitial).toMatchObject({ revision: 11, saved: true });
      expect(secondInitial.frames.map(({ asset_id }) => asset_id)).toEqual(sourceIds);
      const secondSaved = await second.services.savePersonalRoughCut!(
        chapterId,
        updateFor(11, submittedFrames.map((frame) => ({ ...frame, included: !frame.included }))),
        new AbortController().signal,
      );
      expect(secondSaved.revision).toBe(12);
      expect(secondSaved.frames.map(({ included }) => included)).toEqual([false, true]);

      const firstReread = await first.services.getPersonalRoughCut(chapterId, new AbortController().signal);
      expect(firstReread.revision).toBe(7);
      expect(firstReread.frames.map(({ included }) => included)).toEqual([true, false]);

      const logs = fixtureLogs(fixture.output());
      expect(logs.length).toBeGreaterThan(0);
      expect(logs.every((entry) => Object.keys(entry).sort().join(",") === "method,pathname,status")).toBe(true);
      expect(fixture.output()).not.toContain("fixture-demo-token");
      expect(fixture.output()).not.toContain("fixture-demo-token-02");
      expect(fixture.output()).not.toContain("粗剪私密文字");
    } finally {
      await fixture.stop();
    }
  });

  it("accepts an empty array and null-content chapter as an empty revision-zero source set", async () => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_MODE: "empty" });
    const { services } = createServices(fixture.baseUrl);

    try {
      for (const currentChapterId of [emptyChapterId, nullContentChapterId]) {
        const initial = await services.getPersonalRoughCut(currentChapterId, new AbortController().signal);
        expect(initial).toMatchObject({ chapter_id: currentChapterId, revision: 0, saved: false, frames: [] });

        const saved = await services.savePersonalRoughCut!(
          currentChapterId,
          updateFor(0, []),
          new AbortController().signal,
        );
        expect(saved).toMatchObject({
          chapter_id: currentChapterId,
          revision: 1,
          saved: true,
          frames: [],
          removed_asset_ids: [],
        });
        expect(await services.getPersonalRoughCut(currentChapterId, new AbortController().signal)).toEqual(saved);
      }
    } finally {
      await fixture.stop();
    }
  });

  it("rejects malformed, incomplete, duplicate, foreign, stale, and query-bearing updates without changing state", async () => {
    const fixture = await startFixture();
    try {
      const chapterResponse = await globalThis.fetch(fixture.baseUrl + "/series/" + seriesId + "/chapters", {
        headers: { Authorization: "Bearer fixture-demo-token" },
      });
      const chapters = await chapterResponse.json() as Array<{ id: string; content: unknown[] | null }>;
      const chapter = chapters.find((candidate) => candidate.id === chapterId);
      if (!chapter || !Array.isArray(chapter.content)) {
        throw new Error("Expected the default two-frame chapter.");
      }
      const ids = chapter.content.map(sourceAssetId);
      if (ids.length !== 2 || ids.some((id) => id === null)) {
        throw new Error("Expected two stable source asset IDs.");
      }
      const full = ids.map((asset_id) => ({ asset_id: asset_id!, included: true }));

      const bodies: unknown[] = [
        { expected_revision: 6, frames: full, extra: true },
        { expected_revision: 6, frames: [{ ...full[0], extra: true }, full[1]] },
        { expected_revision: 6, frames: [full[0], full[0]] },
        { expected_revision: 6, frames: [full[0]] },
        { expected_revision: 6, frames: [{ asset_id: "foreign-frame", included: true }, full[1]] },
        { expected_revision: 6, frames: [{ asset_id: full[0]!.asset_id, included: 1 }, full[1]] },
      ];
      for (const body of bodies) {
        expect((await rawPut(fixture.baseUrl, chapterId, body)).status).toBe(422);
      }
      expect((await rawPut(
        fixture.baseUrl,
        chapterId,
        updateFor(6, full),
        { query: "?unexpected=1" },
      )).status).toBe(422);
      expect((await rawPut(fixture.baseUrl, chapterId, updateFor(5, full))).status).toBe(409);
      expect((await rawPut(fixture.baseUrl, chapterId, updateFor(6, full), { token: null })).status).toBe(401);

      const { services } = createServices(fixture.baseUrl);
      const unchanged = await services.getPersonalRoughCut(chapterId, new AbortController().signal);
      expect(unchanged).toMatchObject({ revision: 6, saved: true });
    } finally {
      await fixture.stop();
    }
  });

  it.each(forcedStatuses)("maps the explicit save status override %i", async (status) => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_SAVE_STATUS: String(status) });
    const { services } = createServices(fixture.baseUrl);
    try {
      await expect(services.savePersonalRoughCut!(
        chapterId,
        updateFor(6, [
          { asset_id: seriesId + "-chapter-02-shot-b", included: true },
          { asset_id: seriesId + "-chapter-02-shot-a", included: false },
        ]),
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "http", status });
    } finally {
      await fixture.stop();
    }
  });

  it("returns maintained metadata without changing submitted identity/order, then GET reprojects current source", async () => {
    const fixture = await startFixture({ FIXTURE_ROUGH_CUT_SAVE_MODE: "source-maintenance-success" });
    const { services } = createServices(fixture.baseUrl);
    try {
      const chapters = await services.listChapters(seriesId, new AbortController().signal);
      const chapter = chapters.find((candidate) => candidate.id === chapterId);
      if (!chapter || !Array.isArray(chapter.content)) {
        throw new Error("Expected the default two-frame chapter.");
      }
      const ids = chapter.content.map(sourceAssetId);
      if (ids.length !== 2 || ids.some((id) => id === null)) {
        throw new Error("Expected two stable source asset IDs.");
      }
      const update = updateFor(6, ids.map((asset_id, index) => ({
        asset_id: asset_id!,
        included: index === 1,
      })));
      const maintained = await services.savePersonalRoughCut!(chapterId, update, new AbortController().signal);

      expect(maintained.frames.map(({ asset_id }) => asset_id)).toEqual(ids);
      expect(maintained.frames.map(({ included }) => included)).toEqual([false, true]);
      expect(maintained.frames.map(({ frame_index }) => frame_index)).toEqual([1, 0]);
      expect(maintained.frames.map(({ frame_index }) => frame_index).sort()).toEqual([0, 1]);
      expect(maintained.frames.every(({ text }) => text.endsWith("（服务端维护）"))).toBe(true);
      expect(maintained.frames.every(({ preview_url }) => preview_url === null)).toBe(true);

      const reread = await services.getPersonalRoughCut(chapterId, new AbortController().signal);
      expect(reread.frames.map(({ asset_id }) => asset_id)).toEqual(ids);
      expect(reread.frames.map(({ frame_index }) => frame_index)).toEqual([0, 1]);
      expect(reread.frames.map(({ text }) => text)).toEqual(chapter.content.map(sourceText));
      expect(reread.frames.map(({ included }) => included)).toEqual([false, true]);
    } finally {
      await fixture.stop();
    }
  });

  it("keeps applied writes readable after malformed success and body timeout, but not after header timeout", async () => {
    const malformedFixture = await startFixture({
      FIXTURE_ROUGH_CUT_SAVE_MODE: "applied-invalid-structure",
    });
    const malformed = createServices(malformedFixture.baseUrl);
    try {
      await expect(malformed.services.savePersonalRoughCut!(
        chapterId,
        updateFor(6, [
          { asset_id: seriesId + "-chapter-02-shot-b", included: true },
          { asset_id: seriesId + "-chapter-02-shot-a", included: false },
        ]),
        new AbortController().signal,
      )).rejects.toBeInstanceOf(InvalidResponseError);
      expect((await malformed.services.getPersonalRoughCut(chapterId, new AbortController().signal)).revision)
        .toBe(7);
    } finally {
      await malformedFixture.stop();
    }

    const bodyTimeoutFixture = await startFixture({ FIXTURE_ROUGH_CUT_SAVE_MODE: "body-timeout" });
    const bodyTimeout = createServices(bodyTimeoutFixture.baseUrl, [], { timeoutMs: 40 });
    try {
      await expect(bodyTimeout.services.savePersonalRoughCut!(
        chapterId,
        updateFor(6, [
          { asset_id: seriesId + "-chapter-02-shot-b", included: true },
          { asset_id: seriesId + "-chapter-02-shot-a", included: false },
        ]),
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "timeout" });
      expect((await bodyTimeout.services.getPersonalRoughCut(chapterId, new AbortController().signal)).revision)
        .toBe(7);
    } finally {
      await bodyTimeoutFixture.stop();
    }

    const timeoutFixture = await startFixture({ FIXTURE_ROUGH_CUT_SAVE_MODE: "timeout" });
    const timeout = createServices(timeoutFixture.baseUrl, [], { timeoutMs: 40 });
    try {
      await expect(timeout.services.savePersonalRoughCut!(
        chapterId,
        updateFor(6, [
          { asset_id: seriesId + "-chapter-02-shot-b", included: true },
          { asset_id: seriesId + "-chapter-02-shot-a", included: false },
        ]),
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "timeout" });
      expect((await timeout.services.getPersonalRoughCut(chapterId, new AbortController().signal)).revision)
        .toBe(6);
    } finally {
      await timeoutFixture.stop();
    }
  });
});
