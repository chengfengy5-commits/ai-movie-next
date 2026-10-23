import { describe, expect, it, vi } from "vitest";
import { CanvasDataError } from "./canvasData";
import { createCanvasDataHttpPort } from "./canvasDataHttp";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

function memoryStorage(token = "canvas-test-token"): StorageLike {
  const values = new Map([[API_TOKEN_KEY, token], [API_USER_KEY, JSON.stringify({ id: "actor-a" })]]);
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

function payload(overrides: Record<string, unknown> = {}) {
  return {
    id: null,
    chapter_id: "chapter/a",
    version: 1,
    document_json: { notes: [], unknown: { preserve: true } },
    updated_by: null,
    updated_at: null,
    ...overrides,
  };
}

describe("canvas data HTTP adapter", () => {
  it("negotiates before writing and validates the same-write receipt", async () => {
    const document = { notes: [{ id: "note-a", text: "draft" }], unknown: { keep: 0 } };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(payload({ save_capability: "guarded-v1" })))
      .mockResolvedValueOnce(Response.json(payload({
        id: "canvas-a",
        version: 1,
        document_json: document,
        updated_by: "actor-a",
        updated_at: "2026-10-22T04:00:00Z",
        save_receipt: { mode: "guarded-v1", id: "canvas-a", version: 1 },
      })));
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });

    const initial = await port.get("chapter/a", new AbortController().signal);
    expect(initial.save_capability).toBe("guarded-v1");
    const saved = await port.save(
      "chapter/a",
      document,
      { mode: "guarded-v1", id: null, version: 1 },
      new AbortController().signal,
    );

    const getUrl = new URL(String(fetcher.mock.calls[0]?.[0]));
    expect(getUrl.pathname).toBe("/api/chapters/chapter%2Fa/canvas");
    expect(getUrl.searchParams.get("save_mode")).toBe("guarded-v1");
    const putInit = fetcher.mock.calls[1]?.[1];
    expect(putInit?.method).toBe("PUT");
    expect(JSON.parse(String(putInit?.body))).toEqual({
      document_json: document,
      version: 1,
      save_guard: { mode: "guarded-v1", id: null, version: 1 },
    });
    expect(new Headers(putInit?.headers).get("Authorization")).toBe("Bearer canvas-test-token");
    expect(putInit?.credentials).toBe("omit");
    expect(saved.save_receipt?.version).toBe(1);
  });

  it("never sends a PUT when the server did not advertise guarded saving", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(payload()));
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });
    await port.get("chapter/a", new AbortController().signal);
    await expect(port.save(
      "chapter/a",
      { notes: [] },
      { mode: "guarded-v1", id: null, version: 1 },
      new AbortController().signal,
    )).rejects.toMatchObject({ kind: "unsupported" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("treats a malformed receipt as unknown and a 409 as conflict", async () => {
    const document = { notes: [] };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(Response.json(payload({ save_capability: "guarded-v1" })))
      .mockResolvedValueOnce(Response.json(payload({
        id: "canvas-a",
        version: 2,
        document_json: document,
        updated_by: "actor-a",
        updated_at: "2026-10-22T04:00:00Z",
        save_receipt: { mode: "guarded-v1", id: "canvas-a", version: 3 },
      })))
      .mockResolvedValueOnce(new Response(null, { status: 409 }));
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });
    await port.get("chapter/a", new AbortController().signal);
    await expect(port.save("chapter/a", document, {
      mode: "guarded-v1", id: null, version: 1,
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "unknown" });
    await expect(port.save("chapter/a", document, {
      mode: "guarded-v1", id: null, version: 1,
    }, new AbortController().signal)).rejects.toMatchObject({ kind: "conflict" });
  });

  it("does not clear a replacement session after a delayed unauthorized response", async () => {
    const storage = memoryStorage("old-token");
    const fetcher = vi.fn<typeof fetch>(async () => {
      storage.setItem(API_TOKEN_KEY, "new-token");
      return new Response(null, { status: 401 });
    });
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    await expect(port.get("chapter/a", new AbortController().signal)).rejects.toBeInstanceOf(CanvasDataError);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("new-token");
  });

  it("reads same-origin uploads directly and sends remote images only to the authenticated proxy", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(new Uint8Array([1, 2, 3]), {
        headers: { "Content-Type": "image/png" },
      }))
      .mockResolvedValueOnce(new Response(new Uint8Array([4, 5, 6]), {
        headers: { "Content-Type": "image/png" },
      }));
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(),
      fetcher,
    });
    const signal = new AbortController().signal;
    expect((await port.readImage("/uploads/character.png", signal)).type).toBe("image/png");
    expect(new URL(String(fetcher.mock.calls[0]?.[0])).pathname).toBe("/uploads/character.png");
    expect((await port.readImage("https://images.example.test/storyboard.png", signal)).type).toBe("image/png");
    const proxied = new URL(String(fetcher.mock.calls[1]?.[0]));
    expect(proxied.pathname).toBe("/api/chat/proxy-image");
    expect(proxied.searchParams.get("url")).toBe("https://images.example.test/storyboard.png");
    expect(new Headers(fetcher.mock.calls[1]?.[1]?.headers).get("Authorization")).toBe("Bearer canvas-test-token");
    await expect(port.readImage("data:image/png;base64,AAAA", signal)).rejects.toMatchObject({ kind: "rejected" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("bounds streamed image reads, cancels rejected bodies, blocks redirects, and keeps BMP", async () => {
    const cancel = vi.fn();
    let chunksPulled = 0;
    const overflow = new Response(new ReadableStream<Uint8Array>({
      pull(controller) {
        if (chunksPulled === 0) {
          chunksPulled += 1;
          controller.enqueue(new Uint8Array(10 * 1024 * 1024));
        } else if (chunksPulled === 1) {
          chunksPulled += 1;
          controller.enqueue(new Uint8Array(1));
        }
        else controller.close();
      },
      cancel,
    }, { highWaterMark: 0 }), { headers: { "Content-Type": "image/png" } });
    const bmp = new Response(new Uint8Array([1, 2, 3]), { headers: { "Content-Type": "image/bmp" } });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(overflow).mockResolvedValueOnce(bmp);
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage: memoryStorage(), fetcher });
    await expect(port.readImage("/uploads/large.png", new AbortController().signal))
      .rejects.toMatchObject({ kind: "rejected" });
    expect(chunksPulled).toBe(2);
    expect(cancel).toHaveBeenCalled();
    expect(fetcher.mock.calls[0]?.[1]?.redirect).toBe("error");
    expect((await port.readImage("/uploads/old.bmp", new AbortController().signal)).type).toBe("image/bmp");
  });

  it("does not let an old GET body overwrite a newer token negotiation", async () => {
    const storage = memoryStorage("token-a");
    const oldBody = { release: undefined as ((value: unknown) => void) | undefined };
    let bodyStarted: (() => void) | null = null;
    const started = new Promise<void>((resolve) => { bodyStarted = resolve; });
    const oldResponse = Response.json(payload({ save_capability: "guarded-v1" }));
    oldResponse.json = () => {
      bodyStarted?.();
      return new Promise((resolve) => { oldBody.release = resolve; });
    };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(oldResponse)
      .mockResolvedValueOnce(Response.json(payload({ save_capability: "guarded-v1" })))
      .mockResolvedValueOnce(Response.json(payload({
        id: "canvas-a", version: 1, document_json: { notes: [] }, updated_by: "actor-a",
        updated_at: "2026-10-22T04:00:00Z",
        save_receipt: { mode: "guarded-v1", id: "canvas-a", version: 1 },
      })));
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    const oldController = new AbortController();
    const oldRead = port.get("chapter/a", oldController.signal);
    await started;
    oldController.abort();
    storage.setItem(API_TOKEN_KEY, "token-b");
    await port.get("chapter/a", new AbortController().signal);
    oldBody.release?.(payload({ save_capability: "guarded-v1" }));
    await expect(oldRead).rejects.toMatchObject({ kind: "rejected" });
    await expect(port.save("chapter/a", { notes: [] },
      { mode: "guarded-v1", id: null, version: 1 }, new AbortController().signal))
      .resolves.toMatchObject({ save_receipt: { mode: "guarded-v1", version: 1 } });
  });

  it("cancels unread image bodies on bad MIME, declared size, and HTTP errors", async () => {
    const cancelled: ReturnType<typeof vi.fn>[] = [];
    function streamedResponse(status: number, headers: Record<string, string>) {
      const cancel = vi.fn();
      cancelled.push(cancel);
      const stream = new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new Uint8Array([1, 2, 3])); },
        cancel,
      }, { highWaterMark: 0 });
      return new Response(stream, { status, headers });
    }
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(streamedResponse(200, { "Content-Type": "image/svg+xml" }))
      .mockResolvedValueOnce(streamedResponse(200, { "Content-Type": "image/png", "Content-Length": "10485761" }))
      .mockResolvedValueOnce(streamedResponse(503, { "Content-Type": "image/png" }));
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage: memoryStorage(), fetcher });
    for (const path of ["bad-mime", "oversized", "unavailable"]) {
      await expect(port.readImage("/uploads/" + path, new AbortController().signal)).rejects.toMatchObject({ kind: "rejected" });
    }
    expect(cancelled).toHaveLength(3);
    for (const cancel of cancelled) expect(cancel).toHaveBeenCalledOnce();
  });

  it("cleans up an image reader after caller abort and after the bounded timeout", async () => {
    function pendingResponse() {
      let resolveRead: ((result: ReadableStreamReadResult<Uint8Array>) => void) | null = null;
      const cancel = vi.fn(async () => { resolveRead?.({ done: true, value: undefined }); });
      const reader = {
        read: vi.fn(() => new Promise<ReadableStreamReadResult<Uint8Array>>((resolve) => { resolveRead = resolve; })),
        cancel,
      };
      const response = new Response(null, { headers: { "Content-Type": "image/png" } });
      Object.defineProperty(response, "body", {
        value: { getReader: () => reader, cancel },
      });
      return { response, cancel, reader };
    }

    const aborted = pendingResponse();
    const timeout = pendingResponse();
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(aborted.response)
      .mockResolvedValueOnce(timeout.response);
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage: memoryStorage(), fetcher });
    const controller = new AbortController();
    const abortedRead = port.readImage("/uploads/abort.png", controller.signal);
    await Promise.resolve();
    await Promise.resolve();
    controller.abort();
    await expect(abortedRead).rejects.toMatchObject({ name: "AbortError" });
    expect(aborted.cancel).toHaveBeenCalled();

    vi.useFakeTimers();
    try {
      const timeoutRead = port.readImage("/uploads/timeout.png", new AbortController().signal);
      const timeoutAssertion = expect(timeoutRead).rejects.toMatchObject({ kind: "rejected" });
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(15_000);
      await timeoutAssertion;
      expect(timeout.cancel).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects a timed-out image reader after it has already received bytes", async () => {
    vi.useFakeTimers();
    let resolvePendingRead: ((result: ReadableStreamReadResult<Uint8Array>) => void) | null = null;
    let markSecondReadStarted: (() => void) | null = null;
    const secondReadStarted = new Promise<void>((resolve) => { markSecondReadStarted = resolve; });
    const cancel = vi.fn(async () => {
      resolvePendingRead?.({ done: true, value: undefined });
    });
    const reader = {
      read: vi.fn<() => Promise<ReadableStreamReadResult<Uint8Array>>>()
        .mockResolvedValueOnce({ done: false, value: new Uint8Array([1, 2, 3]) })
        .mockImplementationOnce(() => {
          markSecondReadStarted?.();
          return new Promise((resolve) => { resolvePendingRead = resolve; });
        }),
      cancel,
    };
    const response = new Response(null, { headers: { "Content-Type": "image/png" } });
    Object.defineProperty(response, "body", { value: { getReader: () => reader, cancel } });
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", {
      storage: memoryStorage(), fetcher: vi.fn<typeof fetch>().mockResolvedValue(response),
    });

    try {
      const read = port.readImage("/uploads/partial.png", new AbortController().signal);
      const outcome = read.then(
        (blob) => ({ resolved: true as const, blob }),
        (error: unknown) => ({ resolved: false as const, error }),
      );
      await secondReadStarted;
      await vi.advanceTimersByTimeAsync(15_000);
      const result = await outcome;
      expect(result.resolved).toBe(false);
      if (!result.resolved) expect(result.error).toMatchObject({ kind: "rejected", message: expect.stringContaining("超时") });
      expect(reader.read).toHaveBeenCalledTimes(2);
      expect(cancel).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("rejects a valid image body returned after the internal timeout", async () => {
    vi.useFakeTimers();
    const deferredFetch: { resolve?: (response: Response) => void } = {};
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      deferredFetch.resolve = resolve;
    }));
    const port = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage: memoryStorage(), fetcher });

    try {
      const read = port.readImage("/uploads/late.png", new AbortController().signal);
      const outcome = read.then(
        (blob) => ({ resolved: true as const, blob }),
        (error: unknown) => ({ resolved: false as const, error }),
      );
      expect(fetcher).toHaveBeenCalledOnce();
      await vi.advanceTimersByTimeAsync(15_000);
      const cancel = vi.fn();
      const response = new Response(new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new Uint8Array([1, 2, 3])); controller.close(); },
        cancel,
      }, { highWaterMark: 0 }), { headers: { "Content-Type": "image/png" } });
      deferredFetch.resolve?.(response);
      const result = await outcome;
      expect(result.resolved).toBe(false);
      if (!result.resolved) expect(result.error).toMatchObject({ kind: "rejected", message: expect.stringContaining("超时") });
      expect(cancel).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
