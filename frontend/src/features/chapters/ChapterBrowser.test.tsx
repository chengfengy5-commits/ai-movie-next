import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { createElement, useLayoutEffect, useState, type ComponentProps } from "react";
import type { Character, Chapter, ChapterEditLockAcquisition, ChapterEditLockLeaseHandle, ChapterEditLockState, CreateChapterInput, Scene, Series, StoryboardAsset, UpdateChapterInput } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type {
  ChapterGenerationHistoryContext,
  ChapterGenerationHistoryMessage,
  ChapterGenerationHistoryPort,
  ChapterGenerationHistorySnapshot,
} from "../../shared/api/chapterGenerationHistory";
import type { PersonalRoughCutSnapshot } from "../../shared/api/personalRoughCut";
import type { PersonalRoughCutFrameTarget, PersonalRoughCutReadIdentity } from "./PersonalRoughCutPanel";
import {
  parsePersonalProductionSnapshot,
  type PersonalProductionSnapshot,
} from "../../shared/api/personalProductionNotes";
import type { WorkspaceServices } from "../../shared/api/services";
import { createApiServices, createDemoServices } from "../../shared/api/services";
import { API_TOKEN_KEY } from "../../shared/api/storage";
import {
  continuousOperationResultDigest,
  continuousSourceSnapshotBinding,
  continuousTargetManifestDigest,
  type ContinuousChapterOperationKind,
  type ContinuousChapterOperationRequest,
  type ContinuousChapterOperationResponse,
  type ContinuousChapterOperationReceipt,
} from "../../shared/api/continuousChapterOperations";
import type { CreatorModel, CreatorPrompt, CreatorSettingsServicePort } from "../../shared/api/creatorSettings";
import type {
  StoryboardAssetGenerationServicePort,
  StoryboardAssetKind,
  StoryboardAssetSaveInput,
  StoryboardAssetStreamEvent,
} from "../../shared/api/storyboardAssetGeneration";
import storyboardAssetWire from "../../testing/fixtures/storyboardAssetWire.json";
import type { FrameReviewCapabilities, FrameReviewServicePort, FrameReviewTask } from "../../shared/api/frameReview";
import { parseFrameOptimizeTaskStatus } from "../../shared/api/frameOptimizeHttp";
import type {
  FrameOptimizeCapabilities,
  FrameOptimizeServicePort,
  FrameOptimizeSubmitInput,
  FrameOptimizeTaskStatus,
} from "../../shared/api/frameOptimize";
import frameOptimizeStatusWire from "../../testing/fixtures/frameOptimizeStatusWire.json";
import type { StoryboardGenerationServicePort } from "../storyboard-generation/contracts";
import { digestPersonalProductionMediaIdentity } from "./personal-production/mediaIdentity";
import { buildGuardedChapterUpdate, createContinuousChapterDraft } from "./continuousChapterEditing";
import {
  storyboardChapterStorageKey,
  storyboardPageSizeStorageKey,
} from "./storyboardBrowsePreferences";
import { demoSeries } from "../series/demoSeries";
import { getDemoChapterData } from "./demoChapters";
import { ChapterBrowser, type ChapterNavigationIntent } from "./ChapterBrowser";
import {
  continuousChapterOperationJournalKey,
  continuousChapterOperationRoute,
  createContinuousOperationJournalEnvelope,
  freezeContinuousOperationRequest,
  withContinuousChapterOperationLock,
  writeContinuousChapterJournal,
} from "./continuousChapterOperationJournal";
import type {
  PersonalProductionNoteFrameTarget,
  PersonalProductionNoteReadIdentity,
} from "./PersonalProductionNotesPanel";

const personalNotesBridge = vi.hoisted(() => ({
  onLocateResume: null as ((target: { position: number; isCurrent(): boolean }) => boolean | void | Promise<boolean | void>) | null,
  onResumeInvalidated: null as (() => void) | null,
  lastTarget: null as { position: number; isCurrent(): boolean } | null,
  onRegisterFrameRead: null as ((identity: PersonalProductionNoteReadIdentity) => void) | null,
  onFrameReadInvalidated: null as ((identity: PersonalProductionNoteReadIdentity) => void) | null,
  onLocateProductionNoteFrame: null as ((target: PersonalProductionNoteFrameTarget) => boolean | Promise<boolean>) | null,
  onClose: null as ((identity: PersonalProductionNoteReadIdentity | null) => void) | null,
  lastProductionNoteTarget: null as PersonalProductionNoteFrameTarget | null,
  assets: [] as StoryboardAsset[],
}));

const frameAssetReferencesBridge = vi.hoisted(() => ({
  onClose: null as ((owner: unknown) => void) | null,
  owner: null as unknown,
}));

const roughCutPanelBridge = vi.hoisted(() => ({
  onClose: null as (() => void) | null,
  onLocateFrame: null as ((target: PersonalRoughCutFrameTarget) => boolean | Promise<boolean>) | null,
  lastTarget: null as PersonalRoughCutFrameTarget | null,
  onFrameNavigationInvalidated: null as ((identity: PersonalRoughCutReadIdentity) => void) | null,
  lastInvalidation: null as PersonalRoughCutReadIdentity | null,
}));

vi.mock("./PersonalProductionNotesPanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./PersonalProductionNotesPanel")>();
  return {
    ...actual,
    PersonalProductionNotesPanel: (props: ComponentProps<typeof actual.PersonalProductionNotesPanel>) => {
      personalNotesBridge.assets = props.assets;
      personalNotesBridge.onLocateResume = props.onLocateResume;
      personalNotesBridge.onResumeInvalidated = props.onResumeInvalidated;
      personalNotesBridge.onRegisterFrameRead = props.onRegisterFrameRead;
      personalNotesBridge.onFrameReadInvalidated = props.onFrameReadInvalidated;
      personalNotesBridge.onLocateProductionNoteFrame = props.onLocateProductionNoteFrame;
      personalNotesBridge.onClose = props.onClose;
      return createElement(actual.PersonalProductionNotesPanel, {
        ...props,
        onLocateResume: (target) => {
          personalNotesBridge.lastTarget = target;
          return props.onLocateResume(target);
        },
        onLocateProductionNoteFrame: (target) => {
          personalNotesBridge.lastProductionNoteTarget = target;
          return props.onLocateProductionNoteFrame(target);
        },
      });
    },
  };
});

describe("frame generation history entry", () => {
  afterEach(() => cleanup());

  it("opens from a real frame, reads without a lock, and confirms once before refreshing the chapter", async () => {
    const user = userEvent.setup();
    const candidateUrl = "https://cdn.example.test/history/selected.png?signature=kept";
    const target = chapter("chapter-generation-history-entry", "生成历史入口", [
      { text: "历史镜头正文", storyboard: ["history-storyboard-entry"] },
    ]);
    const base = makeServices([target], {
      [target.id]: [{ ...asset("history-storyboard-entry", target.id), frame_index: 0 }],
    }, false, true);
    const selectedSeries: Series = { ...series, team_id: "history-team-entry", can_enter: true };
    const message: ChapterGenerationHistoryMessage = {
      id: "history-message-entry",
      chapter_id: target.id,
      frame_index: 0,
      asset_type: null,
      asset_id: null,
      chat_mode: "image",
      role: "assistant",
      content: candidateUrl,
      model_name: "history-test-model",
      created_at: "2026-10-28T10:00:00Z",
      message_digest: "d".repeat(64),
    };
    let targetUrl: string | null = null;
    const makeSnapshot = (context: ChapterGenerationHistoryContext): ChapterGenerationHistorySnapshot => ({
      protocol_version: 1,
      series_id: context.seriesId,
      chapter_id: context.chapterId,
      target_kind: context.targetKind,
      storyboard_asset_id: context.storyboardAssetId,
      frame_index: context.frameIndex,
      chat_mode: context.mode,
      writable: true,
      read_only_reason: null,
      target_proof: { mapping_digest: "a".repeat(64), target_digest: "b".repeat(64), media_url: targetUrl },
      messages: [message],
    });
    const readSnapshot = vi.fn(async (context: ChapterGenerationHistoryContext) => makeSnapshot(context));
    const adopt = vi.fn(async (context: ChapterGenerationHistoryContext) => {
      targetUrl = candidateUrl;
      base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === target.id
        ? { ...row, content: row.content?.map((frame, index) => index === 0 ? { ...frame, preview: candidateUrl } : frame) ?? null }
        : row));
      return {
        protocol_version: 1 as const,
        outcome: "applied" as const,
        series_id: context.seriesId,
        chapter_id: context.chapterId,
        target_kind: context.targetKind,
        storyboard_asset_id: context.storyboardAssetId!,
        frame_index: context.frameIndex,
        target_proof: { mapping_digest: "a".repeat(64), target_digest: "c".repeat(64), media_url: candidateUrl },
      };
    });
    const readMedia = vi.fn(async (_url: string, _signal: AbortSignal) => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" }));
    const historyPort: ChapterGenerationHistoryPort = {
      beginRun(context) {
        return {
          isCurrent: context.isContextCurrent,
          readSnapshot: (signal) => {
            if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
            return readSnapshot(context);
          },
          adopt: async (_input, signal) => {
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
            return await adopt(context);
          },
          retryOssUpload: async (_message, signal) => {
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
            return candidateUrl;
          },
          updateMessageContent: async (_message, _content, signal) => {
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
          },
          deleteMessage: async (_message, signal) => {
            if (signal.aborted) throw new DOMException("Aborted", "AbortError");
          },
          readMedia: (url, signal) => {
            if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
            return readMedia(url, signal);
          },
        };
      },
    };
    Object.assign(base.services, { chapterGenerationHistory: historyPort });
    base.getChapterEditLock
      .mockResolvedValueOnce({ locked: false })
      .mockResolvedValueOnce({
        locked: true,
        user_id: "demo-user",
        username: "演示创作者",
        is_mine: true,
        acquired_at: "2026-10-28T10:01:00Z",
        expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      });

    renderBrowser(base.services, vi.fn(), vi.fn(), selectedSeries);
    await user.click(await screen.findByRole("button", { name: "生成历史" }));
    expect(await screen.findByRole("heading", { name: "生成历史" })).toBeInTheDocument();
    await waitFor(() => expect(readSnapshot).toHaveBeenCalledTimes(1));
    expect(base.getChapterEditLock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "选用此图片结果" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认替换镜头预览" })).toBeEnabled());
    expect(base.getChapterEditLock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "确认替换镜头预览" }));

    await waitFor(() => expect(adopt).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(base.listChapters).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(base.listStoryboardAssets).toHaveBeenCalledTimes(2));
    expect(base.getChapterEditLock).toHaveBeenCalledTimes(2);
    expect(base.acquireChapterEditLock).toHaveBeenCalledTimes(1);
    expect(base.releaseChapterEditLock).toHaveBeenCalledTimes(1);
    expect(base.getCurrentChapters()[0]?.content?.[0]?.preview).toBe(candidateUrl);
  });
});

vi.mock("./PersonalRoughCutPanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./PersonalRoughCutPanel")>();
  return {
    ...actual,
    PersonalRoughCutPanel: (props: ComponentProps<typeof actual.PersonalRoughCutPanel>) => {
      roughCutPanelBridge.onClose = props.onClose;
      roughCutPanelBridge.onLocateFrame = props.onLocateFrame ?? null;
      roughCutPanelBridge.onFrameNavigationInvalidated = props.onFrameNavigationInvalidated ?? null;
      return createElement(actual.PersonalRoughCutPanel, {
        ...props,
        ...(props.onLocateFrame === undefined
          ? {}
          : {
              onLocateFrame: (target: PersonalRoughCutFrameTarget) => {
                roughCutPanelBridge.lastTarget = target;
                return props.onLocateFrame?.(target) ?? false;
              },
            }),
        onFrameNavigationInvalidated: (identity) => {
          roughCutPanelBridge.lastInvalidation = identity;
          props.onFrameNavigationInvalidated?.(identity);
        },
      });
    },
  };
});

vi.mock("./FrameAssetReferencesPanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./FrameAssetReferencesPanel")>();
  return {
    ...actual,
    FrameAssetReferencesPanel: (props: ComponentProps<typeof actual.FrameAssetReferencesPanel>) => {
      frameAssetReferencesBridge.onClose = props.onClose as unknown as (owner: unknown) => void;
      frameAssetReferencesBridge.owner = props.owner;
      return createElement(actual.FrameAssetReferencesPanel, props);
    },
  };
});

const series: Series = demoSeries[0]!;
const apiBaseUrl = "http://127.0.0.1:4175/api";
const restoreOverrides: Array<() => void> = [];

function chapter(
  id: string,
  title: string,
  frames: Chapter["content"],
  overrides: Partial<Chapter> = {},
): Chapter {
  return {
    id,
    series_id: series.id,
    title,
    content: frames,
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
    ...overrides,
  };
}

function seedStoryboardPageSize(userId: string, chapterId: string, pageSize = 10): void {
  const scope = { userId, apiBaseUrl, seriesId: series.id, chapterId };
  window.localStorage.setItem(storyboardPageSizeStorageKey(scope), String(pageSize));
}

function asset(id: string, chapterId: string, imageUrl: string | null = null): StoryboardAsset {
  return {
    id,
    series_id: series.id,
    chapter_id: chapterId,
    frame_index: 99,
    name: "不可直接展示的资产名",
    description: null,
    image_url: imageUrl,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
  };
}

function character(id: string, seriesId: string, name: string): Character {
  return {
    id,
    series_id: seriesId,
    name,
    gender: null,
    age: null,
    role: "守门人",
    appearance: null,
    description: "角色文字详情。",
    image_url: "https://media.example.invalid/character.png",
    audio_url: "https://media.example.invalid/voice.mp3",
    voice_ref: "private voice ref",
    aliases: ["角色别名"],
    canonical_key: "private canonical key",
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
  };
}

function makeServices(
  chapters: Chapter[],
  assetsByChapter: Record<string, StoryboardAsset[]> = {},
  enableManagement = false,
  enableEditing = false,
) {
  let currentChapters = [...chapters];
  const listChapters = vi.fn(async () => chapters);
  const listStoryboardAssets = vi.fn(async (_seriesId: string, chapterId: string) => (
    assetsByChapter[chapterId] ?? []
  ));
  const listFusedAssets = vi.fn(async () => []);
  const createChapter = vi.fn(async (seriesId: string, input: CreateChapterInput) => {
    const created = chapter("chapter-created", input.title, input.content, { series_id: seriesId, order: 3 });
    currentChapters = [...currentChapters, created];
    return created;
  });
  const updateChapter = vi.fn(async (seriesId: string, chapterId: string, update: UpdateChapterInput) => {
    const updated = currentChapters.map((row) => row.id === chapterId ? { ...row, ...update } : row);
    currentChapters = updated;
    const result = updated.find((row) => row.id === chapterId);
    if (result === undefined || result.series_id !== seriesId) {
      throw new ApiError("http", "missing", 404);
    }
    return result;
  });
  const deleteChapter = vi.fn(async (seriesId: string, chapterId: string) => {
    if (!currentChapters.some((row) => row.id === chapterId && row.series_id === seriesId)) {
      throw new ApiError("http", "missing", 404);
    }
    currentChapters = currentChapters.filter((row) => row.id !== chapterId);
  });
  const reorderChapters = vi.fn(async (seriesId: string, input: { chapters: Array<{ id: string; order: number }> }) => {
    if (seriesId !== series.id || input.chapters.length !== currentChapters.length) {
      throw new ApiError("http", "章节目录已变化", 409);
    }
    const orderById = new Map(input.chapters.map((entry) => [entry.id, entry.order]));
    currentChapters = currentChapters
      .map((row) => ({ ...row, order: orderById.get(row.id) ?? row.order }))
      .sort((left, right) => right.order - left.order);
    return { message: "排序已更新" };
  });
  const getChapterEditLock = vi.fn(async (
    _chapterId: string,
    _signal: AbortSignal,
    _lease?: ChapterEditLockLeaseHandle,
  ): Promise<ChapterEditLockState> => ({ locked: false }));
  const acquireChapterEditLock = vi.fn(async (_chapterId: string, _signal: AbortSignal): Promise<ChapterEditLockAcquisition> => ({
    state: {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    },
    lease: {} as ChapterEditLockLeaseHandle,
  }));
  const releaseChapterEditLock = vi.fn(async () => ({ message: "锁已释放" }));
  listChapters.mockImplementation(async () => currentChapters);
  const services: WorkspaceServices = {
    mode: "api",
    apiBaseUrl,
    restore: async () => null,
    login: async () => {
      throw new Error("Not used by this component test.");
    },
    listSeries: async () => [],
    listMyTeams: async () => [],
    listChapters,
    ...(enableManagement ? { createChapter, updateChapter, deleteChapter } : {}),
    ...(enableEditing ? { updateChapter, reorderChapters, getChapterEditLock, acquireChapterEditLock, releaseChapterEditLock } : {}),
    listStoryboardAssets,
    listCharacters: async () => [],
    listScenes: async () => [],
    listProps: async () => [],
    listFusedAssets,
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
  };
  return {
    services,
    listChapters,
    listStoryboardAssets,
    listFusedAssets,
    createChapter,
    updateChapter,
    deleteChapter,
    reorderChapters,
    getChapterEditLock,
    acquireChapterEditLock,
    releaseChapterEditLock,
    assetsByChapter,
    getCurrentChapters: () => currentChapters,
    setCurrentChapters: (next: Chapter[]) => { currentChapters = next; },
  };
}

function renderBrowser(
  services: WorkspaceServices,
  onBack = vi.fn(),
  onUnauthorized = vi.fn(),
  selectedSeries = series,
  userId = "demo-user",
  navigation?: {
    intent: ChapterNavigationIntent | null;
    onConsumed?: (epoch: number) => void;
    onAbandoned?: (epoch: number) => void;
  },
) {
  const view = render(
    <ChapterBrowser
      onBack={onBack}
      onUnauthorized={onUnauthorized}
      {...(navigation === undefined
        ? {}
        : {
            navigationIntent: navigation.intent,
            ...(navigation.onAbandoned === undefined ? {} : { onNavigationAbandoned: navigation.onAbandoned }),
            ...(navigation.onConsumed === undefined ? {} : { onNavigationConsumed: navigation.onConsumed }),
          })}
      series={selectedSeries}
      services={services}
      userId={userId}
    />,
  );
  return { ...view, onBack, onUnauthorized };
}

function renderContinuousBrowser(services: WorkspaceServices, userId = "demo-user") {
  return render(
    <ChapterBrowser
      onBack={vi.fn()}
      onUnauthorized={vi.fn()}
      continuousPreviewActive
      workbenchActive
      series={series}
      services={services}
      userId={userId}
    />,
  );
}

function clearOriginalTextPanelPositions(): void {
  const prefix = "haoai.frame-original-reference-panel-position.v1:";
  for (const key of Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))) {
    if (key?.startsWith(prefix)) localStorage.removeItem(key);
  }
}

function installDocumentExecCommand(
  execute: ((command: string, showUI?: boolean, value?: string) => boolean) | undefined,
): () => void {
  const descriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
  Object.defineProperty(document, "execCommand", { configurable: true, value: execute });
  const restore = () => {
    if (descriptor === undefined) Reflect.deleteProperty(document, "execCommand");
    else Object.defineProperty(document, "execCommand", descriptor);
  };
  restoreOverrides.push(restore);
  return restore;
}

function stubPointerCapture(element: HTMLElement) {
  const captured = new Set<number>();
  const setPointerCapture = vi.fn((pointerId: number) => { captured.add(pointerId); });
  const hasPointerCapture = vi.fn((pointerId: number) => captured.has(pointerId));
  const releasePointerCapture = vi.fn((pointerId: number) => { captured.delete(pointerId); });
  Object.defineProperties(element, {
    setPointerCapture: { configurable: true, value: setPointerCapture },
    hasPointerCapture: { configurable: true, value: hasPointerCapture },
    releasePointerCapture: { configurable: true, value: releasePointerCapture },
  });
  return { setPointerCapture, hasPointerCapture, releasePointerCapture };
}

async function prepareSceneAssetDialog(
  selectedSeries: Series = series,
  preparePlan = true,
  forgedExistingId: string | null = null,
) {
  const user = userEvent.setup();
  const current = chapter("chapter-asset-scene-boundary", "素材确认边界章节", [
    { text: "镜头一：阁楼里有一把旧钥匙。", scene: ["original-scene"] },
  ]);
  const base = makeServices([current], {}, false, true);
  const model: CreatorModel = {
    id: "scene-boundary-model",
    category: "text",
    name: "场景边界模型",
    provider: "openai",
    params: null,
    sort_order: 1,
    is_system: false,
    user_id: "demo-user",
  };
  const creatorSettings = {
    listModels: vi.fn(async () => [model]),
    listPrompts: vi.fn(async () => [] as CreatorPrompt[]),
  } as unknown as Pick<CreatorSettingsServicePort, "listModels" | "listPrompts">;
  const event = (type: StoryboardAssetStreamEvent["type"], data: Record<string, unknown>): StoryboardAssetStreamEvent => ({ type, data });
  const candidate = { title: "阁楼", description: "旧屋的阁楼" };
  const sceneLibrary: Scene[] = [];
  const plan = {
    ...storyboardAssetWire.save_dry_run_complete.plan,
    items: storyboardAssetWire.save_dry_run_complete.plan.items.map((item) => ({ ...item, frames: [1] })),
    frame_map: [{ frame: 1, candidates: [candidate.title] }],
  };
  const streamExtract = vi.fn(async function* () {
    yield event("start", { type: "start", message: "开始提取场景..." });
    yield event("complete", {
      ...storyboardAssetWire.producer.extract_complete.scene,
      scenes: [forgedExistingId === null
        ? candidate
        : { ...candidate, existing_id: forgedExistingId, is_existing: true, is_backfill: true }],
    });
    yield event("done", { type: "done" });
  });
  const streamJudge = vi.fn(async function* () {
    yield event("start", { type: "start", message: "开始判断出场..." });
    yield event("complete", {
      ...storyboardAssetWire.producer.judge_complete.scene,
      appearances: [candidate.title],
      scenes: [candidate],
    });
    yield event("done", { type: "done" });
  });
  const streamSave = vi.fn(async function* (_kind: StoryboardAssetKind, input: StoryboardAssetSaveInput) {
    if (input.dry_run) {
      yield event("start", { type: "start", message: "开始预览保存..." });
      yield event("plan", plan);
      yield event("complete", { ...storyboardAssetWire.save_dry_run_complete, plan });
    } else {
      const created: Scene = {
        id: "created-scene-boundary",
        series_id: current.series_id,
        title: candidate.title,
        description: candidate.description,
        image_url: null,
        aliases: [],
        canonical_key: null,
        created_at: "2026-10-24T00:00:00Z",
        updated_at: "2026-10-24T00:00:00Z",
      };
      sceneLibrary.push(created);
      const appearanceByFrame = new Map(input.target_frames.map((frame, slot) => [frame, input.appearances[slot]]));
      base.setCurrentChapters(base.getCurrentChapters().map((row) => {
        if (row.id !== current.id || row.content === null) return row;
        return {
          ...row,
          content: row.content.map((frame, index) => {
            const appearance = appearanceByFrame.get(index);
            if (appearance === undefined || appearance === null) return frame;
            return { ...frame, scene: appearance === candidate.title ? [created.id] : [] };
          }),
        };
      }));
      yield event("complete", { type: "complete", success: true, dry_run: false, message: "保存完成" });
    }
    yield event("done", { type: "done" });
  });
  const generation: StoryboardAssetGenerationServicePort = { streamExtract, streamJudge, streamSave };
  const services: WorkspaceServices = {
    ...base.services,
    creatorSettings: creatorSettings as unknown as NonNullable<WorkspaceServices["creatorSettings"]>,
    listScenes: vi.fn(async () => sceneLibrary),
    storyboardAssetGeneration: generation,
  };

  const view = renderBrowser(services, vi.fn(), vi.fn(), selectedSeries);
  await user.click(await screen.findByRole("button", { name: "提取并确认素材" }));
  const dialog = within(await screen.findByRole("dialog", { name: "提取并确认素材" }));
  await user.selectOptions(dialog.getByRole("combobox", { name: "素材类别" }), "scene");
  await user.click(dialog.getByRole("button", { name: "提取场景" }));
  await screen.findByRole("heading", { name: "编辑场景候选" });
  if (preparePlan) {
    await user.click(dialog.getByRole("button", { name: "判断出场并继续" }));
    await screen.findByRole("heading", { name: "逐镜出场与绑定" });
    await user.click(dialog.getByRole("button", { name: "生成 dry-run 保存计划" }));
    await screen.findByRole("heading", { name: /保存计划/ });
  }
  return { user, base, current, dialog, sceneLibrary, streamExtract, streamJudge, streamSave, services, view };
}

function attachFrameOptimizeServices(
  services: WorkspaceServices,
  overrides: Partial<FrameOptimizeServicePort> = {},
) {
  const capabilities: FrameOptimizeCapabilities = {
    operation: "frame.optimize",
    enabled: true,
    reason: null,
    supported_providers: ["openai"],
  };
  const model: CreatorModel = {
    id: "frame-optimize-model",
    category: "text",
    name: "测试文本模型",
    provider: "openai",
    params: null,
    sort_order: 1,
    is_system: true,
    user_id: "system",
  };
  const frameOptimize: FrameOptimizeServicePort = {
    getCapabilities: vi.fn(async () => capabilities),
    submit: vi.fn(async (input) => ({ task_id: "chapter-optimize-task", chapter_id: input.chapter_id, frame_count: input.frames.length })),
    getRunning: vi.fn(async () => null),
    getTaskStatus: vi.fn(async () => ({ task_id: "chapter-optimize-task", status: "processing", progress: 0, progress_message: "处理中", result: null })),
    cancel: vi.fn(async (taskId) => ({ task_id: taskId, status: "cancelling" as const })),
    ...overrides,
  };
  const creatorSettings = {
    listModels: vi.fn(async () => [model]),
    listPrompts: vi.fn(async (_category?: string | null) => [] as CreatorPrompt[]),
  } as unknown as Pick<CreatorSettingsServicePort, "listModels" | "listPrompts">;
  Object.assign(services, { frameOptimize, creatorSettings });
  return { frameOptimize, creatorSettings };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function installContinuousChapterOperationLock(available = true): void {
  const descriptor = Object.getOwnPropertyDescriptor(navigator, "locks");
  const value = available
    ? { request: async (_name: string, _options: { mode: "exclusive"; ifAvailable: true }, callback: (lock: Lock | null) => unknown) => callback({} as Lock) }
    : undefined;
  Object.defineProperty(navigator, "locks", { configurable: true, value });
  restoreOverrides.push(() => {
    if (descriptor === undefined) Reflect.deleteProperty(navigator, "locks");
    else Object.defineProperty(navigator, "locks", descriptor);
  });
}

function installContinuousOperationCrypto(): void {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", { configurable: true, value: webcrypto });
  restoreOverrides.push(() => {
    if (descriptor === undefined) Reflect.deleteProperty(globalThis, "crypto");
    else Object.defineProperty(globalThis, "crypto", descriptor);
  });
}

async function makeContinuousOperationResponse(
  actorId: string,
  seriesId: string,
  chapterId: string,
  operationId: string,
  bodyJson: string,
  result: Record<string, unknown>,
): Promise<ContinuousChapterOperationResponse> {
  const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
  const command = request.command;
  const frozen = await freezeContinuousOperationRequest(request);
  const sourceDigest = await continuousSourceSnapshotBinding(frozen.bodySha256);
  const targetDigest = await continuousTargetManifestDigest(command.draft_frame_ids);
  const resultIdentity: Record<string, unknown> = {
    kind: command.kind,
    series_id: seriesId,
    chapter_id: chapterId,
    draft_frame_ids: command.draft_frame_ids,
    manifest_digest: targetDigest,
    source_snapshot_digest: sourceDigest,
  };
  let targetDraftId: string | null = null;
  if (command.kind === "save-chapter") {
    resultIdentity.frame_storyboard_ids = command.content.map((frame) => (
      Array.isArray(frame.storyboard) ? frame.storyboard : []
    ));
  } else {
    targetDraftId = command.target_draft_id;
    resultIdentity.asset_type = command.kind.slice("create-".length);
    resultIdentity.target_draft_id = targetDraftId;
    resultIdentity.asset_id = result.id;
  }
  const canonicalResponseJson = JSON.stringify(result);
  const receipt: ContinuousChapterOperationReceipt = {
    receipt_version: 1,
    operation_id: operationId,
    owner_id: actorId,
    editor_session_id: request.editor_session_id,
    series_id: seriesId,
    chapter_id: chapterId,
    command_kind: command.kind as ContinuousChapterOperationKind,
    request_body_sha256: frozen.bodySha256,
    source_snapshot_digest: sourceDigest,
    target_draft_id: targetDraftId,
    target_manifest_digest: targetDigest,
    outcome: "committed",
    http_status: 200,
    result_identity: resultIdentity,
    result,
    canonical_response_json: canonicalResponseJson,
    result_digest: await continuousOperationResultDigest(canonicalResponseJson),
    committed_at: new Date().toISOString(),
  };
  return { receipt, result };
}

function attachContinuousChapterOperationPort(
  services: WorkspaceServices,
  submitOperation: (chapterId: string, operationId: string, bodyJson: string, signal: AbortSignal) => Promise<ContinuousChapterOperationResponse>,
) {
  const receipts = new Map<string, ContinuousChapterOperationReceipt>();
  services.getContinuousChapterEditCapabilities = vi.fn(async () => ({ chapterEditGuard: 1 as const, operationReceipt: 1 as const }));
  const submit = vi.fn(async (chapterId: string, operationId: string, bodyJson: string, signal: AbortSignal) => {
    const response = await submitOperation(chapterId, operationId, bodyJson, signal);
    receipts.set(operationId, response.receipt);
    return response;
  });
  const lookup = vi.fn(async (_chapterId: string, operationId: string, _signal: AbortSignal) => {
    const receipt = receipts.get(operationId);
    if (receipt === undefined) throw new ApiError("http", "原操作不存在", 404);
    return { receipt };
  });
  Object.assign(services, { continuousChapterOperations: { submit, lookup } });
  return { submit, lookup };
}

function clearContinuousChapterOperationJournals(): void {
  const prefix = "haoai:continuous-chapter-pending:v1:";
  for (const key of Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))) {
    if (key?.startsWith(prefix)) localStorage.removeItem(key);
  }
}

function captureButtonHandler(button: HTMLElement): () => void {
  const propsKey = Object.getOwnPropertyNames(button).find((key) => key.startsWith("__reactProps$"));
  if (propsKey === undefined) {
    throw new Error("The rendered button should expose its React click handler for stale-callback testing.");
  }
  const props = (button as unknown as Record<string, unknown>)[propsKey];
  if (typeof props !== "object" || props === null || !("onClick" in props) || typeof props.onClick !== "function") {
    throw new Error("The rendered button does not have a click handler.");
  }

  const onClick = props.onClick as (event: unknown) => void;
  // Invoke the original React handler directly to bypass the browser's disabled-button guard.
  return () => onClick({});
}

function LayoutCommitObserver({ onCommit }: { onCommit(): void }) {
  useLayoutEffect(() => {
    onCommit();
  });
  return null;
}

function emptyPersonalNotesSnapshot(chapterId: string, note: string): PersonalProductionSnapshot {
  return parsePersonalProductionSnapshot({
    chapter_id: chapterId,
    revision: 1,
    media_state: "empty",
    frames: [],
    frame_notes: { orphan: { status: "needs_revision", note } },
    resume_frame_id: null,
  }, chapterId);
}

function stableStoryboardId(frame: NonNullable<Chapter["content"]>[number]): string | null {
  const references = frame.storyboard;
  return Array.isArray(references) && typeof references[0] === "string" ? references[0] : null;
}

function frameStructureNotes(chapterValue: Chapter, resumeFrameId: string | null): PersonalProductionSnapshot {
  const frames = chapterValue.content ?? [];
  return parsePersonalProductionSnapshot({
    chapter_id: chapterValue.id,
    revision: 4,
    media_state: frames.length === 0 ? "empty" : "ready",
    frames: frames.map((frame, index) => ({
      frame_index: index,
      storyboard_asset_id: stableStoryboardId(frame),
      media_revision: 1,
      source_valid: true,
      asset_image_digest: null,
      preview_digest: null,
      invalid_reason: null,
    })),
    frame_notes: resumeFrameId === null
      ? { "legacy-frame-note": { status: "needs_revision", note: "保留历史记录" } }
      : { [resumeFrameId]: { status: "unmarked", note: "删除后保留的历史备注" } },
    resume_frame_id: resumeFrameId,
  }, chapterValue.id);
}

function frameStructureRoughCut(chapterValue: Chapter, removedAssetIds: string[] = []): PersonalRoughCutSnapshot {
  const frames = chapterValue.content ?? [];
  return {
    chapter_id: chapterValue.id,
    revision: 3,
    saved: true,
    frames: frames.map((frame, index) => ({
      asset_id: stableStoryboardId(frame) ?? "",
      frame_index: index,
      text: typeof frame.text === "string" ? frame.text : "",
      preview_url: null,
      missing_reason: null,
      included: index === 0,
      pending: false,
    })),
    removed_asset_ids: [...removedAssetIds],
  };
}

function makeFrameStructureServices(initialChapter: Chapter, resumeFrameId: string | null = null) {
  const initialAssets = (initialChapter.content ?? []).flatMap((frame, frameIndex) => {
    const id = stableStoryboardId(frame);
    return id === null ? [] : [{ ...asset(id, initialChapter.id), frame_index: frameIndex }];
  });
  const base = makeServices([initialChapter], { [initialChapter.id]: initialAssets }, false, true);
  const { services, assetsByChapter } = base;
  let nextAssetId = 1;
  let removedAssetIds: string[] = [];

  base.updateChapter.mockImplementation(async (seriesId, chapterId, update) => {
    const current = base.getCurrentChapters().find((row) => row.id === chapterId);
    if (current === undefined || current.series_id !== seriesId || update.content === undefined) {
      throw new ApiError("http", "missing", 404);
    }
    const content = update.content.map((frame) => {
      const references = frame.storyboard;
      if (Array.isArray(references) && references.length === 0) {
        return { ...frame, storyboard: [`${chapterId}-inserted-${nextAssetId++}`] };
      }
      return frame;
    });
    const updated = { ...current, content };
    base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? updated : row));
    assetsByChapter[chapterId] = content.flatMap((frame, frameIndex) => {
      const id = stableStoryboardId(frame);
      return id === null ? [] : [{ ...asset(id, chapterId), frame_index: frameIndex }];
    });
    return updated;
  });

  const deleteFrame = vi.fn(async (seriesId: string, chapterId: string, frameIndex: number) => {
    const current = base.getCurrentChapters().find((row) => row.id === chapterId);
    if (current === undefined || current.series_id !== seriesId || current.content === null
      || current.content.length <= 1 || frameIndex < 0 || frameIndex >= current.content.length) {
      throw new ApiError("http", "invalid delete", 422);
    }
    const removedId = stableStoryboardId(current.content[frameIndex]!);
    if (removedId === null) throw new ApiError("http", "missing stable identity", 422);
    removedAssetIds = [...removedAssetIds, removedId];
    const content = current.content.filter((_frame, index) => index !== frameIndex);
    const updated = { ...current, content };
    base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? updated : row));
    assetsByChapter[chapterId] = content.flatMap((frame, index) => {
      const id = stableStoryboardId(frame);
      return id === null ? [] : [{ ...asset(id, chapterId), frame_index: index }];
    });
    return updated;
  });
  services.deleteFrame = deleteFrame;
  vi.spyOn(services, "getPersonalProductionNotes").mockImplementation(async (chapterId) => {
    const current = base.getCurrentChapters().find((row) => row.id === chapterId);
    if (current === undefined) throw new ApiError("http", "missing", 404);
    return frameStructureNotes(current, resumeFrameId);
  });
  vi.spyOn(services, "getPersonalRoughCut").mockImplementation(async (chapterId) => {
    const current = base.getCurrentChapters().find((row) => row.id === chapterId);
    if (current === undefined) throw new ApiError("http", "missing", 404);
    return frameStructureRoughCut(current, removedAssetIds);
  });
  return { ...base, deleteFrame, assetsByChapter };
}

describe("read-only chapter browser", () => {
  afterEach(() => {
    for (const restore of restoreOverrides.splice(0).reverse()) restore();
    cleanup();
    clearContinuousChapterOperationJournals();
    clearOriginalTextPanelPositions();
    personalNotesBridge.onLocateResume = null;
    personalNotesBridge.onResumeInvalidated = null;
    personalNotesBridge.lastTarget = null;
    personalNotesBridge.onRegisterFrameRead = null;
    personalNotesBridge.onFrameReadInvalidated = null;
    personalNotesBridge.onLocateProductionNoteFrame = null;
    personalNotesBridge.onClose = null;
    personalNotesBridge.lastProductionNoteTarget = null;
    personalNotesBridge.assets = [];
    frameAssetReferencesBridge.onClose = null;
    frameAssetReferencesBridge.owner = null;
    roughCutPanelBridge.onClose = null;
    roughCutPanelBridge.onLocateFrame = null;
    roughCutPanelBridge.lastTarget = null;
    roughCutPanelBridge.onFrameNavigationInvalidated = null;
    roughCutPanelBridge.lastInvalidation = null;
  });

  it("keeps local demo chapter editing available without a remote operation journal", async () => {
    const user = userEvent.setup();
    const demoChapterData = getDemoChapterData(series.id);
    expect(demoChapterData).not.toBeNull();
    const services = createDemoServices(window.localStorage);
    expect(services.mode).toBe("demo");
    expect(services.apiBaseUrl).toBeNull();
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);

    renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "第一章 · 雾起 · 镜头 1" });
    expect(screen.queryByRole("region", { name: "连续编辑操作恢复" })).not.toBeInTheDocument();
    const editButton = within(frame).getByRole("button", { name: "编辑文字" });
    expect(editButton).toBeEnabled();
    await user.click(editButton);
    expect(await screen.findByRole("dialog", { name: "编辑分镜文字" })).toBeInTheDocument();
  });

  it("keeps an unresolved API journal blocking edits when the service has no receipt port", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const current = chapter("chapter-api-journal-without-receipts", "旧 API 回执能力", [{
      text: "原始正文",
      storyboard: ["api-journal-without-receipts-frame"],
    }]);
    const storyboard = { ...asset("api-journal-without-receipts-frame", current.id), frame_index: 0 };
    const base = makeServices([current], { [current.id]: [storyboard] }, false, true);
    expect(base.services.mode).toBe("api");
    expect(base.services.continuousChapterOperations).toBeUndefined();

    const snapshot = createContinuousChapterDraft(current, [storyboard], null);
    const update = buildGuardedChapterUpdate(snapshot, snapshot.frames);
    if (update.content === undefined || update.chapter_edit_guard === undefined) {
      throw new Error("The API journal fixture requires a guarded chapter update.");
    }
    const editorSessionId = crypto.randomUUID();
    const operationId = crypto.randomUUID();
    const draftFrameIds = snapshot.frames.map((frame) => frame.draftId);
    const request: ContinuousChapterOperationRequest = {
      protocol_version: 1,
      editor_session_id: editorSessionId,
      command: {
        kind: "save-chapter",
        content: update.content,
        chapter_edit_guard: { ...update.chapter_edit_guard },
        draft_frame_ids: draftFrameIds,
      },
    };
    const frozen = await freezeContinuousOperationRequest(request);
    const journalScope = { actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: current.id };
    const [sourceSnapshotDigest, targetManifestDigest] = await Promise.all([
      continuousSourceSnapshotBinding(frozen.bodySha256),
      continuousTargetManifestDigest(draftFrameIds),
    ]);
    const key = continuousChapterOperationJournalKey(journalScope);
    const journal = createContinuousOperationJournalEnvelope({
      scope: journalScope,
      editorSessionId,
      documentId: crypto.randomUUID(),
      authSessionGeneration: 1,
      snapshot,
      frames: snapshot.frames,
      selectedDraftId: snapshot.frames[0]?.draftId ?? null,
      operationId,
      kind: "save-chapter",
      targetDraftId: null,
      draftFrameIds,
      bodyJson: frozen.bodyJson,
      bodySha256: frozen.bodySha256,
      sourceSnapshotDigest,
      targetManifestDigest,
      phase: "dispatch_possible",
      route: continuousChapterOperationRoute(current.id, operationId),
    });
    await withContinuousChapterOperationLock(key, (lease) => (
      writeContinuousChapterJournal(window.localStorage, key, journal, lease, null)
    ));

    renderContinuousBrowser(base.services);

    expect(await screen.findByRole("region", { name: "连续编辑操作恢复" })).toBeInTheDocument();
    const frame = await screen.findByRole("article", { name: "旧 API 回执能力 · 镜头 1" });
    expect(within(frame).getByRole("button", { name: "编辑文字" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "开启整章连续编辑" })).toBeDisabled();
  });

  it("opens the chapter asset extraction and confirmation flow from the chapter page", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-asset-extraction-entry", "素材提取章节", [
      { text: "镜头一：周晴走进旧屋。", storyboard: ["asset-extraction-entry-frame"] },
    ]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("asset-extraction-entry-frame", current.id), frame_index: 0 }],
    }, false, true);
    const model: CreatorModel = {
      id: "asset-extraction-model",
      category: "text",
      name: "素材提取模型",
      provider: "openai",
      params: null,
      sort_order: 1,
      is_system: false,
      user_id: "demo-user",
    };
    const creatorSettings = {
      listModels: vi.fn(async () => [model]),
      listPrompts: vi.fn(async () => [] as CreatorPrompt[]),
    } as unknown as Pick<CreatorSettingsServicePort, "listModels" | "listPrompts">;
    const generation: StoryboardAssetGenerationServicePort = {
      streamExtract: vi.fn(async function* () {}),
      streamJudge: vi.fn(async function* () {}),
      streamSave: vi.fn(async function* () {}),
    };
    Object.assign(base.services, {
      creatorSettings,
      storyboardAssetGeneration: generation,
    });

    renderBrowser(base.services);

    await user.click(await screen.findByRole("button", { name: "提取并确认素材" }));
    const dialog = within(await screen.findByRole("dialog", { name: "提取并确认素材" }));
    expect(dialog.getByRole("combobox", { name: "模型" })).toHaveValue(model.id);
    expect(dialog.getByRole("checkbox", { name: "选择镜头 1" })).toBeChecked();
    expect(dialog.getByText("镜头一：周晴走进旧屋。")).toBeInTheDocument();
    expect(base.updateChapter).not.toHaveBeenCalled();
  });

  it("saves a confirmed scene plan, preserves null bindings, clears an explicit empty choice, and reads it back", async () => {
    const user = userEvent.setup();
    const original = chapter("chapter-asset-scene-save", "场景素材章节", [
      { text: "第一镜：夜色书房。", scene: ["existing-scene"] },
      { text: "第二镜：只保留旧场景。", scene: ["preserve-selected-scene"] },
      { text: "第三镜：明确没有场景。", scene: ["clear-this-scene"] },
      { text: "第四镜不参与本次操作。", scene: ["keep-unselected-scene"] },
    ]);
    const base = makeServices([original], {}, true, true);
    const existingScene: Scene = {
      id: "existing-scene",
      series_id: series.id,
      title: "夜色书房",
      description: "当前剧集场景",
      image_url: null,
      aliases: [],
      canonical_key: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    };
    const model: CreatorModel = {
      id: "scene-extraction-model",
      category: "text",
      name: "场景提取模型",
      provider: "openai",
      params: null,
      sort_order: 1,
      is_system: false,
      user_id: "demo-user",
    };
    const creatorSettings = {
      listModels: vi.fn(async () => [model]),
      listPrompts: vi.fn(async () => [] as CreatorPrompt[]),
    } as unknown as Pick<CreatorSettingsServicePort, "listModels" | "listPrompts">;
    const wireEvent = (type: StoryboardAssetStreamEvent["type"], data: Record<string, unknown>): StoryboardAssetStreamEvent => ({ type, data });
    const name = "夜色书房";
    const streamExtract = vi.fn(async function* (_kind: StoryboardAssetKind) {
      yield wireEvent("start", { type: "start", message: "开始提取素材..." });
      yield wireEvent("complete", storyboardAssetWire.producer.extract_complete.scene);
      yield wireEvent("done", { type: "done" });
    });
    const streamJudge = vi.fn(async function* (_kind: StoryboardAssetKind) {
      yield wireEvent("start", { type: "start", message: "开始判断出场..." });
      yield wireEvent("complete", {
        ...storyboardAssetWire.producer.judge_complete.scene,
        appearances: [[name], null, [name]],
      });
      yield wireEvent("done", { type: "done" });
    });
    const streamSave = vi.fn(async function* (_kind: StoryboardAssetKind, input: StoryboardAssetSaveInput) {
      const plan = {
        asset_type: "scene",
        items: [{
          name,
          status: "reuse",
          reason: "",
          frames: [1],
          existing_id: existingScene.id,
          existing_name: name,
          is_existing: true,
          is_backfill: true,
          description: existingScene.description ?? "",
          aliases: [],
        }],
        frame_map: input.target_frames.map((frame, slot) => ({
          frame: frame + 1,
          candidates: input.appearances[slot] === name ? [name] : [],
        })),
      };
      if (input.dry_run) {
        yield wireEvent("start", { type: "start", message: "开始预览保存..." });
        yield wireEvent("plan", plan);
        yield wireEvent("complete", { type: "complete", success: true, dry_run: true, plan, message: "预览完成" });
        yield wireEvent("done", { type: "done" });
        return;
      }
      const chapters = base.getCurrentChapters().map((row) => {
        if (row.id !== original.id || row.content === null) return row;
        const appearances = new Map(input.target_frames.map((frame, slot) => [frame, input.appearances[slot]]));
        return {
          ...row,
          content: row.content.map((frame, index) => {
            const appearance = appearances.get(index);
            if (appearance === undefined || appearance === null) return frame;
            const names = typeof appearance === "string" ? (appearance === "" ? [] : [appearance]) : [];
            return { ...frame, scene: names.map(() => existingScene.id) };
          }),
        };
      });
      base.setCurrentChapters(chapters);
      yield wireEvent("complete", { type: "complete", success: true, message: "保存完成" });
      yield wireEvent("done", { type: "done" });
    });
    const generation: StoryboardAssetGenerationServicePort = { streamExtract, streamJudge, streamSave };
    const services: WorkspaceServices = {
      ...base.services,
      creatorSettings: creatorSettings as unknown as NonNullable<WorkspaceServices["creatorSettings"]>,
      listScenes: vi.fn(async () => [existingScene]),
      storyboardAssetGeneration: generation,
    };

    renderBrowser(services);

    await user.click(await screen.findByRole("button", { name: "提取并确认素材" }));
    const dialog = within(await screen.findByRole("dialog", { name: "提取并确认素材" }));
    await user.selectOptions(dialog.getByRole("combobox", { name: "素材类别" }), "scene");
    await user.click(dialog.getByRole("checkbox", { name: "选择镜头 4" }));
    await user.click(dialog.getByRole("button", { name: "提取场景" }));
    await screen.findByRole("heading", { name: "编辑场景候选" });
    await user.selectOptions(dialog.getByRole("combobox", { name: "复用场景" }), existingScene.id);
    await user.click(dialog.getByRole("button", { name: "判断出场并继续" }));
    await screen.findByRole("heading", { name: "逐镜出场与绑定" });
    expect(dialog.getByRole("combobox", { name: "镜头 2 场景绑定" })).toHaveValue("__preserve_original_binding__");
    await user.selectOptions(dialog.getByRole("combobox", { name: "镜头 3 场景绑定" }), "");
    await user.click(dialog.getByRole("button", { name: "生成 dry-run 保存计划" }));
    await screen.findByRole("heading", { name: /保存计划/ });
    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "提取并确认素材" })).not.toBeInTheDocument());
    expect(await screen.findByText("素材、章节与逐镜绑定已重新读取并核对。" )).toBeInTheDocument();
    expect(streamSave).toHaveBeenCalledTimes(2);
    expect(streamSave.mock.calls[0]?.[1]).toMatchObject({
      expected_series_id: original.series_id,
      expected_content: original.content,
      target_frames: [0, 1, 2],
      appearances: [name, null, ""],
      dry_run: true,
    });
    expect(streamSave.mock.calls[1]?.[1]).toMatchObject({
      expected_series_id: original.series_id,
      expected_content: original.content,
      target_frames: [0, 1, 2],
      appearances: [name, null, ""],
      dry_run: false,
    });
    const saved = base.getCurrentChapters()[0]?.content;
    expect(saved?.map((frame) => frame.scene)).toEqual([
      [existingScene.id],
      ["preserve-selected-scene"],
      [],
      ["keep-unselected-scene"],
    ]);
    expect(base.updateChapter).not.toHaveBeenCalled();
    expect(base.listChapters.mock.calls.length).toBeGreaterThan(2);
  });

  it("stops before the final save when the chapter source changes after dry-run", async () => {
    const { user, base, current, dialog, streamSave } = await prepareSceneAssetDialog();
    base.setCurrentChapters([{
      ...current,
      content: [{ text: "另一位编辑刚刚修改了这一镜。", scene: ["original-scene"] }],
    }]);

    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));

    expect(await screen.findByText(/章节文字、镜头顺序、原图或当前剧集素材目录已变化/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "提取并确认素材" })).not.toBeInTheDocument();
    expect(streamSave).toHaveBeenCalledTimes(1);
    expect(streamSave.mock.calls[0]?.[1]).toMatchObject({
      dry_run: true,
      expected_content: current.content,
      expected_series_id: current.series_id,
    });
    expect(base.updateChapter).not.toHaveBeenCalled();
  });

  it("ignores an AI-provided foreign scene ID and saves the candidate as newly created", async () => {
    const forgedId = "scene-from-another-series";
    const { user, base, current, dialog, sceneLibrary, streamSave } = await prepareSceneAssetDialog(series, true, forgedId);

    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "提取并确认素材" })).not.toBeInTheDocument());
    expect(await screen.findByText("素材、章节与逐镜绑定已重新读取并核对。")).toBeInTheDocument();
    expect(streamSave).toHaveBeenCalledTimes(2);
    const finalInput = streamSave.mock.calls[1]?.[1];
    expect(finalInput).toMatchObject({
      dry_run: false,
      expected_content: current.content,
      expected_series_id: current.series_id,
      candidates: [{ title: "阁楼", is_existing: false, is_backfill: false }],
    });
    expect([null, undefined]).toContain(finalInput?.candidates[0]?.existing_id);
    expect(JSON.stringify(finalInput)).not.toContain(forgedId);
    expect(sceneLibrary).toMatchObject([{ id: "created-scene-boundary", series_id: current.series_id, title: "阁楼" }]);
    expect(base.getCurrentChapters()[0]?.content?.[0]?.scene).toEqual(["created-scene-boundary"]);
    expect(base.updateChapter).not.toHaveBeenCalled();
  });

  it("does not send the final save after the acquired team lock identity is lost", async () => {
    const teamSeries = { ...series, team_id: "team-asset-lock-boundary" };
    const { user, base, dialog, streamSave } = await prepareSceneAssetDialog(teamSeries);
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    base.getChapterEditLock
      .mockResolvedValueOnce({ locked: false })
      .mockResolvedValueOnce(ownedLock)
      .mockResolvedValueOnce({ locked: false });

    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));

    expect(await dialog.findByText("最终保存前无法再次确认当前账号仍持有同一有效编辑锁。")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "提取并确认素材" })).toBeInTheDocument();
    expect(base.getChapterEditLock).toHaveBeenCalledTimes(3);
    expect(base.releaseChapterEditLock).toHaveBeenCalledTimes(1);
    expect(streamSave).toHaveBeenCalledTimes(1);
    expect(streamSave.mock.calls[0]?.[1]).toMatchObject({ dry_run: true });
    expect(base.updateChapter).not.toHaveBeenCalled();
  });

  it("releases a team lock acquired after the asset dialog scope has changed", async () => {
    const teamSeries = { ...series, team_id: "team-asset-late-acquire" };
    const { user, base, current, dialog, streamSave, services, view } = await prepareSceneAssetDialog(teamSeries);
    const pendingAcquisition = deferred<ChapterEditLockAcquisition>();
    const lease = {} as ChapterEditLockLeaseHandle;
    const acquiredState: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    base.acquireChapterEditLock.mockImplementation(() => pendingAcquisition.promise);

    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));
    await waitFor(() => expect(base.acquireChapterEditLock).toHaveBeenCalledTimes(1));

    view.rerender(
      <ChapterBrowser
        onBack={view.onBack}
        onUnauthorized={view.onUnauthorized}
        series={{ ...teamSeries, id: "team-asset-late-acquire-new-scope" }}
        services={services}
        userId="new-user-scope"
      />,
    );
    await act(async () => {
      pendingAcquisition.resolve({ state: acquiredState, lease });
      await pendingAcquisition.promise;
    });

    await waitFor(() => expect(base.releaseChapterEditLock).toHaveBeenCalledTimes(1));
    expect(base.releaseChapterEditLock).toHaveBeenCalledWith(current.id, expect.any(AbortSignal), lease);
    expect(streamSave).toHaveBeenCalledTimes(1);
    expect(streamSave.mock.calls[0]?.[1]).toMatchObject({ dry_run: true });
  });

  it("does not start the final save when scope changes during the last lock read", async () => {
    const teamSeries = { ...series, team_id: "team-asset-final-lock-read" };
    const { user, base, dialog, streamSave, services, view } = await prepareSceneAssetDialog(teamSeries);
    const pendingFinalCheck = deferred<ChapterEditLockState>();
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    base.getChapterEditLock
      .mockResolvedValueOnce({ locked: false })
      .mockResolvedValueOnce(ownedLock)
      .mockImplementationOnce(() => pendingFinalCheck.promise);

    await user.click(dialog.getByRole("checkbox", { name: /我已核对候选/ }));
    await user.click(dialog.getByRole("button", { name: "明确确认并保存" }));
    await waitFor(() => expect(base.getChapterEditLock).toHaveBeenCalledTimes(3));

    view.rerender(
      <ChapterBrowser
        onBack={view.onBack}
        onUnauthorized={view.onUnauthorized}
        series={{ ...teamSeries, id: "team-asset-final-lock-read-new-scope" }}
        services={services}
        userId="new-user-scope"
      />,
    );
    await act(async () => {
      pendingFinalCheck.resolve(ownedLock);
      await pendingFinalCheck.promise;
    });

    expect(base.getChapterEditLock).toHaveBeenCalledTimes(3);
    expect(base.releaseChapterEditLock).toHaveBeenCalledTimes(1);
    expect(streamSave).toHaveBeenCalledTimes(1);
    expect(streamSave.mock.calls[0]?.[1]).toMatchObject({ dry_run: true });
  });

  it("stops after a user closes the extracted candidate dialog without judging or saving", async () => {
    const { user, dialog, streamJudge, streamSave } = await prepareSceneAssetDialog(series, false);
    expect(await screen.findByRole("heading", { name: "编辑场景候选" })).toBeInTheDocument();

    await user.click(dialog.getByRole("button", { name: "关闭素材提取" }));

    expect(screen.queryByRole("dialog", { name: "提取并确认素材" })).not.toBeInTheDocument();
    expect(streamJudge).not.toHaveBeenCalled();
    expect(streamSave).not.toHaveBeenCalled();
  });

  it("confirms repeated frame reviews using each admission source while preserving historic original text", async () => {
    const user = userEvent.setup();
    const original = chapter("chapter-frame-review", "复核章节", [
      { text: "原始镜头文字", original_text: "更早的历史原文", storyboard: ["review-storyboard-1"] },
    ]);
    const base = makeServices([original], {
      [original.id]: [{ ...asset("review-storyboard-1", original.id), frame_index: 0 }],
    }, false, true);
    const reviewModel: CreatorModel = {
      id: "review-model",
      category: "text",
      name: "复核模型",
      provider: "openai",
      params: null,
      sort_order: 1,
      is_system: false,
      user_id: "demo-user",
    };
    const reviewPrompt: CreatorPrompt = {
      id: "review-prompt",
      category: "ai-review",
      name: "复核提示词",
      is_system: false,
      sort_order: 1,
      user_id: "demo-user",
    };
    const enabledCapabilities: FrameReviewCapabilities = {
      operation: "frame.review",
      enabled: true,
      reason: null,
      supported_providers: ["openai", "deepseek", "custom"],
    };
    const tasks = new Map<string, FrameReviewTask>();
    const frameReview: FrameReviewServicePort = {
      getCapabilities: vi.fn(async () => ({
        ...enabledCapabilities,
      })),
      submit: vi.fn(async () => ({
        task_id: "pending-frame-review-task",
        model_name: "复核模型",
        is_system: false,
        credit_cost: 3,
      })),
      getTask: vi.fn(async (taskId: string) => tasks.get(taskId)!),
      cancel: vi.fn(async (taskId: string) => ({ task_id: taskId, status: "cancelling" as const })),
    };
    const creatorSettings = {
      listModels: vi.fn(async () => [reviewModel]),
      listPrompts: vi.fn(async () => [reviewPrompt]),
    };
    const services: WorkspaceServices = {
      ...base.services,
      creatorSettings: creatorSettings as unknown as NonNullable<WorkspaceServices["creatorSettings"]>,
      frameReview,
    };
    base.setCurrentChapters([original]);
    let reviewNumber = 0;
    frameReview.submit = vi.fn(async () => {
      reviewNumber += 1;
      const current = base.getCurrentChapters()[0]!;
      const sourceText = current.content![0]!.text as string;
      const optimizedText = reviewNumber === 1 ? "第一次复核后的文字" : "第二次复核后的文字";
      const taskId = `frame-review-task-${reviewNumber}`;
      const result = {
        chapter_id: original.id,
        frame_index: 0,
        prompt_id: reviewPrompt.id,
        optimized_text: optimizedText,
        original_text: sourceText,
      };
      tasks.set(taskId, {
        id: taskId,
        type: "ai-review",
        status: "completed",
        progress: 100,
        progress_message: null,
        result: JSON.stringify(result),
        credit_cost: 3,
        model_name: "复核模型",
      });
      base.setCurrentChapters([{
        ...current,
        updated_at: `2026-10-20T00:3${reviewNumber}:00`,
        content: [{ ...current.content![0]!, text: optimizedText }],
      }]);
      return { task_id: taskId, model_name: "复核模型", is_system: false, credit_cost: 3 };
    });

    renderBrowser(services);

    async function reviewCurrentFrame(expectedSource: string, expectedResult: string): Promise<void> {
      const frame = await screen.findByRole("article", { name: "复核章节 · 镜头 1" });
      const reviewButton = await within(frame).findByRole("button", { name: "AI 复核" });
      expect(reviewButton).toBeEnabled();
      await user.click(reviewButton);
      const dialog = within(await screen.findByRole("dialog", { name: "AI 分镜复核" }));
      expect(dialog.getByText(expectedSource)).toBeInTheDocument();
      await user.type(dialog.getByRole("textbox", { name: "希望核对的问题" }), "检查镜头连续性");
      await user.click(dialog.getByRole("button", { name: "提交复核" }));

      expect(await screen.findByText("服务器已回写镜头；章节源数据已重新读取并核对。")).toBeInTheDocument();
      expect(await screen.findByRole("heading", { name: "服务器回写后的镜头文字" })).toBeInTheDocument();
      expect(dialog.getByText(expectedResult)).toBeInTheDocument();
      await waitFor(() => expect(base.listChapters).toHaveBeenCalledTimes(reviewNumber + 1));
      expect(frameReview.submit).toHaveBeenCalledTimes(reviewNumber);
      await user.click(dialog.getByRole("button", { name: "关闭" }));
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "AI 分镜复核" })).not.toBeInTheDocument());
    }

    await reviewCurrentFrame("原始镜头文字", "第一次复核后的文字");
    await reviewCurrentFrame("第一次复核后的文字", "第二次复核后的文字");
    expect(base.getCurrentChapters()[0]!.content![0]!.original_text).toBe("更早的历史原文");
    expect(base.listStoryboardAssets).toHaveBeenCalledTimes(3);
    expect(frameReview.getTask).toHaveBeenNthCalledWith(1, "frame-review-task-1", expect.any(AbortSignal));
    expect(frameReview.getTask).toHaveBeenNthCalledWith(2, "frame-review-task-2", expect.any(AbortSignal));
    expect(base.updateChapter).not.toHaveBeenCalled();
  });

  it("keeps a text-editor draft open and disables frame review until the editor closes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-review-draft", "草稿章节", [
      { text: "当前镜头文字", storyboard: ["review-draft-storyboard"] },
    ]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("review-draft-storyboard", current.id), frame_index: 0 }],
    }, false, true);
    const enabledCapabilities: FrameReviewCapabilities = {
      operation: "frame.review",
      enabled: true,
      reason: null,
      supported_providers: ["openai", "deepseek", "custom"],
    };
    const services: WorkspaceServices = {
      ...base.services,
      frameReview: {
        getCapabilities: vi.fn(async () => ({
          ...enabledCapabilities,
        })),
        submit: vi.fn(async () => ({ task_id: "task", model_name: "复核模型", is_system: false, credit_cost: 1 })),
        getTask: vi.fn(async () => ({
          id: "task", type: "ai-review", status: "queued", progress: 0,
          progress_message: null, result: null, credit_cost: 1, model_name: "复核模型",
        })),
        cancel: vi.fn(async () => ({ task_id: "task", status: "cancelling" as const })),
      },
      creatorSettings: {
        listModels: vi.fn(async () => []),
        listPrompts: vi.fn(async () => []),
      } as unknown as NonNullable<WorkspaceServices["creatorSettings"]>,
    };

    renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "草稿章节 · 镜头 1" });
    await within(frame).findByRole("button", { name: "AI 复核" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const draft = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(draft);
    await user.type(draft, "仍在编辑中的草稿");

    expect(within(frame).getByRole("button", { name: "AI 复核" })).toBeDisabled();
    expect(draft).toHaveValue("仍在编辑中的草稿");
    expect(screen.queryByRole("dialog", { name: "AI 分镜复核" })).not.toBeInTheDocument();
  });

  it("selects the exact non-first target by raw chapter ID and loads only its assets", async () => {
    const first = chapter("chapter-first", "同名章节", [{ text: "首章文字" }]);
    const target = chapter("chapter-target", "同名章节", [{ text: "目标章节文字" }]);
    const { services, listChapters, listStoryboardAssets } = makeServices([first, target]);
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 81,
    };
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();

    renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", {
      intent,
      onConsumed,
      onAbandoned,
    });

    expect(await screen.findByText("目标章节文字")).toBeInTheDocument();
    expect(screen.queryByText("首章文字")).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    expect(listStoryboardAssets).toHaveBeenCalledWith(series.id, target.id, expect.any(AbortSignal));
    expect(onConsumed).toHaveBeenCalledTimes(1);
    expect(onConsumed).toHaveBeenCalledWith(intent.navigationEpoch);
    expect(onAbandoned).not.toHaveBeenCalled();
  });

  it("locates the exact storyboard[0] frame after fresh chapter and asset reads", async () => {
    const first = chapter("chapter-first", "同名章节", [
      { storyboard: ["storyboard-first"], character: ["character-first"], text: "首章文字" },
    ]);
    const targetPosition = 22;
    const target = chapter("chapter-target", "同名章节", [
      ...Array.from({ length: targetPosition - 1 }, (_, index) => ({
        storyboard: [`storyboard-other-${index + 1}`],
        character: [`character-other-${index + 1}`],
        text: `镜头${index + 1}文字`,
      })),
      {
        storyboard: ["storyboard-target", "ignored-second-id"],
        character: ["character-target"],
        text: "目标镜头文字",
      },
    ]);
    const pendingAssets = deferred<StoryboardAsset[]>();
    const { services, listChapters, listStoryboardAssets } = makeServices([first, target]);
    vi.spyOn(services, "listStoryboardAssets").mockReturnValue(pendingAssets.promise);
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    const onUnauthorized = vi.fn();
    const browseUserId = "browse-navigation-user";
    const browseScope = { userId: browseUserId, apiBaseUrl, seriesId: series.id, chapterId: target.id };
    window.localStorage.setItem(storyboardPageSizeStorageKey(browseScope), "10");
    window.localStorage.setItem(storyboardChapterStorageKey(browseScope), JSON.stringify({ page: 2, scrollTop: 700 }));
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const intent: ChapterNavigationIntent = {
      userId: browseUserId,
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 201,
      frameTarget: {
        storyboardAssetId: "storyboard-target",
        category: "characters",
        assetId: "character-target",
      },
    };

    renderBrowser(services, vi.fn(), onUnauthorized, series, browseUserId, {
      intent,
      onConsumed,
      onAbandoned,
    });

    expect(await screen.findByText("镜头11文字")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    expect(listStoryboardAssets).toHaveBeenCalledWith(series.id, target.id, expect.any(AbortSignal));
    expect(screen.getByText(/第 2 \/ 3 页/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "同名章节 · 镜头 22" })).not.toBeInTheDocument();
    expect(onConsumed).not.toHaveBeenCalled();

    await act(async () => {
      pendingAssets.resolve([
        asset("character-target", target.id),
        asset("storyboard-target", target.id, null),
      ]);
      await pendingAssets.promise;
    });

    const targetArticle = await screen.findByRole("article", { name: "同名章节 · 镜头 22" });
    await act(async () => {
      await Promise.resolve();
    });
    expect(targetArticle).toHaveFocus();
    expect(targetArticle).toHaveAttribute("aria-label", "同名章节 · 镜头 22");
    expect(screen.getByText(/第 3 \/ 3 页/)).toBeInTheDocument();
    expect(screen.getByText("已定位到目标镜头 22。")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(onConsumed).toHaveBeenCalledTimes(1);
    expect(onConsumed).toHaveBeenCalledWith(intent.navigationEpoch);
    expect(onAbandoned).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(scrollTo).not.toHaveBeenCalledWith(0, 700);
    scrollTo.mockRestore();
  });

  it.each([
    ["missing storyboard ID", [{ character: ["character-target"], text: "目标镜头文字" }]],
    ["duplicate storyboard ID", [
      { storyboard: ["storyboard-target"], character: ["character-target"], text: "第一处" },
      { storyboard: ["storyboard-target"], character: ["character-target"], text: "第二处" },
    ]],
    ["missing current category reference", [{ storyboard: ["storyboard-target"], character: ["another-character"], text: "目标镜头文字" }]],
  ] as const)("does not select a chapter or request assets when the fresh source has %s", async (_caseName, content) => {
    const target = chapter("chapter-invalid-frame-target", "目标章节", content as unknown as Chapter["content"]);
    const { services, listChapters, listStoryboardAssets } = makeServices([target]);
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 202,
      frameTarget: {
        storyboardAssetId: "storyboard-target",
        category: "characters",
        assetId: "character-target",
      },
    };

    renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", { intent, onConsumed, onAbandoned });

    expect(await screen.findByRole("heading", { name: "无法确认目标镜头" })).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /目标章节 · 镜头/ })).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).not.toHaveBeenCalled();
    expect(onConsumed).not.toHaveBeenCalled();
    expect(onAbandoned).not.toHaveBeenCalled();
  });

  it("keeps a verified chapter and retries the full chain after a missing response asset", async () => {
    const user = userEvent.setup();
    const target = chapter("chapter-late-mismatch", "目标章节", [
      { storyboard: ["storyboard-target"], character: ["character-target"], text: "已核对章节文字" },
    ]);
    const { services, listChapters, listStoryboardAssets } = makeServices([target]);
    vi.spyOn(services, "listStoryboardAssets")
      .mockResolvedValueOnce([asset("character-target", target.id)])
      .mockResolvedValueOnce([asset("storyboard-target", target.id)]);
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 203,
      frameTarget: {
        storyboardAssetId: "storyboard-target",
        category: "characters",
        assetId: "character-target",
      },
    };

    renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", { intent, onConsumed, onAbandoned });

    expect(await screen.findByText("已核对章节文字")).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("当前镜头快照与素材目录无法对应");
    expect(screen.getByRole("button", { name: "重新核对目标镜头" })).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(onConsumed).not.toHaveBeenCalled();
    expect(onAbandoned).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "重新核对目标镜头" }));
    expect(await screen.findByText("已定位到目标镜头 1。")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
    expect(onConsumed).toHaveBeenCalledTimes(1);
    expect(onAbandoned).not.toHaveBeenCalled();
  });

  it("does not consume or scroll when focus does not reach the target article", async () => {
    const target = chapter("chapter-focus-failure", "聚焦失败章节", [
      { storyboard: ["storyboard-focus"], character: ["character-focus"], text: "聚焦目标文字" },
    ]);
    const pendingAssets = deferred<StoryboardAsset[]>();
    const { services, listStoryboardAssets } = makeServices([target]);
    vi.spyOn(services, "listStoryboardAssets").mockReturnValue(pendingAssets.promise);
    const onConsumed = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 204,
      frameTarget: {
        storyboardAssetId: "storyboard-focus",
        category: "characters",
        assetId: "character-focus",
      },
    };

    renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", { intent, onConsumed });
    expect(await screen.findByText("聚焦目标文字")).toBeInTheDocument();
    const targetArticle = screen.getByRole("article", { name: "聚焦失败章节 · 镜头 1" });
    const focus = vi.fn();
    const scrollIntoView = vi.fn();
    Object.defineProperty(targetArticle, "focus", { configurable: true, value: focus });
    Object.defineProperty(targetArticle, "scrollIntoView", { configurable: true, value: scrollIntoView });

    await act(async () => {
      pendingAssets.resolve([asset("storyboard-focus", target.id)]);
      await pendingAssets.promise;
    });

    expect(targetArticle).not.toHaveFocus();
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("浏览器未能将焦点移到目标镜头");
    expect(onConsumed).not.toHaveBeenCalled();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
  });

  it("clears frame-location feedback on blur without replaying a consumed target", async () => {
    const user = userEvent.setup();
    const target = chapter("chapter-frame-feedback", "定位反馈章节", [
      { storyboard: ["storyboard-feedback"], character: ["character-feedback"], text: "定位反馈正文" },
    ]);
    const { services, listChapters, listStoryboardAssets } = makeServices([target], {
      [target.id]: [asset("storyboard-feedback", target.id)],
    });
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 205,
      frameTarget: {
        storyboardAssetId: "storyboard-feedback",
        category: "characters",
        assetId: "character-feedback",
      },
    };
    const onConsumed = vi.fn();
    const onBack = vi.fn();
    const onUnauthorized = vi.fn();
    const view = renderBrowser(services, onBack, onUnauthorized, series, "demo-user", { intent, onConsumed });

    expect(await screen.findByText("已定位到目标镜头 1。" )).toBeInTheDocument();
    const targetArticle = screen.getByRole("article", { name: "定位反馈章节 · 镜头 1" });
    expect(targetArticle).toHaveFocus();
    expect(targetArticle).toHaveClass("is-frame-navigation-target");
    expect(onConsumed).toHaveBeenCalledTimes(1);

    view.rerender(
      <ChapterBrowser
        onBack={onBack}
        onUnauthorized={onUnauthorized}
        navigationIntent={null}
        onNavigationConsumed={onConsumed}
        series={series}
        services={services}
        userId="demo-user"
      />,
    );
    const rereadButton = screen.getByRole("button", { name: "重新读取章节" });
    rereadButton.focus();
    expect(rereadButton).toHaveFocus();
    await waitFor(() => expect(screen.queryByText("已定位到目标镜头 1。")).not.toBeInTheDocument());
    expect(targetArticle).not.toHaveClass("is-frame-navigation-target");

    await user.click(rereadButton);
    expect(await screen.findByText("定位反馈正文")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
    expect(onConsumed).toHaveBeenCalledTimes(1);
  });

  it.each(["hidden", "disconnected"] as const)(
    "does not focus or consume a target article that becomes %s while assets are pending",
    async (state) => {
      const target = chapter("chapter-unavailable-frame", "暂不可见章节", [
        { storyboard: ["storyboard-unavailable"], character: ["character-unavailable"], text: "暂不可见正文" },
      ]);
      const pendingAssets = deferred<StoryboardAsset[]>();
      const { services, listStoryboardAssets } = makeServices([target]);
      vi.spyOn(services, "listStoryboardAssets").mockReturnValue(pendingAssets.promise);
      const onConsumed = vi.fn();
      const onAbandoned = vi.fn();
      const intent: ChapterNavigationIntent = {
        userId: "demo-user",
        services,
        seriesId: series.id,
        chapterId: target.id,
        navigationEpoch: state === "hidden" ? 206 : 207,
        frameTarget: {
          storyboardAssetId: "storyboard-unavailable",
          category: "characters",
          assetId: "character-unavailable",
        },
      };

      renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", { intent, onConsumed, onAbandoned });
      expect(await screen.findByText("暂不可见正文")).toBeInTheDocument();
      const targetArticle = screen.getByRole("article", { name: "暂不可见章节 · 镜头 1" });
      const focus = vi.fn();
      const scrollIntoView = vi.fn();
      Object.defineProperty(targetArticle, "focus", { configurable: true, value: focus });
      Object.defineProperty(targetArticle, "scrollIntoView", { configurable: true, value: scrollIntoView });
      if (state === "hidden") {
        targetArticle.setAttribute("hidden", "");
      } else {
        targetArticle.remove();
      }

      await act(async () => {
        pendingAssets.resolve([asset("storyboard-unavailable", target.id)]);
        await pendingAssets.promise;
      });

      expect(await screen.findByRole("alert")).toHaveTextContent("目标镜头当前不可见");
      expect(screen.getByRole("button", { name: "重新核对目标镜头" })).toBeInTheDocument();
      expect(focus).not.toHaveBeenCalled();
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(onConsumed).not.toHaveBeenCalled();
      expect(onAbandoned).not.toHaveBeenCalled();
      expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    },
  );

  it("rejects a stale valid-row callback after reread replaces it with duplicate and foreign identities", async () => {
    const user = userEvent.setup();
    const oldChapter = chapter("chapter-stale-row", "旧的唯一章节", [{ text: "旧目录文字" }]);
    const duplicateCurrent = chapter("chapter-stale-row", "重复目标", [{ text: "重复本地项" }]);
    const duplicateForeign = { ...duplicateCurrent, series_id: "another-series", title: "跨剧集重复项" };
    const foreignOnly = { ...chapter("chapter-foreign-only", "跨剧集章节", [{ text: "不应加载" }]), series_id: "another-series" };
    const legalChapter = chapter("chapter-legal-selection", "合法章节", [{ text: "合法章节正文" }]);
    const listChapters = vi.fn()
      .mockResolvedValueOnce([oldChapter])
      .mockResolvedValueOnce([duplicateCurrent, duplicateForeign, foreignOnly, legalChapter]);
    const listStoryboardAssets = vi.fn(async () => []);
    const services = makeServices([oldChapter]).services;
    vi.spyOn(services, "listChapters").mockImplementation(listChapters);
    vi.spyOn(services, "listStoryboardAssets").mockImplementation(listStoryboardAssets);
    const onAbandoned = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: "chapter-navigation-target",
      navigationEpoch: 86,
    };

    renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", { intent, onAbandoned });
    expect(await screen.findByRole("alert")).toHaveTextContent("不在当前目录中");
    const oldRow = screen.getByRole("button", { name: /旧的唯一章节/ });
    const oldClick = captureButtonHandler(oldRow);
    expect(oldRow).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "重新核对目标章节" }));
    expect(await screen.findByRole("button", { name: /重复目标/ })).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).not.toHaveBeenCalled();

    const invalidRows = [
      screen.getByRole("button", { name: /重复目标/ }),
      screen.getByRole("button", { name: /跨剧集重复项/ }),
      screen.getByRole("button", { name: /跨剧集章节/ }),
    ];
    for (const row of invalidRows) {
      expect(row).toBeDisabled();
      expect(row).toHaveTextContent("章节身份无法核对，不能打开。");
      const invalidClick = captureButtonHandler(row);
      await act(async () => invalidClick());
    }

    await act(async () => oldClick());
    expect(listStoryboardAssets).not.toHaveBeenCalled();
    expect(onAbandoned).not.toHaveBeenCalled();

    const validRow = screen.getByRole("button", { name: /合法章节/ });
    expect(validRow).toBeEnabled();
    await user.click(validRow);
    expect(await screen.findByText("合法章节正文")).toBeInTheDocument();
    expect(onAbandoned).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    expect(listStoryboardAssets).toHaveBeenCalledWith(series.id, legalChapter.id, expect.any(AbortSignal));
  });

  it.each([
    ["missing target", (_target: Chapter) => [] as Chapter[], "不在当前目录中"],
    ["duplicate target", (target: Chapter) => [target, { ...target, title: "重复目标" }], "多个同 ID 章节"],
    ["foreign target", (target: Chapter) => [{ ...target, series_id: "another-series" }], "不属于当前剧集"],
    [
      "same raw ID in current and foreign series",
      (target: Chapter) => [target, { ...target, series_id: "another-series", title: "跨剧集重复" }],
      "多个同 ID 章节",
    ],
  ] as const)("keeps the accepted target pending after a %s list and selects it on explicit retry", async (_caseName, makeInitial, message) => {
    const user = userEvent.setup();
    const target = chapter("chapter-retry-target", "待确认章节", [{ text: "重试后目标文字" }]);
    const listChapters = vi.fn()
      .mockResolvedValueOnce(makeInitial(target))
      .mockResolvedValueOnce([target]);
    const listStoryboardAssets = vi.fn(async () => []);
    const services = makeServices([target]).services;
    vi.spyOn(services, "listChapters").mockImplementation(listChapters);
    vi.spyOn(services, "listStoryboardAssets").mockImplementation(listStoryboardAssets);
    const onConsumed = vi.fn();
    const onAbandoned = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 82,
    };

    renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", {
      intent,
      onConsumed,
      onAbandoned,
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.queryByText("重试后目标文字")).not.toBeInTheDocument();
    expect(listStoryboardAssets).not.toHaveBeenCalled();
    expect(onConsumed).not.toHaveBeenCalled();
    expect(onAbandoned).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "重新核对目标章节" }));
    expect(await screen.findByText("重试后目标文字")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    expect(listStoryboardAssets).toHaveBeenCalledWith(series.id, target.id, expect.any(AbortSignal));
    expect(onConsumed).toHaveBeenCalledTimes(1);
    expect(onAbandoned).not.toHaveBeenCalled();
  });

  it("keeps a target through a non-401 directory error and consumes it after retry", async () => {
    const user = userEvent.setup();
    const target = chapter("chapter-recover-target", "恢复后的目标", [{ text: "目标恢复正文" }]);
    const listChapters = vi.fn()
      .mockRejectedValueOnce(new ApiError("http", "temporary failure", 500))
      .mockResolvedValueOnce([target]);
    const services = makeServices([target]).services;
    vi.spyOn(services, "listChapters").mockImplementation(listChapters);
    const onUnauthorized = vi.fn();
    const onConsumed = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 84,
    };

    renderBrowser(services, vi.fn(), onUnauthorized, series, "demo-user", { intent, onConsumed });

    expect(await screen.findByRole("heading", { name: "暂时无法读取内容" })).toBeInTheDocument();
    expect(screen.queryByText("目标恢复正文")).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(onConsumed).not.toHaveBeenCalled();
    await user.click(screen.getAllByRole("button", { name: "重试读取" })[0]!);
    expect(await screen.findByText("目标恢复正文")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(onConsumed).toHaveBeenCalledTimes(1);
  });

  it("selects a valid empty target chapter without reading storyboard assets", async () => {
    const target = chapter("chapter-empty-target", "空的目标章节", []);
    const { services, listStoryboardAssets } = makeServices([
      chapter("chapter-first-nonempty", "首章", [{ text: "首章不应显示" }]),
      target,
    ]);
    const onConsumed = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 85,
    };

    renderBrowser(services, vi.fn(), vi.fn(), series, "demo-user", { intent, onConsumed });

    expect(await screen.findByRole("heading", { name: "空的目标章节" })).toBeInTheDocument();
    expect(screen.queryByText("首章不应显示")).not.toBeInTheDocument();
    expect(onConsumed).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).not.toHaveBeenCalled();
  });

  it("does not invalidate the pending asset read when the selected chapter is clicked again", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-repeated-selection", "已选章节", [{ text: "等待原图" }]);
    const pendingAssets = deferred<StoryboardAsset[]>();
    const { services } = makeServices([current]);
    const listStoryboardAssets = vi.spyOn(services, "listStoryboardAssets").mockReturnValue(pendingAssets.promise);
    renderBrowser(services);

    expect(await screen.findByText("等待原图")).toBeInTheDocument();
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    const notesButton = screen.getByRole("button", { name: "查看我的制作记录" });
    expect(notesButton).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /已选章节/ }));
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    await act(async () => {
      pendingAssets.resolve([]);
      await pendingAssets.promise;
    });
    await waitFor(() => expect(notesButton).toBeEnabled());
  });

  it.each(["user", "services"] as const)(
    "hides the old ready directory on the first layout commit after a %s-only scope change and A→B→A",
    async (changedScope) => {
      const oldChapter = chapter("chapter-scope-old", "旧目录章节", [{ text: "旧作用域正文" }]);
      const newChapter = chapter("chapter-scope-new", "新目录章节", [{ text: "新作用域正文" }]);
      const pendingB = deferred<Chapter[]>();
      const servicesA = makeServices([oldChapter]).services;
      const servicesB = changedScope === "services" ? makeServices([newChapter]).services : servicesA;
      const listA = vi.spyOn(servicesA, "listChapters");
      const listB = servicesB === servicesA
        ? listA
        : vi.spyOn(servicesB, "listChapters");
      if (servicesB === servicesA) {
        listA
          .mockResolvedValueOnce([oldChapter])
          .mockReturnValueOnce(pendingB.promise)
          .mockResolvedValueOnce([oldChapter]);
      } else {
        listA.mockResolvedValueOnce([oldChapter]).mockResolvedValueOnce([oldChapter]);
        listB.mockReturnValue(pendingB.promise);
      }

      const commits: boolean[] = [];
      const onBack = vi.fn();
      const onUnauthorized = vi.fn();
      const renderForScope = (nextUserId: string, nextServices: WorkspaceServices) => (
        <>
          <ChapterBrowser
            onBack={onBack}
            onUnauthorized={onUnauthorized}
            series={series}
            services={nextServices}
            userId={nextUserId}
          />
          <LayoutCommitObserver onCommit={() => {
            commits.push(screen.queryByText("旧作用域正文") !== null);
          }} />
        </>
      );
      const view = render(renderForScope("scope-user-a", servicesA));

      expect(await screen.findByText("旧作用域正文")).toBeInTheDocument();
      expect(listA).toHaveBeenCalledTimes(1);

      const changedCommit = commits.length;
      view.rerender(renderForScope(
        changedScope === "user" ? "scope-user-b" : "scope-user-a",
        changedScope === "services" ? servicesB : servicesA,
      ));
      expect(commits[changedCommit]).toBe(false);
      expect(screen.queryByText("旧作用域正文")).not.toBeInTheDocument();
      await waitFor(() => expect(listB).toHaveBeenCalledTimes(changedScope === "services" ? 1 : 2));

      const returnedCommit = commits.length;
      view.rerender(renderForScope("scope-user-a", servicesA));
      expect(commits[returnedCommit]).toBe(false);
      expect(screen.queryByText("旧作用域正文")).not.toBeInTheDocument();
      expect(await screen.findByText("旧作用域正文")).toBeInTheDocument();
      expect(listA).toHaveBeenCalledTimes(servicesB === servicesA ? 3 : 2);

      await act(async () => {
        pendingB.resolve([newChapter]);
        await pendingB.promise;
      });
      expect(screen.getByText("旧作用域正文")).toBeInTheDocument();
      expect(screen.queryByText("新作用域正文")).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
    },
  );

  it.each(["success", "401"] as const)(
    "does not revive a pending target after a real scope B-pending to A return when the old request settles with %s",
    async (oldOutcome) => {
    const target = chapter("chapter-aba-target", "不可复活目标", [{ text: "不应显示的旧目标" }]);
    const staleA = deferred<Chapter[]>();
    const pendingB = deferred<Chapter[]>();
    const currentA = deferred<Chapter[]>();
    const servicesA = makeServices([target]).services;
    const servicesB = makeServices([]).services;
    const listChaptersA = vi.spyOn(servicesA, "listChapters")
      .mockReturnValueOnce(staleA.promise)
      .mockReturnValueOnce(currentA.promise);
    const listChaptersB = vi.spyOn(servicesB, "listChapters").mockReturnValue(pendingB.promise);
    const listStoryboardAssetsA = vi.spyOn(servicesA, "listStoryboardAssets");
    const onUnauthorized = vi.fn();
    const onConsumed = vi.fn();
    const intent: ChapterNavigationIntent = {
      userId: "scope-user-a",
      services: servicesA,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch: 83,
    };
    const renderForScope = (nextUserId: string, nextServices: WorkspaceServices) => (
      <ChapterBrowser
        navigationIntent={intent}
        onBack={vi.fn()}
        onNavigationConsumed={onConsumed}
        onUnauthorized={onUnauthorized}
        series={series}
        services={nextServices}
        userId={nextUserId}
      />
    );
    const view = render(renderForScope("scope-user-a", servicesA));
    await waitFor(() => expect(listChaptersA).toHaveBeenCalledTimes(1));

    view.rerender(renderForScope("scope-user-b", servicesB));
    await waitFor(() => expect(listChaptersB).toHaveBeenCalledTimes(1));
    expect(screen.getByText("正在读取章节内容…")).toBeInTheDocument();

    view.rerender(renderForScope("scope-user-a", servicesA));
    await waitFor(() => expect(listChaptersA).toHaveBeenCalledTimes(2));

    await act(async () => {
      if (oldOutcome === "success") {
        staleA.resolve([target]);
        await staleA.promise;
      } else {
        staleA.reject(new ApiError("http", "expired", 401));
        await staleA.promise.catch(() => undefined);
      }
      pendingB.resolve([target]);
      await pendingB.promise;
    });
    expect(screen.queryByText("不应显示的旧目标")).not.toBeInTheDocument();
    expect(listStoryboardAssetsA).not.toHaveBeenCalled();
    expect(onConsumed).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();

    await act(async () => {
      currentA.resolve([target]);
      await currentA.promise;
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("无法确认目标章节");
    expect(screen.queryByText("不应显示的旧目标")).not.toBeInTheDocument();
    expect(listStoryboardAssetsA).not.toHaveBeenCalled();
    expect(onConsumed).not.toHaveBeenCalled();
    },
  );

  it("opens references before assets finish, reads only the selected type, and stays separate from notes and rough cut", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-references", "镜头关联素材", [
      { text: "第一镜头", character: ["character-reference"], scene: ["scene-reference"] },
    ]);
    const pendingAssets = deferred<StoryboardAsset[]>();
    const { services, listStoryboardAssets } = makeServices([current]);
    listStoryboardAssets.mockReturnValueOnce(pendingAssets.promise);
    const listCharacters = vi.spyOn(services, "listCharacters").mockResolvedValue([
      character("character-reference", current.series_id, "沈照"),
    ]);
    const listScenes = vi.spyOn(services, "listScenes").mockResolvedValue([]);
    const listProps = vi.spyOn(services, "listProps").mockResolvedValue([]);
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes").mockResolvedValue(
      parsePersonalProductionSnapshot({
        chapter_id: current.id,
        revision: 0,
        media_state: "empty",
        frames: [],
        frame_notes: {},
        resume_frame_id: null,
      }, current.id),
    );
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockResolvedValue({
      chapter_id: current.id,
      revision: 1,
      saved: true,
      frames: [],
      removed_asset_ids: [],
    });
    const { container } = renderBrowser(services);

    expect(await screen.findByText("第一镜头")).toBeInTheDocument();
    expect(await screen.findByText("正在读取本章原图…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBeInTheDocument();
    expect(screen.getByText("请选择素材分类。")).toBeInTheDocument();
    expect(listCharacters).not.toHaveBeenCalled();
    expect(listScenes).not.toHaveBeenCalled();
    expect(listProps).not.toHaveBeenCalled();
    expect(getNotes).not.toHaveBeenCalled();
    expect(getRoughCut).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("沈照", { selector: "strong" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listScenes).not.toHaveBeenCalled();
    expect(listProps).not.toHaveBeenCalled();
    expect(container.textContent).not.toMatch(/character-reference|canonical key|voice ref|https?:/i);
    expect(container.querySelector("audio, video, source, a")).toBeNull();

    await act(async () => {
      pendingAssets.resolve([]);
      await pendingAssets.promise;
    });
    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBeInTheDocument();
    expect(screen.getByText("沈照", { selector: "strong" })).toBeInTheDocument();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
  });

  it("ignores a close callback from an earlier reference-panel owner", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-stale-reference-close", "旧关闭回调", [
      { text: "同一镜头", character: ["character-close"] },
    ]);
    const { services } = makeServices([current]);
    vi.spyOn(services, "listCharacters").mockResolvedValue([
      character("character-close", current.series_id, "当前角色"),
    ]);
    renderBrowser(services);

    await screen.findByText("同一镜头");
    await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
    const oldOwner = frameAssetReferencesBridge.owner;
    const oldClose = frameAssetReferencesBridge.onClose;
    if (oldOwner === null || oldClose === null) {
      throw new Error("The reference panel owner and close callback should be captured.");
    }

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBeInTheDocument();
    expect(frameAssetReferencesBridge.owner).not.toBe(oldOwner);

    await act(async () => oldClose(oldOwner));
    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("当前角色", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "镜头 1 的关联素材" })).toBeInTheDocument();
  });

  it.each(["user", "services"] as const)(
    "permanently closes reference panels on the first parent %s-scope commit",
    async (changedScope) => {
      const user = userEvent.setup();
      const current = chapter("chapter-reference-scope", "引用作用域隔离", [
        { text: "保留同一章节对象", character: ["scope-character"] },
      ]);
      const servicesA = makeServices([current]).services;
      const servicesB = changedScope === "services" ? makeServices([current]).services : servicesA;
      const charactersA = vi.spyOn(servicesA, "listCharacters").mockResolvedValue([
        character("scope-character", current.series_id, "旧作用域角色"),
      ]);
      const charactersB = servicesB === servicesA
        ? charactersA
        : vi.spyOn(servicesB, "listCharacters").mockResolvedValue([
          character("scope-character", current.series_id, "新服务角色"),
        ]);
      const commits: boolean[] = [];
      const onBack = vi.fn();
      const onUnauthorized = vi.fn();
      const renderForScope = (userId: string, nextServices: WorkspaceServices) => (
        <>
          <ChapterBrowser
            onBack={onBack}
            onUnauthorized={onUnauthorized}
            series={series}
            services={nextServices}
            userId={userId}
          />
          <LayoutCommitObserver onCommit={() => {
            commits.push(screen.queryByRole("region", { name: "镜头 1 的关联素材" }) !== null);
          }} />
        </>
      );
      const view = render(renderForScope("scope-user-a", servicesA));

      expect(await screen.findByText("保留同一章节对象")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(await screen.findByText("旧作用域角色", { selector: "strong" })).toBeInTheDocument();
      expect(charactersA).toHaveBeenCalledTimes(1);

      const changedCommit = commits.length;
      view.rerender(renderForScope(
        changedScope === "user" ? "scope-user-b" : "scope-user-a",
        changedScope === "services" ? servicesB : servicesA,
      ));
      expect(commits[changedCommit]).toBe(false);
      expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
      expect(await screen.findByText("保留同一章节对象")).toBeInTheDocument();

      const returnedCommit = commits.length;
      view.rerender(renderForScope("scope-user-a", servicesA));
      expect(commits[returnedCommit]).toBe(false);
      expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
      expect(await screen.findByText("保留同一章节对象")).toBeInTheDocument();
      expect(charactersA).toHaveBeenCalledTimes(1);
      if (changedScope === "services") {
        expect(charactersB).toHaveBeenCalledTimes(0);
      }

      await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
      await user.click(screen.getByRole("button", { name: "角色" }));
      expect(await screen.findByText("旧作用域角色", { selector: "strong" })).toBeInTheDocument();
      expect(charactersA).toHaveBeenCalledTimes(2);
    },
  );

  it("closes references on an explicit original-image retry and requires a fresh selection", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-reference-image-retry", "原图重读关闭引用", [
      { text: "正文仍可阅读", character: ["image-retry-character"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current]);
    let assetAttempt = 0;
    listStoryboardAssets.mockImplementation(async () => {
      assetAttempt += 1;
      if (assetAttempt === 1) {
        throw new ApiError("http", "asset service unavailable", 500);
      }
      return [];
    });
    const listCharacters = vi.spyOn(services, "listCharacters").mockResolvedValue([
      character("image-retry-character", current.series_id, "原图重读后角色"),
    ]);
    renderBrowser(services);

    expect(await screen.findByRole("heading", { name: "暂时无法读取内容" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("原图重读后角色", { selector: "strong" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重试读取原图" }));
    expect(await screen.findByText("正文仍可阅读")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
    expect(listCharacters).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
    await user.click(screen.getByRole("button", { name: "角色" }));
    expect(await screen.findByText("原图重读后角色", { selector: "strong" })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(2);
  });

  it("defaults to the first API chapter, preserves order, and reuses successful A→B→A asset snapshots", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-first", "第一章", [
      { text: "镜头 A", storyboard: ["frame-a"] },
      { text: "镜头 B", storyboard: ["frame-b"] },
    ], {
      order: 0,
      lock: { locked: true, locked_by_username: "周编剧", is_mine: false },
    });
    const second = chapter("chapter-second", "第二章", [
      { text: "镜头 C", storyboard: ["frame-c"] },
    ], { order: 100 });
    const { services, listChapters, listStoryboardAssets } = makeServices([first, second], {
      "chapter-first": [
        asset("frame-a", first.id, "http://127.0.0.1:4175/media/frame-a.svg"),
        asset("frame-b", first.id, "http://127.0.0.1:4175/media/frame-b.svg"),
      ],
      "chapter-second": [asset("frame-c", second.id, "http://127.0.0.1:4175/media/frame-c.svg")],
    });
    renderBrowser(services);

    expect(await screen.findByRole("heading", { name: "第一章" })).toBeInTheDocument();
    expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/frame-a.svg",
    );
    expect(screen.getByAltText("镜头 2 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/frame-b.svg",
    );
    expect(screen.getByText(/当前由 周编剧 编辑/)).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenLastCalledWith(first.series_id, first.id, expect.any(AbortSignal));
    expect(screen.queryByText("不可直接展示的资产名")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /第二章/ }));
    expect(await screen.findByText("镜头 C")).toBeInTheDocument();
    expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/frame-c.svg",
    );

    await user.click(screen.getByRole("button", { name: /第一章/ }));
    expect(await screen.findByText("镜头 A")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /第一章/ }));
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
    expect(listChapters).toHaveBeenCalledTimes(1);
  });

  it.each([
    { content: null, message: "本章暂无分镜" },
    { content: [], message: "本章暂无分镜" },
  ])("distinguishes a valid empty content state ($message) without an asset request", async ({ content, message }) => {
    const current = chapter("chapter-empty", "空章节", content);
    const { services, listStoryboardAssets } = makeServices([current]);
    renderBrowser(services);

    expect(await screen.findByRole("heading", { name: message })).toBeInTheDocument();
    expect(listStoryboardAssets).not.toHaveBeenCalled();
  });

  it("shows an empty chapter-list state without requesting storyboard assets", async () => {
    const { services, listChapters, listStoryboardAssets } = makeServices([]);
    renderBrowser(services);

    expect(await screen.findByRole("heading", { name: "这个剧集还没有章节" })).toBeInTheDocument();
    expect(screen.getByText("暂无章节")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).not.toHaveBeenCalled();
  });

  it("opens the rough-cut panel only on request and keeps it separate from notes and media", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-rough-cut-open", "粗剪入口章节", [
      { text: "正文仍然可读", storyboard: ["frame-rough-cut"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current]);
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockResolvedValue({
      chapter_id: current.id,
      revision: 2,
      saved: true,
      frames: [{
        asset_id: "private-rough-cut-id",
        frame_index: 0,
        text: "草稿中的纯文本",
        preview_url: "https://media.example.invalid/private.mp4",
        missing_reason: null,
        included: true,
        pending: false,
      }],
      removed_asset_ids: [],
    });
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes").mockResolvedValue(
      parsePersonalProductionSnapshot({
        chapter_id: current.id,
        revision: 1,
        media_state: "ready",
        frames: [],
        frame_notes: {},
        resume_frame_id: null,
      }, current.id),
    );
    const { container } = renderBrowser(services);

    expect(await screen.findByText("正文仍然可读")).toBeInTheDocument();
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    expect(getRoughCut).not.toHaveBeenCalled();
    expect(getNotes).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    const panel = await screen.findByRole("region", { name: "我的粗剪草稿" });
    expect(await screen.findByText("草稿中的纯文本")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(getRoughCut).toHaveBeenCalledWith(current.id, expect.any(AbortSignal));
    expect(getNotes).not.toHaveBeenCalled();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(panel.compareDocumentPosition(container.querySelector(".storyboard-list")!)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(container.querySelector("video, audio, source")).toBeNull();
    expect(container.textContent).not.toContain("private-rough-cut-id");
    expect(container.textContent).not.toContain("private.mp4");

    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
  });

  it("keeps an explicitly opened rough-cut visible when the pending asset request later succeeds", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-rough-cut-assets-pending", "原图仍在读取", [
      { text: "章节镜头文字", storyboard: ["frame-pending-assets"] },
    ]);
    const pendingAssets = deferred<StoryboardAsset[]>();
    const { services } = makeServices([current]);
    const listStoryboardAssets = vi.spyOn(services, "listStoryboardAssets")
      .mockReturnValue(pendingAssets.promise);
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockResolvedValue({
      chapter_id: current.id,
      revision: 1,
      saved: true,
      frames: [{
        asset_id: "frame-pending-assets",
        frame_index: 99,
        text: "粗剪先行显示",
        preview_url: null,
        missing_reason: null,
        included: false,
        pending: true,
      }],
      removed_asset_ids: [],
    });
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes");
    renderBrowser(services);

    expect(await screen.findByText("正在读取本章原图…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看我的粗剪草稿" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("粗剪先行显示")).toBeInTheDocument();
    expect(screen.getByText("原图目录读取完成前，暂不能核对定位。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "定位到对应镜头 1" })).not.toBeInTheDocument();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(getNotes).not.toHaveBeenCalled();

    await act(async () => {
      pendingAssets.resolve([asset(
        "frame-pending-assets",
        current.id,
        "http://127.0.0.1:4175/media/late-asset.svg",
      )]);
      await pendingAssets.promise;
    });

    expect(screen.getByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(screen.getByText("粗剪先行显示")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "定位到对应镜头 1" })).toBeInTheDocument();
    expect(screen.queryByText("已定位到粗剪镜头 1。")).not.toBeInTheDocument();
    expect(screen.getByAltText("镜头 1 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/late-asset.svg",
    );
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(getNotes).not.toHaveBeenCalled();
  });

  it("locates the frame by stable identity and keeps the rough-cut panel open", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-rough-cut-locate", "粗剪定位章节", [
      { text: "章节第一镜头", storyboard: ["rough-cut-first"] },
      { text: "粗剪对应第二镜头", storyboard: ["rough-cut-target"], character: ["rough-cut-character"] },
    ]);
    const { services, listChapters, listStoryboardAssets } = makeServices([current], {
      [current.id]: [asset("rough-cut-first", current.id), asset("rough-cut-target", current.id)],
    });
    const roughSnapshot: PersonalRoughCutSnapshot = {
      chapter_id: current.id,
      revision: 3,
      saved: true,
      frames: [{
        asset_id: "rough-cut-target",
        frame_index: 0,
        text: "粗剪列表首项来自章节第二镜头",
        preview_url: null,
        missing_reason: "当前没有可用视频。",
        included: false,
        pending: true,
      }],
      removed_asset_ids: [],
    };
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockResolvedValue(roughSnapshot);
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes");
    const { container } = renderBrowser(services);

    expect(await screen.findByRole("heading", { name: "粗剪定位章节" })).toBeInTheDocument();
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    expect(getRoughCut).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("粗剪列表首项来自章节第二镜头")).toBeInTheDocument();
    const locate = await screen.findByRole("button", { name: "定位到对应镜头 2" });
    const frame = screen.getByRole("article", { name: "粗剪定位章节 · 镜头 2" });
    const scrollIntoView = vi.fn();
    Object.defineProperty(frame, "scrollIntoView", { configurable: true, value: scrollIntoView });
    const imageCount = container.querySelectorAll("img").length;

    await user.click(locate);

    expect(frame).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "center" });
    expect(frame).toHaveClass("is-rough-cut-frame-target");
    expect(screen.getByText("已定位到粗剪镜头 2。")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(container.querySelectorAll("img")).toHaveLength(imageCount);
    expect(container.textContent).not.toContain("rough-cut-target");
    const relatedAssetsButton = within(frame).getByRole("button", { name: "查看镜头 2 的关联素材" });
    act(() => relatedAssetsButton.focus());
    expect(relatedAssetsButton).toHaveFocus();
    expect(screen.queryByText("已定位到粗剪镜头 2。")).not.toBeInTheDocument();
    expect(frame).not.toHaveClass("is-rough-cut-frame-target");
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    expect(getNotes).not.toHaveBeenCalled();
  });

  it.each(["hidden", "detached", "focus-does-not-transfer"] as const)(
    "keeps the locate action retryable when the target frame is %s",
    async (failure) => {
      const user = userEvent.setup();
      const current = chapter("chapter-rough-cut-focus-guard", "粗剪焦点守卫", [
        { text: "可见目标镜头", storyboard: ["rough-cut-focus-target"] },
      ]);
      const { services, listStoryboardAssets } = makeServices([current], {
        [current.id]: [asset("rough-cut-focus-target", current.id)],
      });
      const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockResolvedValue({
        chapter_id: current.id,
        revision: 1,
        saved: true,
        frames: [{
          asset_id: "rough-cut-focus-target",
          frame_index: 0,
          text: "目标镜头正文",
          preview_url: null,
          missing_reason: null,
          included: false,
          pending: true,
        }],
        removed_asset_ids: [],
      });
      renderBrowser(services);
      await screen.findByRole("heading", { name: "粗剪焦点守卫" });
      await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      const locate = await screen.findByRole("button", { name: "定位到对应镜头 1" });
      const target = screen.getByRole("article", { name: "粗剪焦点守卫 · 镜头 1" });
      const scrollIntoView = vi.fn();
      Object.defineProperty(target, "scrollIntoView", { configurable: true, value: scrollIntoView });

      if (failure === "hidden") {
        target.setAttribute("hidden", "");
      } else if (failure === "detached") {
        target.remove();
      } else {
        vi.spyOn(target, "focus").mockImplementation(() => undefined);
      }

      await user.click(locate);

      expect(screen.getByText("暂时无法定位到对应镜头，请确认该镜头仍在当前章节中后重试。")).toBeInTheDocument();
      expect(target).not.toHaveClass("is-rough-cut-frame-target");
      expect(scrollIntoView).not.toHaveBeenCalled();
      expect(getRoughCut).toHaveBeenCalledTimes(1);
      expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
      expect(screen.getByRole("button", { name: "定位到对应镜头 1" })).toBeInTheDocument();
    },
  );

  it("does not let a prior rough-cut read invalidation clear a newer frame location", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-rough-cut-read-ticket", "粗剪读取代次", [
      { text: "读取代次目标镜头", storyboard: ["rough-read-ticket-frame"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current], {
      [current.id]: [asset("rough-read-ticket-frame", current.id)],
    });
    const makeSnapshot = (revision: number): PersonalRoughCutSnapshot => ({
      chapter_id: current.id,
      revision,
      saved: true,
      frames: [{
        asset_id: "rough-read-ticket-frame",
        frame_index: 0,
        text: "版本 " + revision,
        preview_url: null,
        missing_reason: null,
        included: true,
        pending: false,
      }],
      removed_asset_ids: [],
    });
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut")
      .mockResolvedValueOnce(makeSnapshot(1))
      .mockResolvedValueOnce(makeSnapshot(2))
      .mockResolvedValueOnce(makeSnapshot(3));
    renderBrowser(services);

    await screen.findByRole("heading", { name: "粗剪读取代次" });
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("版本 1")).toBeInTheDocument();
    const firstReadInvalidation = roughCutPanelBridge.onFrameNavigationInvalidated;
    if (firstReadInvalidation === null) {
      throw new Error("The current rough-cut invalidation callback should be captured.");
    }
    const frame = screen.getByRole("article", { name: "粗剪读取代次 · 镜头 1" });
    const scrollIntoView = vi.fn();
    Object.defineProperty(frame, "scrollIntoView", { configurable: true, value: scrollIntoView });
    await user.click(screen.getByRole("button", { name: "定位到对应镜头 1" }));
    expect(frame).toHaveFocus();
    expect(screen.getByText("已定位到粗剪镜头 1。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByText("版本 2")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "定位到对应镜头 1" }));
    expect(screen.getByText("已定位到粗剪镜头 1。")).toBeInTheDocument();
    const secondReadFeedback = screen.getByText("已定位到粗剪镜头 1。");
    const firstIdentity = roughCutPanelBridge.lastInvalidation;
    if (firstIdentity === null) {
      throw new Error("The first read invalidation identity should have been captured.");
    }

    await act(async () => firstReadInvalidation(firstIdentity));

    expect(screen.getByText("已定位到粗剪镜头 1。")).toBe(secondReadFeedback);
    expect(frame).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledTimes(2);

    const oldLocate = roughCutPanelBridge.onLocateFrame;
    const oldClose = roughCutPanelBridge.onClose;
    const oldInvalidate = roughCutPanelBridge.onFrameNavigationInvalidated;
    const oldTarget = roughCutPanelBridge.lastTarget;
    if (oldLocate === null || oldClose === null || oldInvalidate === null || oldTarget === null) {
      throw new Error("The located rough-cut callbacks and target should be captured.");
    }

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("版本 3")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "定位到对应镜头 1" }));
    expect(frame).toHaveFocus();
    expect(screen.getByText("已定位到粗剪镜头 1。")).toBeInTheDocument();
    const reopenedFeedback = screen.getByText("已定位到粗剪镜头 1。");
    scrollIntoView.mockClear();

    await act(async () => {
      oldLocate(oldTarget);
      oldClose();
      oldInvalidate(firstIdentity);
    });

    expect(frame).toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(screen.getByText("已定位到粗剪镜头 1。")).toBe(reopenedFeedback);
    expect(getRoughCut).toHaveBeenCalledTimes(3);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
  });

  it("closes on chapter navigation and reread, then requires an explicit new rough-cut read", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-rough-cut-a", "粗剪章节 A", []);
    const second = chapter("chapter-rough-cut-b", "粗剪章节 B", []);
    const { services, listChapters } = makeServices([first, second]);
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockImplementation(async (chapterId) => ({
      chapter_id: chapterId,
      revision: 0,
      saved: false,
      frames: [],
      removed_asset_ids: [],
    }));
    const onBack = vi.fn();
    renderBrowser(services, onBack);

    expect(await screen.findByRole("heading", { name: "粗剪章节 A" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("未保存的初始投影")).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: /粗剪章节 B/ }));
    expect(await screen.findByRole("heading", { name: "粗剪章节 B" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(3);
    await user.click(screen.getByRole("button", { name: "重新读取章节" }));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(3);

    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(4);
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(onBack).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
  });

  it("closes an open rough-cut panel when the chapter's original image is re-read", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-rough-cut-assets-retry", "图片重读会关闭粗剪", [
      { text: "仍可阅读正文", storyboard: ["rough-cut-frame"] },
    ]);
    const { services } = makeServices([current]);
    const stale = deferred<PersonalRoughCutSnapshot>();
    let assetAttempts = 0;
    const listStoryboardAssets = vi.spyOn(services, "listStoryboardAssets").mockImplementation(async () => {
      assetAttempts += 1;
      if (assetAttempts === 1) {
        throw new ApiError("http", "asset service unavailable", 500);
      }
      return [];
    });
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockReturnValue(stale.promise);
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);

    expect(await screen.findByRole("heading", { name: "暂时无法读取内容" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重试读取原图" }));
    expect(await screen.findByText("仍可阅读正文")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
    expect(getRoughCut).toHaveBeenCalledTimes(1);
    await act(async () => {
      stale.reject(new ApiError("http", "expired", 401));
      await stale.promise.catch(() => undefined);
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it.each(["success", "unauthorized"] as const)(
    "settles a pending rough-cut %s after chapter reread without reopening it",
    async (lateResult) => {
      const user = userEvent.setup();
      const current = chapter("chapter-rough-cut-refresh-pending", "重读时等待的粗剪", []);
      const stale = deferred<PersonalRoughCutSnapshot>();
      const { services, listChapters } = makeServices([current]);
      const getRoughCut = vi.spyOn(services, "getPersonalRoughCut")
        .mockReturnValueOnce(stale.promise)
        .mockResolvedValueOnce({
          chapter_id: current.id,
          revision: 1,
          saved: true,
          frames: [],
          removed_asset_ids: [],
        });
      const onUnauthorized = vi.fn();
      renderBrowser(services, vi.fn(), onUnauthorized);

      expect(await screen.findByRole("heading", { name: "重读时等待的粗剪" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      await waitFor(() => expect(getRoughCut).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: "重新读取章节" }));
      await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
      expect(await screen.findByRole("heading", { name: "重读时等待的粗剪" })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();

      await act(async () => {
        if (lateResult === "unauthorized") {
          stale.reject(new ApiError("http", "expired", 401));
          await stale.promise.catch(() => undefined);
        } else {
          stale.resolve({
            chapter_id: current.id,
            revision: 8,
            saved: true,
            frames: [],
            removed_asset_ids: [],
          });
          await stale.promise;
        }
      });
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(getRoughCut).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      expect(await screen.findByText("已保存草稿的当前投影 · 版本 1")).toBeInTheDocument();
      expect(screen.queryByText("已保存草稿的当前投影 · 版本 8")).not.toBeInTheDocument();
      expect(getRoughCut).toHaveBeenCalledTimes(2);
    },
  );

  it("invalidates a pending rough-cut on close before a new explicit read", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-rough-cut-close-pending", "关闭中的粗剪", []);
    const stale = deferred<PersonalRoughCutSnapshot>();
    const { services } = makeServices([current]);
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut")
      .mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce({
        chapter_id: current.id,
        revision: 2,
        saved: true,
        frames: [],
        removed_asset_ids: [],
      });
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);

    expect(await screen.findByRole("heading", { name: "关闭中的粗剪" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    await waitFor(() => expect(getRoughCut).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("已保存草稿的当前投影 · 版本 2")).toBeInTheDocument();

    await act(async () => {
      stale.reject(new ApiError("http", "expired", 401));
      await stale.promise.catch(() => undefined);
    });
    expect(screen.getByText("已保存草稿的当前投影 · 版本 2")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
  });

  it("permanently closes an open rough-cut view after an A→B→A user-scope round trip", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-same-object", "同一对象章节", []);
    const { services, listChapters } = makeServices([current]);
    const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockImplementation(async (chapterId) => ({
      chapter_id: chapterId,
      revision: 1,
      saved: true,
      frames: [],
      removed_asset_ids: [],
    }));
    const onBack = vi.fn();
    const onUnauthorized = vi.fn();
    const renderForUser = (userId: string) => (
      <ChapterBrowser
        onBack={onBack}
        onUnauthorized={onUnauthorized}
        series={series}
        services={services}
        userId={userId}
      />
    );
    const view = render(renderForUser("user-a"));

    expect(await screen.findByRole("heading", { name: "同一对象章节" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);

    view.rerender(renderForUser("user-b"));
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("heading", { name: "同一对象章节" })).toBeInTheDocument();
    view.rerender(renderForUser("user-a"));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(3));
    expect(await screen.findByRole("heading", { name: "同一对象章节" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByRole("region", { name: "我的粗剪草稿" })).toBeInTheDocument();
    expect(getRoughCut).toHaveBeenCalledTimes(2);
  });

  it.each(["success", "unauthorized"] as const)(
    "settles an old rough-cut %s after switching chapters without publishing it",
    async (lateResult) => {
      const user = userEvent.setup();
      const first = chapter("chapter-rough-cut-pending-a", "待关闭粗剪 A", []);
      const second = chapter("chapter-rough-cut-pending-b", "当前粗剪 B", []);
      const stale = deferred<PersonalRoughCutSnapshot>();
      const { services } = makeServices([first, second]);
      const getRoughCut = vi.spyOn(services, "getPersonalRoughCut").mockImplementation((chapterId) => (
        chapterId === first.id
          ? stale.promise
          : Promise.resolve({
              chapter_id: chapterId,
              revision: 0,
              saved: false,
              frames: [],
              removed_asset_ids: [],
            })
      ));
      const onUnauthorized = vi.fn();
      renderBrowser(services, vi.fn(), onUnauthorized);

      expect(await screen.findByRole("heading", { name: "待关闭粗剪 A" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      await waitFor(() => expect(getRoughCut).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: /当前粗剪 B/ }));
      expect(await screen.findByRole("heading", { name: "当前粗剪 B" })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();

      await act(async () => {
        if (lateResult === "unauthorized") {
          stale.reject(new ApiError("http", "expired", 401));
          await stale.promise.catch(() => undefined);
        } else {
          stale.resolve({
            chapter_id: first.id,
            revision: 2,
            saved: true,
            frames: [],
            removed_asset_ids: [],
          });
          await stale.promise;
        }
      });

      expect(screen.getByRole("heading", { name: "当前粗剪 B" })).toBeInTheDocument();
      expect(screen.queryByText("已保存草稿的当前投影 · 版本 2")).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(getRoughCut).toHaveBeenCalledTimes(1);
    },
  );

  it("shows the current user's lock snapshot without requesting or releasing a lock", async () => {
    const current = chapter("chapter-own-lock", "本人锁快照", [], {
      lock: { locked: true, locked_by_username: "演示创作者", is_mine: true },
    });
    const { services, listStoryboardAssets } = makeServices([current]);
    renderBrowser(services);

    expect(await screen.findByText(/你在其他位置持有编辑锁/)).toBeInTheDocument();
    expect(screen.getByText(/这是读取时的状态快照，当前页面始终只读/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /接管编辑锁|释放编辑锁/ })).not.toBeInTheDocument();
    expect(listStoryboardAssets).not.toHaveBeenCalled();
  });

  it("shows local field fallbacks and renders HTML-looking content only as text", async () => {
    const current = chapter("chapter-copy", "文字章节", [
      { text: 17, original_text: { unsafe: true }, storyboard: [], character: ["id", 3], scene: ["scene"], prop: null },
      { text: "<script>window.bad = true</script>", original_text: null, preview: "https://remote.example.invalid/video" },
    ]);
    const { services } = makeServices([current]);
    const { container } = renderBrowser(services);

    expect(await screen.findByText("这条镜头文字暂时无法识别。")).toBeInTheDocument();
    expect(screen.getByText("原文字段暂时无法识别。")).toBeInTheDocument();
    expect(screen.getByText("<script>window.bad = true</script>")).toBeInTheDocument();
    expect(screen.getByText("已有预览")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getAllByText("已关联素材").map((label) => label.parentElement?.textContent))
      .toEqual(["已关联素材2 项", "已关联素材0 项"]);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText("https://remote.example.invalid/video")).not.toBeInTheDocument();
  });

  it("falls back after a broken local original while keeping frame text visible", async () => {
    const current = chapter("chapter-broken", "破图", [{ text: "文字仍然可读", storyboard: ["frame-broken"] }]);
    const { services } = makeServices([current], {
      [current.id]: [asset("frame-broken", current.id, "http://127.0.0.1:4175/media/broken.svg")],
    });
    renderBrowser(services);

    const image = await screen.findByAltText("镜头 1 原图");
    fireEvent.error(image);
    expect(await screen.findByText("原图暂时无法加载")).toBeInTheDocument();
    expect(screen.getByText("文字仍然可读")).toBeInTheDocument();
  });

  it("keeps frame text on asset errors and retries only the selected chapter assets", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-retry", "可重试", [{ text: "保留的文字", storyboard: ["frame-retry"] }]);
    let attempts = 0;
    const { services, listChapters, listStoryboardAssets } = makeServices([current]);
    listStoryboardAssets.mockImplementation(async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new ApiError("http", "server error", 500);
      }
      return [asset("frame-retry", current.id, "http://127.0.0.1:4175/media/retry.svg")];
    });
    renderBrowser(services);

    expect(await screen.findByRole("heading", { name: "暂时无法读取内容" })).toBeInTheDocument();
    expect(screen.getByText("保留的文字")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "重试读取原图" }));
    expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/retry.svg",
    );
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
  });

  it("places personal notes before frames and waits for an explicit reopen after replacing the asset snapshot", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-notes-retry", "记录随媒体快照关闭", [
      { text: "仍可阅读的镜头", storyboard: ["frame-notes-retry"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current]);
    let assetAttempts = 0;
    listStoryboardAssets.mockImplementation(async () => {
      assetAttempts += 1;
      if (assetAttempts === 1) {
        throw new ApiError("http", "server error", 500);
      }
      return [asset("frame-notes-retry", current.id, "http://127.0.0.1:4175/media/retry.svg")];
    });
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes").mockImplementation(async (chapterId) => (
      parsePersonalProductionSnapshot({
        chapter_id: chapterId,
        revision: 1,
        media_state: "ready",
        frames: [{
          frame_index: 0,
          storyboard_asset_id: "frame-notes-retry",
          media_revision: 1,
          source_valid: false,
          asset_image_digest: null,
          preview_digest: null,
          invalid_reason: null,
        }],
        frame_notes: { "frame-notes-retry": { status: "needs_revision", note: "记录文本可见" } },
        resume_frame_id: null,
      }, chapterId)
    ));
    const { container } = renderBrowser(services);

    expect(await screen.findByRole("heading", { name: "暂时无法读取内容" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    const panel = await screen.findByRole("region", { name: "我的制作记录" });
    expect(await screen.findByText("记录文本可见")).toBeInTheDocument();
    const storyboard = container.querySelector(".storyboard-list");
    expect(storyboard).not.toBeNull();
    expect(panel.compareDocumentPosition(storyboard!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(getNotes).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重试读取原图" }));
    expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/retry.svg",
    );
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("记录文本可见")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
  });

  it("locates only a current verified resume target without closing notes or adding media requests", async () => {
    const user = userEvent.setup();
    const imageUrl = "http://127.0.0.1:4175/media/resume-target.png";
    const imageDigest = await digestPersonalProductionMediaIdentity(imageUrl);
    if (imageDigest === null) {
      throw new Error("The local image identity should produce a digest.");
    }
    const current = chapter("chapter-resume-location", "续作定位", [
      { text: "尚未关联素材的镜头", storyboard: ["missing-frame"] },
      { text: "待重新确认的续作镜头", storyboard: ["frame-resume-target"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current], {
      [current.id]: [asset("frame-resume-target", current.id, imageUrl)],
    });
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes").mockResolvedValue(
      parsePersonalProductionSnapshot({
        chapter_id: current.id,
        revision: 4,
        media_state: "ready",
        frames: [
          {
            frame_index: 0,
            storyboard_asset_id: null,
            media_revision: null,
            source_valid: false,
            asset_image_digest: null,
            preview_digest: null,
            invalid_reason: "missing",
          },
          {
            frame_index: 1,
            storyboard_asset_id: "frame-resume-target",
            media_revision: 1,
            source_valid: true,
            asset_image_digest: imageDigest,
            preview_digest: null,
            invalid_reason: null,
          },
        ],
        frame_notes: {
          "frame-resume-target": { status: "needs_revision", note: "待修的续作记录" },
        },
        resume_frame_id: "frame-resume-target",
      }, current.id),
    );
    const roughCutRequest = vi.spyOn(services, "getPersonalRoughCut");
    const { container } = renderBrowser(services);

    await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("待修的续作记录")).toBeInTheDocument();
    const notesPanel = screen.getByRole("region", { name: "我的制作记录" });
    const target = screen.getByRole("article", { name: "续作定位 · 镜头 2" });
    expect(target).not.toHaveFocus();
    expect(target).not.toHaveClass("is-resume-target");
    const scrollIntoView = vi.fn();
    Object.defineProperty(target, "scrollIntoView", { configurable: true, value: scrollIntoView });
    const imageCountBeforeLocate = container.querySelectorAll("img").length;

    await user.click(screen.getByRole("button", { name: "定位到续作镜头" }));

    expect(target).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "center" });
    expect(target).toHaveAttribute("tabindex", "-1");
    expect(target).toHaveClass("is-resume-target");
    expect(screen.getByText("已定位到续作镜头 2。")).toBeInTheDocument();
    expect(notesPanel).toBeInTheDocument();
    expect(container.querySelectorAll("img")).toHaveLength(imageCountBeforeLocate);
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(roughCutRequest).not.toHaveBeenCalled();
    const oldCallbacks = {
      onLocateResume: personalNotesBridge.onLocateResume,
      onResumeInvalidated: personalNotesBridge.onResumeInvalidated,
    };
    const oldTarget = personalNotesBridge.lastTarget;
    if (oldCallbacks.onLocateResume === null || oldCallbacks.onResumeInvalidated === null || oldTarget === null) {
      throw new Error("The notes panel callbacks and resume target should have been captured.");
    }

    expect(getNotes).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "重新读取记录" }));
    expect(screen.queryByText("已定位到续作镜头 2。")).not.toBeInTheDocument();
    expect(target).not.toHaveClass("is-resume-target");
    expect(await screen.findByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();
    expect(target).not.toHaveFocus();
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(roughCutRequest).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "定位到续作镜头" }));
    expect(target).toHaveClass("is-resume-target");
    await user.click(screen.getByRole("button", { name: "关闭记录" }));
    expect(screen.queryByText("已定位到续作镜头 2。")).not.toBeInTheDocument();
    expect(target).not.toHaveClass("is-resume-target");

    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("待修的续作记录")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(3);
    await user.click(screen.getByRole("button", { name: "定位到续作镜头" }));
    expect(target).toHaveClass("is-resume-target");
    expect(oldTarget.isCurrent()).toBe(false);

    const chapterRefreshButton = screen.getAllByRole("button", { name: "重新读取章节" })[0];
    if (chapterRefreshButton === undefined) {
      throw new Error("The chapter refresh button should remain available.");
    }
    const oldLocate = oldCallbacks.onLocateResume;
    const oldInvalidate = oldCallbacks.onResumeInvalidated;
    if (oldLocate === null || oldInvalidate === null) {
      throw new Error("The old notes callbacks should remain available for the stale-callback check.");
    }
    chapterRefreshButton.focus();
    scrollIntoView.mockClear();
    await act(async () => {
      oldLocate(oldTarget);
      oldLocate({ position: oldTarget.position, isCurrent: () => true });
      oldInvalidate();
    });

    expect(chapterRefreshButton).toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(target).not.toHaveFocus();
    expect(target).toHaveClass("is-resume-target");
    expect(screen.getByText("已定位到续作镜头 2。")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(3);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(roughCutRequest).not.toHaveBeenCalled();
  });

  it("requires a visible, focused current notes frame and tombstones an invalidated read generation", async () => {
    const user = userEvent.setup();
    const imageUrl = "http://127.0.0.1:4175/media/note-frame.png";
    const imageDigest = await digestPersonalProductionMediaIdentity(imageUrl);
    if (imageDigest === null) {
      throw new Error("The local image identity should produce a digest.");
    }

    const current = chapter("chapter-note-frame-navigation", "记录定位章节", [
      { text: "已核对的制作记录镜头", storyboard: ["note-frame-id"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current], {
      [current.id]: [asset("note-frame-id", current.id, imageUrl)],
    });
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes").mockResolvedValue(
      parsePersonalProductionSnapshot({
        chapter_id: current.id,
        revision: 3,
        media_state: "ready",
        frames: [{
          frame_index: 0,
          storyboard_asset_id: "note-frame-id",
          media_revision: 1,
          source_valid: true,
          asset_image_digest: imageDigest,
          preview_digest: null,
          invalid_reason: null,
        }],
        frame_notes: { "note-frame-id": { status: "needs_revision", note: "当前可定位记录" } },
        resume_frame_id: null,
      }, current.id),
    );
    const onUnauthorized = vi.fn();
    const { container } = renderBrowser(services, vi.fn(), onUnauthorized);

    await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("当前可定位记录")).toBeInTheDocument();
    const locateButton = screen.getByRole("button", { name: "定位到记录镜头 1" });
    const targetElement = screen.getByRole("article", { name: "记录定位章节 · 镜头 1" });
    const scrollIntoView = vi.fn();
    Object.defineProperty(targetElement, "scrollIntoView", { configurable: true, value: scrollIntoView });

    targetElement.style.display = "none";
    await user.click(locateButton);
    expect(targetElement).not.toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(await screen.findByText("未能定位当前记录镜头，请确认镜头仍可见后重试。")).toBeInTheDocument();

    targetElement.style.display = "";
    const targetParent = targetElement.parentElement;
    if (targetParent === null) {
      throw new Error("The current chapter article should have a parent before detaching it.");
    }
    const nextSibling = targetElement.nextSibling;
    targetElement.remove();
    await user.click(locateButton);
    expect(targetElement.isConnected).toBe(false);
    expect(targetElement).not.toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(screen.getByText("未能定位当前记录镜头，请确认镜头仍可见后重试。")).toBeInTheDocument();
    targetParent.insertBefore(targetElement, nextSibling);
    expect(targetElement.isConnected).toBe(true);

    const originalFocus = targetElement.focus.bind(targetElement);
    const noOpFocus = vi.fn();
    Object.defineProperty(targetElement, "focus", { configurable: true, value: noOpFocus });
    await user.click(locateButton);
    expect(noOpFocus).toHaveBeenCalledTimes(1);
    expect(targetElement).not.toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(screen.getByText("未能定位当前记录镜头，请确认镜头仍可见后重试。")).toBeInTheDocument();

    const throwingFocus = vi.fn(() => { throw new Error("Focus failed"); });
    Object.defineProperty(targetElement, "focus", { configurable: true, value: throwingFocus });
    await user.click(locateButton);
    expect(throwingFocus).toHaveBeenCalledTimes(1);
    expect(targetElement).not.toHaveFocus();
    expect(scrollIntoView).not.toHaveBeenCalled();
    expect(screen.getByText("未能定位当前记录镜头，请确认镜头仍可见后重试。")).toBeInTheDocument();
    Object.defineProperty(targetElement, "focus", { configurable: true, value: originalFocus });

    await user.click(locateButton);
    expect(targetElement).toHaveFocus();
    expect(targetElement).toHaveClass("is-personal-note-frame-target");
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "center" });
    expect(screen.getByText("已定位到记录镜头 1。")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(container.querySelectorAll("img")).toHaveLength(1);

    const innerControl = screen.getByRole("button", { name: "查看镜头 1 的关联素材" });
    await act(async () => {
      innerControl.focus();
    });
    expect(innerControl).toHaveFocus();
    expect(screen.queryByText("已定位到记录镜头 1。")).not.toBeInTheDocument();
    expect(targetElement).not.toHaveClass("is-personal-note-frame-target");
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    const target = personalNotesBridge.lastProductionNoteTarget;
    const invalidate = personalNotesBridge.onFrameReadInvalidated;
    const register = personalNotesBridge.onRegisterFrameRead;
    const locate = personalNotesBridge.onLocateProductionNoteFrame;
    if (target === null || invalidate === null || register === null || locate === null) {
      throw new Error("The current read identity and parent callbacks should be available.");
    }
    expect(target.isCurrent()).toBe(true);
    let staleLocateResult = true;
    await act(async () => {
      invalidate(target.readIdentity);
      register(target.readIdentity);
      staleLocateResult = (await locate(target)) === true;
    });
    expect(staleLocateResult).toBe(false);
    expect(target.isCurrent()).toBe(true);
    expect(screen.queryByText("已定位到记录镜头 1。")).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("rejects an old row locate, close, and invalidation after a same-owner reread", async () => {
    const user = userEvent.setup();
    const imageUrl = "http://127.0.0.1:4175/media/note-reread.png";
    const imageDigest = await digestPersonalProductionMediaIdentity(imageUrl);
    if (imageDigest === null) {
      throw new Error("The local image identity should produce a digest.");
    }

    const current = chapter("chapter-note-reread", "同一笔记 owner", [
      { text: "同一可定位镜头", storyboard: ["note-reread-id"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current], {
      [current.id]: [asset("note-reread-id", current.id, imageUrl)],
    });
    const makeNotes = (note: string) => parsePersonalProductionSnapshot({
      chapter_id: current.id,
      revision: note === "R1" ? 1 : 2,
      media_state: "ready",
      frames: [{
        frame_index: 0,
        storyboard_asset_id: "note-reread-id",
        media_revision: 1,
        source_valid: true,
        asset_image_digest: imageDigest,
        preview_digest: null,
        invalid_reason: null,
      }],
      frame_notes: { "note-reread-id": { status: "needs_revision", note } },
      resume_frame_id: null,
    }, current.id);
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes")
      .mockResolvedValueOnce(makeNotes("R1"))
      .mockResolvedValueOnce(makeNotes("R2"));
    const unauthorized = vi.fn();
    renderBrowser(services, vi.fn(), unauthorized);

    await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("R1")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "定位到记录镜头 1" }));
    const oldTarget = personalNotesBridge.lastProductionNoteTarget;
    const oldClose = personalNotesBridge.onClose;
    const oldInvalidate = personalNotesBridge.onFrameReadInvalidated;
    const oldLocate = personalNotesBridge.onLocateProductionNoteFrame;
    if (oldTarget === null || oldClose === null || oldInvalidate === null || oldLocate === null) {
      throw new Error("The first row target and parent callbacks should be captured.");
    }

    await user.click(screen.getByRole("button", { name: "重新读取记录" }));
    expect(await screen.findByText("R2")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(oldTarget.isCurrent()).toBe(false);
    const article = screen.getByRole("article", { name: "同一笔记 owner · 镜头 1" });
    const scrollIntoView = vi.fn();
    Object.defineProperty(article, "scrollIntoView", { configurable: true, value: scrollIntoView });

    await user.click(screen.getByRole("button", { name: "定位到记录镜头 1" }));
    expect(article).toHaveFocus();
    expect(screen.getByText("已定位到记录镜头 1。")).toBeInTheDocument();
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    const currentScrollCount = scrollIntoView.mock.calls.length;
    let staleLocateResult = true;
    await act(async () => {
      oldClose(oldTarget.readIdentity);
      oldInvalidate(oldTarget.readIdentity);
      staleLocateResult = (await oldLocate(oldTarget)) === true;
    });
    expect(staleLocateResult).toBe(false);
    expect(screen.getByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
    expect(screen.getByText("R2")).toBeInTheDocument();
    expect(screen.getByText("已定位到记录镜头 1。")).toBeInTheDocument();
    expect(article).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledTimes(currentScrollCount);
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("rejects old row callbacks after closing and reopening a fresh notes owner", async () => {
    const user = userEvent.setup();
    const imageUrl = "http://127.0.0.1:4175/media/note-owner-reopen.png";
    const imageDigest = await digestPersonalProductionMediaIdentity(imageUrl);
    if (imageDigest === null) {
      throw new Error("The local image identity should produce a digest.");
    }

    const current = chapter("chapter-note-owner-reopen", "重新打开记录章节", [
      { text: "第一条已核对记录", storyboard: ["note-owner-reopen-id"] },
    ]);
    const { services, listStoryboardAssets } = makeServices([current], {
      [current.id]: [asset("note-owner-reopen-id", current.id, imageUrl)],
    });
    const makeNotes = (revision: number, note: string) => parsePersonalProductionSnapshot({
      chapter_id: current.id,
      revision,
      media_state: "ready",
      frames: [{
        frame_index: 0,
        storyboard_asset_id: "note-owner-reopen-id",
        media_revision: 1,
        source_valid: true,
        asset_image_digest: imageDigest,
        preview_digest: null,
        invalid_reason: null,
      }],
      frame_notes: { "note-owner-reopen-id": { status: "needs_revision", note } },
      resume_frame_id: null,
    }, current.id);
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes")
      .mockResolvedValueOnce(makeNotes(1, "旧 owner 记录"))
      .mockResolvedValueOnce(makeNotes(2, "新 owner 记录"));
    const unauthorized = vi.fn();
    renderBrowser(services, vi.fn(), unauthorized);

    await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("旧 owner 记录")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "定位到记录镜头 1" }));
    const oldTarget = personalNotesBridge.lastProductionNoteTarget;
    const oldCallbacks = {
      register: personalNotesBridge.onRegisterFrameRead,
      invalidate: personalNotesBridge.onFrameReadInvalidated,
      locate: personalNotesBridge.onLocateProductionNoteFrame,
      close: personalNotesBridge.onClose,
    };
    const oldRegister = oldCallbacks.register;
    const oldInvalidate = oldCallbacks.invalidate;
    const oldLocate = oldCallbacks.locate;
    const oldClose = oldCallbacks.close;
    if (
      oldTarget === null
      || oldRegister === null
      || oldInvalidate === null
      || oldLocate === null
      || oldClose === null
    ) {
      throw new Error("The old panel row target and callbacks should be captured.");
    }
    const oldReadIdentity = oldTarget.readIdentity;
    const article = screen.getByRole("article", { name: "重新打开记录章节 · 镜头 1" });
    const scrollIntoView = vi.fn();
    Object.defineProperty(article, "scrollIntoView", { configurable: true, value: scrollIntoView });
    expect(article).toHaveFocus();
    expect(screen.getByText("已定位到记录镜头 1。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "关闭记录" }));
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(oldTarget.isCurrent()).toBe(false);
    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("新 owner 记录")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
    scrollIntoView.mockClear();
    await user.click(screen.getByRole("button", { name: "定位到记录镜头 1" }));
    expect(article).toHaveFocus();
    expect(screen.getByText("已定位到记录镜头 1。")).toBeInTheDocument();
    const currentScrollCalls = scrollIntoView.mock.calls.length;

    const staleTarget = { ...oldTarget, isCurrent: () => true };
    let staleLocateResult = true;
    await act(async () => {
      oldRegister(oldReadIdentity);
      staleLocateResult = (await oldLocate(staleTarget)) === true;
      oldClose(oldReadIdentity);
      oldInvalidate(oldReadIdentity);
    });
    expect(staleLocateResult).toBe(false);
    expect(screen.getByRole("region", { name: "我的制作记录" })).toBeInTheDocument();
    expect(screen.getByText("新 owner 记录")).toBeInTheDocument();
    expect(screen.getByText("已定位到记录镜头 1。")).toBeInTheDocument();
    expect(article).toHaveFocus();
    expect(scrollIntoView).toHaveBeenCalledTimes(currentScrollCalls);
    expect(getNotes).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it.each(["success", "401"] as const)(
    "permanently closes a notes owner across user A→B→A before its old %s settles",
    async (lateResult) => {
      const user = userEvent.setup();
      const current = chapter("chapter-resume-scope", "同一章节对象", []);
      const pending = deferred<PersonalProductionSnapshot>();
      const { services, listChapters } = makeServices([current]);
      const getNotes = vi.spyOn(services, "getPersonalProductionNotes")
        .mockReturnValueOnce(pending.promise)
        .mockResolvedValueOnce(emptyPersonalNotesSnapshot(current.id, "显式重开后的记录"));
      const onUnauthorized = vi.fn();
      const view = render(
        <ChapterBrowser
          onBack={vi.fn()}
          onUnauthorized={onUnauthorized}
          series={series}
          services={services}
          userId="scope-user-a"
        />,
      );

      expect(await screen.findByRole("heading", { name: current.title })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
      await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(1));
      expect(screen.getByText("正在读取个人制作记录…")).toBeInTheDocument();

      view.rerender(
        <ChapterBrowser
          onBack={vi.fn()}
          onUnauthorized={onUnauthorized}
          series={series}
          services={services}
          userId="scope-user-b"
        />,
      );
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
      expect(await screen.findByRole("heading", { name: current.title })).toBeInTheDocument();

      view.rerender(
        <ChapterBrowser
          onBack={vi.fn()}
          onUnauthorized={onUnauthorized}
          series={series}
          services={services}
          userId="scope-user-a"
        />,
      );
      expect(await screen.findByRole("heading", { name: current.title })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(1);

      await act(async () => {
        if (lateResult === "success") {
          pending.resolve(emptyPersonalNotesSnapshot(current.id, "旧scope迟到记录"));
          await pending.promise;
        } else {
          pending.reject(new ApiError("http", "expired", 401));
          await pending.promise.catch(() => undefined);
        }
      });
      expect(screen.queryByText("旧scope迟到记录")).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(getNotes).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
      expect(await screen.findByText("显式重开后的记录")).toBeInTheDocument();
      expect(getNotes).toHaveBeenCalledTimes(2);
    },
  );

  it("permanently closes notes when the services object changes and later returns", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-services-scope", "服务上下文章节", []);
    const pending = deferred<PersonalProductionSnapshot>();
    const { services, listChapters } = makeServices([current]);
    const getNotes = vi.spyOn(services, "getPersonalProductionNotes")
      .mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(emptyPersonalNotesSnapshot(current.id, "新服务下显式读取的记录"));
    const replacementListChapters = vi.fn(async () => [current]);
    const replacementServices: WorkspaceServices = {
      ...services,
      listChapters: replacementListChapters,
    };
    const onUnauthorized = vi.fn();
    const view = renderBrowser(services, vi.fn(), onUnauthorized);

    expect(await screen.findByRole("heading", { name: current.title })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(1));

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        series={series}
        services={replacementServices}
        userId="demo-user"
      />,
    );
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    await waitFor(() => expect(replacementListChapters).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("heading", { name: current.title })).toBeInTheDocument();

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        series={series}
        services={services}
        userId="demo-user"
      />,
    );
    expect(await screen.findByRole("heading", { name: current.title })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(1);

    await act(async () => {
      pending.resolve(emptyPersonalNotesSnapshot(current.id, "旧服务迟到记录"));
      await pending.promise;
    });
    expect(screen.queryByText("旧服务迟到记录")).not.toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(getNotes).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("新服务下显式读取的记录")).toBeInTheDocument();
    expect(getNotes).toHaveBeenCalledTimes(2);
  });

  it.each(["switch", "refresh"] as const)(
    "closes the pending private request on chapter %s and ignores its late 401",
    async (action) => {
      const user = userEvent.setup();
      const first = chapter("chapter-notes-pending-a", "待关闭的章节 A", []);
      const second = chapter("chapter-notes-pending-b", "章节 B", []);
      const pending = deferred<PersonalProductionSnapshot>();
      const { services, listChapters } = makeServices([first, second]);
      const getNotes = vi.spyOn(services, "getPersonalProductionNotes").mockReturnValue(pending.promise);
      const onUnauthorized = vi.fn();
      renderBrowser(services, vi.fn(), onUnauthorized);

      expect(await screen.findByRole("heading", { name: "待关闭的章节 A" })).toBeInTheDocument();
      await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
      await waitFor(() => expect(getNotes).toHaveBeenCalledTimes(1));

      if (action === "switch") {
        await user.click(screen.getByRole("button", { name: /章节 B/ }));
        expect(await screen.findByRole("heading", { name: "章节 B" })).toBeInTheDocument();
      } else {
        await user.click(screen.getByRole("button", { name: "重新读取章节" }));
        expect(await screen.findByRole("heading", { name: "待关闭的章节 A" })).toBeInTheDocument();
        await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
      }

      await act(async () => {
        pending.reject(new ApiError("http", "expired", 401));
        await pending.promise.catch(() => undefined);
      });
      expect(screen.queryByRole("region", { name: "我的制作记录" })).not.toBeInTheDocument();
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(getNotes).toHaveBeenCalledTimes(1);
    },
  );

  it("does not show old chapter images after switching to a chapter whose assets fail", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-with-image", "有图章节", [{ text: "第一章文字", storyboard: ["frame-old"] }]);
    const second = chapter("chapter-with-error", "失败章节", [{ text: "当前章节文字", storyboard: ["frame-new"] }]);
    const { services } = makeServices([first, second], {
      [first.id]: [asset("frame-old", first.id, "http://127.0.0.1:4175/media/old.svg")],
    });
    const listStoryboardAssets = vi.spyOn(services, "listStoryboardAssets")
      .mockImplementation(async (_seriesId, chapterId) => {
        if (chapterId === second.id) {
          throw new ApiError("timeout", "timed out");
        }
        return [asset("frame-old", first.id, "http://127.0.0.1:4175/media/old.svg")];
      });
    renderBrowser(services);

    expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute("src", "http://127.0.0.1:4175/media/old.svg");
    await user.click(screen.getByRole("button", { name: /失败章节/ }));
    expect(await screen.findByText("当前章节文字")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "请求超时" })).toBeInTheDocument();
    expect(screen.queryByAltText("镜头 1 原图")).not.toBeInTheDocument();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
  });

  it("ignores an abort-insensitive late asset 401 after switching chapters and does not cache it", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-slow", "慢章节", [{ text: "慢章节镜头", storyboard: ["frame-slow"] }]);
    const second = chapter("chapter-fast", "快章节", [{ text: "快章节镜头", storyboard: ["frame-fast"] }]);
    const lateFirst = deferred<StoryboardAsset[]>();
    const returnedFirst = deferred<StoryboardAsset[]>();
    let firstAttempts = 0;
    const { services, listStoryboardAssets } = makeServices([first, second]);
    listStoryboardAssets.mockImplementation((_seriesId, chapterId) => {
      if (chapterId === first.id) {
        firstAttempts += 1;
        return firstAttempts === 1
          ? lateFirst.promise
          : returnedFirst.promise;
      }
      return Promise.resolve([asset("frame-fast", second.id)]);
    });
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);
    expect(await screen.findByText("慢章节镜头")).toBeInTheDocument();
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: /快章节/ }));
    expect(await screen.findByText("快章节镜头")).toBeInTheDocument();
    await act(async () => {
      lateFirst.reject(new ApiError("http", "expired", 401));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.getByText("快章节镜头")).toBeInTheDocument());
    expect(onUnauthorized).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /慢章节/ }));
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(3));
    await act(async () => {
      returnedFirst.resolve([asset("frame-slow", first.id)]);
      await returnedFirst.promise;
    });
    expect(await screen.findByText("慢章节镜头")).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("does not cache an abort-insensitive late success from chapter A after switching to B", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-a-late", "章节 A", [{ text: "章节 A 正文", storyboard: ["frame-a"] }]);
    const second = chapter("chapter-b-late", "章节 B", [{ text: "章节 B 正文", storyboard: ["frame-b"] }]);
    const staleA = deferred<StoryboardAsset[]>();
    const freshA = deferred<StoryboardAsset[]>();
    let firstAttempts = 0;
    const { services, listStoryboardAssets } = makeServices([first, second]);
    listStoryboardAssets.mockImplementation((_seriesId, chapterId) => {
      if (chapterId === first.id) {
        firstAttempts += 1;
        return firstAttempts === 1
          ? staleA.promise
          : freshA.promise;
      }
      return Promise.resolve([asset("frame-b", second.id)]);
    });
    renderBrowser(services);
    expect(await screen.findByText("章节 A 正文")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /章节 B/ }));
    expect(await screen.findByText("章节 B 正文")).toBeInTheDocument();
    await act(async () => {
      staleA.resolve([asset("frame-stale-a", first.id, "http://127.0.0.1:4175/media/stale.svg")]);
      await staleA.promise;
    });

    await user.click(screen.getByRole("button", { name: /章节 A/ }));
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(3));
    await act(async () => {
      freshA.resolve([asset("frame-a", first.id, "http://127.0.0.1:4175/media/fresh.svg")]);
      await freshA.promise;
    });
    expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/fresh.svg",
    );
    expect(screen.queryByAltText("镜头 1 原图")).not.toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/stale.svg",
    );
  });

  it("ignores a late asset 401 after re-reading chapters and shows only the refreshed response", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-refresh", "刷新后章节", [{ text: "刷新后的正文", storyboard: ["frame-refresh"] }]);
    const staleAssets = deferred<StoryboardAsset[]>();
    const refreshedAssets = deferred<StoryboardAsset[]>();
    let assetAttempts = 0;
    const { services, listChapters, listStoryboardAssets } = makeServices([current]);
    listStoryboardAssets.mockImplementation(() => {
      assetAttempts += 1;
      return assetAttempts === 1 ? staleAssets.promise : refreshedAssets.promise;
    });
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);
    expect(await screen.findByText("刷新后的正文")).toBeInTheDocument();
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));

    await user.click(screen.getByRole("button", { name: "重新读取章节" }));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(2));
    await act(async () => {
      staleAssets.reject(new ApiError("http", "expired", 401));
      await Promise.resolve();
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByText("刷新后的正文")).toBeInTheDocument();

    await act(async () => {
      refreshedAssets.resolve([asset("frame-refresh", current.id, "http://127.0.0.1:4175/media/refreshed.svg")]);
      await refreshedAssets.promise;
    });
    expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute(
      "src",
      "http://127.0.0.1:4175/media/refreshed.svg",
    );
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("clears the session for a current storyboard asset 401", async () => {
    const current = chapter("chapter-asset-401", "图片会话失效", [{ text: "仍需隐藏的正文", storyboard: ["frame-401"] }]);
    const { services } = makeServices([current]);
    vi.spyOn(services, "listStoryboardAssets")
      .mockRejectedValue(new ApiError("http", "expired", 401));
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(screen.getByText("仍需隐藏的正文")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试读取原图" })).not.toBeInTheDocument();
  });

  it.each([
    [new ApiError("http", "forbidden", 403, "没有访问权限"), "没有访问权限"],
    [new ApiError("http", "not found", 404, "内容不存在"), "内容不存在"],
    [new ApiError("http", "unprocessable", 422, "请求有误"), "请求未通过校验"],
    [new ApiError("http", "server error", 500), "暂时无法读取内容"],
    [new ApiError("timeout", "timeout"), "请求超时"],
    [new ApiError("network", "offline"), "无法连接本地服务"],
    [new ApiError("invalid-response", "malformed"), "服务返回的数据无法识别"],
  ])("keeps the workspace and allows retry for chapter error %s", async (failure, heading) => {
    const user = userEvent.setup();
    const current = chapter("chapter-recover", "恢复章节", []);
    let attempts = 0;
    const { services, listChapters } = makeServices([current]);
    listChapters.mockImplementation(async () => {
      attempts += 1;
      if (attempts === 1) {
        throw failure;
      }
      return [current];
    });
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);

    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "返回剧集列表" })).toHaveLength(2);
    await user.click(screen.getAllByRole("button", { name: "重试读取" })[0]!);
    expect(await screen.findByRole("heading", { name: "恢复章节" })).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(listChapters).toHaveBeenCalledTimes(2);
  });

  it("clears the session only when the current chapter request returns 401", async () => {
    const current = chapter("chapter-401", "失效会话", []);
    const { services } = makeServices([current]);
    vi.spyOn(services, "listChapters").mockRejectedValue(new ApiError("http", "expired", 401));
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("button", { name: "重试读取" })).not.toBeInTheDocument();
  });

  it("does not offer chapter management when the series is read-only", async () => {
    const readOnlySeries = { ...series, can_enter: false };
    const { services } = makeServices([chapter("chapter-read-only", "只读章节", [])], {}, true);
    renderBrowser(services, vi.fn(), vi.fn(), readOnlySeries);

    await screen.findByRole("heading", { level: 2, name: "只读章节" });
    expect(screen.queryByRole("button", { name: "新建章节" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修改名称" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "删除章节" })).not.toBeInTheDocument();
  });

  it("opens the storyboard generation dialog through the chapter browser capability", async () => {
    const user = userEvent.setup();
    const base = makeServices([], {}, true);
    const generation: StoryboardGenerationServicePort = {
      listModels: vi.fn(async () => []),
      listPrompts: vi.fn(async () => []),
      quote: vi.fn(async () => { throw new Error("Not used by this integration test."); }),
      stream: vi.fn(async function* () {}),
      getSubmission: vi.fn(async () => { throw new Error("Not used by this integration test."); }),
      getTask: vi.fn(async () => { throw new Error("Not used by this integration test."); }),
    };
    const services: WorkspaceServices = { ...base.services, storyboardGeneration: generation };
    renderBrowser(services);

    await user.click(await screen.findByRole("button", { name: "AI 生成分镜" }));

    expect(await screen.findByRole("dialog", { name: "AI 生成分镜" })).toBeInTheDocument();
    expect(generation.listModels).toHaveBeenCalledWith(expect.any(AbortSignal));
    expect(generation.listPrompts).toHaveBeenCalledWith(expect.any(AbortSignal));
  });

  it("creates a blank chapter with the legacy payload and selects it after a fresh list read", async () => {
    const user = userEvent.setup();
    const { services, createChapter, listChapters } = makeServices([], {}, true);
    renderBrowser(services);

    await user.click(await screen.findByRole("button", { name: "新建章节" }));
    await user.type(screen.getByRole("textbox", { name: "章节名称" }), "  新章节  ");
    await user.click(screen.getByRole("button", { name: "创建章节" }));

    expect(createChapter).toHaveBeenCalledWith(
      series.id,
      { title: "新章节", content: [{ text: "" }], order: 0 },
      expect.any(AbortSignal),
    );
    expect(await screen.findByRole("heading", { level: 2, name: "新章节" })).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("《新章节》已创建")).toBeInTheDocument();
  });

  it("updates only the title and preserves the selected chapter after a fresh list read", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-rename", "旧名称", []);
    const { services, updateChapter, listChapters } = makeServices([current], {}, true);
    renderBrowser(services);

    expect(await screen.findByRole("heading", { level: 2, name: "旧名称" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "修改名称" }));
    const input = screen.getByRole("textbox", { name: "章节名称" });
    await user.clear(input);
    await user.type(input, "更新名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));

    expect(updateChapter).toHaveBeenCalledWith(
      series.id,
      current.id,
      { title: "更新名称" },
      expect.any(AbortSignal),
    );
    expect(await screen.findByRole("heading", { level: 2, name: "更新名称" })).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
  });

  it("confirms deletion and selects the next remaining chapter from the refreshed directory", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-delete-first", "将删除的章节", []);
    const second = chapter("chapter-delete-second", "保留章节", []);
    const { services, deleteChapter, listChapters } = makeServices([first, second], {}, true);
    renderBrowser(services);

    expect(await screen.findByRole("heading", { level: 2, name: "将删除的章节" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "删除章节" }));
    expect(screen.getByRole("dialog").querySelector(".chapter-delete-copy")).toHaveTextContent(
      "确认删除《将删除的章节》？此操作无法撤销，章节中的分镜、聊天等相关数据也会一并删除。",
    );
    await user.click(screen.getByRole("checkbox", { name: "我已核对章节名称，并确认删除。" }));
    await user.click(screen.getByRole("button", { name: "确认删除章节" }));

    expect(deleteChapter).toHaveBeenCalledWith(series.id, first.id, expect.any(AbortSignal));
    expect(await screen.findByRole("heading", { level: 2, name: "保留章节" })).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
  });

  it.each(["   ", "legacy ".repeat(16)])("does not rewrite an untouched %s", async (legacyTitle) => {
    const user = userEvent.setup();
    const current = chapter("chapter-legacy-title", legacyTitle, []);
    const { services, updateChapter } = makeServices([current], {}, true);
    renderBrowser(services);

    await screen.findByRole("button", { name: "修改名称" });
    await user.click(screen.getByRole("button", { name: "修改名称" }));
    expect(screen.getByRole("textbox", { name: "章节名称" })).toHaveValue(legacyTitle);
    await user.click(screen.getByRole("button", { name: "保存名称" }));

    expect(updateChapter).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not apply a stale verification or 401 to a changed chapter draft", async () => {
    const user = userEvent.setup();
    const pendingRead = deferred<Chapter[]>();
    const { services, listChapters, createChapter } = makeServices([], {}, true);
    listChapters.mockImplementation(async () => (
      listChapters.mock.calls.length === 1 ? [] : pendingRead.promise
    ));
    createChapter.mockRejectedValueOnce(new ApiError("network", "连接中断"));
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);

    await user.click(await screen.findByRole("button", { name: "新建章节" }));
    const input = screen.getByRole("textbox", { name: "章节名称" });
    await user.type(input, "初始名称");
    await user.click(screen.getByRole("button", { name: "创建章节" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本次请求结果未确认");

    await user.click(screen.getByRole("button", { name: "重新读取章节列表核对" }));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    await user.clear(input);
    await user.type(input, "新草稿名称");
    await act(async () => {
      pendingRead.reject(new ApiError("http", "expired", 401));
      await pendingRead.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(createChapter).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox", { name: "章节名称" })).toHaveValue("新草稿名称");
    expect(screen.getByRole("button", { name: "创建章节" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "重新读取章节列表核对" })).toBeEnabled();
    expect(within(screen.getByRole("dialog")).queryByRole("status")).not.toBeInTheDocument();
  });

  it("keeps an unknown rename retry tied to the freshly read chapter", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-retry-rename", "原章节名", []);
    const { services, updateChapter, listChapters } = makeServices([current], {}, true);
    const readCurrentDirectory = listChapters.getMockImplementation();
    if (readCurrentDirectory === undefined) throw new Error("Chapter directory fixture is unavailable.");
    listChapters.mockImplementation(async () => (await readCurrentDirectory()).map((row) => ({ ...row })));
    updateChapter.mockRejectedValueOnce(new ApiError("http", "server error", 500));
    renderBrowser(services);

    await screen.findByRole("heading", { level: 2, name: "原章节名" });
    await user.click(screen.getByRole("button", { name: "修改名称" }));
    const input = screen.getByRole("textbox", { name: "章节名称" });
    await user.clear(input);
    await user.type(input, "修改后的名称");
    await user.click(screen.getByRole("button", { name: "保存名称" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本次请求结果未确认");
    expect(updateChapter).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取章节列表核对" }));
    expect(await within(screen.getByRole("dialog")).findByRole("status"))
      .toHaveTextContent("最新列表仍包含《原章节名》");
    await user.click(screen.getByRole("checkbox", { name: /我已重新核对列表/ }));
    await user.click(screen.getByRole("button", { name: "保存名称" }));

    expect(updateChapter).toHaveBeenCalledTimes(2);
    expect(updateChapter).toHaveBeenLastCalledWith(
      series.id,
      current.id,
      { title: "修改后的名称" },
      expect.any(AbortSignal),
    );
    expect(await screen.findByRole("heading", { level: 2, name: "修改后的名称" })).toBeInTheDocument();
  });

  it("keeps an unknown delete retry tied to the freshly read chapter", async () => {
    const user = userEvent.setup();
    const target = chapter("chapter-retry-delete", "待删除章节", []);
    const remaining = chapter("chapter-retry-delete-remaining", "保留章节", []);
    const { services, deleteChapter, listChapters } = makeServices([target, remaining], {}, true);
    const readCurrentDirectory = listChapters.getMockImplementation();
    if (readCurrentDirectory === undefined) throw new Error("Chapter directory fixture is unavailable.");
    listChapters.mockImplementation(async () => (await readCurrentDirectory()).map((row) => ({ ...row })));
    deleteChapter.mockRejectedValueOnce(new ApiError("http", "server error", 500));
    renderBrowser(services);

    await screen.findByRole("heading", { level: 2, name: "待删除章节" });
    await user.click(screen.getByRole("button", { name: "删除章节" }));
    await user.click(screen.getByRole("checkbox", { name: "我已核对章节名称，并确认删除。" }));
    await user.click(screen.getByRole("button", { name: "确认删除章节" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本次请求结果未确认");
    expect(deleteChapter).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "重新读取章节列表核对" }));
    expect(await within(screen.getByRole("dialog")).findByRole("status"))
      .toHaveTextContent("最新列表仍包含《待删除章节》");
    await user.click(screen.getByRole("checkbox", { name: /我已重新核对列表/ }));
    await user.click(screen.getByRole("button", { name: "确认删除章节" }));

    expect(deleteChapter).toHaveBeenCalledTimes(2);
    expect(deleteChapter).toHaveBeenLastCalledWith(series.id, target.id, expect.any(AbortSignal));
    expect(await screen.findByRole("heading", { level: 2, name: "保留章节" })).toBeInTheDocument();
  });

  it("explains when the freshly read directory no longer contains the delete target", async () => {
    const user = userEvent.setup();
    const target = chapter("chapter-missing-after-delete", "待核对删除", []);
    const { services, deleteChapter, listChapters } = makeServices([target], {}, true);
    listChapters.mockImplementationOnce(async () => [target]).mockImplementationOnce(async () => []);
    deleteChapter.mockRejectedValueOnce(new ApiError("http", "server error", 500));
    renderBrowser(services);

    await screen.findByRole("heading", { level: 2, name: "待核对删除" });
    await user.click(screen.getByRole("button", { name: "删除章节" }));
    await user.click(screen.getByRole("checkbox", { name: "我已核对章节名称，并确认删除。" }));
    await user.click(screen.getByRole("button", { name: "确认删除章节" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本次请求结果未确认");

    await user.click(screen.getByRole("button", { name: "重新读取章节列表核对" }));
    expect(await within(screen.getByRole("dialog")).findByRole("status"))
      .toHaveTextContent("已不在最新列表中；无法确认原请求结果，也不能再次提交");

    expect(deleteChapter).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("checkbox", { name: /我已重新核对列表/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认删除章节" })).toBeDisabled();
  });

  it("ignores a late chapter mutation 401 after switching to another series", async () => {
    const user = userEvent.setup();
    const pendingCreate = deferred<Chapter>();
    const { services, createChapter } = makeServices([], {}, true);
    createChapter.mockReturnValueOnce(pendingCreate.promise);
    const onUnauthorized = vi.fn();
    const view = renderBrowser(services, vi.fn(), onUnauthorized);

    await user.click(await screen.findByRole("button", { name: "新建章节" }));
    await user.type(screen.getByRole("textbox", { name: "章节名称" }), "旧剧集的新章");
    await user.click(screen.getByRole("button", { name: "创建章节" }));
    await waitFor(() => expect(createChapter).toHaveBeenCalledTimes(1));

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        series={{ ...series, id: "another-series" }}
        services={services}
        userId="demo-user"
      />,
    );
    await act(async () => {
      pendingCreate.reject(new ApiError("http", "expired", 401));
      await pendingCreate.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(createChapter).toHaveBeenCalledTimes(1);
  });

  it("requires a fresh directory read and a separate user confirmation before retrying an unknown create", async () => {
    const user = userEvent.setup();
    const { services, createChapter } = makeServices([], {}, true);
    createChapter.mockRejectedValueOnce(new ApiError("network", "连接中断"));
    renderBrowser(services);

    await user.click(await screen.findByRole("button", { name: "新建章节" }));
    await user.type(screen.getByRole("textbox", { name: "章节名称" }), "待核对章节");
    await user.click(screen.getByRole("button", { name: "创建章节" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本次请求结果未确认");
    expect(screen.getByRole("button", { name: "创建章节" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "重新读取章节列表核对" }));
    expect(await within(screen.getByRole("dialog")).findByRole("status"))
      .toHaveTextContent("名称相同也不能证明是本次创建");
    expect(await within(screen.getByRole("dialog")).findByRole("region", { name: "最新读取的章节目录" }))
      .toHaveTextContent("最新列表暂无章节。");
    await user.click(screen.getByRole("checkbox", { name: /我已重新核对列表/ }));
    await user.click(screen.getByRole("button", { name: "创建章节" }));

    expect(createChapter).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("heading", { level: 2, name: "待核对章节" })).toBeInTheDocument();
  });

  it("UCHAP-01 revokes create retry confirmation after a later verification fails", async () => {
    const user = userEvent.setup();
    const sameName = chapter("chapter-same-name", "待核对章节", [], {
      created_at: "2026-10-04T08:00:00Z",
    });
    const otherName = chapter("chapter-other-name", "另一个已存在章节", [], {
      created_at: "2026-10-03T08:00:00Z",
    });
    const { services, listChapters, createChapter } = makeServices([], {}, true);
    listChapters
      .mockImplementationOnce(async () => [])
      .mockImplementationOnce(async () => [sameName, otherName])
      .mockRejectedValueOnce(new ApiError("network", "目录读取失败"))
      .mockImplementationOnce(async () => [sameName, otherName]);
    createChapter.mockRejectedValueOnce(new ApiError("network", "创建结果未知"));
    renderBrowser(services);

    await user.click(await screen.findByRole("button", { name: "新建章节" }));
    await user.type(screen.getByRole("textbox", { name: "章节名称" }), "待核对章节");
    await user.click(screen.getByRole("button", { name: "创建章节" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本次请求结果未确认");

    await user.click(screen.getByRole("button", { name: "重新读取章节列表核对" }));
    const dialog = within(screen.getByRole("dialog"));
    const firstDirectory = await dialog.findByRole("region", { name: "最新读取的章节目录" });
    expect(within(firstDirectory).getByText("待核对章节")).toBeInTheDocument();
    expect(within(firstDirectory).getByText("另一个已存在章节")).toBeInTheDocument();
    expect(firstDirectory).toHaveTextContent("创建于 2026-10-04T08:00:00Z");
    expect(firstDirectory).not.toHaveTextContent(sameName.id);
    await user.click(dialog.getByRole("checkbox", { name: /我已重新核对列表/ }));

    await user.click(dialog.getByRole("button", { name: "重新读取章节列表核对" }));
    expect(await dialog.findByRole("status")).toHaveTextContent("暂时无法核实：目录读取失败");
    expect(dialog.queryByRole("region", { name: "最新读取的章节目录" })).not.toBeInTheDocument();
    expect(dialog.queryByRole("checkbox", { name: /我已重新核对列表/ })).not.toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "创建章节" })).toBeDisabled();
    expect(createChapter).toHaveBeenCalledTimes(1);

    await user.click(dialog.getByRole("button", { name: "重新读取章节列表核对" }));
    expect(await dialog.findByRole("region", { name: "最新读取的章节目录" }))
      .toHaveTextContent("另一个已存在章节");
    expect(dialog.getByRole("checkbox", { name: /我已重新核对列表/ })).not.toBeChecked();
    await user.click(dialog.getByRole("checkbox", { name: /我已重新核对列表/ }));
    await user.click(dialog.getByRole("button", { name: "创建章节" }));

    expect(createChapter).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("heading", { level: 2, name: "待核对章节" })).toBeInTheDocument();
  });

  it("UCHAP-01/02 revokes delete retry after a failed verification and shows the current directory", async () => {
    const user = userEvent.setup();
    const target = chapter("chapter-delete-reverify", "需要再次核对", [], {
      created_at: "2026-10-05T10:30:00Z",
    });
    const remaining = chapter("chapter-delete-reverify-remaining", "另一条目录记录", [], {
      created_at: "2026-10-04T10:30:00Z",
    });
    const { services, listChapters, deleteChapter } = makeServices([target, remaining], {}, true);
    listChapters
      .mockImplementationOnce(async () => [target, remaining])
      .mockImplementationOnce(async () => [target, remaining])
      .mockRejectedValueOnce(new ApiError("network", "目录读取失败"))
      .mockImplementationOnce(async () => [target, remaining]);
    deleteChapter.mockRejectedValueOnce(new ApiError("http", "删除结果未知", 500));
    renderBrowser(services);

    await screen.findByRole("heading", { level: 2, name: "需要再次核对" });
    await user.click(screen.getByRole("button", { name: "删除章节" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByRole("checkbox", { name: "我已核对章节名称，并确认删除。" }));
    await user.click(dialog.getByRole("button", { name: "确认删除章节" }));
    expect(await dialog.findByRole("alert")).toHaveTextContent("本次请求结果未确认");
    expect(deleteChapter).toHaveBeenCalledTimes(1);

    await user.click(dialog.getByRole("button", { name: "重新读取章节列表核对" }));
    const firstDirectory = await dialog.findByRole("region", { name: "最新读取的章节目录" });
    expect(within(firstDirectory).getByText("需要再次核对")).toBeInTheDocument();
    expect(within(firstDirectory).getByText("另一条目录记录")).toBeInTheDocument();
    expect(firstDirectory).toHaveTextContent("创建于 2026-10-05T10:30:00Z");
    await user.click(dialog.getByRole("checkbox", { name: /我已重新核对列表/ }));

    await user.click(dialog.getByRole("button", { name: "重新读取章节列表核对" }));
    expect(await dialog.findByRole("status")).toHaveTextContent("暂时无法核实：目录读取失败");
    expect(dialog.queryByRole("region", { name: "最新读取的章节目录" })).not.toBeInTheDocument();
    expect(dialog.queryByRole("checkbox", { name: /我已重新核对列表/ })).not.toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "确认删除章节" })).toBeDisabled();
    expect(deleteChapter).toHaveBeenCalledTimes(1);

    await user.click(dialog.getByRole("button", { name: "重新读取章节列表核对" }));
    const latestDirectory = await dialog.findByRole("region", { name: "最新读取的章节目录" });
    expect(latestDirectory).toHaveTextContent("另一条目录记录");
    expect(dialog.getByRole("checkbox", { name: /我已重新核对列表/ })).not.toBeChecked();
    await user.click(dialog.getByRole("checkbox", { name: /我已重新核对列表/ }));
    await user.click(dialog.getByRole("button", { name: "确认删除章节" }));

    expect(deleteChapter).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("heading", { level: 2, name: "另一条目录记录" })).toBeInTheDocument();
  });

  it("UCHAP-02 lets keyboard users enter the freshly read chapter directory", async () => {
    const user = userEvent.setup();
    const latestChapters = Array.from({ length: 20 }, (_, index) => chapter(
      `fresh-directory-${index + 1}`,
      `核对章节 ${index + 1}`,
      [],
      { created_at: `2026-10-15T00:${String(index).padStart(2, "0")}:00Z` },
    ));
    const { services, listChapters, createChapter } = makeServices([], {}, true);
    listChapters
      .mockImplementationOnce(async () => [])
      .mockImplementationOnce(async () => latestChapters);
    createChapter.mockRejectedValueOnce(new ApiError("network", "创建结果未知"));
    renderBrowser(services);

    await user.click(await screen.findByRole("button", { name: "新建章节" }));
    await user.type(screen.getByRole("textbox", { name: "章节名称" }), "等待核对的新章");
    await user.click(screen.getByRole("button", { name: "创建章节" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("本次请求结果未确认");

    const dialog = within(screen.getByRole("dialog"));
    const verifyButton = dialog.getByRole("button", { name: "重新读取章节列表核对" });
    await user.click(verifyButton);
    const directory = await dialog.findByRole("region", { name: "最新读取的章节目录" });
    expect(within(directory).getAllByRole("listitem")).toHaveLength(20);
    expect(within(directory).getByText("核对章节 1")).toBeInTheDocument();
    expect(within(directory).getByText("核对章节 20")).toBeInTheDocument();
    expect(directory).toHaveTextContent("创建于 2026-10-15T00:00:00Z");
    expect(directory).toHaveTextContent("创建于 2026-10-15T00:19:00Z");
    expect(screen.getByRole("heading", { level: 1, name: series.name })
      .closest(".chapter-browser-heading")?.hasAttribute("inert")).toBe(true);
    expect(verifyButton).toHaveFocus();

    await user.tab({ shift: true });

    expect(directory).toHaveFocus();
  });

  it("saves the complete chapter order and keeps the selected chapter selected", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-order-first", "第一章", [], { order: 1 });
    const second = chapter("chapter-order-second", "第二章", [], { order: 2 });
    const { services, reorderChapters } = makeServices([second, first], {}, false, true);
    renderBrowser(services);

    await screen.findByRole("heading", { level: 2, name: "第二章" });
    await user.click(screen.getByRole("button", { name: /第一章/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "第一章" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "调整顺序" }));
    const dialog = within(screen.getByRole("dialog", { name: "调整章节顺序" }));
    await user.click(dialog.getByRole("button", { name: "将第一章上移" }));
    await user.click(dialog.getByRole("button", { name: "保存全部顺序" }));

    await waitFor(() => expect(reorderChapters).toHaveBeenCalledTimes(1));
    expect(reorderChapters).toHaveBeenCalledWith(series.id, {
      chapters: [{ id: first.id, order: 2 }, { id: second.id, order: 1 }],
    }, expect.any(AbortSignal));
    expect(await screen.findByRole("heading", { level: 2, name: "第一章" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /第一章/ })).toHaveAttribute("aria-current", "true");
  });

  it("does not normalize stored order gaps when the visual order is unchanged", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-order-gap-first", "第一章", [], { order: 2 });
    const second = chapter("chapter-order-gap-second", "第二章", [], { order: 9 });
    const third = chapter("chapter-order-gap-third", "第三章", [], { order: 14 });
    const { services, reorderChapters } = makeServices([third, second, first], {}, false, true);
    renderBrowser(services);

    await screen.findByRole("heading", { level: 2, name: "第三章" });
    await user.click(screen.getByRole("button", { name: "调整顺序" }));
    const dialog = within(screen.getByRole("dialog", { name: "调整章节顺序" }));
    await user.click(dialog.getByRole("button", { name: "保存全部顺序" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "调整章节顺序" })).not.toBeInTheDocument());
    expect(reorderChapters).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /第三章/ })).toHaveAttribute("aria-current", "true");
  });

  it("closes the active editor on external chapter navigation and ignores its late save", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-editor-switch-first", "编辑器第一章", [{ text: "第一章文字" }], { order: 1 });
    const second = chapter("chapter-editor-switch-second", "编辑器第二章", [{ text: "第二章文字" }], { order: 2 });
    const pendingWrite = deferred<Chapter>();
    const { services, updateChapter } = makeServices([first, second], {}, false, true);
    updateChapter.mockReturnValueOnce(pendingWrite.promise);
    const onBack = vi.fn();
    const onUnauthorized = vi.fn();
    const view = renderBrowser(services, onBack, onUnauthorized);

    await screen.findByText("第一章文字");
    const firstFrame = screen.getByRole("article", { name: "编辑器第一章 · 镜头 1" });
    await user.click(within(firstFrame).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "旧章节待确认草稿");
    await user.click(editor.getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));

    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: second.id,
      navigationEpoch: 632,
    };
    view.rerender(
      <ChapterBrowser
        navigationIntent={intent}
        onBack={onBack}
        onUnauthorized={onUnauthorized}
        series={series}
        services={services}
        userId="demo-user"
      />,
    );

    expect(await screen.findByRole("heading", { level: 2, name: "编辑器第二章" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument();
    expect(screen.getByText("第二章文字")).toBeInTheDocument();

    await act(async () => {
      pendingWrite.reject(new ApiError("http", "expired", 401));
      await pendingWrite.promise.catch(() => undefined);
    });

    expect(screen.getByRole("heading", { level: 2, name: "编辑器第二章" })).toBeInTheDocument();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("saves an empty frame text while preserving the complete frame payload", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-text", "文字章节", [{
      text: "原文字",
      original_text: "分镜原文",
      storyboard: ["frame-text-asset"],
      character: ["character-ref"],
      scene: ["scene-ref"],
      prop: ["prop-ref"],
      preview: { future: "field" },
      nested_unknown: { retained: ["value"] },
    }]);
    const frameAsset = asset("frame-text-asset", current.id);
    const { services, updateChapter } = makeServices(
      [current], { [current.id]: [frameAsset] }, false, true,
    );
    renderBrowser(services);

    await screen.findByText("原文字");
    const frame = screen.getByRole("article", { name: "文字章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const editor = within(screen.getByRole("dialog", { name: "编辑分镜文字" }));
    await user.clear(editor.getByRole("textbox", { name: "镜头文字" }));
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    expect(updateChapter).toHaveBeenCalledWith(series.id, current.id, {
      content: [{
        text: "",
        original_text: "分镜原文",
        storyboard: ["frame-text-asset"],
        character: ["character-ref"],
        scene: ["scene-ref"],
        prop: ["prop-ref"],
        preview: { future: "field" },
        nested_unknown: { retained: ["value"] },
      }],
    }, expect.any(AbortSignal));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument());
  });

  it.each([
    { label: "fires an input event", dispatchInput: true },
    { label: "does not fire an input event", dispatchInput: false },
  ])("fills the opening original through a reversible edit when execCommand $label", async ({ dispatchInput }) => {
    const user = userEvent.setup();
    const openingOriginal = "原文第一行\n<b>原文第二行</b>";
    const current = chapter("chapter-original-fill", "原文回填章节", [{
      text: "当前正文",
      original_text: openingOriginal,
      storyboard: [],
      editor_note: "保留字段",
    }]);
    const { services, updateChapter } = makeServices([current], {}, false, true);
    installDocumentExecCommand((command, showUI, value) => {
      expect(command).toBe("insertText");
      expect(showUI).toBe(false);
      const textarea = document.activeElement;
      if (!(textarea instanceof HTMLTextAreaElement)) return false;
      const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setValue?.call(textarea, value ?? "");
      if (dispatchInput) fireEvent.input(textarea, { inputType: "insertText", data: value ?? "" });
      return true;
    });
    renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "原文回填章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    expect(textbox).toHaveValue("当前正文");
    await user.click(editor.getByRole("button", { name: "回填打开时原文" }));

    expect(textbox).toHaveValue(openingOriginal);
    expect(updateChapter).not.toHaveBeenCalled();
    await user.click(editor.getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    expect(updateChapter).toHaveBeenCalledWith(series.id, current.id, {
      content: [{ text: openingOriginal, original_text: openingOriginal, storyboard: [], editor_note: "保留字段" }],
    }, expect.any(AbortSignal));
  });

  it.each([
    { label: "execCommand is unavailable", execute: undefined },
    { label: "execCommand reports failure", execute: () => false },
  ])("keeps the draft unchanged when $label", async ({ execute }) => {
    const user = userEvent.setup();
    const current = chapter("chapter-original-fill-failure", "回填失败章节", [{
      text: "保留当前正文",
      original_text: "未回填原文",
      storyboard: [],
    }]);
    const { services, updateChapter } = makeServices([current], {}, false, true);
    installDocumentExecCommand(execute);
    renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "回填失败章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.click(editor.getByRole("button", { name: "回填打开时原文" }));

    expect(textbox).toHaveValue("保留当前正文");
    expect(await editor.findByRole("alert")).toHaveTextContent(/原文未回填|草稿保持不变/);
    expect(updateChapter).not.toHaveBeenCalled();
  });

  it("saves and independently verifies before moving to the next frame without reacquiring its lock", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-edit-sequence", "连续编辑章节", [
      { text: "第一镜原文", original_text: "第一镜打开原文", storyboard: ["sequence-frame-1"], extra: { keep: 1 } },
      { text: "第二镜文字", original_text: "第二镜打开原文", storyboard: ["sequence-frame-2"], extra: { keep: 2 } },
    ]);
    const assets = [asset("sequence-frame-1", current.id), asset("sequence-frame-2", current.id)];
    const base = makeServices([current], { [current.id]: assets }, false, true);
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-28T00:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    base.getChapterEditLock.mockResolvedValueOnce({ locked: false }).mockResolvedValue(ownedLock);
    const teamSeries: Series = { ...series, team_id: "team-sequence", team_name: "团队" };
    renderBrowser(base.services, vi.fn(), vi.fn(), teamSeries);

    const firstCard = await screen.findByRole("article", { name: "连续编辑章节 · 镜头 1" });
    await user.click(within(firstCard).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const initialTextarea = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(initialTextarea);
    await user.type(initialTextarea, "保存后继续的第一镜草稿");
    const listReadsBeforeMove = base.listChapters.mock.calls.length;
    await user.click(editor.getByRole("button", { name: "下一镜头" }));

    await waitFor(() => expect(base.updateChapter).toHaveBeenCalledTimes(1));
    const nextDialog = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const nextEditor = within(nextDialog);
    const nextTextarea = nextEditor.getByRole("textbox", { name: "镜头文字" });
    await waitFor(() => expect(nextTextarea).toHaveValue("第二镜文字"));
    expect(nextTextarea).not.toBe(initialTextarea);
    expect(base.updateChapter).toHaveBeenCalledWith(series.id, current.id, {
      content: [
        { text: "保存后继续的第一镜草稿", original_text: "第一镜打开原文", storyboard: ["sequence-frame-1"], extra: { keep: 1 } },
        { text: "第二镜文字", original_text: "第二镜打开原文", storyboard: ["sequence-frame-2"], extra: { keep: 2 } },
      ],
    }, expect.any(AbortSignal));
    expect(base.listChapters.mock.calls.length - listReadsBeforeMove).toBeGreaterThanOrEqual(4);
    expect(base.acquireChapterEditLock).toHaveBeenCalledTimes(1);
    expect(base.getChapterEditLock.mock.calls.filter((call) => call[2] !== undefined).length).toBeGreaterThanOrEqual(3);
    expect(nextEditor.getByRole("region", { name: "打开时原文" })).toHaveTextContent("第二镜打开原文");
    expect(nextEditor.getByRole("region", { name: "镜头 2 的关联素材" })).toBeInTheDocument();

    await user.click(nextEditor.getByRole("button", { name: "上一镜头" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "镜头文字" })).toHaveValue("保存后继续的第一镜草稿"));
    expect(base.updateChapter).toHaveBeenCalledTimes(1);
  });

  it("closes after an explicit overwrite rebases the same cursor and releases its original lock", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-overwrite-rebase", "覆盖重基线章节", [
      {
        text: "初始正文",
        original_text: "镜头一原文",
        storyboard: ["overwrite-rebase-frame-1"],
        extra: { keep: "frame-one" },
      },
      {
        text: "第二镜原文",
        storyboard: ["overwrite-rebase-frame-2"],
        extra: { revision: "initial" },
      },
    ]);
    const base = makeServices(
      [current],
      {
        [current.id]: [
          asset("overwrite-rebase-frame-1", current.id),
          asset("overwrite-rebase-frame-2", current.id),
        ],
      },
      false,
      true,
    );
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-28T00:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    const lease = {} as ChapterEditLockLeaseHandle;
    base.getChapterEditLock.mockImplementation(async (_chapterId, _signal, suppliedLease) => (
      suppliedLease === undefined ? { locked: false } : ownedLock
    ));
    base.acquireChapterEditLock.mockResolvedValue({ state: ownedLock, lease });
    const teamSeries: Series = { ...series, team_id: "team-frame-overwrite-rebase", team_name: "团队" };
    renderBrowser(base.services, vi.fn(), vi.fn(), teamSeries);

    const firstFrame = await screen.findByRole("article", { name: "覆盖重基线章节 · 镜头 1" });
    await user.click(within(firstFrame).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "明确覆盖后的正文");

    const conflictChapter = chapter(current.id, current.title, [
      {
        text: "服务器冲突正文",
        original_text: "镜头一原文",
        storyboard: ["overwrite-rebase-frame-1"],
        extra: { keep: "frame-one" },
      },
      {
        text: "第二镜原文",
        storyboard: ["overwrite-rebase-frame-2"],
        extra: { revision: "initial" },
      },
    ]);
    base.setCurrentChapters([conflictChapter]);
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    const serverText = await editor.findByRole("region", { name: "服务器当前镜头文字" });
    expect(serverText).toHaveTextContent("服务器冲突正文");
    expect(textbox).toHaveValue("明确覆盖后的正文");
    expect(base.updateChapter).not.toHaveBeenCalled();

    base.setCurrentChapters([chapter(current.id, current.title, [
      {
        text: "服务器冲突正文",
        original_text: "镜头一原文",
        storyboard: ["overwrite-rebase-frame-1"],
        extra: { keep: "frame-one" },
      },
      {
        text: "第二镜并发更新",
        storyboard: ["overwrite-rebase-frame-2"],
        extra: { revision: "concurrent" },
        server_only: { preserve: true },
      },
    ])]);
    await user.click(editor.getByRole("button", { name: "以草稿覆盖当前文字" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument());
    expect(base.updateChapter).toHaveBeenCalledTimes(1);
    expect(base.updateChapter).toHaveBeenCalledWith(series.id, current.id, {
      content: [
        {
          text: "明确覆盖后的正文",
          original_text: "镜头一原文",
          storyboard: ["overwrite-rebase-frame-1"],
          extra: { keep: "frame-one" },
        },
        {
          text: "第二镜并发更新",
          storyboard: ["overwrite-rebase-frame-2"],
          extra: { revision: "concurrent" },
          server_only: { preserve: true },
        },
      ],
    }, expect.any(AbortSignal));
    expect(base.acquireChapterEditLock).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(base.releaseChapterEditLock).toHaveBeenCalledTimes(1));
    expect(base.releaseChapterEditLock).toHaveBeenCalledWith(current.id, expect.any(AbortSignal), lease);
  });

  it("rejects a late close and reader 401 from the previous frame cursor", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-editor-stale-cursor", "旧游标回调章节", [
      { text: "第一镜正文", character: ["stale-cursor-character"], storyboard: [] },
      { text: "第二镜正文", character: [], storyboard: [] },
    ]);
    const base = makeServices([current], {}, false, true);
    const pendingCharacters = deferred<Character[]>();
    base.services.listCharacters = vi.fn(async () => pendingCharacters.promise);
    const onUnauthorized = vi.fn();
    renderBrowser(base.services, vi.fn(), onUnauthorized);

    const firstFrame = await screen.findByRole("article", { name: "旧游标回调章节 · 镜头 1" });
    await user.click(within(firstFrame).getByRole("button", { name: "编辑文字" }));
    const firstDialog = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const staleClose = captureButtonHandler(firstDialog.getByRole("button", { name: "关闭文字编辑" }));
    await user.click(firstDialog.getByRole("button", { name: "角色" }));
    await waitFor(() => expect(base.services.listCharacters).toHaveBeenCalledTimes(1));
    await user.click(firstDialog.getByRole("button", { name: "下一镜头" }));

    await waitFor(() => expect(screen.getByRole("textbox", { name: "镜头文字" })).toHaveValue("第二镜正文"));
    staleClose();
    expect(screen.getByRole("dialog", { name: "编辑分镜文字" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "镜头文字" })).toHaveValue("第二镜正文");

    await act(async () => {
      pendingCharacters.reject(new ApiError("http", "expired", 401));
      await pendingCharacters.promise.catch(() => undefined);
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "编辑分镜文字" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "镜头文字" })).toHaveValue("第二镜正文");
  });

  it("stays on the current frame after a preflight conflict and blocks navigation after an unknown write", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-edit-sequence-unknown", "连续编辑保护章节", [
      { text: "原始第一镜", original_text: "第一镜原文", storyboard: ["sequence-unknown-1"] },
      { text: "第二镜", storyboard: ["sequence-unknown-2"] },
    ]);
    const assets = [asset("sequence-unknown-1", current.id), asset("sequence-unknown-2", current.id)];
    const preflight = makeServices([current], { [current.id]: assets }, false, true);
    renderBrowser(preflight.services);
    const firstCard = await screen.findByRole("article", { name: "连续编辑保护章节 · 镜头 1" });
    await user.click(within(firstCard).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "草稿不应切镜");
    preflight.setCurrentChapters([chapter(current.id, current.title, [
      { text: "服务器并发修改", original_text: "第一镜原文", storyboard: ["sequence-unknown-1"] },
      { text: "第二镜", storyboard: ["sequence-unknown-2"] },
    ])]);
    await user.click(editor.getByRole("button", { name: "下一镜头" }));

    expect(await editor.findByRole("region", { name: "服务器当前镜头文字" })).toHaveTextContent("服务器并发修改");
    expect(textbox).toHaveValue("草稿不应切镜");
    expect(preflight.updateChapter).not.toHaveBeenCalled();
    expect(editor.getByRole("button", { name: "下一镜头" })).toBeDisabled();

    preflight.setCurrentChapters([chapter(current.id, current.title, [
      { text: "服务器并发修改", original_text: "第一镜原文", storyboard: ["sequence-unknown-1"] },
      { text: "第二镜", storyboard: ["sequence-unknown-2"] },
    ])]);
    await user.clear(textbox);
    await user.type(textbox, "请求已发出但未知");
    preflight.updateChapter.mockRejectedValueOnce(new ApiError("network", "PUT响应丢失"));
    await user.click(editor.getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(preflight.updateChapter).toHaveBeenCalledTimes(1));
    expect(await editor.findByText(/不能再次提交或切镜/)).toBeInTheDocument();
    expect(editor.getByRole("button", { name: "下一镜头" })).toBeDisabled();
    expect(preflight.updateChapter).toHaveBeenCalledTimes(1);
  });

  it("keeps the opening original-text snapshot separate from the draft after a conflict read", async () => {
    clearOriginalTextPanelPositions();
    const user = userEvent.setup();
    const current = chapter("chapter-original-reference-conflict", "原文快照章节", [{
      text: "打开前正文",
      original_text: "<b>打开时原文</b>\n第二行",
      storyboard: ["original-reference-frame"],
    }]);
    const { services, updateChapter, setCurrentChapters } = makeServices(
      [current], { [current.id]: [asset("original-reference-frame", current.id)] }, false, true,
    );
    renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "原文快照章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const dialogElement = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const dialog = within(dialogElement);
    const textbox = dialog.getByRole("textbox", { name: "镜头文字" });
    const originalPanel = await screen.findByRole("region", { name: "打开时原文" });
    expect(originalPanel.querySelector("pre")?.textContent).toBe("<b>打开时原文</b>\n第二行");
    expect(originalPanel.querySelector("b")).toBeNull();
    expect(dialog.getByRole("button", { name: "拖动原文对照浮窗；也可按方向键移动" })).toBeInTheDocument();
    expect(dialogElement.contains(originalPanel)).toBe(true);
    await user.clear(textbox);
    await user.type(textbox, "仍在编辑的草稿");

    setCurrentChapters([chapter("chapter-original-reference-conflict", "原文快照章节", [{
      text: "服务器当前正文",
      original_text: "冲突后重新读取的原文",
      storyboard: ["original-reference-frame"],
    }])]);
    await user.click(dialog.getByRole("button", { name: "保存文字" }));

    const serverValue = screen.getByRole("region", { name: "服务器当前镜头文字" });
    expect(within(serverValue).getByText("服务器当前正文")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "打开时原文" })).toBe(originalPanel);
    expect(originalPanel.querySelector("pre")?.textContent).toBe("<b>打开时原文</b>\n第二行");
    expect(originalPanel).not.toHaveTextContent("冲突后重新读取的原文");
    expect(dialog.getByRole("textbox", { name: "镜头文字" })).toBe(textbox);
    expect(textbox).toHaveValue("仍在编辑的草稿");
    expect(updateChapter).not.toHaveBeenCalled();
    const savedOriginalValues = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
      .filter((key): key is string => key?.startsWith("haoai.frame-original-reference-panel-position.v1:") === true)
      .map((key) => localStorage.getItem(key) ?? "");
    expect(savedOriginalValues.join("\n")).not.toContain("打开时原文");
    expect(savedOriginalValues.join("\n")).not.toContain("仍在编辑的草稿");
  });

  it("keeps the real reference reader mounted when hidden and shown inside the editor", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-editor-reference-reader", "编辑器素材章节", [{
      text: "正文",
      original_text: "打开原文",
      character: ["reference-reader-character"],
    }]);
    const { services, updateChapter } = makeServices([current], {}, false, true);
    const listCharacters = vi.fn(async () => []);
    services.listCharacters = listCharacters;
    renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "编辑器素材章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const editorElement = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const editor = within(editorElement);
    const panel = await editor.findByRole("region", { name: "镜头 1 的关联素材" });
    expect(editor.getByRole("region", { name: "打开时原文" })).toHaveTextContent("打开原文");
    expect(editorElement.contains(panel)).toBe(true);
    await user.click(within(panel).getByRole("button", { name: "角色" }));
    await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(1));
    await user.click(within(panel).getByRole("button", { name: "关闭" }));
    expect(panel).not.toBeVisible();
    await user.click(editor.getByRole("button", { name: "显示关联素材" }));

    expect(await editor.findByRole("region", { name: "镜头 1 的关联素材" })).toBe(panel);
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(updateChapter).not.toHaveBeenCalled();
  });

  it("hides the old reference reader when a conflict refresh removes the current frame", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-editor-reference-owner-change", "素材所有者变化章节", [{
      text: "原始正文",
      original_text: "打开原文",
      character: ["reference-owner-character"],
    }]);
    const { services, updateChapter, setCurrentChapters } = makeServices(
      [current], {}, false, true,
    );
    const listCharacters = vi.fn(async () => []);
    services.listCharacters = listCharacters;
    renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "素材所有者变化章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const dialogElement = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const dialog = within(dialogElement);
    const panel = await dialog.findByRole("region", { name: "镜头 1 的关联素材" });
    await user.click(within(panel).getByRole("button", { name: "角色" }));
    await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(1));

    const textbox = dialog.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "保留草稿");
    setCurrentChapters([chapter(current.id, current.title, [])]);
    await user.click(dialog.getByRole("button", { name: "保存文字" }));

    expect(await dialog.findByRole("region", { name: "服务器当前镜头文字" })).toBeInTheDocument();
    expect(textbox).toHaveValue("保留草稿");
    expect(panel).not.toBeVisible();
    expect(await dialog.findByText("当前镜头关联素材来源已变化，旧读取已隐藏。")).toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: "显示关联素材" })).not.toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(updateChapter).not.toHaveBeenCalled();
  });

  it("keeps the editor and pending save mounted while hiding, floating, and reopening the original panel", async () => {
    clearOriginalTextPanelPositions();
    const user = userEvent.setup();
    const current = chapter("chapter-original-reference-session", "原文会话章节", [{
      text: "编辑前正文",
      original_text: "会话原文",
      storyboard: ["original-reference-session-frame"],
    }]);
    const pendingWrite = deferred<Chapter>();
    const { services, updateChapter, listChapters, setCurrentChapters } = makeServices(
      [current], { [current.id]: [asset("original-reference-session-frame", current.id)] }, false, true,
    );
    const editorView = renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "原文会话章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const dialogElement = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const dialog = within(dialogElement);
    const textbox = dialog.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "保留的草稿");
    const firstPanel = await screen.findByRole("region", { name: "打开时原文" });
    await user.click(within(firstPanel).getByRole("button", { name: "隐藏对照" }));
    expect(screen.queryByRole("region", { name: "打开时原文" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "编辑分镜文字" })).toBe(dialogElement);
    expect(dialog.getByRole("textbox", { name: "镜头文字" })).toBe(textbox);
    await user.click(dialog.getByRole("button", { name: "显示原文对照" }));
    const panel = await screen.findByRole("region", { name: "打开时原文" });
    expect(dialogElement.contains(panel)).toBe(true);
    expect(dialog.getByRole("textbox", { name: "镜头文字" })).toBe(textbox);

    const dragHandle = within(panel).getByRole("button", { name: "拖动原文对照浮窗；也可按方向键移动" });
    const capture = stubPointerCapture(dragHandle);
    fireEvent.pointerDown(dragHandle, { pointerId: 13, button: 0, clientX: 20, clientY: 20 });
    fireEvent.pointerMove(dragHandle, { pointerId: 13, clientX: 60, clientY: 50 });
    fireEvent.pointerUp(dragHandle, { pointerId: 13, clientX: 60, clientY: 50 });
    expect(capture.setPointerCapture).toHaveBeenCalledOnce();
    expect(capture.releasePointerCapture).toHaveBeenCalledOnce();
    const savedKeys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
      .filter((key): key is string => key?.startsWith("haoai.frame-original-reference-panel-position.v1:") === true);
    expect(savedKeys).toHaveLength(1);
    const savedPosition = JSON.parse(localStorage.getItem(savedKeys[0]!) ?? "null") as Record<string, unknown>;
    expect(Object.keys(savedPosition).sort()).toEqual(["left", "top"]);
    expect(typeof savedPosition.left).toBe("number");
    expect(typeof savedPosition.top).toBe("number");
    expect(localStorage.getItem(savedKeys[0]!)).not.toContain("会话原文");
    expect(localStorage.getItem(savedKeys[0]!)).not.toContain("保留的草稿");

    const savedLeft = panel.style.left;
    const savedTop = panel.style.top;
    await user.click(within(panel).getByRole("button", { name: "隐藏对照" }));
    expect(screen.queryByRole("region", { name: "打开时原文" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "编辑分镜文字" })).toBe(dialogElement);
    await user.click(dialog.getByRole("button", { name: "显示原文对照" }));
    const reopenedPanel = await screen.findByRole("region", { name: "打开时原文" });
    expect(reopenedPanel.style.left).toBe(savedLeft);
    expect(reopenedPanel.style.top).toBe(savedTop);
    expect(dialog.getByRole("textbox", { name: "镜头文字" })).toBe(textbox);

    await user.click(within(reopenedPanel).getByRole("button", { name: "放回编辑器内" }));
    expect(reopenedPanel).not.toHaveClass("is-floating");
    await user.click(within(reopenedPanel).getByRole("button", { name: "浮动查看" }));
    expect(reopenedPanel).toHaveClass("is-floating");
    expect(dialog.getByRole("textbox", { name: "镜头文字" })).toBe(textbox);

    updateChapter.mockImplementationOnce(async (seriesId, chapterId, update) => {
      const saved = { ...current, ...(chapterId === current.id && seriesId === series.id ? update : {}) };
      setCurrentChapters([saved]);
      return pendingWrite.promise;
    });
    await user.click(dialog.getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    const readsWhileSaving = listChapters.mock.calls.length;
    expect(textbox).toBeDisabled();
    await user.click(within(reopenedPanel).getByRole("button", { name: "放回编辑器内" }));
    await user.click(within(reopenedPanel).getByRole("button", { name: "浮动查看" }));
    expect(dialog.getByRole("textbox", { name: "镜头文字" })).toBe(textbox);
    expect(textbox).toBeDisabled();
    expect(textbox).toHaveValue("保留的草稿");
    expect(listChapters).toHaveBeenCalledTimes(readsWhileSaving);

    await act(async () => {
      pendingWrite.resolve({ ...current, content: [{ text: "保留的草稿", original_text: "会话原文", storyboard: ["original-reference-session-frame"] }] });
      await pendingWrite.promise;
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument());
    expect(editorView.container.querySelector(".frame-original-text-panel")).toBeNull();
  });

  it("releases a pending original-panel drag when the editor scope changes", async () => {
    clearOriginalTextPanelPositions();
    const user = userEvent.setup();
    const current = chapter("chapter-original-reference-stale-drag", "原文拖动章节", [{
      text: "正文",
      original_text: "只读原文",
      storyboard: ["original-reference-stale-frame"],
    }]);
    const { services, updateChapter } = makeServices(
      [current], { [current.id]: [asset("original-reference-stale-frame", current.id)] }, false, true,
    );
    const view = renderBrowser(services);

    const frame = await screen.findByRole("article", { name: "原文拖动章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const panel = await screen.findByRole("region", { name: "打开时原文" });
    const handle = within(panel).getByRole("button", { name: "拖动原文对照浮窗；也可按方向键移动" });
    const capture = stubPointerCapture(handle);
    fireEvent.pointerDown(handle, { pointerId: 21, button: 0, clientX: 5, clientY: 5 });
    fireEvent.pointerMove(handle, { pointerId: 21, clientX: 45, clientY: 45 });

    const nextServices = { ...services, apiBaseUrl: "http://127.0.0.1:4176/api" };
    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        series={series}
        services={nextServices}
        userId="demo-user"
      />,
    );
    expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument();
    fireEvent.pointerUp(handle, { pointerId: 21, clientX: 90, clientY: 90 });
    expect(capture.releasePointerCapture).toHaveBeenCalledOnce();
    expect(updateChapter).not.toHaveBeenCalled();
    const savedKeys = Array.from({ length: localStorage.length }, (_, index) => localStorage.key(index))
      .filter((key): key is string => key?.startsWith("haoai.frame-original-reference-panel-position.v1:") === true);
    expect(savedKeys).toHaveLength(0);
  });

  it.each([
    { label: "missing", frame: { text: "不能替代原文" }, message: "打开编辑器时，本镜头没有原文记录。" },
    { label: "unrecognized", frame: { text: "不能替代原文", original_text: { value: "unknown" } }, message: "打开编辑器时的原文内容无法识别。" },
  ])("shows $label original text without substituting the editable text", async ({ label, frame: sourceFrame, message }) => {
    const user = userEvent.setup();
    const current = chapter(`chapter-original-reference-${label}`, "原文结构章节", [sourceFrame as NonNullable<Chapter["content"]>[number]]);
    const { services } = makeServices(
      [current], { [current.id]: [asset("original-reference-frame", current.id)] }, false, true,
    );
    renderBrowser(services);

    const article = await screen.findByRole("article", { name: "原文结构章节 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const dialog = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(dialog.getByRole("textbox", { name: "镜头文字" })).toHaveValue("不能替代原文");
  });

  it.each(["missing", "null"] as const)("does not write when an empty editor keeps %s frame text unchanged", async (kind) => {
    const user = userEvent.setup();
    const frame = kind === "missing"
      ? { storyboard: ["empty-frame-asset"] }
      : { text: null, storyboard: ["empty-frame-asset"] } as unknown as NonNullable<Chapter["content"]>[number];
    const current = chapter("chapter-frame-empty-noop", "空文字章节", [frame] as NonNullable<Chapter["content"]>);
    const { services, updateChapter } = makeServices(
      [current], { [current.id]: [asset("empty-frame-asset", current.id)] }, false, true,
    );
    renderBrowser(services);

    const article = await screen.findByRole("article", { name: "空文字章节 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    expect(editor.getByRole("textbox", { name: "镜头文字" })).toHaveValue("");
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument());
    expect(updateChapter).not.toHaveBeenCalled();
  });

  it("checks an unknown text-write result with one PUT and fresh reads only", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-uncertain", "待核对章节", [
      { text: "保存前", storyboard: ["uncertain-asset"], extra: { keep: true } },
    ]);
    const { services, updateChapter, listChapters, setCurrentChapters } = makeServices(
      [current], { [current.id]: [asset("uncertain-asset", current.id)] }, false, true,
    );
    updateChapter.mockImplementationOnce(async (seriesId, chapterId, update) => {
      setCurrentChapters([{
        ...current,
        ...(chapterId === current.id && seriesId === series.id ? update : {}),
      }]);
      throw new ApiError("network", "确认响应丢失");
    });
    renderBrowser(services);

    await screen.findByText("保存前");
    const frame = screen.getByRole("article", { name: "待核对章节 · 镜头 1" });
    await user.click(within(frame).getByRole("button", { name: "编辑文字" }));
    const editor = within(screen.getByRole("dialog", { name: "编辑分镜文字" }));
    await user.clear(editor.getByRole("textbox", { name: "镜头文字" }));
    await user.type(editor.getByRole("textbox", { name: "镜头文字" }), "服务器已落库");
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument());
    expect(updateChapter).toHaveBeenCalledTimes(1);
    expect(listChapters.mock.calls.length).toBeGreaterThanOrEqual(3);
    expect(await screen.findByText("服务器已落库")).toBeInTheDocument();
  });

  it("keeps an unknown write bound to its original frame when another frame replaces that position", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-unknown-replaced", "未知写入身份章节", [
      { text: "A原文", storyboard: ["unknown-frame-a"], frame_key: "A" },
      { text: "C原文", storyboard: ["unknown-frame-c"], frame_key: "C" },
    ]);
    const { services, updateChapter, setCurrentChapters } = makeServices(
      [current], {
        [current.id]: [
          asset("unknown-frame-a", current.id),
          asset("unknown-frame-b", current.id),
          asset("unknown-frame-c", current.id),
        ],
      }, false, true,
    );
    updateChapter.mockImplementationOnce(async () => {
      setCurrentChapters([chapter(current.id, current.title, [
        { text: "A的新草稿", storyboard: ["unknown-frame-b"], frame_key: "B" },
        { text: "C原文", storyboard: ["unknown-frame-c"], frame_key: "C" },
      ])]);
      throw new ApiError("network", "写入响应丢失");
    });
    renderBrowser(services);

    const article = await screen.findByRole("article", { name: "未知写入身份章节 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const dialog = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = dialog.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "A的新草稿");
    await user.click(dialog.getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    expect(await dialog.findByText(/草稿已冻结，请只读核对/)).toBeInTheDocument();

    await user.click(dialog.getByRole("button", { name: "只读核对来源" }));
    expect(await dialog.findByText("最新章节和素材目录没有确认原写入结果。草稿继续冻结，不会自动重发。")).toBeInTheDocument();
    expect(textbox).toHaveValue("A的新草稿");
    expect(dialog.getByRole("button", { name: "下一镜头" })).toBeDisabled();
    expect(dialog.getByRole("button", { name: "只读核对来源" })).toBeEnabled();
    expect(updateChapter).toHaveBeenCalledTimes(1);
  });

  it("unfreezes an unknown write after a later read confirms the original frame structure", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-unknown-confirmed", "原镜头核对章节", [
      { text: "A原文", storyboard: ["confirmed-frame-a"], frame_key: "A" },
      { text: "B原文", storyboard: ["confirmed-frame-b"], frame_key: "B" },
    ]);
    const { services, updateChapter, setCurrentChapters } = makeServices(
      [current], {
        [current.id]: [asset("confirmed-frame-a", current.id), asset("confirmed-frame-b", current.id)],
      }, false, true,
    );
    updateChapter.mockRejectedValueOnce(new ApiError("network", "写入响应丢失"));
    renderBrowser(services);

    const article = await screen.findByRole("article", { name: "原镜头核对章节 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const dialog = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = dialog.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "A已确认草稿");
    await user.click(dialog.getByRole("button", { name: "保存文字" }));
    expect(await dialog.findByRole("button", { name: "只读核对来源" })).toBeInTheDocument();
    expect(updateChapter).toHaveBeenCalledTimes(1);

    setCurrentChapters([chapter(current.id, current.title, [
      { text: "A已确认草稿", storyboard: ["confirmed-frame-a"], frame_key: "A" },
      { text: "B原文", storyboard: ["confirmed-frame-b"], frame_key: "B" },
    ])]);
    await user.click(dialog.getByRole("button", { name: "只读核对来源" }));
    expect(await dialog.findByText("原写入已通过章节与素材目录核对，可以继续编辑。")).toBeInTheDocument();
    expect(textbox).toHaveValue("A已确认草稿");
    expect(dialog.getByRole("button", { name: "下一镜头" })).toBeEnabled();
    expect(updateChapter).toHaveBeenCalledTimes(1);

    await user.click(dialog.getByRole("button", { name: "下一镜头" }));
    await waitFor(() => expect(screen.getByRole("textbox", { name: "镜头文字" })).toHaveValue("B原文"));
    expect(updateChapter).toHaveBeenCalledTimes(1);
  });

  it("confirms a dispatched HTTP 408 text write through fresh reads without a second PUT", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-408-confirmed", "408写入确认章节", [
      { text: "保存前", storyboard: ["408-confirmed-asset"], retained: true },
    ]);
    const { services, updateChapter, setCurrentChapters } = makeServices(
      [current], { [current.id]: [asset("408-confirmed-asset", current.id)] }, false, true,
    );
    updateChapter.mockImplementationOnce(async (_seriesId, _chapterId, update) => {
      if (update.content === undefined) throw new Error("Expected the frozen frame payload.");
      setCurrentChapters([chapter(current.id, current.title, update.content)]);
      throw new ApiError("http", "gateway timed out after dispatch", 408);
    });
    renderBrowser(services);

    const article = await screen.findByRole("article", { name: "408写入确认章节 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const dialog = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = dialog.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "服务器已核对");
    await user.click(dialog.getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("服务器已核对")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument();
    expect(updateChapter).toHaveBeenCalledTimes(1);
  });

  it("blocks another save and frame navigation when a dispatched HTTP 408 cannot be confirmed", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-408-unconfirmed", "408结果未知章节", [
      { text: "A原文", storyboard: ["408-unknown-a"] },
      { text: "B原文", storyboard: ["408-unknown-b"] },
    ]);
    const { services, updateChapter } = makeServices(
      [current], {
        [current.id]: [asset("408-unknown-a", current.id), asset("408-unknown-b", current.id)],
      }, false, true,
    );
    updateChapter.mockRejectedValueOnce(new ApiError("http", "gateway timed out after dispatch", 408));
    renderBrowser(services);

    const article = await screen.findByRole("article", { name: "408结果未知章节 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const dialog = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = dialog.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "尚未确认的草稿");
    await user.click(dialog.getByRole("button", { name: "保存文字" }));
    expect(await dialog.findByText(/请求可能仍在处理中/)).toBeInTheDocument();
    expect(dialog.getByRole("button", { name: "下一镜头" })).toBeDisabled();
    expect(dialog.queryByRole("button", { name: "保存文字" })).not.toBeInTheDocument();

    await user.click(dialog.getByRole("button", { name: "只读核对来源" }));
    expect(await dialog.findByText(/最新章节和素材目录没有确认原写入结果/)).toBeInTheDocument();
    expect(textbox).toHaveValue("尚未确认的草稿");
    expect(dialog.getByRole("button", { name: "下一镜头" })).toBeDisabled();
    expect(dialog.queryByRole("button", { name: "保存文字" })).not.toBeInTheDocument();
    expect(updateChapter).toHaveBeenCalledTimes(1);
  });

  it("clears the current session on a text preflight 401 without sending the chapter PUT", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-preflight-401", "预检失效章节", [
      { text: "保存前文字", storyboard: ["preflight-401-asset"] },
    ]);
    const { services, listChapters, updateChapter } = makeServices(
      [current], { [current.id]: [asset("preflight-401-asset", current.id)] }, false, true,
    );
    const onUnauthorized = vi.fn();
    renderBrowser(services, vi.fn(), onUnauthorized);

    const article = await screen.findByRole("article", { name: "预检失效章节 · 镜头 1" });
    listChapters.mockRejectedValueOnce(new ApiError("http", "expired", 401));
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "保留的草稿");
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    expect(updateChapter).not.toHaveBeenCalled();
    expect(editor.getByRole("textbox", { name: "镜头文字" })).toHaveValue("保留的草稿");
  });

  it("ignores a late text-save 401 after the user scope changes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-frame-stale-401", "切换作用域章节", [
      { text: "原有文字", storyboard: ["stale-401-asset"] },
    ]);
    const pendingWrite = deferred<Chapter>();
    const { services, updateChapter } = makeServices(
      [current], { [current.id]: [asset("stale-401-asset", current.id)] }, false, true,
    );
    updateChapter.mockReturnValueOnce(pendingWrite.promise);
    const onUnauthorized = vi.fn();
    const view = renderBrowser(services, vi.fn(), onUnauthorized);

    const article = await screen.findByRole("article", { name: "切换作用域章节 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "尚未确认的草稿");
    await user.click(editor.getByRole("button", { name: "保存文字" }));
    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        series={series}
        services={services}
        userId="another-user"
      />,
    );
    expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument();

    await act(async () => {
      pendingWrite.reject(new ApiError("http", "expired", 401));
      await pendingWrite.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("ignores a late team-lock response after switching chapters and opens only the new chapter", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-lock-first", "第一锁章", [
      { text: "第一文字", storyboard: ["lock-first-asset"] },
    ]);
    const second = chapter("chapter-lock-second", "第二锁章", [
      { text: "第二文字", storyboard: ["lock-second-asset"] },
    ], { order: 2 });
    const pendingFirstLock = deferred<ChapterEditLockState>();
    const { services, getChapterEditLock, acquireChapterEditLock, releaseChapterEditLock } = makeServices(
      [first, second], {
        [first.id]: [asset("lock-first-asset", first.id)],
        [second.id]: [asset("lock-second-asset", second.id)],
      }, false, true,
    );
    const teamSeries = { ...series, team_id: "team-edit-test" };
    getChapterEditLock.mockImplementation(async (chapterId) => {
      if (chapterId === first.id) return pendingFirstLock.promise;
      return {
        locked: true,
        user_id: "demo-user",
        username: "演示创作者",
        is_mine: true,
        acquired_at: "2026-10-15T10:00:00Z",
        expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      };
    });
    const view = renderBrowser(services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByText("第一文字");
    const firstFrame = screen.getByRole("article", { name: "第一锁章 · 镜头 1" });
    await user.click(within(firstFrame).getByRole("button", { name: "编辑文字" }));
    await waitFor(() => expect(getChapterEditLock).toHaveBeenCalledWith(first.id, expect.any(AbortSignal)));
    await user.click(screen.getByRole("button", { name: /第二锁章/ }));
    await screen.findByText("第二文字");
    const secondEditButton = screen.getByRole("button", { name: "编辑文字" });
    expect(secondEditButton).toBeEnabled();
    await user.click(secondEditButton);
    expect(await screen.findByRole("dialog", { name: "编辑分镜文字" })).toHaveTextContent("第二锁章 · 镜头 1");

    await act(async () => {
      pendingFirstLock.resolve({ locked: false });
      await pendingFirstLock.promise;
    });
    expect(screen.getByRole("dialog", { name: "编辑分镜文字" })).toHaveTextContent("第二锁章 · 镜头 1");
    expect(acquireChapterEditLock).not.toHaveBeenCalled();

    expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toHaveTextContent("第一锁章");
    view.unmount();
    expect(releaseChapterEditLock).not.toHaveBeenCalled();
  });

  it("releases a newly confirmed team lock when the browser unmounts", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-editor-unmount", "卸载清理章节", [
      { text: "卸载清理文字", storyboard: ["unmount-asset"] },
    ]);
    const { services, getChapterEditLock, releaseChapterEditLock } = makeServices(
      [current], { [current.id]: [asset("unmount-asset", current.id)] }, false, true,
    );
    const lock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    getChapterEditLock.mockResolvedValueOnce({ locked: false }).mockResolvedValueOnce(lock);
    const teamSeries = { ...series, team_id: "team-unmount-test" };
    const view = renderBrowser(services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByText("卸载清理文字");
    await user.click(screen.getByRole("button", { name: "编辑文字" }));
    expect(await screen.findByRole("dialog", { name: "编辑分镜文字" })).toBeInTheDocument();

    view.unmount();

    await waitFor(() => expect(releaseChapterEditLock).toHaveBeenCalledTimes(1));
    expect(releaseChapterEditLock).toHaveBeenCalledWith(current.id, expect.any(AbortSignal), expect.any(Object));
  });

  it("refreshes the chapter lock snapshot after saving text and releasing its new team lock", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-lock-refresh-success", "保存后刷新锁", [
      { text: "保存前文字", storyboard: ["lock-refresh-asset"] },
    ], {
      lock: { locked: true, locked_by_username: "演示创作者", is_mine: true },
    });
    const { services, getChapterEditLock, releaseChapterEditLock, updateChapter } = makeServices(
      [current], { [current.id]: [asset("lock-refresh-asset", current.id)] }, false, true,
    );
    const ownedLock = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    getChapterEditLock
      .mockResolvedValueOnce({ locked: false })
      .mockResolvedValueOnce(ownedLock)
      .mockResolvedValueOnce(ownedLock)
      .mockResolvedValueOnce({ locked: false });
    const teamSeries = { ...series, team_id: "team-lock-refresh" };
    renderBrowser(services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByText(/你在其他位置持有编辑锁/);
    const article = await screen.findByRole("article", { name: "保存后刷新锁 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "已保存的新文字");
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(releaseChapterEditLock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText(/你在其他位置持有编辑锁/)).not.toBeInTheDocument());
    expect(screen.getByText("已保存的新文字")).toBeInTheDocument();
    const calls = getChapterEditLock.mock.calls;
    expect(calls).toHaveLength(4);
    expect(calls[3]).toHaveLength(2);
    expect(calls[3]?.[0]).toBe(current.id);
  });

  it("shows a newer lock returned after release instead of clearing it", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-lock-refresh-new-owner", "释放后出现新锁", [
      { text: "正文", storyboard: ["lock-refresh-new-owner-asset"] },
    ], {
      lock: { locked: true, locked_by_username: "演示创作者", is_mine: true },
    });
    const { services, getChapterEditLock } = makeServices(
      [current], { [current.id]: [asset("lock-refresh-new-owner-asset", current.id)] }, false, true,
    );
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    const newerLock: ChapterEditLockState = {
      locked: true,
      user_id: "other-user",
      username: "周编剧",
      is_mine: false,
      acquired_at: "2026-10-15T10:05:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    getChapterEditLock
      .mockResolvedValueOnce({ locked: false })
      .mockResolvedValueOnce(ownedLock)
      .mockResolvedValueOnce(newerLock);
    const teamSeries = { ...series, team_id: "team-lock-refresh-new-owner" };
    renderBrowser(services, vi.fn(), vi.fn(), teamSeries);

    const article = await screen.findByRole("article", { name: "释放后出现新锁 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    await screen.findByRole("dialog", { name: "编辑分镜文字" });
    await user.click(screen.getByRole("button", { name: "关闭文字编辑" }));

    expect(await screen.findByText(/当前由 周编剧 编辑/)).toBeInTheDocument();
    expect(screen.queryByText(/你在其他位置持有编辑锁/)).not.toBeInTheDocument();
  });

  it.each(["release", "confirmation read"] as const)(
    "keeps the chapter lock snapshot when %s fails after closing the editor",
    async (failurePoint) => {
      const user = userEvent.setup();
      const current = chapter(`chapter-lock-refresh-failure-${failurePoint}`, "释放失败保留锁提示", [
        { text: "原始文字", storyboard: ["lock-refresh-failure-asset"] },
      ], {
        lock: { locked: true, locked_by_username: "演示创作者", is_mine: true },
      });
      const { services, getChapterEditLock, releaseChapterEditLock } = makeServices(
        [current], { [current.id]: [asset("lock-refresh-failure-asset", current.id)] }, false, true,
      );
      const ownedLock: ChapterEditLockState = {
        locked: true,
        user_id: "demo-user",
        username: "演示创作者",
        is_mine: true,
        acquired_at: "2026-10-15T10:00:00Z",
        expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      };
      getChapterEditLock
        .mockResolvedValueOnce({ locked: false })
        .mockResolvedValueOnce(ownedLock);
      if (failurePoint === "release") {
        releaseChapterEditLock.mockRejectedValueOnce(new ApiError("network", "release was not confirmed"));
        getChapterEditLock.mockResolvedValueOnce(ownedLock);
      } else {
        getChapterEditLock.mockRejectedValueOnce(new ApiError("network", "lock state was not confirmed"));
      }
      const teamSeries = { ...series, team_id: `team-lock-refresh-${failurePoint}` };
      renderBrowser(services, vi.fn(), vi.fn(), teamSeries);

      await screen.findByText(/你在其他位置持有编辑锁/);
      const article = await screen.findByRole("article", { name: "释放失败保留锁提示 · 镜头 1" });
      await user.click(within(article).getByRole("button", { name: "编辑文字" }));
      await screen.findByRole("dialog", { name: "编辑分镜文字" });
      await user.click(screen.getByRole("button", { name: "关闭文字编辑" }));

      await waitFor(() => expect(releaseChapterEditLock).toHaveBeenCalledTimes(1));
      if (failurePoint === "release") {
        await waitFor(() => expect(getChapterEditLock).toHaveBeenCalledTimes(3));
        expect(getChapterEditLock.mock.calls[2]).toEqual([current.id, expect.any(AbortSignal)]);
      } else {
        await waitFor(() => expect(getChapterEditLock).toHaveBeenCalledTimes(3));
      }
      expect(screen.getByText(/你在其他位置持有编辑锁/)).toBeInTheDocument();
    },
  );

  it("ignores a late released-lock snapshot while a new chapter editor is opening", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-lock-refresh-late-first", "迟到锁章节", [
      { text: "迟到锁正文", storyboard: ["lock-refresh-late-first-asset"] },
    ], {
      lock: { locked: true, locked_by_username: "演示创作者", is_mine: true },
    });
    const second = chapter("chapter-lock-refresh-late-second", "新编辑章节", [
      { text: "新编辑正文", storyboard: ["lock-refresh-late-second-asset"] },
    ], { order: 2 });
    const pendingReleaseRead = deferred<ChapterEditLockState>();
    const pendingOpeningRead = deferred<ChapterEditLockState>();
    const { services, getChapterEditLock } = makeServices([first, second], {
      [first.id]: [asset("lock-refresh-late-first-asset", first.id)],
      [second.id]: [asset("lock-refresh-late-second-asset", second.id)],
    }, false, true);
    const firstLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    const secondLock: ChapterEditLockState = {
      ...firstLock,
      acquired_at: "2026-10-15T10:05:00Z",
    };
    let firstUnlockedReads = 0;
    getChapterEditLock.mockImplementation(async (chapterId, _signal, lease) => {
      if (chapterId === first.id) {
        if (lease !== undefined) return firstLock;
        firstUnlockedReads += 1;
        return firstUnlockedReads === 1 ? { locked: false } : pendingReleaseRead.promise;
      }
      if (chapterId === second.id) {
        if (lease !== undefined) return secondLock;
        return pendingOpeningRead.promise;
      }
      return { locked: false };
    });
    const teamSeries = { ...series, team_id: "team-lock-refresh-late" };
    renderBrowser(services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByText(/你在其他位置持有编辑锁/);
    await user.click(within(await screen.findByRole("article", { name: "迟到锁章节 · 镜头 1" }))
      .getByRole("button", { name: "编辑文字" }));
    await screen.findByRole("dialog", { name: "编辑分镜文字" });
    await user.click(screen.getByRole("button", { name: "关闭文字编辑" }));
    await waitFor(() => expect(getChapterEditLock).toHaveBeenCalledTimes(3));

    await user.click(screen.getByRole("button", { name: /新编辑章节/ }));
    await screen.findByText("新编辑正文");
    await user.click(within(await screen.findByRole("article", { name: "新编辑章节 · 镜头 1" }))
      .getByRole("button", { name: "编辑文字" }));
    await waitFor(() => expect(getChapterEditLock).toHaveBeenLastCalledWith(second.id, expect.any(AbortSignal)));

    await act(async () => {
      pendingReleaseRead.resolve({ locked: false });
      await pendingReleaseRead.promise;
    });
    await user.click(screen.getByRole("button", { name: /迟到锁章节/ }));
    await screen.findByText("迟到锁正文");
    await act(async () => {
      pendingOpeningRead.resolve({ locked: false });
      await pendingOpeningRead.promise;
    });

    expect(screen.getByText(/你在其他位置持有编辑锁/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "编辑分镜文字" })).not.toBeInTheDocument();
  });

  it("inserts after the selected frame, preserves neighboring payloads, and confirms stable IDs on reread", async () => {
    const user = userEvent.setup();
    const first = {
      text: "开场文字",
      original_text: "开场原文",
      storyboard: ["structure-first"],
      character: ["character-existing"],
      preview: "preview://keep",
      fused: { source: "preserve" },
      unknown_payload: { nested: ["untouched"] },
    };
    const second = { text: "后续文字", storyboard: ["structure-second"], future_flag: true };
    const current = chapter("chapter-structure-insert", "分镜结构插入", [first, second]);
    const { services, updateChapter, listStoryboardAssets, getCurrentChapters } = makeFrameStructureServices(current);
    const listCharacters = vi.spyOn(services, "listCharacters").mockResolvedValue([
      character("character-new", series.id, "林舟"),
    ]);
    renderBrowser(services);

    const firstArticle = await screen.findByRole("article", { name: "分镜结构插入 · 镜头 1" });
    await user.click(within(firstArticle).getByRole("button", { name: "在此镜头后插入" }));
    const dialog = within(await screen.findByRole("dialog", { name: "插入分镜" }));
    expect(await screen.findByLabelText("林舟")).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledWith(series.id, expect.any(AbortSignal));
    await user.type(dialog.getByRole("textbox", { name: "分镜文字" }), "新插入的镜头");
    await user.click(dialog.getByLabelText("林舟"));
    await user.click(dialog.getByRole("button", { name: "确认插入" }));

    await waitFor(() => expect(updateChapter).toHaveBeenCalledTimes(1));
    expect(updateChapter).toHaveBeenCalledWith(series.id, current.id, {
      content: [
        first,
        {
          text: "新插入的镜头",
          character: ["character-new"],
          scene: [],
          prop: [],
          storyboard: [],
          preview: null,
        },
        second,
      ],
    }, expect.any(AbortSignal));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "插入分镜" })).not.toBeInTheDocument());
    expect(getCurrentChapters()[0]?.content?.map(stableStoryboardId)).toEqual([
      "structure-first",
      `${current.id}-inserted-1`,
      "structure-second",
    ]);
    expect(services.getPersonalProductionNotes).toHaveBeenCalledTimes(1);
    expect(services.getPersonalRoughCut).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets.mock.calls.length).toBeGreaterThanOrEqual(4);
  });

  it("uses dedicated delete-frame and rereads shifted stable identities and historical notes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-delete", "分镜结构删除", [
      { text: "前镜", storyboard: ["structure-delete-first"], original_text: "保留原文一" },
      { text: "目标镜头", storyboard: ["structure-delete-target"], preview: "preview://target" },
      { text: "后镜", storyboard: ["structure-delete-last"], fused: { keep: true } },
    ]);
    const { services, updateChapter, deleteFrame, getCurrentChapters } = makeFrameStructureServices(
      current,
      "structure-delete-target",
    );
    renderBrowser(services);

    const targetArticle = await screen.findByRole("article", { name: "分镜结构删除 · 镜头 2" });
    await user.click(within(targetArticle).getByRole("button", { name: "删除镜头" }));
    const dialog = within(await screen.findByRole("dialog", { name: "删除分镜" }));
    await user.click(dialog.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(deleteFrame).toHaveBeenCalledTimes(1));
    expect(deleteFrame).toHaveBeenCalledWith(series.id, current.id, 1, expect.any(AbortSignal));
    expect(updateChapter).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "删除分镜" })).not.toBeInTheDocument());
    expect(getCurrentChapters()[0]?.content).toEqual([
      { text: "前镜", storyboard: ["structure-delete-first"], original_text: "保留原文一" },
      { text: "后镜", storyboard: ["structure-delete-last"], fused: { keep: true } },
    ]);
    expect(services.getPersonalProductionNotes).toHaveBeenCalledTimes(1);
    expect(services.getPersonalRoughCut).toHaveBeenCalledTimes(1);
  });

  it("keeps a delete draft and does not write when the target stable ID drifts before submit", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-drift", "删除身份漂移", [
      { text: "前镜", storyboard: ["structure-drift-first"] },
      { text: "目标镜头", storyboard: ["structure-drift-target"] },
      { text: "后镜", storyboard: ["structure-drift-last"] },
    ]);
    const { services, updateChapter, deleteFrame, setCurrentChapters, assetsByChapter } = makeFrameStructureServices(current);
    renderBrowser(services);

    const targetArticle = await screen.findByRole("article", { name: "删除身份漂移 · 镜头 2" });
    await user.click(within(targetArticle).getByRole("button", { name: "删除镜头" }));
    const dialog = within(await screen.findByRole("dialog", { name: "删除分镜" }));
    const drifted = {
      ...current,
      content: current.content?.map((frame, index) => index === 1
        ? { ...frame, storyboard: ["structure-drift-replacement"] }
        : frame) ?? null,
    };
    setCurrentChapters([drifted]);
    assetsByChapter[current.id] = [
      { ...asset("structure-drift-first", current.id), frame_index: 0 },
      { ...asset("structure-drift-replacement", current.id), frame_index: 1 },
      { ...asset("structure-drift-last", current.id), frame_index: 2 },
    ];

    await user.click(dialog.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByText(/章节内容或镜头顺序已变化；草稿保留/)).toBeInTheDocument();
    expect(deleteFrame).not.toHaveBeenCalled();
    expect(updateChapter).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "删除分镜" })).toBeInTheDocument();
  });

  it("keeps an insert draft and sends no write when a non-target asset position drifts", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-insert-position-drift", "插入位置漂移", [
      { text: "前镜", storyboard: ["insert-position-first"] },
      { text: "后镜", storyboard: ["insert-position-last"] },
    ]);
    const { services, updateChapter, assetsByChapter } = makeFrameStructureServices(current);
    renderBrowser(services);

    await screen.findByRole("heading", { name: "插入位置漂移" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "插入分镜" }));
    const textbox = dialog.getByRole("textbox", { name: "分镜文字" });
    await user.type(textbox, "草稿保留");
    const nonTargetAsset = assetsByChapter[current.id]?.find((value) => value.id === "insert-position-last");
    if (nonTargetAsset === undefined) throw new Error("Expected the non-target frame asset.");
    nonTargetAsset.frame_index = 8;

    await user.click(dialog.getByRole("button", { name: "确认插入" }));
    expect(await screen.findByText(/素材位置在编辑期间发生变化；未发送修改/)).toBeInTheDocument();
    expect(updateChapter).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "插入分镜" })).toBeInTheDocument();
    expect(textbox).toHaveValue("草稿保留");
  });

  it("keeps a delete draft and sends no delete when a non-target asset position drifts", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-delete-position-drift", "删除位置漂移", [
      { text: "前镜", storyboard: ["delete-position-first"] },
      { text: "目标镜头", storyboard: ["delete-position-target"] },
      { text: "后镜", storyboard: ["delete-position-last"] },
    ]);
    const { services, deleteFrame, updateChapter, assetsByChapter } = makeFrameStructureServices(current);
    renderBrowser(services);

    const targetArticle = await screen.findByRole("article", { name: "删除位置漂移 · 镜头 2" });
    await user.click(within(targetArticle).getByRole("button", { name: "删除镜头" }));
    const dialog = within(await screen.findByRole("dialog", { name: "删除分镜" }));
    const nonTargetAsset = assetsByChapter[current.id]?.find((value) => value.id === "delete-position-first");
    if (nonTargetAsset === undefined) throw new Error("Expected the non-target frame asset.");
    nonTargetAsset.frame_index = 8;

    await user.click(dialog.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByText(/素材位置在编辑期间发生变化；未发送修改/)).toBeInTheDocument();
    expect(deleteFrame).not.toHaveBeenCalled();
    expect(updateChapter).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog", { name: "删除分镜" })).toBeInTheDocument();
  });

  it("guards same-scope navigation until a dirty structure draft is discarded", async () => {
    const user = userEvent.setup();
    const first = chapter("chapter-draft-navigation-first", "草稿来源章节", [{ text: "首镜" }]);
    const target = chapter("chapter-draft-navigation-target", "导航目标章节", [{ text: "目标镜头" }], { order: 2 });
    const { services, listChapters } = makeFrameStructureServices(first);
    listChapters.mockImplementation(async () => [first, target]);
    const onAbandoned = vi.fn();
    const onBack = vi.fn();
    const onUnauthorized = vi.fn();
    let setNavigationIntent: ((intent: ChapterNavigationIntent | null) => void) | null = null;
    function NavigationHarness() {
      const [navigationIntent, updateNavigationIntent] = useState<ChapterNavigationIntent | null>(null);
      setNavigationIntent = updateNavigationIntent;
      return (
        <ChapterBrowser
          onBack={onBack}
          onNavigationAbandoned={onAbandoned}
          onUnauthorized={onUnauthorized}
          navigationIntent={navigationIntent}
          series={series}
          services={services}
          userId="demo-user"
        />
      );
    }
    render(<NavigationHarness />);

    await screen.findByRole("heading", { name: "草稿来源章节" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    const editor = within(await screen.findByRole("dialog", { name: "插入分镜" }));
    const textbox = editor.getByRole("textbox", { name: "分镜文字" });
    await user.type(textbox, "尚未保存的内容");

    const intent = (navigationEpoch: number): ChapterNavigationIntent => ({
      userId: "demo-user",
      services,
      seriesId: series.id,
      chapterId: target.id,
      navigationEpoch,
    });
    await act(async () => {
      setNavigationIntent?.(intent(701));
    });
    const firstConfirmation = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
    await user.click(within(firstConfirmation).getByRole("button", { name: "继续编辑" }));
    expect(onAbandoned).toHaveBeenCalledWith(701);
    expect(screen.getByRole("dialog", { name: "插入分镜" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "分镜文字" })).toHaveValue("尚未保存的内容");

    await act(async () => {
      setNavigationIntent?.(intent(702));
    });
    const secondConfirmation = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
    await user.click(within(secondConfirmation).getByRole("button", { name: "放弃草稿并离开" }));

    await screen.findByRole("heading", { name: "导航目标章节" });
    expect(screen.queryByRole("dialog", { name: "插入分镜" })).not.toBeInTheDocument();
  });

  it("freezes after a failed post-write read and retries by verification without resending", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-read-failure", "提交后重读失败", [
      { text: "前镜", storyboard: ["structure-read-first"] },
      { text: "后镜", storyboard: ["structure-read-last"] },
    ]);
    const { services, updateChapter } = makeFrameStructureServices(current);
    vi.spyOn(services, "getPersonalProductionNotes")
      .mockRejectedValueOnce(new ApiError("http", "snapshot unavailable", 503));
    renderBrowser(services);

    await screen.findByRole("heading", { name: "提交后重读失败" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "插入分镜" }));
    await user.type(dialog.getByRole("textbox", { name: "分镜文字" }), "新增镜头");
    await user.click(dialog.getByRole("button", { name: "确认插入" }));

    expect(await screen.findByText(/请求已发送，但章节、素材或制作映射无法重新读取/)).toBeInTheDocument();
    expect(updateChapter).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "确认插入" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "只核对服务器状态" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "插入分镜" })).not.toBeInTheDocument());
    expect(updateChapter).toHaveBeenCalledTimes(1);
    expect(services.getPersonalProductionNotes).toHaveBeenCalledTimes(2);
  });

  it("reloads the selected chapter and its assets after closing unknown-write verification", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-close-unknown", "关闭未知核对", [
      { text: "前镜", storyboard: ["close-unknown-first"] },
      { text: "后镜", storyboard: ["close-unknown-last"] },
    ]);
    const { services, updateChapter, listChapters, listStoryboardAssets } = makeFrameStructureServices(current);
    vi.spyOn(services, "getPersonalProductionNotes")
      .mockRejectedValueOnce(new ApiError("http", "snapshot unavailable", 503));
    renderBrowser(services);

    await screen.findByRole("heading", { name: "关闭未知核对" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "插入分镜" }));
    await user.type(dialog.getByRole("textbox", { name: "分镜文字" }), "待核对镜头");
    await user.click(dialog.getByRole("button", { name: "确认插入" }));
    expect(await screen.findByText(/请求已发送，但章节、素材或制作映射无法重新读取/)).toBeInTheDocument();
    const directoryReadsBeforeClose = listChapters.mock.calls.length;
    const assetReadsBeforeClose = listStoryboardAssets.mock.calls.length;

    await user.click(screen.getByRole("button", { name: "结束本次核对并关闭" }));
    const discard = await screen.findByRole("alertdialog", { name: "放弃分镜草稿？" });
    expect(discard).toHaveTextContent("原请求仍可能迟到完成");
    await user.click(within(discard).getByRole("button", { name: "放弃草稿并离开" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "插入分镜" })).not.toBeInTheDocument());
    await screen.findByText("写入结果仍未确认，章节与素材已重新读取；请核对当前状态。");
    await screen.findByRole("article", { name: "关闭未知核对 · 镜头 1" });
    await waitFor(() => expect(listChapters.mock.calls.length).toBeGreaterThan(directoryReadsBeforeClose));
    await waitFor(() => expect(listStoryboardAssets.mock.calls.length).toBeGreaterThan(assetReadsBeforeClose));
    expect(updateChapter).toHaveBeenCalledTimes(1);
  });

  it("keeps an ambiguous delete frozen when a baseline read precedes a delayed write", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-delayed-delete", "迟到删除核对", [
      { text: "前镜", storyboard: ["structure-delayed-first"] },
      { text: "目标镜头", storyboard: ["structure-delayed-target"] },
      { text: "后镜", storyboard: ["structure-delayed-last"] },
    ]);
    const { services, deleteFrame, getCurrentChapters, setCurrentChapters, assetsByChapter } = makeFrameStructureServices(
      current,
      "structure-delayed-target",
    );
    deleteFrame.mockRejectedValueOnce(new ApiError("network", "connection lost", null));
    renderBrowser(services);

    const targetArticle = await screen.findByRole("article", { name: "迟到删除核对 · 镜头 2" });
    await user.click(within(targetArticle).getByRole("button", { name: "删除镜头" }));
    const dialog = within(await screen.findByRole("dialog", { name: "删除分镜" }));
    await user.click(dialog.getByRole("button", { name: "确认删除" }));

    expect(await screen.findByText(/无法证明请求不会迟到落库/)).toBeInTheDocument();
    expect(deleteFrame).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "只核对服务器状态" }));
    expect(await screen.findByText(/无法证明原请求不会迟到落库/)).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "删除分镜" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认删除" })).not.toBeInTheDocument();
    expect(deleteFrame).toHaveBeenCalledTimes(1);

    const baseline = getCurrentChapters()[0]!;
    const lateContent = baseline.content?.filter((_frame, index) => index !== 1) ?? [];
    setCurrentChapters([{ ...baseline, content: lateContent }]);
    assetsByChapter[current.id] = lateContent.flatMap((frame, frameIndex) => {
      const id = stableStoryboardId(frame);
      return id === null ? [] : [{ ...asset(id, current.id), frame_index: frameIndex }];
    });
    await user.click(screen.getByRole("button", { name: "只核对服务器状态" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "删除分镜" })).not.toBeInTheDocument());
    expect(deleteFrame).toHaveBeenCalledTimes(1);
    expect(getCurrentChapters()[0]?.content).toEqual([
      { text: "前镜", storyboard: ["structure-delayed-first"] },
      { text: "后镜", storyboard: ["structure-delayed-last"] },
    ]);
  });

  it("ignores a late readback 401 after the user scope changes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-late-401", "迟到401的分镜", [
      { text: "前镜", storyboard: ["structure-late-first"] },
      { text: "后镜", storyboard: ["structure-late-last"] },
    ]);
    const { services, updateChapter } = makeFrameStructureServices(current);
    const pendingNotes = deferred<PersonalProductionSnapshot>();
    vi.spyOn(services, "getPersonalProductionNotes").mockReturnValue(pendingNotes.promise);
    const onUnauthorized = vi.fn();
    const view = renderBrowser(services, vi.fn(), onUnauthorized);

    await screen.findByRole("heading", { name: "迟到401的分镜" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "插入分镜" }));
    await user.type(dialog.getByRole("textbox", { name: "分镜文字" }), "迟到结果镜头");
    await user.click(dialog.getByRole("button", { name: "确认插入" }));
    await waitFor(() => expect(services.getPersonalProductionNotes).toHaveBeenCalledTimes(1));
    expect(updateChapter).toHaveBeenCalledTimes(1);

    view.rerender(
      <ChapterBrowser
        onBack={view.onBack}
        onUnauthorized={onUnauthorized}
        series={series}
        services={services}
        userId="another-user"
      />,
    );
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "插入分镜" })).not.toBeInTheDocument());
    await act(async () => {
      pendingNotes.reject(new ApiError("http", "expired", 401));
      await pendingNotes.promise.catch(() => undefined);
    });
    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(updateChapter).toHaveBeenCalledTimes(1);
  });

  it("releases a newly acquired structure lock with an unleased refresh and keeps an existing lock", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-lock", "分镜团队锁", [
      { text: "前镜", storyboard: ["structure-lock-first"] },
      { text: "后镜", storyboard: ["structure-lock-last"] },
    ]);
    const newlyAcquired = makeFrameStructureServices(current);
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    newlyAcquired.getChapterEditLock.mockImplementation(async (_chapterId, _signal, lease) => (
      lease !== undefined ? ownedLock : { locked: false }
    ));
    newlyAcquired.releaseChapterEditLock.mockImplementation(async () => {
      return { message: "锁已释放" };
    });
    const teamSeries = { ...series, team_id: "team-structure-lock" };
    renderBrowser(newlyAcquired.services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByRole("heading", { name: "分镜团队锁" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    await screen.findByRole("dialog", { name: "插入分镜" });
    await user.click(screen.getByRole("button", { name: "关闭分镜结构编辑" }));
    await waitFor(() => expect(newlyAcquired.releaseChapterEditLock).toHaveBeenCalledTimes(1));
    expect(newlyAcquired.acquireChapterEditLock).toHaveBeenCalledTimes(1);
    expect(newlyAcquired.getChapterEditLock).toHaveBeenLastCalledWith(
      current.id,
      expect.any(AbortSignal),
    );
    expect(newlyAcquired.getCurrentChapters()[0]?.lock).toBeNull();

    cleanup();
    const existing = makeFrameStructureServices({
      ...current,
      id: "chapter-structure-existing-lock",
      title: "分镜已有本人锁",
    });
    existing.getChapterEditLock.mockResolvedValue(ownedLock);
    renderBrowser(existing.services, vi.fn(), vi.fn(), teamSeries);
    await screen.findByRole("heading", { name: "分镜已有本人锁" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    await screen.findByRole("dialog", { name: "插入分镜" });
    await user.click(screen.getByRole("button", { name: "关闭分镜结构编辑" }));
    expect(existing.acquireChapterEditLock).not.toHaveBeenCalled();
    expect(existing.releaseChapterEditLock).not.toHaveBeenCalled();
  });

  it("keeps a team lock without a proven lease and refreshes its badge on close", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-unproven-lock", "来源未证实的团队锁", [
      { text: "前镜", storyboard: ["unproven-lock-first"] },
      { text: "后镜", storyboard: ["unproven-lock-last"] },
    ]);
    const frame = makeFrameStructureServices(current);
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    frame.getChapterEditLock.mockResolvedValueOnce({ locked: false }).mockResolvedValue(ownedLock);
    frame.acquireChapterEditLock.mockResolvedValue({ state: ownedLock, lease: null });
    const teamSeries = { ...series, team_id: "team-unproven-lock" };
    renderBrowser(frame.services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByRole("heading", { name: "来源未证实的团队锁" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    expect(await screen.findByRole("dialog", { name: "插入分镜" })).toHaveTextContent("取得来源无法确认");
    await user.click(screen.getByRole("button", { name: "关闭分镜结构编辑" }));

    await waitFor(() => expect(frame.getChapterEditLock).toHaveBeenCalledTimes(3));
    expect(frame.releaseChapterEditLock).not.toHaveBeenCalled();
    const calls = frame.getChapterEditLock.mock.calls;
    expect(calls[1]).toHaveLength(2);
    expect(calls[2]).toHaveLength(2);
    expect(screen.getByText(/你在其他位置持有编辑锁/)).toBeInTheDocument();
  });

  it("keeps a structure draft and sends no write when an unleased team lock identity changes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-structure-lock-drift", "结构锁身份漂移", [
      { text: "已有镜头", storyboard: ["structure-lock-drift-first"] },
      { text: "后续镜头", storyboard: ["structure-lock-drift-last"] },
    ]);
    const frame = makeFrameStructureServices(current);
    const originalLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-16T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    const replacementLock: ChapterEditLockState = {
      ...originalLock,
      acquired_at: "2026-10-16T10:05:00Z",
      expires_at: new Date(Date.now() + 20 * 60_000).toISOString(),
    };
    frame.getChapterEditLock
      .mockResolvedValueOnce({ locked: false })
      .mockResolvedValueOnce(originalLock)
      .mockResolvedValueOnce(replacementLock);
    frame.acquireChapterEditLock.mockResolvedValue({ state: originalLock, lease: null });
    const teamSeries = { ...series, team_id: "team-structure-lock-drift" };
    renderBrowser(frame.services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByRole("heading", { name: "结构锁身份漂移" });
    await user.click(screen.getByRole("button", { name: "在开头插入分镜" }));
    const editor = within(await screen.findByRole("dialog", { name: "插入分镜" }));
    const draftText = "保留的结构草稿";
    await user.type(editor.getByRole("textbox", { name: "分镜文字" }), draftText);
    await user.click(editor.getByRole("button", { name: "确认插入" }));

    expect(await editor.findByText("提交前无法确认当前账号仍持有有效章节锁；草稿仍保留。")).toBeInTheDocument();
    expect(editor.getByRole("textbox", { name: "分镜文字" })).toHaveValue(draftText);
    expect(frame.updateChapter).not.toHaveBeenCalled();
    expect(frame.deleteFrame).not.toHaveBeenCalled();
    expect(frame.releaseChapterEditLock).not.toHaveBeenCalled();
    expect(frame.getChapterEditLock).toHaveBeenCalledTimes(3);
    expect(frame.getChapterEditLock.mock.calls.every((call) => call.length === 2)).toBe(true);
  });

  it("keeps a text draft and sends no write when an unleased team lock identity changes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-text-lock-drift", "文字锁身份漂移", [
      { text: "原始文字", storyboard: ["text-lock-drift-asset"] },
    ]);
    const frame = makeFrameStructureServices(current);
    const originalLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-16T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    const replacementLock: ChapterEditLockState = {
      ...originalLock,
      acquired_at: "2026-10-16T10:05:00Z",
      expires_at: new Date(Date.now() + 20 * 60_000).toISOString(),
    };
    frame.getChapterEditLock
      .mockResolvedValueOnce({ locked: false })
      .mockResolvedValueOnce(originalLock)
      .mockResolvedValueOnce(replacementLock);
    frame.acquireChapterEditLock.mockResolvedValue({ state: originalLock, lease: null });
    const teamSeries = { ...series, team_id: "team-text-lock-drift" };
    renderBrowser(frame.services, vi.fn(), vi.fn(), teamSeries);

    const article = await screen.findByRole("article", { name: "文字锁身份漂移 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const draftText = "保留的文字草稿";
    await user.clear(editor.getByRole("textbox", { name: "镜头文字" }));
    await user.type(editor.getByRole("textbox", { name: "镜头文字" }), draftText);
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    expect(await editor.findByRole("alert")).toHaveTextContent("保存前无法确认当前账号仍持有有效章节锁；草稿已保留。");
    expect(editor.getByRole("textbox", { name: "镜头文字" })).toHaveValue(draftText);
    expect(frame.updateChapter).not.toHaveBeenCalled();
    expect(frame.getChapterEditLock).toHaveBeenCalledTimes(3);
    expect(frame.getChapterEditLock.mock.calls.every((call) => call.length === 2)).toBe(true);
    expect(frame.releaseChapterEditLock).not.toHaveBeenCalled();
  });

  it("allows existing text editing with an unproven API lock without releasing it", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-text-unproven-lock", "文字沿用未证锁", [
      { text: "原始文字", storyboard: ["text-unproven-lock-asset"] },
    ]);
    const frame = makeFrameStructureServices(current);
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    frame.getChapterEditLock.mockResolvedValueOnce({ locked: false }).mockResolvedValue(ownedLock);
    frame.acquireChapterEditLock.mockResolvedValue({ state: ownedLock, lease: null });
    const teamSeries = { ...series, team_id: "team-text-unproven-lock" };
    renderBrowser(frame.services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByRole("heading", { name: "文字沿用未证锁" });
    const article = await screen.findByRole("article", { name: "文字沿用未证锁 · 镜头 1" });
    await user.click(within(article).getByRole("button", { name: "编辑文字" }));
    const editor = within(await screen.findByRole("dialog", { name: "编辑分镜文字" }));
    const textbox = editor.getByRole("textbox", { name: "镜头文字" });
    await user.clear(textbox);
    await user.type(textbox, "保存新文字");
    await user.click(editor.getByRole("button", { name: "保存文字" }));

    await screen.findByText("保存新文字");
    expect(frame.updateChapter).toHaveBeenCalledTimes(1);
    expect(frame.releaseChapterEditLock).not.toHaveBeenCalled();
    expect(frame.getChapterEditLock.mock.calls.every((call) => call.length === 2)).toBe(true);
  });

  it("restores selected originals only after confirming the team lock and a complete source readback", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-optimize-restore", "恢复原文章节", [
      {
        text: "服务器优化文字",
        original_text: "保留的原始文字",
        storyboard: ["optimize-restore-asset"],
        editor_note: "保留其它字段",
      },
      { text: "相邻镜头", storyboard: ["optimize-restore-neighbor"] },
    ]);
    const { services, updateChapter, getChapterEditLock, acquireChapterEditLock, releaseChapterEditLock, getCurrentChapters, setCurrentChapters, assetsByChapter } = makeServices(
      [current],
      { [current.id]: [
        { ...asset("optimize-restore-asset", current.id), frame_index: 0 },
        { ...asset("optimize-restore-neighbor", current.id), frame_index: 1 },
      ] },
      false,
      true,
    );
    attachFrameOptimizeServices(services);
    updateChapter.mockImplementation(async (seriesId, chapterId, update) => {
      const updated = getCurrentChapters().map((row) => row.id === chapterId ? { ...row, ...update } : row);
      setCurrentChapters(updated);
      const result = updated.find((row) => row.id === chapterId);
      if (result === undefined || result.series_id !== seriesId) throw new ApiError("http", "missing", 404);
      const frames = Array.isArray(update.content) ? update.content as Array<Record<string, unknown>> : [];
      const expectedById = new Map<string, { frame: Record<string, unknown>; index: number }>();
      frames.forEach((value, index) => {
        const refs = value.storyboard;
        if (Array.isArray(refs) && typeof refs[0] === "string") {
          expectedById.set(refs[0], { frame: value, index });
        }
      });
      assetsByChapter[chapterId] = (assetsByChapter[chapterId] ?? []).flatMap((sourceAsset) => {
        const expected = expectedById.get(sourceAsset.id);
        if (expected === undefined) return [];
        const description = expected.frame.text || expected.frame.original_text || "";
        return [{
          ...sourceAsset,
          frame_index: expected.index,
          name: `分镜 ${expected.index + 1}`,
          description: typeof description === "string" ? description : "",
          updated_at: "2026-10-23T12:00:00Z",
        }];
      });
      return result;
    });
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    getChapterEditLock.mockImplementation(async (_chapterId, _signal, lease) => (
      lease === undefined ? { locked: false } : ownedLock
    ));
    const teamSeries = { ...series, team_id: "team-optimize-restore" };
    renderBrowser(services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByRole("heading", { name: "恢复原文章节" });
    await user.click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    await user.click(dialog.getByRole("button", { name: "恢复所选镜头原文" }));

    expect(await dialog.findByText("所选原文已恢复；章节与素材重新读取后通过完整内容核对。")).toBeInTheDocument();
    expect(acquireChapterEditLock).toHaveBeenCalledTimes(1);
    expect(getChapterEditLock).toHaveBeenLastCalledWith(current.id, expect.any(AbortSignal), expect.any(Object));
    expect(updateChapter).toHaveBeenCalledTimes(1);
    expect(updateChapter).toHaveBeenCalledWith(series.id, current.id, {
      content: [
        { text: "保留的原始文字", storyboard: ["optimize-restore-asset"], editor_note: "保留其它字段" },
        { text: "相邻镜头", storyboard: ["optimize-restore-neighbor"] },
      ],
    }, expect.any(AbortSignal));
    expect(getCurrentChapters()[0]?.content).toEqual([
      { text: "保留的原始文字", storyboard: ["optimize-restore-asset"], editor_note: "保留其它字段" },
      { text: "相邻镜头", storyboard: ["optimize-restore-neighbor"] },
    ]);
    expect(assetsByChapter[current.id]).toMatchObject([
      { id: "optimize-restore-asset", name: "分镜 1", description: "保留的原始文字" },
      { id: "optimize-restore-neighbor", name: "分镜 2", description: "相邻镜头" },
    ]);
    expect(releaseChapterEditLock).toHaveBeenCalledTimes(1);
  });

  it("does not submit after the dialog source baseline changes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-optimize-source-drift", "优化源变化章节", [
      { text: "打开时原文", storyboard: ["optimize-source-drift-asset"] },
    ]);
    const frame = makeServices(
      [current],
      { [current.id]: [{ ...asset("optimize-source-drift-asset", current.id), frame_index: 0 }] },
      false,
      true,
    );
    const { frameOptimize } = attachFrameOptimizeServices(frame.services);
    renderBrowser(frame.services);

    await screen.findByRole("heading", { name: "优化源变化章节" });
    await user.click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    frame.setCurrentChapters([{
      ...current,
      content: [{ text: "外部改动后的文字", storyboard: ["optimize-source-drift-asset"] }],
    }]);
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 未保存原文" }));
    await user.click(dialog.getByRole("button", { name: "提交优化" }));
    expect(await dialog.findByText(/提交前发现章节文字、顺序或素材基线变化/)).toBeInTheDocument();
    expect(frameOptimize.submit).not.toHaveBeenCalled();
  });

  it("checks an applied partial result against the refreshed chapter and asset descriptions without writing it", async () => {
    const current = chapter("chapter-optimize-partial-readback", "部分结果读回章节", [
      { text: "提交时原文", storyboard: ["optimize-partial-readback-asset"], keep: "字段" },
    ]);
    const sourceAsset = { ...asset("optimize-partial-readback-asset", current.id), frame_index: 0 };
    const frame = makeServices([current], { [current.id]: [sourceAsset] }, false, true);
    const request: FrameOptimizeSubmitInput = {
      chapter_id: current.id,
      model_id: "system-text-model",
      prompt_id: "",
      enable_thinking: false,
      concurrency: 5,
      chapter_texts: ["【剧情片段】\n提交时原文"],
      frames: [{ index: 0, text: "提交时原文", asset_context: "" }],
    };
    const status: FrameOptimizeTaskStatus = {
      task_id: "partial-readback-task",
      status: "failed",
      progress: 100,
      progress_message: "其它镜头失败",
      result: { partial: [{ index: 0, optimized_text: "已应用优化" }], error: "one_item_failed" },
    };
    Object.assign(frame.services, {
      getTaskRequest: vi.fn(async (taskId: string) => ({
        id: taskId,
        type: "batch-optimize",
        status: "processing",
        modelName: null,
        creditCost: null,
        externalTaskId: null,
        externalProvider: null,
        createdAt: null,
        request,
      })),
    });
    const { frameOptimize } = attachFrameOptimizeServices(frame.services, {
      getRunning: vi.fn()
        .mockResolvedValueOnce({
          task_id: status.task_id, status: "processing", progress: 50, progress_message: "处理中", created_at: null,
        })
        .mockResolvedValue(null),
      getTaskStatus: vi.fn(async () => {
        frame.setCurrentChapters([{
          ...current,
          content: [{ text: "已应用优化", original_text: "提交时原文", storyboard: ["optimize-partial-readback-asset"], keep: "字段" }],
        }]);
        frame.assetsByChapter[current.id] = [{ ...sourceAsset, description: "已应用优化", updated_at: "2026-10-23T00:00:00Z" }];
        return status;
      }),
    });
    renderBrowser(frame.services);

    await screen.findByRole("heading", { name: "部分结果读回章节" });
    await userEvent.setup().click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    expect(await dialog.findByText("任务已结束；章节与素材已重新读取，任务结果、原文和镜头身份均与服务端真源核对一致。"))
      .toBeInTheDocument();
    expect(dialog.getByText("终态：失败（可能含部分结果）")).toBeInTheDocument();
    expect(frameOptimize.submit).not.toHaveBeenCalled();
    expect(frame.updateChapter).not.toHaveBeenCalled();
    expect(frame.getCurrentChapters()[0]?.content).toEqual([
      { text: "已应用优化", original_text: "提交时原文", storyboard: ["optimize-partial-readback-asset"], keep: "字段" },
    ]);
  });

  it("verifies a completed projection array against fresh chapter and assets without writing task output", async () => {
    const current = chapter("chapter-optimize-array-readback", "成功数组读回章节", [
      { text: "提交时原文", storyboard: ["optimize-array-readback-asset"], keep: "字段" },
    ]);
    const sourceAsset = { ...asset("optimize-array-readback-asset", current.id), frame_index: 0 };
    const frame = makeServices([current], { [current.id]: [sourceAsset] }, false, true);
    const request: FrameOptimizeSubmitInput = {
      chapter_id: current.id,
      model_id: "system-text-model",
      prompt_id: "",
      enable_thinking: false,
      concurrency: 5,
      chapter_texts: ["【剧情片段】\n提交时原文"],
      frames: [{ index: 0, text: "提交时原文", asset_context: "" }],
    };
    const processingWire = frameOptimizeStatusWire.cases.processingArray.wire;
    const completedWire = frameOptimizeStatusWire.cases.completedArray.wire;
    let statusReads = 0;
    Object.assign(frame.services, {
      getTaskRequest: vi.fn(async (taskId: string) => ({
        id: taskId,
        type: "batch-optimize",
        status: "processing",
        modelName: null,
        creditCost: null,
        externalTaskId: null,
        externalProvider: null,
        createdAt: null,
        request,
      })),
    });
    const { frameOptimize } = attachFrameOptimizeServices(frame.services, {
      getRunning: vi.fn()
        .mockResolvedValueOnce({
          task_id: processingWire.task_id, status: "processing", progress: 45, progress_message: "已应用1项", created_at: null,
        })
        .mockResolvedValue(null),
      getTaskStatus: vi.fn(async (taskId: string) => {
        const wire = statusReads++ === 0 ? processingWire : completedWire;
        if (wire.status === "completed") {
          frame.setCurrentChapters([{
            ...current,
            content: [{
              text: "合成成功文字", original_text: "提交时原文",
              storyboard: ["optimize-array-readback-asset"], keep: "字段",
            }],
          }]);
          frame.assetsByChapter[current.id] = [{
            ...sourceAsset, description: "合成成功文字", updated_at: "2026-10-23T00:00:00Z",
          }];
        }
        return parseFrameOptimizeTaskStatus(wire, taskId);
      }),
    });
    renderBrowser(frame.services);

    await screen.findByRole("heading", { name: "成功数组读回章节" });
    await userEvent.setup().click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await waitFor(() => expect(dialog.getByText(
      "任务已结束；章节与素材已重新读取，任务结果、原文和镜头身份均与服务端真源核对一致。",
    )).toBeInTheDocument(), { timeout: 4000 });
    expect(dialog.getByText("终态：完成")).toBeInTheDocument();
    expect(dialog.getByRole("region", { name: "任务返回的分镜结果" })).toHaveTextContent("镜头 1：合成成功文字");
    expect(statusReads).toBe(2);
    expect(frameOptimize.submit).not.toHaveBeenCalled();
    expect(frame.updateChapter).not.toHaveBeenCalled();
    expect(frame.getCurrentChapters()[0]?.content).toEqual([{
      text: "合成成功文字", original_text: "提交时原文",
      storyboard: ["optimize-array-readback-asset"], keep: "字段",
    }]);
  });

  it("observes a same-account legacy batch task read-only and refreshes the chapter on terminal status", async () => {
    const current = chapter("chapter-optimize-legacy-readback", "旧版任务读回章节", [
      { text: "提交时原文", storyboard: ["optimize-legacy-readback-asset"] },
    ]);
    const sourceAsset = { ...asset("optimize-legacy-readback-asset", current.id), frame_index: 0 };
    const frame = makeServices([current], { [current.id]: [sourceAsset] }, false, true);
    Object.assign(frame.services, {
      getTaskRequest: vi.fn(async (taskId: string) => ({
        id: taskId,
        type: "batch-optimize",
        status: "processing",
        modelName: null,
        creditCost: null,
        externalTaskId: null,
        externalProvider: null,
        createdAt: null,
        request: { chapter_id: current.id, frame_count: 1 },
      })),
    });
    const { frameOptimize } = attachFrameOptimizeServices(frame.services, {
      getRunning: vi.fn()
        .mockResolvedValueOnce({
          task_id: "legacy-readback-task", status: "processing", progress: 50, progress_message: "旧任务处理中", created_at: null,
        })
        .mockResolvedValue(null),
      getTaskStatus: vi.fn(async () => {
        frame.setCurrentChapters([{
          ...current,
          content: [{ text: "旧任务已应用文字", original_text: "提交时原文", storyboard: ["optimize-legacy-readback-asset"] }],
        }]);
        frame.assetsByChapter[current.id] = [{ ...sourceAsset, description: "旧任务已应用文字", updated_at: "2026-10-23T00:00:00Z" }];
        return {
          task_id: "legacy-readback-task",
          status: "failed",
          progress: 100,
          progress_message: "旧任务有部分成功",
          result: { partial: [{ index: 0, optimized_text: "旧任务已应用文字" }], error: "legacy_failure" },
        };
      }),
    });
    renderBrowser(frame.services);

    await screen.findByRole("heading", { name: "旧版任务读回章节" });
    await userEvent.setup().click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialogElement = await screen.findByRole("dialog", { name: "批量优化分镜" });
    const dialog = within(dialogElement);
    expect(await dialog.findByText("旧版任务已结束；章节与素材已重新读取，旧任务输出未用于写入或判断章节内容。"))
      .toBeInTheDocument();
    expect(dialogElement).toHaveTextContent("仅提供状态读取和取消请求");
    expect(frame.getCurrentChapters()[0]?.content?.[0]).toMatchObject({
      text: "旧任务已应用文字",
      original_text: "提交时原文",
    });
    expect(frameOptimize.submit).not.toHaveBeenCalled();
    expect(frame.updateChapter).not.toHaveBeenCalled();
  });

  it("ignores a late frame-optimize capability 401 after the user scope changes", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-optimize-scope-change", "切换优化范围章节", [
      { text: "待优化文字", storyboard: ["optimize-scope-change-asset"] },
    ]);
    const frame = makeServices(
      [current],
      { [current.id]: [{ ...asset("optimize-scope-change-asset", current.id), frame_index: 0 }] },
      false,
      true,
    );
    const pendingCapabilities = deferred<FrameOptimizeCapabilities>();
    const { frameOptimize } = attachFrameOptimizeServices(frame.services, {
      getCapabilities: vi.fn(() => pendingCapabilities.promise),
    });
    const onUnauthorized = vi.fn();
    const view = renderBrowser(frame.services, vi.fn(), onUnauthorized);

    await screen.findByRole("heading", { name: "切换优化范围章节" });
    await user.click(screen.getByRole("button", { name: "批量优化分镜" }));
    await screen.findByRole("dialog", { name: "批量优化分镜" });
    view.rerender(
      <ChapterBrowser
        onBack={view.onBack}
        onUnauthorized={onUnauthorized}
        series={series}
        services={frame.services}
        userId="another-user"
      />,
    );
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "批量优化分镜" })).not.toBeInTheDocument());
    await act(async () => {
      pendingCapabilities.reject(new ApiError("http", "expired", 401));
      await pendingCapabilities.promise.catch(() => undefined);
    });

    expect(frameOptimize.getCapabilities).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("freezes an ambiguous original-text restore and never sends it twice", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-optimize-restore-unknown", "恢复结果不明章节", [
      { text: "当前优化文字", original_text: "需要恢复的原文", storyboard: ["optimize-restore-unknown-asset"] },
    ]);
    const frame = makeServices(
      [current],
      { [current.id]: [{ ...asset("optimize-restore-unknown-asset", current.id), frame_index: 0 }] },
      false,
      true,
    );
    attachFrameOptimizeServices(frame.services);
    frame.updateChapter.mockRejectedValueOnce(new ApiError("network", "connection lost"));
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    frame.getChapterEditLock.mockImplementation(async (_chapterId, _signal, lease) => (
      lease === undefined ? { locked: false } : ownedLock
    ));
    const teamSeries = { ...series, team_id: "team-optimize-restore-unknown" };
    renderBrowser(frame.services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByRole("heading", { name: "恢复结果不明章节" });
    await user.click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    await user.click(dialog.getByRole("button", { name: "恢复所选镜头原文" }));
    expect(await dialog.findByText("恢复请求可能已生效，但读回内容与唯一预期不一致；已冻结再次写入，请人工核对服务器真源。")).toBeInTheDocument();
    expect(frame.updateChapter).toHaveBeenCalledTimes(1);
    expect(dialog.queryByRole("button", { name: "恢复所选镜头原文" })).not.toBeInTheDocument();
  });

  it("rechecks running optimization after acquiring the team lock before restoring originals", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-optimize-active-before-write", "锁后活任务章节", [
      { text: "优化文字", original_text: "原文", storyboard: ["optimize-active-before-write-asset"] },
    ]);
    const frame = makeServices(
      [current],
      { [current.id]: [{ ...asset("optimize-active-before-write-asset", current.id), frame_index: 0 }] },
      false,
      true,
    );
    const running = {
      task_id: "active-optimization", status: "processing", progress: 10,
      progress_message: "处理中", created_at: null,
    };
    const getRunning = vi.fn()
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce(running);
    attachFrameOptimizeServices(frame.services, { getRunning });
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    frame.getChapterEditLock.mockImplementation(async (_chapterId, _signal, lease) => (
      lease === undefined ? { locked: false } : ownedLock
    ));
    const teamSeries = { ...series, team_id: "team-optimize-active-before-write" };
    renderBrowser(frame.services, vi.fn(), vi.fn(), teamSeries);

    await screen.findByRole("heading", { name: "锁后活任务章节" });
    await user.click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    await user.click(dialog.getByRole("button", { name: "恢复所选镜头原文" }));

    expect(await dialog.findByText("本章节存在运行中的分镜优化任务；原文恢复已停止，请先核对任务终态。")).toBeInTheDocument();
    expect(getRunning).toHaveBeenCalledTimes(3);
    expect(frame.updateChapter).not.toHaveBeenCalled();
    expect(frame.releaseChapterEditLock).toHaveBeenCalledTimes(1);
  });

  it("does not log out a new account when an old-scope restore receives a late unauthorized write", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-optimize-late-401", "迟到401章节", [
      { text: "优化文字", original_text: "原文", storyboard: ["optimize-late-401-asset"] },
    ]);
    const frame = makeServices(
      [current],
      { [current.id]: [{ ...asset("optimize-late-401-asset", current.id), frame_index: 0 }] },
      false,
      true,
    );
    attachFrameOptimizeServices(frame.services);
    const pendingWrite = deferred<Chapter>();
    frame.updateChapter.mockImplementation(() => pendingWrite.promise);
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: "2026-10-15T10:00:00Z",
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    frame.getChapterEditLock.mockImplementation(async (_chapterId, _signal, lease) => (
      lease === undefined ? { locked: false } : ownedLock
    ));
    const onUnauthorized = vi.fn();
    const view = renderBrowser(frame.services, vi.fn(), onUnauthorized, { ...series, team_id: "team-optimize-late-401" });

    await screen.findByRole("heading", { name: "迟到401章节" });
    await user.click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    await user.click(dialog.getByRole("button", { name: "恢复所选镜头原文" }));
    await waitFor(() => expect(frame.updateChapter).toHaveBeenCalledTimes(1));

    view.rerender(
      <ChapterBrowser
        onBack={view.onBack}
        onUnauthorized={onUnauthorized}
        series={{ ...series, team_id: "team-optimize-late-401" }}
        services={frame.services}
        userId="another-user"
      />,
    );
    await act(async () => {
      pendingWrite.reject(new ApiError("http", "expired", 401));
      await pendingWrite.promise.catch(() => undefined);
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(frame.releaseChapterEditLock).toHaveBeenCalledTimes(1);
  });

  it("does not refresh the new scope asset cache from a late restore conflict read", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-optimize-late-read", "迟到读回章节", [
      { text: "优化文字", original_text: "原文", storyboard: ["optimize-late-read-asset"] },
    ]);
    const oldAsset = { ...asset("optimize-late-read-asset", current.id), frame_index: 0 };
    const newScopeAsset = { ...oldAsset, description: "new-scope-description", updated_at: "new-scope" };
    const frame = makeServices([current], { [current.id]: [oldAsset] }, false, true);
    attachFrameOptimizeServices(frame.services);
    const pendingLatestDirectory = deferred<Chapter[]>();
    const pendingLatestAssets = deferred<StoryboardAsset[]>();
    let lockAcquired = false;
    let lockedDirectoryReadStarted = false;
    let lockedAssetsReadStarted = false;
    frame.acquireChapterEditLock.mockImplementation(async () => {
      lockAcquired = true;
      return {
        state: {
          locked: true,
          user_id: "demo-user",
          username: "演示创作者",
          is_mine: true,
          acquired_at: "2026-10-15T10:00:00Z",
          expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
        },
        lease: {} as ChapterEditLockLeaseHandle,
      };
    });
    frame.getChapterEditLock.mockImplementation(async (_chapterId, _signal, lease) => (
      lease === undefined ? { locked: false } : {
        locked: true,
        user_id: "demo-user",
        username: "演示创作者",
        is_mine: true,
        acquired_at: "2026-10-15T10:00:00Z",
        expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
      }
    ));
    frame.listChapters.mockImplementation(async () => {
      if (lockAcquired && !lockedDirectoryReadStarted) {
        lockedDirectoryReadStarted = true;
        return pendingLatestDirectory.promise;
      }
      return frame.getCurrentChapters();
    });
    frame.listStoryboardAssets.mockImplementation(async (_seriesId, chapterId) => {
      if (lockAcquired && lockedDirectoryReadStarted && !lockedAssetsReadStarted) {
        lockedAssetsReadStarted = true;
        return pendingLatestAssets.promise;
      }
      return frame.assetsByChapter[chapterId] ?? [];
    });
    const view = renderBrowser(frame.services, vi.fn(), vi.fn(), { ...series, team_id: "team-optimize-late-read" });

    await screen.findByRole("heading", { name: "迟到读回章节" });
    await user.click(screen.getByRole("button", { name: "批量优化分镜" }));
    const dialog = within(await screen.findByRole("dialog", { name: "批量优化分镜" }));
    await user.click(dialog.getByRole("checkbox", { name: "镜头 1 · 已有原文" }));
    await user.click(dialog.getByRole("button", { name: "恢复所选镜头原文" }));
    await waitFor(() => expect(lockedDirectoryReadStarted && lockedAssetsReadStarted).toBe(true));

    frame.assetsByChapter[current.id] = [newScopeAsset];
    view.rerender(
      <ChapterBrowser
        onBack={view.onBack}
        onUnauthorized={view.onUnauthorized}
        series={{ ...series, team_id: "team-optimize-late-read" }}
        services={frame.services}
        userId="another-user"
      />,
    );
    await screen.findByRole("heading", { name: "迟到读回章节" });
    await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
    await waitFor(() => expect(personalNotesBridge.assets[0]?.description).toBe("new-scope-description"));

    await act(async () => {
      pendingLatestDirectory.resolve([{
        ...current,
        content: [{ text: "另一范围的旧读回", storyboard: [oldAsset.id] }],
      }]);
      pendingLatestAssets.resolve([oldAsset]);
      await Promise.all([pendingLatestDirectory.promise, pendingLatestAssets.promise]);
      await Promise.resolve();
    });

    expect(personalNotesBridge.assets).toEqual([newScopeAsset]);
  });

  it("aggregates chapter omissions and locates only the original unique storyboard identity", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-production-checklist", "制作缺项章节", [
      {
        text: "镜头一",
        storyboard: ["checklist-frame-1"],
        character: ["missing-character"],
        fused: ["fused-with-empty-name"],
      },
      { text: "镜头二", storyboard: ["checklist-frame-2"], character: ["missing-character"] },
    ]);
    const firstFrameAsset = { ...asset("checklist-frame-1", current.id), frame_index: 0 };
    const secondFrameAsset = { ...asset("checklist-frame-2", current.id), frame_index: 1 };
    const frame = makeServices([current], { [current.id]: [firstFrameAsset, secondFrameAsset] });
    let fusedAssetsUrl = "";
    const authenticatedFusedServices = createApiServices(apiBaseUrl, {
      storage: {
        getItem: (key) => key === API_TOKEN_KEY ? "checklist-token" : null,
        setItem: () => undefined,
        removeItem: () => undefined,
      },
      fetcher: async (input) => {
        fusedAssetsUrl = String(input);
        return new Response(JSON.stringify([{
          id: "fused-with-empty-name",
          series_id: series.id,
          member_type: "",
          members: null,
          name: "",
          image_url: null,
          size: null,
          status: "",
          error_message: null,
          created_at: "2026-10-01T00:00:00",
          updated_at: "2026-10-02T00:00:00",
        }]), { status: 200, headers: { "Content-Type": "application/json" } });
      },
    });
    frame.services.listFusedAssets = authenticatedFusedServices.listFusedAssets!;
    renderBrowser(frame.services);

    await screen.findByRole("heading", { name: "制作缺项章节" });
    await user.click(screen.getByRole("button", { name: "制作缺项" }));

    const issue = await screen.findByText(
      "角色「未命名素材」：引用未在当前已读取资产中找到（关联：分镜 1、分镜 2）",
    );
    expect(await screen.findByText("融合素材「未命名素材」：缺少图片（关联：分镜 1）")).toBeTruthy();
    const issueRow = issue.closest("li");
    expect(issueRow).not.toBeNull();
    await waitFor(() => expect(fusedAssetsUrl).toBe(`${apiBaseUrl}/series/${series.id}/fused-assets`));
    await user.click(within(issueRow!).getByRole("button", { name: "定位分镜" }));

    expect(document.activeElement).toBe(screen.getByLabelText("制作缺项章节 · 镜头 1"));
  });

  it("lets a note and resume locator reveal their original off-page frame", async () => {
    const user = userEvent.setup();
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const targetPosition = 11;
    const browseUserId = "notes-cross-page-user";
    const imageUrl = "http://127.0.0.1:4175/media/cross-page-note.png";
    const imageDigest = await digestPersonalProductionMediaIdentity(imageUrl);
    if (imageDigest === null) throw new Error("The local image identity should produce a digest.");
    const current = chapter("chapter-notes-cross-page", "跨页记录章节", [
      ...Array.from({ length: targetPosition - 1 }, (_, index) => ({
        text: `前置镜头 ${index + 1}`,
        storyboard: [`note-cross-page-filler-${index + 1}`],
      })),
      { text: "跨页目标镜头", storyboard: ["note-cross-page-target"] },
    ]);
    seedStoryboardPageSize(browseUserId, current.id);
    const { services, listStoryboardAssets } = makeServices([current], {
      [current.id]: [asset("note-cross-page-target", current.id, imageUrl)],
    });
    const missingFrames = Array.from({ length: targetPosition - 1 }, (_, index) => ({
      frame_index: index,
      storyboard_asset_id: null,
      media_revision: null,
      source_valid: false,
      asset_image_digest: null,
      preview_digest: null,
      invalid_reason: "missing" as const,
    }));
    vi.spyOn(services, "getPersonalProductionNotes").mockResolvedValue(parsePersonalProductionSnapshot({
      chapter_id: current.id,
      revision: 1,
      media_state: "ready",
      frames: [
        ...missingFrames,
        {
          frame_index: targetPosition - 1,
          storyboard_asset_id: "note-cross-page-target",
          media_revision: 1,
          source_valid: true,
          asset_image_digest: imageDigest,
          preview_digest: null,
          invalid_reason: null,
        },
      ],
      frame_notes: { "note-cross-page-target": { status: "needs_revision", note: "跨页制作记录" } },
      resume_frame_id: "note-cross-page-target",
    }, current.id));
    renderBrowser(services, vi.fn(), vi.fn(), series, browseUserId);

    await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByText("跨页制作记录")).toBeInTheDocument();
    const noteLocator = screen.getByRole("button", { name: "定位到记录镜头 11" });
    const queuedFrames = new Set<number>();
    const cancelledFrames = new Set<number>();
    let nextFrameId = 0;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => {
      const id = ++nextFrameId;
      queuedFrames.add(id);
      return id;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      cancelledFrames.add(id);
      queuedFrames.delete(id);
    });
    await user.selectOptions(screen.getByRole("combobox", { name: "每页分镜" }), "20");
    const queuedBeforeLocate = [...queuedFrames];
    expect(queuedBeforeLocate.length).toBeGreaterThan(0);
    expect(screen.getByRole("article", { name: "跨页记录章节 · 镜头 11" })).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(noteLocator);
      fireEvent.click(screen.getByRole("button", { name: "定位到续作镜头" }));
    });
    expect(queuedBeforeLocate.some((id) => cancelledFrames.has(id))).toBe(true);
    await waitFor(() => expect(noteLocator).toBeEnabled());
    expect(screen.queryByText("未能定位当前记录镜头，请确认镜头仍可见后重试。"))
      .not.toBeInTheDocument();
    expect(screen.getByText("已定位到续作镜头 11。")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "每页分镜" }), "10");
    await user.click(noteLocator);
    const target = await screen.findByRole("article", { name: "跨页记录章节 · 镜头 11" });
    await waitFor(() => expect(target).toHaveFocus());
    expect(screen.getByText("已定位到记录镜头 11。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "上一页分镜" }));
    expect(await screen.findByRole("article", { name: "跨页记录章节 · 镜头 1" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "定位到续作镜头" }));
    const resumedTarget = await screen.findByRole("article", { name: "跨页记录章节 · 镜头 11" });
    await waitFor(() => expect(resumedTarget).toHaveFocus());
    expect(screen.getByText("已定位到续作镜头 11。")).toBeInTheDocument();
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    scrollTo.mockRestore();
  });

  it("lets the rough-cut locator reveal its stable frame position on a later page", async () => {
    const user = userEvent.setup();
    const targetPosition = 22;
    const browseUserId = "rough-cut-cross-page-user";
    const current = chapter("chapter-rough-cut-cross-page", "跨页粗剪章节", [
      ...Array.from({ length: targetPosition - 1 }, (_, index) => ({
        text: `前置粗剪镜头 ${index + 1}`,
        storyboard: [`rough-cut-cross-page-filler-${index + 1}`],
      })),
      { text: "粗剪目标镜头", storyboard: ["rough-cut-cross-page-target"] },
    ]);
    seedStoryboardPageSize(browseUserId, current.id);
    const { services } = makeServices([current], {
      [current.id]: [{ ...asset("rough-cut-cross-page-target", current.id), frame_index: targetPosition - 1 }],
    });
    vi.spyOn(services, "getPersonalRoughCut").mockResolvedValue({
      chapter_id: current.id,
      revision: 1,
      saved: true,
      frames: [{
        asset_id: "rough-cut-cross-page-target",
        frame_index: 0,
        text: "粗剪快照中的目标",
        preview_url: null,
        missing_reason: "当前没有可用视频。",
        included: false,
        pending: true,
      }],
      removed_asset_ids: [],
    });
    renderBrowser(services, vi.fn(), vi.fn(), series, browseUserId);

    await user.click(await screen.findByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("粗剪快照中的目标")).toBeInTheDocument();
    const locate = await screen.findByRole("button", { name: "定位到对应镜头 22" });
    expect(screen.queryByRole("article", { name: "跨页粗剪章节 · 镜头 22" })).not.toBeInTheDocument();
    await user.click(locate);
    const target = await screen.findByRole("article", { name: "跨页粗剪章节 · 镜头 22" });
    await waitFor(() => expect(target).toHaveFocus());
    expect(screen.getByText("已定位到粗剪镜头 22。")).toBeInTheDocument();
  });

  it("lets the production checklist locator reveal the original storyboard frame", async () => {
    const user = userEvent.setup();
    const targetPosition = 11;
    const browseUserId = "checklist-cross-page-user";
    const current = chapter("chapter-checklist-cross-page", "跨页清单章节", [
      ...Array.from({ length: targetPosition - 1 }, (_, index) => ({
        text: `前置清单镜头 ${index + 1}`,
        storyboard: [`checklist-cross-page-filler-${index + 1}`],
      })),
      { text: "清单目标镜头", storyboard: ["checklist-cross-page-target"] },
    ]);
    seedStoryboardPageSize(browseUserId, current.id);
    const storyboardAssets = (current.content ?? []).map((_frame, index) => ({
      ...asset(index === targetPosition - 1
        ? "checklist-cross-page-target"
        : `checklist-cross-page-filler-${index + 1}`, current.id),
      frame_index: index,
    }));
    const { services } = makeServices([current], { [current.id]: storyboardAssets });
    renderBrowser(services, vi.fn(), vi.fn(), series, browseUserId);

    await user.click(await screen.findByRole("button", { name: "制作缺项" }));
    const issue = await screen.findByText("分镜 11：预览未配置");
    const issueRow = issue.closest("li");
    if (issueRow === null) throw new Error("The target checklist issue should have a row.");
    await user.click(within(issueRow).getByRole("button", { name: "定位分镜" }));
    const target = await screen.findByRole("article", { name: "跨页清单章节 · 镜头 11" });
    await waitFor(() => expect(target).toHaveFocus());
    expect(screen.getByText("已定位到关联分镜 11。")).toBeInTheDocument();
  });

  it("restores the scoped page and actual window scroll, then persists manual scrolling", async () => {
    const browseUserId = "browse-scroll-restore-user";
    const current = chapter("chapter-browse-scroll-restore", "滚动恢复章节", Array.from({ length: 23 }, (_, index) => ({
      text: `滚动章节镜头 ${index + 1}`,
      storyboard: [`scroll-frame-${index + 1}`],
    })));
    const browseScope = { userId: browseUserId, apiBaseUrl, seriesId: series.id, chapterId: current.id };
    window.localStorage.setItem(storyboardPageSizeStorageKey(browseScope), "10");
    window.localStorage.setItem(storyboardChapterStorageKey(browseScope), JSON.stringify({ page: 2, scrollTop: 460 }));
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    const originalScrollY = Object.getOwnPropertyDescriptor(window, "scrollY");
    Object.defineProperty(window, "scrollY", { configurable: true, value: 0 });

    try {
      const { services } = makeServices([current]);
      renderBrowser(services, vi.fn(), vi.fn(), series, browseUserId);
      expect(await screen.findByRole("article", { name: "滚动恢复章节 · 镜头 11" })).toBeInTheDocument();
      expect(screen.getByText(/第 2 \/ 3 页/)).toBeInTheDocument();
      await waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 460));

      Object.defineProperty(window, "scrollY", { configurable: true, value: 720 });
      act(() => window.dispatchEvent(new Event("scroll")));
      await waitFor(() => {
        const saved = JSON.parse(window.localStorage.getItem(storyboardChapterStorageKey(browseScope)) ?? "null") as {
          scrollTop?: number;
        } | null;
        expect(saved?.scrollTop).toBe(720);
      });
    } finally {
      scrollTo.mockRestore();
      if (originalScrollY === undefined) {
        Reflect.deleteProperty(window, "scrollY");
      } else {
        Object.defineProperty(window, "scrollY", originalScrollY);
      }
    }
  });

  it("keeps manual paging available when browse storage cannot be read or written", async () => {
    const user = userEvent.setup();
    const browseUserId = "unavailable-browse-storage-user";
    const current = chapter("chapter-browse-storage-unavailable", "存储不可用章节", Array.from({ length: 23 }, (_, index) => ({
      text: `可手动翻页镜头 ${index + 1}`,
      storyboard: [`storage-failure-frame-${index + 1}`],
    })));
    const { services } = makeServices([current]);
    const originalGetItem = Storage.prototype.getItem;
    const originalSetItem = Storage.prototype.setItem;
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(function (this: Storage, key: string) {
      if (key.startsWith("haoai:storyboard-browse:")) throw new Error("Storage read denied");
      return originalGetItem.call(this, key);
    });
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key: string, value: string) {
      if (key.startsWith("haoai:storyboard-browse:")) throw new Error("Storage write denied");
      originalSetItem.call(this, key, value);
    });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
    renderBrowser(services, vi.fn(), vi.fn(), series, browseUserId);

    expect(await screen.findByRole("article", { name: "存储不可用章节 · 镜头 1" })).toBeInTheDocument();
    expect(screen.getByText("无法读取浏览偏好；可继续手动翻页和滚动。")).toBeInTheDocument();
    await user.selectOptions(screen.getByRole("combobox", { name: "每页分镜" }), "10");
    await user.click(screen.getByRole("button", { name: "下一页分镜" }));
    expect(await screen.findByRole("article", { name: "存储不可用章节 · 镜头 11" })).toBeInTheDocument();
    expect(screen.getByText("浏览状态未能保存；本次翻页和定位仍可继续使用。")).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: "存储不可用章节 · 镜头 1" })).not.toBeInTheDocument();
    getItem.mockRestore();
    setItem.mockRestore();
    scrollTo.mockRestore();
  });

  it("durably records a guarded save before one POST and requires fresh target review before release", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const teamSeries = { ...series, team_id: "team-continuous-resolve" };
    const current = chapter("chapter-continuous-consumer", "连续编辑章节", [{
      text: "第一段正文",
      original_text: "打开时原文",
      storyboard: ["continuous-shot-1"],
      character: ["continuous-character-1"],
      scene: [],
      prop: [],
      editor_unknown: { retain: true },
    }]);
    const storyboardAsset = { ...asset("continuous-shot-1", current.id), frame_index: 0 };
    const base = makeServices([current], { [current.id]: [storyboardAsset] }, false, true);
    const ownedLock: ChapterEditLockState = {
      locked: true,
      user_id: "demo-user",
      username: "演示创作者",
      is_mine: true,
      acquired_at: new Date(Date.now() - 1_000).toISOString(),
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    };
    base.getChapterEditLock.mockImplementation(async (_chapterId, _signal, lease) => (
      lease === undefined ? { locked: false } : ownedLock
    ));
    const listCharacters = vi.fn(async () => [character("continuous-character-1", series.id, "门卫")]);
    base.services.listCharacters = listCharacters;
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const checkOrphans = vi.fn(async () => ({
      orphans: [{ type: "character" as const, id: "continuous-character-1" }],
    }));
    Object.assign(base.services, {
      assetMaintenance: { checkOrphans } as unknown as NonNullable<WorkspaceServices["assetMaintenance"]>,
    });
    const port = attachContinuousChapterOperationPort(base.services, async (chapterId, operationId, bodyJson) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save.");
      const result = { ...current, id: chapterId, content: request.command.content as unknown as Chapter["content"] };
      base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? result : row));
      return makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
    });
    const originalSelection = vi.fn();

    render(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        onContinuousOriginalChange={originalSelection}
        continuousPreviewActive
        workbenchActive
        series={teamSeries}
        services={base.services}
        userId="demo-user"
      />,
    );

    await screen.findByRole("region", { name: "整章连续编辑入口" });
    await user.click(screen.getByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    expect(base.services.getContinuousChapterEditCapabilities).toHaveBeenCalledOnce();
    expect(originalSelection).toHaveBeenLastCalledWith({ position: 1, status: "text", text: "打开时原文" });
    const text = within(editor).getByRole("textbox", { name: "第 1 段剧本文字" });
    await user.deselectOptions(within(editor).getByRole("listbox", { name: "第 1 段角色素材" }), "continuous-character-1");
    await user.clear(text);
    fireEvent.keyDown(text, { key: "z", ctrlKey: true });
    expect(text).toHaveValue("第一段正文");
    fireEvent.keyDown(text, { key: "z", ctrlKey: true, shiftKey: true });
    expect(text).toHaveValue("");
    await user.type(text, "改写后的正文");
    fireEvent.keyDown(text, { key: "s", ctrlKey: true });

    await waitFor(() => expect(port.submit).toHaveBeenCalledOnce());
    const [chapterId, operationId, bodyJson] = port.submit.mock.calls[0]!;
    const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
    expect(chapterId).toBe(current.id);
    expect(operationId).toMatch(/^[0-9a-f-]{36}$/u);
    expect(request.command).toMatchObject({ kind: "save-chapter" });
    if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save request.");
    expect(request.command.chapter_edit_guard).toMatchObject({
      expected_chapter_id: current.id,
      expected_series_id: series.id,
      expected_content: current.content,
      frame_sources: [{ kind: "retained", source_index: 0, storyboard_id: "continuous-shot-1" }],
      deleted_source_indexes: [],
    });
    expect(request.command.content[0]).toMatchObject({
      text: "改写后的正文",
      original_text: "打开时原文",
      editor_unknown: { retain: true },
      storyboard: ["continuous-shot-1"],
      character: [],
    });
    expect(base.updateChapter).not.toHaveBeenCalled();
    await waitFor(() => expect(checkOrphans).toHaveBeenCalledWith(series.id, {
      items: [{ type: "character", id: "continuous-character-1" }],
    }, expect.any(AbortSignal)));
    expect(await screen.findByRole("button", { name: "我已审阅已保存章节，解除本章冻结" })).toBeInTheDocument();
    const storageKey = continuousChapterOperationJournalKey({
      actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: current.id,
    });
    expect(JSON.parse(localStorage.getItem(storageKey) ?? "null").phase).toBe("receipt_committed_pending_target_read");

    await user.click(screen.getByRole("button", { name: "我已审阅已保存章节，解除本章冻结" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "连续编辑操作恢复" })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByRole("region", { name: "整章连续编辑" })).not.toBeInTheDocument());
    expect(localStorage.getItem(storageKey)).toBeNull();
    expect(base.releaseChapterEditLock).toHaveBeenCalledOnce();
    expect(port.submit).toHaveBeenCalledOnce();
  });

  it("changes the asset selection on the frame whose select control fired", async () => {
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-frame-select", "双镜头连续编辑", [
      { text: "第一段", storyboard: ["continuous-select-shot-1"], character: [], scene: [], prop: [] },
      { text: "第二段", storyboard: ["continuous-select-shot-2"], character: [], scene: [], prop: [] },
    ]);
    const storyboardAssets = [
      { ...asset("continuous-select-shot-1", current.id), frame_index: 0 },
      { ...asset("continuous-select-shot-2", current.id), frame_index: 1 },
    ];
    const base = makeServices([current], { [current.id]: storyboardAssets }, false, true);
    installContinuousChapterOperationLock();
    base.services.listCharacters = vi.fn(async () => [
      character("continuous-select-character-1", series.id, "第一段角色"),
      character("continuous-select-character-2", series.id, "第二段角色"),
    ]);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const port = attachContinuousChapterOperationPort(base.services, async (chapterId, operationId, bodyJson) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save.");
      const result = { ...current, id: chapterId, content: request.command.content as unknown as Chapter["content"] };
      base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? result : row));
      return makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
    });

    render(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="demo-user"
      />,
    );

    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    const firstSelection = within(editor).getByRole("listbox", { name: "第 1 段角色素材" });
    const secondSelection = within(editor).getByRole("listbox", { name: "第 2 段角色素材" });
    await user.selectOptions(secondSelection, "continuous-select-character-2");

    expect(firstSelection).toHaveValue([]);
    expect(secondSelection).toHaveValue(["continuous-select-character-2"]);
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    await waitFor(() => expect(port.submit).toHaveBeenCalledOnce());
    const request = JSON.parse(port.submit.mock.calls[0]![2]) as ContinuousChapterOperationRequest;
    if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save request.");
    expect(request.command.content).toMatchObject([
      { text: "第一段", character: [] },
      { text: "第二段", character: ["continuous-select-character-2"] },
    ]);
  });

  it("ignores a late target-read 401 after the continuous editor changes owner", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-late-target-401", "迟到的目标核对", [{
      text: "原段落", storyboard: ["continuous-late-401-shot"], character: [], scene: [], prop: [],
    }]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-late-401-shot", current.id), frame_index: 0 }],
    }, false, true);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const pendingTargetDirectory = deferred<Chapter[]>();
    const targetReadStarted = deferred<void>();
    const initialListChapters = base.services.listChapters;
    let postReturned = false;
    let delayedTargetDirectoryClaimed = false;
    Object.assign(base.services, {
      listChapters: vi.fn(async (seriesId: string, signal: AbortSignal) => {
        if (postReturned && !delayedTargetDirectoryClaimed) {
          delayedTargetDirectoryClaimed = true;
          targetReadStarted.resolve();
          return pendingTargetDirectory.promise;
        }
        return initialListChapters(seriesId, signal);
      }),
    });
    const port = attachContinuousChapterOperationPort(base.services, async (chapterId, operationId, bodyJson) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save.");
      const result = { ...current, id: chapterId, content: request.command.content as unknown as Chapter["content"] };
      base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? result : row));
      const response = await makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
      postReturned = true;
      return response;
    });
    const onUnauthorized = vi.fn();
    const view = render(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="demo-user"
      />,
    );

    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.clear(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }));
    await user.type(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }), "保存后核对");
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    await waitFor(() => expect(port.submit).toHaveBeenCalledOnce());
    await targetReadStarted.promise;

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={onUnauthorized}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="new-user"
      />,
    );
    await act(async () => {
      pendingTargetDirectory.reject(new ApiError("http", "late unauthorized read", 401));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(onUnauthorized).not.toHaveBeenCalled();
    expect(port.submit).toHaveBeenCalledOnce();
  });

  it("does not publish a recovered editor when the final target read resolves after a scope change", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-late-recovery-target", "迟到的恢复目标", [{
      text: "原段落", storyboard: ["continuous-late-recovery-shot"], character: [], scene: [], prop: [],
    }]);
    const createdCharacter = character("continuous-late-recovery-character", series.id, "恢复角色");
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-late-recovery-shot", current.id), frame_index: 0 }],
    }, false, true);
    let characterCreated = false;
    base.services.listCharacters = vi.fn(async () => characterCreated ? [createdCharacter] : []);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const port = attachContinuousChapterOperationPort(base.services, async (chapterId, operationId, bodyJson) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "create-character") throw new Error("Expected a character creation.");
      characterCreated = true;
      const result = { id: createdCharacter.id, series_id: series.id };
      return makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
    });

    const firstMount = renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.type(within(editor).getByRole("textbox", { name: "新增素材名称" }), "恢复角色");
    await user.click(within(editor).getByRole("button", { name: "创建并关联角色" }));
    await waitFor(() => expect(port.submit).toHaveBeenCalledOnce());
    expect(await screen.findByRole("button", { name: "继续编辑已核对草稿" })).toBeEnabled();
    firstMount.unmount();

    const pendingTargetDirectory = deferred<Chapter[]>();
    const finalRecoveryTargetReadStarted = deferred<void>();
    const initialListChapters = base.services.listChapters;
    let deferFinalRecoveryTargetRead = false;
    let recoveryDirectoryReads = 0;
    Object.assign(base.services, {
      listChapters: vi.fn(async (seriesId: string, signal: AbortSignal) => {
        if (deferFinalRecoveryTargetRead) {
          recoveryDirectoryReads += 1;
          if (recoveryDirectoryReads === 5) {
            finalRecoveryTargetReadStarted.resolve();
            return pendingTargetDirectory.promise;
          }
        }
        return initialListChapters(seriesId, signal);
      }),
    });
    const recoveredMount = renderContinuousBrowser(base.services);
    await screen.findByRole("button", { name: "继续编辑已核对草稿" });
    deferFinalRecoveryTargetRead = true;
    await user.click(screen.getByRole("button", { name: "继续编辑已核对草稿" }));
    await finalRecoveryTargetReadStarted.promise;
    expect(recoveryDirectoryReads).toBe(5);

    recoveredMount.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="new-user"
      />,
    );
    pendingTargetDirectory.resolve(base.getCurrentChapters());
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(screen.queryByRole("region", { name: "整章连续编辑" })).not.toBeInTheDocument();
    expect(port.submit).toHaveBeenCalledOnce();
  });

  it("keeps the reviewed journal when its clear waits for a lock past a scope change", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-clear-scope-race", "清理期间切换范围", [{
      text: "原段落", storyboard: ["continuous-clear-scope-shot"], character: [], scene: [], prop: [],
    }]);
    const createdCharacter = character("continuous-clear-scope-character", series.id, "清理竞态角色");
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-clear-scope-shot", current.id), frame_index: 0 }],
    }, false, true);
    let characterCreated = false;
    base.services.listCharacters = vi.fn(async () => characterCreated ? [createdCharacter] : []);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const queuedClearStarted = deferred<void>();
    const allowQueuedClear = deferred<void>();
    let lockRequestCount = 0;
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: async (_name: string, _options: { mode: "exclusive"; ifAvailable: true }, callback: (lock: Lock | null) => unknown) => {
          lockRequestCount += 1;
          if (lockRequestCount === 2) {
            queuedClearStarted.resolve();
            await allowQueuedClear.promise;
          }
          return callback({} as Lock);
        },
      },
    });
    const port = attachContinuousChapterOperationPort(base.services, async (chapterId, operationId, bodyJson) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "create-character") throw new Error("Expected a character creation.");
      characterCreated = true;
      const result = { id: createdCharacter.id, series_id: series.id };
      return makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
    });
    const view = renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.type(within(editor).getByRole("textbox", { name: "新增素材名称" }), "清理竞态角色");
    await user.click(within(editor).getByRole("button", { name: "创建并关联角色" }));
    await waitFor(() => expect(port.submit).toHaveBeenCalledOnce());
    const storageKey = continuousChapterOperationJournalKey({
      actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: current.id,
    });
    expect(await screen.findByRole("button", { name: "继续编辑已核对草稿" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "继续编辑已核对草稿" }));
    await queuedClearStarted.promise;
    expect(lockRequestCount).toBe(2);

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="new-user"
      />,
    );
    allowQueuedClear.resolve();
    await waitFor(() => expect(localStorage.getItem(storageKey)).not.toBeNull());

    expect(JSON.parse(localStorage.getItem(storageKey) ?? "null").phase)
      .toBe("receipt_committed_pending_target_read");
    expect(port.submit).toHaveBeenCalledOnce();
  });

  it("does not remove a reviewed journal when its tombstone read finishes after a scope change", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-tombstone-scope-race", "结案校验期间切换范围", [{
      text: "原段落", storyboard: ["continuous-tombstone-scope-shot"], character: [], scene: [], prop: [],
    }]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-tombstone-scope-shot", current.id), frame_index: 0 }],
    }, false, true);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const port = attachContinuousChapterOperationPort(base.services, async (chapterId, operationId, bodyJson) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save.");
      const result = { ...current, id: chapterId, content: request.command.content as unknown as Chapter["content"] };
      base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? result : row));
      return makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
    });
    const view = renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.clear(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }));
    await user.type(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }), "已保存待审阅");
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    await waitFor(() => expect(port.submit).toHaveBeenCalledOnce());
    const storageKey = continuousChapterOperationJournalKey({
      actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: current.id,
    });
    expect(await screen.findByRole("button", { name: "我已审阅已保存章节，解除本章冻结" })).toBeEnabled();
    const originalRaw = localStorage.getItem(storageKey);
    if (originalRaw === null) throw new Error("The reviewed operation should still be journaled.");

    const digestStarted = deferred<void>();
    const delayedDigest = deferred<ArrayBuffer>();
    const originalDigest = webcrypto.subtle.digest.bind(webcrypto.subtle);
    let digestCalls = 0;
    let heldDigest: Promise<ArrayBuffer> | null = null;
    const digestSpy = vi.spyOn(webcrypto.subtle, "digest").mockImplementation((algorithm, data) => {
      digestCalls += 1;
      if (digestCalls === 2) {
        heldDigest = originalDigest(algorithm, data);
        digestStarted.resolve();
        return delayedDigest.promise;
      }
      return originalDigest(algorithm, data);
    });
    restoreOverrides.push(() => digestSpy.mockRestore());
    await user.click(screen.getByRole("button", { name: "我已审阅已保存章节，解除本章冻结" }));
    await digestStarted.promise;

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="new-user"
      />,
    );
    delayedDigest.resolve(await heldDigest!);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(localStorage.getItem(storageKey)).toBe(originalRaw);
    expect(port.submit).toHaveBeenCalledOnce();
  });

  it("does not discard an undispatched journal when its integrity read finishes after a scope change", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-discard-scope-race", "未发送记录清理期间切换范围", [{
      text: "原段落", storyboard: ["continuous-discard-scope-shot"], character: [], scene: [], prop: [],
    }]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-discard-scope-shot", current.id), frame_index: 0 }],
    }, false, true);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const port = attachContinuousChapterOperationPort(base.services, async () => {
      throw new Error("The preflight conflict must prevent POST.");
    });
    const view = renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.clear(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }));
    await user.type(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }), "保留未发送草稿");
    base.setCurrentChapters([{
      ...current,
      content: [{ ...current.content![0]!, text: "另一个编辑者已经更新正文" }],
    }]);
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    await screen.findByRole("button", { name: "放弃明确尚未发送的记录" });
    const storageKey = continuousChapterOperationJournalKey({
      actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: current.id,
    });
    const originalRaw = localStorage.getItem(storageKey);
    if (originalRaw === null) throw new Error("The captured operation should remain journaled.");

    const digestStarted = deferred<void>();
    const delayedDigest = deferred<ArrayBuffer>();
    const originalDigest = webcrypto.subtle.digest.bind(webcrypto.subtle);
    let heldDigest: Promise<ArrayBuffer> | null = null;
    const digestSpy = vi.spyOn(webcrypto.subtle, "digest").mockImplementationOnce((algorithm, data) => {
      heldDigest = originalDigest(algorithm, data);
      digestStarted.resolve();
      return delayedDigest.promise;
    });
    restoreOverrides.push(() => digestSpy.mockRestore());
    await user.click(screen.getByRole("button", { name: "放弃明确尚未发送的记录" }));
    await digestStarted.promise;

    view.rerender(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="new-user"
      />,
    );
    delayedDigest.resolve(await heldDigest!);
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });

    expect(localStorage.getItem(storageKey)).toBe(originalRaw);
    expect(port.submit).not.toHaveBeenCalled();
  });

  it("recovers a lost acknowledgement by reading the original UUID after remount", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-unknown-recovery", "未知结果恢复", [{
      text: "原段落",
      storyboard: ["continuous-recovery-shot"],
      character: [], scene: [], prop: [],
    }]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-recovery-shot", current.id), frame_index: 0 }],
    }, false, true);
    base.services.deleteFrame = vi.fn(async (_seriesId, _chapterId, _frameIndex) => current);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    let remoteReceipt: ContinuousChapterOperationReceipt | null = null;
    const submit = vi.fn(async (chapterId: string, operationId: string, bodyJson: string) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save.");
      const result = { ...current, id: chapterId, content: request.command.content as unknown as Chapter["content"] };
      base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? result : row));
      remoteReceipt = (await makeContinuousOperationResponse(
        "demo-user", series.id, chapterId, operationId, bodyJson, result,
      )).receipt;
      throw new ApiError("network", "响应丢失");
    });
    const lookup = vi.fn(async (_chapterId: string, _operationId: string, _signal: AbortSignal) => {
      if (remoteReceipt === null) throw new ApiError("http", "原操作不存在", 404);
      return { receipt: remoteReceipt };
    });
    base.services.getContinuousChapterEditCapabilities = vi.fn(async () => ({ chapterEditGuard: 1 as const, operationReceipt: 1 as const }));
    Object.assign(base.services, { continuousChapterOperations: { submit, lookup } });

    const firstMount = renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    const text = within(editor).getByRole("textbox", { name: "第 1 段剧本文字" });
    await user.clear(text);
    await user.type(text, "冻结的原草稿");
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    const [, operationId] = submit.mock.calls[0]!;
    await screen.findByRole("region", { name: "连续编辑操作恢复" });
    expect(screen.getByText("请求可能已发出，等待核对")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "原连续编辑草稿" })).getByText("冻结的原草稿")).toBeInTheDocument();
    const storageKey = continuousChapterOperationJournalKey({
      actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: current.id,
    });
    expect(JSON.parse(localStorage.getItem(storageKey) ?? "null").phase).toBe("dispatch_possible");
    firstMount.unmount();

    renderContinuousBrowser(base.services);
    expect(await screen.findByRole("region", { name: "连续编辑操作恢复" })).toBeInTheDocument();
    expect(await screen.findByText("请求可能已发出，等待核对")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "原连续编辑草稿" })).getByText("冻结的原草稿")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编辑文字" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "在开头插入分镜" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "开启整章连续编辑" })).toBeDisabled();
    await user.click(await screen.findByRole("button", { name: "重新读取原操作" }));
    await waitFor(() => expect(lookup).toHaveBeenCalledOnce());
    expect(lookup).toHaveBeenCalledWith(current.id, operationId, expect.any(AbortSignal));
    expect(await screen.findByRole("button", { name: "我已审阅已保存章节，解除本章冻结" })).toBeEnabled();
    expect(submit).toHaveBeenCalledOnce();
    expect(JSON.parse(localStorage.getItem(storageKey) ?? "null").operation.operation_id).toBe(operationId);
    await user.click(screen.getByRole("button", { name: "我已审阅已保存章节，解除本章冻结" }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "连续编辑操作恢复" })).not.toBeInTheDocument());
    expect(localStorage.getItem(storageKey)).toBeNull();
    expect(submit).toHaveBeenCalledOnce();
  });

  it("attaches a confirmed asset receipt to the exact original draft after explicit review", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-asset-owner", "延迟素材创建", [
      { text: "原目标段", storyboard: ["continuous-owner-shot-1"], character: [], scene: [], prop: [] },
      { text: "切换后段", storyboard: ["continuous-owner-shot-2"], character: [], scene: [], prop: [] },
    ]);
    const storyboardAssets = [
      { ...asset("continuous-owner-shot-1", current.id), frame_index: 0 },
      { ...asset("continuous-owner-shot-2", current.id), frame_index: 1 },
    ];
    const base = makeServices([current], { [current.id]: storyboardAssets }, false, true);
    const createdCharacter = character("continuous-created-character", series.id, "新角色");
    let directoryShowsCreatedCharacter = false;
    const listCharacters = vi.fn(async () => directoryShowsCreatedCharacter ? [createdCharacter] : []);
    base.services.listCharacters = listCharacters;
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const port = attachContinuousChapterOperationPort(base.services, async (chapterId, operationId, bodyJson) => {
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "create-character") throw new Error("Expected a character creation.");
      directoryShowsCreatedCharacter = true;
      const result = { id: createdCharacter.id, series_id: series.id };
      return makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
    });

    render(
      <ChapterBrowser
        onBack={vi.fn()}
        onUnauthorized={vi.fn()}
        continuousPreviewActive
        workbenchActive
        series={series}
        services={base.services}
        userId="demo-user"
      />,
    );

    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.type(within(editor).getByRole("textbox", { name: "新增素材名称" }), "新角色");
    await user.click(within(editor).getByRole("button", { name: "创建并关联角色" }));
    await waitFor(() => expect(port.submit).toHaveBeenCalledOnce());
    const [, , bodyJson] = port.submit.mock.calls[0]!;
    const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
    expect(request.command).toMatchObject({ kind: "create-character", target_draft_id: "source:0" });
    expect(screen.queryByRole("region", { name: "整章连续编辑" })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "继续编辑已核对草稿" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "继续编辑已核对草稿" }));
    const recoveredEditor = await screen.findByRole("region", { name: "整章连续编辑" });
    expect(within(recoveredEditor).getByRole("listbox", { name: "第 1 段角色素材" }))
      .toHaveValue(["continuous-created-character"]);
    expect(within(recoveredEditor).getByRole("listbox", { name: "第 2 段角色素材" })).toHaveValue([]);
    expect(port.submit).toHaveBeenCalledOnce();
  });

  it("blocks the actual submit when Web Locks cannot serialize the chapter", async () => {
    installContinuousChapterOperationLock(false);
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-no-lock", "没有互斥能力", [{
      text: "原始正文", storyboard: ["continuous-no-lock-shot"], character: [], scene: [], prop: [],
    }]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-no-lock-shot", current.id), frame_index: 0 }],
    }, false, true);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const submit = vi.fn(async () => { throw new Error("A POST must not start without Web Locks."); });
    base.services.getContinuousChapterEditCapabilities = vi.fn(async () => ({ chapterEditGuard: 1 as const, operationReceipt: 1 as const }));
    Object.assign(base.services, { continuousChapterOperations: { submit, lookup: vi.fn() } });

    renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.type(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }), "改写");
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    expect(await within(editor).findByRole("status")).toHaveTextContent(/跨标签互斥/u);
    expect(submit).not.toHaveBeenCalled();
  });

  it("blocks the actual POST when it cannot persist and read back the frozen operation", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const current = chapter("chapter-continuous-storage-failure", "原操作存储失败", [{
      text: "原始正文", storyboard: ["continuous-storage-shot"], character: [], scene: [], prop: [],
    }]);
    const base = makeServices([current], {
      [current.id]: [{ ...asset("continuous-storage-shot", current.id), frame_index: 0 }],
    }, false, true);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const submit = vi.fn(async () => { throw new Error("POST must not start before durable readback."); });
    base.services.getContinuousChapterEditCapabilities = vi.fn(async () => ({ chapterEditGuard: 1 as const, operationReceipt: 1 as const }));
    Object.assign(base.services, { continuousChapterOperations: { submit, lookup: vi.fn() } });
    const storage = {
      getItem: vi.fn(() => null),
      setItem: vi.fn(() => { throw new Error("QuotaExceededError"); }),
      removeItem: vi.fn(),
    };
    Object.defineProperty(base.services, "continuousChapterOperationStorage", { configurable: true, value: storage });

    renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    const editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.clear(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }));
    await user.type(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }), "未能持久化的改稿");
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));

    expect(await within(editor).findByRole("status")).toHaveTextContent(/本地原操作记录不可用/u);
    expect(storage.setItem).toHaveBeenCalledOnce();
    expect(submit).not.toHaveBeenCalled();
  });

  it("keeps a different chapter editable while another chapter has an unresolved operation", async () => {
    installContinuousChapterOperationLock();
    installContinuousOperationCrypto();
    const user = userEvent.setup();
    const pendingChapter = chapter("chapter-continuous-pending-owner", "待核对章节", [{
      text: "待核对原文", storyboard: ["continuous-pending-owner-shot"], character: [], scene: [], prop: [],
    }]);
    const otherChapter = chapter("chapter-continuous-independent", "独立可编辑章节", [{
      text: "另一章原文", storyboard: ["continuous-independent-shot"], character: [], scene: [], prop: [],
    }], { order: 2 });
    const base = makeServices([pendingChapter, otherChapter], {
      [pendingChapter.id]: [{ ...asset("continuous-pending-owner-shot", pendingChapter.id), frame_index: 0 }],
      [otherChapter.id]: [{ ...asset("continuous-independent-shot", otherChapter.id), frame_index: 0 }],
    }, false, true);
    base.services.listScenes = vi.fn(async () => []);
    base.services.listProps = vi.fn(async () => []);
    const submit = vi.fn(async (chapterId: string, operationId: string, bodyJson: string) => {
      if (chapterId === pendingChapter.id) throw new ApiError("network", "原请求响应丢失");
      const request = JSON.parse(bodyJson) as ContinuousChapterOperationRequest;
      if (request.command.kind !== "save-chapter") throw new Error("Expected a chapter save.");
      const result = { ...otherChapter, content: request.command.content as unknown as Chapter["content"] };
      base.setCurrentChapters(base.getCurrentChapters().map((row) => row.id === chapterId ? result : row));
      return makeContinuousOperationResponse("demo-user", series.id, chapterId, operationId, bodyJson, result);
    });
    base.services.getContinuousChapterEditCapabilities = vi.fn(async () => ({ chapterEditGuard: 1 as const, operationReceipt: 1 as const }));
    Object.assign(base.services, { continuousChapterOperations: {
      submit,
      lookup: vi.fn(async () => { throw new ApiError("http", "原操作尚无回执", 404); }),
    } });

    renderContinuousBrowser(base.services);
    await user.click(await screen.findByRole("button", { name: "开启整章连续编辑" }));
    let editor = await screen.findByRole("region", { name: "整章连续编辑" });
    await user.clear(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }));
    await user.type(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }), "待核对的已冻结草稿");
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(await screen.findByRole("region", { name: "连续编辑操作恢复" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /独立可编辑章节/u }));
    expect(screen.getAllByRole("heading", { name: "独立可编辑章节" })).toHaveLength(2);
    await waitFor(() => expect(screen.getByRole("button", { name: "开启整章连续编辑" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "开启整章连续编辑" }));
    editor = await screen.findByRole("region", { name: "整章连续编辑" });
    expect(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" })).toHaveValue("另一章原文");
    await user.clear(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }));
    await user.type(within(editor).getByRole("textbox", { name: "第 1 段剧本文字" }), "独立章节可以继续编辑");
    await user.click(within(editor).getByRole("button", { name: "保存并核对整章" }));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));

    const pendingKey = continuousChapterOperationJournalKey({
      actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: pendingChapter.id,
    });
    const otherKey = continuousChapterOperationJournalKey({
      actorId: "demo-user", apiBaseUrl, seriesId: series.id, chapterId: otherChapter.id,
    });
    expect(submit.mock.calls.map(([chapterId]) => chapterId)).toEqual([pendingChapter.id, otherChapter.id]);
    expect(JSON.parse(localStorage.getItem(pendingKey) ?? "null").phase).toBe("dispatch_possible");
    await waitFor(() => {
      expect(JSON.parse(localStorage.getItem(otherKey) ?? "null").phase)
        .toBe("receipt_committed_pending_target_read");
    });
  });

});
