// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  CanvasImageGenerationRunContext,
  CanvasImageGenerationSubmitInput,
} from "./canvasImageGeneration";
import { createApiServices } from "./services";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  type StorageLike,
} from "./storage";
import type { CanvasOwnedResultsTarget } from "./canvasOwnedResults";

const API_BASE_URL = "http://127.0.0.1:4175/api";
const CHAPTER_ID = "chapter-1";
const SHA256 = "a".repeat(64);

const stableFrame: CanvasOwnedResultsTarget = {
  kind: "frame_preview",
  chapter_id: CHAPTER_ID,
  storyboard_asset_id: "storyboard-7",
};

const noIdFrame: CanvasOwnedResultsTarget = {
  kind: "frame_preview",
  chapter_id: CHAPTER_ID,
  identity_mode: "strict-no-id-v1",
  frame_index: 2,
  expected_chapter_digest: SHA256,
};

const characterImage: CanvasOwnedResultsTarget = {
  kind: "asset_image",
  chapter_id: CHAPTER_ID,
  asset_type: "character",
  asset_id: "character-5",
};

const sceneImage: CanvasOwnedResultsTarget = {
  kind: "asset_image",
  chapter_id: CHAPTER_ID,
  asset_type: "scene",
  asset_id: "scene-5",
};

const propImage: CanvasOwnedResultsTarget = {
  kind: "asset_image",
  chapter_id: CHAPTER_ID,
  asset_type: "prop",
  asset_id: "prop-5",
};

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function receiptWire(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    submission_id: "submission-1",
    operation: "image.single",
    task_id: "task-1",
    message_id: null,
    status: "queued",
    quoted_amount: 12,
    billing_status: "unbilled",
    ...overrides,
  };
}

function jsonResponse(
  value: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function makeInput(
  overrides: Partial<CanvasImageGenerationSubmitInput> = {},
): CanvasImageGenerationSubmitInput {
  return {
    modelId: "image-model-1",
    prompt: "A scene\nin blue light",
    destination: stableFrame,
    idempotencyKey: "canvas+run 1",
    ...overrides,
  };
}

function createHarness(fetcher: typeof fetch, timeoutMs?: number) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "captured-api-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "user-1" }));
  const services = createApiServices(API_BASE_URL, {
    storage,
    fetcher,
    ...(timeoutMs === undefined ? {} : { timeoutMs }),
  });
  const port = services.canvasImageGeneration;
  if (port === undefined) throw new Error("API services did not expose canvasImageGeneration");

  let contextCurrent = true;
  const context: CanvasImageGenerationRunContext = {
    actorId: "user-1",
    seriesId: "series-1",
    chapterId: CHAPTER_ID,
    isContextCurrent: () => contextCurrent,
  };
  const run = port.beginRun(context);
  return {
    services,
    storage,
    context,
    run,
    setContextCurrent(value: boolean) { contextCurrent = value; },
  };
}

function requestParts(fetcher: ReturnType<typeof vi.fn<typeof fetch>>) {
  const call = fetcher.mock.calls[0];
  if (call === undefined) throw new Error("Expected one fetch call");
  return {
    url: new URL(String(call[0])),
    init: call[1] ?? {},
  };
}

function assertSecurityOptions(init: RequestInit): void {
  expect(init.credentials).toBe("omit");
  expect(init.cache).toBe("no-store");
  expect(init.redirect).toBe("error");
  expect(init.referrerPolicy).toBe("no-referrer");
  expect(init.signal).toBeInstanceOf(AbortSignal);
}

describe("Canvas image generation HTTP port", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("is wired into the real API services factory", () => {
    const services = createApiServices(API_BASE_URL, {
      storage: new MemoryStorage(),
      fetcher: vi.fn<typeof fetch>(),
    });

    expect("canvasImageGeneration" in services).toBe(true);
  });

  it.each([
    ["stable frame", stableFrame],
    ["strict no-ID frame", noIdFrame],
    ["character", characterImage],
    ["scene", sceneImage],
    ["prop", propImage],
  ] as const)("submits one exact image.single request for a %s destination", async (_name, destination) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire({
      message_id: "message-1",
      billing_status: "partially_refunded",
    })));
    const harness = createHarness(fetcher);
    const input = makeInput({ destination });

    await expect(harness.run.submit(input, new AbortController().signal)).resolves.toEqual({
      submissionId: "submission-1",
      operation: "image.single",
      taskId: "task-1",
      messageId: "message-1",
      status: "queued",
      quotedAmount: 12,
      billingStatus: "partially_refunded",
    });

    expect(fetcher).toHaveBeenCalledTimes(1);
    const { url, init } = requestParts(fetcher);
    expect(url.href).toBe(`${API_BASE_URL}/chat/image`);
    expect(init.method).toBe("POST");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer captured-api-token");
    expect(headers.get("Idempotency-Key")).toBe(input.idempotencyKey);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({
      model_id: input.modelId,
      prompt: input.prompt,
      target: { kind: "canvas_apply", destination },
    });
    expect(Object.keys(JSON.parse(String(init.body)))).toEqual(["model_id", "prompt", "target"]);
    assertSecurityOptions(init);
  });

  it("passes a safe configured size verbatim and omits an explicitly undefined size", async () => {
    for (const size of ["16:9", "quality:high"]) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
      const harness = createHarness(fetcher);
      const input = makeInput({ size });

      await harness.run.submit(input, new AbortController().signal);

      const { init } = requestParts(fetcher);
      expect(JSON.parse(String(init.body))).toEqual({
        model_id: input.modelId,
        prompt: input.prompt,
        size,
        target: { kind: "canvas_apply", destination: input.destination },
      });
    }

    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
    const harness = createHarness(fetcher);
    const inputWithUndefinedSize = { ...makeInput(), size: undefined };
    await harness.run.submit(inputWithUndefinedSize as unknown as CanvasImageGenerationSubmitInput, new AbortController().signal);
    expect(JSON.parse(String(requestParts(fetcher).init.body))).not.toHaveProperty("size");
  });

  it.each([
    "queued",
    "processing",
    "cancelling",
    "reconciling",
    "completed",
    "failed",
  ] as const)("accepts the public task status %s", async (status) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire({ status })));
    const harness = createHarness(fetcher);

    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .resolves.toMatchObject({ status });
  });

  it.each([
    "legacy_unreconciled",
    "unbilled",
    "charged",
    "partially_refunded",
    "refunded",
    "no_charge",
  ] as const)("accepts the public billing status %s without reconciling it locally", async (billingStatus) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire({ billing_status: billingStatus })));
    const harness = createHarness(fetcher);

    await expect(harness.run.lookup("canvas+run 1", new AbortController().signal))
      .resolves.toMatchObject({ billingStatus });
  });

  it("looks up only the exact original key and does not send a second admission", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
    const harness = createHarness(fetcher);
    const key = "canvas+run 1";

    await expect(harness.run.lookup(key, new AbortController().signal)).resolves.toMatchObject({
      submissionId: "submission-1",
      operation: "image.single",
    });

    expect(fetcher).toHaveBeenCalledTimes(1);
    const { url, init } = requestParts(fetcher);
    expect(url.pathname).toBe("/api/submissions/image.single");
    expect(url.search).toBe("?idempotency_key=canvas%2Brun+1");
    expect(url.searchParams.get("idempotency_key")).toBe(key);
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer captured-api-token");
    expect(headers.has("Idempotency-Key")).toBe(false);
    assertSecurityOptions(init);
  });

  it("rejects non-exact HTTP header keys without trimming or replacing them", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
    const harness = createHarness(fetcher);
    const base = makeInput();
    const invalidKeys = ["", "   ", " leading", "trailing ", "bad\nkey", "x".repeat(256), "wide-\u0100"];

    for (const idempotencyKey of invalidKeys) {
      await expect(harness.run.submit(
        { ...base, idempotencyKey },
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "not-sent" });
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects malformed inputs and targets before dispatch", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
    const harness = createHarness(fetcher);
    const base = makeInput();
    const invalidInputs: unknown[] = [
      { ...base, modelId: " \n " },
      { ...base, modelId: "m".repeat(256) },
      { ...base, prompt: " \n\t" },
      { ...base, destination: { ...stableFrame, chapter_id: "chapter-other" } },
      { ...base, destination: { ...stableFrame, public_url: "https://invalid.example/image.png" } },
      { ...base, destination: { ...noIdFrame, expected_chapter_digest: "not-a-digest" } },
      { ...base, destination: { ...characterImage, asset_type: "storyboard" } },
      { ...base, reference_images: ["https://invalid.example/ref.png"] },
      { ...base, size: null },
      { ...base, size: 42 },
      { ...base, size: "" },
      { ...base, size: "   " },
      { ...base, size: "s".repeat(101) },
      { ...base, size: "https://invalid.example/size" },
      { ...base, size: "bad\nsize" },
    ];

    for (const input of invalidInputs) {
      await expect(harness.run.submit(
        input as CanvasImageGenerationSubmitInput,
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "not-sent" });
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it.each([400, 401, 403, 422])("classifies POST %i as a definite rejection without clearing session", async (status) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "rejected" }, status));
    const harness = createHarness(fetcher);

    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "rejected", status });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(harness.storage.getItem(API_TOKEN_KEY)).toBe("captured-api-token");
    expect(harness.storage.getItem(API_USER_KEY)).toBe(JSON.stringify({ id: "user-1" }));
  });

  it("treats POST 5xx and unexpected successful status as unknown without replay", async () => {
    for (const status of [500, 503, 504, 201]) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire(), status));
      const harness = createHarness(fetcher);
      await expect(harness.run.submit(makeInput(), new AbortController().signal))
        .rejects.toMatchObject({ kind: "unknown-outcome", status });
      expect(fetcher).toHaveBeenCalledTimes(1);
    }
  });

  it.each([
    ["malformed JSON", "not-json"],
    ["wrong operation", receiptWire({ operation: "video.single" })],
    ["extra receipt field", receiptWire({ provider_url: "https://invalid.example/result" })],
    ["unknown task status", receiptWire({ status: "running" })],
    ["unknown billing status", receiptWire({ billing_status: "settled-later" })],
    ["invalid amount", receiptWire({ quoted_amount: 1.5 })],
  ] as const)("treats a successful response with %s as unknown", async (_case, payload) => {
    const response = typeof payload === "string"
      ? new Response(payload, { status: 200 })
      : jsonResponse(payload);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response);
    const harness = createHarness(fetcher);

    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown-outcome", status: 200 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("does not let a late 401 from a replaced token clear the new session", async () => {
    let resolveFetch: (response: Response) => void = () => {};
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    }));
    const harness = createHarness(fetcher);
    const pending = harness.run.submit(makeInput(), new AbortController().signal);

    harness.storage.setItem(API_TOKEN_KEY, "replacement-token");
    resolveFetch(jsonResponse({ detail: "expired" }, 401));

    await expect(pending).rejects.toMatchObject({ kind: "unknown-outcome" });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(harness.storage.getItem(API_TOKEN_KEY)).toBe("replacement-token");
  });

  it("rejects a late success after the caller invalidates the captured canvas context", async () => {
    let resolveFetch: (response: Response) => void = () => {};
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    }));
    const harness = createHarness(fetcher);
    const pending = harness.run.submit(makeInput(), new AbortController().signal);

    harness.setContextCurrent(false);
    resolveFetch(jsonResponse(receiptWire()));

    await expect(pending).rejects.toMatchObject({ kind: "unknown-outcome" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("does not dispatch with an already-aborted signal or stale run context", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
    const harness = createHarness(fetcher);
    const aborted = new AbortController();
    aborted.abort();

    await expect(harness.run.submit(makeInput(), aborted.signal)).rejects.toMatchObject({ kind: "not-sent" });
    harness.setContextCurrent(false);
    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "not-sent" });
    expect(harness.run.isCurrent()).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects changed actor, series, and chapter identities before sending", async () => {
    for (const identity of ["actorId", "seriesId", "chapterId"] as const) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
      const harness = createHarness(fetcher);
      Object.assign(harness.context, { [identity]: `${identity}-changed` });

      await expect(harness.run.submit(makeInput(), new AbortController().signal))
        .rejects.toMatchObject({ kind: "not-sent" });
      expect(harness.run.isCurrent()).toBe(false);
      expect(fetcher).not.toHaveBeenCalled();
    }
  });

  it("rejects a Bearer token that cannot be represented exactly in an HTTP header", () => {
    const storage = new MemoryStorage();
    const fetcher = vi.fn<typeof fetch>();
    storage.setItem(API_TOKEN_KEY, "token-\u0100");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "user-1" }));
    const services = createApiServices(API_BASE_URL, {
      storage,
      fetcher,
    });
    const context: CanvasImageGenerationRunContext = {
      actorId: "user-1",
      seriesId: "series-1",
      chapterId: CHAPTER_ID,
      isContextCurrent: () => true,
    };
    let error: unknown;
    try {
      services.canvasImageGeneration?.beginRun(context);
    } catch (caught) {
      error = caught;
    }

    expect(error).toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("reports synchronous request-header failures as not sent", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire()));
    const harness = createHarness(fetcher);
    const NativeHeaders = globalThis.Headers;
    vi.stubGlobal("Headers", class extends NativeHeaders {
      constructor(init?: HeadersInit) {
        if (new NativeHeaders(init).has("Authorization")) {
          throw new TypeError("synthetic invalid authorization header");
        }
        super(init);
      }
    });

    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .rejects.toMatchObject({
        kind: "not-sent",
        message: "请求校验未通过，未发送请求。",
      });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("returns a point-in-time lookup miss and keeps the captured session", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "missing" }, 404));
    const harness = createHarness(fetcher);

    await expect(harness.run.lookup("canvas+run 1", new AbortController().signal))
      .rejects.toMatchObject({ kind: "not-found", status: 404 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(harness.storage.getItem(API_TOKEN_KEY)).toBe("captured-api-token");
  });

  it.each([
    [401, "unauthorized"],
    [403, "forbidden"],
    [503, "unavailable"],
  ] as const)("classifies read-only lookup status %i as %s", async (status, kind) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: "lookup failed" }, status));
    const harness = createHarness(fetcher);

    await expect(harness.run.lookup("canvas+run 1", new AbortController().signal))
      .rejects.toMatchObject({ kind, status });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(harness.storage.getItem(API_TOKEN_KEY)).toBe("captured-api-token");
  });

  it("rejects a lookup receipt with extra fields as an invalid response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(receiptWire({ url: "https://invalid.example" })));
    const harness = createHarness(fetcher);

    await expect(harness.run.lookup("canvas+run 1", new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("treats a fetch transport failure after POST dispatch as unknown and never retries", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("synthetic network failure"));
    const harness = createHarness(fetcher);

    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown-outcome" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("treats the default 15 second deadline while reading a POST body as unknown and cancels it", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{"));
      },
      cancel() {
        cancelled = true;
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 200 }));
    const harness = createHarness(fetcher);
    vi.useFakeTimers();

    const pending = harness.run.submit(makeInput(), new AbortController().signal);
    const rejection = expect(pending).rejects.toMatchObject({ kind: "unknown-outcome" });
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(15_000);
    await rejection;
    expect(cancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("ends a fetch that ignores abort at 15 seconds and cancels its late response body", async () => {
    let resolveFetch: (response: Response) => void = () => {};
    let lateBodyCancelled = false;
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    }));
    const harness = createHarness(fetcher);
    vi.useFakeTimers();

    const pending = harness.run.submit(makeInput(), new AbortController().signal);
    const rejection = expect(pending).rejects.toMatchObject({ kind: "unknown-outcome" });
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(15_000);
    await rejection;
    expect(fetcher).toHaveBeenCalledTimes(1);

    const lateBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(JSON.stringify(receiptWire())));
      },
      cancel() {
        lateBodyCancelled = true;
      },
    });
    resolveFetch(new Response(lateBody, { status: 200 }));
    await Promise.resolve();
    await Promise.resolve();
    expect(lateBodyCancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("bounds a never-settling error response cancellation by the 15 second deadline", async () => {
    let cancellationWasStarted = false;
    let signalCancellationStarted: () => void = () => undefined;
    const cancellationStarted = new Promise<void>((resolve) => {
      signalCancellationStarted = resolve;
    });
    const body = new ReadableStream<Uint8Array>({
      cancel() {
        cancellationWasStarted = true;
        signalCancellationStarted();
        return new Promise<void>(() => {});
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 503 }));
    const harness = createHarness(fetcher);
    vi.useFakeTimers();

    const pending = harness.run.submit(makeInput(), new AbortController().signal);
    const rejection = expect(pending).rejects.toMatchObject({ kind: "unknown-outcome", status: 503 });
    try {
      await cancellationStarted;
      expect(cancellationWasStarted).toBe(true);
      await vi.advanceTimersByTimeAsync(15_000);
      await rejection;
      expect(fetcher).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("treats caller abort after POST dispatch as unknown and cancels a late response", async () => {
    let resolveFetch: (response: Response) => void = () => {};
    let lateBodyCancelled = false;
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    }));
    const harness = createHarness(fetcher);
    const controller = new AbortController();
    const pending = harness.run.submit(makeInput(), controller.signal);
    const rejection = expect(pending).rejects.toMatchObject({ kind: "unknown-outcome" });

    controller.abort();
    await rejection;
    const lateBody = new ReadableStream<Uint8Array>({
      start(streamController) {
        streamController.enqueue(new TextEncoder().encode(JSON.stringify(receiptWire())));
      },
      cancel() {
        lateBodyCancelled = true;
      },
    });
    resolveFetch(new Response(lateBody, { status: 200 }));
    await Promise.resolve();
    await Promise.resolve();
    expect(lateBodyCancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("classifies the default lookup deadline as read-only without clearing the session", async () => {
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>(() => {}));
    const harness = createHarness(fetcher);
    vi.useFakeTimers();

    const pending = harness.run.lookup("canvas+run 1", new AbortController().signal);
    const rejection = expect(pending).rejects.toMatchObject({ kind: "read-error" });
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(15_000);
    await rejection;
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(harness.storage.getItem(API_TOKEN_KEY)).toBe("captured-api-token");
  });

  it("rejects a streamed POST body above 512 KiB and cancels the response", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(512 * 1024 + 1));
      },
      cancel() {
        cancelled = true;
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 200 }));
    const harness = createHarness(fetcher);

    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown-outcome", status: 200 });
    expect(cancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects a declared response size above 512 KiB before reading the body", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("{}"));
      },
      cancel() {
        cancelled = true;
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, {
      status: 200,
      headers: { "Content-Length": String(512 * 1024 + 1) },
    }));
    const harness = createHarness(fetcher);

    await expect(harness.run.submit(makeInput(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown-outcome", status: 200 });
    expect(cancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects an oversized point lookup response without treating it as a submission outcome", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(512 * 1024 + 1));
      },
      cancel() {
        cancelled = true;
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status: 200 }));
    const harness = createHarness(fetcher);

    await expect(harness.run.lookup("canvas+run 1", new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    expect(cancelled).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
