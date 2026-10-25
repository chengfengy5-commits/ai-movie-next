// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { createApiServices, type WorkspaceServices } from "./services";
import {
  CanvasOwnedResultsError,
  type CanvasOwnedResultItem,
  type CanvasOwnedResultTargetSnapshot,
  type CanvasOwnedResultsPageOptions,
  type CanvasOwnedResultsTarget,
} from "./canvasOwnedResults";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "./storage";

const digest = "a".repeat(64);
const stableFrameTarget: CanvasOwnedResultsTarget = {
  kind: "frame_preview",
  chapter_id: "chapter-1",
  storyboard_asset_id: "storyboard-7",
};
const noIdFrameTarget: CanvasOwnedResultsTarget = {
  kind: "frame_preview",
  chapter_id: "chapter-1",
  identity_mode: "strict-no-id-v1",
  frame_index: 2,
  expected_chapter_digest: digest,
};
const assetTarget: CanvasOwnedResultsTarget = {
  kind: "asset_image",
  chapter_id: "chapter-1",
  asset_type: "character",
  asset_id: "character-5",
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
    compat_frame_index: 0,
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

function makeSnapshotWithout(field: keyof CanvasOwnedResultTargetSnapshot): Record<string, unknown> {
  return Object.fromEntries(Object.entries(makeSnapshot()).filter(([key]) => key !== field));
}

function makeItem(overrides: Partial<CanvasOwnedResultItem> = {}): CanvasOwnedResultItem {
  return {
    task_id: "task-1",
    result_ref: "result-1",
    operation: "image.single",
    completed_at: "2026-10-25T00:00:00Z",
    expected_target: makeSnapshot(),
    application_key: digest,
    eligibility: "ready",
    application: null,
    ...overrides,
  };
}

function makePage(
  target: CanvasOwnedResultsTarget,
  items: readonly unknown[] = [],
  nextCursor: unknown = null,
): Record<string, unknown> {
  return {
    chapter_id: target.chapter_id,
    target,
    items,
    next_cursor: nextCursor,
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
  storage.setItem(API_TOKEN_KEY, "captured-discovery-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "user-1" }));
  const services: WorkspaceServices = createApiServices("http://127.0.0.1:4175/api", {
    storage,
    fetcher,
    timeoutMs,
  });
  const port = services.canvasOwnedResults;
  if (port === undefined) throw new Error("API services did not expose canvasOwnedResults");

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
    run: port.beginRun(context),
    setContextCurrent(value: boolean) { contextCurrent = value; },
  };
}

function requestUrl(fetcher: ReturnType<typeof vi.fn<typeof fetch>>): URL {
  const call = fetcher.mock.calls[0];
  if (call === undefined) throw new Error("Expected a discovery request");
  return new URL(String(call[0]));
}

interface InvalidPageCase {
  readonly name: string;
  payload(): unknown;
  readonly target?: CanvasOwnedResultsTarget;
  readonly options?: CanvasOwnedResultsPageOptions;
}

const invalidPageCases: InvalidPageCase[] = [
  {
    name: "rejects extra page fields",
    payload: () => ({ ...makePage(stableFrameTarget), preview_url: "https://example.invalid/image.png" }),
  },
  {
    name: "rejects a foreign target echo",
    payload: () => makePage({ ...stableFrameTarget, chapter_id: "chapter-2" }),
  },
  {
    name: "rejects an item with extra fields",
    payload: () => makePage(stableFrameTarget, [{ ...makeItem(), preview_url: "https://example.invalid/image.png" }]),
  },
  {
    name: "rejects duplicate result references",
    payload: () => makePage(stableFrameTarget, [makeItem(), { ...makeItem(), task_id: "task-2" }]),
  },
  {
    name: "rejects a page larger than the requested limit",
    payload: () => makePage(stableFrameTarget, [makeItem(), { ...makeItem(), result_ref: "result-2" }]),
    options: { limit: 1 },
  },
  {
    name: "rejects a cursor equal to the requested cursor",
    payload: () => makePage(stableFrameTarget, [], "YWJj"),
    options: { cursor: "YWJj" },
  },
  {
    name: "rejects an invalid cursor alphabet",
    payload: () => makePage(stableFrameTarget, [], "bad$cursor"),
  },
  {
    name: "rejects an original target from another series",
    payload: () => makePage(stableFrameTarget, [
      { ...makeItem(), expected_target: makeSnapshot({ series_id: "series-2" }) },
    ]),
  },
  {
    name: "rejects unknown original target snapshot fields",
    payload: () => makePage(stableFrameTarget, [
      { ...makeItem(), expected_target: { ...makeSnapshot(), source_url: "https://example.invalid/image.png" } },
    ]),
  },
  {
    name: "rejects an omitted nullable original target field",
    payload: () => makePage(stableFrameTarget, [
      { ...makeItem(), expected_target: makeSnapshotWithout("media_identity") },
    ]),
  },
  {
    name: "rejects a no-ID frame snapshot with an asset identity",
    target: noIdFrameTarget,
    payload: () => makePage(noIdFrameTarget, [
      {
        ...makeItem(),
        expected_target: makeSnapshot({
          stable_storyboard_asset_id: null,
          asset_type: null,
          asset_id: "stray-asset",
          compat_frame_index: 2,
          full_chapter_digest: digest,
        }),
      },
    ]),
  },
  {
    name: "rejects an unready item that carries a target",
    payload: () => makePage(stableFrameTarget, [
      { ...makeItem(), eligibility: "unready", application_key: null, application: null },
    ]),
  },
  {
    name: "rejects application proof on a ready item",
    payload: () => makePage(stableFrameTarget, [
      { ...makeItem(), application: { application_ref: "application-1", target_revision: digest, committed_at: "2026-10-25T00:00:00Z" } },
    ]),
  },
  {
    name: "rejects a completed time without explicit UTC",
    payload: () => makePage(stableFrameTarget, [
      { ...makeItem(), completed_at: "2026-10-25T00:00:00+01:00" },
    ]),
  },
];

describe("canvas owned-result discovery HTTP port", () => {
  it("is exposed through the real API services factory", () => {
    const storage = new MemoryStorage();
    storage.setItem(API_TOKEN_KEY, "captured-discovery-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "user-1" }));

    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage,
      fetcher: vi.fn<typeof fetch>(),
    });

    expect(services.canvasOwnedResults).toBeDefined();
  });

  it.each([
    {
      name: "stable frame identity",
      target: stableFrameTarget,
      query: { target_kind: "frame_preview", storyboard_asset_id: "storyboard-7" },
    },
    {
      name: "strict no-ID frame identity",
      target: noIdFrameTarget,
      query: {
        target_kind: "frame_preview",
        identity_mode: "strict-no-id-v1",
        frame_index: "2",
        expected_chapter_digest: digest,
      },
    },
    {
      name: "asset image identity",
      target: assetTarget,
      query: { target_kind: "asset_image", asset_type: "character", asset_id: "character-5" },
    },
  ])("sends one exact GET for $name and returns its opaque next cursor", async ({ target, query }) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(makePage(target, [], "ZGVm")));
    const { run } = createHarness(fetcher);

    const page = await run.page(target, new AbortController().signal, { limit: 7, cursor: "YWJj" });

    expect(page).toEqual({ ...makePage(target, [], "ZGVm"), target });
    expect(fetcher).toHaveBeenCalledTimes(1);
    const url = requestUrl(fetcher);
    expect(url.pathname).toBe("/api/chapters/chapter-1/canvas-owned-results");
    expect(Object.fromEntries(url.searchParams.entries())).toEqual({ ...query, limit: "7", cursor: "YWJj" });
    const [input, init] = fetcher.mock.calls[0]!;
    expect(input).toBeInstanceOf(URL);
    expect(init).toMatchObject({
      method: "GET",
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
      referrerPolicy: "no-referrer",
    });
    expect(new Headers(init?.headers).get("authorization")).toBe("Bearer captured-discovery-token");
    expect(init?.body).toBeUndefined();
  });

  it("uses the default page size and never follows next_cursor automatically", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(makePage(stableFrameTarget, [], "YWJj")));
    const { run } = createHarness(fetcher);

    const page = await run.page(stableFrameTarget, new AbortController().signal);

    expect(page.next_cursor).toBe("YWJj");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(requestUrl(fetcher).searchParams.get("limit")).toBe("20");
  });

  it("preserves a valid original target snapshot and its historical application proof", async () => {
    const item = makeItem({
      eligibility: "already_applied",
      application: {
        application_ref: "application-1",
        target_revision: digest,
        committed_at: "2026-10-25T00:00:00+00:00",
      },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(makePage(stableFrameTarget, [item])));
    const { run } = createHarness(fetcher);

    const page = await run.page(stableFrameTarget, new AbortController().signal);

    expect(page.items).toEqual([item]);
    expect(page.items[0]?.expected_target?.full_frame_digest).toBe(digest);
    expect(page.items[0]?.expected_target?.expected_text_digest).toBeNull();
    expect(page.items[0]?.expected_target?.media_identity).toBeNull();
    expect(page.items[0]?.eligibility).toBe("already_applied");
    expect(page.items[0]?.application?.application_ref).toBe("application-1");
  });

  it.each([
    {
      name: "strict no-ID frame snapshot",
      target: noIdFrameTarget,
      snapshot: makeSnapshot({
        stable_storyboard_asset_id: null,
        asset_type: null,
        asset_id: null,
        compat_frame_index: 2,
        full_chapter_digest: digest,
      }),
    },
    {
      name: "asset image snapshot",
      target: assetTarget,
      snapshot: makeSnapshot({
        kind: "canvas_asset_image",
        stable_storyboard_asset_id: null,
        asset_type: "character",
        asset_id: "character-5",
        compat_frame_index: null,
        full_frame_digest: null,
        full_chapter_digest: null,
        same_series_target_digest: digest,
      }),
    },
  ])("accepts the original $name identity", async ({ target, snapshot }) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(makePage(target, [
      makeItem({ expected_target: snapshot }),
    ])));
    const { run } = createHarness(fetcher);

    const page = await run.page(target, new AbortController().signal);

    expect(page.items[0]?.expected_target).toEqual(snapshot);
  });

  it.each(invalidPageCases)("$name", async ({ payload, options, target }) => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(payload()));
    const { run } = createHarness(fetcher);

    await expect(run.page(
      target ?? stableFrameTarget,
      new AbortController().signal,
      options as CanvasOwnedResultsPageOptions | undefined,
    ))
      .rejects.toMatchObject({ kind: "invalid-response" } satisfies Partial<CanvasOwnedResultsError>);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed selectors, limits, and cursors before making a request", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const { run } = createHarness(fetcher);
    const invalidTargets: unknown[] = [
      { ...stableFrameTarget, identity_mode: "strict-no-id-v1" },
      { ...noIdFrameTarget, storyboard_asset_id: "storyboard-7" },
      { ...noIdFrameTarget, frame_index: -1 },
      { ...noIdFrameTarget, expected_chapter_digest: digest.toUpperCase() },
      { ...assetTarget, frame_index: 2 },
      { ...stableFrameTarget, chapter_id: "chapter-2" },
    ];

    for (const target of invalidTargets) {
      await expect(run.page(target as CanvasOwnedResultsTarget, new AbortController().signal))
        .rejects.toMatchObject({ kind: "not-sent" });
    }
    for (const options of [
      { limit: 0 },
      { limit: 51 },
      { limit: 1.5 },
      { cursor: "bad$cursor" },
      { cursor: "a".repeat(2049) },
      { limit: 1, unexpected: true },
    ]) {
      await expect(run.page(stableFrameTarget, new AbortController().signal, options))
        .rejects.toMatchObject({ kind: "not-sent" });
    }
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("does not dispatch when the captured chapter context or token is stale", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const { run, storage, setContextCurrent } = createHarness(fetcher);

    setContextCurrent(false);
    await expect(run.page(stableFrameTarget, new AbortController().signal))
      .rejects.toMatchObject({ kind: "not-sent" });
    setContextCurrent(true);
    storage.setItem(API_TOKEN_KEY, "rotated-token");
    await expect(run.page(stableFrameTarget, new AbortController().signal))
      .rejects.toMatchObject({ kind: "not-sent" });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rechecks context after a late unauthorized response and never clears the session", async () => {
    let deliverResponse: ((response: Response) => void) | undefined;
    let cancelled = false;
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => {
      deliverResponse = (response) => resolve(response);
    }));
    const { run, storage, setContextCurrent } = createHarness(fetcher);
    const request = run.page(stableFrameTarget, new AbortController().signal);
    await vi.waitFor(() => expect(deliverResponse).toBeDefined());
    setContextCurrent(false);
    deliverResponse?.(new Response(new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new TextEncoder().encode("private")); },
      cancel() { cancelled = true; },
    }), { status: 401 }));

    await expect(request).rejects.toMatchObject({ kind: "read-error" });
    await vi.waitFor(() => expect(cancelled).toBe(true));
    expect(storage.getItem(API_TOKEN_KEY)).toBe("captured-discovery-token");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("maps HTTP failures after cancelling their bodies and performs no writes or retry", async () => {
    const statuses = [
      { status: 401, kind: "unauthorized" },
      { status: 403, kind: "forbidden" },
      { status: 404, kind: "not-found" },
      { status: 422, kind: "invalid-query" },
      { status: 503, kind: "unavailable" },
    ] as const;

    for (const { status, kind } of statuses) {
      let cancelled = false;
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new TextEncoder().encode("private")); },
        cancel() { cancelled = true; },
      }), { status }));
      const { run, storage } = createHarness(fetcher);

      await expect(run.page(stableFrameTarget, new AbortController().signal))
        .rejects.toMatchObject({ kind, status });

      expect(cancelled).toBe(true);
      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(fetcher.mock.calls[0]?.[1]?.method).toBe("GET");
      expect(fetcher.mock.calls[0]?.[1]?.body).toBeUndefined();
      expect(storage.getItem(API_TOKEN_KEY)).toBe("captured-discovery-token");
    }
  });

  it("cancels a pending response body when the caller aborts", async () => {
    let cancelCalled = false;
    let readStarted = false;
    const body = new ReadableStream<Uint8Array>({
      pull: () => {
        readStarted = true;
        return new Promise<void>(() => undefined);
      },
      cancel() { cancelCalled = true; },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    const { run } = createHarness(fetcher);
    const controller = new AbortController();
    const request = run.page(stableFrameTarget, controller.signal);
    await vi.waitFor(() => expect(readStarted).toBe(true));
    controller.abort();

    await expect(request).rejects.toMatchObject({ kind: "read-error" });
    await vi.waitFor(() => expect(cancelCalled).toBe(true));
  });

  it("applies the body deadline and cancels a stalled stream", async () => {
    let cancelCalled = false;
    let readStarted = false;
    const body = new ReadableStream<Uint8Array>({
      pull: () => {
        readStarted = true;
        return new Promise<void>(() => undefined);
      },
      cancel() { cancelCalled = true; },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    const { run } = createHarness(fetcher, 250);

    const request = run.page(stableFrameTarget, new AbortController().signal);
    await vi.waitFor(() => expect(readStarted).toBe(true));
    await expect(request).rejects.toMatchObject({ kind: "read-error" });
    await vi.waitFor(() => expect(cancelCalled).toBe(true));
  });

  it("cancels a streamed body that exceeds 512 KiB", async () => {
    let cancelCalled = false;
    const body = new ReadableStream<Uint8Array>({
      start(controller) { controller.enqueue(new Uint8Array(512 * 1024 + 1)); },
      cancel() { cancelCalled = true; },
    });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(body));
    const { run } = createHarness(fetcher);

    await expect(run.page(stableFrameTarget, new AbortController().signal))
      .rejects.toMatchObject({ kind: "invalid-response", status: 200 });
    await vi.waitFor(() => expect(cancelCalled).toBe(true));
  });
});
