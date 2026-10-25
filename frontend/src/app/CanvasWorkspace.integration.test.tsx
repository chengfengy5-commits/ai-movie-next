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
  const requests: Array<{ url: URL; method: string; body: unknown }> = [];
  const putReplies = [...(options.putReplies ?? [])];
  const lookupReplies = [...(options.lookupReplies ?? [])];
  let sourceReadCount = 0;
  let lockAcquired = false;
  let lockPostCount = 0;
  let releaseDeferredPage: ((response: Response) => void) | null = null;
  let markDeferredPageStarted: (() => void) | null = null;
  const deferredPageStarted = new Promise<void>((resolve) => { markDeferredPageStarted = resolve; });
  let deferredPageUsed = false;
  const lockState = () => options.lockMode === "foreign"
    ? { locked: true, user_id: "other-user", is_mine: false, expires_at: "2026-10-26T00:00:00Z" }
    : options.lockMode === "expired"
      ? { locked: true, user_id: "demo-user", is_mine: true, expires_at: "2020-01-01T00:00:00Z" }
      : lockAcquired
        ? { locked: true, user_id: "demo-user", is_mine: true, expires_at: "2026-10-26T00:00:00Z" }
        : { locked: false };
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const url = new URL(String(input));
    const method = init?.method ?? "GET";
    const body = init?.body === undefined ? null : JSON.parse(String(init.body)) as unknown;
    requests.push({ url, method, body });
    if (method === "GET" && url.pathname === `/api/series/${currentSeries.id}/chapters`) {
      return Response.json([currentChapter]);
    }
    if (method === "GET" && url.pathname === `/api/chapters/${currentChapter.id}/canvas-source`) {
      sourceReadCount += 1;
      if (sourceReadCount === options.failSourceReadAt) return new Response("source unavailable", { status: 503 });
      return Response.json(sourceSnapshot);
    }
    if (method === "GET" && url.pathname === `/api/chapters/${currentChapter.id}/canvas`) {
      return Response.json({
        id: "generation-canvas", chapter_id: currentChapter.id, version: 1, document_json: documentJson,
        updated_by: "demo-user", updated_at: timestamp, save_capability: "guarded-v1",
      });
    }
    if (method === "GET" && url.pathname === `/api/chapters/${currentChapter.id}/canvas-owned-results`) {
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
      return Response.json({ chapter_id: currentChapter.id, target: pageTarget, items: [item], next_cursor: nextCursor });
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
    if (url.pathname === `/api/chapters/${currentChapter.id}/lock`) {
      if (method === "GET") return Response.json(lockState());
      if (method === "POST") {
        lockPostCount += 1;
        lockAcquired = true;
        return Response.json(lockState());
      }
    }
    if (method === "PUT" && url.pathname === `/api/chapters/${currentChapter.id}`) {
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
  const canvasPort = services.canvasData;
  if (canvasPort === undefined) throw new Error("API services did not expose the canvas port.");
  const workspace = (sessionGeneration = 2) => (
    <CanvasWorkspace
      chapter={currentChapter}
      onClose={vi.fn()}
      onUnauthorized={vi.fn()}
      onSourceChanged={onSourceChanged}
      port={canvasPort}
      series={currentSeries}
      services={services}
      sessionGeneration={sessionGeneration}
      userId="demo-user"
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
    rerender: (sessionGeneration: number) => view.rerender(workspace(sessionGeneration)),
    deferredPageStarted,
    resolveDeferredPage(payload: unknown) { releaseDeferredPage?.(Response.json(payload)); },
    lockPostCount: () => lockPostCount,
  };
}

afterEach(() => cleanup());

function generationResultsDialog() {
  return within(screen.getByRole("dialog", { name: "生成图片记录" }));
}

describe("selected chapter to canvas integration", () => {
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
