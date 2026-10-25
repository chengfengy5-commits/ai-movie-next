// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { createApiServices } from "./services";
import {
  CanvasGenerationApplicationError,
  type CanvasGenerationApplicationCandidate,
  type CanvasGenerationApplicationDestination,
  type CanvasGenerationApplicationResult,
} from "./canvasGenerationApplications";
import type { CanvasOwnedResultTargetSnapshot } from "./canvasOwnedResults";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

const digest = "a".repeat(64);
const secondDigest = "b".repeat(64);

const stableFrame: CanvasGenerationApplicationDestination = {
  kind: "frame_preview",
  chapter_id: "chapter-1",
  storyboard_asset_id: "storyboard-7",
};
const noIdFrame: CanvasGenerationApplicationDestination = {
  kind: "frame_preview",
  chapter_id: "chapter-1",
  identity_mode: "strict-no-id-v1",
  frame_index: 2,
  expected_chapter_digest: digest,
};
const characterImage: CanvasGenerationApplicationDestination = {
  kind: "asset_image",
  chapter_id: "chapter-1",
  asset_type: "character",
  asset_id: "character-5",
};
const sceneImage: CanvasGenerationApplicationDestination = {
  kind: "asset_image",
  chapter_id: "chapter-1",
  asset_type: "scene",
  asset_id: "scene-5",
};
const propImage: CanvasGenerationApplicationDestination = {
  kind: "asset_image",
  chapter_id: "chapter-1",
  asset_type: "prop",
  asset_id: "prop-5",
};

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

function makeSnapshot(
  overrides: Partial<CanvasOwnedResultTargetSnapshot> = {},
): CanvasOwnedResultTargetSnapshot {
  return {
    kind: "canvas_frame_preview",
    chapter_id: "chapter-1",
    series_id: "series-1",
    stable_storyboard_asset_id: "storyboard-7",
    asset_type: "storyboard",
    asset_id: "storyboard-7",
    compat_frame_index: 2,
    expected_text_digest: null,
    media_identity: null,
    content_digest: null,
    notes_revision: null,
    full_frame_digest: digest,
    full_chapter_digest: digest,
    expected_asset_image_url: null,
    same_series_target_digest: null,
    ...overrides,
  };
}

function snapshotFor(destination: CanvasGenerationApplicationDestination): CanvasOwnedResultTargetSnapshot {
  if (destination.kind === "frame_preview" && "storyboard_asset_id" in destination) {
    return makeSnapshot({
      stable_storyboard_asset_id: destination.storyboard_asset_id,
      asset_type: "storyboard",
      asset_id: destination.storyboard_asset_id,
    });
  }
  if (destination.kind === "frame_preview") {
    return makeSnapshot({
      stable_storyboard_asset_id: null,
      asset_type: null,
      asset_id: null,
      compat_frame_index: destination.frame_index,
      full_chapter_digest: destination.expected_chapter_digest,
    });
  }
  return makeSnapshot({
    kind: "canvas_asset_image",
    stable_storyboard_asset_id: null,
    asset_type: destination.asset_type,
    asset_id: destination.asset_id,
    compat_frame_index: null,
    full_frame_digest: null,
    full_chapter_digest: null,
    same_series_target_digest: digest,
  });
}

function makeCandidate(
  destination: CanvasGenerationApplicationDestination = stableFrame,
  overrides: Partial<CanvasGenerationApplicationCandidate> = {},
): CanvasGenerationApplicationCandidate {
  return {
    task_id: "task-1",
    result_ref: "result-1",
    expected_target: snapshotFor(destination),
    application_key: digest,
    ...overrides,
  };
}

function makeResult(
  candidate: CanvasGenerationApplicationCandidate,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    task_id: candidate.task_id,
    result_ref: candidate.result_ref,
    status: "applied",
    application: {
      application_ref: "application-1",
      target_revision: digest,
      committed_at: "2026-10-25T00:00:00+00:00",
    },
    ...overrides,
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createHarness(fetcher: typeof fetch, timeoutMs = 15_000) {
  const storage = new MemoryStorage();
  storage.setItem(API_TOKEN_KEY, "captured-generation-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "generation-user" }));
  const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher, timeoutMs });
  const port = services.canvasGenerationApplications;
  if (port === undefined) throw new Error("API services did not expose canvasGenerationApplications");

  let contextCurrent = true;
  const context = {
    actorId: "generation-user",
    seriesId: "series-1",
    chapterId: "chapter-1",
    isContextCurrent: () => contextCurrent,
  };
  return {
    storage,
    port,
    run: port.beginRun(context),
    setContextCurrent(value: boolean) { contextCurrent = value; },
  };
}

function firstCall(fetcher: ReturnType<typeof vi.fn<typeof fetch>>): [URL, RequestInit] {
  const call = fetcher.mock.calls[0];
  if (call === undefined || call[1] === undefined) throw new Error("Expected an application request");
  return [new URL(String(call[0])), call[1]];
}

function proof(candidate: CanvasGenerationApplicationCandidate): CanvasGenerationApplicationResult {
  return {
    task_id: candidate.task_id,
    result_ref: candidate.result_ref,
    status: "applied",
    application: {
      application_ref: "application-1",
      target_revision: digest,
      committed_at: "2026-10-25T00:00:00+00:00",
    },
  };
}

describe("canvas generation applications HTTP port", () => {
  it("is exposed through the real API services factory", () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "captured-generation-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "generation-user" }));

    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn<typeof fetch>(),
    });

    expect(Reflect.get(services, "canvasGenerationApplications")).toBeDefined();
  });

  it.each([
    { name: "stable frame", destination: stableFrame, path: "/api/chapters/chapter-1" },
    { name: "strict no-ID frame", destination: noIdFrame, path: "/api/chapters/chapter-1" },
    { name: "character image", destination: characterImage, path: "/api/characters/character-5" },
    { name: "scene image", destination: sceneImage, path: "/api/scenes/scene-5" },
    { name: "prop image", destination: propImage, path: "/api/props/prop-5" },
  ])("sends one strict generation_application PUT for a $name", async ({ destination, path }) => {
    const candidate = makeCandidate(destination);
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(makeResult(candidate)));
    const { run } = createHarness(fetcher);

    await expect(run.apply(destination, candidate, new AbortController().signal)).resolves.toEqual(proof(candidate));

    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = firstCall(fetcher);
    expect(url.pathname).toBe(path);
    expect(url.search).toBe("");
    expect(init.method).toBe("PUT");
    expect(init.credentials).toBe("omit");
    expect(init.cache).toBe("no-store");
    expect(init.redirect).toBe("error");
    expect(init.referrerPolicy).toBe("no-referrer");
    expect(new Headers(init.headers).get("authorization")).toBe("Bearer captured-generation-token");
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(["generation_application"]);
    expect(body).toEqual({ generation_application: candidate });
    expect(JSON.stringify(body)).not.toContain("https://");
  });

  it("accepts the server's explicit already_applied receipt without a lookup or retry", async () => {
    const candidate = makeCandidate();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(makeResult(candidate, {
      status: "already_applied",
    })));
    const { run } = createHarness(fetcher);

    const result = await run.apply(stableFrame, candidate, new AbortController().signal);

    expect(result.status).toBe("already_applied");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed destinations and original candidates before dispatch", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const { run } = createHarness(fetcher);
    const missingNullableKey = Object.fromEntries(Object.entries(snapshotFor(stableFrame))
      .filter(([key]) => key !== "media_identity"));
    const cases: Array<{
      destination: unknown;
      candidate: unknown;
    }> = [
      { destination: { ...stableFrame, chapter_id: "chapter-2" }, candidate: makeCandidate() },
      { destination: { ...stableFrame, storyboard_asset_id: "bad/id" }, candidate: makeCandidate() },
      { destination: { ...stableFrame, identity_mode: "strict-no-id-v1" }, candidate: makeCandidate() },
      { destination: { kind: "video", chapter_id: "chapter-1" }, candidate: makeCandidate() },
      { destination: stableFrame, candidate: makeCandidate(stableFrame, {
        expected_target: makeSnapshot({ series_id: "series-2" }),
      }) },
      { destination: noIdFrame, candidate: makeCandidate(noIdFrame, {
        expected_target: { ...snapshotFor(noIdFrame), asset_id: "stray-asset" } as CanvasOwnedResultTargetSnapshot,
      }) },
      { destination: stableFrame, candidate: makeCandidate(stableFrame, {
        expected_target: missingNullableKey as unknown as CanvasOwnedResultTargetSnapshot,
      }) },
      { destination: stableFrame, candidate: makeCandidate(stableFrame, {
        expected_target: { ...snapshotFor(stableFrame), source_url: "https://example.invalid/private.png" } as CanvasOwnedResultTargetSnapshot,
      }) },
      { destination: stableFrame, candidate: makeCandidate(stableFrame, {
        expected_target: makeSnapshot({ stable_storyboard_asset_id: "another-storyboard" }),
      }) },
      { destination: characterImage, candidate: makeCandidate(characterImage, {
        expected_target: { ...snapshotFor(characterImage), asset_id: "another-character" },
      }) },
      { destination: stableFrame, candidate: makeCandidate(stableFrame, { application_key: "A".repeat(64) }) },
      { destination: stableFrame, candidate: makeCandidate(stableFrame, { task_id: "bad/id" }) },
    ];

    for (const { destination, candidate } of cases) {
      await expect(run.apply(
        destination as CanvasGenerationApplicationDestination,
        candidate as CanvasGenerationApplicationCandidate,
        new AbortController().signal,
      )).rejects.toMatchObject({ kind: "not-sent" });
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("classifies known 4xx and unknown 5xx responses without retry or session mutation", async () => {
    const statuses = [
      { status: 401, kind: "unauthorized" },
      { status: 403, kind: "forbidden" },
      { status: 404, kind: "not-found" },
      { status: 409, kind: "conflict" },
      { status: 422, kind: "invalid-request" },
      { status: 400, kind: "unknown-outcome" },
      { status: 500, kind: "unknown-outcome" },
      { status: 503, kind: "unknown-outcome" },
    ] as const;

    for (const { status, kind } of statuses) {
      const candidate = makeCandidate();
      let bodyCancelled = false;
      const responseBody = new ReadableStream<Uint8Array>({
        start(stream) { stream.enqueue(new TextEncoder().encode("private response")); },
        cancel() { bodyCancelled = true; },
      });
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(responseBody, { status }));
      const { run, storage } = createHarness(fetcher);

      await expect(run.apply(stableFrame, candidate, new AbortController().signal))
        .rejects.toMatchObject({ kind, status } satisfies Partial<CanvasGenerationApplicationError>);

      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(bodyCancelled).toBe(true);
      expect(firstCall(fetcher)[1].method).toBe("PUT");
      expect(storage.getItem(API_TOKEN_KEY)).toBe("captured-generation-token");
      expect(storage.getItem(API_USER_KEY)).toBe(JSON.stringify({ id: "generation-user" }));
    }
  });

  it("treats a failed PUT transport as an unknown outcome without retry", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("connection reset"));
    const { run, storage } = createHarness(fetcher);

    await expect(run.apply(stableFrame, makeCandidate(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown-outcome" });

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("captured-generation-token");
    expect(storage.getItem(API_USER_KEY)).toBe(JSON.stringify({ id: "generation-user" }));
  });

  it("treats malformed or mismatched success receipts as unknown outcomes", async () => {
    const candidate = makeCandidate();
    const base = makeResult(candidate);
    const invalidResults: unknown[] = [
      { ...base, extra: "unexpected" },
      { ...base, task_id: "another-task" },
      { ...base, result_ref: "another-result" },
      { ...base, status: "not_applied" },
      { ...base, application: { ...base.application as Record<string, unknown>, extra: true } },
      { ...base, application: { ...base.application as Record<string, unknown>, application_ref: "bad/id" } },
      { ...base, application: { ...base.application as Record<string, unknown>, target_revision: secondDigest.toUpperCase() } },
      { ...base, application: { ...base.application as Record<string, unknown>, committed_at: "2026-10-25T00:00:00" } },
      { ...base, application: { ...base.application as Record<string, unknown>, committed_at: "2026-13-40T99:99:99Z" } },
    ];

    for (const payload of invalidResults) {
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload));
      const { run } = createHarness(fetcher);

      await expect(run.apply(stableFrame, candidate, new AbortController().signal))
        .rejects.toMatchObject({ kind: "unknown-outcome", status: 200 });
      expect(fetcher).toHaveBeenCalledTimes(1);
    }
  });

  it("does not dispatch for a stale context or an already-aborted signal", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const { run, setContextCurrent } = createHarness(fetcher);
    const candidate = makeCandidate();

    setContextCurrent(false);
    await expect(run.apply(stableFrame, candidate, new AbortController().signal))
      .rejects.toMatchObject({ kind: "not-sent" });
    setContextCurrent(true);
    const controller = new AbortController();
    controller.abort();
    await expect(run.apply(stableFrame, candidate, controller.signal))
      .rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("marks an aborted dispatched request unknown and cancels any late response", async () => {
    let deliverResponse: ((response: Response) => void) | undefined;
    let cancelled = false;
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      deliverResponse = resolve;
    }));
    const { run, storage } = createHarness(fetcher);
    const controller = new AbortController();
    const request = run.apply(stableFrame, makeCandidate(), controller.signal);
    await vi.waitFor(() => expect(deliverResponse).toBeDefined());
    controller.abort();

    await expect(request).rejects.toMatchObject({ kind: "unknown-outcome" });
    deliverResponse?.(new Response(new ReadableStream<Uint8Array>({
      start(stream) { stream.enqueue(new TextEncoder().encode("late")); },
      cancel() { cancelled = true; },
    })));
    await vi.waitFor(() => expect(cancelled).toBe(true));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("captured-generation-token");
  });

  it("treats context changes after PUT as unknown outcome and cancels the late success", async () => {
    let deliverResponse: ((response: Response) => void) | undefined;
    let cancelled = false;
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      deliverResponse = resolve;
    }));
    const { run, setContextCurrent } = createHarness(fetcher);
    const request = run.apply(stableFrame, makeCandidate(), new AbortController().signal);
    await vi.waitFor(() => expect(deliverResponse).toBeDefined());
    setContextCurrent(false);
    deliverResponse?.(new Response(new ReadableStream<Uint8Array>({
      start(stream) { stream.enqueue(new TextEncoder().encode("late")); },
      cancel() { cancelled = true; },
    })));

    await expect(request).rejects.toMatchObject({ kind: "unknown-outcome" });
    await vi.waitFor(() => expect(cancelled).toBe(true));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("treats a captured token change after PUT as unknown and cancels the response", async () => {
    let deliverResponse: ((response: Response) => void) | undefined;
    let cancelled = false;
    const candidate = makeCandidate();
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      deliverResponse = resolve;
    }));
    const { run, storage } = createHarness(fetcher);
    const request = run.apply(stableFrame, candidate, new AbortController().signal);
    await vi.waitFor(() => expect(deliverResponse).toBeDefined());
    storage.setItem(API_TOKEN_KEY, "replacement-token");
    deliverResponse?.(new Response(new ReadableStream<Uint8Array>({
      start(stream) { stream.enqueue(new TextEncoder().encode(JSON.stringify(makeResult(candidate)))); },
      cancel() { cancelled = true; },
    })));

    await expect(request).rejects.toMatchObject({ kind: "unknown-outcome" });
    await vi.waitFor(() => expect(cancelled).toBe(true));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("replacement-token");
  });

  it("enforces the fetch-and-body deadline and cancels a stalled response stream", async () => {
    let readStarted = false;
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull: () => {
        readStarted = true;
        return new Promise<void>(() => undefined);
      },
      cancel() { cancelled = true; },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    const { run } = createHarness(fetcher, 250);

    const request = run.apply(stableFrame, makeCandidate(), new AbortController().signal);
    await vi.waitFor(() => expect(readStarted).toBe(true));
    await expect(request).rejects.toMatchObject({ kind: "unknown-outcome" });
    await vi.waitFor(() => expect(cancelled).toBe(true));
  });

  it("cancels and classifies a response body larger than 512 KiB as unknown", async () => {
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      start(stream) { stream.enqueue(new Uint8Array(512 * 1024 + 1)); },
      cancel() { cancelled = true; },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    const { run } = createHarness(fetcher);

    await expect(run.apply(stableFrame, makeCandidate(), new AbortController().signal))
      .rejects.toMatchObject({ kind: "unknown-outcome", status: 200 });
    await vi.waitFor(() => expect(cancelled).toBe(true));
  });
});
