import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement, useEffect } from "react";
import { AuthProvider } from "../features/auth/AuthProvider";
import { Workspace } from "./Workspace";
import { createApiServices, createDemoServices, type WorkspaceServices } from "../shared/api/services";
import { demoSeries } from "../features/series/demoSeries";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
} from "../shared/api/storage";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset, User } from "../shared/api/contracts";
import { ApiError } from "../shared/api/errors";
import { parsePersonalProductionSnapshot } from "../shared/api/personalProductionNotes";
import { digestPersonalProductionMediaIdentity } from "../features/chapters/personal-production/mediaIdentity";
import type { PersonalRoughCutSnapshot } from "../shared/api/personalRoughCut";
import { MY_TASK_PAGE_SIZE, type MyTaskPage, type MyTaskRecord } from "../shared/api/myTasks";
import type { MyTeam } from "../shared/api/teams";

type AssetLibraryCategory = "characters" | "scenes" | "props";

const frameReferenceCategoryCases = [
  { category: "characters", label: "角色", kind: "character" },
  { category: "scenes", label: "场景", kind: "scene" },
  { category: "props", label: "道具", kind: "prop" },
] as const;

type CapturedChapterTarget = {
  chapterId: string;
  seriesId: string;
  isCurrent(): boolean;
};

type CapturedFrameTarget = CapturedChapterTarget & {
  storyboardAssetId: string;
  category: AssetLibraryCategory;
  assetId: string;
};

type CapturedFrameIdentity = Pick<CapturedFrameTarget, "storyboardAssetId" | "category" | "assetId">;

type CapturedReferenceAssetTarget = {
  seriesId: string;
  category: AssetLibraryCategory;
  assetId: string;
  isCurrent(): boolean;
};

type CapturedAssetNavigationIntent = {
  userId: string;
  seriesId: string;
  category: AssetLibraryCategory;
  assetId: string;
  navigationEpoch: number;
};

type CapturedNavigationIntent = {
  chapterId: string;
  navigationEpoch: number;
  frameTarget?: CapturedFrameIdentity;
};

const chapterNavigationProbe = vi.hoisted(() => ({
  target: null as CapturedChapterTarget | null,
  callback: null as ((target: CapturedChapterTarget) => void) | null,
  frameTarget: null as CapturedFrameTarget | null,
  frameCallback: null as ((target: CapturedFrameTarget) => void) | null,
  frameParentCallback: null as ((target: CapturedFrameTarget) => void) | null,
  sourcePanelCloseCallback: null as (() => void) | null,
  intent: null as CapturedNavigationIntent | null,
  intentHistory: [] as CapturedNavigationIntent[],
  consumedEpochs: [] as number[],
  abandonedEpochs: [] as number[],
}));

const assetNavigationProbe = vi.hoisted(() => ({
  target: null as CapturedReferenceAssetTarget | null,
  parentCallback: null as ((target: CapturedReferenceAssetTarget) => void) | null,
  intent: null as CapturedAssetNavigationIntent | null,
  intentHistory: [] as CapturedAssetNavigationIntent[],
  consumedEpochs: [] as number[],
  abandonedEpochs: [] as number[],
  sourcePanelCloseCallback: null as (() => void) | null,
}));

vi.mock("../features/chapters/ChapterBrowser", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../features/chapters/ChapterBrowser")>();
  return {
    ...actual,
    ChapterBrowser: (props: Parameters<typeof actual.ChapterBrowser>[0]) => {
      const navigationIntent = props.navigationIntent ?? null;
      const capturedIntent: CapturedNavigationIntent | null = navigationIntent === null
        ? null
        : {
          chapterId: navigationIntent.chapterId,
          navigationEpoch: navigationIntent.navigationEpoch,
          ...(navigationIntent.frameTarget === undefined ? {} : {
            frameTarget: { ...navigationIntent.frameTarget },
          }),
        };
      chapterNavigationProbe.intent = capturedIntent;
      const capturedEpoch = capturedIntent?.navigationEpoch ?? null;
      useEffect(() => () => {
        if (
          capturedEpoch !== null
          && chapterNavigationProbe.intent?.navigationEpoch === capturedEpoch
        ) {
          chapterNavigationProbe.intent = null;
        }
      }, [capturedEpoch]);
      if (
        capturedIntent !== null
        && chapterNavigationProbe.intentHistory.at(-1)?.navigationEpoch !== capturedIntent.navigationEpoch
      ) {
        chapterNavigationProbe.intentHistory.push(capturedIntent);
      }
      const navigationProps = props as Parameters<typeof actual.ChapterBrowser>[0] & {
        onNavigateToAsset?: (target: CapturedReferenceAssetTarget) => void;
      };
      const parentAssetCallback = navigationProps.onNavigateToAsset;
      if (parentAssetCallback !== undefined) {
        assetNavigationProbe.parentCallback = parentAssetCallback;
      }
      return createElement(actual.ChapterBrowser, {
        ...props,
        ...(parentAssetCallback === undefined ? {} : {
          onNavigateToAsset: (target: CapturedReferenceAssetTarget) => {
            assetNavigationProbe.target = target;
            assetNavigationProbe.parentCallback = parentAssetCallback;
            parentAssetCallback(target);
          },
        }),
        ...(props.onNavigationConsumed === undefined ? {} : {
          onNavigationConsumed: (epoch: number) => {
            chapterNavigationProbe.consumedEpochs.push(epoch);
            props.onNavigationConsumed?.(epoch);
          },
        }),
        ...(props.onNavigationAbandoned === undefined ? {} : {
          onNavigationAbandoned: (epoch: number) => {
            chapterNavigationProbe.abandonedEpochs.push(epoch);
            props.onNavigationAbandoned?.(epoch);
          },
        }),
      } as Parameters<typeof actual.ChapterBrowser>[0]);
    },
  };
});

vi.mock("../features/assets/AssetFrameUsagePanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../features/assets/AssetFrameUsagePanel")>();
  return {
    ...actual,
    AssetFrameUsagePanel: (props: Parameters<typeof actual.AssetFrameUsagePanel>[0]) => {
      chapterNavigationProbe.sourcePanelCloseCallback = () => props.onClose(props.owner);
      return createElement(actual.AssetFrameUsagePanel, props);
    },
  };
});

vi.mock("../features/chapters/FrameAssetReferencesPanel", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../features/chapters/FrameAssetReferencesPanel")>();
  return {
    ...actual,
    FrameAssetReferencesPanel: (props: Parameters<typeof actual.FrameAssetReferencesPanel>[0]) => {
      assetNavigationProbe.sourcePanelCloseCallback = () => props.onClose(props.owner);
      return createElement(actual.FrameAssetReferencesPanel, props);
    },
  };
});

vi.mock("../features/assets/AssetLibrary", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../features/assets/AssetLibrary")>();
  return {
    ...actual,
    AssetLibrary: (props: Parameters<typeof actual.AssetLibrary>[0]) => {
      const navigationProps = props as Parameters<typeof actual.AssetLibrary>[0] & {
        navigationIntent?: CapturedAssetNavigationIntent | null;
        onNavigationConsumed?: (epoch: number) => void;
        onNavigationAbandoned?: (epoch: number) => void;
      };
      const suppliedIntent = navigationProps.navigationIntent ?? null;
      const capturedIntent = suppliedIntent === null ? null : { ...suppliedIntent };
      assetNavigationProbe.intent = capturedIntent;
      const capturedEpoch = capturedIntent?.navigationEpoch ?? null;
      useEffect(() => () => {
        if (
          capturedEpoch !== null
          && assetNavigationProbe.intent?.navigationEpoch === capturedEpoch
        ) {
          assetNavigationProbe.intent = null;
        }
      }, [capturedEpoch]);
      if (
        capturedIntent !== null
        && assetNavigationProbe.intentHistory.at(-1)?.navigationEpoch !== capturedIntent.navigationEpoch
      ) {
        assetNavigationProbe.intentHistory.push(capturedIntent);
      }
      const parentCallback = props.onViewChapter;
      const frameProps = props as Parameters<typeof actual.AssetLibrary>[0] & {
        onViewFrame?: (target: CapturedFrameTarget) => void;
      };
      const parentFrameCallback = frameProps.onViewFrame;
      if (parentFrameCallback !== undefined) {
        chapterNavigationProbe.frameParentCallback = parentFrameCallback;
      }
      const wrappedProps = {
        ...props,
        ...(parentCallback === undefined ? {} : {
          onViewChapter: (target: CapturedChapterTarget) => {
            chapterNavigationProbe.target = target;
            chapterNavigationProbe.callback = parentCallback;
            parentCallback(target);
          },
        }),
        ...(parentFrameCallback === undefined ? {} : {
          onViewFrame: (target: CapturedFrameTarget) => {
            chapterNavigationProbe.frameTarget = target;
            chapterNavigationProbe.frameCallback = parentFrameCallback;
            parentFrameCallback(target);
          },
        }),
      };
      const wrappedNavigationProps = {
        ...wrappedProps,
        ...(navigationProps.onNavigationConsumed === undefined ? {} : {
          onNavigationConsumed: (epoch: number) => {
            assetNavigationProbe.consumedEpochs.push(epoch);
            navigationProps.onNavigationConsumed?.(epoch);
          },
        }),
        ...(navigationProps.onNavigationAbandoned === undefined ? {} : {
          onNavigationAbandoned: (epoch: number) => {
            assetNavigationProbe.abandonedEpochs.push(epoch);
            navigationProps.onNavigationAbandoned?.(epoch);
          },
        }),
      };
      return createElement(actual.AssetLibrary, wrappedNavigationProps as Parameters<typeof actual.AssetLibrary>[0]);
    },
  };
});

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response;
}

const fixtureUser = {
  id: "fixture-user",
  username: "演示创作者",
  email: "fixture@example.invalid",
  created_at: "2026-10-01T00:00:00",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((complete, fail) => {
    resolve = complete;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function chapterSnapshot(
  seriesId: string,
  id: string,
  title: string,
  text: string | null,
  storyboardId = `${id}-frame`,
): Chapter {
  return {
    id,
    series_id: seriesId,
    title,
    content: text === null ? [] : [{ text, storyboard: [storyboardId] }],
    order: 1,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
    lock: null,
  };
}

function chapterWithCharacterReference(
  seriesId: string,
  id: string,
  title: string,
  text: string,
  characterId: string,
): Chapter {
  return {
    ...chapterSnapshot(seriesId, id, title, null),
    content: [{
      text,
      original_text: text + " 原文",
      character: [characterId],
      scene: [],
      prop: [],
    }],
  };
}

function chapterWithAssetReference(
  seriesId: string,
  id: string,
  title: string,
  text: string,
  kind: "character" | "scene" | "prop",
  assetId: string,
): Chapter {
  return {
    ...chapterSnapshot(seriesId, id, title, null),
    content: [{
      text,
      original_text: text + " 原文",
      storyboard: [id + "-frame"],
      character: kind === "character" ? [assetId] : [],
      scene: kind === "scene" ? [assetId] : [],
      prop: kind === "prop" ? [assetId] : [],
    }],
  };
}

type FrameReferenceFixture = {
  text: string;
  storyboardAssetId: string;
  kind: "character" | "scene" | "prop";
  assetId: string;
};

function chapterWithFrameReferences(
  seriesId: string,
  id: string,
  title: string,
  frames: FrameReferenceFixture[],
  order = 1,
): Chapter {
  return {
    ...chapterSnapshot(seriesId, id, title, null),
    order,
    content: frames.map((frame) => ({
      text: frame.text,
      original_text: frame.text + " 原文",
      storyboard: [frame.storyboardAssetId],
      character: frame.kind === "character" ? [frame.assetId] : [],
      scene: frame.kind === "scene" ? [frame.assetId] : [],
      prop: frame.kind === "prop" ? [frame.assetId] : [],
    })),
  };
}

function chapterWithStoryboardAssets(
  seriesId: string,
  id: string,
  title: string,
  storyboardAssetIds: string[],
): Chapter {
  return {
    ...chapterSnapshot(seriesId, id, title, null),
    content: storyboardAssetIds.map((storyboardAssetId, index) => ({
      text: "章节镜头 " + (index + 1),
      original_text: "章节镜头 " + (index + 1) + " 原文",
      storyboard: [storyboardAssetId],
      character: [],
      scene: [],
      prop: [],
    })),
  };
}

function sceneSnapshot(seriesId: string, id: string, title: string): Scene {
  return {
    id,
    series_id: seriesId,
    title,
    description: null,
    image_url: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
  };
}

function propSnapshot(seriesId: string, id: string, name: string): Prop {
  return {
    id,
    series_id: seriesId,
    name,
    description: null,
    image_url: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
  };
}

function storyboardAssetSnapshot(
  seriesId: string,
  chapterId: string,
  id: string,
  frameIndex = 1,
): StoryboardAsset {
  return {
    id,
    series_id: seriesId,
    chapter_id: chapterId,
    frame_index: frameIndex,
    name: id,
    description: null,
    image_url: `http://127.0.0.1:4175/media/${id}.png`,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-02T00:00:00",
  };
}

function characterSnapshot(seriesId: string, name: string, id = "shared-character-id"): Character {
  return {
    id,
    series_id: seriesId,
    name,
    gender: null,
    age: null,
    role: null,
    appearance: null,
    description: null,
    image_url: null,
    audio_url: null,
    voice_ref: null,
    aliases: null,
    canonical_key: null,
    created_at: "2026-10-01T00:00:00",
    updated_at: "2026-10-01T00:00:00",
  };
}

function personalNotesSnapshot(chapterId: string, note: string) {
  return parsePersonalProductionSnapshot({
    chapter_id: chapterId,
    revision: 1,
    media_state: "empty",
    frames: [],
    frame_notes: { orphan: { status: "needs_revision", note } },
    resume_frame_id: null,
  }, chapterId);
}

function personalRoughCutSnapshot(chapterId: string, text: string): PersonalRoughCutSnapshot {
  return {
    chapter_id: chapterId,
    revision: 1,
    saved: true,
    frames: [{
      asset_id: "hidden-stable-id",
      frame_index: 0,
      text,
      preview_url: null,
      missing_reason: null,
      included: true,
      pending: false,
    }],
    removed_asset_ids: [],
  };
}

function personalRoughCutFrameSnapshot(
  chapterId: string,
  text: string,
  assetId: string,
): PersonalRoughCutSnapshot {
  return {
    chapter_id: chapterId,
    revision: 1,
    saved: true,
    frames: [{
      asset_id: assetId,
      frame_index: 0,
      text,
      preview_url: null,
      missing_reason: null,
      included: true,
      pending: false,
    }],
    removed_asset_ids: [],
  };
}

function myTaskRecord(id: string, overrides: Partial<MyTaskRecord> = {}): MyTaskRecord {
  return {
    id,
    type: "image",
    message_id: `message-${id}`,
    status: "completed",
    result: "有结果记录",
    request_data: null,
    progress_message: null,
    credit_cost: 2,
    progress: 100,
    created_at: "2026-10-05T00:00:00",
    updated_at: "2026-10-05T00:00:00",
    asset_type: null,
    asset_id: null,
    asset_name: null,
    chapter_title: null,
    chapter_id: null,
    frame_index: null,
    frame_count: null,
    frame_text: null,
    ...overrides,
  };
}

function myTaskPage(tasks: MyTaskRecord[], total = tasks.length): MyTaskPage {
  return { total, page: 1, page_size: MY_TASK_PAGE_SIZE, tasks };
}

function teamDirectoryItem(id: string, name: string): MyTeam {
  return {
    id,
    name,
    owner_id: "fixture-user",
    created_at: "2026-10-05T10:00:00",
    member_count: 1,
    my_role: "writer",
  };
}

function renderWorkspace(services: WorkspaceServices) {
  return render(
    <AuthProvider services={services}>
      <Workspace />
    </AuthProvider>,
  );
}

function authenticatedFixtureUser(id = "fixture-user", username = "演示创作者"): User {
  return {
    ...fixtureUser,
    id,
    username,
    is_superuser: false,
    membership_type: "free",
    membership_expires_at: null,
    avatar_url: null,
    bio: null,
  };
}

function frameReferenceNavigationServices(options: {
  user?: User;
  listChapters: WorkspaceServices["listChapters"];
  listStoryboardAssets: WorkspaceServices["listStoryboardAssets"];
  listCharacters?: WorkspaceServices["listCharacters"];
  listScenes?: WorkspaceServices["listScenes"];
  listProps?: WorkspaceServices["listProps"];
  login?: WorkspaceServices["login"];
  logout?: WorkspaceServices["logout"];
}): WorkspaceServices {
  const user = options.user ?? authenticatedFixtureUser();
  return {
    ...createDemoServices(window.localStorage),
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => user,
    login: options.login ?? (async () => { throw new Error("Not used."); }),
    listSeries: async () => demoSeries,
    listMyTeams: async () => [],
    listChapters: options.listChapters,
    listStoryboardAssets: options.listStoryboardAssets,
    listCharacters: options.listCharacters ?? (async () => []),
    listScenes: options.listScenes ?? (async () => []),
    listProps: options.listProps ?? (async () => []),
    getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: options.logout ?? vi.fn(),
  };
}

function frameReferenceDirectories(seriesId: string, assetId: string, decoyId: string) {
  const character = (id: string, description: string): Character => ({
    ...characterSnapshot(seriesId, "同名引用素材", id),
    description,
  });
  const scene = (id: string, description: string): Scene => ({
    ...sceneSnapshot(seriesId, id, "同名引用素材"),
    description,
  });
  const prop = (id: string, description: string): Prop => ({
    ...propSnapshot(seriesId, id, "同名引用素材"),
    description,
  });

  return {
    source: {
      characters: [character(assetId, "来源快照目标记录"), character(decoyId, "来源快照干扰记录")],
      scenes: [scene(assetId, "来源快照目标记录"), scene(decoyId, "来源快照干扰记录")],
      props: [prop(assetId, "来源快照目标记录"), prop(decoyId, "来源快照干扰记录")],
    },
    target: {
      characters: [character(decoyId, "目标目录首项同名干扰项"), character(assetId, "目标目录唯一目标记录")],
      scenes: [scene(decoyId, "目标目录首项同名干扰项"), scene(assetId, "目标目录唯一目标记录")],
      props: [prop(decoyId, "目标目录首项同名干扰项"), prop(assetId, "目标目录唯一目标记录")],
    },
  };
}

function frameNavigationServices(options: {
  category: AssetLibraryCategory;
  assetId: string;
  assetName: string;
  listChapters: WorkspaceServices["listChapters"];
  listStoryboardAssets: WorkspaceServices["listStoryboardAssets"];
  user?: User;
  login?: WorkspaceServices["login"];
  logout?: WorkspaceServices["logout"];
}): WorkspaceServices {
  const user = options.user ?? authenticatedFixtureUser();
  return {
    ...createDemoServices(window.localStorage),
    mode: "api",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    restore: async () => user,
    login: options.login ?? (async () => { throw new Error("Not used."); }),
    listSeries: async () => demoSeries,
    listMyTeams: async () => [],
    listChapters: options.listChapters,
    listStoryboardAssets: options.listStoryboardAssets,
    listCharacters: async (seriesId) => options.category === "characters"
      ? [characterSnapshot(seriesId, options.assetName, options.assetId)]
      : [],
    listScenes: async (seriesId) => options.category === "scenes"
      ? [sceneSnapshot(seriesId, options.assetId, options.assetName)]
      : [],
    listProps: async (seriesId) => options.category === "props"
      ? [propSnapshot(seriesId, options.assetId, options.assetName)]
      : [],
    getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
    getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
    listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
    logout: options.logout ?? vi.fn(),
  };
}

type ScrollIntoViewProbe = {
  targets: HTMLElement[];
  restore(): void;
};

let activeScrollProbe: ScrollIntoViewProbe | null = null;

function installScrollIntoViewProbe(): ScrollIntoViewProbe {
  const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");
  const targets: HTMLElement[] = [];
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: function (this: HTMLElement) {
      targets.push(this);
    },
  });
  const probe: ScrollIntoViewProbe = {
    targets,
    restore() {
      if (original === undefined) {
        Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
      } else {
        Object.defineProperty(HTMLElement.prototype, "scrollIntoView", original);
      }
    },
  };
  activeScrollProbe = probe;
  return probe;
}

function apiServicesForSeries(status: number, detail = "当前账号没有访问此列表的权限。") {
  window.localStorage.setItem(API_TOKEN_KEY, "fixture-token");
  window.localStorage.setItem(API_USER_KEY, "cached-user");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const pathname = new URL(String(input)).pathname;
    if (pathname.endsWith("/auth/me")) {
      return jsonResponse(fixtureUser);
    }
    return jsonResponse({ detail }, status);
  });
  return {
    fetcher,
    services: createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher,
    }),
  };
}

describe("read-only workspace", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal("fetch", vi.fn());
    chapterNavigationProbe.target = null;
    chapterNavigationProbe.callback = null;
    chapterNavigationProbe.frameTarget = null;
    chapterNavigationProbe.frameCallback = null;
    chapterNavigationProbe.frameParentCallback = null;
    chapterNavigationProbe.sourcePanelCloseCallback = null;
    chapterNavigationProbe.intent = null;
    chapterNavigationProbe.intentHistory.length = 0;
    chapterNavigationProbe.consumedEpochs.length = 0;
    chapterNavigationProbe.abandonedEpochs.length = 0;
    assetNavigationProbe.target = null;
    assetNavigationProbe.parentCallback = null;
    assetNavigationProbe.intent = null;
    assetNavigationProbe.intentHistory.length = 0;
    assetNavigationProbe.consumedEpochs.length = 0;
    assetNavigationProbe.abandonedEpochs.length = 0;
    assetNavigationProbe.sourcePanelCloseCallback = null;
  });

  afterEach(() => {
    activeScrollProbe?.restore();
    activeScrollProbe = null;
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
    chapterNavigationProbe.target = null;
    chapterNavigationProbe.callback = null;
    chapterNavigationProbe.frameTarget = null;
    chapterNavigationProbe.frameCallback = null;
    chapterNavigationProbe.frameParentCallback = null;
    chapterNavigationProbe.sourcePanelCloseCallback = null;
    chapterNavigationProbe.intent = null;
    chapterNavigationProbe.intentHistory.length = 0;
    chapterNavigationProbe.consumedEpochs.length = 0;
    chapterNavigationProbe.abandonedEpochs.length = 0;
    assetNavigationProbe.target = null;
    assetNavigationProbe.parentCallback = null;
    assetNavigationProbe.intent = null;
    assetNavigationProbe.intentHistory.length = 0;
    assetNavigationProbe.consumedEpochs.length = 0;
    assetNavigationProbe.abandonedEpochs.length = 0;
    assetNavigationProbe.sourcePanelCloseCallback = null;
  });

  it("keeps demo auth local, preserves source order, and applies the four filters", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(API_TOKEN_KEY, "separate-api-session");
    renderWorkspace(createDemoServices(window.localStorage));

    expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("账号"), "someone");
    await user.type(screen.getByLabelText("密码"), "wrong");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("账号或密码不正确");

    await user.clear(screen.getByLabelText("账号"));
    await user.clear(screen.getByLabelText("密码"));
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));

    await screen.findAllByRole("article");
    const firstCard = screen.getAllByRole("article")[0]!;
    expect(within(firstCard).getByRole("heading", { level: 3 })).toHaveTextContent("雾港来信");
    expect(screen.getAllByRole("article")).toHaveLength(20);
    expect(screen.getByRole("button", { name: /加载更多/ })).toBeInTheDocument();
    expect(screen.getByText("当前账号受限")).toBeInTheDocument();
    const restrictedCard = screen.getByText("当前账号受限").closest("article");
    expect(restrictedCard).not.toBeNull();
    expect(within(restrictedCard as HTMLElement).queryByRole("button", { name: /只读查看素材库/ }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getAllByText("林编剧").length).toBeGreaterThan(0);
    expect(screen.getAllByText("拾光工作室").length).toBeGreaterThan(0);
    expect(screen.getAllByText("我负责").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getAllByRole("article")).toHaveLength(24);

    await user.click(screen.getByRole("button", { name: "我创建的" }));
    expect(screen.getAllByRole("article")).toHaveLength(12);
    expect(within(screen.getAllByRole("article")[0]!).getByRole("heading", { level: 3 }))
      .toHaveTextContent("雾港来信");

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(screen.getAllByRole("article")).toHaveLength(16);
    await user.click(screen.getByRole("button", { name: "我认领的" }));
    expect(screen.getAllByRole("article")).toHaveLength(4);

    expect(window.localStorage.getItem(MOCK_SESSION_KEY)).toBe("demo-session");
    expect(window.localStorage.getItem(MOCK_USER_KEY)).not.toBeNull();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("separate-api-session");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("opens the asset library without prefetching chapters and returns without losing list state", async () => {
    const user = userEvent.setup();
    const services = createDemoServices(window.localStorage);
    const listChapters = vi.spyOn(services, "listChapters");
    const { container } = renderWorkspace(services);
    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await user.click(screen.getByRole("button", { name: /全部剧集/ }));
    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();

    const entryCard = screen.getAllByRole("article")[0]!;
    await user.click(within(entryCard).getByRole("button", { name: /只读查看素材库/ }));
    expect(await screen.findByRole("heading", { name: "雾港来信 · 素材库" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "林岚" })).toBeInTheDocument();
    expect(listChapters).not.toHaveBeenCalled();
    const hiddenList = container.querySelector(".series-list-view") as HTMLElement;
    expect(hiddenList.hidden).toBe(true);
    expect(hiddenList.hasAttribute("inert")).toBe(true);
    expect(screen.queryByRole("button", { name: "团队剧集" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "场景" }));
    expect(await screen.findByRole("heading", { name: "旧街清晨" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "道具" }));
    expect(await screen.findByRole("heading", { name: "黄铜钥匙" })).toBeInTheDocument();
    expect(listChapters).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));

    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
    expect(fetch).not.toHaveBeenCalled();
  });


  it.each([
    {
      category: "characters" as const,
      categoryLabel: "角色",
      searchLabel: "搜索角色素材",
      nextCategory: "scenes" as const,
      nextCategoryLabel: "场景",
      nextSearchLabel: "搜索场景素材",
      targetName: "Target Character",
      targetAlias: "Silver Lantern",
      decoyName: "Other Character",
      decoyAlias: "Copper Lantern",
    },
    {
      category: "scenes" as const,
      categoryLabel: "场景",
      searchLabel: "搜索场景素材",
      nextCategory: "props" as const,
      nextCategoryLabel: "道具",
      nextSearchLabel: "搜索道具素材",
      targetName: "Target Street",
      targetAlias: "Silver Lane",
      decoyName: "Other Street",
      decoyAlias: "Copper Lane",
    },
    {
      category: "props" as const,
      categoryLabel: "道具",
      searchLabel: "搜索道具素材",
      nextCategory: "characters" as const,
      nextCategoryLabel: "角色",
      nextSearchLabel: "搜索角色素材",
      targetName: "Target Object",
      targetAlias: "Silver Token",
      decoyName: "Other Object",
      decoyAlias: "Copper Token",
    },
  ])(
    "searches $category names and aliases locally without changing the source directory",
    async ({
      category,
      categoryLabel,
      searchLabel,
      nextCategory,
      nextCategoryLabel,
      nextSearchLabel,
      targetName,
      targetAlias,
      decoyName,
      decoyAlias,
    }) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const targetId = "search-shared-category-id";
      const decoyId = "search-decoy-" + category;
      const characters: Character[] = [
        { ...characterSnapshot(series.id, targetName, targetId), aliases: [targetAlias] },
        {
          ...characterSnapshot(series.id, decoyName, decoyId),
          aliases: [decoyAlias],
          description: "needle appears only in hidden prose",
        },
      ];
      const scenes: Scene[] = [
        { ...sceneSnapshot(series.id, targetId, targetName), aliases: [targetAlias] },
        {
          ...sceneSnapshot(series.id, decoyId, decoyName),
          aliases: [decoyAlias],
          description: "needle appears only in hidden prose",
        },
      ];
      const props: Prop[] = [
        { ...propSnapshot(series.id, targetId, targetName), aliases: [targetAlias] },
        {
          ...propSnapshot(series.id, decoyId, decoyName),
          aliases: [decoyAlias],
          description: "needle appears only in hidden prose",
        },
      ];
      const listCharacters = vi.fn(async () => category === "characters" || nextCategory === "characters"
        ? characters
        : []);
      const listScenes = vi.fn(async () => category === "scenes" || nextCategory === "scenes"
        ? scenes
        : []);
      const listProps = vi.fn(async () => category === "props" || nextCategory === "props"
        ? props
        : []);
      const listChapters = vi.fn(async () => [] as Chapter[]);
      const listStoryboardAssets = vi.fn(async () => [] as StoryboardAsset[]);
      const services = frameReferenceNavigationServices({
        listChapters,
        listStoryboardAssets,
        listCharacters,
        listScenes,
        listProps,
      });
      window.localStorage.setItem(API_TOKEN_KEY, "local-search-session");
      renderWorkspace(services);

      const seriesList = await screen.findByRole("region", { name: "我的剧集" });
      const seriesHeading = await within(seriesList).findByRole("heading", { name: series.name });
      const seriesCard = seriesHeading.closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("The series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
      const library = await screen.findByRole("region", { name: series.name + " · 素材库" });
      if (category !== "characters") {
        await user.click(within(library).getByRole("button", { name: categoryLabel }));
      }

      const targetHeading = await within(library).findByRole("heading", { name: targetName });
      const targetCard = targetHeading.closest("article");
      const decoyHeading = within(library).getByRole("heading", { name: decoyName });
      const decoyCard = decoyHeading.closest("article");
      if (!(targetCard instanceof HTMLElement) || !(decoyCard instanceof HTMLElement)) {
        throw new Error("The category cards were not rendered.");
      }
      const readers = { characters: listCharacters, scenes: listScenes, props: listProps };
      const selectedDirectoryReads = readers[category].mock.calls.length;
      const search = within(library).getByRole("searchbox", { name: searchLabel });
      expect(within(library).getByText("显示 2 / 2 项素材")).toBeInTheDocument();

      await user.click(search);
      await user.paste("  tArGeT ");
      expect(search).toHaveValue("  tArGeT ");
      expect(within(library).getByText("显示 1 / 2 项素材")).toBeInTheDocument();
      expect(targetCard.hidden).toBe(false);
      expect(decoyCard.hidden).toBe(true);
      expect(decoyCard).toHaveAttribute("inert");

      await user.clear(search);
      await user.paste("  sIlVeR ");
      expect(within(library).getByText("显示 1 / 2 项素材")).toBeInTheDocument();
      expect(targetCard.hidden).toBe(false);
      expect(decoyCard.hidden).toBe(true);

      await user.clear(search);
      await user.paste("needle");
      expect(within(library).getByText("显示 0 / 2 项素材")).toBeInTheDocument();
      expect(within(library).getByText("当前分类中没有匹配的素材。")).toBeInTheDocument();
      expect(within(library).getByRole("button", { name: "清空搜索" })).toBeEnabled();

      await user.click(within(library).getByRole("button", { name: "清空搜索" }));
      expect(within(library).getByText("显示 2 / 2 项素材")).toBeInTheDocument();
      expect(within(library).getByRole("heading", { name: targetName }).closest("article"))
        .toBe(targetCard);
      expect(within(library).getByRole("heading", { name: decoyName }).closest("article"))
        .toBe(decoyCard);
      expect(targetCard.hidden).toBe(false);
      expect(decoyCard.hidden).toBe(false);
      expect(readers[category]).toHaveBeenCalledTimes(selectedDirectoryReads);
      expect(listChapters).not.toHaveBeenCalled();
      expect(listStoryboardAssets).not.toHaveBeenCalled();

      await user.click(search);
      await user.paste("target");
      await user.click(within(library).getByRole("button", { name: new RegExp("^" + nextCategoryLabel) }));
      const nextSearch = await within(library).findByRole("searchbox", { name: nextSearchLabel });
      expect(nextSearch).toHaveValue("");
      expect(within(library).getByText("显示 2 / 2 项素材")).toBeInTheDocument();
      expect(readers[category]).toHaveBeenCalledTimes(selectedDirectoryReads);
    },
  );

  it("keeps searched asset navigation local, closes old usage on input change, and clears search for a new frame target", async () => {
    const user = userEvent.setup();
    const series = demoSeries[0]!;
    const assetId = "search-navigation-character";
    const chapterId = "search-navigation-chapter";
    const storyboardId = "search-navigation-storyboard";
    const asset = {
      ...characterSnapshot(series.id, "搜索导航角色", assetId),
      aliases: ["Silver Lantern"],
    };
    const chapter = chapterWithFrameReferences(series.id, chapterId, "搜索导航章节", [
      {
        text: "搜索导航目标镜头",
        storyboardAssetId: storyboardId,
        kind: "character",
        assetId,
      },
    ]);
    const listCharacters = vi.fn(async () => [asset]);
    const listChapters = vi.fn(async () => [chapter]);
    const listStoryboardAssets = vi.fn(async () => [
      storyboardAssetSnapshot(series.id, chapterId, storyboardId, 1),
    ]);
    const services = frameNavigationServices({
      category: "characters",
      assetId,
      assetName: asset.name,
      listChapters,
      listStoryboardAssets,
    });
    services.listCharacters = listCharacters;
    const scrollProbe = installScrollIntoViewProbe();
    window.localStorage.setItem(API_TOKEN_KEY, "search-navigation-session");
    renderWorkspace(services);

    const seriesList = await screen.findByRole("region", { name: "我的剧集" });
    const seriesHeading = await within(seriesList).findByRole("heading", { name: series.name });
      const seriesCard = seriesHeading.closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("The series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
    const library = await screen.findByRole("region", { name: series.name + " · 素材库" });
    const search = await within(library).findByRole("searchbox", { name: "搜索角色素材" });
    expect(await within(library).findByRole("heading", { name: asset.name })).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);

    await user.click(search);
    await user.paste("  sIlVeR ");
    expect(search).toHaveValue("  sIlVeR ");
    expect(within(library).getByText("显示 1 / 1 项素材")).toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listChapters).not.toHaveBeenCalled();
    expect(listStoryboardAssets).not.toHaveBeenCalled();

    const assetCard = within(library).getByRole("heading", { name: asset.name }).closest("article");
    if (!(assetCard instanceof HTMLElement)) {
      throw new Error("The searched asset card was not rendered.");
    }
    await user.click(within(assetCard).getByRole("button", { name: "查看关联镜头：" + asset.name }));
    const firstUsage = await within(library).findByRole("region", { name: "关联镜头" });
    expect(listChapters).toHaveBeenCalledTimes(1);

    await user.click(search);
    await user.clear(search);
    await user.paste("missing");
    expect(await within(library).findByText("当前分类中没有匹配的素材。")).toBeInTheDocument();
    await waitFor(() => expect(within(library).queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument());
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).not.toHaveBeenCalled();
    expect(firstUsage).not.toBeInTheDocument();

    await user.click(within(library).getByRole("button", { name: "清空搜索" }));
    expect(within(library).getByText("显示 1 / 1 项素材")).toBeInTheDocument();
    await user.click(within(assetCard).getByRole("button", { name: "查看关联镜头：" + asset.name }));
    const usage = await within(library).findByRole("region", { name: "关联镜头" });
    const locateFrame = within(usage).getByRole("button", {
      name: "定位对应镜头：" + chapter.title + " · 镜头1",
    });
    const targetEpochBefore = chapterNavigationProbe.consumedEpochs.length;
    await user.click(locateFrame);

    const chapterRegion = await screen.findByRole("region", { name: series.name });
    const targetFrame = await within(chapterRegion).findByRole("article", {
      name: chapter.title + " · 镜头 1",
    });
    await waitFor(() => expect(targetFrame).toHaveFocus());
    expect(scrollProbe.targets).toContain(targetFrame);
    expect(chapterNavigationProbe.consumedEpochs).toHaveLength(targetEpochBefore + 1);
    expect(listChapters).toHaveBeenCalledTimes(3);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    await user.click(within(targetFrame).getByRole("button", { name: "查看镜头 1 的关联素材" }));
    const references = await within(chapterRegion).findByRole("region", { name: "镜头 1 的关联素材" });
    await user.click(within(references).getByRole("button", { name: "角色" }));
    await within(references).findByText(asset.name, { exact: true });
    await user.click(within(references).getByRole("button", { name: "查看素材：" + asset.name }));

    const destinationLibrary = await screen.findByRole("region", { name: series.name + " · 素材库" });
    const destinationSearch = await within(destinationLibrary).findByRole("searchbox", { name: "搜索角色素材" });
    expect(destinationSearch).toHaveValue("");
    const destinationCard = within(destinationLibrary).getByRole("heading", {
      name: asset.name,
    }).closest("article");
    if (!(destinationCard instanceof HTMLElement)) {
      throw new Error("The target asset card was not rendered.");
    }
    await waitFor(() => expect(destinationCard).toHaveFocus());
    expect(scrollProbe.targets).toContain(destinationCard);
    await waitFor(() => expect(assetNavigationProbe.consumedEpochs).toHaveLength(1));
    expect(assetNavigationProbe.intent).toBeNull();
  });

  it.each(["success", "401"] as const)(
    "does not let a late search directory %s from the previous user change the current filter or session",
    async (lateResult) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const firstUser = authenticatedFixtureUser("search-user-first", "第一个搜索账号");
      const secondUser = authenticatedFixtureUser("search-user-second", "第二个搜索账号");
      const oldDirectory = deferred<Character[]>();
      let directoryReadCount = 0;
      const nextUserAsset = {
        ...characterSnapshot(series.id, "第二用户目标角色", "second-search-asset"),
        aliases: ["Silver Search"],
      };
      const listCharacters = vi.fn((seriesId: string) => {
        directoryReadCount += 1;
        return directoryReadCount === 1
          ? oldDirectory.promise
          : Promise.resolve([{ ...nextUserAsset, series_id: seriesId }]);
      });
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services = frameReferenceNavigationServices({
        user: firstUser,
        listChapters: async () => [],
        listStoryboardAssets: async () => [],
        listCharacters,
        logout,
        login: async () => {
          window.localStorage.setItem(API_TOKEN_KEY, "search-second-session");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
      });
      window.localStorage.setItem(API_TOKEN_KEY, "search-first-session");
      renderWorkspace(services);

      const firstSeriesList = await screen.findByRole("region", { name: "我的剧集" });
      const firstSeriesHeading = await within(firstSeriesList)
        .findByRole("heading", { name: series.name });
      const firstSeriesCard = firstSeriesHeading.closest("article");
      if (!(firstSeriesCard instanceof HTMLElement)) {
        throw new Error("The first user series card was not rendered.");
      }
      await user.click(within(firstSeriesCard).getByRole("button", { name: /只读查看素材库/ }));
      const firstLibrary = await screen.findByRole("region", { name: series.name + " · 素材库" });
      expect(await within(firstLibrary).findByText("正在读取角色…")).toBeInTheDocument();
      expect(listCharacters).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      const loginCard = await screen.findByRole("region", { name: "欢迎回来" });
      await user.click(within(loginCard).getByLabelText("账号"));
      await user.paste("second");
      await user.click(within(loginCard).getByLabelText("密码"));
      await user.paste("password");
      await user.click(within(loginCard).getByRole("button", { name: "登录工作台" }));

      const secondSeriesList = await screen.findByRole("region", { name: "我的剧集" });
      const secondSeriesHeading = await within(secondSeriesList)
        .findByRole("heading", { name: series.name });
      const secondSeriesCard = secondSeriesHeading.closest("article");
      if (!(secondSeriesCard instanceof HTMLElement)) {
        throw new Error("The second user series card was not rendered.");
      }
      await user.click(within(secondSeriesCard).getByRole("button", { name: /只读查看素材库/ }));
      const secondLibrary = await screen.findByRole("region", { name: series.name + " · 素材库" });
      const secondTarget = await within(secondLibrary).findByRole("heading", {
        name: nextUserAsset.name,
      });
      const secondTargetCard = secondTarget.closest("article");
      if (!(secondTargetCard instanceof HTMLElement)) {
        throw new Error("The second user's asset card was not rendered.");
      }
      const secondSearch = within(secondLibrary).getByRole("searchbox", { name: "搜索角色素材" });
      await user.click(secondSearch);
      await user.paste(" silver ");
      expect(secondSearch).toHaveValue(" silver ");
      expect(within(secondLibrary).getByText("显示 1 / 1 项素材")).toBeInTheDocument();

      await act(async () => {
        if (lateResult === "success") {
          oldDirectory.resolve([
            {
              ...characterSnapshot(series.id, "第一个账号迟到素材", "first-search-asset"),
              aliases: ["Legacy Search"],
            },
          ]);
          await oldDirectory.promise;
        } else {
          oldDirectory.reject(new ApiError("http", "expired", 401));
          await oldDirectory.promise.catch(() => undefined);
        }
      });

      expect(within(secondLibrary).getByRole("searchbox", { name: "搜索角色素材" })).toHaveValue(" silver ");
      expect(within(secondLibrary).getByText("显示 1 / 1 项素材")).toBeInTheDocument();
      expect(within(secondLibrary).getByRole("heading", { name: nextUserAsset.name })
        .closest("article")).toBe(secondTargetCard);
      expect(within(secondLibrary).queryByRole("heading", { name: "第一个账号迟到素材" }))
        .not.toBeInTheDocument();
      expect(screen.getByText(secondUser.username)).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("search-second-session");
      expect(window.localStorage.getItem(API_USER_KEY)).toBe(JSON.stringify(secondUser));
      expect(logout).toHaveBeenCalledTimes(1);
      expect(listCharacters).toHaveBeenCalledTimes(2);
    },
  );

  it("loads a demo asset's associated frames only after an explicit card action", async () => {
    const user = userEvent.setup();
    const services = createDemoServices(window.localStorage);
    const listChapters = vi.spyOn(services, "listChapters");
    renderWorkspace(services);
    const series = demoSeries[0]!;

    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));

    const seriesHeading = await screen.findByRole("heading", { name: series.name });
    const seriesCard = seriesHeading.closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("Series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
    await screen.findByRole("heading", { name: series.name + " · 素材库" });
    const assetHeading = await screen.findByRole("heading", { name: "林岚" });
    const assetCard = assetHeading.closest("article");
    if (!(assetCard instanceof HTMLElement)) {
      throw new Error("Asset card was not rendered.");
    }

    expect(listChapters).not.toHaveBeenCalled();
    await user.click(within(assetCard).getByRole("button", { name: "查看关联镜头：林岚" }));
    const usagePanel = await screen.findByRole("region", { name: "关联镜头" });
    expect(await within(usagePanel).findByText(/清晨的雾沿着旧街/)).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledWith(series.id, expect.any(AbortSignal));
  });

  it.each([
    {
      category: "characters" as const,
      categoryLabel: "角色",
      assetKind: "character" as const,
      assetId: "navigation-character",
      assetName: "导航角色",
    },
    {
      category: "scenes" as const,
      categoryLabel: "场景",
      assetKind: "scene" as const,
      assetId: "navigation-scene",
      assetName: "导航场景",
    },
    {
      category: "props" as const,
      categoryLabel: "道具",
      assetKind: "prop" as const,
      assetId: "navigation-prop",
      assetName: "导航道具",
    },
  ])(
    "navigates from the $category card using a fresh, unique non-first chapter read",
    async ({ category, categoryLabel, assetKind, assetId, assetName }) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const authenticatedUser: User = {
        ...fixtureUser,
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const sameTitle = "重名章节";
      const sourceFirst = chapterWithAssetReference(
        series.id,
        "source-first-" + category,
        sameTitle,
        "来源首项文字",
        assetKind,
        assetId,
      );
      const sourceTarget = chapterWithAssetReference(
        series.id,
        "source-target-" + category,
        sameTitle,
        "来源目标文字",
        assetKind,
        assetId,
      );
      const freshFirst = chapterWithAssetReference(
        series.id,
        "fresh-first-" + category,
        sameTitle,
        "新目录首项文字",
        assetKind,
        assetId,
      );
      const freshTarget = chapterWithAssetReference(
        series.id,
        sourceTarget.id,
        sameTitle,
        "新目录目标镜头文字",
        assetKind,
        assetId,
      );
      let currentCategory = category;
      const listChapters = vi.fn(async (_seriesId: string) => {
        const countForCategory = listChapters.mock.calls.length;
        if (currentCategory !== category) {
          throw new Error("Unexpected category change during a navigation test.");
        }
        if (countForCategory === 1) {
          return [sourceFirst, sourceTarget];
        }
        if (countForCategory === 2 || countForCategory === 3) {
          return [freshFirst, freshTarget];
        }
        throw new Error("Unexpected extra chapter read.");
      });
      const listStoryboardAssets = vi.fn(async () => []);
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => authenticatedUser,
        login: async () => { throw new Error("Not used."); },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters,
        listStoryboardAssets,
        listCharacters: async (seriesId) => [
          characterSnapshot(seriesId, "导航角色", assetId),
        ],
        listScenes: async (seriesId) => [
          sceneSnapshot(seriesId, "navigation-scene", "导航场景"),
        ],
        listProps: async (seriesId) => [
          propSnapshot(seriesId, "navigation-prop", "导航道具"),
        ],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout: vi.fn(),
      };
      window.localStorage.setItem(API_TOKEN_KEY, "navigation-session-token");
      renderWorkspace(services);

      await screen.findAllByRole("article");
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      await user.click(screen.getByRole("button", { name: /全部剧集/ }));
      await user.click(screen.getByRole("button", { name: /加载更多/ }));
      expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();

      const seriesCard = screen.getAllByRole("article")[0]!;
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
      await screen.findByRole("heading", { name: series.name + " · 素材库" });
      expect(listChapters).not.toHaveBeenCalled();

      if (category !== "characters") {
        await user.click(screen.getByRole("button", { name: categoryLabel }));
      }
      const assetHeading = await screen.findByRole("heading", { name: assetName });
      const assetCard = assetHeading.closest("article");
      if (!(assetCard instanceof HTMLElement)) {
        throw new Error("Asset card was not rendered.");
      }
      expect(listChapters).not.toHaveBeenCalled();

      await user.click(within(assetCard).getByRole("button", { name: "查看关联镜头：" + assetName }));
      const sourcePanel = await screen.findByRole("region", { name: "关联镜头" });
      expect(await within(sourcePanel).findByText("来源目标文字")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(1);

      const chapterLinks = within(sourcePanel).getAllByRole("button", {
        name: "查看对应章节：" + sameTitle,
      });
      expect(chapterLinks).toHaveLength(2);
      await user.click(chapterLinks[1]!);

      expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
      expect(await screen.findByText("新目录目标镜头文字")).toBeInTheDocument();
      await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
      expect(listChapters).toHaveBeenNthCalledWith(1, series.id, expect.any(AbortSignal));
      expect(listChapters).toHaveBeenNthCalledWith(2, series.id, expect.any(AbortSignal));
      expect(listStoryboardAssets).toHaveBeenCalledWith(
        series.id,
        freshTarget.id,
        expect.any(AbortSignal),
      );

      if (category === "characters") {
        expect(chapterNavigationProbe.consumedEpochs).toHaveLength(1);
        expect(chapterNavigationProbe.intent).toBeNull();
        const chapterRows = screen.getAllByRole("button", { name: new RegExp(sameTitle) });
        expect(chapterRows).toHaveLength(2);
        await user.click(chapterRows[0]!);
        expect(await screen.findByText("新目录首项文字")).toBeInTheDocument();
        await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(2));
        expect(listStoryboardAssets).toHaveBeenNthCalledWith(
          2,
          series.id,
          freshFirst.id,
          expect.any(AbortSignal),
        );

        await user.click(screen.getByRole("button", { name: "重新读取章节" }));
        expect(await screen.findByText("新目录首项文字")).toBeInTheDocument();
        expect(screen.queryByText("新目录目标镜头文字")).not.toBeInTheDocument();
        await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(3));
        await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(3));
        expect(listStoryboardAssets).toHaveBeenNthCalledWith(
          3,
          series.id,
          freshFirst.id,
          expect.any(AbortSignal),
        );
        expect(chapterNavigationProbe.consumedEpochs).toHaveLength(1);
        expect(chapterNavigationProbe.intent).toBeNull();
        expect(chapterNavigationProbe.abandonedEpochs).toHaveLength(0);
      }

      await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
      expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
      expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
      expect(currentCategory).toBe(category);
    },
  );

  it("rejects a captured asset navigation callback after the workspace changes series", async () => {
    const user = userEvent.setup();
    const sourceSeries = demoSeries[0]!;
    const nextSeries = demoSeries[1]!;
    const authenticatedUser: User = {
      ...fixtureUser,
      is_superuser: false,
      membership_type: "free",
      membership_expires_at: null,
      avatar_url: null,
      bio: null,
    };
    const sourceAssetId = "stale-source-character";
    const sourceFirst = chapterWithAssetReference(
      sourceSeries.id,
      "stale-first",
      "同名章节",
      "来源第一章文字",
      "character",
      sourceAssetId,
    );
    const sourceTarget = chapterWithAssetReference(
      sourceSeries.id,
      "stale-target",
      "同名章节",
      "来源目标文字",
      "character",
      sourceAssetId,
    );
    const freshFirst = chapterWithAssetReference(
      sourceSeries.id,
      "fresh-first",
      "同名章节",
      "新目录第一章文字",
      "character",
      sourceAssetId,
    );
    const freshTarget = chapterWithAssetReference(
      sourceSeries.id,
      sourceTarget.id,
      "同名章节",
      "新目录目标章节文字",
      "character",
      sourceAssetId,
    );
    let sourceChapterReads = 0;
    const listChapters = vi.fn(async (seriesId: string) => {
      if (seriesId !== sourceSeries.id) {
        return [];
      }
      sourceChapterReads += 1;
      return sourceChapterReads === 1
        ? [sourceFirst, sourceTarget]
        : [freshFirst, freshTarget];
    });
    const services: WorkspaceServices = {
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      restore: async () => authenticatedUser,
      login: async () => { throw new Error("Not used."); },
      listSeries: async () => demoSeries,
      listMyTeams: async () => [],
      listChapters,
      listStoryboardAssets: async () => [],
      listCharacters: async (seriesId) => [
        characterSnapshot(
          seriesId,
          seriesId === sourceSeries.id ? "来源素材角色" : "下一剧集角色",
          seriesId === sourceSeries.id ? sourceAssetId : "next-series-character",
        ),
      ],
      listScenes: async () => [],
      listProps: async () => [],
      getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
      getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
      listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout: vi.fn(),
    };
    window.localStorage.setItem(API_TOKEN_KEY, "stale-callback-session");
    renderWorkspace(services);

    await screen.findAllByRole("article");
    const sourceCard = screen.getByRole("heading", { name: sourceSeries.name }).closest("article");
    if (!(sourceCard instanceof HTMLElement)) {
      throw new Error("Source series card was not rendered.");
    }
    await user.click(within(sourceCard).getByRole("button", { name: /只读查看素材库/ }));
    await screen.findByRole("heading", { name: sourceSeries.name + " · 素材库" });
    await user.click(screen.getByRole("button", { name: "查看关联镜头：来源素材角色" }));

    const usagePanel = await screen.findByRole("region", { name: "关联镜头" });
    expect(await within(usagePanel).findByText("来源目标文字")).toBeInTheDocument();
    const chapterLinks = within(usagePanel).getAllByRole("button", { name: "查看对应章节：同名章节" });
    expect(chapterLinks).toHaveLength(2);
    await user.click(chapterLinks[1]!);
    expect(await screen.findByText("新目录目标章节文字")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);

    const staleTarget = chapterNavigationProbe.target;
    const staleCallback = chapterNavigationProbe.callback;
    expect(staleTarget).not.toBeNull();
    expect(staleCallback).not.toBeNull();
    expect(staleTarget?.chapterId).toBe(sourceTarget.id);
    expect(staleTarget?.isCurrent()).toBe(false);

    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    const nextSeriesCard = screen.getByRole("heading", { name: nextSeries.name }).closest("article");
    if (!(nextSeriesCard instanceof HTMLElement)) {
      throw new Error("Next series card was not rendered.");
    }
    await user.click(within(nextSeriesCard).getByRole("button", { name: /只读查看素材库/ }));
    expect(await screen.findByRole("heading", { name: nextSeries.name + " · 素材库" })).toBeInTheDocument();

    await act(async () => {
      staleCallback!(staleTarget!);
    });
    await act(async () => {
      staleCallback!({ ...staleTarget!, isCurrent: () => true });
    });

    expect(screen.getByRole("heading", { name: nextSeries.name + " · 素材库" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(listChapters.mock.calls.map(([seriesId]) => seriesId)).toEqual([sourceSeries.id, sourceSeries.id]);
  });

  it.each(["success", "401"] as const)(
    "does not let a late asset %s from a logged-out user affect the next login",
    async (lateResult) => {
      const user = userEvent.setup();
      const firstUser: User = {
        ...fixtureUser,
        id: "first-asset-user",
        username: "第一个账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = { ...firstUser, id: "second-asset-user", username: "第二个账号" };
      const oldRequest = deferred<Character[]>();
      let attempts = 0;
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => firstUser,
        login: async () => {
          window.localStorage.setItem(API_TOKEN_KEY, "next-session-token");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters: async () => [],
        listStoryboardAssets: async () => [],
        listCharacters: vi.fn(() => {
          attempts += 1;
          return attempts === 1
            ? oldRequest.promise
            : Promise.resolve([characterSnapshot(demoSeries[0]!.id, "新用户角色")]);
        }),
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "first-session-token");
      renderWorkspace(services);

      await screen.findAllByRole("article");
      const firstCard = screen.getByRole("heading", { name: demoSeries[0]!.name }).closest("article");
      if (!(firstCard instanceof HTMLElement)) {
        throw new Error("First series card was not rendered.");
      }
      await user.click(within(firstCard).getByRole("button", { name: /只读查看素材库/ }));
      expect(await screen.findByText("正在读取角色…")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "退出登录" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      await user.type(screen.getByLabelText("账号"), "second");
      await user.type(screen.getByLabelText("密码"), "pass");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findByRole("heading", { name: "我的剧集" });
      await screen.findAllByRole("article");

      const secondCard = screen.getByRole("heading", { name: demoSeries[0]!.name }).closest("article");
      if (!(secondCard instanceof HTMLElement)) {
        throw new Error("Second series card was not rendered.");
      }
      await user.click(within(secondCard).getByRole("button", { name: /只读查看素材库/ }));
      expect(await screen.findByRole("heading", { name: "新用户角色" })).toBeInTheDocument();

      await act(async () => {
        if (lateResult === "success") {
          oldRequest.resolve([characterSnapshot(demoSeries[0]!.id, "旧用户角色")]);
          await oldRequest.promise;
        } else {
          oldRequest.reject(new ApiError("http", "expired", 401));
          await oldRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByRole("heading", { name: "新用户角色" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "旧用户角色" })).not.toBeInTheDocument();
      expect(screen.getByText("第二个账号")).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("next-session-token");
    },
  );

  it.each(["success", "401"] as const)(
    "does not let a late chapters %s from a logged-out account affect the next login",
    async (lateResult) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const firstUser: User = {
        ...fixtureUser,
        id: "chapters-first-user",
        username: "旧章节账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = { ...firstUser, id: "chapters-second-user", username: "新章节账号" };
      const oldRequest = deferred<Chapter[]>();
      let activeUserId = firstUser.id;
      let firstRequest = true;
      const logout = vi.fn(() => {
        activeUserId = "anonymous";
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const listChapters = vi.fn((seriesId: string) => {
        if (activeUserId === firstUser.id && firstRequest) {
          firstRequest = false;
          return oldRequest.promise;
        }
        return Promise.resolve([
          chapterWithCharacterReference(
            seriesId,
            "new-account-chapter",
            "新账号章节",
            "新账号镜头文字",
            "shared-character-id",
          ),
        ]);
      });
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => firstUser,
        login: async () => {
          activeUserId = secondUser.id;
          window.localStorage.setItem(API_TOKEN_KEY, "chapters-new-session-token");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters,
        listStoryboardAssets: async () => [],
        listCharacters: async (seriesId) => [
          characterSnapshot(
            seriesId,
            activeUserId === firstUser.id ? "旧账号角色" : "新账号角色",
          ),
        ],
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "chapters-old-session-token");
      renderWorkspace(services);

      await screen.findAllByRole("article");
      const firstCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(firstCard instanceof HTMLElement)) {
        throw new Error("First series card was not rendered.");
      }
      await user.click(within(firstCard).getByRole("button", { name: /只读查看素材库/ }));
      expect(await screen.findByRole("heading", { name: "旧账号角色" })).toBeInTheDocument();
      expect(listChapters).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "查看关联镜头：旧账号角色" }));
      await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(1));
      await user.click(screen.getByRole("button", { name: "退出登录" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();

      await user.type(screen.getByLabelText("账号"), "second");
      await user.type(screen.getByLabelText("密码"), "pass");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");
      const secondCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(secondCard instanceof HTMLElement)) {
        throw new Error("Second account series card was not rendered.");
      }
      await user.click(within(secondCard).getByRole("button", { name: /只读查看素材库/ }));
      expect(await screen.findByRole("heading", { name: "新账号角色" })).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(1);

      await user.click(screen.getByRole("button", { name: "查看关联镜头：新账号角色" }));
      expect(await screen.findByText("新账号镜头文字")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(2);

      await act(async () => {
        if (lateResult === "success") {
          oldRequest.resolve([
            chapterWithCharacterReference(
              series.id,
              "old-account-chapter",
              "旧账号章节",
              "旧账号迟到镜头文字",
              "shared-character-id",
            ),
          ]);
          await oldRequest.promise;
        } else {
          oldRequest.reject(new ApiError("http", "expired", 401));
          await oldRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByText("新账号镜头文字")).toBeInTheDocument();
      expect(screen.queryByText("旧账号迟到镜头文字")).not.toBeInTheDocument();
      expect(screen.getByText("新章节账号")).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("chapters-new-session-token");
      expect(listChapters).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["success", "401"] as const)(
    "ignores a late asset-navigation chapter %s after a new login",
    async (lateResult) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const firstUser: User = {
        ...fixtureUser,
        id: "navigation-first-user",
        username: "章节来源账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = { ...firstUser, id: "navigation-second-user", username: "章节新账号" };
      const oldTargetRequest = deferred<Chapter[]>();
      const sourceChapter = chapterWithAssetReference(
        series.id,
        "source-navigation-chapter",
        "旧账号来源章节",
        "旧账号来源命中镜头",
        "character",
        "shared-character-id",
      );
      const oldTargetChapter = chapterWithAssetReference(
        series.id,
        "old-account-target-chapter",
        "旧账号迟到目标章节",
        "旧账号迟到目标镜头文字",
        "character",
        "shared-character-id",
      );
      const newAccountChapter = chapterWithAssetReference(
        series.id,
        "new-account-default-chapter",
        "新账号首章",
        "新账号当前镜头文字",
        "character",
        "shared-character-id",
      );
      let activeUserId = firstUser.id;
      let firstUserChapterReads = 0;
      const listChapters = vi.fn((_seriesId: string) => {
        if (activeUserId === firstUser.id) {
          firstUserChapterReads += 1;
          return firstUserChapterReads === 1
            ? Promise.resolve([sourceChapter])
            : oldTargetRequest.promise;
        }
        return Promise.resolve([newAccountChapter]);
      });
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => firstUser,
        login: async () => {
          activeUserId = secondUser.id;
          window.localStorage.setItem(API_TOKEN_KEY, "navigation-new-session-token");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters,
        listStoryboardAssets: vi.fn(async () => []),
        listCharacters: async (seriesId) => [
          characterSnapshot(
            seriesId,
            activeUserId === firstUser.id ? "旧账号角色" : "新账号角色",
          ),
        ],
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "navigation-old-session-token");
      renderWorkspace(services);

      await screen.findAllByRole("article");
      const firstSeriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(firstSeriesCard instanceof HTMLElement)) {
        throw new Error("First series card was not rendered.");
      }
      await user.click(within(firstSeriesCard).getByRole("button", { name: /只读查看素材库/ }));
      await screen.findByRole("heading", { name: "旧账号角色" });
      await user.click(screen.getByRole("button", { name: "查看关联镜头：旧账号角色" }));
      const sourcePanel = await screen.findByRole("region", { name: "关联镜头" });
      expect(await within(sourcePanel).findByText("旧账号来源命中镜头")).toBeInTheDocument();
      await user.click(
        within(sourcePanel).getByRole("button", { name: "查看对应章节：旧账号来源章节" }),
      );
      expect(await screen.findByText("正在读取章节…")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(2);

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      await user.type(screen.getByLabelText("账号"), "second");
      await user.type(screen.getByLabelText("密码"), "pass");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");

      const secondSeriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(secondSeriesCard instanceof HTMLElement)) {
        throw new Error("Second account series card was not rendered.");
      }
      await user.click(within(secondSeriesCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByText("新账号当前镜头文字")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(3);
      const listStoryboardAssets = services.listStoryboardAssets as ReturnType<typeof vi.fn>;
      await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));

      await act(async () => {
        if (lateResult === "success") {
          oldTargetRequest.resolve([oldTargetChapter]);
          await oldTargetRequest.promise;
        } else {
          oldTargetRequest.reject(new ApiError("http", "expired", 401));
          await oldTargetRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByText("新账号当前镜头文字")).toBeInTheDocument();
      expect(screen.queryByText("旧账号迟到目标镜头文字")).not.toBeInTheDocument();
      expect(screen.getByText("章节新账号")).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("navigation-new-session-token");
      expect(listChapters).toHaveBeenCalledTimes(3);
      expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    },
  );

  it("restores a demo session after remount and clears only demo data on logout", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(API_TOKEN_KEY, "api-session");
    renderWorkspace(createDemoServices(window.localStorage));
    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findByRole("heading", { name: "我的剧集" });

    cleanup();
    renderWorkspace(createDemoServices(window.localStorage));
    await screen.findByRole("heading", { name: "我的剧集" });
    await user.click(screen.getByRole("button", { name: "退出登录" }));

    expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
    expect(window.localStorage.getItem(MOCK_SESSION_KEY)).toBeNull();
    expect(window.localStorage.getItem(MOCK_USER_KEY)).toBeNull();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("api-session");
  });

  it("opens chapters without losing the mounted list filter or pagination state", async () => {
    const user = userEvent.setup();
    const { container } = renderWorkspace(createDemoServices(window.localStorage));
    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await user.click(screen.getByRole("button", { name: /全部剧集/ }));
    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();

    const firstCard = screen.getAllByRole("article")[0]!;
    await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
    expect(await screen.findByRole("heading", { name: "第一章 · 雾起" })).toBeInTheDocument();
    expect(screen.getByText(/当前由 周编剧 编辑/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新读取章节" })).toBeInTheDocument();

    const hiddenList = container.querySelector(".series-list-view") as HTMLElement;
    expect(hiddenList.hidden).toBe(true);
    expect(hiddenList.hasAttribute("inert")).toBe(true);
    expect(screen.queryByRole("button", { name: "团队剧集" })).not.toBeInTheDocument();
    // jsdom does not simulate native focus movement when a focused subtree becomes hidden; the browser check covers it.

    await user.click(screen.getByRole("button", { name: /第二章/ }));
    expect(await screen.findByText(/她拆开信封/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));

    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("ignores a late series response after logout", async () => {
    const user = userEvent.setup();
    const pendingList = deferred<Series[]>();
    const authenticatedUser: User = {
      ...fixtureUser,
      is_superuser: false,
      membership_type: "free",
      membership_expires_at: null,
      avatar_url: null,
      bio: null,
    };
    const services: WorkspaceServices = {
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      restore: async () => authenticatedUser,
      login: async () => authenticatedUser,
      listSeries: vi.fn(() => pendingList.promise),
      listMyTeams: async () => [],
      listChapters: async () => [],
      listStoryboardAssets: async () => [],
      listCharacters: async () => [],
      listScenes: async () => [],
      listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout: vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      }),
    };
    renderWorkspace(services);

    expect(await screen.findByText("正在读取剧集…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
    pendingList.resolve(demoSeries);

    await waitFor(() => expect(screen.getByRole("heading", { name: "欢迎回来" })).toBeInTheDocument());
    expect(screen.queryByRole("heading", { name: "我的剧集" })).not.toBeInTheDocument();
    expect(services.logout).toHaveBeenCalledTimes(1);
  });

  it("ignores a late chapter response after returning to the list and opening another series", async () => {
    const user = userEvent.setup();
    const firstSeries = demoSeries[0]!;
    const secondSeries = demoSeries[1]!;
    const staleChapterRequest = deferred<Chapter[]>();
    const oldChapter = chapterSnapshot(firstSeries.id, "old-series-chapter", "旧剧集迟到章节", null);
    const newChapter = chapterSnapshot(secondSeries.id, "new-series-chapter", "新剧集章节", null);
    const authenticatedUser: User = {
      ...fixtureUser,
      is_superuser: false,
      membership_type: "free",
      membership_expires_at: null,
      avatar_url: null,
      bio: null,
    };
    const listChapters = vi.fn((seriesId: string) => (
      seriesId === firstSeries.id ? staleChapterRequest.promise : Promise.resolve([newChapter])
    ));
    const services: WorkspaceServices = {
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      restore: async () => authenticatedUser,
      login: async () => authenticatedUser,
      listSeries: async () => demoSeries,
      listMyTeams: async () => [],
      listChapters,
      listStoryboardAssets: vi.fn(async () => []),
      listCharacters: async () => [],
      listScenes: async () => [],
      listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout: vi.fn(),
    };
    renderWorkspace(services);

    await screen.findAllByRole("article");
    const firstCard = screen.getByRole("heading", { name: firstSeries.name }).closest("article");
    if (!(firstCard instanceof HTMLElement)) {
      throw new Error("First series card was not rendered.");
    }
    await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
    expect(await screen.findByText("正在读取章节…")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    await screen.findByRole("heading", { name: "我的剧集" });
    const secondCard = screen.getByRole("heading", { name: secondSeries.name }).closest("article");
    if (!(secondCard instanceof HTMLElement)) {
      throw new Error("Second series card was not rendered.");
    }
    await user.click(within(secondCard).getByRole("button", { name: /只读查看章节/ }));
    expect(await screen.findByRole("heading", { name: "新剧集章节" })).toBeInTheDocument();

    await act(async () => {
      staleChapterRequest.resolve([oldChapter]);
      await staleChapterRequest.promise;
    });

    expect(screen.getByRole("heading", { name: "新剧集章节" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "旧剧集迟到章节" })).not.toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(services.listStoryboardAssets).not.toHaveBeenCalled();
  });

  it("ignores an old asset response after returning and opening another series", async () => {
    const user = userEvent.setup();
    const firstSeries = demoSeries[0]!;
    const secondSeries = demoSeries[1]!;
    const firstAssetRequest = deferred<import("../shared/api/contracts").Character[]>();
    const listCharacters = vi.fn((seriesId: string) => (
      seriesId === firstSeries.id
        ? firstAssetRequest.promise
        : Promise.resolve([characterSnapshot(secondSeries.id, "第二部剧集角色")])
    ));
    const services: WorkspaceServices = {
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      restore: async () => ({
        ...fixtureUser,
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      }),
      login: async () => { throw new Error("Not used."); },
      listSeries: async () => demoSeries,
      listMyTeams: async () => [],
      listChapters: async () => [],
      listStoryboardAssets: async () => [],
      listCharacters,
      listScenes: async () => [],
      listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout: vi.fn(),
    };
    renderWorkspace(services);
    await screen.findAllByRole("article");

    const firstCard = screen.getByRole("heading", { name: firstSeries.name }).closest("article");
    if (!(firstCard instanceof HTMLElement)) {
      throw new Error("First series card was not rendered.");
    }
    await user.click(within(firstCard).getByRole("button", { name: /只读查看素材库/ }));
    expect(await screen.findByText("正在读取角色…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));

    const secondCard = screen.getByRole("heading", { name: secondSeries.name }).closest("article");
    if (!(secondCard instanceof HTMLElement)) {
      throw new Error("Second series card was not rendered.");
    }
    await user.click(within(secondCard).getByRole("button", { name: /只读查看素材库/ }));
    expect(await screen.findByRole("heading", { name: "第二部剧集角色" })).toBeInTheDocument();

    await act(async () => {
      firstAssetRequest.resolve([characterSnapshot(firstSeries.id, "迟到的第一部角色")]);
      await firstAssetRequest.promise;
    });
    expect(screen.getByRole("heading", { name: "第二部剧集角色" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "迟到的第一部角色" })).not.toBeInTheDocument();
    expect(listCharacters).toHaveBeenCalledTimes(2);
  });

  it.each(["success", "401"] as const)(
    "keeps a new user's private notes after returning to another series and settling old requests (%s)",
    async (result) => {
      const user = userEvent.setup();
      const firstSeries = demoSeries[0]!;
      const secondSeries = demoSeries[1]!;
      const firstUser: User = {
        ...fixtureUser,
        id: "notes-first-user",
        username: "第一个记录账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = { ...firstUser, id: "notes-second-user", username: "第二个记录账号" };
      const firstChapterData = chapterWithStoryboardAssets(
        firstSeries.id,
        "private-notes-chapter-a",
        "第一剧集章节",
        ["notes-resume-frame", "notes-locate-frame"],
      );
      const otherChapter = chapterSnapshot(secondSeries.id, "private-notes-chapter-b", "第二剧集章节", null);
      const firstAssets = [
        storyboardAssetSnapshot(firstSeries.id, firstChapterData.id, "notes-resume-frame", 10),
        storyboardAssetSnapshot(firstSeries.id, firstChapterData.id, "notes-locate-frame", 0),
      ];
      const noteFrames = await Promise.all(firstAssets.map(async (asset, frameIndex) => {
        const assetImageDigest = await digestPersonalProductionMediaIdentity(asset.image_url);
        if (assetImageDigest === null) {
          throw new Error("A fixture image URL must produce a media identity digest.");
        }
        return {
          frame_index: frameIndex,
          storyboard_asset_id: asset.id,
          media_revision: 1,
          source_valid: true,
          asset_image_digest: assetImageDigest,
          preview_digest: null,
          invalid_reason: null,
        };
      }));
      const firstSessionNotes = parsePersonalProductionSnapshot({
        chapter_id: firstChapterData.id,
        revision: 1,
        media_state: "ready",
        frames: noteFrames,
        frame_notes: {
          "notes-resume-frame": {
            status: "approved",
            note: "第一账号的续作记录",
            approved_media_revision: 1,
          },
          "notes-locate-frame": {
            status: "needs_revision",
            note: "第一账号的私人记录",
          },
        },
        resume_frame_id: "notes-resume-frame",
      }, firstChapterData.id);
      const secondSessionNotes = parsePersonalProductionSnapshot({
        chapter_id: firstChapterData.id,
        revision: 1,
        media_state: "ready",
        frames: noteFrames,
        frame_notes: {
          "notes-resume-frame": {
            status: "approved",
            note: "第二账号的续作记录",
            approved_media_revision: 1,
          },
          "notes-locate-frame": {
            status: "approved",
            note: "第二账号的私人记录",
            approved_media_revision: 1,
            needs_reconfirmation: true,
          },
        },
        resume_frame_id: "notes-resume-frame",
      }, firstChapterData.id);
      const pendingRequests: Array<{ chapterId: string; request: ReturnType<typeof deferred<ReturnType<typeof personalNotesSnapshot>>> }> = [];
      let activeUserId = firstUser.id;
      const getPersonalProductionNotes = vi.fn((chapterId: string) => {
        if (activeUserId === secondUser.id) {
          return Promise.resolve(chapterId === firstChapterData.id
            ? secondSessionNotes
            : personalNotesSnapshot(chapterId, "第二账号的私人记录"));
        }
        if (chapterId === firstChapterData.id) {
          return Promise.resolve(firstSessionNotes);
        }
        const request = deferred<ReturnType<typeof personalNotesSnapshot>>();
        pendingRequests.push({ chapterId, request });
        return request.promise;
      });
      const listChapters = vi.fn(async (seriesId: string) => (
        seriesId === firstSeries.id ? [firstChapterData] : [otherChapter]
      ));
      const listStoryboardAssets = vi.fn(async (seriesId: string, chapterId: string) => (
        seriesId === firstSeries.id && chapterId === firstChapterData.id ? firstAssets : []
      ));
      const listMyTasks = vi.fn(async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }));
      const logout = vi.fn(() => {
        activeUserId = "anonymous";
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => firstUser,
        login: async () => {
          activeUserId = secondUser.id;
          window.localStorage.setItem(API_TOKEN_KEY, "notes-second-session-token");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters,
        listStoryboardAssets,
        listCharacters: async () => [],
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes,
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks,
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "notes-first-session-token");
      renderWorkspace(services);
      let seriesList = await screen.findByRole("region", { name: "我的剧集" });
      const getSeriesCard = async (name: string): Promise<HTMLElement> => {
        const heading = await within(seriesList).findByRole("heading", { name });
        const card = heading.closest("article");
        if (!(card instanceof HTMLElement)) {
          throw new Error("Series card was not rendered: " + name);
        }
        return card;
      };
      const firstCard = await getSeriesCard(firstSeries.name);
      await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
      const firstChapterRegion = await screen.findByRole("region", { name: firstSeries.name });
      await user.click(within(firstChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      const firstNotes = await within(firstChapterRegion).findByRole("region", { name: "我的制作记录" });
      expect(await within(firstNotes).findByText("第一账号的私人记录")).toBeInTheDocument();
      const firstFilterGroup = within(firstNotes).getByRole("group", { name: "按状态筛选" });
      expect(within(firstFilterGroup).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
      expect(within(firstFilterGroup).getByRole("button", { name: "待修 1" })).toBeInTheDocument();
      expect(within(firstFilterGroup).getByRole("button", { name: "已认可 1" })).toBeInTheDocument();

      const scrollProbe = installScrollIntoViewProbe();
      const firstReadsBeforeFilter = {
        notes: getPersonalProductionNotes.mock.calls.length,
        chapters: listChapters.mock.calls.length,
        assets: listStoryboardAssets.mock.calls.length,
      };
      await user.click(within(firstFilterGroup).getByRole("button", { name: "待修 1" }));
      expect(within(firstNotes).getByText("第一账号的私人记录")).toBeInTheDocument();
      expect(within(firstNotes).queryByText("第一账号的续作记录")).not.toBeInTheDocument();
      const firstTargetArticle = within(firstChapterRegion).getByRole("article", { name: "第一剧集章节 · 镜头 2" });
      const firstLocateButton = within(firstNotes).getByRole("button", { name: "定位到记录镜头 2" });
      await user.click(firstLocateButton);
      await waitFor(() => expect(firstTargetArticle).toHaveFocus());
      expect(within(firstChapterRegion).getByText("已定位到记录镜头 2。")).toBeInTheDocument();
      expect(scrollProbe.targets).toContain(firstTargetArticle);

      const approvedFilterButton = within(firstFilterGroup).getByRole("button", { name: "已认可 1" });
      approvedFilterButton.focus();
      await user.keyboard("{Enter}");
      expect(approvedFilterButton).toHaveAttribute("aria-pressed", "true");
      expect(within(firstChapterRegion).queryByText("已定位到记录镜头 2。")).not.toBeInTheDocument();
      expect(within(firstNotes).getByText("第一账号的续作记录")).toBeInTheDocument();
      expect(within(firstNotes).getByRole("heading", { name: "我的制作记录" })).toBeInTheDocument();

      await user.click(within(firstFilterGroup).getByRole("button", { name: "待修 1" }));
      await user.click(within(firstNotes).getByRole("button", { name: "定位到记录镜头 2" }));
      await waitFor(() => expect(firstTargetArticle).toHaveFocus());
      expect(within(firstChapterRegion).getByText("已定位到记录镜头 2。")).toBeInTheDocument();
      expect(within(firstChapterRegion).getByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();
      await user.click(within(firstChapterRegion).getByRole("button", { name: "定位到续作镜头" }));
      const firstResumeArticle = within(firstChapterRegion).getByRole("article", { name: "第一剧集章节 · 镜头 1" });
      await waitFor(() => expect(firstResumeArticle).toHaveFocus());
      expect(scrollProbe.targets).toContain(firstResumeArticle);
      expect(within(firstFilterGroup).getByRole("button", { name: "待修 1" })).toHaveAttribute("aria-pressed", "true");
      expect(getPersonalProductionNotes).toHaveBeenCalledTimes(firstReadsBeforeFilter.notes);
      expect(listChapters).toHaveBeenCalledTimes(firstReadsBeforeFilter.chapters);
      expect(listStoryboardAssets).toHaveBeenCalledTimes(firstReadsBeforeFilter.assets);

      await user.click(within(firstChapterRegion).getByRole("button", { name: "返回剧集列表" }));
      seriesList = await screen.findByRole("region", { name: "我的剧集" });
      const secondCard = await getSeriesCard(secondSeries.name);
      await user.click(within(secondCard).getByRole("button", { name: /只读查看章节/ }));
      const otherSeriesChapterRegion = await screen.findByRole("region", { name: secondSeries.name });
      await user.click(within(otherSeriesChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      await waitFor(() => expect(pendingRequests).toHaveLength(1));

      await user.click(within(otherSeriesChapterRegion).getByRole("button", { name: "返回剧集列表" }));
      await user.click(screen.getByRole("button", { name: "退出" }));
      const loginCard = await screen.findByRole("region", { name: "欢迎回来" });
      const accountInput = within(loginCard).getByLabelText("账号");
      const passwordInput = within(loginCard).getByLabelText("密码");
      await user.click(accountInput);
      await user.paste("second");
      await user.click(passwordInput);
      await user.paste("pass");
      await user.click(within(loginCard).getByRole("button", { name: "登录工作台" }));
      seriesList = await screen.findByRole("region", { name: "我的剧集" });
      await user.click(within(seriesList).getByRole("button", { name: "团队剧集" }));
      await user.click(within(seriesList).getByRole("button", { name: /全部剧集/ }));
      await user.click(within(seriesList).getByRole("button", { name: /加载更多/ }));
      expect(within(seriesList).getByText("显示 24 / 24 部")).toBeInTheDocument();
      const secondSessionCard = await getSeriesCard(firstSeries.name);
      await user.click(within(secondSessionCard).getByRole("button", { name: /只读查看章节/ }));
      const newUserChapterRegion = await screen.findByRole("region", { name: firstSeries.name });
      await user.click(within(newUserChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      const secondNotes = await within(newUserChapterRegion).findByRole("region", { name: "我的制作记录" });
      expect(await within(secondNotes).findByText("第二账号的私人记录")).toBeInTheDocument();
      const secondFilterGroup = within(secondNotes).getByRole("group", { name: "按状态筛选" });
      expect(within(secondFilterGroup).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
      expect(within(secondFilterGroup).getByRole("button", { name: "待修 0" })).toBeInTheDocument();
      expect(within(secondFilterGroup).getByRole("button", { name: "待重新确认 1" })).toBeInTheDocument();
      expect(within(secondFilterGroup).getByRole("button", { name: "已认可 1" })).toBeInTheDocument();

      await act(async () => {
        for (const { chapterId, request } of pendingRequests) {
          if (result === "success") {
            request.resolve(personalNotesSnapshot(chapterId, "旧账号迟到的私人记录"));
            await request.promise;
          } else {
            request.reject(new ApiError("http", "expired", 401));
            await request.promise.catch(() => undefined);
          }
        }
      });

      expect(within(newUserChapterRegion).getByRole("heading", { name: "第一剧集章节" })).toBeInTheDocument();
      expect(within(secondNotes).getByText("第二账号的私人记录")).toBeInTheDocument();
      expect(within(secondNotes).queryByText("旧账号迟到的私人记录")).not.toBeInTheDocument();
      expect(screen.getByText("第二个记录账号")).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("notes-second-session-token");
      expect(getPersonalProductionNotes).toHaveBeenCalledTimes(3);
      expect(within(secondFilterGroup).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
      await user.click(within(secondFilterGroup).getByRole("button", { name: "待重新确认 1" }));
      expect(within(secondNotes).getByText("第二账号的私人记录")).toBeInTheDocument();
      expect(within(secondNotes).queryByText("第二账号的续作记录")).not.toBeInTheDocument();

      expect(within(newUserChapterRegion).getByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();
      await user.click(within(newUserChapterRegion).getByRole("button", { name: "定位到续作镜头" }));
      const resumeArticle = within(newUserChapterRegion).getByRole("article", { name: "第一剧集章节 · 镜头 1" });
      await waitFor(() => expect(resumeArticle).toHaveFocus());
      expect(scrollProbe.targets).toContain(resumeArticle);

      const targetArticle = within(newUserChapterRegion).getByRole("article", { name: "第一剧集章节 · 镜头 2" });
      const locateNoteButton = within(secondNotes).getByRole("button", { name: "定位到记录镜头 2" });
      const readsBeforeLocate = {
        notes: getPersonalProductionNotes.mock.calls.length,
        chapters: listChapters.mock.calls.length,
        assets: listStoryboardAssets.mock.calls.length,
      };
      for (let attempt = 0; attempt < 2; attempt += 1) {
        await user.click(locateNoteButton);
        await waitFor(() => expect(targetArticle).toHaveFocus());
        expect(scrollProbe.targets).toContain(targetArticle);
      }

      expect(within(secondNotes).getByText("第二账号的私人记录")).toBeInTheDocument();
      expect(within(newUserChapterRegion).getByRole("button", { name: "定位到续作镜头" })).toBeInTheDocument();
      expect(within(secondNotes).getByRole("heading", { name: "我的制作记录" })).toBeInTheDocument();
      expect(within(newUserChapterRegion).getByText("已定位到记录镜头 2。")).toBeInTheDocument();
      expect(getPersonalProductionNotes).toHaveBeenCalledTimes(readsBeforeLocate.notes);
      expect(listChapters).toHaveBeenCalledTimes(readsBeforeLocate.chapters);
      expect(listStoryboardAssets).toHaveBeenCalledTimes(readsBeforeLocate.assets);

      const childControl = within(targetArticle).getByRole("button", { name: "查看镜头 2 的关联素材" });
      await act(async () => childControl.focus());
      expect(within(newUserChapterRegion).queryByText("已定位到记录镜头 2。")).not.toBeInTheDocument();
      expect(within(secondNotes).getByRole("heading", { name: "我的制作记录" })).toBeInTheDocument();

      if (result === "success") {
        await user.click(
          within(screen.getByRole("navigation", { name: "主要导航" }))
            .getByRole("button", { name: "我的任务" }),
        );
        expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
        await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(1));
        expect(screen.queryByRole("heading", { name: "我的制作记录" })).not.toBeInTheDocument();

        await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
        expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
        expect(within(seriesList).getByText("显示 24 / 24 部")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
        expect(listMyTasks).toHaveBeenCalledTimes(1);
        expect(getPersonalProductionNotes).toHaveBeenCalledTimes(readsBeforeLocate.notes);
        expect(listChapters).toHaveBeenCalledTimes(readsBeforeLocate.chapters);
        expect(listStoryboardAssets).toHaveBeenCalledTimes(readsBeforeLocate.assets);
      }
    },
  );

  it("locates the first rough-cut row by raw asset ID and preserves the list after task navigation", async () => {
    const user = userEvent.setup();
    const series = demoSeries[0]!;
    const chapterId = "rough-cut-frame-navigation-chapter";
    const firstAssetId = "rough-cut-frame-first";
    const targetAssetId = "rough-cut-frame-target";
    const chapter = chapterWithStoryboardAssets(
      series.id,
      chapterId,
      "粗剪定位章节",
      [firstAssetId, targetAssetId],
    );
    const assets = [
      storyboardAssetSnapshot(series.id, chapterId, firstAssetId, 20),
      storyboardAssetSnapshot(series.id, chapterId, targetAssetId, 0),
    ];
    const listChapters = vi.fn(async () => [chapter]);
    const listStoryboardAssets = vi.fn(async () => assets);
    const getPersonalRoughCut = vi.fn(async (requestedChapterId: string) => {
      const snapshot = personalRoughCutFrameSnapshot(
        requestedChapterId,
        "粗剪首项应按原始 ID 对应第二镜头",
        targetAssetId,
      );
      snapshot.frames[0] = {
        ...snapshot.frames[0]!,
        frame_index: 1,
        included: false,
        pending: true,
      };
      snapshot.frames.push({
        asset_id: firstAssetId,
        frame_index: 0,
        text: "草稿列表第二项映射第一镜头",
        preview_url: null,
        missing_reason: null,
        included: true,
        pending: false,
      });
      return snapshot;
    });
    const listMyTasks = vi.fn(async () => myTaskPage([
      myTaskRecord("rough-cut-navigation-task"),
    ], 1));
    const baseServices = createDemoServices(window.localStorage);
    const services: WorkspaceServices = {
      ...baseServices,
      listChapters,
      listStoryboardAssets,
      getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
      getPersonalRoughCut,
      listMyTasks,
    };
    const scrollProbe = installScrollIntoViewProbe();
    const { container } = renderWorkspace(services);

    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.click(screen.getByLabelText("账号"));
    await user.paste("demo");
    await user.click(screen.getByLabelText("密码"));
    await user.paste("demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await user.click(screen.getByRole("button", { name: /全部剧集/ }));
    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();

    const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("The demo series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    expect(await screen.findByRole("heading", { name: chapter.title })).toBeInTheDocument();
    await waitFor(() => {
      expect(listChapters).toHaveBeenCalledTimes(1);
      expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    });

    await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
    expect(await screen.findByText("粗剪首项应按原始 ID 对应第二镜头")).toBeInTheDocument();
    const targetArticle = screen.getByRole("article", { name: "粗剪定位章节 · 镜头 2" });
    const locateButtonName = "定位到对应镜头 2";
    const roughCutPanel = screen.getByRole("region", { name: "我的粗剪草稿" });
    const statusFilters = within(roughCutPanel).getByRole("group", { name: "按草稿状态筛选" });

    expect(within(statusFilters).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
    expect(within(statusFilters).getByRole("button", { name: "已纳入 1" })).toBeInTheDocument();
    expect(within(statusFilters).getByRole("button", { name: "已排除 1" })).toBeInTheDocument();
    expect(within(statusFilters).getByRole("button", { name: "待安排 1" })).toBeInTheDocument();

    await user.click(within(statusFilters).getByRole("button", { name: "已排除 1" }));
    expect(within(statusFilters).getByRole("button", { name: "已排除 1" })).toHaveAttribute("aria-pressed", "true");
    expect(within(roughCutPanel).getByText("草稿列表第 1 项 · 读取时章节第 2 个镜头")).toBeInTheDocument();
    expect(within(roughCutPanel).getByText("粗剪首项应按原始 ID 对应第二镜头")).toBeInTheDocument();
    expect(within(roughCutPanel).queryByText("草稿列表第二项映射第一镜头")).not.toBeInTheDocument();

    await user.click(within(roughCutPanel).getByRole("button", { name: locateButtonName }));
    await waitFor(() => expect(targetArticle).toHaveFocus());
    expect(scrollProbe.targets).toContain(targetArticle);
    expect(screen.getByText("已定位到粗剪镜头 2。")).toBeInTheDocument();

    const pendingFilter = within(statusFilters).getByRole("button", { name: "待安排 1" });
    pendingFilter.focus();
    expect(pendingFilter).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(pendingFilter).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("已定位到粗剪镜头 2。")).not.toBeInTheDocument();
    expect(within(roughCutPanel).getByText("粗剪首项应按原始 ID 对应第二镜头")).toBeInTheDocument();
    expect(within(roughCutPanel).queryByText("草稿列表第二项映射第一镜头")).not.toBeInTheDocument();

    await user.click(within(roughCutPanel).getByRole("button", { name: locateButtonName }));
    await waitFor(() => expect(targetArticle).toHaveFocus());
    expect(scrollProbe.targets).toContain(targetArticle);
    expect(screen.getByText("已定位到粗剪镜头 2。")).toBeInTheDocument();
    await user.click(within(statusFilters).getByRole("button", { name: "已纳入 1" }));
    expect(within(statusFilters).getByRole("button", { name: "已纳入 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("已定位到粗剪镜头 2。")).not.toBeInTheDocument();
    expect(within(roughCutPanel).getByText("草稿列表第 2 项 · 读取时章节第 1 个镜头")).toBeInTheDocument();
    expect(within(roughCutPanel).getByText("草稿列表第二项映射第一镜头")).toBeInTheDocument();
    expect(within(roughCutPanel).queryByText("粗剪首项应按原始 ID 对应第二镜头")).not.toBeInTheDocument();

    await user.click(within(statusFilters).getByRole("button", { name: "全部 2" }));
    expect(within(statusFilters).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
    expect(within(roughCutPanel).getByText("粗剪首项应按原始 ID 对应第二镜头")).toBeInTheDocument();
    expect(within(roughCutPanel).getByText("草稿列表第二项映射第一镜头")).toBeInTheDocument();

    expect(roughCutPanel).toBeInTheDocument();
    expect(getPersonalRoughCut).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    const taskNavigation = within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" });
    await user.click(taskNavigation);
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
    expect(listMyTasks).toHaveBeenCalledTimes(1);
    expect(listChapters).toHaveBeenCalledTimes(1);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
    expect(getPersonalRoughCut).toHaveBeenCalledTimes(1);
    expect(container.querySelector<HTMLElement>(".series-list-view")?.hidden).toBe(false);
  });

  it.each(["success", "401"] as const)(
    "does not let a late rough-cut %s from a logged-out session affect the next user",
    async (lateResult) => {
      const user = userEvent.setup();
      const firstSeries = demoSeries[0]!;
      const firstUser: User = {
        ...fixtureUser,
        id: "rough-cut-first-user",
        username: "粗剪旧账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = {
        ...firstUser,
        id: "rough-cut-second-user",
        username: "粗剪新账号",
      };
      const sharedChapter = chapterWithStoryboardAssets(
        firstSeries.id,
        "shared-rough-cut-chapter",
        "同章粗剪",
        ["rough-cut-session-first", "rough-cut-session-target"],
      );
      const chapterAssets = [
        storyboardAssetSnapshot(firstSeries.id, sharedChapter.id, "rough-cut-session-first", 20),
        storyboardAssetSnapshot(firstSeries.id, sharedChapter.id, "rough-cut-session-target", 0),
      ];
      const listChapters = vi.fn(async () => [sharedChapter]);
      const listStoryboardAssets = vi.fn(async () => chapterAssets);
      const oldRequest = deferred<PersonalRoughCutSnapshot>();
      let activeUserId = firstUser.id;
      let firstUserReadCount = 0;
      const firstUserFrames = [
        {
          asset_id: "rough-cut-session-target",
          frame_index: 1,
          text: "旧账号首项的粗剪正文",
          preview_url: null,
          missing_reason: null,
          included: false,
          pending: true,
        },
        {
          asset_id: "rough-cut-session-first",
          frame_index: 0,
          text: "旧账号第二项的粗剪正文",
          preview_url: null,
          missing_reason: null,
          included: true,
          pending: false,
        },
      ];
      const secondUserFrames = [
        {
          asset_id: "rough-cut-session-target",
          frame_index: 1,
          text: "新账号的私有草稿",
          preview_url: null,
          missing_reason: null,
          included: true,
          pending: false,
        },
        {
          asset_id: "rough-cut-session-first",
          frame_index: 0,
          text: "新账号第二项的私有草稿",
          preview_url: null,
          missing_reason: null,
          included: true,
          pending: false,
        },
      ];
      const rowsSnapshot = (
        chapterId: string,
        frames: PersonalRoughCutSnapshot["frames"],
      ): PersonalRoughCutSnapshot => {
        const snapshot = personalRoughCutFrameSnapshot(chapterId, frames[0]!.text, frames[0]!.asset_id);
        snapshot.frames = frames;
        return snapshot;
      };
      const getPersonalRoughCut = vi.fn((chapterId: string) => {
        if (activeUserId === firstUser.id) {
          firstUserReadCount += 1;
          return firstUserReadCount === 1
            ? Promise.resolve(rowsSnapshot(chapterId, firstUserFrames))
            : oldRequest.promise;
        }
        return Promise.resolve(rowsSnapshot(chapterId, secondUserFrames));
      });
      const logout = vi.fn(() => {
        activeUserId = "anonymous";
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => firstUser,
        login: async () => {
          activeUserId = secondUser.id;
          window.localStorage.setItem(API_TOKEN_KEY, "rough-cut-second-session-token");
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters,
        listStoryboardAssets,
        listCharacters: async () => [],
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut,
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "rough-cut-first-session-token");
      renderWorkspace(services);
      await screen.findAllByRole("article");

      const firstCard = screen.getByRole("heading", { name: firstSeries.name }).closest("article");
      if (!(firstCard instanceof HTMLElement)) {
        throw new Error("First user's series card was not rendered.");
      }
      await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByRole("heading", { name: "同章粗剪" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      await waitFor(() => expect(getPersonalRoughCut).toHaveBeenCalledTimes(1));
      expect(await screen.findByText("旧账号首项的粗剪正文")).toBeInTheDocument();
      const firstPanel = screen.getByRole("region", { name: "我的粗剪草稿" });
      const firstFilters = within(firstPanel).getByRole("group", { name: "按草稿状态筛选" });
      expect(within(firstFilters).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
      expect(within(firstFilters).getByRole("button", { name: "已纳入 1" })).toBeInTheDocument();
      expect(within(firstFilters).getByRole("button", { name: "已排除 1" })).toBeInTheDocument();
      expect(within(firstFilters).getByRole("button", { name: "待安排 1" })).toBeInTheDocument();
      await user.click(within(firstFilters).getByRole("button", { name: "已排除 1" }));
      expect(within(firstFilters).getByRole("button", { name: "已排除 1" })).toHaveAttribute("aria-pressed", "true");
      await user.click(within(firstPanel).getByRole("button", { name: "重新读取" }));
      await waitFor(() => expect(getPersonalRoughCut).toHaveBeenCalledTimes(2));
      expect(screen.getByText("正在读取粗剪草稿…")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "我的粗剪草稿" })).not.toBeInTheDocument();
      await user.click(screen.getByLabelText("账号"));
      await user.paste("second");
      await user.click(screen.getByLabelText("密码"));
      await user.paste("password");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");

      const secondCard = screen.getByRole("heading", { name: firstSeries.name }).closest("article");
      if (!(secondCard instanceof HTMLElement)) {
        throw new Error("Second user's series card was not rendered.");
      }
      await user.click(within(secondCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByRole("heading", { name: "同章粗剪" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      expect(await screen.findByText("新账号的私有草稿")).toBeInTheDocument();
      const secondPanel = screen.getByRole("region", { name: "我的粗剪草稿" });
      const secondFilters = within(secondPanel).getByRole("group", { name: "按草稿状态筛选" });
      expect(within(secondFilters).getByRole("button", { name: "全部 2" })).toHaveAttribute("aria-pressed", "true");
      expect(within(secondFilters).getByRole("button", { name: "已纳入 2" })).toBeInTheDocument();
      expect(within(secondFilters).getByRole("button", { name: "已排除 0" })).toBeInTheDocument();
      expect(within(secondFilters).getByRole("button", { name: "待安排 0" })).toBeInTheDocument();
      await user.click(within(secondFilters).getByRole("button", { name: "已纳入 2" }));
      expect(within(secondFilters).getByRole("button", { name: "已纳入 2" })).toHaveAttribute("aria-pressed", "true");

      await act(async () => {
        if (lateResult === "401") {
          oldRequest.reject(new ApiError("http", "expired", 401));
          await oldRequest.promise.catch(() => undefined);
        } else {
          oldRequest.resolve(rowsSnapshot(sharedChapter.id, [
            {
              asset_id: "rough-cut-session-target",
              frame_index: 1,
              text: "旧账号迟到的草稿",
              preview_url: null,
              missing_reason: null,
              included: false,
              pending: true,
            },
          ]));
          await oldRequest.promise;
        }
      });

      expect(screen.getByRole("heading", { name: "同章粗剪" })).toBeInTheDocument();
      expect(screen.getByText("新账号的私有草稿")).toBeInTheDocument();
      expect(screen.getByText("新账号第二项的私有草稿")).toBeInTheDocument();
      expect(screen.queryByText("旧账号迟到的草稿")).not.toBeInTheDocument();
      expect(screen.getByText("粗剪新账号")).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-second-session-token");
      expect(within(secondFilters).getByRole("button", { name: "已纳入 2" })).toHaveAttribute("aria-pressed", "true");
      expect(getPersonalRoughCut).toHaveBeenCalledTimes(3);
      await waitFor(() => {
        expect(listChapters).toHaveBeenCalledTimes(2);
        expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
      });

      const scrollProbe = installScrollIntoViewProbe();
      const targetArticle = screen.getByRole("article", { name: "同章粗剪 · 镜头 2" });
      await user.click(within(secondPanel).getByRole("button", { name: "定位到对应镜头 2" }));
      await waitFor(() => expect(targetArticle).toHaveFocus());
      expect(scrollProbe.targets).toContain(targetArticle);
      expect(screen.getByText("新账号的私有草稿")).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-second-session-token");
      expect(within(secondFilters).getByRole("button", { name: "已纳入 2" })).toHaveAttribute("aria-pressed", "true");
      expect(getPersonalRoughCut).toHaveBeenCalledTimes(3);
      expect(listChapters).toHaveBeenCalledTimes(2);
      expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["success", "401"] as const)(
    "ignores an old rough-cut %s after returning to the list and opening another series",
    async (lateResult) => {
      const user = userEvent.setup();
      const firstSeries = demoSeries[0]!;
      const secondSeries = demoSeries[1]!;
      const currentUser: User = {
        ...fixtureUser,
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const firstChapter = chapterSnapshot(firstSeries.id, "rough-cut-old-series", "旧剧集粗剪章节", null);
      const secondChapter = chapterSnapshot(secondSeries.id, "rough-cut-new-series", "新剧集粗剪章节", null);
      const stale = deferred<PersonalRoughCutSnapshot>();
      const getPersonalRoughCut = vi.fn((chapterId: string) => (
        chapterId === firstChapter.id
          ? stale.promise
          : Promise.resolve(personalRoughCutSnapshot(chapterId, "新剧集的粗剪内容"))
      ));
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => currentUser,
        login: async () => currentUser,
        listSeries: async () => demoSeries,
        listMyTeams: async () => [],
        listChapters: async (seriesId) => (
          seriesId === firstSeries.id ? [firstChapter] : [secondChapter]
        ),
        listStoryboardAssets: async () => [],
        listCharacters: async () => [],
        listScenes: async () => [],
        listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut,
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
        logout,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "rough-cut-return-session-token");
      renderWorkspace(services);
      await screen.findAllByRole("article");

      const firstCard = screen.getByRole("heading", { name: firstSeries.name }).closest("article");
      if (!(firstCard instanceof HTMLElement)) {
        throw new Error("First series card was not rendered.");
      }
      await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByRole("heading", { name: "旧剧集粗剪章节" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      await waitFor(() => expect(getPersonalRoughCut).toHaveBeenCalledTimes(1));

      await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
      expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
      const secondCard = screen.getByRole("heading", { name: secondSeries.name }).closest("article");
      if (!(secondCard instanceof HTMLElement)) {
        throw new Error("Second series card was not rendered.");
      }
      await user.click(within(secondCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByRole("heading", { name: "新剧集粗剪章节" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看我的粗剪草稿" }));
      expect(await screen.findByText("新剧集的粗剪内容")).toBeInTheDocument();

      await act(async () => {
        if (lateResult === "401") {
          stale.reject(new ApiError("http", "expired", 401));
          await stale.promise.catch(() => undefined);
        } else {
          stale.resolve(personalRoughCutSnapshot(firstChapter.id, "旧剧集迟到的粗剪内容"));
          await stale.promise;
        }
      });

      expect(screen.getByRole("heading", { name: "新剧集粗剪章节" })).toBeInTheDocument();
      expect(screen.getByText("新剧集的粗剪内容")).toBeInTheDocument();
      expect(screen.queryByText("旧剧集迟到的粗剪内容")).not.toBeInTheDocument();
      expect(getPersonalRoughCut).toHaveBeenCalledTimes(2);
      expect(logout).not.toHaveBeenCalled();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("rough-cut-return-session-token");
    },
  );

  it.each([
    { request: "chapter", result: "success" },
    { request: "chapter", result: "401" },
    { request: "asset", result: "success" },
    { request: "asset", result: "401" },
  ] as const)("keeps a second login after a late $request $result", async ({ request, result }) => {
    const user = userEvent.setup();
    const firstSeries = demoSeries[0]!;
    const firstUser: User = {
      ...fixtureUser,
      id: "first-session-user",
      username: "第一个账号",
      is_superuser: false,
      membership_type: "free",
      membership_expires_at: null,
      avatar_url: null,
      bio: null,
    };
    const secondUser: User = { ...firstUser, id: "second-session-user", username: "第二个账号" };
    const sharedChapterId = "session-shared-chapter";
    const oldChapter = chapterSnapshot(
      firstSeries.id,
      request === "asset" ? sharedChapterId : "old-session-chapter",
      "旧会话章节",
      request === "asset" ? "旧会话镜头文字" : null,
      "first-session-frame",
    );
    const newChapter = chapterSnapshot(
      firstSeries.id,
      request === "asset" ? sharedChapterId : "new-session-chapter",
      "新会话章节",
      request === "asset" ? "新会话镜头文字" : null,
      "second-session-frame",
    );
    const lateChapterRequest = deferred<Chapter[]>();
    const lateAssetRequest = deferred<StoryboardAsset[]>();
    const freshAsset = storyboardAssetSnapshot(firstSeries.id, sharedChapterId, "second-session-frame");
    const chapterRequestCount = { value: 0 };
    const assetRequestCount = { value: 0 };
    const listChapters = vi.fn((_seriesId: string) => {
      chapterRequestCount.value += 1;
      if (chapterRequestCount.value === 1 && request === "chapter") {
        return lateChapterRequest.promise;
      }
      return Promise.resolve([chapterRequestCount.value === 1 ? oldChapter : newChapter]);
    });
    const listStoryboardAssets = vi.fn((_seriesId: string, _chapterId: string) => {
      assetRequestCount.value += 1;
      if (assetRequestCount.value === 1 && request === "asset") {
        return lateAssetRequest.promise;
      }
      return Promise.resolve([freshAsset]);
    });
    const logout = vi.fn(() => {
      window.localStorage.removeItem(API_TOKEN_KEY);
      window.localStorage.removeItem(API_USER_KEY);
    });
    const services: WorkspaceServices = {
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      restore: async () => firstUser,
      login: async () => {
        window.localStorage.setItem(API_TOKEN_KEY, "second-session-token");
        window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
        return secondUser;
      },
      listSeries: async () => demoSeries,
      listMyTeams: async () => [],
      listChapters,
      listStoryboardAssets,
      listCharacters: async () => [],
      listScenes: async () => [],
      listProps: async () => [],
        getPersonalProductionNotes: async () => { throw new Error("Unexpected personal notes request."); },
        getPersonalRoughCut: async () => { throw new Error("Unexpected personal rough-cut request."); },
        listMyTasks: async () => ({ total: 0, page: 1, page_size: 10, tasks: [] }),
      logout,
    };
    window.localStorage.setItem(API_TOKEN_KEY, "first-session-token");
    renderWorkspace(services);
    await screen.findAllByRole("article");

    const firstCard = screen.getByRole("heading", { name: firstSeries.name }).closest("article");
    if (!(firstCard instanceof HTMLElement)) {
      throw new Error("Series card was not rendered.");
    }
    await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
    if (request === "chapter") {
      expect(await screen.findByText("正在读取章节…")).toBeInTheDocument();
    } else {
      expect(await screen.findByText("旧会话镜头文字")).toBeInTheDocument();
      await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
    }

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("账号"), "second");
    await user.type(screen.getByLabelText("密码"), "pass");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findByRole("heading", { name: "我的剧集" });
    await screen.findAllByRole("article");

    const secondSessionCard = screen.getByRole("heading", { name: firstSeries.name }).closest("article");
    if (!(secondSessionCard instanceof HTMLElement)) {
      throw new Error("Series card was not rendered for the second session.");
    }
    await user.click(within(secondSessionCard).getByRole("button", { name: /只读查看章节/ }));
    expect(await screen.findByRole("heading", { name: "新会话章节" })).toBeInTheDocument();
    if (request === "asset") {
      expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute(
        "src",
        freshAsset.image_url,
      );
    }

    await act(async () => {
      if (request === "chapter") {
        if (result === "success") {
          lateChapterRequest.resolve([oldChapter]);
          await lateChapterRequest.promise;
        } else {
          lateChapterRequest.reject(new ApiError("http", "expired", 401));
          await lateChapterRequest.promise.catch(() => undefined);
        }
      } else if (result === "success") {
        lateAssetRequest.resolve([
          storyboardAssetSnapshot(firstSeries.id, sharedChapterId, "first-session-frame"),
        ]);
        await lateAssetRequest.promise;
      } else {
        lateAssetRequest.reject(new ApiError("http", "expired", 401));
        await lateAssetRequest.promise.catch(() => undefined);
      }
    });

    expect(screen.getByRole("heading", { name: "新会话章节" })).toBeInTheDocument();
    expect(screen.getByText("第二个账号")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "旧会话章节" })).not.toBeInTheDocument();
    expect(logout).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("second-session-token");
    if (request === "chapter") {
      expect(listChapters).toHaveBeenCalledTimes(2);
      expect(listStoryboardAssets).not.toHaveBeenCalled();
    } else {
      expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
      expect(await screen.findByAltText("镜头 1 原图")).toHaveAttribute("src", freshAsset.image_url);
      expect(screen.getByAltText("镜头 1 原图")).not.toHaveAttribute(
        "src",
        "http://127.0.0.1:4175/media/first-session-frame.png",
      );
    }
  });

  it("keeps the session on 403, renders membership limits, and retries the list", async () => {
    const user = userEvent.setup();
    const { services, fetcher } = apiServicesForSeries(403, "当前演示账号的会员资格暂不可用。");
    renderWorkspace(services);

    expect(await screen.findByRole("heading", { name: "当前账号暂不可查看剧集" })).toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("fixture-token");
    await user.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("fixture-token");
    expect(screen.getByRole("button", { name: "重试读取" })).toBeInTheDocument();
  });

  it("clears an expired API session on 401 and preserves it for invalid payloads", async () => {
    const { services: expired } = apiServicesForSeries(401, "登录状态已失效。");
    renderWorkspace(expired);

    expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(window.localStorage.getItem(MOCK_SESSION_KEY)).toBeNull();

    cleanup();
    window.localStorage.clear();
    window.localStorage.setItem(API_TOKEN_KEY, "valid-token");
    const malformed = createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher: vi.fn(async (input: RequestInfo | URL) => {
        const pathname = new URL(String(input)).pathname;
        return pathname.endsWith("/auth/me")
          ? jsonResponse(fixtureUser)
          : jsonResponse([{ id: "broken" }]);
      }),
    });
    renderWorkspace(malformed);

    expect(await screen.findByRole("heading", { name: "服务返回的数据无法识别" })).toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("valid-token");
  });

  it("shows the empty-list state and keeps the readonly shell free of write actions", async () => {
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher: vi.fn(async (input: RequestInfo | URL) => {
        const pathname = new URL(String(input)).pathname;
        return pathname.endsWith("/auth/me")
          ? jsonResponse(fixtureUser)
          : jsonResponse([]);
      }),
    });
    window.localStorage.setItem(API_TOKEN_KEY, "valid-token");
    renderWorkspace(services);

    expect(await screen.findByRole("heading", { name: "还没有剧集" })).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(创建剧集|认领|制作|章节|生成)$/ })).not.toBeInTheDocument();
  });

  it("enters tasks only on request, unloads chapter notes, and preserves series filters and count", async () => {
    const user = userEvent.setup();
    const services = createDemoServices(window.localStorage);
    const listMyTasks = vi.spyOn(services, "listMyTasks");
    const { container } = renderWorkspace(services);
    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await user.click(screen.getByRole("button", { name: /全部剧集/ }));
    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(listMyTasks).not.toHaveBeenCalled();

    const card = screen.getAllByRole("article")[0]!;
    await user.click(within(card).getByRole("button", { name: /只读查看章节/ }));
    await screen.findByRole("heading", { name: "雾港来信" });
    await user.click(await screen.findByRole("button", { name: "查看我的制作记录" }));
    expect(await screen.findByRole("heading", { name: "我的制作记录" })).toBeInTheDocument();
    expect(listMyTasks).not.toHaveBeenCalled();

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("button", { name: "我的任务" }));
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(1));
    await screen.findAllByRole("article");
    expect(screen.getAllByRole("article")).toHaveLength(10);
    expect(screen.queryByRole("heading", { name: "我的制作记录" })).not.toBeInTheDocument();
    const hiddenList = container.querySelector(".series-list-view") as HTMLElement;
    expect(hiddenList.hidden).toBe(true);
    expect(hiddenList.hasAttribute("inert")).toBe(true);
    expect(listMyTasks).toHaveBeenCalledTimes(1);
    expect(listMyTasks).toHaveBeenLastCalledWith(1, expect.any(AbortSignal));

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("button", { name: "我的任务" }));
    expect(listMyTasks).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("heading", { name: "雾港来信 · 第 1 章" })).not.toBeInTheDocument();

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("button", { name: "我的任务" }));
    await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(2));
    await screen.findAllByRole("article");
    expect(screen.getAllByRole("article")).toHaveLength(10);
    expect(listMyTasks).toHaveBeenLastCalledWith(1, expect.any(AbortSignal));
  });

  it("ignores a late task 401 after leaving and reopening the task page", async () => {
    const userEvents = userEvent.setup();
    const currentUser: User = {
      ...fixtureUser,
      is_superuser: false,
      membership_type: "free",
      membership_expires_at: null,
      avatar_url: null,
      bio: null,
    };
    window.localStorage.setItem(API_TOKEN_KEY, "same-user-task-token");
    window.localStorage.setItem(API_USER_KEY, JSON.stringify(currentUser));
    const lateRequest = deferred<MyTaskPage>();
    let taskRequestCount = 0;
    const listMyTasks = vi.fn(() => {
      taskRequestCount += 1;
      return taskRequestCount === 1
        ? lateRequest.promise
        : Promise.resolve(myTaskPage([
          myTaskRecord("reopened-task", { chapter_title: "重新打开后的任务记录" }),
        ], 1));
    });
    const baseServices = createDemoServices(window.localStorage);
    const logout = vi.fn(() => {
      window.localStorage.removeItem(API_TOKEN_KEY);
      window.localStorage.removeItem(API_USER_KEY);
    });
    const services: WorkspaceServices = {
      ...baseServices,
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      restore: async () => currentUser,
      listSeries: async () => [],
      listMyTeams: async () => [],
      listMyTasks,
      logout,
    };
    renderWorkspace(services);
    await screen.findByRole("heading", { name: "我的剧集" });

    const taskNavigation = within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" });
    await userEvents.click(taskNavigation);
    await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(1));
    await userEvents.click(screen.getByRole("button", { name: "返回剧集列表" }));
    await screen.findByRole("heading", { name: "我的剧集" });
    await userEvents.click(taskNavigation);
    expect(await screen.findByText("重新打开后的任务记录")).toBeInTheDocument();

    await act(async () => {
      lateRequest.reject(new ApiError("http", "expired", 401));
      await lateRequest.promise.catch(() => undefined);
    });

    expect(screen.getByText("重新打开后的任务记录")).toBeInTheDocument();
    expect(listMyTasks).toHaveBeenCalledTimes(2);
    expect(logout).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("same-user-task-token");
    expect(JSON.parse(window.localStorage.getItem(API_USER_KEY) ?? "null")).toMatchObject({
      id: currentUser.id,
      username: currentUser.username,
    });
  });

  it.each(["success", "401"] as const)(
    "does not let a late task %s from a logged-out user affect the next login",
    async (lateResult) => {
      const userEvents = userEvent.setup();
      const firstUser: User = {
        ...fixtureUser,
        id: "first-task-user",
        username: "第一个账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = {
        ...fixtureUser,
        id: "second-task-user",
        username: "第二个账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const lateRequest = deferred<MyTaskPage>();
      let loginCount = 0;
      let taskRequestCount = 0;
      const listMyTasks = vi.fn((_pageNumber: number) => {
        taskRequestCount += 1;
        if (taskRequestCount === 1) {
          return lateRequest.promise;
        }
        return Promise.resolve(myTaskPage([
          myTaskRecord("second-user-task", { chapter_title: "第二账号的任务记录" }),
        ], 1));
      });
      const baseServices = createDemoServices(window.localStorage);
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        ...baseServices,
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => null,
        login: async () => {
          loginCount += 1;
          const loggedInUser = loginCount === 1 ? firstUser : secondUser;
          window.localStorage.setItem(API_TOKEN_KEY, `task-user-token-${loginCount}`);
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(loggedInUser));
          return loggedInUser;
        },
        listSeries: async () => [],
        listMyTeams: async () => [],
        listMyTasks,
        logout,
      };
      renderWorkspace(services);
      await screen.findByRole("heading", { name: "欢迎回来" });

      await userEvents.type(screen.getByLabelText("账号"), "first");
      await userEvents.type(screen.getByLabelText("密码"), "password");
      await userEvents.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findByRole("heading", { name: "我的剧集" });
      await userEvents.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("button", { name: "我的任务" }));
      await waitFor(() => expect(listMyTasks).toHaveBeenCalledTimes(1));

      await userEvents.click(screen.getByRole("button", { name: "退出" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      await userEvents.type(screen.getByLabelText("账号"), "second");
      await userEvents.type(screen.getByLabelText("密码"), "password");
      await userEvents.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findByRole("heading", { name: "我的剧集" });
      await userEvents.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("button", { name: "我的任务" }));
      expect(await screen.findByText("第二账号的任务记录")).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("task-user-token-2");

      await act(async () => {
        if (lateResult === "success") {
          lateRequest.resolve(myTaskPage([
            myTaskRecord("first-user-task", { chapter_title: "第一账号的旧任务记录" }),
          ], 1));
          await lateRequest.promise;
        } else {
          lateRequest.reject(new ApiError("http", "expired", 401));
          await lateRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByRole("heading", { name: "我的任务" })).toBeInTheDocument();
      expect(screen.getByText("第二账号的任务记录")).toBeInTheDocument();
      expect(screen.queryByText("第一账号的旧任务记录")).not.toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("task-user-token-2");
      expect(listMyTasks).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["success", "401"] as const)(
    "does not let a logged-out user's late character %s replace the next account's panel",
    async (lateResult) => {
      const userEvents = userEvent.setup();
      const firstUser: User = {
        ...fixtureUser,
        id: "first-reference-user",
        username: "第一个引用账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = {
        ...fixtureUser,
        id: "second-reference-user",
        username: "第二个引用账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const selectedSeries = demoSeries[0]!;
      const selectedChapter: Chapter = {
        ...chapterSnapshot(
          selectedSeries.id,
          "chapter-reference-account-switch",
          "账号切换引用章节",
          "切换账号期间仍可阅读的章节正文",
          "account-switch-frame",
        ),
        content: [{
          text: "切换账号期间仍可阅读的章节正文",
          storyboard: ["account-switch-frame"],
          character: ["shared-character-id"],
        }],
      };
      const lateDirectory = deferred<Character[]>();
      let loginCount = 0;
      let characterRequestCount = 0;
      const listCharacters = vi.fn((_seriesId: string, _signal: AbortSignal) => {
        characterRequestCount += 1;
        return characterRequestCount === 1
          ? lateDirectory.promise
          : Promise.resolve([characterSnapshot(selectedSeries.id, "第二账号角色")]);
      });
      const baseServices = createDemoServices(window.localStorage);
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        ...baseServices,
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => null,
        login: async () => {
          loginCount += 1;
          const loggedInUser = loginCount === 1 ? firstUser : secondUser;
          window.localStorage.setItem(API_TOKEN_KEY, `reference-user-token-${loginCount}`);
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(loggedInUser));
          return loggedInUser;
        },
        listSeries: async () => [selectedSeries],
        listMyTeams: async () => [],
        listChapters: async () => [selectedChapter],
        listStoryboardAssets: async () => [],
        listCharacters,
        listScenes: async () => [],
        listProps: async () => [],
        logout,
      };
      renderWorkspace(services);
      await screen.findByRole("heading", { name: "欢迎回来" });

      await userEvents.type(screen.getByLabelText("账号"), "first");
      await userEvents.type(screen.getByLabelText("密码"), "password");
      await userEvents.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findByRole("heading", { name: "我的剧集" });
      const firstSeriesCard = await screen.findByRole("article");
      await userEvents.click(within(firstSeriesCard).getByRole("button", { name: /只读查看章节/ }));
      await screen.findByRole("heading", { name: "账号切换引用章节" });
      await userEvents.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
      await userEvents.click(screen.getByRole("button", { name: "角色" }));
      await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(1));
      expect(within(screen.getByRole("region", { name: "镜头 1 的关联素材" }))
        .getByRole("status")).toHaveTextContent("正在读取角色");

      await userEvents.click(screen.getByRole("button", { name: /^退出$/ }));
      await screen.findByRole("heading", { name: "欢迎回来" });
      await userEvents.type(screen.getByLabelText("账号"), "second");
      await userEvents.type(screen.getByLabelText("密码"), "password");
      await userEvents.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findByRole("heading", { name: "我的剧集" });
      const secondSeriesCard = await screen.findByRole("article");
      await userEvents.click(within(secondSeriesCard).getByRole("button", { name: /只读查看章节/ }));
      await screen.findByRole("heading", { name: "账号切换引用章节" });
      await userEvents.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
      await userEvents.click(screen.getByRole("button", { name: "角色" }));
      expect(await screen.findByText("第二账号角色", { selector: "strong" })).toBeInTheDocument();
      expect(listCharacters).toHaveBeenCalledTimes(2);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("reference-user-token-2");

      await act(async () => {
        if (lateResult === "success") {
          lateDirectory.resolve([characterSnapshot(selectedSeries.id, "第一个账号迟到角色")]);
          await lateDirectory.promise;
        } else {
          lateDirectory.reject(new ApiError("http", "expired", 401));
          await lateDirectory.promise.catch(() => undefined);
        }
        await Promise.resolve();
      });

      expect(screen.getByText("第二账号角色", { selector: "strong" })).toBeInTheDocument();
      expect(screen.queryByText("第一个账号迟到角色", { selector: "strong" })).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "账号切换引用章节" })).toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("reference-user-token-2");
      expect(JSON.parse(window.localStorage.getItem(API_USER_KEY) ?? "null")).toMatchObject({
        id: secondUser.id,
        username: secondUser.username,
      });
    },
  );

  it("preserves a successful team snapshot while opening chapters, assets, and tasks", async () => {
    const user = userEvent.setup();
    const baseServices = createDemoServices(window.localStorage);
    const listMyTeams = vi.fn((signal: AbortSignal) => baseServices.listMyTeams(signal));
    const services: WorkspaceServices = { ...baseServices, listMyTeams };
    renderWorkspace(services);

    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    expect(await screen.findAllByRole("option", { name: "拾光工作室" })).toHaveLength(2);
    await user.selectOptions(screen.getByLabelText("筛选团队"), "team-studio");
    expect(screen.getAllByRole("article")).toHaveLength(16);

    const firstTeamCard = screen.getAllByRole("article")[0]!;
    await user.click(within(firstTeamCard).getByRole("button", { name: /只读查看章节/ }));
    expect(await screen.findByRole("heading", { name: /第一章 · 雾起/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByText("显示 16 / 16 部")).toBeInTheDocument();
    expect(screen.getByLabelText("筛选团队")).toHaveValue("team-studio");

    const returnedCard = screen.getAllByRole("article")[0]!;
    await user.click(within(returnedCard).getByRole("button", { name: /只读查看素材库/ }));
    expect(await screen.findByRole("heading", { name: /素材库/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByText("显示 16 / 16 部")).toBeInTheDocument();
    expect(screen.getByLabelText("筛选团队")).toHaveValue("team-studio");

    const taskNavigation = within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" });
    await user.click(taskNavigation);
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByText("显示 16 / 16 部")).toBeInTheDocument();
    expect(screen.getByLabelText("筛选团队")).toHaveValue("team-studio");
    expect(listMyTeams).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "401"] as const)(
    "starts a fresh directory read after hiding a pending request and ignores its late %s",
    async (lateResult) => {
      const user = userEvent.setup();
      const authenticatedUser: User = {
        ...fixtureUser,
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      window.localStorage.setItem(API_TOKEN_KEY, "team-session-token");
      window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticatedUser));
      const staleRequest = deferred<MyTeam[]>();
      let requestCount = 0;
      let staleSignal: AbortSignal | undefined;
      const listMyTeams = vi.fn((signal: AbortSignal) => {
        requestCount += 1;
        if (requestCount === 1) {
          staleSignal = signal;
          return staleRequest.promise;
        }
        return Promise.resolve([teamDirectoryItem("current-team", "当前团队")]);
      });
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        ...createDemoServices(window.localStorage),
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => authenticatedUser,
        listSeries: async () => demoSeries,
        listMyTeams,
        listMyTasks: async () => myTaskPage([], 0),
        logout,
      };
      renderWorkspace(services);

      await screen.findAllByRole("article");
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      await waitFor(() => expect(listMyTeams).toHaveBeenCalledTimes(1));
      const taskNavigation = within(screen.getByRole("navigation", { name: "主要导航" }))
        .getByRole("button", { name: "我的任务" });
      await user.click(taskNavigation);
      expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
      expect(staleSignal?.aborted).toBe(true);

      await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
      await screen.findByRole("heading", { name: "我的剧集" });
      expect(await screen.findByRole("option", { name: "当前团队" })).toBeInTheDocument();
      expect(listMyTeams).toHaveBeenCalledTimes(2);

      await act(async () => {
        if (lateResult === "success") {
          staleRequest.resolve([teamDirectoryItem("stale-team", "迟到旧团队")]);
          await staleRequest.promise;
        } else {
          staleRequest.reject(new ApiError("http", "expired", 401));
          await staleRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByRole("option", { name: "当前团队" })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: "迟到旧团队" })).not.toBeInTheDocument();
      expect(logout).not.toHaveBeenCalled();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("team-session-token");
    },
  );

  it.each(["success", "401"] as const)(
    "does not let a logged-out user's late team %s alter a new session",
    async (lateResult) => {
      const user = userEvent.setup();
      const firstUser: User = {
        ...fixtureUser,
        id: "first-team-user",
        username: "第一个账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const secondUser: User = {
        ...fixtureUser,
        id: "second-team-user",
        username: "第二个账号",
        is_superuser: false,
        membership_type: "free",
        membership_expires_at: null,
        avatar_url: null,
        bio: null,
      };
      const staleRequest = deferred<MyTeam[]>();
      let loginCount = 0;
      let teamRequestCount = 0;
      const listMyTeams = vi.fn(() => {
        teamRequestCount += 1;
        return teamRequestCount === 1
          ? staleRequest.promise
          : Promise.resolve([teamDirectoryItem("second-team", "第二账号团队")]);
      });
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services: WorkspaceServices = {
        ...createDemoServices(window.localStorage),
        mode: "api",
        apiBaseUrl: "http://127.0.0.1:4175/api",
        restore: async () => null,
        login: async ({ username }) => {
          loginCount += 1;
          const authenticated = username === "first" ? firstUser : secondUser;
          window.localStorage.setItem(API_TOKEN_KEY, `team-session-${loginCount}`);
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticated));
          return authenticated;
        },
        listSeries: async () => demoSeries,
        listMyTeams,
        logout,
      };
      renderWorkspace(services);
      await screen.findByRole("heading", { name: "欢迎回来" });

      await user.type(screen.getByLabelText("账号"), "first");
      await user.type(screen.getByLabelText("密码"), "password");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      await waitFor(() => expect(listMyTeams).toHaveBeenCalledTimes(1));

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      await user.type(screen.getByLabelText("账号"), "second");
      await user.type(screen.getByLabelText("密码"), "password");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      expect(await screen.findByRole("option", { name: "第二账号团队" })).toBeInTheDocument();

      await act(async () => {
        if (lateResult === "success") {
          staleRequest.resolve([teamDirectoryItem("first-team", "第一个账号团队")]);
          await staleRequest.promise;
        } else {
          staleRequest.reject(new ApiError("http", "expired", 401));
          await staleRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByRole("option", { name: "第二账号团队" })).toBeInTheDocument();
      expect(screen.queryByRole("option", { name: "第一个账号团队" })).not.toBeInTheDocument();
      expect(screen.getByText("第二个账号")).toBeInTheDocument();
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("team-session-2");
      expect(logout).toHaveBeenCalledTimes(1);
      expect(listMyTeams).toHaveBeenCalledTimes(2);
    },
  );

  it.each([
    { category: "characters" as const, kind: "character" as const, label: "角色", assetName: "目标角色" },
    { category: "scenes" as const, kind: "scene" as const, label: "场景", assetName: "目标场景" },
    { category: "props" as const, kind: "prop" as const, label: "道具", assetName: "目标道具" },
  ])(
    "navigates from a $category reference to the fresh non-first chapter and frame",
    async ({ category, kind, label, assetName }) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const assetId = "usage-asset-" + category;
      const targetChapterId = "usage-target-chapter-" + category;
      const targetStoryboardId = "usage-storyboard-" + category;
      const otherAssetId = "other-usage-asset-" + category;
      const sourceFirst = chapterWithFrameReferences(series.id, "usage-source-first-" + category, "同名章节", [
        { text: "来源首章镜头文字", storyboardAssetId: "source-first-frame-" + category, kind, assetId },
      ], 90);
      const sourceTarget = chapterWithFrameReferences(series.id, targetChapterId, "同名章节", [
        { text: "来源前置镜头文字", storyboardAssetId: "source-before-frame-" + category, kind, assetId: otherAssetId },
        { text: "来源目标镜头文字", storyboardAssetId: targetStoryboardId, kind, assetId },
        { text: "来源后置镜头文字", storyboardAssetId: "source-after-frame-" + category, kind, assetId: otherAssetId },
      ], 1);
      const freshFirst = chapterWithFrameReferences(series.id, "usage-fresh-first-" + category, "同名章节", [
        { text: "新目录首章镜头文字", storyboardAssetId: "fresh-first-frame-" + category, kind, assetId },
      ], 80);
      const freshTarget = chapterWithFrameReferences(series.id, targetChapterId, "同名章节", [
        { text: "新目录重排镜头文字甲", storyboardAssetId: "fresh-tail-frame-" + category, kind, assetId: otherAssetId },
        { text: "新目录重排镜头文字乙", storyboardAssetId: "fresh-before-frame-" + category, kind, assetId: otherAssetId },
        { text: "新目录目标镜头文字", storyboardAssetId: targetStoryboardId, kind, assetId },
      ], 1);
      const sourceChapters = [sourceFirst, sourceTarget];
      const freshChapters = [freshFirst, freshTarget];
      const targetChaptersRequest = deferred<Chapter[]>();
      const targetAssetsRequest = deferred<StoryboardAsset[]>();
      let chapterRequestCount = 0;
      const listChapters = vi.fn((_seriesId: string) => {
        chapterRequestCount += 1;
        if (chapterRequestCount === 1) {
          return Promise.resolve(sourceChapters);
        }
        if (chapterRequestCount === 2) {
          return targetChaptersRequest.promise;
        }
        return Promise.resolve(freshChapters);
      });
      const targetAssets = [
        { ...storyboardAssetSnapshot(series.id, targetChapterId, targetStoryboardId, 0), image_url: null },
        storyboardAssetSnapshot(series.id, targetChapterId, "fresh-before-frame-" + category, 3),
        storyboardAssetSnapshot(series.id, targetChapterId, "fresh-tail-frame-" + category, 1),
      ];
      const firstChapterAssets = [
        storyboardAssetSnapshot(series.id, freshFirst.id, "fresh-first-frame-" + category),
      ];
      const listStoryboardAssets = vi.fn((_seriesId: string, chapterId: string) => {
        if (chapterId === targetChapterId) {
          return category === "props" ? Promise.resolve(targetAssets) : targetAssetsRequest.promise;
        }
        return Promise.resolve(firstChapterAssets);
      });
      const services = frameNavigationServices({
        category,
        assetId,
        assetName,
        listChapters,
        listStoryboardAssets,
      });
      const scrollProbe = installScrollIntoViewProbe();
      window.localStorage.setItem(API_TOKEN_KEY, "frame-navigation-session");
      renderWorkspace(services);

      await screen.findAllByRole("article");
      await user.click(screen.getByRole("button", { name: "团队剧集" }));
      await user.click(screen.getByRole("button", { name: /全部剧集/ }));
      await user.click(screen.getByRole("button", { name: /加载更多/ }));
      expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();

      const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
      await screen.findByRole("heading", { name: series.name + " · 素材库" });
      if (category !== "characters") {
        await user.click(screen.getByRole("button", { name: label }));
      }
      const assetCard = screen.getByRole("heading", { name: assetName }).closest("article");
      if (!(assetCard instanceof HTMLElement)) {
        throw new Error("Asset card was not rendered.");
      }
      expect(listChapters).not.toHaveBeenCalled();
      await user.click(within(assetCard).getByRole("button", { name: "查看关联镜头：" + assetName }));
      const sourcePanel = await screen.findByRole("region", { name: "关联镜头" });
      expect(await within(sourcePanel).findByText("来源目标镜头文字")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(1);
      expect(listStoryboardAssets).not.toHaveBeenCalled();

      const oldClose = chapterNavigationProbe.sourcePanelCloseCallback;
      if (oldClose === null) {
        throw new Error("The source panel close callback was not captured.");
      }
      await user.click(within(sourcePanel).getByRole("button", {
        name: "定位对应镜头：同名章节 · 镜头2",
      }));
      expect(screen.queryByRole("region", { name: "关联镜头" })).not.toBeInTheDocument();
      await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
      expect(chapterNavigationProbe.intent).toMatchObject({
        chapterId: targetChapterId,
        frameTarget: { storyboardAssetId: targetStoryboardId, category, assetId },
      });
      const acceptedEpoch = chapterNavigationProbe.intent?.navigationEpoch;
      expect(acceptedEpoch).toEqual(expect.any(Number));
      expect(chapterNavigationProbe.intentHistory).toContainEqual({
        chapterId: targetChapterId,
        navigationEpoch: acceptedEpoch,
        frameTarget: { storyboardAssetId: targetStoryboardId, category, assetId },
      });
      await act(async () => oldClose());
      expect(chapterNavigationProbe.intent).toMatchObject({
        chapterId: targetChapterId,
        frameTarget: { storyboardAssetId: targetStoryboardId, category, assetId },
      });
      expect(chapterNavigationProbe.abandonedEpochs).toHaveLength(0);

      await act(async () => {
        targetChaptersRequest.resolve(freshChapters);
        await targetChaptersRequest.promise;
      });
      await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
      expect(listStoryboardAssets).toHaveBeenCalledWith(
        series.id,
        targetChapterId,
        expect.any(AbortSignal),
      );

      if (category === "characters") {
        const sameChapterButtons = screen.getAllByRole("button", { name: /同名章节/ });
        expect(sameChapterButtons).toHaveLength(2);
        await user.click(sameChapterButtons[1]!);
        expect(chapterNavigationProbe.abandonedEpochs).toHaveLength(0);
        expect(chapterNavigationProbe.intent).toMatchObject({
          chapterId: targetChapterId,
          frameTarget: { storyboardAssetId: targetStoryboardId, category, assetId },
        });
        expect(listStoryboardAssets).toHaveBeenCalledTimes(1);
        await act(async () => {
          targetAssetsRequest.resolve(targetAssets);
          await targetAssetsRequest.promise;
        });
      } else if (category === "scenes") {
        const otherChapterButton = screen.getAllByRole("button", { name: /同名章节/ })[0];
        if (otherChapterButton === undefined) {
          throw new Error("The alternative chapter button was not rendered.");
        }
        await user.click(otherChapterButton);
        expect(chapterNavigationProbe.abandonedEpochs).toEqual([acceptedEpoch]);
        expect(chapterNavigationProbe.intent).toBeNull();
        await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(2));
        expect(await screen.findByText("新目录首章镜头文字")).toBeInTheDocument();
        await act(async () => {
          targetAssetsRequest.resolve(targetAssets);
          await targetAssetsRequest.promise;
        });
        expect(chapterNavigationProbe.consumedEpochs).toHaveLength(0);
        expect(scrollProbe.targets).toHaveLength(0);
        expect(screen.queryByText("新目录目标镜头文字")).not.toBeInTheDocument();
      }

      if (category !== "scenes") {
        const targetFrame = await screen.findByRole("article", { name: "同名章节 · 镜头 3" });
        await waitFor(() => expect(targetFrame).toHaveFocus());
        expect(scrollProbe.targets).toContain(targetFrame);
        expect(scrollProbe.targets.every((node) => document.activeElement === node)).toBe(true);
        expect(chapterNavigationProbe.consumedEpochs).toEqual([acceptedEpoch]);
        expect(chapterNavigationProbe.intent).toBeNull();
        expect(screen.queryByText(targetStoryboardId, { exact: true })).not.toBeInTheDocument();
        expect(screen.queryByText(assetId, { exact: true })).not.toBeInTheDocument();
      }

      if (category === "characters") {
        scrollProbe.targets.length = 0;
        await user.click(screen.getByRole("button", { name: "重新读取章节" }));
        await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(3));
        expect(chapterNavigationProbe.intent).toBeNull();
        expect(chapterNavigationProbe.consumedEpochs).toEqual([acceptedEpoch]);
        expect(scrollProbe.targets).toHaveLength(0);
      }

      await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
      expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
      expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
    },
  );

  it("rejects old chapter and frame callbacks after the same series view is reopened", async () => {
    const user = userEvent.setup();
    const series = demoSeries[0]!;
    const chapterId = "same-view-aba-chapter";
    const storyboardAssetId = "same-view-aba-storyboard";
    const assetId = "same-view-aba-character";
    const targetChapter = chapterWithFrameReferences(series.id, chapterId, "同会话章节", [
      { text: "同会话目标镜头", storyboardAssetId, kind: "character", assetId },
    ]);
    const listChapters = vi.fn(async () => [targetChapter]);
    const listStoryboardAssets = vi.fn(async () => [
      storyboardAssetSnapshot(series.id, chapterId, storyboardAssetId),
    ]);
    const services = frameNavigationServices({
      category: "characters",
      assetId,
      assetName: "同会话角色",
      listChapters,
      listStoryboardAssets,
    });
    window.localStorage.setItem(API_TOKEN_KEY, "same-view-aba-session");
    renderWorkspace(services);
    await screen.findAllByRole("article");

    const openAssetLibrary = async () => {
      const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("The series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
      await screen.findByRole("heading", { name: series.name + " · 素材库" });
    };
    await openAssetLibrary();
    const staleFrameParent = chapterNavigationProbe.frameParentCallback;
    if (staleFrameParent === null) {
      throw new Error("The original frame callback was not captured.");
    }

    const sourceAssetCard = screen.getByRole("heading", { name: "同会话角色" }).closest("article");
    if (!(sourceAssetCard instanceof HTMLElement)) {
      throw new Error("The source asset card was not rendered.");
    }
    await user.click(within(sourceAssetCard).getByRole("button", { name: "查看关联镜头：同会话角色" }));
    const sourcePanel = await screen.findByRole("region", { name: "关联镜头" });
    await user.click(within(sourcePanel).getByRole("button", { name: "查看对应章节：同会话章节" }));
    expect(await screen.findByText("同会话目标镜头")).toBeInTheDocument();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    const staleChapterParent = chapterNavigationProbe.callback;
    const staleChapterTarget = chapterNavigationProbe.target;
    if (staleChapterParent === null || staleChapterTarget === null) {
      throw new Error("The original chapter callback was not captured.");
    }
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    await openAssetLibrary();

    await act(async () => {
      staleFrameParent({
        chapterId,
        seriesId: series.id,
        storyboardAssetId,
        category: "characters",
        assetId,
        isCurrent: () => true,
      });
      staleChapterParent({ ...staleChapterTarget, isCurrent: () => true });
    });

    expect(screen.getByRole("heading", { name: series.name + " · 素材库" })).toBeInTheDocument();
    expect(chapterNavigationProbe.intent).toBeNull();
    expect(listChapters).toHaveBeenCalledTimes(2);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

    const currentAssetCard = screen.getByRole("heading", { name: "同会话角色" }).closest("article");
    if (!(currentAssetCard instanceof HTMLElement)) {
      throw new Error("The current asset card was not rendered.");
    }
    await user.click(within(currentAssetCard).getByRole("button", { name: "查看关联镜头：同会话角色" }));
    const currentPanel = await screen.findByRole("region", { name: "关联镜头" });
    await user.click(within(currentPanel).getByRole("button", {
      name: "定位对应镜头：同会话章节 · 镜头1",
    }));
    const targetFrame = await screen.findByRole("article", { name: "同会话章节 · 镜头 1" });
    await waitFor(() => expect(targetFrame).toHaveFocus());
    expect(listChapters).toHaveBeenCalledTimes(4);
    expect(listStoryboardAssets).toHaveBeenCalledTimes(2);
    expect(chapterNavigationProbe.consumedEpochs).toHaveLength(2);
  });

  it("rejects a replayed frame parent callback after the same user returns from A to B to A", async () => {
    const user = userEvent.setup();
    const firstUser = authenticatedFixtureUser("frame-scope-a", "账号 A");
    const secondUser = authenticatedFixtureUser("frame-scope-b", "账号 B");
    let activeUser: User | null = firstUser;
    let loginCount = 0;
    const listChapters = vi.fn(async () => [] as Chapter[]);
    const listStoryboardAssets = vi.fn(async () => [] as StoryboardAsset[]);
    const services = frameNavigationServices({
      category: "characters",
      assetId: "frame-scope-asset",
      assetName: "范围角色",
      listChapters,
      listStoryboardAssets,
      user: firstUser,
      login: async ({ username }) => {
        const authenticated = username === "first" ? firstUser : secondUser;
        activeUser = authenticated;
        loginCount += 1;
        window.localStorage.setItem(API_TOKEN_KEY, "frame-scope-session-" + loginCount);
        window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticated));
        return authenticated;
      },
      logout: () => {
        activeUser = null;
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      },
    });
    window.localStorage.setItem(API_TOKEN_KEY, "frame-scope-session-a1");
    renderWorkspace({ ...services, restore: async () => activeUser });

    await screen.findAllByRole("article");
    const openAssetsForSeries = async () => {
      const seriesCard = screen.getByRole("heading", { name: demoSeries[0]!.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
      await screen.findByRole("heading", { name: demoSeries[0]!.name + " · 素材库" });
    };
    await openAssetsForSeries();
    const oldParentCallback = chapterNavigationProbe.frameParentCallback;
    if (oldParentCallback === null) {
      throw new Error("The original AssetLibrary frame callback was not captured.");
    }

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "second");
    await user.type(screen.getByLabelText("密码"), "pass");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "first");
    await user.type(screen.getByLabelText("密码"), "pass");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");
    await openAssetsForSeries();

    const oldTarget: CapturedFrameTarget = {
      chapterId: "stale-frame-chapter",
      seriesId: demoSeries[0]!.id,
      storyboardAssetId: "stale-storyboard-id",
      category: "characters",
      assetId: "frame-scope-asset",
      isCurrent: () => true,
    };
    await act(async () => oldParentCallback(oldTarget));

    expect(screen.getByRole("heading", { name: demoSeries[0]!.name + " · 素材库" })).toBeInTheDocument();
    expect(chapterNavigationProbe.intent).toBeNull();
    expect(listChapters).not.toHaveBeenCalled();
    expect(listStoryboardAssets).not.toHaveBeenCalled();
    expect(activeUser?.id).toBe(firstUser.id);
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("frame-scope-session-2");
  });

  it("leaving to tasks abandons a pending frame target and ignores its later chapter result", async () => {
    const user = userEvent.setup();
    const series = demoSeries[0]!;
    const targetChaptersRequest = deferred<Chapter[]>();
    const chapterId = "task-leave-frame-chapter";
    const storyboardAssetId = "task-leave-storyboard-id";
    const assetId = "task-leave-asset-id";
    const sourceChapter = chapterWithFrameReferences(series.id, chapterId, "任务离开章节", [
      { text: "任务离开目标镜头", storyboardAssetId, kind: "character", assetId },
    ]);
    let chapterReadCount = 0;
    const listChapters = vi.fn((_seriesId: string) => ++chapterReadCount === 1
      ? Promise.resolve([sourceChapter])
      : targetChaptersRequest.promise);
    const listStoryboardAssets = vi.fn(async () => [storyboardAssetSnapshot(series.id, chapterId, storyboardAssetId)]);
    const services = frameNavigationServices({
      category: "characters",
      assetId,
      assetName: "任务离开角色",
      listChapters,
      listStoryboardAssets,
    });
    window.localStorage.setItem(API_TOKEN_KEY, "task-leave-frame-session");
    renderWorkspace(services);
    await screen.findAllByRole("article");
    const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("Series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
    await screen.findByRole("heading", { name: series.name + " · 素材库" });
    const assetCard = screen.getByRole("heading", { name: "任务离开角色" }).closest("article");
    if (!(assetCard instanceof HTMLElement)) {
      throw new Error("Asset card was not rendered.");
    }
    await user.click(within(assetCard).getByRole("button", { name: "查看关联镜头：任务离开角色" }));
    const sourcePanel = await screen.findByRole("region", { name: "关联镜头" });
    await user.click(within(sourcePanel).getByRole("button", {
      name: "定位对应镜头：任务离开章节 · 镜头1",
    }));
    await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
    expect(chapterNavigationProbe.intent?.frameTarget).toMatchObject({ storyboardAssetId, category: "characters", assetId });

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("button", { name: "我的任务" }));
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    expect(chapterNavigationProbe.intent).toBeNull();
    await act(async () => {
      targetChaptersRequest.resolve([sourceChapter]);
      await targetChaptersRequest.promise;
    });
    expect(screen.getByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    expect(listStoryboardAssets).not.toHaveBeenCalled();
    expect(chapterNavigationProbe.consumedEpochs).toHaveLength(0);
  });

  it.each([
    { request: "chapters" as const, result: "success" as const },
    { request: "chapters" as const, result: "401" as const },
    { request: "assets" as const, result: "success" as const },
    { request: "assets" as const, result: "401" as const },
  ])(
    "ignores a frame-navigation $request $result that settles after logout and a new login",
    async ({ request, result }) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const firstUser = authenticatedFixtureUser("frame-late-user-a", "镜头旧账号");
      const secondUser = authenticatedFixtureUser("frame-late-user-b", "镜头新账号");
      const sourceChapterId = "frame-late-source-chapter";
      const targetChapterId = sourceChapterId;
      const targetStoryboardId = "frame-late-source-id";
      const assetId = "frame-late-category-asset";
      const sourceChapter = chapterWithFrameReferences(series.id, sourceChapterId, "来源章节", [
        { text: "旧账号来源镜头", storyboardAssetId: targetStoryboardId, kind: "character", assetId },
      ]);
      const oldTargetChapter = chapterWithFrameReferences(series.id, targetChapterId, "旧目标章节", [
        { text: "迟到目标镜头", storyboardAssetId: targetStoryboardId, kind: "character", assetId },
      ]);
      const newUserChapter = chapterWithFrameReferences(series.id, "new-user-frame-chapter", "新账号章节", [
        { text: "新账号当前镜头", storyboardAssetId: "new-user-storyboard-id", kind: "character", assetId },
      ]);
      const oldChapterRequest = deferred<Chapter[]>();
      const oldAssetsRequest = deferred<StoryboardAsset[]>();
      let activeUser: User | null = firstUser;
      let firstUserChapterReads = 0;
      const listChapters = vi.fn((_seriesId: string) => {
        if (activeUser?.id === firstUser.id) {
          firstUserChapterReads += 1;
          if (firstUserChapterReads === 1) {
            return Promise.resolve([sourceChapter]);
          }
          if (request === "chapters") {
            return oldChapterRequest.promise;
          }
          return Promise.resolve([oldTargetChapter]);
        }
        return Promise.resolve([newUserChapter]);
      });
      let firstUserAssetReads = 0;
      const listStoryboardAssets = vi.fn((_seriesId: string, chapterId: string) => {
        if (activeUser?.id === firstUser.id && chapterId === targetChapterId) {
          firstUserAssetReads += 1;
          if (request === "assets") {
            return oldAssetsRequest.promise;
          }
          return Promise.resolve([storyboardAssetSnapshot(series.id, targetChapterId, targetStoryboardId)]);
        }
        return Promise.resolve([storyboardAssetSnapshot(series.id, newUserChapter.id, "new-user-storyboard-id")]);
      });
      let loginCount = 0;
      const logout = vi.fn(() => {
        activeUser = null;
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services = frameNavigationServices({
        category: "characters",
        assetId,
        assetName: "旧新账号共享名角色",
        user: firstUser,
        listChapters,
        listStoryboardAssets,
        login: async ({ username }) => {
          const authenticated = username === "first" ? firstUser : secondUser;
          activeUser = authenticated;
          loginCount += 1;
          window.localStorage.setItem(API_TOKEN_KEY, "frame-late-session-" + loginCount);
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticated));
          return authenticated;
        },
        logout,
      });
      window.localStorage.setItem(API_TOKEN_KEY, "frame-late-session-old");
      renderWorkspace({ ...services, restore: async () => activeUser });

      await screen.findAllByRole("article");
      const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
      const assetCard = screen.getByRole("heading", { name: "旧新账号共享名角色" }).closest("article");
      if (!(assetCard instanceof HTMLElement)) {
        throw new Error("Asset card was not rendered for the first user.");
      }
      await user.click(within(assetCard).getByRole("button", { name: "查看关联镜头：旧新账号共享名角色" }));
      const sourcePanel = await screen.findByRole("region", { name: "关联镜头" });
      await within(sourcePanel).findByText("旧账号来源镜头");
      await user.click(within(sourcePanel).getByRole("button", {
        name: "定位对应镜头：来源章节 · 镜头1",
      }));
      if (request === "chapters") {
        await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(2));
      } else {
        await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));
      }

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      await screen.findByRole("heading", { name: "欢迎回来" });
      await user.type(screen.getByLabelText("账号"), "second");
      await user.type(screen.getByLabelText("密码"), "pass");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");
      const secondSeriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(secondSeriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered for the second user.");
      }
      await user.click(within(secondSeriesCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByText("新账号当前镜头")).toBeInTheDocument();
      await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(3));
      await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(request === "assets" ? 2 : 1));

      await act(async () => {
        if (result === "success") {
          if (request === "chapters") {
            oldChapterRequest.resolve([oldTargetChapter]);
            await oldChapterRequest.promise;
          } else {
            oldAssetsRequest.resolve([storyboardAssetSnapshot(series.id, targetChapterId, targetStoryboardId)]);
            await oldAssetsRequest.promise;
          }
        } else if (request === "chapters") {
          oldChapterRequest.reject(new ApiError("http", "expired", 401));
          await oldChapterRequest.promise.catch(() => undefined);
        } else {
          oldAssetsRequest.reject(new ApiError("http", "expired", 401));
          await oldAssetsRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByText("新账号当前镜头")).toBeInTheDocument();
      expect(screen.getByText("镜头新账号")).toBeInTheDocument();
      expect(screen.queryByText("迟到目标镜头")).not.toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("frame-late-session-1");
      expect(chapterNavigationProbe.intent).toBeNull();
      expect(chapterNavigationProbe.consumedEpochs).toHaveLength(0);
    },
  );

  it.each(frameReferenceCategoryCases)(
    "navigates a ready $category frame reference to the fresh non-first asset card",
    async ({ category, label, kind }) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const assetId = "shared-frame-reference-asset";
      const decoyId = "same-title-different-asset";
      const chapterId = "frame-reference-navigation-chapter-" + category;
      const directories = frameReferenceDirectories(series.id, assetId, decoyId);
      const sourceChapter = chapterWithFrameReferences(
        series.id,
        chapterId,
        "引用导航章节",
        [
          {
            text: "首个分镜原文",
            storyboardAssetId: "source-frame-first",
            kind,
            assetId: decoyId,
          },
          {
            text: "目标分镜原文",
            storyboardAssetId: "source-frame-target",
            kind,
            assetId,
          },
        ],
        2,
      );
      const listChapters = vi.fn(async () => [sourceChapter]);
      const listStoryboardAssets = vi.fn(async (seriesId: string, requestedChapterId: string) => [
        storyboardAssetSnapshot(seriesId, requestedChapterId, "source-frame-first", 1),
        storyboardAssetSnapshot(seriesId, requestedChapterId, "source-frame-target", 2),
      ]);
      const categoryReads = { characters: 0, scenes: 0, props: 0 };
      const pendingTargets = {
        characters: deferred<Character[]>(),
        scenes: deferred<Scene[]>(),
        props: deferred<Prop[]>(),
      };
      const listCharacters = vi.fn(async () => {
        categoryReads.characters += 1;
        if (category !== "characters") {
          return [];
        }
        return categoryReads.characters === 1
          ? directories.source.characters
          : pendingTargets.characters.promise;
      });
      const listScenes = vi.fn(async () => {
        categoryReads.scenes += 1;
        if (category !== "scenes") {
          return [];
        }
        return categoryReads.scenes === 1
          ? directories.source.scenes
          : pendingTargets.scenes.promise;
      });
      const listProps = vi.fn(async () => {
        categoryReads.props += 1;
        if (category !== "props") {
          return [];
        }
        return categoryReads.props === 1
          ? directories.source.props
          : pendingTargets.props.promise;
      });
      const services = frameReferenceNavigationServices({
        listChapters,
        listStoryboardAssets,
        listCharacters,
        listScenes,
        listProps,
      });
      const scrollProbe = installScrollIntoViewProbe();
      window.localStorage.setItem(API_TOKEN_KEY, "frame-reference-navigation-session");
      renderWorkspace(services);

      await screen.findAllByRole("article");
      if (category === "characters") {
        await user.click(screen.getByRole("button", { name: "团队剧集" }));
        await user.click(screen.getByRole("button", { name: /全部剧集/ }));
        await user.click(screen.getByRole("button", { name: /加载更多/ }));
        expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
      }
      const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByText("首个分镜原文")).toBeInTheDocument();
      expect(listChapters).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(listStoryboardAssets).toHaveBeenCalledTimes(1));

      await user.click(screen.getByRole("button", { name: "查看镜头 2 的关联素材" }));
      const sourcePanel = await screen.findByRole("region", { name: "镜头 2 的关联素材" });
      await user.click(within(sourcePanel).getByRole("button", { name: label }));
      expect(await within(sourcePanel).findByText("来源快照目标记录")).toBeInTheDocument();
      expect(listCharacters).toHaveBeenCalledTimes(category === "characters" ? 1 : 0);
      expect(listScenes).toHaveBeenCalledTimes(category === "scenes" ? 1 : 0);
      expect(listProps).toHaveBeenCalledTimes(category === "props" ? 1 : 0);

      await user.click(within(sourcePanel).getByRole("button", { name: "查看素材：同名引用素材" }));
      expect(await screen.findByRole("heading", { name: series.name + " · 素材库" })).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "镜头 2 的关联素材" })).not.toBeInTheDocument();
      const acceptedIntent = assetNavigationProbe.intent;
      if (acceptedIntent === null) {
        throw new Error("The accepted asset navigation intent was not observed.");
      }
      expect(acceptedIntent).toMatchObject({
        userId: "fixture-user",
        seriesId: series.id,
        category,
        assetId,
      });
      expect(assetNavigationProbe.target).not.toBeNull();
      expect(assetNavigationProbe.sourcePanelCloseCallback).not.toBeNull();
      expect(categoryReads).toEqual({
        characters: category === "characters" ? 2 : 0,
        scenes: category === "scenes" ? 2 : 0,
        props: category === "props" ? 2 : 0,
      });

      await act(async () => {
        assetNavigationProbe.sourcePanelCloseCallback?.();
      });
      expect(assetNavigationProbe.intent?.navigationEpoch).toBe(acceptedIntent.navigationEpoch);

      if (category === "characters") {
        await act(async () => {
          pendingTargets.characters.resolve(directories.target.characters);
          await pendingTargets.characters.promise;
        });
      } else if (category === "scenes") {
        await act(async () => {
          pendingTargets.scenes.resolve(directories.target.scenes);
          await pendingTargets.scenes.promise;
        });
      } else {
        await act(async () => {
          pendingTargets.props.resolve(directories.target.props);
          await pendingTargets.props.promise;
        });
      }

      const targetDetail = await screen.findByText("目标目录唯一目标记录", { exact: true });
      const targetCard = targetDetail.closest("article");
      if (!(targetCard instanceof HTMLElement)) {
        throw new Error("The target asset card was not rendered.");
      }
      await waitFor(() => expect(targetCard).toHaveFocus());
      expect(scrollProbe.targets).toContain(targetCard);
      expect(screen.getAllByRole("heading", { name: "同名引用素材" })).toHaveLength(2);
      expect(screen.getAllByRole("article").at(-1)).toBe(targetCard);
      await waitFor(() => expect(assetNavigationProbe.consumedEpochs).toEqual([acceptedIntent.navigationEpoch]));
      expect(assetNavigationProbe.intent).toBeNull();
      expect(assetNavigationProbe.abandonedEpochs).toHaveLength(0);
      expect(categoryReads).toEqual({
        characters: category === "characters" ? 2 : 0,
        scenes: category === "scenes" ? 2 : 0,
        props: category === "props" ? 2 : 0,
      });
      expect(listChapters).toHaveBeenCalledTimes(1);
      expect(listStoryboardAssets).toHaveBeenCalledTimes(1);

      if (category === "characters") {
        await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
        expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
        expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
      }
    },
  );

  it("rejects an old frame-reference callback after a same-series chapter-to-assets-to-chapters cycle", async () => {
    const user = userEvent.setup();
    const series = demoSeries[0]!;
    const assetId = "same-view-reference-asset";
    const decoyId = "same-view-reference-decoy";
    const chapterId = "same-view-reference-chapter";
    const directories = frameReferenceDirectories(series.id, assetId, decoyId);
    const sourceChapter = chapterWithFrameReferences(
      series.id,
      chapterId,
      "同剧集引用章节",
      [
        { text: "首镜头正文", storyboardAssetId: "same-view-first", kind: "scene", assetId: decoyId },
        { text: "目标镜头正文", storyboardAssetId: "same-view-target", kind: "scene", assetId },
      ],
    );
    const listChapters = vi.fn(async () => [sourceChapter]);
    const listStoryboardAssets = vi.fn(async (seriesId: string, requestedChapterId: string) => [
      storyboardAssetSnapshot(seriesId, requestedChapterId, "same-view-first", 1),
      storyboardAssetSnapshot(seriesId, requestedChapterId, "same-view-target", 2),
    ]);
    const categoryReads = { characters: 0, scenes: 0, props: 0 };
    const listCharacters = vi.fn(async (seriesId: string) => {
      categoryReads.characters += 1;
      return [characterSnapshot(seriesId, "默认素材", "default-character")];
    });
    const listScenes = vi.fn(async () => {
      categoryReads.scenes += 1;
      return categoryReads.scenes === 1
        ? directories.source.scenes
        : directories.target.scenes;
    });
    const listProps = vi.fn(async () => {
      categoryReads.props += 1;
      return [];
    });
    const services = frameReferenceNavigationServices({
      listChapters,
      listStoryboardAssets,
      listCharacters,
      listScenes,
      listProps,
    });
    window.localStorage.setItem(API_TOKEN_KEY, "same-view-reference-session");
    renderWorkspace(services);

    const openSeriesView = async (action: "chapters" | "assets") => {
      const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", {
        name: action === "chapters" ? /只读查看章节/ : /只读查看素材库/,
      }));
      if (action === "chapters") {
        await screen.findByText("首镜头正文");
      } else {
        await screen.findByRole("heading", { name: series.name + " · 素材库" });
      }
    };
    await screen.findAllByRole("article");
    await openSeriesView("chapters");
    const staleParentCallback = assetNavigationProbe.parentCallback;
    if (staleParentCallback === null) {
      throw new Error("The original ChapterBrowser asset callback was not captured.");
    }

    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    await screen.findByRole("heading", { name: "我的剧集" });
    await openSeriesView("assets");
    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    await screen.findByRole("heading", { name: "我的剧集" });
    await openSeriesView("chapters");

    const categoryReadsBeforeReplay = { ...categoryReads };
    await act(async () => {
      staleParentCallback({
        seriesId: series.id,
        category: "scenes",
        assetId,
        isCurrent: () => true,
      });
    });
    expect(screen.getByRole("heading", { name: "同剧集引用章节" })).toBeInTheDocument();
    expect(assetNavigationProbe.intent).toBeNull();
    expect(categoryReads).toEqual(categoryReadsBeforeReplay);
    expect(listChapters).toHaveBeenCalledTimes(2);

    const scrollProbe = installScrollIntoViewProbe();
    await user.click(screen.getByRole("button", { name: "查看镜头 2 的关联素材" }));
    const sourcePanel = await screen.findByRole("region", { name: "镜头 2 的关联素材" });
    await user.click(within(sourcePanel).getByRole("button", { name: "场景" }));
    expect(await within(sourcePanel).findByText("来源快照目标记录")).toBeInTheDocument();
    await user.click(within(sourcePanel).getByRole("button", { name: "查看素材：同名引用素材" }));

    expect(await screen.findByText("目标目录唯一目标记录", { exact: true })).toBeInTheDocument();
    const targetDetail = screen.getByText("目标目录唯一目标记录", { exact: true });
    const targetCard = targetDetail.closest("article");
    if (!(targetCard instanceof HTMLElement)) {
      throw new Error("The target scene card was not rendered.");
    }
    await waitFor(() => expect(targetCard).toHaveFocus());
    expect(scrollProbe.targets).toContain(targetCard);
    expect(categoryReads).toEqual({ characters: 1, scenes: 2, props: 0 });
    expect(assetNavigationProbe.intentHistory).toHaveLength(1);
    expect(assetNavigationProbe.consumedEpochs).toEqual([
      assetNavigationProbe.intentHistory[0]!.navigationEpoch,
    ]);
    expect(listChapters).toHaveBeenCalledTimes(2);
  });

  it.each([
    { source: "chapter" as const, result: "success" as const },
    { source: "chapter" as const, result: "401" as const },
    { source: "category" as const, result: "success" as const },
    { source: "category" as const, result: "401" as const },
  ])(
    "ignores a late source $source $result after a different account logs in",
    async ({ source, result }) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const firstUser = authenticatedFixtureUser("reference-old-user", "旧引用账号");
      const secondUser = authenticatedFixtureUser("reference-new-user", "新引用账号");
      let activeUserId = firstUser.id;
      let loginCount = 0;
      const oldChapterRequest = deferred<Chapter[]>();
      const oldCategoryRequest = deferred<Character[]>();
      const sourceChapter = chapterWithFrameReferences(
        series.id,
        "late-reference-source-chapter",
        "迟到来源章节",
        [{
          text: "迟到来源镜头",
          storyboardAssetId: "late-reference-storyboard",
          kind: "character",
          assetId: "late-reference-character",
        }],
      );
      const listChapters = vi.fn(() => source === "chapter" && activeUserId === firstUser.id
        ? oldChapterRequest.promise
        : Promise.resolve([sourceChapter]));
      const listStoryboardAssets = vi.fn(async (seriesId: string, requestedChapterId: string) => [
        storyboardAssetSnapshot(seriesId, requestedChapterId, "late-reference-storyboard", 1),
      ]);
      const listCharacters = vi.fn(() => source === "category" && activeUserId === firstUser.id
        ? oldCategoryRequest.promise
        : Promise.resolve([characterSnapshot(series.id, "迟到引用素材", "late-reference-character")]));
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services = frameReferenceNavigationServices({
        user: firstUser,
        listChapters,
        listStoryboardAssets,
        listCharacters,
        login: async () => {
          activeUserId = secondUser.id;
          loginCount += 1;
          window.localStorage.setItem(API_TOKEN_KEY, "reference-new-session-" + loginCount);
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        logout,
      });
      window.localStorage.setItem(API_TOKEN_KEY, "reference-old-session");
      renderWorkspace(services);
      await screen.findAllByRole("article");

      const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
      if (source === "chapter") {
        expect(await screen.findByText("正在读取章节…")).toBeInTheDocument();
        expect(listChapters).toHaveBeenCalledTimes(1);
      } else {
        expect(await screen.findByText("迟到来源镜头")).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
        const sourcePanel = await screen.findByRole("region", { name: "镜头 1 的关联素材" });
        await user.click(within(sourcePanel).getByRole("button", { name: "角色" }));
        await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(1));
      }

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      await user.type(screen.getByLabelText("账号"), "new-reference-user");
      await user.type(screen.getByLabelText("密码"), "pass");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");

      await act(async () => {
        if (result === "success") {
          if (source === "chapter") {
            oldChapterRequest.resolve([sourceChapter]);
            await oldChapterRequest.promise;
          } else {
            oldCategoryRequest.resolve([
              characterSnapshot(series.id, "旧账号迟到素材", "late-reference-character"),
            ]);
            await oldCategoryRequest.promise;
          }
        } else if (source === "chapter") {
          oldChapterRequest.reject(new ApiError("http", "expired", 401));
          await oldChapterRequest.promise.catch(() => undefined);
        } else {
          oldCategoryRequest.reject(new ApiError("http", "expired", 401));
          await oldCategoryRequest.promise.catch(() => undefined);
        }
      });

      expect(screen.getByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
      expect(screen.queryByText("旧账号迟到素材")).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "镜头 1 的关联素材" })).not.toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("reference-new-session-1");
      expect(activeUserId).toBe(secondUser.id);
      expect(listChapters).toHaveBeenCalledTimes(1);
    },
  );

  it.each(["success", "401"] as const)(
    "ignores a late target category %s after logging into a different account",
    async (result) => {
      const user = userEvent.setup();
      const series = demoSeries[0]!;
      const firstUser = authenticatedFixtureUser("target-old-user", "旧目标账号");
      const secondUser = authenticatedFixtureUser("target-new-user", "新目标账号");
      const assetId = "late-target-reference-asset";
      const sourceChapter = chapterWithFrameReferences(
        series.id,
        "late-target-reference-chapter",
        "迟到目标导航章节",
        [{
          text: "迟到目标引用镜头",
          storyboardAssetId: "late-target-reference-frame",
          kind: "character",
          assetId,
        }],
      );
      const pendingTarget = deferred<Character[]>();
      let categoryReads = 0;
      let loginCount = 0;
      let activeUserId = firstUser.id;
      const listChapters = vi.fn(async () => [sourceChapter]);
      const listStoryboardAssets = vi.fn(async (seriesId: string, requestedChapterId: string) => [
        storyboardAssetSnapshot(seriesId, requestedChapterId, "late-target-reference-frame", 1),
      ]);
      const listCharacters = vi.fn(() => {
        categoryReads += 1;
        if (categoryReads === 1) {
          return Promise.resolve([
            characterSnapshot(series.id, "来源可核对素材", assetId),
          ]);
        }
        if (categoryReads === 2) {
          return pendingTarget.promise;
        }
        return Promise.resolve([]);
      });
      const logout = vi.fn(() => {
        window.localStorage.removeItem(API_TOKEN_KEY);
        window.localStorage.removeItem(API_USER_KEY);
      });
      const services = frameReferenceNavigationServices({
        user: firstUser,
        listChapters,
        listStoryboardAssets,
        listCharacters,
        login: async () => {
          activeUserId = secondUser.id;
          loginCount += 1;
          window.localStorage.setItem(API_TOKEN_KEY, "target-new-session-" + loginCount);
          window.localStorage.setItem(API_USER_KEY, JSON.stringify(secondUser));
          return secondUser;
        },
        logout,
      });
      window.localStorage.setItem(API_TOKEN_KEY, "target-old-session");
      renderWorkspace(services);
      await screen.findAllByRole("article");

      const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) {
        throw new Error("Series card was not rendered.");
      }
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
      expect(await screen.findByText("迟到目标引用镜头")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
      const sourcePanel = await screen.findByRole("region", { name: "镜头 1 的关联素材" });
      await user.click(within(sourcePanel).getByRole("button", { name: "角色" }));
      expect(await within(sourcePanel).findByText("来源可核对素材")).toBeInTheDocument();
      await user.click(within(sourcePanel).getByRole("button", { name: "查看素材：来源可核对素材" }));
      expect(await screen.findByRole("heading", { name: series.name + " · 素材库" })).toBeInTheDocument();
      await waitFor(() => expect(listCharacters).toHaveBeenCalledTimes(2));
      expect(assetNavigationProbe.intent).not.toBeNull();

      await user.click(screen.getByRole("button", { name: "退出登录" }));
      expect(await screen.findByRole("heading", { name: "欢迎回来" })).toBeInTheDocument();
      await user.type(screen.getByLabelText("账号"), "new-target-user");
      await user.type(screen.getByLabelText("密码"), "pass");
      await user.click(screen.getByRole("button", { name: "登录工作台" }));
      await screen.findAllByRole("article");

      await act(async () => {
        if (result === "success") {
          pendingTarget.resolve([
            characterSnapshot(series.id, "旧账号迟到目标素材", assetId),
          ]);
          await pendingTarget.promise;
        } else {
          pendingTarget.reject(new ApiError("http", "expired", 401));
          await pendingTarget.promise.catch(() => undefined);
        }
      });

      expect(screen.getByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
      expect(screen.queryByText("旧账号迟到目标素材")).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: series.name + " · 素材库" })).not.toBeInTheDocument();
      expect(logout).toHaveBeenCalledTimes(1);
      expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("target-new-session-1");
      expect(activeUserId).toBe(secondUser.id);
      expect(listChapters).toHaveBeenCalledTimes(1);
      expect(listCharacters).toHaveBeenCalledTimes(2);
    },
  );

  it("abandons an accepted target intent on leaving for tasks and restores the list filters after returning", async () => {
    const user = userEvent.setup();
    const series = demoSeries[0]!;
    const assetId = "leave-pending-reference-asset";
    const decoyId = "leave-pending-reference-decoy";
    const directories = frameReferenceDirectories(series.id, assetId, decoyId);
    const chapterId = "leave-pending-reference-chapter";
    const sourceChapter = chapterWithFrameReferences(
      series.id,
      chapterId,
      "离开时引用章节",
      [{
        text: "等待定位的引用镜头",
        storyboardAssetId: "leave-pending-storyboard",
        kind: "character",
        assetId,
      }],
    );
    const targetRequest = deferred<Character[]>();
    let characterReads = 0;
    const listCharacters = vi.fn(async () => {
      characterReads += 1;
      if (characterReads === 1) {
        return directories.source.characters;
      }
      return targetRequest.promise;
    });
    const listChapters = vi.fn(async () => [sourceChapter]);
    const listStoryboardAssets = vi.fn(async (seriesId: string, requestedChapterId: string) => [
      storyboardAssetSnapshot(seriesId, requestedChapterId, "leave-pending-storyboard", 1),
    ]);
    const services = frameReferenceNavigationServices({
      listChapters,
      listStoryboardAssets,
      listCharacters,
    });
    window.localStorage.setItem(API_TOKEN_KEY, "leave-pending-reference-session");
    renderWorkspace(services);
    await screen.findAllByRole("article");

    await user.click(screen.getByRole("button", { name: "团队剧集" }));
    await user.click(screen.getByRole("button", { name: /全部剧集/ }));
    await user.click(screen.getByRole("button", { name: /加载更多/ }));
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();

    const seriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
    if (!(seriesCard instanceof HTMLElement)) {
      throw new Error("Series card was not rendered.");
    }
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    expect(await screen.findByText("等待定位的引用镜头")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看镜头 1 的关联素材" }));
    const sourcePanel = await screen.findByRole("region", { name: "镜头 1 的关联素材" });
    await user.click(within(sourcePanel).getByRole("button", { name: "角色" }));
    expect(await within(sourcePanel).findByText("来源快照目标记录")).toBeInTheDocument();
    await user.click(within(sourcePanel).getByRole("button", { name: "查看素材：同名引用素材" }));
    expect(await screen.findByRole("heading", { name: series.name + " · 素材库" })).toBeInTheDocument();
    expect(assetNavigationProbe.intent).not.toBeNull();
    expect(characterReads).toBe(2);

    await user.click(
      within(screen.getByRole("navigation", { name: "主要导航" }))
        .getByRole("button", { name: "我的任务" }),
    );
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    await act(async () => {
      targetRequest.resolve(directories.target.characters);
      await targetRequest.promise;
    });
    expect(screen.getByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    expect(assetNavigationProbe.consumedEpochs).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    expect(screen.getByText("显示 24 / 24 部")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");

    const returnedSeriesCard = screen.getByRole("heading", { name: series.name }).closest("article");
    if (!(returnedSeriesCard instanceof HTMLElement)) {
      throw new Error("Series card was not rendered after returning from tasks.");
    }
    await user.click(within(returnedSeriesCard).getByRole("button", { name: /只读查看素材库/ }));
    expect(await screen.findByRole("heading", { name: series.name + " · 素材库" })).toBeInTheDocument();
    expect(assetNavigationProbe.intent).toBeNull();
    expect(characterReads).toBe(3);
    expect(assetNavigationProbe.consumedEpochs).toHaveLength(0);
  });

  it("uses the API response rather than any legacy series cache key", async () => {
    window.localStorage.setItem(API_TOKEN_KEY, "valid-token");
    window.localStorage.setItem("muse_series_list", "this is not valid JSON");
    const services = createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher: vi.fn(async (input: RequestInfo | URL) => {
        const pathname = new URL(String(input)).pathname;
        return pathname.endsWith("/auth/me")
          ? jsonResponse(fixtureUser)
          : jsonResponse(demoSeries);
      }),
    });
    renderWorkspace(services);

    expect(await screen.findAllByRole("article")).toHaveLength(20);
    expect(window.localStorage.getItem("muse_series_list")).toBe("this is not valid JSON");
  });
});
