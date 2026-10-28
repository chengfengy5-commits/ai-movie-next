// @vitest-environment node
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvalidResponseError, type ChapterEditGuard } from "./contracts";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, type StorageLike } from "./storage";
import { projectStoryboardFrames } from "../../features/chapters/storyboardProjection";
import { continuousSourceSnapshotBinding } from "./continuousChapterOperations";

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

function requireServiceMethod<T>(method: T | undefined, name: string): NonNullable<T> {
  if (method === undefined) throw new Error(`Expected ${name} to be available.`);
  return method as NonNullable<T>;
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
  it("negotiates the guarded chapter capability through the authenticated current API session", async () => {
    const requests: Array<{ method: string; url: string; authorization: string | null; body: string | null }> = [];
    const seriesId = "series-a";
    const chapterId = "chapter-a";
    const guard: ChapterEditGuard = {
      version: 1,
      expected_chapter_id: chapterId,
      expected_series_id: seriesId,
      expected_order: 1,
      expected_content: [{ text: "原文", storyboard: ["shot-a"] }],
      expected_storyboards: [{ id: "shot-a", series_id: seriesId, chapter_id: chapterId, frame_index: 0 }],
      expected_lock_identity: null,
      frame_sources: [{ kind: "retained", source_index: 0, storyboard_id: "shot-a" }],
      deleted_source_indexes: [],
    };
    const update = { content: [{ text: "新正文", storyboard: ["shot-a"] }], chapter_edit_guard: guard };
    const { services } = createServices("http://127.0.0.1/api", {
      fetcher: async (input, init) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        requests.push({
          method,
          url,
          authorization: new Headers(init?.headers).get("Authorization"),
          body: typeof init?.body === "string" ? init.body : null,
        });
        const response = method === "GET"
          ? { features: { chapter_edit_guard: 1 } }
          : {
              id: chapterId,
              series_id: seriesId,
              title: "第一章",
              content: update.content,
              order: 1,
              created_at: "2026-10-01T00:00:00Z",
              updated_at: "2026-10-02T00:00:00Z",
              lock: null,
            };
        return new Response(JSON.stringify(response), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    });
    const getCapabilities = requireServiceMethod(
      services.getContinuousChapterEditCapabilities,
      "getContinuousChapterEditCapabilities",
    );

    await expect(getCapabilities(new AbortController().signal)).resolves.toEqual({ chapterEditGuard: 1 });
    const updateChapter = requireServiceMethod(services.updateChapter, "updateChapter");
    await expect(updateChapter(seriesId, chapterId, update, new AbortController().signal))
      .resolves.toMatchObject({ id: chapterId, content: update.content });
    expect(requests).toEqual([{
      method: "GET",
      url: "http://127.0.0.1/api/chapters/continuous-edit/capabilities",
      authorization: "Bearer fixture-demo-token",
      body: null,
    }, {
      method: "PUT",
      url: "http://127.0.0.1/api/chapters/chapter-a",
      authorization: "Bearer fixture-demo-token",
      body: JSON.stringify(update),
    }]);
  });

  it("fails closed when chapter guard capability is missing or malformed", async () => {
    const { services } = createServices("http://127.0.0.1/api", {
      fetcher: async () => new Response(JSON.stringify({ features: { chapter_edit_guard: "1" } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    });
    const getCapabilities = requireServiceMethod(
      services.getContinuousChapterEditCapabilities,
      "getContinuousChapterEditCapabilities",
    );
    await expect(getCapabilities(new AbortController().signal)).rejects.toMatchObject({ name: "InvalidResponseError" });
  });

  it("negotiates the operation receipt and sends the frozen request bytes to the original UUID routes", async () => {
    const operationId = "00000000-0000-4000-8000-000000000001";
    const editorSessionId = "00000000-0000-4000-8000-000000000002";
    const requestBody = `{"protocol_version":1, "editor_session_id":"${editorSessionId}","command":{"kind":"save-chapter"}}`;
    const requestBodySha256 = createHash("sha256").update(requestBody, "utf8").digest("hex");
    const sourceBinding = await continuousSourceSnapshotBinding(requestBodySha256);
    const targetManifestDigest = createHash("sha256").update(JSON.stringify(["source:0"]), "utf8").digest("hex");
    const canonicalResponseJson = JSON.stringify({ content: [{ storyboard: [] }], id: "chapter/a", series_id: "series-a" });
    const receipt = {
      receipt_version: 1,
      operation_id: operationId,
      owner_id: "user-a",
      editor_session_id: editorSessionId,
      series_id: "series-a",
      chapter_id: "chapter/a",
      command_kind: "save-chapter",
      request_body_sha256: requestBodySha256,
      source_snapshot_digest: sourceBinding,
      target_draft_id: null,
      target_manifest_digest: targetManifestDigest,
      outcome: "committed",
      http_status: 200,
      result_identity: {
        kind: "save-chapter",
        series_id: "series-a",
        chapter_id: "chapter/a",
        draft_frame_ids: ["source:0"],
        manifest_digest: targetManifestDigest,
        source_snapshot_digest: sourceBinding,
        frame_storyboard_ids: [[]],
      },
      result: { content: [{ storyboard: [] }], id: "chapter/a", series_id: "series-a" },
      canonical_response_json: canonicalResponseJson,
      result_digest: createHash("sha256").update(canonicalResponseJson, "utf8").digest("hex"),
      committed_at: "2026-10-28T00:00:00",
    };
    const requests: Array<{ method: string; url: string; body: string | null }> = [];
    const { services } = createServices("http://127.0.0.1/api", {
      fetcher: async (input, init) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        requests.push({ method, url, body: typeof init?.body === "string" ? init.body : null });
        const payload = url.endsWith("/chapters/continuous-edit/capabilities")
          ? { features: { chapter_edit_guard: 1, continuous_edit_operation_receipt: 1 } }
          : method === "POST" ? { receipt, result: receipt.result } : { receipt };
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    });

    const getCapabilities = requireServiceMethod(
      services.getContinuousChapterEditCapabilities,
      "getContinuousChapterEditCapabilities",
    );
    await expect(getCapabilities(new AbortController().signal)).resolves.toEqual({
      chapterEditGuard: 1,
      operationReceipt: 1,
    });
    const operations = services.continuousChapterOperations;
    if (operations === undefined) throw new Error("Expected continuous chapter operation API.");
    await expect(operations.submit("chapter/a", operationId, requestBody, new AbortController().signal))
      .resolves.toMatchObject({ receipt: { operation_id: operationId, outcome: "committed" } });
    await expect(operations.lookup("chapter/a", operationId, new AbortController().signal))
      .resolves.toMatchObject({ receipt: { operation_id: operationId } });

    expect(requests).toEqual([
      {
        method: "GET",
        url: "http://127.0.0.1/api/chapters/continuous-edit/capabilities",
        body: null,
      },
      {
        method: "POST",
        url: `http://127.0.0.1/api/chapters/chapter%2Fa/continuous-edit/operations/${operationId}`,
        body: requestBody,
      },
      {
        method: "GET",
        url: `http://127.0.0.1/api/chapters/chapter%2Fa/continuous-edit/operations/${operationId}`,
        body: null,
      },
    ]);
  });

  it("accepts a durable operation rejection at actual HTTP 409 without widening generic request errors", async () => {
    const operationId = "00000000-0000-4000-8000-000000000011";
    const editorSessionId = "00000000-0000-4000-8000-000000000012";
    const requestBody = JSON.stringify({ protocol_version: 1, editor_session_id: editorSessionId, command: { kind: "save-chapter" } });
    const requestBodySha256 = createHash("sha256").update(requestBody, "utf8").digest("hex");
    const sourceBinding = await continuousSourceSnapshotBinding(requestBodySha256);
    const targetManifestDigest = createHash("sha256").update(JSON.stringify(["draft-a"]), "utf8").digest("hex");
    const canonicalResponseJson = JSON.stringify({ detail: "章节来源已变化" });
    const receipt = {
      receipt_version: 1,
      operation_id: operationId,
      owner_id: "user-a",
      editor_session_id: editorSessionId,
      series_id: "series-a",
      chapter_id: "chapter-a",
      command_kind: "save-chapter",
      request_body_sha256: requestBodySha256,
      source_snapshot_digest: sourceBinding,
      target_draft_id: null,
      target_manifest_digest: targetManifestDigest,
      outcome: "rejected",
      http_status: 409,
      result_identity: {
        kind: "save-chapter",
        series_id: "series-a",
        chapter_id: "chapter-a",
        draft_frame_ids: ["draft-a"],
        manifest_digest: targetManifestDigest,
        source_snapshot_digest: sourceBinding,
      },
      result: { detail: "章节来源已变化" },
      canonical_response_json: canonicalResponseJson,
      result_digest: createHash("sha256").update(canonicalResponseJson, "utf8").digest("hex"),
      committed_at: "2026-10-28T00:00:00",
    };
    const { services } = createServices("http://127.0.0.1/api", {
      fetcher: async (input, init) => {
        const url = input instanceof Request ? input.url : String(input);
        const method = init?.method ?? (input instanceof Request ? input.method : "GET");
        if (url.endsWith("/chapters/continuous-edit/capabilities")) {
          return new Response(JSON.stringify({ features: { chapter_edit_guard: 1, continuous_edit_operation_receipt: 1 } }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        if (method === "POST") {
          return new Response(JSON.stringify({ receipt, error: receipt.result }), {
            status: 409,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ detail: "generic conflict" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        });
      },
    });

    const operations = services.continuousChapterOperations;
    if (operations === undefined) throw new Error("Expected continuous chapter operation API.");
    await expect(operations.submit("chapter-a", operationId, requestBody, new AbortController().signal))
      .resolves.toMatchObject({ receipt: { outcome: "rejected", http_status: 409 } });
    const updateChapter = requireServiceMethod(services.updateChapter, "updateChapter");
    await expect(updateChapter("series-a", "chapter-a", { content: [] }, new AbortController().signal))
      .rejects.toMatchObject({ kind: "http", status: 409 });
  });

  it("keeps unauthenticated, missing, server, and receipt-less conflict responses unconfirmed", async () => {
    for (const status of [401, 403, 404, 500]) {
      const { services } = createServices("http://127.0.0.1/api", {
        fetcher: async () => new Response(JSON.stringify({ detail: "synthetic failure" }), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      });
      const operations = services.continuousChapterOperations;
      if (operations === undefined) throw new Error("Expected continuous chapter operation API.");
      await expect(operations.submit(
        "chapter-a",
        "00000000-0000-4000-8000-000000000041",
        "{}",
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "http", status });
    }

    for (const status of [400, 409]) {
      const { services } = createServices("http://127.0.0.1/api", {
        fetcher: async () => new Response(JSON.stringify({ detail: "receipt missing" }), {
          status,
          headers: { "Content-Type": "application/json" },
        }),
      });
      const operations = services.continuousChapterOperations;
      if (operations === undefined) throw new Error("Expected continuous chapter operation API.");
      await expect(operations.submit(
        "chapter-a",
        "00000000-0000-4000-8000-000000000042",
        "{}",
        new AbortController().signal,
      )).rejects.toMatchObject({ name: "InvalidResponseError" });
    }
  });

  afterEach(() => vi.restoreAllMocks());

  it("sends one full content PUT, then confirms a committed 500 result through fresh chapter and asset reads", async () => {
    const fixture = await startFixture({ FIXTURE_CHAPTER_UPDATE_MODE: "commit-then-500" });
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
    const requests: Array<{ method: string; pathname: string; authorization: string | null; body: string | null }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input instanceof URL
        ? input
        : input instanceof Request
          ? new URL(input.url)
          : new URL(input);
      requests.push({
        method: init?.method ?? (input instanceof Request ? input.method : "GET"),
        pathname: url.pathname,
        authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
          .get("Authorization"),
        body: typeof init?.body === "string" ? init.body : null,
      });
      return globalThis.fetch(input, init);
    };
    const services = createApiServices(fixture.baseUrl, { storage, fetcher });
    const updateChapter = requireServiceMethod(services.updateChapter, "updateChapter");
    const seriesId = "fixture-series-01";
    const chapterId = `${seriesId}-chapter-03`;
    const content = [{ text: "", original_text: null, nested_future: { preserved: true } }];

    try {
      await expect(updateChapter(
        seriesId,
        chapterId,
        { content },
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "http", status: 500 });

      const chapters = await services.listChapters(seriesId, new AbortController().signal);
      const saved = chapters.find((chapter) => chapter.id === chapterId);
      const assets = await services.listStoryboardAssets(seriesId, chapterId, new AbortController().signal);
      expect(saved?.content).toEqual([{ ...content[0], storyboard: [`fixture-generated-${chapterId}-0`] }]);
      expect(assets).toContainEqual(expect.objectContaining({
        id: `fixture-generated-${chapterId}-0`,
        series_id: seriesId,
        chapter_id: chapterId,
        frame_index: 0,
      }));
      expect(requests.filter((request) => request.method === "PUT")).toEqual([{
        method: "PUT",
        pathname: `/api/chapters/${chapterId}`,
        authorization: "Bearer fixture-demo-token",
        body: JSON.stringify({ content }),
      }]);
      expect(requests.slice(1).every((request) => request.authorization === "Bearer fixture-demo-token")).toBe(true);
    } finally {
      await fixture.stop();
    }
  });

  it("sends one complete reorder request and reads back the assigned chapter orders", async () => {
    const fixture = await startFixture();
    const requests: Array<{ method: string; pathname: string; authorization: string | null; body: string | null }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input instanceof URL
        ? input
        : input instanceof Request
          ? new URL(input.url)
          : new URL(input);
      requests.push({
        method: init?.method ?? (input instanceof Request ? input.method : "GET"),
        pathname: url.pathname,
        authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
          .get("Authorization"),
        body: typeof init?.body === "string" ? init.body : null,
      });
      return globalThis.fetch(input, init);
    };
    const { services } = createServices(fixture.baseUrl, { fetcher });
    const reorderChapters = requireServiceMethod(services.reorderChapters, "reorderChapters");
    const seriesId = "fixture-series-01";
    const ids = ["fixture-series-01-chapter-01", "fixture-series-01-chapter-02", "fixture-series-01-chapter-03", "fixture-series-01-chapter-04"];
    const input = { chapters: [...ids].reverse().map((id, index) => ({ id, order: ids.length - index })) };

    try {
      await reorderChapters(seriesId, input, new AbortController().signal);
      const chapters = await services.listChapters(seriesId, new AbortController().signal);
      const reordered = chapters.filter((chapter) => ids.includes(chapter.id));
      expect(reordered).toHaveLength(ids.length);
      expect(reordered.map((chapter) => chapter.id).sort()).toEqual([...ids].sort());
      expect(Object.fromEntries(reordered.map((chapter) => [chapter.id, chapter.order])))
        .toEqual(Object.fromEntries(input.chapters.map(({ id, order }) => [id, order])));
      expect(requests.filter((request) => request.method === "PUT")).toEqual([{
        method: "PUT",
        pathname: `/api/series/${seriesId}/chapters/reorder`,
        authorization: "Bearer fixture-demo-token",
        body: JSON.stringify(input),
      }]);
    } finally {
      await fixture.stop();
    }
  });

  it("keeps legacy lock acquisition server-managed and rejects an unleased read after the API session changes", async () => {
    const fixture = await startFixture();
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "fixture-demo-token");
    const requests: Array<{ method: string; pathname: string; authorization: string | null }> = [];
    let holdNextLockRead = false;
    let signalLockReadStarted!: () => void;
    let releaseLockRead!: () => void;
    const lockReadStarted = new Promise<void>((resolve) => { signalLockReadStarted = resolve; });
    const lockReadGate = new Promise<void>((resolve) => { releaseLockRead = resolve; });
    const fetcher: typeof fetch = async (input, init) => {
      const url = input instanceof URL
        ? input
        : input instanceof Request
          ? new URL(input.url)
          : new URL(input);
      const method = init?.method ?? (input instanceof Request ? input.method : "GET");
      requests.push({
        method,
        pathname: url.pathname,
        authorization: new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined))
          .get("Authorization"),
      });
      if (holdNextLockRead && method === "GET" && url.pathname.endsWith("/lock")) {
        holdNextLockRead = false;
        signalLockReadStarted();
        await lockReadGate;
      }
      return globalThis.fetch(input, init);
    };
    const services = createApiServices(fixture.baseUrl, { storage, fetcher });
    const acquireLock = requireServiceMethod(services.acquireChapterEditLock, "acquireChapterEditLock");
    const getLock = requireServiceMethod(services.getChapterEditLock, "getChapterEditLock");
    const signal = new AbortController().signal;
    const chapterId = "fixture-series-03-chapter-02";

    try {
      const acquisition = await acquireLock(chapterId, signal);
      expect(acquisition.state.locked).toBe(true);
      expect(acquisition.lease).toBeNull();
      const verified = await getLock(chapterId, signal);
      expect(verified).toMatchObject({ locked: true, user_id: "demo-user", is_mine: true });

      holdNextLockRead = true;
      const pendingRead = getLock(chapterId, signal);
      await lockReadStarted;
      storage.setItem(API_TOKEN_KEY, "fixture-demo-token-02");
      releaseLockRead();
      await expect(pendingRead).rejects.toMatchObject({ kind: "aborted" });

      expect(requests.map(({ method, pathname }) => [method, pathname])).toEqual([
        ["POST", `/api/chapters/${chapterId}/lock`],
        ["GET", `/api/chapters/${chapterId}/lock`],
        ["GET", `/api/chapters/${chapterId}/lock`],
      ]);
      expect(requests.every((request) => request.authorization !== null)).toBe(true);
      expect(requests.every((request) => request.authorization === "Bearer fixture-demo-token")).toBe(true);
      expect(requests.some((request) => request.method === "DELETE")).toBe(false);
    } finally {
      releaseLockRead();
      await fixture.stop();
    }
  });

  it("does not create a chapter lock for a personal series", async () => {
    const fixture = await startFixture();
    const { services } = createServices(fixture.baseUrl);
    const acquireLock = requireServiceMethod(services.acquireChapterEditLock, "acquireChapterEditLock");

    try {
      const acquisition = await acquireLock("fixture-series-01-chapter-01", new AbortController().signal);

      expect(acquisition).toEqual({
        state: { locked: false, message: "个人剧集无需加锁" },
        lease: null,
      });
      expect(fixture.output()).toContain(
        '"method":"POST","pathname":"/api/chapters/fixture-series-01-chapter-01/lock","status":200',
      );
    } finally {
      await fixture.stop();
    }
  });

  it("allows a chapter PUT to extend the same server-managed lock without claiming release authority", async () => {
    const fixture = await startFixture();
    const requests: Array<{ method: string; pathname: string }> = [];
    const fetcher: typeof fetch = async (input, init) => {
      const url = input instanceof URL
        ? input
        : input instanceof Request
          ? new URL(input.url)
          : new URL(input);
      requests.push({
        method: init?.method ?? (input instanceof Request ? input.method : "GET"),
        pathname: url.pathname,
      });
      return globalThis.fetch(input, init);
    };
    const { services } = createServices(fixture.baseUrl, { fetcher });
    const acquireLock = requireServiceMethod(services.acquireChapterEditLock, "acquireChapterEditLock");
    const getLock = requireServiceMethod(services.getChapterEditLock, "getChapterEditLock");
    const updateChapter = requireServiceMethod(services.updateChapter, "updateChapter");
    const seriesId = "fixture-series-03";
    const signal = new AbortController().signal;

    try {
      const chapters = await services.listChapters(seriesId, signal);
      const chapter = chapters[0];
      if (!chapter || chapter.content === null) throw new Error("Expected a fixture chapter with storyboard content.");
      const acquisition = await acquireLock(chapter.id, signal);
      expect(acquisition.state.locked).toBe(true);
      expect(acquisition.lease).toBeNull();
      const before = await getLock(chapter.id, signal);
      const updated = await updateChapter(seriesId, chapter.id, { content: chapter.content }, signal);
      const after = await getLock(chapter.id, signal);

      expect(updated.content).toEqual(chapter.content);
      expect(after.acquired_at).toBe(before.acquired_at);
      expect(Date.parse(after.expires_at ?? "")).toBeGreaterThan(Date.parse(before.expires_at ?? ""));
      expect(fixture.output()).toContain(`"method":"PUT","pathname":"/api/chapters/${chapter.id}","status":200`);
      expect(requests.filter((request) => request.pathname === `/api/chapters/${chapter.id}/lock`)
        .map((request) => request.method)).toEqual(["POST", "GET", "GET"]);
      expect(requests.some((request) => request.method === "DELETE"
        && request.pathname === `/api/chapters/${chapter.id}/lock`)).toBe(false);
    } finally {
      await fixture.stop();
    }
  });

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
