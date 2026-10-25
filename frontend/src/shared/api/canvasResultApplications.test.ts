// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  CanvasResultApplicationError,
  type CanvasResultApplicationRunContext,
} from "./canvasResultApplications";
import { createCanvasResultApplicationsHttpPort } from "./canvasResultApplicationsHttp";
import { createApiServices, createDemoServices } from "./services";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

const apiBaseUrl = "http://127.0.0.1:4175/api";
const actorId = "result-lookup-user";
const seriesId = "result-lookup-series";
const chapterId = "chapter-one";
const taskId = "task+one";
const resultRef = "result-one";
const targetRevision = "a".repeat(64);

function makeStorage(): MemoryStorage {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "captured-result-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: actorId }));
  return storage;
}

function makeContext(overrides: Partial<CanvasResultApplicationRunContext> = {}): CanvasResultApplicationRunContext {
  return {
    actorId,
    seriesId,
    chapterId,
    isContextCurrent: () => true,
    ...overrides,
  };
}

function selector(overrides: Partial<{ taskId: string; resultRef: string }> = {}) {
  return { taskId, resultRef, ...overrides };
}

function appliedPayload(overrides: Record<string, unknown> = {}) {
  return {
    task_id: taskId,
    result_ref: resultRef,
    status: "applied",
    application: {
      application_ref: "application-1",
      target_revision: targetRevision,
      committed_at: "2026-10-25T08:30:00.000Z",
    },
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makePort(
  storage: StorageLike = makeStorage(),
  fetcher: typeof fetch = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(appliedPayload())),
  timeoutMs?: number,
) {
  return createCanvasResultApplicationsHttpPort(apiBaseUrl, {
    storage,
    fetcher,
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
  });
}

function getUrl(input: RequestInfo | URL): URL {
  return input instanceof URL ? input : new URL(input instanceof Request ? input.url : input);
}

function headersFor(init: RequestInit): Headers {
  return new Headers(init.headers);
}

describe("canvas owned-result application lookup HTTP port", () => {
  it("is exposed only in API services and performs the exact authenticated read", async () => {
    const storage = makeStorage();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(appliedPayload()));
    const apiServices = createApiServices(apiBaseUrl, { storage, fetcher });
    const demoServices = createDemoServices(storage);
    const port = apiServices.canvasResultApplications;
    if (port === undefined) throw new Error("Expected API mode canvas result application lookup port.");

    const result = await port.beginRun(makeContext()).lookup(selector(), new AbortController().signal);

    expect(result).toEqual(appliedPayload());
    expect(demoServices.canvasResultApplications).toBeUndefined();
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [input, init = {}] = fetcher.mock.calls[0]!;
    const url = getUrl(input);
    expect(url.href).toBe(
      `${apiBaseUrl}/chapters/chapter-one/canvas-owned-results/result-one/application?task_id=task%2Bone`,
    );
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect(headersFor(init).get("authorization")).toBe("Bearer captured-result-token");
    expect(headersFor(init).get("accept")).toBe("application/json");
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    expect(init.redirect).toBe("error");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(url.origin).toBe(new URL(apiBaseUrl).origin);
    expect(url.searchParams.getAll("task_id")).toEqual([taskId]);
    expect([...url.searchParams.keys()]).toEqual(["task_id"]);
  });

  it("accepts not_applied only with a null application", async () => {
    const payload = { task_id: taskId, result_ref: resultRef, status: "not_applied", application: null };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload));
    const port = makePort(makeStorage(), fetcher);

    await expect(port.beginRun(makeContext()).lookup(selector(), new AbortController().signal)).resolves.toEqual(payload);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed or non-minimal application envelopes", async () => {
    const validApplication = appliedPayload().application;
    const invalidPayloads: unknown[] = [
      appliedPayload({ application: null }),
      { ...appliedPayload(), application: { ...validApplication, application_ref: "" } },
      { ...appliedPayload(), application: { ...validApplication, application_ref: "r".repeat(37) } },
      { ...appliedPayload(), application: { ...validApplication, application_ref: "application\u0090id" } },
      { ...appliedPayload(), application: { ...validApplication, target_revision: "A".repeat(64) } },
      { ...appliedPayload(), application: { ...validApplication, target_revision: "not-a-revision" } },
      { ...appliedPayload(), application: { ...validApplication, committed_at: "2026-13-40T99:99:99Z" } },
      { ...appliedPayload(), application: { ...validApplication, committed_at: "2026-10-25T08:30:00" } },
      { ...appliedPayload(), application: { ...validApplication, extra: "unexpected" } },
      { ...appliedPayload(), extra: "unexpected" },
      { ...appliedPayload(), application_url: "https://private.example.invalid/result" },
      { task_id: taskId, result_ref: resultRef, status: "not_applied", application: validApplication },
      { ...appliedPayload(), status: "queued" },
    ];

    for (const payload of invalidPayloads) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload));
      const port = makePort(makeStorage(), fetcher);
      await expect(port.beginRun(makeContext()).lookup(selector(), new AbortController().signal))
        .rejects.toMatchObject({ kind: "invalid-response" } satisfies Partial<CanvasResultApplicationError>);
      expect(fetcher).toHaveBeenCalledTimes(1);
    }
  });

  it("rejects response task and result identifiers that differ from the selector", async () => {
    const mismatches = [
      appliedPayload({ task_id: "another-task" }),
      appliedPayload({ result_ref: "another-result" }),
    ];

    for (const payload of mismatches) {
      const port = makePort(makeStorage(), vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload)));
      await expect(port.beginRun(makeContext()).lookup(selector(), new AbortController().signal))
        .rejects.toMatchObject({ kind: "invalid-response" });
    }
  });

  it("classifies read statuses without retrying, logging out, or issuing writes", async () => {
    const statuses = [
      { status: 401, kind: "unauthorized" },
      { status: 403, kind: "forbidden" },
      { status: 404, kind: "not-found" },
      { status: 409, kind: "unready" },
      { status: 503, kind: "unavailable" },
    ] as const;

    for (const { status, kind } of statuses) {
      const storage = makeStorage();
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "synthetic" }, status));
      const port = makePort(storage, fetcher);
      await expect(port.beginRun(makeContext()).lookup(selector(), new AbortController().signal))
        .rejects.toMatchObject({ kind, status });
      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(fetcher.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true);
      expect(storage.getItem(API_TOKEN_KEY)).toBe("captured-result-token");
      expect(storage.getItem(API_USER_KEY)).toBe(JSON.stringify({ id: actorId }));
    }
  });

  it("rejects invalid selector and captured identity values before dispatch", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(appliedPayload()));
    const port = makePort(makeStorage(), fetcher);
    const run = port.beginRun(makeContext());

    const invalidIdentifiers = [
      "",
      " ",
      ` ${taskId}`,
      `${taskId} `,
      "t".repeat(37),
      "control\u0000id",
      "control\u009fid",
      "path/segment",
      "path\\segment",
      ".",
      "..",
    ];
    for (const invalid of invalidIdentifiers) {
      await expect(run.lookup(selector({ taskId: invalid }), new AbortController().signal))
        .rejects.toMatchObject({ kind: "not-sent" });
      await expect(run.lookup(selector({ resultRef: invalid }), new AbortController().signal))
        .rejects.toMatchObject({ kind: "not-sent" });
    }
    for (const invalid of invalidIdentifiers) {
      expect(() => port.beginRun(makeContext({ chapterId: invalid }))).toThrowError(CanvasResultApplicationError);
    }
    expect(() => port.beginRun(makeContext({ seriesId: ".." }))).toThrowError(CanvasResultApplicationError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("captures actor, token, series, chapter, and the active context before dispatch", async () => {
    const contextStorage = makeStorage();
    const contextFetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(appliedPayload()));
    const contextRun = makePort(contextStorage, contextFetcher).beginRun(
      makeContext({ isContextCurrent: () => false }),
    );
    await expect(contextRun.lookup(selector(), new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(contextFetcher).not.toHaveBeenCalled();

    for (const change of ["token", "actor"] as const) {
      const storage = makeStorage();
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(appliedPayload()));
      const run = makePort(storage, fetcher).beginRun(makeContext());
      storage.setItem(change === "token" ? API_TOKEN_KEY : API_USER_KEY,
        change === "token" ? "replacement-token" : JSON.stringify({ id: "replacement-actor" }));
      await expect(run.lookup(selector(), new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
      expect(fetcher).not.toHaveBeenCalled();
    }

    for (const change of ["series", "chapter"] as const) {
      let currentSeriesId = seriesId;
      let currentChapterId = chapterId;
      let contextEpoch = 4;
      const capturedEpoch = contextEpoch;
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(appliedPayload()));
      const run = makePort(makeStorage(), fetcher).beginRun(makeContext({
        isContextCurrent: () => currentSeriesId === seriesId
          && currentChapterId === chapterId
          && contextEpoch === capturedEpoch,
      }));
      if (change === "series") currentSeriesId = "another-series";
      else currentChapterId = "another-chapter";
      await expect(run.lookup(selector(), new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
      expect(fetcher).not.toHaveBeenCalled();
    }
  });

  it("rejects late success and late unauthorized responses after a context epoch changes", async () => {
    for (const status of [200, 401]) {
      let resolveResponse: ((response: Response) => void) | undefined;
      let contextEpoch = 3;
      let currentChapterId = chapterId;
      const capturedEpoch = contextEpoch;
      const fetcher: typeof fetch = vi.fn(() => new Promise<Response>((resolve) => { resolveResponse = resolve; }));
      const port = makePort(makeStorage(), fetcher);
      const run = port.beginRun(makeContext({
        isContextCurrent: () => currentChapterId === chapterId && contextEpoch === capturedEpoch,
      }));
      const pending = run.lookup(selector(), new AbortController().signal);
      currentChapterId = "chapter-after-switch";
      contextEpoch += 1;
      currentChapterId = chapterId;
      resolveResponse?.(status === 200 ? jsonResponse(appliedPayload()) : jsonResponse({}, 401));

      await expect(pending).rejects.toMatchObject({ kind: "read-error" });
      expect(fetcher).toHaveBeenCalledTimes(1);
    }
  });

  it("rejects late success after the stored actor or token changes during the request", async () => {
    for (const change of ["token", "actor"] as const) {
      for (const status of [200, 401]) {
        const storage = makeStorage();
        let resolveResponse: ((response: Response) => void) | undefined;
        const fetcher: typeof fetch = vi.fn(() => new Promise<Response>((resolve) => { resolveResponse = resolve; }));
        const run = makePort(storage, fetcher).beginRun(makeContext());
        const pending = run.lookup(selector(), new AbortController().signal);
        storage.setItem(change === "token" ? API_TOKEN_KEY : API_USER_KEY,
          change === "token" ? "rotated-token" : JSON.stringify({ id: "rotated-actor" }));
        resolveResponse?.(status === 200 ? jsonResponse(appliedPayload()) : jsonResponse({}, 401));

        await expect(pending).rejects.toMatchObject({ kind: "read-error" });
        expect(storage.getItem(change === "token" ? API_TOKEN_KEY : API_USER_KEY)).toBe(
          change === "token" ? "rotated-token" : JSON.stringify({ id: "rotated-actor" }),
        );
      }
    }
  });

  it("rechecks context after cancellation of a non-200 response body", async () => {
    let resolveBodyCancel: (() => void) | undefined;
    let markBodyCancelStarted: (() => void) | undefined;
    let bodyCancelled = false;
    const bodyCancelStarted = new Promise<void>((resolve) => { markBodyCancelStarted = resolve; });
    const response = new Response(new ReadableStream<Uint8Array>({
      cancel() {
        bodyCancelled = true;
        markBodyCancelStarted?.();
        return new Promise<void>((resolve) => { resolveBodyCancel = resolve; });
      },
    }), { status: 401 });
    const storage = makeStorage();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
    let contextEpoch = 5;
    const capturedEpoch = contextEpoch;
    const run = makePort(storage, fetcher).beginRun(makeContext({
      isContextCurrent: () => contextEpoch === capturedEpoch,
    }));
    const pending = run.lookup(selector(), new AbortController().signal);
    const expectedReadError = expect(pending).rejects.toMatchObject({ kind: "read-error" });

    await bodyCancelStarted;
    storage.setItem(API_TOKEN_KEY, "replacement-token");
    contextEpoch += 1;
    resolveBodyCancel?.();

    await expectedReadError;
    expect(bodyCancelled).toBe(true);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("replacement-token");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("aborts response-body reads when the caller cancels and cancels the stream", async () => {
    let streamCancelled = false;
    let resolveBodyRead: (() => void) | undefined;
    const bodyReadStarted = new Promise<void>((resolve) => { resolveBodyRead = resolve; });
    const response = new Response(new ReadableStream<Uint8Array>({
      pull() { resolveBodyRead?.(); },
      cancel() { streamCancelled = true; },
    }), { headers: { "content-type": "application/json" } });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
    const run = makePort(makeStorage(), fetcher).beginRun(makeContext());
    const controller = new AbortController();
    const pending = run.lookup(selector(), controller.signal);
    await bodyReadStarted;
    controller.abort();

    await expect(pending).rejects.toMatchObject({ kind: "read-error" });
    expect(streamCancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("applies the default 15 second deadline while reading the response body", async () => {
    vi.useFakeTimers();
    try {
      let streamCancelled = false;
      const response = new Response(new ReadableStream<Uint8Array>({
        start() { /* Keep the body pending until the request deadline cancels it. */ },
        cancel() { streamCancelled = true; },
      }), { headers: { "content-type": "application/json" } });
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
      const run = makePort(makeStorage(), fetcher).beginRun(makeContext());
      const pending = run.lookup(selector(), new AbortController().signal);
      const timeoutAssertion = expect(pending).rejects.toMatchObject({ kind: "read-error" });
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(15_000);
      await timeoutAssertion;
      expect(streamCancelled).toBe(true);
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("cancels a chunked response that exceeds 64 KiB", async () => {
    let bodyCancelled = false;
    const response = new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(40 * 1024));
        controller.enqueue(new Uint8Array(25 * 1024));
      },
      cancel() { bodyCancelled = true; },
    }), { headers: { "content-type": "application/json" } });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
    const run = makePort(makeStorage(), fetcher).beginRun(makeContext());

    await expect(run.lookup(selector(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    expect(bodyCancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
