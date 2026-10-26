import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChapterBrowser } from "../features/chapters/ChapterBrowser";
import { CanvasWorkspace } from "../features/canvas/CanvasWorkspace";
import { demoSeries } from "../features/series/demoSeries";
import type { Character, Chapter, Prop, Scene, StoryboardAsset } from "../shared/api/contracts";
import type { CanvasDataPort } from "../shared/api/canvasData";
import type { CanvasOwnedResultItem, CanvasOwnedResultTargetSnapshot, CanvasOwnedResultsTarget } from "../shared/api/canvasOwnedResults";
import { createCanvasDataHttpPort } from "../shared/api/canvasDataHttp";
import { createApiServices, createDemoServices, type WorkspaceServices } from "../shared/api/services";
import { API_TOKEN_KEY, API_USER_KEY, type StorageLike } from "../shared/api/storage";

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");

function memoryStorage(): StorageLike {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  };
}

type GenerationTargetFixture = "stable-frame" | "no-id-frame" | "invalid-no-id" | "character" | "scene" | "prop";

interface GenerationWorkspaceHarnessOptions {
  readonly targetKind: GenerationTargetFixture;
  readonly requiresLock?: boolean;
  readonly lockMode?: "unlocked" | "foreign" | "expired";
  readonly eligibility?: "ready" | "already_applied" | "stale" | "target_deleted" | "unready";
  readonly initialNextCursor?: string | null;
  readonly deferredNextPage?: boolean;
  readonly putReplies?: readonly { status: number; resultStatus?: "applied" | "already_applied" }[];
  readonly lookupReplies?: readonly { status: "not_applied" | "applied" }[];
  readonly imageSubmitReplies?: readonly { status: number; receiptStatus?: "queued" | "processing" | "cancelling" | "reconciling" | "completed" | "failed" }[];
  readonly imageLookupReplies?: readonly { status: number; receiptStatus?: "queued" | "processing" | "cancelling" | "reconciling" | "completed" | "failed" }[];
  readonly deferredImageSubmit?: boolean;
  readonly ownedImageReplies?: readonly {
    readonly status: number;
    readonly contentType?: string;
    readonly bytes?: readonly number[];
  }[];
  readonly deferredOwnedImageRead?: boolean;
  readonly freshSourceVersion?: string;
  readonly failSourceReadAt?: number;
}

function mountGenerationWorkspace(options: GenerationWorkspaceHarnessOptions) {
  const storage = memoryStorage();
  storage.setItem(API_TOKEN_KEY, "generation-fixture-token");
  storage.setItem(API_USER_KEY, JSON.stringify({ id: "demo-user" }));
  const currentSeries = demoSeries[0]!;
  const stable = options.targetKind === "stable-frame";
  const invalidNoId = options.targetKind === "invalid-no-id";
  const assetKind = options.targetKind === "character" || options.targetKind === "scene" || options.targetKind === "prop"
    ? options.targetKind
    : null;
  const assetId = assetKind === null ? "unused-generation-asset" : `${assetKind}-generation`;
  const characterId = "character-generation";
  const includesCharacter = stable || assetKind === "character";
  const timestamp = "2026-10-25T00:00:00Z";
  const frame = {
    text: "等待图像记录",
    character: includesCharacter ? [characterId] : [],
    scene: assetKind === "scene" ? [assetId] : [],
    prop: assetKind === "prop" ? [assetId] : [],
    storyboard: stable ? ["board-generation"] : [],
    preview: null,
    videos: [],
  };
  const currentChapter: Chapter = {
    id: `generation-${options.targetKind}-chapter`,
    series_id: currentSeries.id,
    title: "生成记录章节",
    content: [frame],
    order: 1,
    created_at: timestamp,
    updated_at: timestamp,
    lock: null,
  };
  const storyboardAsset: StoryboardAsset | null = stable ? {
    id: "board-generation",
    series_id: currentSeries.id,
    chapter_id: currentChapter.id,
    frame_index: 0,
    name: "生成分镜",
    description: null,
    image_url: null,
    created_at: timestamp,
    updated_at: timestamp,
  } : null;
  const character: Character | null = includesCharacter ? {
    id: characterId, series_id: currentSeries.id, name: "生成角色", description: null, image_url: null,
    aliases: null, canonical_key: null, gender: null, age: null, role: null, appearance: null,
    audio_url: null, voice_ref: null, created_at: timestamp, updated_at: timestamp,
  } : null;
  const scene: Scene | null = assetKind === "scene" ? {
    id: assetId, series_id: currentSeries.id, title: "生成场景", description: null, image_url: null,
    aliases: null, canonical_key: null, created_at: timestamp, updated_at: timestamp,
  } : null;
  const prop: Prop | null = assetKind === "prop" ? {
    id: assetId, series_id: currentSeries.id, name: "生成道具", description: null, image_url: null,
    aliases: null, canonical_key: null, created_at: timestamp, updated_at: timestamp,
  } : null;
  const digest = "e".repeat(64);
  const sourceSnapshot = {
    capability: "canvas-source-v1",
    series_id: currentSeries.id,
    chapter_id: currentChapter.id,
    version: "a".repeat(64),
    ...(stable || invalidNoId ? {} : { chapter_canonical_digest: digest }),
    requires_lock: options.requiresLock ?? false,
    chapter: currentChapter,
    storyboard_assets: storyboardAsset === null ? [] : [storyboardAsset],
    characters: character === null ? [] : [character],
    scenes: scene === null ? [] : [scene],
    props: prop === null ? [] : [prop],
  };
  let target: CanvasOwnedResultsTarget;
  if (stable) {
    target = { kind: "frame_preview", chapter_id: currentChapter.id, storyboard_asset_id: "board-generation" };
  } else if (options.targetKind === "no-id-frame" || invalidNoId) {
    target = {
      kind: "frame_preview",
      chapter_id: currentChapter.id,
      identity_mode: "strict-no-id-v1",
      frame_index: 0,
      expected_chapter_digest: digest,
    };
  } else {
    if (assetKind === null) throw new Error(`Unsupported generation target fixture: ${options.targetKind}`);
    target = { kind: "asset_image", chapter_id: currentChapter.id, asset_type: assetKind, asset_id: assetId };
  }
  const targetSnapshot: CanvasOwnedResultTargetSnapshot = stable || assetKind === null
    ? {
      kind: "canvas_frame_preview",
      chapter_id: currentChapter.id,
      series_id: currentSeries.id,
      stable_storyboard_asset_id: stable ? "board-generation" : null,
      asset_type: stable ? "storyboard" : null,
      asset_id: stable ? "board-generation" : null,
      compat_frame_index: 0,
      expected_text_digest: null,
      media_identity: null,
      content_digest: null,
      notes_revision: null,
      full_frame_digest: "a".repeat(64),
      full_chapter_digest: stable ? "b".repeat(64) : digest,
      expected_asset_image_url: null,
      same_series_target_digest: null,
    }
    : {
      kind: "canvas_asset_image",
      chapter_id: currentChapter.id,
      series_id: currentSeries.id,
      stable_storyboard_asset_id: null,
      asset_type: assetKind,
      asset_id: assetId,
      compat_frame_index: null,
      expected_text_digest: null,
      media_identity: null,
      content_digest: null,
      notes_revision: null,
      full_frame_digest: null,
      full_chapter_digest: null,
      expected_asset_image_url: null,
      same_series_target_digest: "b".repeat(64),
    };
  const characterTargetSnapshot: CanvasOwnedResultTargetSnapshot = {
    kind: "canvas_asset_image",
    chapter_id: currentChapter.id,
    series_id: currentSeries.id,
    stable_storyboard_asset_id: null,
    asset_type: "character",
    asset_id: characterId,
    compat_frame_index: null,
    expected_text_digest: null,
    media_identity: null,
    content_digest: null,
    notes_revision: null,
    full_frame_digest: null,
    full_chapter_digest: null,
    expected_asset_image_url: null,
    same_series_target_digest: "b".repeat(64),
  };
  const eligibility = options.eligibility ?? "ready";
  const ownedResult: CanvasOwnedResultItem = {
    task_id: "generation-task",
    result_ref: "generation-result",
    operation: "image.single",
    completed_at: timestamp,
    expected_target: eligibility === "unready" ? null : targetSnapshot,
    application_key: eligibility === "unready" ? null : "c".repeat(64),
    eligibility,
    application: eligibility === "already_applied"
      ? { application_ref: "old-application", target_revision: "d".repeat(64), committed_at: "2026-10-25T00:00:00+00:00" }
      : null,
  };

  const documentJson = {
    nodeLayouts: {
      "frame:0": { x: -44, y: 0, extension: "legacy-layout" },
      "future-node": { x: 72, y: 91, extension: { keep: true } },
    },
    notes: [{ id: "existing-note", text: "保留便签", x: 31, y: 42, width: 220, height: 120 }],
    cards: [{ id: "existing-card", text: "保留卡片", x: 53, y: 64, width: 260, height: 150 }],
    manualEdges: [{ id: "existing-edge", sourceRef: "frame:0", targetRef: "future-node", relation: "manual", extension: true }],
    hiddenRelations: ["future:relation"],
    viewport: { x: 0, y: 0, zoom: 1 },
    unknown: { preserve: true },
  };
  const originalDocumentJson = JSON.parse(JSON.stringify(documentJson)) as typeof documentJson;
  const requests: Array<{
    url: URL;
    method: string;
    body: unknown;
    headers: Headers;
    signal: AbortSignal | null;
  }> = [];
  const putReplies = [...(options.putReplies ?? [])];
  const lookupReplies = [...(options.lookupReplies ?? [])];
  const imageSubmitReplies = [...(options.imageSubmitReplies ?? [])];
  const imageLookupReplies = [...(options.imageLookupReplies ?? [])];
  const ownedImageReplies = [...(options.ownedImageReplies ?? [])];
  let sourceReadCount = 0;
  let freshSourceVersion = options.freshSourceVersion;
  let ownedResultVisible = true;
  let activeChapter = currentChapter;
  let lockAcquired = false;
  let lockPostCount = 0;
  let imageSubmissionNumber = 0;
  let releaseDeferredImageSubmit: ((response: Response) => void) | null = null;
  let markDeferredImageSubmitStarted: (() => void) | null = null;
  const deferredImageSubmitStarted = new Promise<void>((resolve) => { markDeferredImageSubmitStarted = resolve; });
  let markDeferredImageResponseHandled: (() => void) | null = null;
  const deferredImageResponseHandled = new Promise<void>((resolve) => { markDeferredImageResponseHandled = resolve; });
  let releaseDeferredPage: ((response: Response) => void) | null = null;
  let markDeferredPageStarted: (() => void) | null = null;
  const deferredPageStarted = new Promise<void>((resolve) => { markDeferredPageStarted = resolve; });
  let releaseDeferredOwnedImageRead: ((response: Response) => void) | null = null;
  let markDeferredOwnedImageReadStarted: (() => void) | null = null;
  const deferredOwnedImageReadStarted = new Promise<void>((resolve) => {
    markDeferredOwnedImageReadStarted = resolve;
  });
  let deferredPageUsed = false;
  const validLockExpiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  const lockState = () => options.lockMode === "foreign"
    ? { locked: true, user_id: "other-user", is_mine: false, expires_at: validLockExpiresAt }
    : options.lockMode === "expired"
      ? { locked: true, user_id: "demo-user", is_mine: true, expires_at: "2020-01-01T00:00:00Z" }
      : lockAcquired
        ? { locked: true, user_id: "demo-user", is_mine: true, expires_at: validLockExpiresAt }
        : { locked: false };
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    const body = init?.body === undefined ? null : JSON.parse(String(init.body)) as unknown;
    const headers = new Headers(init?.headers);
    requests.push({ url, method, body, headers, signal: init?.signal ?? null });
    if (method === "GET" && url.pathname === `/api/series/${currentSeries.id}/chapters`) {
      return Response.json([activeChapter]);
    }
    if (method === "GET" && url.pathname === "/api/models") {
      return Response.json([
        {
          id: "image-model-private-id",
          category: "image",
          name: "画布单图模型",
          provider: "private-provider-value",
          model: "private-model-value",
          base_url: "https://private-model.example.invalid",
          params: "private-params-value",
          api_key: "private-api-key-value",
          sort_order: 0,
          is_system: false,
          user_id: "demo-user",
        },
        {
          id: "video-model-id",
          category: "video",
          name: "不可选的视频模型",
          provider: "video-provider",
          params: null,
          sort_order: 1,
          is_system: false,
          user_id: "demo-user",
        },
      ]);
    }
    if (method === "POST" && url.pathname === "/api/chat/image") {
      imageSubmissionNumber += 1;
      if (options.deferredImageSubmit) {
        markDeferredImageSubmitStarted?.();
        return new Promise<Response>((resolve) => { releaseDeferredImageSubmit = resolve; });
      }
      const reply = imageSubmitReplies.shift() ?? { status: 200, receiptStatus: "queued" as const };
      if (reply.status !== 200) return new Response("image task admission is not confirmed", { status: reply.status });
      return Response.json({
        submission_id: `image-submission-${imageSubmissionNumber}`,
        operation: "image.single",
        task_id: `image-task-${imageSubmissionNumber}`,
        message_id: `image-message-${imageSubmissionNumber}`,
        status: reply.receiptStatus ?? "queued",
        quoted_amount: 12,
        billing_status: "unbilled",
      });
    }
    if (method === "GET" && url.pathname === "/api/submissions/image.single") {
      const reply = imageLookupReplies.shift() ?? { status: 404 };
      if (reply.status !== 200) return new Response("submission not observed", { status: reply.status });
      return Response.json({
        submission_id: "image-submission-1",
        operation: "image.single",
        task_id: "image-task-1",
        message_id: "image-message-1",
        status: reply.receiptStatus ?? "queued",
        quoted_amount: 12,
        billing_status: "unbilled",
      });
    }
    if (method === "GET" && url.pathname === `/api/chapters/${activeChapter.id}/canvas-source`) {
      sourceReadCount += 1;
      if (sourceReadCount === options.failSourceReadAt) return new Response("source unavailable", { status: 503 });
      const chapterSource = activeChapter.id === currentChapter.id
        ? sourceSnapshot
        : {
          ...sourceSnapshot,
          chapter_id: activeChapter.id,
          chapter: { ...currentChapter, id: activeChapter.id },
        };
      if (sourceReadCount > 1 && freshSourceVersion !== undefined) {
        return Response.json({ ...chapterSource, version: freshSourceVersion });
      }
      return Response.json(chapterSource);
    }
    if (method === "GET" && url.pathname === `/api/chapters/${activeChapter.id}/canvas`) {
      return Response.json({
        id: "generation-canvas", chapter_id: activeChapter.id, version: 1, document_json: documentJson,
        updated_by: "demo-user", updated_at: timestamp, save_capability: "guarded-v1",
      });
    }
    if (method === "GET" && url.pathname === `/api/chapters/${activeChapter.id}/canvas-owned-results`) {
      if (url.searchParams.has("cursor") && options.deferredNextPage && !deferredPageUsed) {
        deferredPageUsed = true;
        return new Promise<Response>((resolve) => {
          releaseDeferredPage = resolve;
          markDeferredPageStarted?.();
        });
      }
      const nextCursor = url.searchParams.has("cursor") ? null : options.initialNextCursor ?? null;
      const queriedAssetType = url.searchParams.get("asset_type");
      const queriedAssetId = url.searchParams.get("asset_id");
      let pageTarget = target;
      if (url.searchParams.get("target_kind") === "asset_image") {
        if ((queriedAssetType !== "character" && queriedAssetType !== "scene" && queriedAssetType !== "prop")
          || queriedAssetId === null) {
          throw new Error("Unexpected asset target selector in generation fixture.");
        }
        pageTarget = {
          kind: "asset_image",
          chapter_id: currentChapter.id,
          asset_type: queriedAssetType,
          asset_id: queriedAssetId,
        };
      }
      const pageItem = pageTarget.kind === "asset_image" && pageTarget.asset_type === "character"
        ? { ...ownedResult, expected_target: characterTargetSnapshot }
        : ownedResult;
      const item = url.searchParams.has("cursor")
        ? { ...pageItem, result_ref: "generation-page-two" }
        : pageItem;
      return Response.json({
        chapter_id: activeChapter.id,
        target: { ...pageTarget, chapter_id: activeChapter.id },
        items: ownedResultVisible ? [item] : [],
        next_cursor: nextCursor,
      });
    }
    if (method === "GET" && /^\/api\/chapters\/[^/]+\/canvas-owned-results\/[^/]+\/image$/.test(url.pathname)) {
      if (options.deferredOwnedImageRead) {
        markDeferredOwnedImageReadStarted?.();
        return new Promise<Response>((resolve) => { releaseDeferredOwnedImageRead = resolve; });
      }
      const reply = ownedImageReplies.shift() ?? { status: 200, contentType: "image/png", bytes: [137, 80, 78, 71] };
      if (reply.status !== 200) return new Response("owned image is unavailable", { status: reply.status });
      return new Response(new Uint8Array(reply.bytes ?? [137, 80, 78, 71]), {
        status: 200,
        headers: { "Content-Type": reply.contentType ?? "image/png" },
      });
    }
    if (method === "GET" && url.pathname.endsWith(`/canvas-owned-results/${ownedResult.result_ref}/application`)) {
      const reply = lookupReplies.shift() ?? { status: "not_applied" as const };
      return Response.json({
        task_id: ownedResult.task_id,
        result_ref: ownedResult.result_ref,
        status: reply.status,
        application: reply.status === "applied"
          ? { application_ref: "verified-application", target_revision: "f".repeat(64), committed_at: "2026-10-25T00:00:00+00:00" }
          : null,
      });
    }
    if (url.pathname === `/api/chapters/${activeChapter.id}/lock`) {
      if (method === "GET") return Response.json(lockState());
      if (method === "POST") {
        lockPostCount += 1;
        lockAcquired = true;
        return Response.json(lockState());
      }
    }
    if (method === "PUT" && url.pathname === `/api/chapters/${activeChapter.id}`) {
      const reply = putReplies.shift() ?? { status: 200, resultStatus: "applied" as const };
      if (reply.status !== 200) return new Response("write outcome is not confirmed", { status: reply.status });
      const generationApplication = (body as { generation_application: { task_id: string; result_ref: string } }).generation_application;
      return Response.json({
        task_id: generationApplication.task_id,
        result_ref: generationApplication.result_ref,
        status: reply.resultStatus ?? "applied",
        application: {
          application_ref: "generation-application",
          target_revision: "d".repeat(64),
          committed_at: "2026-10-25T00:00:00+00:00",
        },
      });
    }
    throw new Error(`Unexpected API request: ${method} ${url.pathname}`);
  });
  const services = createApiServices("http://127.0.0.1:8000/api", { storage, fetcher });
  const onSourceChanged = vi.fn();
  const onUnauthorized = vi.fn();
  const canvasPort = services.canvasData;
  if (canvasPort === undefined) throw new Error("API services did not expose the canvas port.");
  const workspace = (sessionGeneration = 2, actorId = "demo-user", chapter = activeChapter) => (
    <CanvasWorkspace
      chapter={chapter}
      onClose={vi.fn()}
      onUnauthorized={onUnauthorized}
      onSourceChanged={onSourceChanged}
      port={canvasPort}
      series={currentSeries}
      services={services}
      sessionGeneration={sessionGeneration}
      userId={actorId}
    />
  );
  const view = render(workspace());
  return {
    currentChapter,
    currentSeries,
    target,
    targetSnapshot,
    ownedResult,
    documentJson,
    originalDocumentJson,
    requests,
    fetcher,
    services,
    onSourceChanged,
    onUnauthorized,
    token: () => storage.getItem(API_TOKEN_KEY),
    setToken(value: string) { storage.setItem(API_TOKEN_KEY, value); },
    setStoredActor(value: string) { storage.setItem(API_USER_KEY, JSON.stringify({ id: value })); },
    setFreshSourceVersion(value: string | undefined) { freshSourceVersion = value; },
    setOwnedResultVisible(value: boolean) { ownedResultVisible = value; },
    rerender(sessionGeneration: number, actorId = "demo-user", chapter = activeChapter) {
      activeChapter = chapter;
      view.rerender(workspace(sessionGeneration, actorId, chapter));
    },
    deferredImageSubmitStarted,
    deferredImageResponseHandled,
    resolveDeferredImageSubmit(payload: unknown) {
      let delivered = false;
      const body = new ReadableStream<Uint8Array>({
        pull(controller) {
          if (!delivered) {
            delivered = true;
            controller.enqueue(new TextEncoder().encode(JSON.stringify(payload)));
            return;
          }
          markDeferredImageResponseHandled?.();
          controller.close();
        },
        cancel() { markDeferredImageResponseHandled?.(); },
      }, { highWaterMark: 0 });
      releaseDeferredImageSubmit?.(new Response(body, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }));
    },
    deferredPageStarted,
    resolveDeferredPage(payload: unknown) { releaseDeferredPage?.(Response.json(payload)); },
    deferredOwnedImageReadStarted,
    resolveDeferredOwnedImageRead(response: Response = new Response(new Uint8Array([137, 80, 78, 71]), {
      status: 200,
      headers: { "Content-Type": "image/png" },
    })) { releaseDeferredOwnedImageRead?.(response); },
    lockPostCount: () => lockPostCount,
  };
}

afterEach(() => {
  cleanup();
  if (originalCreateObjectUrl === undefined) Reflect.deleteProperty(URL, "createObjectURL");
  else Object.defineProperty(URL, "createObjectURL", originalCreateObjectUrl);
  if (originalRevokeObjectUrl === undefined) Reflect.deleteProperty(URL, "revokeObjectURL");
  else Object.defineProperty(URL, "revokeObjectURL", originalRevokeObjectUrl);
});

function generationResultsDialog() {
  return within(screen.getByRole("dialog", { name: "生成图片记录" }));
}

function ownedImageRequests(harness: ReturnType<typeof mountGenerationWorkspace>) {
  return harness.requests.filter((request) => request.method === "GET"
    && /^\/api\/chapters\/[^/]+\/canvas-owned-results\/[^/]+\/image$/u.test(request.url.pathname));
}

async function openGenerationResultsFor(nodeName = "分镜 1") {
  fireEvent.click(await screen.findByRole("article", { name: nodeName }));
  fireEvent.click(await screen.findByRole("button", { name: "生成图片记录" }));
  const dialog = generationResultsDialog();
  await dialog.findByRole("article");
  return dialog;
}

async function waitForImageModelOption(dialog: HTMLElement) {
  const queries = within(dialog);
  await queries.findByRole("option", { name: "画布单图模型" });
  await waitFor(() => expect(queries.getByRole("button", { name: "提交单张图片任务" })).toBeEnabled());
  return queries;
}

describe("selected chapter to canvas integration", () => {
  it("opens a single-image generation form for a selected stable frame", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame" });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));

    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));

    const dialog = await screen.findByRole("dialog", { name: "生成图片" });
    const dialogQueries = await waitForImageModelOption(dialog);
    const modelReads = harness.requests.filter((request) => request.method === "GET" && request.url.pathname === "/api/models");
    expect(modelReads).toHaveLength(1);
    expect(modelReads[0]?.url.searchParams.get("category")).toBe("image");
    expect((dialogQueries.getByLabelText("图片模型") as HTMLSelectElement).value).toBe("image-model-private-id");
    expect((dialogQueries.getByLabelText("提示词") as HTMLTextAreaElement).value).toBe("等待图像记录");
    expect(dialog.innerHTML).not.toContain("private-provider-value");
    expect(dialog.innerHTML).not.toContain("private-model-value");
    expect(dialog.innerHTML).not.toContain("private-model.example.invalid");
    expect(dialog.innerHTML).not.toContain("private-params-value");
    expect(dialog.innerHTML).not.toContain("private-api-key-value");
    expect(within(dialog).queryByText("不可选的视频模型")).toBeNull();
    expect(harness.requests.filter((request) => request.method === "POST")).toHaveLength(0);
  });

  it("uses the newly selected target after a previous image task was accepted", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame" });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    let dialog = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));
    expect(await screen.findByText("任务已受理；图像尚未应用到画布。")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片" }));
    fireEvent.click(await screen.findByRole("article", { name: "生成角色" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    dialog = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));

    expect(dialog.getByText("角色素材 · 生成角色")).toBeTruthy();
    expect(dialog.queryByText("分镜 1")).toBeNull();
    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));
    expect(await screen.findByText("任务已受理；图像尚未应用到画布。")).toBeTruthy();

    const submissions = harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image");
    expect(submissions).toHaveLength(2);
    expect(submissions[0]?.body).toEqual({
      model_id: "image-model-private-id",
      prompt: "等待图像记录",
      target: {
        kind: "canvas_apply",
        destination: {
          kind: "frame_preview",
          chapter_id: "generation-stable-frame-chapter",
          storyboard_asset_id: "board-generation",
        },
      },
    });
    expect(submissions[1]?.body).toEqual({
      model_id: "image-model-private-id",
      prompt: "生成角色",
      target: {
        kind: "canvas_apply",
        destination: {
          kind: "asset_image",
          chapter_id: "generation-stable-frame-chapter",
          asset_type: "character",
          asset_id: "character-generation",
        },
      },
    });
  });

  it("requires an explicit current edit lock before submitting a single-image task", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame", requiresLock: true, lockMode: "unlocked" });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const dialogElement = await screen.findByRole("dialog", { name: "生成图片" });
    const dialog = await waitForImageModelOption(dialogElement);

    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(0);
    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));

    expect(await dialog.findByText("当前账号尚未持有有效章节编辑锁。请明确取得锁后再次提交。")).toBeTruthy();
    expect(harness.lockPostCount()).toBe(0);
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(0);

    fireEvent.click(dialog.getByRole("button", { name: "取得编辑锁" }));
    expect(await dialog.findByText("已取得并核实当前章节编辑锁。请再次明确提交图片任务。")).toBeTruthy();
    expect(harness.lockPostCount()).toBe(1);
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(0);

    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));
    expect(await screen.findByText("任务已受理；图像尚未应用到画布。")).toBeTruthy();
    expect(harness.lockPostCount()).toBe(1);
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(1);
  });

  it("sends only one image task when submission is activated twice synchronously", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame" });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const dialog = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    const submit = dialog.getByRole("button", { name: "提交单张图片任务" });

    act(() => {
      fireEvent.click(submit);
      fireEvent.click(submit);
    });

    expect(await screen.findByText("任务已受理；图像尚未应用到画布。")).toBeTruthy();
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it("confirms the original image submission through an explicit lookup without resubmitting or applying it", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "stable-frame",
      imageSubmitReplies: [{ status: 503 }],
      imageLookupReplies: [{ status: 200, receiptStatus: "queued" }],
    });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const form = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    fireEvent.click(form.getByRole("button", { name: "提交单张图片任务" }));
    await screen.findByRole("dialog", { name: "核对原生成任务" });

    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.method === "GET" && request.url.pathname === "/api/submissions/image.single")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "继续核对原操作" }));

    const receiptDialog = within(await screen.findByRole("dialog", { name: "生成任务状态" }));
    expect(await receiptDialog.findByText("任务状态：排队中")).toBeTruthy();
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.method === "GET" && request.url.pathname === "/api/submissions/image.single")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it("keeps a no-ID frame without a canonical server digest disabled", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "invalid-no-id" });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    expect(screen.getByRole("button", { name: "生成图片" })).toBeDisabled();
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(0);
  });

  it("refuses image submission after the fresh source version changes", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame", freshSourceVersion: "f".repeat(64) });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const dialog = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));

    await waitFor(() => expect(harness.requests.filter((request) => request.method === "GET"
      && request.url.pathname.endsWith("/canvas-source")).length).toBeGreaterThan(1));
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(0);
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it.each([
    ["foreign", "章节编辑锁由其他成员持有，未发送图片任务。", "其他成员正在编辑；未更改其锁，也没有发送图片任务。"],
    ["expired", "当前账号的编辑锁已失效，请重新核对后再提交。", "当前账号的编辑锁已失效；没有自动续期或发送图片任务。"],
  ] as const)("does not submit an image task with a %s edit lock", async (lockMode, checkMessage, acquireMessage) => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame", requiresLock: true, lockMode });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const dialog = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));

    expect(await dialog.findByText(checkMessage)).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "取得编辑锁" }));
    expect(await dialog.findByText(acquireMessage)).toBeTruthy();
    expect(harness.lockPostCount()).toBe(0);
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(0);
  });

  it("ignores a late image receipt after closing the form and changing session scope", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame", deferredImageSubmit: true });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const dialog = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));
    await harness.deferredImageSubmitStarted;
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片" }));
    harness.rerender(3);
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "继续核对原提交" })).toBeNull();
      expect(screen.queryByText(/上次图片任务提交结果暂时无法确认/)).toBeNull();
    });
    expect(screen.getByRole("article", { name: "分镜 1" })).toBeTruthy();

    await act(async () => {
      harness.resolveDeferredImageSubmit({
        submission_id: "late-image-submission",
        operation: "image.single",
        task_id: "late-image-task",
        message_id: null,
        status: "queued",
        quoted_amount: 12,
        billing_status: "unbilled",
      });
      await harness.deferredImageResponseHandled;
      await Promise.resolve();
    });

    expect(screen.queryByRole("button", { name: "继续核对原提交" })).toBeNull();
    expect(screen.queryByText("最近的单张图片任务已有受理记录。")).toBeNull();
    expect(harness.token()).toBe("generation-fixture-token");
    expect(harness.onUnauthorized).not.toHaveBeenCalled();
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it.each([
    ["no-id-frame", "分镜 1", {
      kind: "frame_preview",
      chapter_id: "generation-no-id-frame-chapter",
      identity_mode: "strict-no-id-v1",
      frame_index: 0,
      expected_chapter_digest: "e".repeat(64),
    }],
    ["character", "生成角色", {
      kind: "asset_image",
      chapter_id: "generation-character-chapter",
      asset_type: "character",
      asset_id: "character-generation",
    }],
    ["scene", "生成场景", {
      kind: "asset_image",
      chapter_id: "generation-scene-chapter",
      asset_type: "scene",
      asset_id: "scene-generation",
    }],
    ["prop", "生成道具", {
      kind: "asset_image",
      chapter_id: "generation-prop-chapter",
      asset_type: "prop",
      asset_id: "prop-generation",
    }],
  ] as const)("submits one image task with the captured %s destination", async (targetKind, nodeName, destination) => {
    const harness = mountGenerationWorkspace({ targetKind });
    fireEvent.click(await screen.findByRole("article", { name: nodeName }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const dialog = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    expect((dialog.getByLabelText("图片模型") as HTMLSelectElement).value).toBe("image-model-private-id");
    expect((dialog.getByLabelText("提示词") as HTMLTextAreaElement).value).toBe(targetKind === "no-id-frame" ? "等待图像记录"
      : targetKind === "character" ? "生成角色" : targetKind === "scene" ? "生成场景" : "生成道具");

    fireEvent.click(dialog.getByRole("button", { name: "提交单张图片任务" }));

    expect(await screen.findByText("任务已受理；图像尚未应用到画布。")).toBeTruthy();
    const submissions = harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image");
    expect(submissions).toHaveLength(1);
    expect(submissions[0]?.body).toEqual({
      model_id: "image-model-private-id",
      prompt: targetKind === "no-id-frame" ? "等待图像记录"
        : targetKind === "character" ? "生成角色" : targetKind === "scene" ? "生成场景" : "生成道具",
      target: { kind: "canvas_apply", destination },
    });
    expect(submissions[0]?.headers.get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/iu);
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
    expect(harness.requests.filter((request) => request.method === "GET" && request.url.pathname === "/api/submissions/image.single")).toHaveLength(0);
  });

  it("retains an uncertain admission across close and target changes, then explicitly checks and retries the exact request", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "stable-frame",
      imageSubmitReplies: [{ status: 503 }, { status: 200, receiptStatus: "queued" }],
      imageLookupReplies: [{ status: 404 }],
    });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片" }));
    const form = await waitForImageModelOption(await screen.findByRole("dialog", { name: "生成图片" }));
    fireEvent.click(form.getByRole("button", { name: "提交单张图片任务" }));

    expect(await screen.findByRole("dialog", { name: "核对原生成任务" })).toBeTruthy();
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.method === "GET" && request.url.pathname === "/api/submissions/image.single")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "新增便签" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存布局" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    const closePrompt = within(await screen.findByRole("dialog", { name: "关闭创作画布" }));
    expect(closePrompt.getByText(/单张图片任务提交结果仍未确认/)).toBeTruthy();
    expect(closePrompt.getByRole("button", { name: "放弃本地草稿并关闭" })).toBeDisabled();
    fireEvent.click(closePrompt.getByRole("button", { name: "继续编辑" }));

    fireEvent.click(await screen.findByRole("article", { name: "生成角色" }));
    expect(screen.getByRole("button", { name: "继续核对原提交" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "继续核对原提交" }));
    expect(await screen.findByRole("dialog", { name: "核对原生成任务" })).toBeTruthy();
    expect(harness.requests.filter((request) => request.method === "GET" && request.url.pathname === "/api/submissions/image.single")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "继续核对原操作" }));
    const pendingDialog = within(screen.getByRole("dialog", { name: "核对原生成任务" }));
    await waitFor(() => expect(pendingDialog.getAllByText(/暂未查到任务记录。之前的请求仍可能稍后完成/)).toHaveLength(2));
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.method === "GET" && request.url.pathname === "/api/submissions/image.single")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "重新提交原操作" }));
    expect(await screen.findByText("任务已受理；图像尚未应用到画布。")).toBeTruthy();
    const submissions = harness.requests.filter((request) => request.method === "POST" && request.url.pathname === "/api/chat/image");
    expect(submissions).toHaveLength(2);
    expect(submissions[1]?.body).toEqual(submissions[0]?.body);
    expect(submissions[1]?.headers.get("Idempotency-Key")).toBe(submissions[0]?.headers.get("Idempotency-Key"));
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it("exposes generation records for a selected stable frame through API Workspace services", async () => {
    const storage = memoryStorage();
    storage.setItem(API_TOKEN_KEY, "generation-fixture-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "demo-user" }));
    const currentSeries = demoSeries[0]!;
    const currentChapter: Chapter = {
      id: "generation-chapter",
      series_id: currentSeries.id,
      title: "生成记录章节",
      content: [{
        text: "等待图像记录",
        character: [],
        scene: [],
        prop: [],
        storyboard: ["board-generation"],
        preview: null,
        videos: [],
      }],
      order: 1,
      created_at: "2026-10-25T00:00:00Z",
      updated_at: "2026-10-25T00:00:00Z",
      lock: null,
    };
    const storyboardAsset: StoryboardAsset = {
      id: "board-generation",
      series_id: currentSeries.id,
      chapter_id: currentChapter.id,
      frame_index: 0,
      name: "生成分镜",
      description: null,
      image_url: null,
      created_at: currentChapter.created_at,
      updated_at: currentChapter.updated_at,
    };
    const documentJson = {
      nodeLayouts: {},
      notes: [],
      cards: [],
      manualEdges: [],
      hiddenRelations: [],
      viewport: { x: 0, y: 0, zoom: 1 },
    };
    const sourceSnapshot = {
      capability: "canvas-source-v1",
      series_id: currentSeries.id,
      chapter_id: currentChapter.id,
      version: "a".repeat(64),
      requires_lock: false,
      chapter: currentChapter,
      storyboard_assets: [storyboardAsset],
      characters: [],
      scenes: [],
      props: [],
    };
    const target = {
      kind: "frame_preview",
      chapter_id: currentChapter.id,
      storyboard_asset_id: storyboardAsset.id,
    };
    const targetSnapshot = {
      kind: "canvas_frame_preview",
      chapter_id: currentChapter.id,
      series_id: currentSeries.id,
      stable_storyboard_asset_id: storyboardAsset.id,
      asset_type: "storyboard",
      asset_id: storyboardAsset.id,
      compat_frame_index: 0,
      expected_text_digest: null,
      media_identity: null,
      content_digest: null,
      notes_revision: null,
      full_frame_digest: "a".repeat(64),
      full_chapter_digest: "b".repeat(64),
      expected_asset_image_url: null,
      same_series_target_digest: null,
    };
    const ownedResult = {
      task_id: "generation-task",
      result_ref: "generation-result",
      operation: "image.single",
      completed_at: "2026-10-25T00:00:00Z",
      expected_target: targetSnapshot,
      application_key: "c".repeat(64),
      eligibility: "ready",
      application: null,
    };
    const requests: Array<{ url: URL; method: string; body: unknown }> = [];
    const onSourceChanged = vi.fn();
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(String(init.body)) as unknown;
      requests.push({ url, method, body });
      if (url.pathname === `/api/series/${currentSeries.id}/chapters`) {
        return Response.json([currentChapter]);
      }
      if (url.pathname === `/api/chapters/${currentChapter.id}/canvas-source`) {
        return Response.json(sourceSnapshot);
      }
      if (method === "GET" && url.pathname === `/api/chapters/${currentChapter.id}/canvas-owned-results`) {
        return Response.json({
          chapter_id: currentChapter.id,
          target,
          items: [ownedResult],
          next_cursor: "YWJj",
        });
      }
      if (method === "PUT" && url.pathname === `/api/chapters/${currentChapter.id}`) {
        const request = body as { generation_application: { task_id: string; result_ref: string } };
        return Response.json({
          task_id: request.generation_application.task_id,
          result_ref: request.generation_application.result_ref,
          status: "applied",
          application: {
            application_ref: "generation-application",
            target_revision: "d".repeat(64),
            committed_at: "2026-10-25T00:00:00+00:00",
          },
        });
      }
      if (url.pathname === `/api/chapters/${currentChapter.id}/canvas`) {
        return Response.json({
          id: "generation-canvas",
          chapter_id: currentChapter.id,
          version: 1,
          document_json: documentJson,
          updated_by: "demo-user",
          updated_at: currentChapter.updated_at,
          save_capability: "guarded-v1",
        });
      }
      throw new Error(`Unexpected API request: ${url.pathname}`);
    });
    const services = createApiServices("http://127.0.0.1:8000/api", { storage, fetcher });
    const canvasPort = services.canvasData;
    if (canvasPort === undefined) throw new Error("API services did not expose the canvas port.");

    render(
      <CanvasWorkspace
        chapter={currentChapter}
        onClose={vi.fn()}
        onUnauthorized={vi.fn()}
        onSourceChanged={onSourceChanged}
        port={canvasPort}
        series={currentSeries}
        services={services}
        sessionGeneration={2}
        userId="demo-user"
      />,
    );

    const frame = await screen.findByRole("article", { name: "分镜 1" });
    fireEvent.click(frame);
    expect(services.canvasOwnedResults).toBeDefined();
    expect(services.canvasGenerationApplications).toBeDefined();
    expect(await screen.findByRole("button", { name: "生成图片记录" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    const discoveryRequests = requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results"));
    expect(discoveryRequests).toHaveLength(1);
    expect(discoveryRequests[0]?.url.searchParams.get("target_kind")).toBe("frame_preview");
    expect(discoveryRequests[0]?.url.searchParams.get("storyboard_asset_id")).toBe(storyboardAsset.id);
    expect(screen.getByRole("button", { name: "加载下一页" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    expect(await generationResultsDialog().findByText(/应用成功。已找到对应记录/)).toBeTruthy();
    const applicationRequests = requests.filter((request) => request.method === "PUT");
    expect(applicationRequests).toHaveLength(1);
    expect(applicationRequests[0]?.body).toEqual({
      generation_application: {
        task_id: "generation-task",
        result_ref: "generation-result",
        expected_target: targetSnapshot,
        application_key: "c".repeat(64),
      },
    });
    expect(requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results/application"))).toHaveLength(0);
    await waitFor(() => expect(onSourceChanged).toHaveBeenCalledWith(currentChapter.id));
  });

  it("keeps a strict no-ID application intent until the original receipt is read and explicitly retried", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "no-id-frame",
      putReplies: [{ status: 503 }, { status: 200, resultStatus: "already_applied" }],
      lookupReplies: [{ status: "not_applied" }],
      failSourceReadAt: 5,
    });

    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    const discoveryRequests = () => harness.requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results"));
    await waitFor(() => expect(discoveryRequests()).toHaveLength(1));
    expect(discoveryRequests()[0]?.url.searchParams.get("target_kind")).toBe("frame_preview");
    expect(discoveryRequests()[0]?.url.searchParams.get("identity_mode")).toBe("strict-no-id-v1");
    expect(discoveryRequests()[0]?.url.searchParams.get("frame_index")).toBe("0");
    expect(discoveryRequests()[0]?.url.searchParams.get("expected_chapter_digest")).toBe("e".repeat(64));
    expect(discoveryRequests()[0]?.url.searchParams.has("storyboard_asset_id")).toBe(false);
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(0);

    const applyButton = screen.getByRole("button", { name: "应用到当前目标" });
    fireEvent.click(applyButton);
    fireEvent.click(applyButton);
    expect(await generationResultsDialog().findByText(/上次提交结果暂时无法确认/)).toBeTruthy();
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "保存布局" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "编辑分镜文字" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "插入分镜" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片记录" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭创作画布" }));
    const closePrompt = within(await screen.findByRole("dialog", { name: "关闭创作画布" }));
    expect(closePrompt.getByText(/应用仍未确认/)).toBeTruthy();
    expect(closePrompt.getByRole("button", { name: "放弃本地草稿并关闭" })).toBeDisabled();
    fireEvent.click(closePrompt.getByRole("button", { name: "继续编辑" }));
    expect(screen.getByRole("button", { name: "继续核对原操作" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "继续核对原操作" }));
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results/generation-result/application"))).toHaveLength(0);
    fireEvent.click(await screen.findByRole("button", { name: "继续核对" }));
    const pendingNotice = generationResultsDialog().getByRole("alert");
    await waitFor(() => {
      expect(within(pendingNotice).getAllByText(/暂未查到应用记录，之前请求仍可能稍后完成/)).toHaveLength(2);
    });
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results/generation-result/application"))).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "重新提交原操作" }));
    expect(await generationResultsDialog().findByText(/此前已应用。已找到对应记录/)).toBeTruthy();
    const puts = harness.requests.filter((request) => request.method === "PUT");
    expect(puts).toHaveLength(2);
    expect(puts[1]?.body).toEqual(puts[0]?.body);
    expect(puts[0]?.body).toEqual({
      generation_application: {
        task_id: "generation-task",
        result_ref: "generation-result",
        expected_target: harness.targetSnapshot,
        application_key: "c".repeat(64),
      },
    });
    expect(await screen.findByText(/应用记录已确认，但当前章节内容未能刷新/)).toBeTruthy();
    expect(screen.getAllByRole("status").some((status) => status.textContent?.includes("不代表当前图像仍未被后续手工修改"))).toBe(true);
    expect(harness.onSourceChanged).toHaveBeenCalledWith(harness.currentChapter.id);
    expect(harness.documentJson).toEqual(harness.originalDocumentJson);
    expect(harness.requests.filter((request) => request.method === "PUT" && request.url.pathname.endsWith("/canvas"))).toHaveLength(0);
  });

  it("retains an applied readback proof when the read-only source refresh fails", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "no-id-frame",
      putReplies: [{ status: 503 }],
      lookupReplies: [{ status: "applied" }],
      failSourceReadAt: 4,
    });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    expect(await generationResultsDialog().findByText(/上次提交结果暂时无法确认/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片记录" }));
    fireEvent.click(screen.getByRole("button", { name: "继续核对原操作" }));
    fireEvent.click(await screen.findByRole("button", { name: "继续核对" }));

    expect(await screen.findByText(/应用记录已确认，但当前章节内容未能刷新/)).toBeTruthy();
    expect(generationResultsDialog().getByText(/此前已应用。已找到对应记录/)).toBeTruthy();
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(1);
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results/generation-result/application"))).toHaveLength(1);
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(0);
  });

  it("reads the selected historical image only on demand without a lock or canvas write", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "stable-frame",
      eligibility: "stale",
      requiresLock: true,
      lockMode: "unlocked",
      ownedImageReplies: [{ status: 200, contentType: "image/webp", bytes: [1, 2, 3, 4] }],
    });
    const createObjectURL = vi.fn((_blob: Blob) => "blob:owned-canvas-image");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });

    const dialog = await openGenerationResultsFor();
    expect(ownedImageRequests(harness)).toHaveLength(0);
    expect(dialog.getByRole("button", { name: "查看图片" })).toBeEnabled();

    fireEvent.click(dialog.getByRole("button", { name: "查看图片" }));
    const image = await screen.findByRole("img", { name: "生成结果图片预览" });
    expect(image.getAttribute("src")).toBe("blob:owned-canvas-image");
    const reads = ownedImageRequests(harness);
    expect(reads).toHaveLength(1);
    expect(reads[0]?.url.pathname).toBe(
      `/api/chapters/${harness.currentChapter.id}/canvas-owned-results/generation-result/image`,
    );
    expect(reads[0]?.url.searchParams.get("task_id")).toBe("generation-task");
    expect(reads[0]?.url.searchParams.get("result_ref")).toBeNull();
    expect(reads[0]?.body).toBeNull();
    expect(createObjectURL).toHaveBeenCalledOnce();
    const blob = createObjectURL.mock.calls[0]?.[0];
    expect(blob?.type).toBe("image/webp");
    expect([...new Uint8Array(await blob!.arrayBuffer())]).toEqual([1, 2, 3, 4]);
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(0);
    expect(harness.requests.some((request) => request.method === "POST" || request.method === "PUT")).toBe(false);
    expect(harness.documentJson).toEqual(harness.originalDocumentJson);

    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片记录" }));
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith("blob:owned-canvas-image"));
    expect(revokeObjectURL).toHaveBeenCalledOnce();
  });

  it("keeps unready owned images unavailable and requires an explicit retry after read failure", async () => {
    const unreadyHarness = mountGenerationWorkspace({ targetKind: "stable-frame", eligibility: "unready" });
    const unreadyDialog = await openGenerationResultsFor();
    expect(unreadyDialog.getByRole("button", { name: "查看图片" })).toBeDisabled();
    expect(unreadyDialog.getByText("记录尚未就绪，暂时不能读取图片。")).toBeTruthy();
    expect(ownedImageRequests(unreadyHarness)).toHaveLength(0);
    cleanup();

    const retryHarness = mountGenerationWorkspace({
      targetKind: "stable-frame",
      eligibility: "already_applied",
      ownedImageReplies: [
        { status: 503 },
        { status: 200, contentType: "image/png", bytes: [9, 8, 7] },
      ],
    });
    const createObjectURL = vi.fn((_blob: Blob) => "blob:retried-canvas-image");
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    const retryDialog = await openGenerationResultsFor();
    fireEvent.click(retryDialog.getByRole("button", { name: "查看图片" }));

    expect(await retryDialog.findByText("图片读取服务暂不可用，请稍后重试。", { selector: "p" })).toBeTruthy();
    expect(ownedImageRequests(retryHarness)).toHaveLength(1);
    expect(createObjectURL).not.toHaveBeenCalled();

    fireEvent.click(retryDialog.getByRole("button", { name: "重试查看图片" }));
    expect(await screen.findByRole("img", { name: "生成结果图片预览" })).toBeTruthy();
    expect(ownedImageRequests(retryHarness)).toHaveLength(2);
    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(retryHarness.requests.filter((request) => request.method === "POST" || request.method === "PUT")).toHaveLength(0);
  });

  it.each(["session", "actor", "chapter", "source", "close"] as const)(
    "aborts an owned image read and rejects its late blob after %s scope changes",
    async (changedScope) => {
      const harness = mountGenerationWorkspace({ targetKind: "stable-frame", deferredOwnedImageRead: true });
      const createObjectURL = vi.fn((_blob: Blob) => "blob:late-canvas-image");
      Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
      const dialog = await openGenerationResultsFor();
      fireEvent.click(dialog.getByRole("button", { name: "查看图片" }));
      await harness.deferredOwnedImageReadStarted;
      const pendingRead = ownedImageRequests(harness)[0];
      expect(pendingRead?.signal).not.toBeNull();
      expect(pendingRead?.signal?.aborted).toBe(false);

      if (changedScope === "session") {
        harness.rerender(3);
      } else if (changedScope === "actor") {
        harness.setStoredActor("other-user");
        harness.rerender(2, "other-user");
      } else if (changedScope === "chapter") {
        harness.rerender(2, "demo-user", { ...harness.currentChapter, id: "replacement-chapter" });
      } else if (changedScope === "source") {
        harness.setFreshSourceVersion("f".repeat(64));
        fireEvent.click(dialog.getByRole("button", { name: "重新读取当前页" }));
      } else {
        fireEvent.click(screen.getByRole("button", { name: "关闭生成图片记录" }));
      }

      await waitFor(() => expect(pendingRead?.signal?.aborted).toBe(true));
      await act(async () => {
        harness.resolveDeferredOwnedImageRead();
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(createObjectURL).not.toHaveBeenCalled();
      expect(screen.queryByRole("img", { name: "生成结果图片预览" })).toBeNull();
      expect(harness.requests.filter((request) => request.method === "POST" || request.method === "PUT")).toHaveLength(0);
    },
  );

  it("releases the owned object URL when a candidate disappears from the refreshed page", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame" });
    const createObjectURL = vi.fn((_blob: Blob) => "blob:removed-candidate-image");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectURL });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectURL });
    const dialog = await openGenerationResultsFor();
    fireEvent.click(dialog.getByRole("button", { name: "查看图片" }));
    await screen.findByRole("img", { name: "生成结果图片预览" });
    expect(revokeObjectURL).not.toHaveBeenCalled();

    harness.setOwnedResultVisible(false);
    fireEvent.click(dialog.getByRole("button", { name: "重新读取当前页" }));
    await waitFor(() => expect(dialog.getByText("当前目标没有可显示的图片生成记录。")).toBeTruthy());
    await waitFor(() => expect(revokeObjectURL).toHaveBeenCalledWith("blob:removed-candidate-image"));
    expect(screen.queryByRole("img", { name: "生成结果图片预览" })).toBeNull();
  });

  it("reads an application proof without lock traffic even when the chapter requires a lock for writes", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "stable-frame",
      requiresLock: true,
      lockMode: "unlocked",
      putReplies: [{ status: 503 }],
      lookupReplies: [{ status: "applied" }],
    });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    fireEvent.click(await screen.findByRole("button", { name: "取得编辑锁" }));
    expect(await screen.findByText(/已取得并核实当前章节编辑锁/)).toBeTruthy();
    expect(harness.lockPostCount()).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    expect(await generationResultsDialog().findByText(/上次提交结果暂时无法确认/)).toBeTruthy();
    const lockRequestsBeforeLookup = harness.requests.filter((request) => request.url.pathname.endsWith("/lock")).length;

    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片记录" }));
    fireEvent.click(screen.getByRole("button", { name: "继续核对原操作" }));
    fireEvent.click(await screen.findByRole("button", { name: "继续核对" }));
    expect(await generationResultsDialog().findByText(/此前已应用。已找到对应记录/)).toBeTruthy();
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(lockRequestsBeforeLookup);
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(1);
  });

  it.each([
    ["character", "生成角色", "character"],
    ["scene", "生成场景", "scene"],
    ["prop", "生成道具", "prop"],
  ] as const)("queries the selected %s image target with its exact asset identity", async (targetKind, nodeName, assetType) => {
    const harness = mountGenerationWorkspace({ targetKind });
    fireEvent.click(await screen.findByRole("article", { name: nodeName }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));

    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    const request = harness.requests.find((entry) => entry.url.pathname.endsWith("/canvas-owned-results"));
    expect(request?.url.searchParams.get("target_kind")).toBe("asset_image");
    expect(request?.url.searchParams.get("asset_type")).toBe(assetType);
    expect(request?.url.searchParams.get("asset_id")).toBe(`${assetType}-generation`);
    expect(harness.requests.filter((entry) => entry.method === "PUT")).toHaveLength(0);
  });

  it("disables generation discovery when the selected legacy frame has no confirmed digest", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "invalid-no-id" });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));

    expect(screen.getByRole("button", { name: "生成图片记录" })).toBeDisabled();
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results"))).toHaveLength(0);
  });

  it.each(["foreign", "expired"] as const)("does not apply a result while the %s edit lock is invalid", async (lockMode) => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame", requiresLock: true, lockMode });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    await waitFor(() => expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(1));
    expect(harness.requests.filter((request) => request.method === "POST" && request.url.pathname.endsWith("/lock"))).toHaveLength(0);
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);
  });

  it("requires explicit lock acquisition and another explicit apply action", async () => {
    const harness = mountGenerationWorkspace({ targetKind: "stable-frame", requiresLock: true, lockMode: "unlocked" });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    expect(harness.requests.filter((request) => request.url.pathname.endsWith("/lock"))).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    fireEvent.click(await screen.findByRole("button", { name: "取得编辑锁" }));
    expect(await screen.findByText(/已取得并核实当前章节编辑锁/)).toBeTruthy();
    expect(harness.lockPostCount()).toBe(1);
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "应用到当前目标" }));
    expect(await generationResultsDialog().findByText(/应用成功。已找到对应记录/)).toBeTruthy();
    expect(harness.requests.filter((request) => request.method === "PUT")).toHaveLength(1);
  });

  it("loads one page only on demand and ignores a late page from the previous session", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "stable-frame",
      initialNextCursor: "YWJj",
      deferredNextPage: true,
    });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    const pageRequests = () => harness.requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results"));
    expect(pageRequests()).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "加载下一页" }));
    await harness.deferredPageStarted;
    expect(pageRequests()).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片记录" }));
    harness.rerender(3);
    await waitFor(() => expect(screen.getByRole("button", { name: "生成图片记录" })).toBeEnabled());
    await screen.findByRole("article", { name: "分镜 1" });
    fireEvent.click(screen.getByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();

    await act(async () => {
      harness.resolveDeferredPage({
        chapter_id: harness.currentChapter.id,
        target: harness.target,
        items: [{ ...harness.ownedResult, result_ref: "late-generation-page-two" }],
        next_cursor: null,
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByText(/late-generation-page-two/)).toBeNull();
    expect(pageRequests()).toHaveLength(3);
  });

  it("does not merge a deferred page after the selected generation target changes", async () => {
    const harness = mountGenerationWorkspace({
      targetKind: "stable-frame",
      initialNextCursor: "YWJj",
      deferredNextPage: true,
    });
    fireEvent.click(await screen.findByRole("article", { name: "分镜 1" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "加载下一页" }));
    await harness.deferredPageStarted;
    fireEvent.click(screen.getByRole("button", { name: "关闭生成图片记录" }));

    fireEvent.click(await screen.findByRole("article", { name: "生成角色" }));
    fireEvent.click(screen.getByRole("button", { name: "生成图片记录" }));
    expect(await screen.findByText("可应用到当前目标")).toBeTruthy();
    const discoveryRequests = harness.requests.filter((request) => request.url.pathname.endsWith("/canvas-owned-results"));
    expect(discoveryRequests).toHaveLength(3);
    expect(discoveryRequests[2]?.url.searchParams.get("target_kind")).toBe("asset_image");
    expect(discoveryRequests[2]?.url.searchParams.get("asset_type")).toBe("character");
    expect(discoveryRequests[2]?.url.searchParams.get("asset_id")).toBe("character-generation");

    await act(async () => {
      harness.resolveDeferredPage({
        chapter_id: harness.currentChapter.id,
        target: harness.target,
        items: [{ ...harness.ownedResult, result_ref: "late-generation-page-two" }],
        next_cursor: null,
      });
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByText(/late-generation-page-two/)).toBeNull();
  });

  it("opens through ChapterBrowser, writes via the authenticated HTTP adapter, then restores on reopen", async () => {
    const storage = memoryStorage();
    const sourceServices = createDemoServices(storage);
    await sourceServices.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    storage.setItem(API_TOKEN_KEY, "canvas-http-fixture-token");
    storage.setItem(API_USER_KEY, JSON.stringify({ id: "demo-user" }));
    const state = {
      id: null as string | null,
      version: 1,
      document: { version: 1, viewport: { x: 0, y: 0, zoom: 1 }, nodes: [], edges: [], notes: [] } as Record<string, unknown>,
      updatedBy: null as string | null,
      updatedAt: null as string | null,
    };
    let releasePutResponseBody!: () => void;
    const putResponseBodyGate = new Promise<void>((resolve) => {
      releasePutResponseBody = resolve;
    });
    const putResponseBodyStarted = vi.fn();
    const requests: Array<{ url: URL; method: string; body: unknown; headers: Headers }> = [];
    const fetcher = vi.fn<typeof fetch>(async (input, init) => {
      const url = new URL(String(input));
      const method = init?.method ?? "GET";
      const body = init?.body === undefined ? null : JSON.parse(String(init.body)) as unknown;
      requests.push({ url, method, body, headers: new Headers(init?.headers) });
      if (method === "GET") {
        return Response.json({
          id: state.id,
          chapter_id: decodeURIComponent(url.pathname.split("/").at(-2) ?? ""),
          version: state.version,
          document_json: state.document,
          updated_by: state.updatedBy,
          updated_at: state.updatedAt,
          save_capability: "guarded-v1",
        });
      }
      const request = body as {
        document_json: Record<string, unknown>;
        save_guard: { id: string | null; version: number };
      };
      state.id ??= "canvas-http-a";
      state.version = request.save_guard.id === null ? 1 : request.save_guard.version + 1;
      state.document = request.document_json;
      state.updatedBy = "demo-user";
      state.updatedAt = "2026-10-22T04:00:00Z";
      const response = Response.json({
        id: state.id,
        chapter_id: decodeURIComponent(url.pathname.split("/").at(-2) ?? ""),
        version: state.version,
        document_json: state.document,
        updated_by: state.updatedBy,
        updated_at: state.updatedAt,
        save_receipt: { mode: "guarded-v1", id: state.id, version: state.version },
      });
      const readResponseJson = response.json.bind(response);
      response.json = async () => {
        putResponseBodyStarted();
        await putResponseBodyGate;
        return readResponseJson();
      };
      return response;
    });
    const port: CanvasDataPort = createCanvasDataHttpPort("http://127.0.0.1:8000/api", { storage, fetcher });
    const services = { ...sourceServices, canvasData: port } as WorkspaceServices;
    const onUnauthorized = vi.fn();
    const series = demoSeries[0]!;
    try {
      render(
        <ChapterBrowser
          onBack={vi.fn()}
          onUnauthorized={onUnauthorized}
          sessionGeneration={2}
          series={series}
          services={services}
          userId="demo-user"
        />,
      );

      const openButton = await screen.findByRole("button", { name: "创作画布" });
      fireEvent.click(openButton);
      const firstRegion = await screen.findByRole("region", { name: "画布工作区" });
      const firstDialog = firstRegion.closest('[role="dialog"]');
      if (!(firstDialog instanceof HTMLElement)) throw new Error("Canvas dialog was not rendered.");
      const canvas = within(firstDialog);
      expect(await canvas.findByRole("article", { name: "分镜 1" })).toBeTruthy();
      fireEvent.click(canvas.getByRole("article", { name: "分镜 1" }));
      expect(canvas.getByRole("button", { name: "编辑分镜文字" })).toBeDisabled();
      const firstGet = requests.find((request) => request.method === "GET"
        && request.url.pathname.startsWith("/api/chapters/") && request.url.pathname.endsWith("/canvas"));
      if (firstGet === undefined) throw new Error("Canvas GET was not sent.");
      const canvasGets = () => requests.filter((request) => request.method === "GET"
        && request.url.pathname === firstGet.url.pathname);
      expect(canvasGets()).toHaveLength(1);
      expect(firstGet.url.searchParams.get("save_mode")).toBe("guarded-v1");
      expect(firstGet.headers.get("Authorization")).toBe("Bearer canvas-http-fixture-token");

      await canvas.findByText("已保存");
      const addCardButton = canvas.getByRole("button", { name: "新增文本卡片" });
      expect(addCardButton).not.toBeDisabled();
      fireEvent.click(addCardButton);
      fireEvent.change(await canvas.findByLabelText("文本卡片文字"), { target: { value: "保存后的章节构图说明" } });
      fireEvent.click(canvas.getByRole("button", { name: "保存布局" }));
      await waitFor(() => expect(putResponseBodyStarted).toHaveBeenCalledOnce());
      const putRequest = requests.find((request) => request.method === "PUT");
      expect(putRequest?.url.pathname).toBe(firstGet.url.pathname);
      expect(putRequest?.headers.get("Authorization")).toBe("Bearer canvas-http-fixture-token");
      expect(putRequest?.body).toMatchObject({
        version: 1,
        save_guard: { mode: "guarded-v1", id: null, version: 1 },
        document_json: { cards: [expect.objectContaining({ text: "保存后的章节构图说明" })] },
      });
      expect(canvas.getByText("正在保存…")).toBeTruthy();
      expect(canvas.queryByText("已保存")).toBeNull();

      // A request reaching the fixture is not a validated save receipt.
      await act(async () => { releasePutResponseBody(); });
      await canvas.findByText("已保存");
      fireEvent.click(canvas.getByRole("button", { name: "关闭创作画布" }));
      await waitFor(() => expect(firstDialog.isConnected).toBe(false));

      const reopenButton = await screen.findByRole("button", { name: "创作画布" });
      expect(reopenButton.closest("[inert]")).toBeNull();
      fireEvent.click(reopenButton);
      const reopenedRegion = await screen.findByRole("region", { name: "画布工作区" });
      expect(reopenedRegion).not.toBe(firstRegion);
      const reopenedDialog = reopenedRegion.closest('[role="dialog"]');
      if (!(reopenedDialog instanceof HTMLElement)) throw new Error("Reopened canvas dialog was not rendered.");
      expect(await within(reopenedDialog).findByDisplayValue("保存后的章节构图说明")).toBeTruthy();
      await waitFor(() => expect(canvasGets()).toHaveLength(2));
      expect(requests.filter((request) => request.method === "PUT")).toHaveLength(1);
      expect(state.version).toBe(1);
      expect(onUnauthorized).not.toHaveBeenCalled();
    } finally {
      releasePutResponseBody();
      cleanup();
    }
  });
});
