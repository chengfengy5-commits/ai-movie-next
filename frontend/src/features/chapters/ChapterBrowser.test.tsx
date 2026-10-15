import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement, useLayoutEffect, type ComponentProps } from "react";
import type { Character, Chapter, ChapterEditLockLeaseHandle, ChapterEditLockState, CreateChapterInput, Series, StoryboardAsset, UpdateChapterInput } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { PersonalRoughCutSnapshot } from "../../shared/api/personalRoughCut";
import type { PersonalRoughCutFrameTarget, PersonalRoughCutReadIdentity } from "./PersonalRoughCutPanel";
import {
  parsePersonalProductionSnapshot,
  type PersonalProductionSnapshot,
} from "../../shared/api/personalProductionNotes";
import type { WorkspaceServices } from "../../shared/api/services";
import type { StoryboardGenerationServicePort } from "../storyboard-generation/contracts";
import { digestPersonalProductionMediaIdentity } from "./personal-production/mediaIdentity";
import { demoSeries } from "../series/demoSeries";
import { ChapterBrowser, type ChapterNavigationIntent } from "./ChapterBrowser";
import type {
  PersonalProductionNoteFrameTarget,
  PersonalProductionNoteReadIdentity,
} from "./PersonalProductionNotesPanel";

const personalNotesBridge = vi.hoisted(() => ({
  onLocateResume: null as ((target: { position: number; isCurrent(): boolean }) => void) | null,
  onResumeInvalidated: null as (() => void) | null,
  lastTarget: null as { position: number; isCurrent(): boolean } | null,
  onRegisterFrameRead: null as ((identity: PersonalProductionNoteReadIdentity) => void) | null,
  onFrameReadInvalidated: null as ((identity: PersonalProductionNoteReadIdentity) => void) | null,
  onLocateProductionNoteFrame: null as ((target: PersonalProductionNoteFrameTarget) => boolean) | null,
  onClose: null as ((identity: PersonalProductionNoteReadIdentity | null) => void) | null,
  lastProductionNoteTarget: null as PersonalProductionNoteFrameTarget | null,
}));

const frameAssetReferencesBridge = vi.hoisted(() => ({
  onClose: null as ((owner: unknown) => void) | null,
  owner: null as unknown,
}));

const roughCutPanelBridge = vi.hoisted(() => ({
  onClose: null as (() => void) | null,
  onLocateFrame: null as ((target: PersonalRoughCutFrameTarget) => boolean) | null,
  lastTarget: null as PersonalRoughCutFrameTarget | null,
  onFrameNavigationInvalidated: null as ((identity: PersonalRoughCutReadIdentity) => void) | null,
  lastInvalidation: null as PersonalRoughCutReadIdentity | null,
}));

vi.mock("./PersonalProductionNotesPanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./PersonalProductionNotesPanel")>();
  return {
    ...actual,
    PersonalProductionNotesPanel: (props: ComponentProps<typeof actual.PersonalProductionNotesPanel>) => {
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
          props.onLocateResume(target);
        },
        onLocateProductionNoteFrame: (target) => {
          personalNotesBridge.lastProductionNoteTarget = target;
          return props.onLocateProductionNoteFrame(target);
        },
      });
    },
  };
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
  const acquireChapterEditLock = vi.fn(async (_chapterId: string, _signal: AbortSignal) => ({
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
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: vi.fn(),
  };
  return {
    services,
    listChapters,
    listStoryboardAssets,
    createChapter,
    updateChapter,
    deleteChapter,
    reorderChapters,
    getChapterEditLock,
    acquireChapterEditLock,
    releaseChapterEditLock,
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
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

describe("read-only chapter browser", () => {
  afterEach(() => {
    cleanup();
    personalNotesBridge.onLocateResume = null;
    personalNotesBridge.onResumeInvalidated = null;
    personalNotesBridge.lastTarget = null;
    personalNotesBridge.onRegisterFrameRead = null;
    personalNotesBridge.onFrameReadInvalidated = null;
    personalNotesBridge.onLocateProductionNoteFrame = null;
    personalNotesBridge.onClose = null;
    personalNotesBridge.lastProductionNoteTarget = null;
    frameAssetReferencesBridge.onClose = null;
    frameAssetReferencesBridge.owner = null;
    roughCutPanelBridge.onClose = null;
    roughCutPanelBridge.onLocateFrame = null;
    roughCutPanelBridge.lastTarget = null;
    roughCutPanelBridge.onFrameNavigationInvalidated = null;
    roughCutPanelBridge.lastInvalidation = null;
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
    const target = chapter("chapter-target", "同名章节", [
      { storyboard: ["storyboard-other"], character: ["character-other"], text: "镜头一文字" },
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
    const intent: ChapterNavigationIntent = {
      userId: "demo-user",
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

    renderBrowser(services, vi.fn(), onUnauthorized, series, "demo-user", {
      intent,
      onConsumed,
      onAbandoned,
    });

    expect(await screen.findByText("目标镜头文字")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    expect(listStoryboardAssets).toHaveBeenCalledWith(series.id, target.id, expect.any(AbortSignal));
    const targetArticle = screen.getByRole("article", { name: "同名章节 · 镜头 2" });
    const scrollIntoView = vi.fn();
    Object.defineProperty(targetArticle, "scrollIntoView", { configurable: true, value: scrollIntoView });
    expect(onConsumed).not.toHaveBeenCalled();

    await act(async () => {
      pendingAssets.resolve([
        asset("character-target", target.id),
        asset("storyboard-target", target.id, null),
      ]);
      await pendingAssets.promise;
    });

    expect(targetArticle).toHaveFocus();
    expect(targetArticle).toHaveAttribute("aria-label", "同名章节 · 镜头 2");
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "auto", block: "center" });
    expect(screen.getByText("已定位到目标镜头 2。")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(onConsumed).toHaveBeenCalledTimes(1);
    expect(onConsumed).toHaveBeenCalledWith(intent.navigationEpoch);
    expect(onAbandoned).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
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
      staleLocateResult = locate(target);
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
      staleLocateResult = oldLocate(oldTarget);
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
      staleLocateResult = oldLocate(staleTarget);
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
        expect(getChapterEditLock).toHaveBeenCalledTimes(2);
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

});
