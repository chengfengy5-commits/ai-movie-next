// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import process from "node:process";
import { createApiServices } from "./services";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

const IMAGE_LIMIT = 10 * 1024 * 1024;

interface ImageSelector {
  readonly taskId: string;
  readonly resultRef: string;
}

interface ImageRun {
  isCurrent(): boolean;
  read(selector: ImageSelector, signal: AbortSignal): Promise<Blob>;
}

interface ImagePort {
  beginRun(context: {
    actorId: string;
    seriesId: string;
    chapterId: string;
    isContextCurrent(): boolean;
  }): ImageRun;
}

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function createHarness(fetcher: typeof fetch) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "image-reader-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "user-1" }));
  const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
  const port = Reflect.get(services, "canvasOwnedImage") as ImagePort | undefined;
  if (port === undefined) throw new Error("API services did not expose canvasOwnedImage");

  let contextCurrent = true;
  const context = {
    actorId: "user-1",
    seriesId: "series-1",
    chapterId: "chapter-1",
    isContextCurrent: () => contextCurrent,
  };
  return {
    storage,
    port,
    context,
    run: port.beginRun(context),
    setContextCurrent(value: boolean) { contextCurrent = value; },
  };
}

function selector(overrides: Partial<ImageSelector> = {}): ImageSelector {
  return { taskId: "task-1", resultRef: "result-1", ...overrides };
}

function imageResponse(
  body: BodyInit | null = new Uint8Array([1, 2, 3]),
  contentType = "image/png",
  extraHeaders: Record<string, string> = {},
): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": contentType, ...extraHeaders },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function requestUrl(fetcher: ReturnType<typeof vi.fn<typeof fetch>>): URL {
  const call = fetcher.mock.calls[0];
  if (call === undefined) throw new Error("Expected an owned image request");
  return new URL(String(call[0]));
}

function errorKind(error: unknown): unknown {
  return error !== null && typeof error === "object" && "kind" in error
    ? error.kind
    : undefined;
}

describe("canvas owned image HTTP port", () => {
  it("exposes the factory reader and returns the exact owned image bytes", async () => {
    const bytes = new Uint8Array([137, 80, 78, 71, 0, 1, 2, 255]);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(imageResponse(bytes, "image/png"));
    const { run } = createHarness(fetcher);

    const blob = await run.read({ taskId: "task ?#雪", resultRef: "résultat image" }, new AbortController().signal);

    expect(run.isCurrent()).toBe(true);
    expect(blob.type).toBe("image/png");
    expect(Array.from(new Uint8Array(await blob.arrayBuffer()))).toEqual(Array.from(bytes));
    expect(fetcher).toHaveBeenCalledOnce();
    const url = requestUrl(fetcher);
    expect(url.origin + url.pathname)
      .toBe("http://127.0.0.1:4175/api/chapters/chapter-1/canvas-owned-results/r%C3%A9sultat%20image/image");
    expect([...url.searchParams.entries()]).toEqual([["task_id", "task ?#雪"]]);
    const init = fetcher.mock.calls[0]?.[1];
    expect(init).toMatchObject({
      method: "GET",
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });
    const headers = new Headers(init?.headers);
    expect(headers.get("authorization")).toBe("Bearer image-reader-token");
    expect(headers.get("accept")).toBe("image/png, image/jpeg, image/webp");
    expect(init?.signal).toBeInstanceOf(AbortSignal);
  });

  it.each(["image/png", "image/jpeg", "image/webp"])(
    "accepts chunked %s bytes when Content-Length is absent",
    async (contentType) => {
      const body = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array([11, 22]));
          controller.enqueue(new Uint8Array([33, 44]));
          controller.close();
        },
      }, { highWaterMark: 0 });
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(imageResponse(body, contentType));
      const { run } = createHarness(fetcher);

      const blob = await run.read(selector(), new AbortController().signal);

      expect(blob.type).toBe(contentType);
      expect(Array.from(new Uint8Array(await blob.arrayBuffer()))).toEqual([11, 22, 33, 44]);
    },
  );

  it("rejects invalid task or result identifiers before dispatch", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(imageResponse());
    const { run } = createHarness(fetcher);
    const invalidValues = ["", " leading", "trailing ", ".", "..", "bad/name", "bad\\name", "bad\u0000id", "😀".repeat(37), "\ud800"];

    for (const invalid of invalidValues) {
      await expect(run.read(selector({ resultRef: invalid }), new AbortController().signal))
        .rejects.toMatchObject({ kind: "not-sent" });
      await expect(run.read(selector({ taskId: invalid }), new AbortController().signal))
        .rejects.toMatchObject({ kind: "not-sent" });
    }
    await expect(run.read({ ...selector(), diagnosticUrl: "https://example.invalid/image" } as ImageSelector,
      new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not dispatch an already-aborted or stale-context read", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(imageResponse());
    const { run, setContextCurrent } = createHarness(fetcher);
    const aborted = new AbortController();
    aborted.abort();

    await expect(run.read(selector(), aborted.signal)).rejects.toMatchObject({ kind: "not-sent" });
    setContextCurrent(false);
    await expect(run.read(selector(), new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
    expect(run.isCurrent()).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects a late 401 after token rotation, cancels its body, and preserves the newer session", async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>(() => pending.promise);
    const { run, storage } = createHarness(fetcher);
    const read = run.read(selector(), new AbortController().signal);
    const cancel = vi.fn();
    const lateBody = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array([1])); },
      cancel,
    }, { highWaterMark: 0 });

    expect(fetcher).toHaveBeenCalledOnce();
    storage.setItem(API_TOKEN_KEY, "new-session-token");
    pending.resolve(new Response(lateBody, { status: 401 }));

    await expect(read).rejects.toMatchObject({ kind: "read-error" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-session-token");
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("rejects a late response after the stored actor changes without clearing its token", async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>(() => pending.promise);
    const { run, storage } = createHarness(fetcher);
    const read = run.read(selector(), new AbortController().signal);
    const cancel = vi.fn();
    const lateBody = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array([1])); },
      cancel,
    }, { highWaterMark: 0 });

    storage.setItem(API_USER_KEY, JSON.stringify({ id: "user-2" }));
    pending.resolve(new Response(lateBody, { status: 401 }));

    await expect(read).rejects.toMatchObject({ kind: "read-error" });
    expect(storage.getItem(API_TOKEN_KEY)).toBe("image-reader-token");
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("cancels a late image body when the captured chapter changes", async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>(() => pending.promise);
    const { run, context } = createHarness(fetcher);
    const read = run.read(selector(), new AbortController().signal);
    const cancel = vi.fn();
    const lateBody = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array([1, 2, 3])); },
      cancel,
    }, { highWaterMark: 0 });

    context.chapterId = "chapter-2";
    pending.resolve(imageResponse(lateBody));

    await expect(read).rejects.toMatchObject({ kind: "read-error" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(run.isCurrent()).toBe(false);
  });

  it.each(["actorId", "seriesId", "chapterId"] as const)(
    "does not dispatch when captured %s changes before a read",
    async (field) => {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(imageResponse());
      const { run, context } = createHarness(fetcher);
      context[field] = `${context[field]}-changed`;

      await expect(run.read(selector(), new AbortController().signal)).rejects.toMatchObject({ kind: "not-sent" });
      expect(run.isCurrent()).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it("cancels a late response after caller abort and never retries the GET", async () => {
    const pending = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>(() => pending.promise);
    const { run } = createHarness(fetcher);
    const controller = new AbortController();
    const read = run.read(selector(), controller.signal);
    const cancel = vi.fn();
    const lateBody = new ReadableStream<Uint8Array>({
      start(streamController) { streamController.enqueue(new Uint8Array([1, 2])); },
      cancel,
    }, { highWaterMark: 0 });

    controller.abort();
    await expect(read).rejects.toMatchObject({ kind: "read-error" });
    pending.resolve(imageResponse(lateBody));
    await Promise.resolve();

    expect(fetcher).toHaveBeenCalledOnce();
    expect(cancel).toHaveBeenCalledOnce();
  });

  it.each(["line\r\nbreak", "token雪"])(
    "fails closed when the captured bearer token cannot be represented in HTTP headers",
    (token) => {
      const storage = new MemoryStorage();
      storage.setItem(API_TOKEN_KEY, token);
      storage.setItem(API_USER_KEY, JSON.stringify({ id: "user-1" }));
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(imageResponse());
      const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
      const port = services.canvasOwnedImage;
      if (port === undefined) throw new Error("API services did not expose canvasOwnedImage");

      let capturedError: unknown;
      try {
        port.beginRun({
          actorId: "user-1",
          seriesId: "series-1",
          chapterId: "chapter-1",
          isContextCurrent: () => true,
        });
      } catch (error) {
        capturedError = error;
      }

      expect(capturedError).toMatchObject({ kind: "not-sent" });
      expect((capturedError as Error).message).not.toContain(token);
      expect(fetcher).not.toHaveBeenCalled();
    },
  );

  it.each(["throw", "return", "reject"] as const)(
    "handles a synchronous caller abort followed by fetcher %s without an unhandled rejection or listener leak",
    async (fetchBehavior) => {
      vi.useFakeTimers();
      const controller = new AbortController();
      const addListener = vi.spyOn(controller.signal, "addEventListener");
      const removeListener = vi.spyOn(controller.signal, "removeEventListener");
      const fetcher = vi.fn<typeof fetch>(() => {
        controller.abort();
        if (fetchBehavior === "throw") throw new Error("transport throw after abort");
        if (fetchBehavior === "return") return Promise.resolve(imageResponse());
        return Promise.reject(new Error("rejected transport after abort"));
      });
      const { run } = createHarness(fetcher);
      const unhandled: unknown[] = [];
      const onUnhandledRejection = (reason: unknown) => unhandled.push(reason);
      process.on("unhandledRejection", onUnhandledRejection);

      try {
        await expect(run.read(selector(), controller.signal)).rejects.toMatchObject({ kind: "read-error" });
        await vi.advanceTimersByTimeAsync(0);
        expect(unhandled).toEqual([]);
        expect(vi.getTimerCount()).toBe(0);
        expect(fetcher).toHaveBeenCalledOnce();
        const registered = addListener.mock.calls.find(([eventName]) => eventName === "abort")?.[1];
        expect(registered).toBeDefined();
        expect(removeListener).toHaveBeenCalledWith("abort", registered);
      } finally {
        process.removeListener("unhandledRejection", onUnhandledRejection);
        vi.useRealTimers();
      }
    },
  );

  it("cancels the response body for HTTP failures and exposes safe status errors", async () => {
    for (const [status, kind] of [
      [401, "unauthorized"],
      [403, "forbidden"],
      [404, "not-found"],
      [409, "unready"],
      [202, "invalid-response"],
      [503, "unavailable"],
    ] as const) {
      const cancel = vi.fn();
      const body = new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new Uint8Array([1])); },
        cancel,
      }, { highWaterMark: 0 });
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status }));
      const { run } = createHarness(fetcher);

      await expect(run.read(selector(), new AbortController().signal)).rejects.toMatchObject({ kind, status });
      expect(cancel).toHaveBeenCalledOnce();
    }
  });

  it("ends at the total deadline when cancelling an HTTP error body never settles", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn(() => new Promise<void>(() => undefined));
    const response = new Response(null, { status: 503 });
    Object.defineProperty(response, "body", { value: { cancel } });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
    const { run } = createHarness(fetcher);
    const read = run.read(selector(), new AbortController().signal);
    const outcome = read.then(
      (blob) => ({ ok: true as const, blob }),
      (error: unknown) => ({ ok: false as const, error }),
    );

    try {
      await vi.advanceTimersByTimeAsync(35_000);
      const result = await outcome;
      expect(result.ok).toBe(false);
      if (!result.ok) expect(errorKind(result.error)).toBe("read-error");
      expect(cancel).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ["text/html", {}],
    ["image/svg+xml", {}],
    ["image/png; charset=binary", {}],
    ["image/png", { "Content-Length": "not-a-number" }],
    ["image/png", { "Content-Length": "3, 3" }],
    ["image/png", { "Content-Length": String(IMAGE_LIMIT + 1) }],
    ["image/png", { "Content-Length": "-1" }],
    ["image/png", { "Content-Length": "1.5" }],
  ])("rejects unsupported response metadata (%s)", async (contentType, headers) => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array([1, 2, 3])); },
      cancel,
    }, { highWaterMark: 0 });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(imageResponse(body, contentType, headers));
    const { run } = createHarness(fetcher);

    await expect(run.read(selector(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response" });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it("rejects empty bodies, mismatched declared lengths, and streamed bodies over 10 MiB", async () => {
    const overflowCancel = vi.fn();
    let chunks = 0;
    const overflow = new ReadableStream<Uint8Array>({
      pull(controller) {
        if (chunks++ === 0) controller.enqueue(new Uint8Array(IMAGE_LIMIT));
        else if (chunks === 2) controller.enqueue(new Uint8Array([1]));
        else controller.close();
      },
      cancel: overflowCancel,
    }, { highWaterMark: 0 });
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(imageResponse(new Uint8Array()))
      .mockResolvedValueOnce(imageResponse(new Uint8Array([1, 2]), "image/png", { "Content-Length": "3" }))
      .mockResolvedValueOnce(imageResponse(overflow));
    const { run } = createHarness(fetcher);

    for (let index = 0; index < 3; index += 1) {
      await expect(run.read(selector(), new AbortController().signal))
        .rejects.toMatchObject({ kind: "invalid-response" });
    }
    expect(overflowCancel).toHaveBeenCalled();
  });

  it("uses one 35-second deadline for a fetcher that ignores abort and cancels a late body", async () => {
    vi.useFakeTimers();
    const pending = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>(() => pending.promise);
    const { run } = createHarness(fetcher);
    const read = run.read(selector(), new AbortController().signal);
    const outcome = read.then(
      (blob) => ({ ok: true as const, blob }),
      (error: unknown) => ({ ok: false as const, error }),
    );
    const cancel = vi.fn();

    try {
      await vi.advanceTimersByTimeAsync(35_000);
      const lateBody = new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new Uint8Array([1])); },
        cancel,
      }, { highWaterMark: 0 });
      pending.resolve(imageResponse(lateBody));
      const result = await outcome;
      expect(result.ok).toBe(false);
      if (!result.ok) expect(errorKind(result.error)).toBe("read-error");
      expect(cancel).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it("enforces the same deadline while a response body read is pending", async () => {
    vi.useFakeTimers();
    const pendingFetch = deferred<Response>();
    const pendingRead = deferred<ReadableStreamReadResult<Uint8Array>>();
    const readStarted = deferred<void>();
    const cancel = vi.fn(() => pendingRead.resolve({ done: true, value: undefined }));
    const reader = {
      read: vi.fn(() => {
        readStarted.resolve();
        return pendingRead.promise;
      }),
      cancel,
      releaseLock: vi.fn(),
    };
    const response = new Response(null, { status: 200, headers: { "Content-Type": "image/png" } });
    Object.defineProperty(response, "body", {
      value: { getReader: () => reader, cancel: () => cancel() },
    });
    const fetcher = vi.fn<typeof fetch>(() => pendingFetch.promise);
    const { run } = createHarness(fetcher);
    const read = run.read(selector(), new AbortController().signal);
    const outcome = read.then(
      (blob) => ({ ok: true as const, blob }),
      (error: unknown) => ({ ok: false as const, error }),
    );

    try {
      await vi.advanceTimersByTimeAsync(20_000);
      pendingFetch.resolve(response);
      await readStarted.promise;
      await vi.advanceTimersByTimeAsync(15_000);
      const result = await outcome;
      expect(result.ok).toBe(false);
      if (!result.ok) expect(errorKind(result.error)).toBe("read-error");
      expect(reader.read).toHaveBeenCalledOnce();
      expect(cancel).toHaveBeenCalled();
      expect(reader.releaseLock).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });
});
