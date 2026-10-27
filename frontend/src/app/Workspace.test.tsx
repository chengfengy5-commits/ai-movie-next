import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createElement, useEffect } from "react";
import { AuthProvider, useAuth } from "../features/auth/AuthProvider";
import { Workspace } from "./Workspace";
import { createApiServices, createDemoServices, type WorkspaceServices } from "../shared/api/services";
import { demoSeries } from "../features/series/demoSeries";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
  type StorageLike,
} from "../shared/api/storage";
import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset, User } from "../shared/api/contracts";
import { ApiError } from "../shared/api/errors";
import { MediaGenerationError, type MediaGenerationPort, type MediaGenerationRun } from "../shared/api/mediaGeneration";
import { parsePersonalProductionSnapshot } from "../shared/api/personalProductionNotes";
import { digestPersonalProductionMediaIdentity } from "../features/chapters/personal-production/mediaIdentity";
import { readSeriesWorkbenchPreferences } from "../features/series-workbench/preferences";
import type { FusedSourceEntrySelection } from "../features/series-workbench/fusedSourceEntry";
import type { PersonalRoughCutSnapshot } from "../shared/api/personalRoughCut";
import { MY_TASK_PAGE_SIZE, type MyTaskPage, type MyTaskRecord } from "../shared/api/myTasks";
import type { MyTeam } from "../shared/api/teams";
import type { CreatorModel, CreatorPrompt, CreatorSettingsServicePort } from "../shared/api/creatorSettings";
import type { FusedGenerationInput, FusedGenerationPort, FusedGenerationReceipt } from "../shared/api/fusedGeneration";
import type { AccountProfileServicePort } from "../shared/api/accountProfile";
import type { AccountSecurityServicePort } from "../shared/api/accountSecurity";
import type { TeamDetail, TeamManagementServicePort } from "../shared/api/teamManagement";
import type { TeamSeriesPage, TeamSeriesRecord, TeamSeriesServicePort } from "../shared/api/teamSeries";

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
  mountedInstances: 0,
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

const fusedSourceProbe = vi.hoisted(() => ({
  selection: null as FusedSourceEntrySelection | null,
  parentCallback: null as ((selection: FusedSourceEntrySelection) => void) | null,
}));

vi.mock("../features/chapters/ChapterBrowser", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../features/chapters/ChapterBrowser")>();
  return {
    ...actual,
    ChapterBrowser: (props: Parameters<typeof actual.ChapterBrowser>[0]) => {
      useEffect(() => {
        chapterNavigationProbe.mountedInstances += 1;
        return () => { chapterNavigationProbe.mountedInstances -= 1; };
      }, []);
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
      const parentFusedSourceCallback = props.onFusedSource;
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
        ...(parentFusedSourceCallback === undefined ? {} : {
          onFusedSource: (selection: FusedSourceEntrySelection) => {
            fusedSourceProbe.selection = selection;
            fusedSourceProbe.parentCallback = parentFusedSourceCallback;
            parentFusedSourceCallback(selection);
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

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function AuthenticatedUserSnapshot() {
  const { state } = useAuth();
  return (
    <output data-testid="authenticated-user-snapshot">
      {state.status === "authenticated" ? JSON.stringify(state.user) : state.status}
    </output>
  );
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

function emptyCreatorSettingsService(): CreatorSettingsServicePort {
  const unexpected = async (): Promise<never> => {
    throw new Error("Unexpected creator settings write or detail request.");
  };
  return {
    listModels: vi.fn(async () => []),
    getModel: unexpected,
    createModel: unexpected,
    updateModel: unexpected,
    deleteModel: unexpected,
    reorderModels: unexpected,
    listPrompts: vi.fn(async () => []),
    getPrompt: unexpected,
    createPrompt: unexpected,
    updatePrompt: unexpected,
    deletePrompt: unexpected,
    reorderPrompts: unexpected,
  };
}

function fusedSourceWorkspaceServices(
  submit: FusedGenerationPort["submit"] = async () => ({ taskId: "fused-task", messageId: null }),
): WorkspaceServices {
  const base = createDemoServices(window.localStorage);
  const listCharacters = base.listCharacters;
  const listProps = base.listProps;
  const listChapters = base.listChapters;
  const listStoryboardAssets = base.listStoryboardAssets;
  const creatorSettings = emptyCreatorSettingsService();
  const model: CreatorModel = {
    id: "workspace-fused-image-model",
    category: "image",
    name: "工作台融合模型",
    provider: "openai",
    model: "test-image-model",
    base_url: null,
    params: JSON.stringify({ image_size: { enabled: true, options: ["1024x1024"], default: "1024x1024" } }),
    sort_order: 0,
    is_system: false,
    user_id: "demo-user",
  };
  const prompt: CreatorPrompt = {
    id: "workspace-fused-prompt",
    category: "fuse",
    name: "融合模板",
    system_prompt: "融合系统提示词",
    user_prompt: JSON.stringify({ character: "角色提示词", prop: "道具提示词" }),
    sort_order: 0,
    is_system: false,
    user_id: "demo-user",
  };
  creatorSettings.listModels = vi.fn(async (category) => category === "image" ? [model] : []);
  creatorSettings.listPrompts = vi.fn(async (category) => category === "fuse" ? [prompt] : []);
  return {
    ...base,
    creatorSettings,
    fusedGeneration: { submit },
    listCharacters: async (seriesId, signal) => (await listCharacters(seriesId, signal)).map((asset) => ({
      ...asset,
      image_url: asset.image_url ?? `/uploads/${asset.id}.png`,
    })),
    listProps: async (seriesId, signal) => (await listProps(seriesId, signal)).map((asset) => ({
      ...asset,
      image_url: asset.image_url ?? `/uploads/${asset.id}.png`,
    })),
    listStoryboardAssets: async (seriesId, chapterId, signal) => {
      const [chapters, assets] = await Promise.all([
        listChapters(seriesId, signal),
        listStoryboardAssets(seriesId, chapterId, signal),
      ]);
      const chapter = chapters.find((candidate) => candidate.id === chapterId);
      return assets.map((asset) => ({
        ...asset,
        frame_index: chapter?.content?.findIndex((frame) => Array.isArray(frame.storyboard)
          && frame.storyboard.includes(asset.id)) ?? asset.frame_index,
      }));
    },
  };
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
    chapterNavigationProbe.mountedInstances = 0;
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
    fusedSourceProbe.selection = null;
    fusedSourceProbe.parentCallback = null;
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
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    window.localStorage.clear();
    chapterNavigationProbe.mountedInstances = 0;
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
    fusedSourceProbe.selection = null;
    fusedSourceProbe.parentCallback = null;
    assetNavigationProbe.target = null;
    assetNavigationProbe.parentCallback = null;
    assetNavigationProbe.intent = null;
    assetNavigationProbe.intentHistory.length = 0;
    assetNavigationProbe.consumedEpochs.length = 0;
    assetNavigationProbe.abandonedEpochs.length = 0;
    assetNavigationProbe.sourcePanelCloseCallback = null;
  });

  it("opens the user-facing AI generation page from the main workspace navigation", async () => {
    const user = userEvent.setup();
    const services = createDemoServices(window.localStorage);
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    renderWorkspace(services);

    await screen.findByRole("region", { name: "我的剧集" });
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "AI 生成" }));

    expect(await screen.findByRole("heading", { name: "AI 生成" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "普通视频" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "批量素材生图" })).toBeInTheDocument();
    expect(screen.getByText("当前服务没有接入媒体生成提交接口，页面不会模拟生成结果。")).toBeInTheDocument();
  });

  it("retains an unknown media generation submission across navigation and rechecks with the same key", async () => {
    const user = userEvent.setup();
    const videoModel: CreatorModel = {
      id: "media-nav-video-model",
      category: "video",
      name: "导航测试视频模型",
      provider: "fixture",
      model: "video-fixture",
      base_url: null,
      params: null,
      sort_order: 0,
      is_system: false,
      user_id: "media-nav-user",
    };
    const creatorSettings = {
      ...emptyCreatorSettingsService(),
      listModels: vi.fn(async (category: string) => category === "video" ? [videoModel] : []),
    };
    let rejectSubmission: ((reason: unknown) => void) | null = null;
    let isContextCurrent = () => false;
    const submitVideo = vi.fn<MediaGenerationRun["submitVideo"]>(() => new Promise((_resolve, reject) => {
      rejectSubmission = reject;
    }));
    const lookup = vi.fn<MediaGenerationRun["lookup"]>(() => new Promise(() => {}));
    const run: MediaGenerationRun = {
      isCurrent: () => isContextCurrent(),
      submitImage: vi.fn<MediaGenerationRun["submitImage"]>(() => new Promise(() => {})),
      submitVideo,
      submitBatch: vi.fn(async () => ({ operation: "image.batch" as const, tasks: [] })),
      lookup,
    };
    const mediaGeneration: MediaGenerationPort = {
      beginRun: vi.fn((context) => {
        isContextCurrent = context.isContextCurrent;
        return run;
      }),
    };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      creatorSettings,
      mediaGeneration,
    };
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    renderWorkspace(services);

    await screen.findByRole("heading", { name: "我的剧集" });
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "AI 生成" }));
    await user.selectOptions(await screen.findByLabelText("视频模型"), videoModel.id);
    await user.type(screen.getByLabelText("视频提示词"), "雨巷跟拍镜头");
    await user.click(screen.getByRole("button", { name: "提交视频任务" }));
    expect(submitVideo).toHaveBeenCalledTimes(1);
    const originalInput = submitVideo.mock.calls[0]![0];

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" }));
    await screen.findByRole("heading", { name: "我的任务" });
    if (rejectSubmission === null) throw new Error("The video POST has not started.");
    await act(async () => {
      rejectSubmission?.(new MediaGenerationError("unknown-outcome", "连接中断，提交结果待确认。"));
      await Promise.resolve();
    });

    await user.click(within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "AI 生成" }));
    expect(await screen.findByText(/提交状态尚未确认/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新核对提交" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "重新核对提交" }));
    expect(lookup).toHaveBeenCalledTimes(2);
    expect(lookup.mock.calls.map(([expectation]) => expectation.idempotencyKey))
      .toEqual([originalInput.idempotencyKey, originalInput.idempotencyKey]);
    expect(submitVideo).toHaveBeenCalledTimes(1);
  });

  it("routes from the mobile topbar and guards navigation and logout while a draft is dirty", async () => {
    const user = userEvent.setup();
    const creatorSettings = emptyCreatorSettingsService();
    const services = {
      ...createDemoServices(window.localStorage),
      creatorSettings,
    };
    renderWorkspace(services);

    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");
    const mobileSettingsEntry = within(screen.getByRole("banner"))
      .getByRole("button", { name: "创作者设置", hidden: true });
    expect(mobileSettingsEntry).toBeInTheDocument();
    fireEvent.click(mobileSettingsEntry);

    const creatorSettingsRegion = await screen.findByRole("region", { name: "创作者设置" });
    expect(within(creatorSettingsRegion).getByRole("heading", { name: "创作者设置" })).toBeInTheDocument();
    await waitFor(() => expect(creatorSettings.listModels).toHaveBeenCalled());
    const newModelButton = within(creatorSettingsRegion).getByRole("button", { name: "新建模型" });
    await user.click(newModelButton);
    expect(await screen.findByLabelText("名称")).toBeInTheDocument();

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const taskNav = within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" });
    await user.click(taskNav);
    expect(confirm).toHaveBeenCalledWith("当前有未保存的创作者设置草稿或待核对操作，离开将丢弃页面状态。继续吗？");
    expect(within(creatorSettingsRegion).getByRole("heading", { name: "创作者设置" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "我的任务" })).not.toBeInTheDocument();

    const workspaceSidebar = screen.getByRole("complementary");
    const logoutButton = within(workspaceSidebar).getByRole("button", { name: "退出登录" });
    await user.click(logoutButton);
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(within(creatorSettingsRegion).getByRole("heading", { name: "创作者设置" })).toBeInTheDocument();

    confirm.mockReturnValue(true);
    await user.click(taskNav);
    const tasksRegion = await screen.findByRole("region", { name: "我的任务" });
    expect(within(tasksRegion).getByRole("heading", { name: "我的任务" })).toBeInTheDocument();

    fireEvent.click(within(screen.getByRole("banner"))
      .getByRole("button", { name: "创作者设置", hidden: true }));
    const creatorSettingsRegionAfterTasks = await screen.findByRole("region", { name: "创作者设置" });
    expect(within(creatorSettingsRegionAfterTasks).getByRole("heading", { name: "创作者设置" })).toBeInTheDocument();
    const settingsBackButton = within(creatorSettingsRegionAfterTasks)
      .getByRole("button", { name: "返回工作空间" });
    await user.click(settingsBackButton);
    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    expect(within(seriesRegion).getByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
  });

  it("routes to account on mobile, protects its dirty draft, and refreshes the full user in-session", async () => {
    const user = userEvent.setup();
    let currentUser = authenticatedFixtureUser("account-route-user", "账户作者");
    const restore = vi.fn(async () => currentUser);
    const listSeries = vi.fn(async () => demoSeries);
    const accountProfile: AccountProfileServicePort = {
      getCredits: vi.fn(async () => ({ credits: -2 })),
      updateProfile: vi.fn(async ({ bio }) => {
        currentUser = { ...currentUser, bio: bio === "" ? null : bio };
        return {
          id: currentUser.id,
          username: currentUser.username,
          avatar_url: currentUser.avatar_url,
          bio: currentUser.bio,
        };
      }),
    };
    const accountSecurity: AccountSecurityServicePort = {
      listSessions: vi.fn(async () => []),
      revokeSession: vi.fn(async (sessionId) => ({ message: "设备已下线", session_id: sessionId })),
      changePassword: vi.fn(async () => ({ message: "密码修改成功，请重新登录" })),
    };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      mode: "api",
      apiBaseUrl: "http://127.0.0.1:4175/api",
      restore,
      listSeries,
      accountProfile,
      accountSecurity,
    };
    const { container } = renderWorkspace(services);

    await screen.findByRole("heading", { name: "我的剧集" });
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(1));
    const topbar = container.querySelector("header.topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("Expected the workspace topbar.");
    await user.click(within(topbar).getByRole("button", { name: "我的账户", hidden: true }));
    await screen.findByRole("heading", { name: "我的账户" });
    expect(await screen.findByText("-2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    await user.type(screen.getByRole("textbox", { name: "个人简介" }), "账户草稿");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await user.click(within(topbar).getByRole("button", { name: "我的任务", hidden: true }));
    expect(confirm).toHaveBeenCalledWith("当前有未保存的账户简介或安全表单，或待核对操作；离开将丢弃页面状态。继续吗？");
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "个人简介" })).toHaveValue("账户草稿");

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "保存简介" }));
    expect(await screen.findByText("账户草稿")).toBeInTheDocument();
    expect(await screen.findByText("简介已由独立账户读取确认。")).toBeInTheDocument();
    expect(accountProfile.updateProfile).toHaveBeenCalledTimes(1);
    expect(restore).toHaveBeenCalledTimes(3);
    expect(listSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();

    await user.type(screen.getByLabelText("当前密码"), "old-secret");
    await user.type(screen.getByLabelText("新密码"), "new-secret-123");
    await user.type(screen.getByLabelText("确认新密码"), "new-secret-123");
    confirm.mockClear();

    await user.click(screen.getByRole("button", { name: "返回工作区" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();

    await user.click(within(topbar).getByRole("button", { name: "我的任务", hidden: true }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(confirm).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();

    await user.clear(screen.getByLabelText("当前密码"));
    await user.clear(screen.getByLabelText("新密码"));
    await user.clear(screen.getByLabelText("确认新密码"));
    await user.click(screen.getByRole("button", { name: "返回工作区" }));
    expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(3);
  });

  it("keeps the full-user cache and authenticated state current when an older /me response arrives late", async () => {
    const user = userEvent.setup();
    const storage = new MemoryStorage();
    const oldUser = authenticatedFixtureUser("account-cache-race", "缓存顺序作者");
    const updatedUser = { ...oldUser, bio: "保存后的完整简介" };
    storage.setItem(API_TOKEN_KEY, "account-cache-token");
    storage.setItem(API_USER_KEY, JSON.stringify(oldUser));

    const lateInitialProfileRead = deferred<Response>();
    const lateProfileReadSignal = { value: null as AbortSignal | null };
    let notifyLateProfileReadStarted!: () => void;
    const lateProfileReadStarted = new Promise<void>((resolve) => {
      notifyLateProfileReadStarted = resolve;
    });
    let currentUser = oldUser;
    let meRequestCount = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      if (url.pathname.endsWith("/auth/me")) {
        meRequestCount += 1;
        if (meRequestCount === 1) return jsonResponse(oldUser);
        if (meRequestCount === 2) {
          lateProfileReadSignal.value = init?.signal ?? null;
          notifyLateProfileReadStarted();
          return lateInitialProfileRead.promise;
        }
        return jsonResponse(currentUser);
      }
      if (url.pathname.endsWith("/auth/credits/me")) return jsonResponse({ credits: 17 });
      if (url.pathname.endsWith("/users/me/profile") && init?.method === "PUT") {
        const body = JSON.parse(String(init.body)) as { bio: string };
        currentUser = { ...currentUser, bio: body.bio };
        return jsonResponse({
          id: currentUser.id,
          username: currentUser.username,
          avatar_url: currentUser.avatar_url,
          bio: currentUser.bio,
        });
      }
      if (url.pathname.endsWith("/series") || url.pathname.endsWith("/teams/my")) {
        return jsonResponse([]);
      }
      return jsonResponse({ detail: `Unexpected test request: ${url.pathname}` }, 404);
    });
    const services = createApiServices("http://127.0.0.1:4175/api", { storage, fetcher });
    const listSeries = vi.spyOn(services, "listSeries");

    render(
      <AuthProvider services={services}>
        <>
          <Workspace />
          <AuthenticatedUserSnapshot />
        </>
      </AuthProvider>,
    );

    await screen.findByRole("heading", { name: "我的剧集" });
    await waitFor(() => expect(listSeries).toHaveBeenCalledTimes(1));
    await user.click(within(screen.getByRole("banner")).getByRole("button", {
      name: "我的账户",
      hidden: true,
    }));
    await screen.findByRole("heading", { name: "我的账户" });
    await lateProfileReadStarted;

    await user.click(screen.getByRole("button", { name: "编辑简介" }));
    await user.clear(screen.getByRole("textbox", { name: "个人简介" }));
    await user.type(screen.getByRole("textbox", { name: "个人简介" }), "保存后的完整简介");
    await user.click(screen.getByRole("button", { name: "保存简介" }));

    expect(await screen.findByText("简介已由独立账户读取确认。")).toBeInTheDocument();
    expect(JSON.parse(screen.getByTestId("authenticated-user-snapshot").textContent ?? "null"))
      .toEqual(updatedUser);
    expect(JSON.parse(storage.getItem(API_USER_KEY) ?? "null")).toEqual(updatedUser);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("account-cache-token");
    expect(lateProfileReadSignal.value?.aborted).toBe(true);
    expect(listSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();

    await act(async () => {
      lateInitialProfileRead.resolve(jsonResponse(oldUser));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(JSON.parse(screen.getByTestId("authenticated-user-snapshot").textContent ?? "null"))
      .toEqual(updatedUser);
    expect(JSON.parse(storage.getItem(API_USER_KEY) ?? "null")).toEqual(updatedUser);
    expect(storage.getItem(API_TOKEN_KEY)).toBe("account-cache-token");
    expect(listSeries).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "我的账户" })).toBeInTheDocument();
  });

  it("keeps the workspace leave guard after cancelling an editor while ordering is still dirty", async () => {
    const user = userEvent.setup();
    const creatorSettings = emptyCreatorSettingsService();
    const models: CreatorModel[] = [
      {
        id: "system-text",
        category: "text",
        name: "系统文本模型",
        provider: "openai",
        model: "fixture-model",
        base_url: null,
        params: null,
        sort_order: 0,
        is_system: true,
        user_id: "system",
      },
      {
        id: "owner-text",
        category: "text",
        name: "我的文本模型",
        provider: "openai",
        model: "fixture-model",
        base_url: null,
        params: null,
        sort_order: 1,
        is_system: false,
        user_id: "demo-user",
      },
    ];
    vi.mocked(creatorSettings.listModels).mockResolvedValue(models);
    const services = {
      ...createDemoServices(window.localStorage),
      creatorSettings,
    };
    renderWorkspace(services);

    await screen.findByRole("heading", { name: "欢迎回来" });
    await user.type(screen.getByLabelText("账号"), "demo");
    await user.type(screen.getByLabelText("密码"), "demo123");
    await user.click(screen.getByRole("button", { name: "登录工作台" }));
    await screen.findAllByRole("article");
    fireEvent.click(within(screen.getByRole("banner"))
      .getByRole("button", { name: "创作者设置", hidden: true }));
    await screen.findByRole("heading", { name: "创作者设置" });
    await screen.findByText("我的文本模型");

    await user.click(screen.getByRole("button", { name: "新建模型" }));
    await user.type(await screen.findByLabelText("名称"), "未完成的模型草稿");
    const personalCard = screen.getByText("我的文本模型").closest("article");
    expect(personalCard).not.toBeNull();
    await user.click(within(personalCard!).getByRole("button", { name: "将我的文本模型设为默认" }));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "保存排序" })).toBeEnabled();

    confirm.mockReturnValue(false);
    const taskNav = within(screen.getByRole("navigation", { name: "主要导航" }))
      .getByRole("button", { name: "我的任务" });
    await user.click(taskNav);
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("heading", { name: "创作者设置" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "退出登录" }));
    expect(confirm).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("heading", { name: "创作者设置" })).toBeInTheDocument();
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

    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesFilters = within(seriesRegion).getByRole("group", { name: "剧集筛选" });
    await within(seriesRegion).findAllByRole("article");
    const firstCard = within(seriesRegion).getAllByRole("article")[0]!;
    expect(within(firstCard).getByRole("heading", { level: 3 })).toHaveTextContent("雾港来信");
    expect(within(seriesRegion).getAllByRole("article")).toHaveLength(20);
    expect(within(seriesRegion).getByRole("button", { name: /加载更多/ })).toBeInTheDocument();
    expect(within(seriesRegion).getByText("当前账号受限")).toBeInTheDocument();
    const restrictedCard = within(seriesRegion).getByText("当前账号受限").closest("article");
    expect(restrictedCard).not.toBeNull();
    expect(within(restrictedCard as HTMLElement).queryByRole("button", { name: /只读查看素材库/ }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(within(seriesRegion).getAllByText("林编剧").length).toBeGreaterThan(0);
    expect(within(seriesRegion).getAllByText("拾光工作室").length).toBeGreaterThan(0);
    expect(within(seriesRegion).getAllByText("我负责").length).toBeGreaterThan(0);

    await user.click(within(seriesRegion).getByRole("button", { name: /加载更多/ }));
    expect(within(seriesRegion).getAllByRole("article")).toHaveLength(24);

    await user.click(within(seriesFilters).getByRole("button", { name: "我创建的" }));
    expect(within(seriesRegion).getAllByRole("article")).toHaveLength(12);
    const firstCreatedCard = within(seriesRegion).getAllByRole("article")[0]!;
    expect(within(firstCreatedCard).getByRole("heading", { level: 3 }))
      .toHaveTextContent("雾港来信");

    await user.click(within(seriesFilters).getByRole("button", { name: "团队剧集" }));
    expect(within(seriesRegion).getAllByRole("article")).toHaveLength(16);
    await user.click(within(seriesFilters).getByRole("button", { name: "我认领的" }));
    expect(within(seriesRegion).getAllByRole("article")).toHaveLength(4);

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
    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesFilters = within(seriesRegion).getByRole("group", { name: "剧集筛选" });
    await within(seriesRegion).findAllByRole("article");

    await user.click(within(seriesFilters).getByRole("button", { name: "团队剧集" }));
    await user.click(within(seriesFilters).getByRole("button", { name: /全部剧集/ }));
    await user.click(within(seriesRegion).getByRole("button", { name: /加载更多/ }));
    expect(within(seriesRegion).getByText("显示 24 / 24 部")).toBeInTheDocument();

    const entryCard = within(seriesRegion).getAllByRole("article")[0]!;
    await user.click(within(entryCard).getByRole("button", { name: /只读查看素材库/ }));
    const assetLibrary = await screen.findByRole("region", { name: "雾港来信 · 素材库" });
    expect(await within(assetLibrary).findByRole("heading", { name: "雾港来信 · 素材库" }))
      .toBeInTheDocument();
    expect(await within(assetLibrary).findByRole("heading", { name: "林岚" })).toBeInTheDocument();
    expect(listChapters).not.toHaveBeenCalled();
    const hiddenList = container.querySelector(".series-list-view") as HTMLElement;
    expect(hiddenList.hidden).toBe(true);
    expect(hiddenList.hasAttribute("inert")).toBe(true);
    expect(screen.queryByRole("button", { name: "团队剧集" })).not.toBeInTheDocument();

    const assetCategories = within(assetLibrary).getByRole("group", { name: "素材分类" });
    await user.click(within(assetCategories).getByRole("button", { name: "场景" }));
    expect(await within(assetLibrary).findByRole("heading", { name: "旧街清晨" })).toBeInTheDocument();
    await user.click(within(assetCategories).getByRole("button", { name: "道具" }));
    expect(await within(assetLibrary).findByRole("heading", { name: "黄铜钥匙" })).toBeInTheDocument();
    expect(listChapters).not.toHaveBeenCalled();

    await user.click(within(assetLibrary).getByRole("button", { name: "返回剧集列表" }));

    const returnedList = await screen.findByRole("region", { name: "我的剧集" });
    expect(await within(returnedList).findByRole("heading", { name: "我的剧集" }))
      .toBeInTheDocument();
    expect(within(returnedList).getByText("显示 24 / 24 部")).toBeInTheDocument();
    const returnedFilters = within(returnedList).getByRole("group", { name: "剧集筛选" });
    expect(within(returnedFilters).getByRole("button", { name: /全部剧集/ }))
      .toHaveAttribute("aria-pressed", "true");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("keeps the real ChapterBrowser instance mounted while Workspace switches workbench routes", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "https://workbench.example.test/api";
    const services = { ...createDemoServices(window.localStorage), apiBaseUrl };
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const { container } = renderWorkspace(services);

    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    await within(seriesRegion).findAllByRole("article");
    const seriesCard = within(seriesRegion).getByRole("heading", { name: "雾港来信" }).closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));

    const chapterRoot = container.querySelector(".chapter-browser");
    expect(chapterRoot).not.toBeNull();
    expect(chapterNavigationProbe.mountedInstances).toBe(1);
    const topbar = container.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    fireEvent.click(within(topbar).getByRole("button", { name: "剧集工作台" }));
    expect(await screen.findByRole("heading", { name: "创作区" })).toBeInTheDocument();
    expect(chapterNavigationProbe.mountedInstances).toBe(1);
    expect(container.querySelector(".chapter-browser")).toBe(chapterRoot);
    const resourceColumn = await screen.findByRole("region", { name: "镜头 1 的五类关联素材" });
    for (const category of ["故事板", "角色", "场景", "道具", "融合素材"]) {
      expect(within(resourceColumn).getByRole("heading", { name: category })).toBeInTheDocument();
    }
    const characterSourceId = `${demoSeries[0]!.id}-character-linlan`;
    const preferenceScope = { userId: "demo-user", apiBaseUrl, seriesId: demoSeries[0]!.id };
    const separator = screen.getByRole("separator", { name: "调整剧集内容与创作区宽度" });
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    expect(readSeriesWorkbenchPreferences(preferenceScope).preferences.leftWidths.storyboard).toBe(72);

    fireEvent.click(screen.getByRole("button", { name: "素材库" }));
    expect(await screen.findByRole("region", { name: "雾港来信 · 素材库" })).toBeInTheDocument();
    expect(chapterNavigationProbe.mountedInstances).toBe(1);
    expect(container.querySelector(".chapter-browser")).toBe(chapterRoot);
    fireEvent.keyDown(separator, { key: "ArrowRight" });
    const leftWidths = readSeriesWorkbenchPreferences(preferenceScope).preferences.leftWidths;
    expect(leftWidths).toMatchObject({ storyboard: 72, assets: 72 });
    fireEvent.click(screen.getByRole("button", { name: "分镜" }));
    await screen.findByRole("button", { name: "林岚" });

    const chatRoot = container.querySelector(".chat-assistant-page");
    const mediaRoot = container.querySelector(".media-generation-page");
    expect(chatRoot).not.toBeNull();
    expect(mediaRoot).not.toBeNull();
    const routeTabs = container.querySelector(".series-workbench-route-tabs");
    if (!(routeTabs instanceof HTMLElement)) throw new Error("The workbench route tabs were not rendered.");
    fireEvent.click(within(routeTabs).getByRole("button", { name: "图片与视频" }));
    const currentResourceColumn = screen.getByRole("region", { name: "镜头 1 的五类关联素材" });
    await user.click(await within(currentResourceColumn).findByRole("button", { name: "林岚" }));
    await waitFor(() => {
      expect(screen.getByLabelText("素材类型")).toHaveValue("character");
      expect(screen.getByLabelText("目标素材")).toHaveValue(characterSourceId);
    });
    const mediaPrompt = screen.getByLabelText("视频提示词");
    await user.type(mediaPrompt, "右侧媒体草稿保持在原页面");
    await user.selectOptions(screen.getByLabelText("目标素材"), "");
    await waitFor(() => expect(screen.getByLabelText("目标素材")).toHaveValue(""));
    fireEvent.click(within(routeTabs).getByRole("button", { name: "AI 助手" }));
    expect(container.querySelector(".media-generation-page")).toBe(mediaRoot);
    expect(within(routeTabs).getByRole("button", { name: "AI 助手" })).toHaveAttribute("aria-pressed", "true");
    await screen.findByLabelText("会话目标");
    fireEvent.click(within(routeTabs).getByRole("button", { name: "图片与视频" }));
    expect(container.querySelector(".media-generation-page")).toBe(mediaRoot);
    expect(screen.getByLabelText("视频提示词")).toBe(mediaPrompt);
    expect(mediaPrompt).toHaveValue("右侧媒体草稿保持在原页面");
    expect(screen.getByLabelText("目标素材")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "素材库" }));
    const assetLibrary = await screen.findByRole("region", { name: "雾港来信 · 素材库" });
    const linlanCard = within(assetLibrary).getByRole("heading", { name: "林岚" }).closest("article");
    if (!(linlanCard instanceof HTMLElement)) throw new Error("The linked character card was not rendered.");
    await user.click(within(linlanCard).getByRole("button", { name: "查看关联镜头：林岚" }));
    const assetUsage = await within(assetLibrary).findByRole("region", { name: "关联镜头" });
    const firstUsageChapter = within(assetUsage).getByRole("heading", { name: /第一章 · 雾起/ }).closest("li");
    if (!(firstUsageChapter instanceof HTMLElement)) throw new Error("The verified asset chapter was not rendered.");
    const secondUsageChapter = within(assetUsage).getByRole("heading", { name: /第二章 · 来信/ }).closest("li");
    if (!(secondUsageChapter instanceof HTMLElement)) throw new Error("The second verified asset chapter was not rendered.");
    const mediaChapterSelect = within(mediaRoot as HTMLElement).getByLabelText("章节") as HTMLSelectElement;
    const secondChapterId = Array.from(mediaChapterSelect.options)
      .find((option) => option.textContent?.includes("第二章 · 来信"))?.value;
    if (secondChapterId === undefined || secondChapterId === "") throw new Error("The verified media chapter option was not rendered.");
    await user.click(within(secondUsageChapter).getByRole("button", { name: /在 AI 中使用此素材/ }));
    await waitFor(() => expect(screen.getByLabelText("目标素材")).toHaveValue(characterSourceId));
    await waitFor(() => expect(within(mediaRoot as HTMLElement).getByLabelText("章节")).toHaveValue(secondChapterId));
    expect(screen.getByRole("button", { name: "素材库" })).toHaveAttribute("aria-pressed", "true");
    const mediaTargetBeforeLocate = (screen.getByLabelText("目标素材") as HTMLSelectElement).value;
    await user.click(within(firstUsageChapter).getByRole("button", { name: /定位对应镜头/ }));
    expect(screen.getByRole("button", { name: "分镜" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("目标素材")).toHaveValue(mediaTargetBeforeLocate);
    expect(within(mediaRoot as HTMLElement).getByLabelText("章节")).toHaveValue(secondChapterId);
    expect(container.querySelector(".chat-assistant-page")).toBe(chatRoot);
    expect(container.querySelector(".chapter-browser")).toBe(chapterRoot);

    fireEvent.click(within(topbar).getByRole("button", { name: "AI 助手", hidden: true }));
    expect(await screen.findByRole("heading", { name: "AI 聊天助手" })).toBeInTheDocument();
    expect(chapterNavigationProbe.mountedInstances).toBe(1);
    expect(container.querySelector(".chapter-browser")).toBe(chapterRoot);
    expect(container.querySelector(".chat-assistant-page")).toBe(chatRoot);
    expect(container.querySelector(".media-generation-page")).toBe(mediaRoot);

    fireEvent.click(within(topbar).getByRole("button", { name: "AI 生成", hidden: true }));
    expect(await screen.findByRole("heading", { name: "AI 生成" })).toBeInTheDocument();
    expect(chapterNavigationProbe.mountedInstances).toBe(1);
    expect(container.querySelector(".chapter-browser")).toBe(chapterRoot);
    expect(container.querySelector(".chat-assistant-page")).toBe(chatRoot);
    expect(container.querySelector(".media-generation-page")).toBe(mediaRoot);
    expect(screen.getByLabelText("视频提示词")).toBe(mediaPrompt);
    expect(screen.getByLabelText("视频提示词")).toHaveValue("右侧媒体草稿保持在原页面");

    fireEvent.click(within(topbar).getByRole("button", { name: "返回剧集工作台" }));
    expect(await screen.findByRole("heading", { name: "创作区" })).toBeInTheDocument();
    expect(container.querySelector(".chapter-browser")).toBe(chapterRoot);
    expect(container.querySelector(".chat-assistant-page")).toBe(chatRoot);
    expect(container.querySelector(".media-generation-page")).toBe(mediaRoot);
  }, 15_000);

  it("persists only confirmed workbench widths when cancelling drags through view and route changes", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "https://workbench-width-cancel.example.test/api";
    const services = { ...createDemoServices(window.localStorage), apiBaseUrl };
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const preferenceScope = { userId: "demo-user", apiBaseUrl, seriesId: demoSeries[0]!.id };

    async function openWorkbench() {
      const rendered = renderWorkspace(services);
      const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
      await within(seriesRegion).findAllByRole("article");
      const seriesCard = within(seriesRegion).getByRole("heading", { name: "雾港来信" }).closest("article");
      if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
      await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
      const topbar = rendered.container.querySelector(".topbar");
      if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
      fireEvent.click(within(topbar).getByRole("button", { name: "剧集工作台" }));
      await screen.findByRole("heading", { name: "创作区" });
      const surface = rendered.container.querySelector(".series-workbench-surface");
      if (!(surface instanceof HTMLElement)) throw new Error("The workbench surface was not rendered.");
      vi.spyOn(surface, "getBoundingClientRect").mockReturnValue({ width: 1_000 } as DOMRect);
      const separator = screen.getByRole("separator", { name: "调整剧集内容与创作区宽度" });
      Object.defineProperties(separator, {
        setPointerCapture: { configurable: true, value: vi.fn() },
        hasPointerCapture: { configurable: true, value: () => true },
        releasePointerCapture: { configurable: true, value: vi.fn() },
      });
      return { ...rendered, separator };
    }

    let rendered = await openWorkbench();
    expect(rendered.separator).toHaveAttribute("aria-valuenow", "70");
    fireEvent.pointerDown(rendered.separator, { pointerId: 301, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(rendered.separator, { pointerId: 301, clientX: 200 });
    expect(rendered.separator).toHaveAttribute("aria-valuenow", "80");
    expect(readSeriesWorkbenchPreferences(preferenceScope).preferences.leftWidths.storyboard).toBe(70);

    fireEvent.click(screen.getByRole("button", { name: "素材库" }));
    await screen.findByRole("region", { name: "雾港来信 · 素材库" });
    expect(readSeriesWorkbenchPreferences(preferenceScope).preferences.leftWidths)
      .toMatchObject({ storyboard: 70, assets: 70 });
    rendered.unmount();

    rendered = await openWorkbench();
    expect(rendered.separator).toHaveAttribute("aria-valuenow", "70");
    fireEvent.pointerDown(rendered.separator, { pointerId: 302, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(rendered.separator, { pointerId: 302, clientX: 200 });
    expect(rendered.separator).toHaveAttribute("aria-valuenow", "80");
    const routeTabs = rendered.container.querySelector(".series-workbench-route-tabs");
    if (!(routeTabs instanceof HTMLElement)) throw new Error("The workbench route tabs were not rendered.");
    fireEvent.click(within(routeTabs).getByRole("button", { name: "图片与视频" }));
    expect(rendered.separator).toHaveAttribute("aria-valuenow", "70");
    expect(readSeriesWorkbenchPreferences(preferenceScope).preferences.leftWidths)
      .toMatchObject({ storyboard: 70, assets: 70 });
    rendered.unmount();

    rendered = await openWorkbench();
    expect(rendered.separator).toHaveAttribute("aria-valuenow", "70");
    fireEvent.pointerDown(rendered.separator, { pointerId: 303, button: 0, isPrimary: true, clientX: 100 });
    fireEvent.pointerMove(rendered.separator, { pointerId: 303, clientX: 200 });
    fireEvent.pointerUp(rendered.separator, { pointerId: 303 });
    expect(readSeriesWorkbenchPreferences(preferenceScope).preferences.leftWidths.assets).toBe(80);
    rendered.unmount();

    rendered = await openWorkbench();
    expect(rendered.separator).toHaveAttribute("aria-valuenow", "80");
  }, 15_000);

  it("uses the team-selected series as the real chapter, asset, and media source host", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const createdAt = "2026-10-28T00:00:00";
    const team = {
      id: "workbench-team-1117",
      name: "工作台团队",
      owner_id: "fixture-user",
      created_at: createdAt,
    };
    const teamDetail: TeamDetail = {
      ...team,
      my_role: "owner",
      my_permissions: [],
      members: [{
        user_id: "fixture-user",
        username: "演示创作者",
        avatar_url: null,
        role: "owner",
        permissions: [],
        joined_at: createdAt,
      }],
    };
    const hostA: Series = {
      ...demoSeries[0]!,
      id: "workbench-host-a-1117",
      name: "已有宿主 A",
      user_id: "fixture-user",
      team_id: null,
      team_name: null,
      can_enter: true,
    };
    const hostB: Series = {
      ...demoSeries[0]!,
      id: "workbench-team-b-1117",
      name: "团队剧集 B",
      user_id: "fixture-user",
      team_id: team.id,
      team_name: team.name,
      can_enter: true,
    };
    const characterId = "workbench-team-b-character-1117";
    const hostAChapter = chapterSnapshot(hostA.id, "workbench-a-chapter-1117", "A 章节", "A 正文");
    const hostBChapter = chapterWithAssetReference(
      hostB.id,
      "workbench-b-chapter-1117",
      "B 章节",
      "B 正文",
      "character",
      characterId,
    );
    const teamCharacter = characterSnapshot(hostB.id, "B 剧集角色", characterId);
    const teamSeriesRow: TeamSeriesRecord = {
      id: hostB.id,
      name: hostB.name,
      description: hostB.description,
      image_url: hostB.image_url,
      user_id: hostB.user_id,
      owner_name: "演示创作者",
      owner_avatar_url: null,
      claimed_by: null,
      claimed_by_username: "",
      claimed_by_avatar_url: null,
      created_at: createdAt,
      updated_at: createdAt,
      chapter_count: 1,
      asset_counts: { characters: 1, scenes: 0, props: 0, storyboard: 1 },
    };
    const apiServices = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher: vi.fn() });
    const managementPort = apiServices.teamManagement as TeamManagementServicePort;
    vi.spyOn(managementPort, "listTeams").mockResolvedValue([{
      ...team,
      member_count: 1,
      my_role: "owner",
    }]);
    vi.spyOn(managementPort, "getTeam").mockResolvedValue(teamDetail);
    const teamSeriesPort = apiServices.teamSeries as TeamSeriesServicePort;
    vi.spyOn(teamSeriesPort, "listTeamSeries").mockResolvedValue({
      items: [teamSeriesRow],
      total: 1,
      page: 1,
      page_size: 10,
      total_pages: 1,
    });
    const listChapters = vi.fn(async (seriesId: string) => seriesId === hostB.id ? [hostBChapter] : [hostAChapter]);
    const listCharacters = vi.fn(async (seriesId: string) => seriesId === hostB.id ? [teamCharacter] : []);
    const listStoryboardAssets = vi.fn(async (seriesId: string, chapterId: string) => (
      [{
        ...storyboardAssetSnapshot(seriesId, chapterId, `${chapterId}-frame`, 0),
      }]
    ));
    const getSeries = vi.fn(async (seriesId: string) => {
      if (seriesId === hostB.id) return hostB;
      throw new Error(`Unexpected team-series open for ${seriesId}`);
    });
    const base = createDemoServices(window.localStorage);
    const services: WorkspaceServices = {
      ...base,
      ...apiServices,
      mode: "api",
      apiBaseUrl,
      restore: async () => authenticatedFixtureUser(),
      listSeries: vi.fn(async () => [hostA, hostB]),
      getSeries,
      listChapters,
      listStoryboardAssets,
      listCharacters,
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    window.localStorage.setItem(API_TOKEN_KEY, "workbench-team-host-session");
    window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticatedFixtureUser()));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container } = renderWorkspace(services);
    const navigation = await screen.findByRole("navigation", { name: "主要导航" });

    await user.click(within(navigation).getByRole("button", { name: "我的团队" }));
    const teamDirectory = await screen.findByRole("region", { name: "我的团队目录" });
    await user.click(await within(teamDirectory).findByRole("button", { name: /工作台团队/ }));
    await screen.findByRole("heading", { name: "工作台团队" });
    await user.click(screen.getByRole("tab", { name: "团队剧集" }));
    const teamSeriesCard = await screen.findByRole("heading", { name: hostB.name });
    const teamSeriesArticle = teamSeriesCard.closest("article");
    if (!(teamSeriesArticle instanceof HTMLElement)) throw new Error("The team-series row was not rendered.");
    await user.click(within(teamSeriesArticle).getByRole("button", { name: /只读查看章节/ }));
    await screen.findByText("B 正文");
    expect(listChapters).toHaveBeenCalledWith(hostB.id, expect.any(AbortSignal));
    expect(container.querySelector(".chapter-browser")).toHaveTextContent("B 章节");

    await user.click(screen.getByRole("button", { name: "返回剧集列表" }));
    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const hostACard = within(seriesRegion).getByRole("heading", { name: hostA.name }).closest("article");
    if (!(hostACard instanceof HTMLElement)) throw new Error("The existing A series was not rendered.");
    await user.click(within(hostACard).getByRole("button", { name: /只读查看章节/ }));
    await screen.findByText("A 正文");
    expect(container.querySelector(".chapter-browser")).toHaveTextContent("A 章节");

    await user.click(within(navigation).getByRole("button", { name: "我的团队" }));
    const reopenedTeamDirectory = await screen.findByRole("region", { name: "我的团队目录" });
    await user.click(await within(reopenedTeamDirectory).findByRole("button", { name: /工作台团队/ }));
    await screen.findByRole("heading", { name: "工作台团队" });
    await user.click(screen.getByRole("tab", { name: "团队剧集" }));
    const reopenedTeamSeriesCard = await screen.findByRole("heading", { name: hostB.name });
    const reopenedTeamSeriesArticle = reopenedTeamSeriesCard.closest("article");
    if (!(reopenedTeamSeriesArticle instanceof HTMLElement)) throw new Error("The team-series row was not rendered again.");
    await user.click(within(reopenedTeamSeriesArticle).getByRole("button", { name: /只读查看素材库/ }));
    await screen.findByRole("heading", { name: `${hostB.name} · 素材库` });
    expect(getSeries).toHaveBeenCalledWith(hostB.id, expect.any(AbortSignal));
    expect(listCharacters).toHaveBeenCalledWith(hostB.id, expect.any(AbortSignal));
    expect(listCharacters.mock.calls.at(-1)?.[0]).toBe(hostB.id);

    const topbar = container.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    await user.click(within(topbar).getByRole("button", { name: "剧集工作台" }));
    await screen.findByRole("heading", { name: "创作区" });
    fireEvent.click(screen.getByRole("button", { name: "素材库" }));
    const workbenchLibrary = await screen.findByRole("region", { name: `${hostB.name} · 素材库` });
    const characterCard = within(workbenchLibrary).getByRole("heading", { name: teamCharacter.name }).closest("article");
    if (!(characterCard instanceof HTMLElement)) throw new Error("The B character card was not rendered.");
    const routeTabs = container.querySelector(".series-workbench-route-tabs");
    if (!(routeTabs instanceof HTMLElement)) throw new Error("The workbench route tabs were not rendered.");
    await user.click(within(routeTabs).getByRole("button", { name: "图片与视频" }));
    const mediaRoot = container.querySelector(".media-generation-page");
    if (!(mediaRoot instanceof HTMLElement)) throw new Error("The workbench media consumer was not rendered.");
    const media = within(mediaRoot);
    await user.click(media.getByRole("button", { name: "普通视频" }));
    await user.click(within(characterCard).getByRole("button", { name: `查看关联镜头：${teamCharacter.name}` }));
    const usage = await within(workbenchLibrary).findByRole("region", { name: "关联镜头" });
    const teamChapterRow = within(usage).getByRole("heading", { name: /B 章节/ }).closest("li");
    if (!(teamChapterRow instanceof HTMLElement)) throw new Error("The B chapter usage row was not rendered.");
    await user.click(within(teamChapterRow).getByRole("button", { name: /在 AI 中使用此素材/ }));
    await waitFor(() => expect(media.getByLabelText("剧集")).toHaveValue(hostB.id));
    expect(media.getByLabelText("章节")).toHaveValue(hostBChapter.id);
    expect(media.getByLabelText("目标素材")).toHaveValue(characterId);
    expect(listChapters.mock.calls.some(([seriesId]) => seriesId === hostB.id)).toBe(true);
  }, 20_000);

  it("uses a verified asset chapter to update idle chat from the workbench asset view", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "https://workbench-asset-chat.example.test/api";
    const series = demoSeries[0]!;
    const assetId = `${series.id}-character-linlan`;
    const chapter = chapterWithAssetReference(series.id, "workbench-chat-asset-chapter", "第一章 · 雾起", "关联正文", "character", assetId);
    const unexpectedChatWrite = async (): Promise<never> => { throw new Error("Unexpected chat mutation in source-target test."); };
    const unexpectedStoryboardWrite = async (): Promise<never> => { throw new Error("Unexpected storyboard mutation in source-target test."); };
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      apiBaseUrl,
      chatAssistant: {
        beginRun: () => ({
          isCurrent: () => true,
          listMessages: async () => [],
          createMessage: unexpectedChatWrite,
          deleteMessage: unexpectedChatWrite,
          getTaskByMessage: async () => ({ kind: "missing" }),
          stream: async function* () { yield { type: "done", data: {} }; },
        }),
      },
      storyboardGeneration: {
        listModels: async () => [],
        listPrompts: async () => [],
        quote: unexpectedStoryboardWrite,
        stream: async function* () { yield { type: "complete", data: {} }; },
        getSubmission: unexpectedStoryboardWrite,
        getTask: unexpectedStoryboardWrite,
      },
      creatorSettings: emptyCreatorSettingsService(),
      listChapters: vi.fn(async () => [chapter]),
      listStoryboardAssets: vi.fn(async () => []),
      listCharacters: vi.fn(async () => [characterSnapshot(series.id, "林岚", assetId)]),
      listScenes: vi.fn(async () => []),
      listProps: vi.fn(async () => []),
    };
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    renderWorkspace(services);

    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    await within(seriesRegion).findAllByRole("article");
    const seriesCard = within(seriesRegion).getByRole("heading", { name: "雾港来信" }).closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const topbar = document.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    fireEvent.click(within(topbar).getByRole("button", { name: "剧集工作台" }));
    await screen.findByRole("heading", { name: "创作区" });
    const routeTabs = document.querySelector(".series-workbench-route-tabs");
    if (!(routeTabs instanceof HTMLElement)) throw new Error("The workbench route tabs were not rendered.");
    fireEvent.click(within(routeTabs).getByRole("button", { name: "AI 助手" }));
    await waitFor(() => expect(screen.getByLabelText("会话目标")).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "素材库" }));
    const assetLibrary = await screen.findByRole("region", { name: "雾港来信 · 素材库" });
    const linlanCard = within(assetLibrary).getByRole("heading", { name: "林岚" }).closest("article");
    if (!(linlanCard instanceof HTMLElement)) throw new Error("The linked character card was not rendered.");
    await user.click(within(linlanCard).getByRole("button", { name: "查看关联镜头：林岚" }));
    const usage = await within(assetLibrary).findByRole("region", { name: "关联镜头" });
    const chapterRow = within(usage).getByRole("heading", { name: /第一章 · 雾起/ }).closest("li");
    if (!(chapterRow instanceof HTMLElement)) throw new Error("The verified chapter was not rendered.");
    await user.click(within(chapterRow).getByRole("button", { name: /在 AI 中使用此素材/ }));

    await waitFor(() => expect(screen.getByLabelText("会话目标")).toHaveValue(`asset:character:${assetId}`));
    expect(screen.getByRole("button", { name: "素材库" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("章节")).toHaveValue(chapter.id);
  }, 15_000);

  it("offers fused asset management in the mobile task entries for an enterable selected series", async () => {
    const user = userEvent.setup();
    const services = createDemoServices(window.localStorage);
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const { container } = renderWorkspace(services);

    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    await within(seriesRegion).findAllByRole("article");
    const seriesCard = within(seriesRegion).getAllByRole("article")[0]!;
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看素材库/ }));
    await screen.findByRole("region", { name: "雾港来信 · 素材库" });

    const topbarActions = container.querySelector(".topbar-actions");
    expect(topbarActions).not.toBeNull();
    const mobileEntry = within(topbarActions as HTMLElement).getByRole("button", { name: "融合素材" });
    expect(mobileEntry).toHaveClass("mobile-task-entry");
    expect(mobileEntry).toBeEnabled();
    await user.click(mobileEntry);

    expect(await screen.findByRole("region", { name: "雾港来信 · 融合素材" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "雾港来信 · 融合素材" })).toBeInTheDocument();
  });

  it("routes real frame and asset selections through the stable fused page host", async () => {
    const user = userEvent.setup();
    const services = fusedSourceWorkspaceServices();
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const { container } = renderWorkspace(services);

    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesCard = (await within(seriesRegion).findByRole("heading", { name: "雾港来信" })).closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const topbar = container.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    await user.click(within(topbar).getByRole("button", { name: "剧集工作台" }));

    const frameColumn = await screen.findByRole("region", { name: "镜头 1 的五类关联素材" });
    const frameFusionButton = within(frameColumn).getByRole("button", { name: "融合镜头角色" });
    await waitFor(() => expect(frameFusionButton).toBeEnabled());
    await user.click(frameFusionButton);
    const fusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    const fusedPage = container.querySelector(".fused-assets-page");
    expect(fusedPage).not.toBeNull();
    expect(await within(fusedRegion).findByText(/已回读来源/)).toBeInTheDocument();
    await user.click(within(fusedRegion).getByRole("button", { name: "应用来源到当前草稿" }));
    expect(await within(fusedRegion).findByLabelText("当前分镜目标")).toHaveTextContent(`${demoSeries[0]!.id}-chapter-opening`);

    const customImageInput = within(fusedRegion).getByLabelText("自定义图片地址");
    await waitFor(() => expect(customImageInput).toBeEnabled());
    await user.type(customImageInput, "https://example.invalid/draft-image.png");
    await user.click(within(topbar).getByRole("button", { name: "返回剧集工作台" }));
    expect(container.querySelector(".fused-assets-page")).toBe(fusedPage);
    expect(customImageInput).toHaveValue("https://example.invalid/draft-image.png");

    await user.click(screen.getByRole("button", { name: "素材库" }));
    const library = await screen.findByRole("region", { name: "雾港来信 · 素材库" });
    const roleCard = await within(library).findByRole("article", { name: "林岚素材卡片" });
    await user.click(within(roleCard).getByRole("button", { name: "用作融合角色" }));
    const returnedFusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    expect(container.querySelector(".fused-assets-page")).toBe(fusedPage);
    expect(await within(returnedFusedRegion).findByText(/角色\/道具素材入口已准备/)).toBeInTheDocument();
    expect(within(returnedFusedRegion).getByLabelText("自定义图片地址")).toHaveValue("https://example.invalid/draft-image.png");
    await user.click(within(returnedFusedRegion).getByRole("button", { name: "应用来源到当前草稿" }));
    expect(within(returnedFusedRegion).queryByLabelText("当前分镜目标")).not.toBeInTheDocument();
    expect(within(returnedFusedRegion).getByLabelText("林岚")).toBeChecked();

    await user.click(within(topbar).getByRole("button", { name: "返回剧集工作台" }));
    await user.click(screen.getByRole("button", { name: "素材库" }));
    const propLibrary = await screen.findByRole("region", { name: "雾港来信 · 素材库" });
    await user.click(within(propLibrary).getByRole("button", { name: "道具" }));
    const propCard = await within(propLibrary).findByRole("article", { name: "黄铜钥匙素材卡片" });
    await user.click(within(propCard).getByRole("button", { name: "用作融合道具" }));
    const propFusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    await within(propFusedRegion).findByText(/角色\/道具素材入口已准备/);
    await user.click(within(propFusedRegion).getByRole("button", { name: "应用来源到当前草稿" }));
    expect(await within(propFusedRegion).findByText(/已切换到道具类型/)).toBeInTheDocument();
    const applyPropSource = within(propFusedRegion).getByRole("button", { name: "应用来源到当前草稿" });
    await waitFor(() => expect(applyPropSource).toBeEnabled());
    await user.click(applyPropSource);
    expect(within(propFusedRegion).queryByLabelText("当前分镜目标")).not.toBeInTheDocument();
    expect(within(propFusedRegion).getByLabelText("黄铜钥匙")).toBeChecked();
    expect(container.querySelector(".fused-assets-page")).toBe(fusedPage);

    const staleSelection = fusedSourceProbe.selection;
    const staleParentCallback = fusedSourceProbe.parentCallback;
    expect(staleSelection).not.toBeNull();
    expect(staleParentCallback).not.toBeNull();
    expect(staleSelection!.isCurrent()).toBe(false);
    const staleSelectionWithLateProof = { ...staleSelection!, isCurrent: () => true };
    act(() => staleParentCallback!(staleSelectionWithLateProof));
    expect(container.querySelector(".fused-assets-page")).toBe(fusedPage);
    expect(within(returnedFusedRegion).queryByText(/已回读来源|素材入口已准备/)).not.toBeInTheDocument();
  }, 20_000);

  it("opens a verified frame target with no linked members for manual fusion selection", async () => {
    const user = userEvent.setup();
    const services = fusedSourceWorkspaceServices();
    const listChapters = services.listChapters;
    services.listChapters = async (seriesId, signal) => (await listChapters(seriesId, signal)).map((chapter) => (
      chapter.id === `${seriesId}-chapter-opening`
        ? {
          ...chapter,
          content: chapter.content?.map((frame, index) => index === 0 ? { ...frame, character: [] } : frame) ?? null,
        }
        : chapter
    ));
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const { container } = renderWorkspace(services);

    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesCard = (await within(seriesRegion).findByRole("heading", { name: "雾港来信" })).closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const topbar = container.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    await user.click(within(topbar).getByRole("button", { name: "剧集工作台" }));

    const frameColumn = await screen.findByRole("region", { name: "镜头 1 的五类关联素材" });
    const frameFusionButton = within(frameColumn).getByRole("button", { name: "融合镜头角色" });
    await waitFor(() => expect(frameFusionButton).toBeEnabled());
    expect(within(frameColumn).getAllByText("当前镜头未关联素材，可进入后手动选择。")).toHaveLength(2);
    await user.click(frameFusionButton);

    const fusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    await within(fusedRegion).findByText(/已回读来源/);
    await user.click(within(fusedRegion).getByRole("button", { name: "应用来源到当前草稿" }));
    expect(within(fusedRegion).getByLabelText("当前分镜目标")).toHaveTextContent(`${demoSeries[0]!.id}-frame-opening`);
    const roleCheckbox = within(fusedRegion).getByLabelText("林岚");
    expect(roleCheckbox).not.toBeChecked();
    await user.click(roleCheckbox);
    expect(roleCheckbox).toBeChecked();
  }, 20_000);

  it("submits the actual frame proof from the Workspace source entry to the fused service", async () => {
    const user = userEvent.setup();
    const submittedInputs: FusedGenerationInput[] = [];
    const services = fusedSourceWorkspaceServices(async (input) => {
      submittedInputs.push(input);
      throw new Error("simulated response loss");
    });
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const { container } = renderWorkspace(services);

    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesCard = (await within(seriesRegion).findByRole("heading", { name: "雾港来信" })).closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const topbar = container.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    await user.click(within(topbar).getByRole("button", { name: "剧集工作台" }));

    const frameColumn = await screen.findByRole("region", { name: "镜头 1 的五类关联素材" });
    const frameFusionButton = within(frameColumn).getByRole("button", { name: "融合镜头角色" });
    await waitFor(() => expect(frameFusionButton).toBeEnabled());
    await user.click(frameFusionButton);
    const fusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    const fusedPage = container.querySelector(".fused-assets-page");
    expect(fusedPage).not.toBeNull();
    await within(fusedRegion).findByText(/已回读来源/);
    await user.click(within(fusedRegion).getByRole("button", { name: "应用来源到当前草稿" }));
    const submitButton = within(fusedRegion).getByRole("button", { name: "提交融合生成" });
    await waitFor(() => expect(submitButton).toBeEnabled());
    await user.click(submitButton);

    await waitFor(() => expect(submittedInputs).toHaveLength(1));
    expect(submittedInputs[0]).toMatchObject({
      series_id: demoSeries[0]!.id,
      member_type: "character",
      members: [{ kind: "asset", id: `${demoSeries[0]!.id}-character-linlan` }],
      chapter_id: `${demoSeries[0]!.id}-chapter-opening`,
      frame_index: 0,
      storyboard_asset_id: `${demoSeries[0]!.id}-frame-opening`,
      expected_frame: expect.objectContaining({
        storyboard: [`${demoSeries[0]!.id}-frame-opening`],
        character: [`${demoSeries[0]!.id}-character-linlan`],
      }),
    });
    expect(await within(fusedRegion).findByText("待核对")).toBeInTheDocument();

    await user.click(within(topbar).getByRole("button", { name: "返回剧集工作台" }));
    await user.click(within(topbar).getByRole("button", { name: "融合素材" }));
    const returnedFusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    expect(container.querySelector(".fused-assets-page")).toBe(fusedPage);
    expect(await within(returnedFusedRegion).findByText("待核对")).toBeInTheDocument();
    expect(submittedInputs).toHaveLength(1);
  }, 20_000);

  it("serializes the Workspace frame source proof through the fused HTTP adapter", async () => {
    const user = userEvent.setup();
    const demoServices = fusedSourceWorkspaceServices();
    await demoServices.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    window.localStorage.setItem(API_TOKEN_KEY, "workspace-fused-wire-token");
    window.localStorage.setItem(API_USER_KEY, JSON.stringify({ id: "demo-user" }));

    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({
      task_id: "workspace-wire-task",
      message_id: null,
    }));
    const apiServices = createApiServices("http://127.0.0.1:4175/api", {
      storage: window.localStorage,
      fetcher,
    });
    if (apiServices.fusedGeneration === undefined) throw new Error("Expected the fused HTTP service.");
    const services: WorkspaceServices = {
      ...demoServices,
      fusedGeneration: apiServices.fusedGeneration,
    };

    const { container } = renderWorkspace(services);
    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesCard = (await within(seriesRegion).findByRole("heading", { name: "雾港来信" })).closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const topbar = container.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    await user.click(within(topbar).getByRole("button", { name: "剧集工作台" }));

    const frameColumn = await screen.findByRole("region", { name: "镜头 1 的五类关联素材" });
    const frameFusionButton = within(frameColumn).getByRole("button", { name: "融合镜头角色" });
    await waitFor(() => expect(frameFusionButton).toBeEnabled());
    await user.click(frameFusionButton);
    const fusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    await within(fusedRegion).findByText(/已回读来源/);
    await user.click(within(fusedRegion).getByRole("button", { name: "应用来源到当前草稿" }));
    const submitButton = within(fusedRegion).getByRole("button", { name: "提交融合生成" });
    await waitFor(() => expect(submitButton).toBeEnabled());
    await user.click(submitButton);

    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    const [url, init] = fetcher.mock.calls[0]!;
    expect(String(url)).toBe("http://127.0.0.1:4175/api/fused-assets/generate");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer workspace-fused-wire-token");
    expect(new Headers(init?.headers).get("Idempotency-Key")).toEqual(expect.any(String));
    const serialized = JSON.parse(String(init?.body)) as Record<string, unknown>;
    expect(serialized).toMatchObject({
      series_id: demoSeries[0]!.id,
      member_type: "character",
      chapter_id: `${demoSeries[0]!.id}-chapter-opening`,
      frame_index: 0,
      storyboard_asset_id: `${demoSeries[0]!.id}-frame-opening`,
      expected_frame: {
        storyboard: [`${demoSeries[0]!.id}-frame-opening`],
        character: [`${demoSeries[0]!.id}-character-linlan`],
      },
    });
    expect(await within(fusedRegion).findByText("已受理")).toBeInTheDocument();
  }, 20_000);

  it("keeps a pending fused request frozen while a new frame source waits for explicit application", async () => {
    const user = userEvent.setup();
    const submittedInputs: FusedGenerationInput[] = [];
    const submittedKeys: string[] = [];
    let resolveSubmit!: (receipt: FusedGenerationReceipt) => void;
    const response = new Promise<FusedGenerationReceipt>((resolve) => { resolveSubmit = resolve; });
    const services = fusedSourceWorkspaceServices(async (input, _signal, options) => {
      submittedInputs.push(input);
      if (options?.idempotencyKey !== undefined) submittedKeys.push(options.idempotencyKey);
      return response;
    });
    await services.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const { container } = renderWorkspace(services);

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesCard = (await within(seriesRegion).findByRole("heading", { name: "雾港来信" })).closest("article");
    if (!(seriesCard instanceof HTMLElement)) throw new Error("The demo series card was not rendered.");
    await user.click(within(seriesCard).getByRole("button", { name: /只读查看章节/ }));
    const topbar = container.querySelector(".topbar");
    if (!(topbar instanceof HTMLElement)) throw new Error("The Workspace topbar was not rendered.");
    await user.click(within(topbar).getByRole("button", { name: "剧集工作台" }));

    const firstFrameColumn = await screen.findByRole("region", { name: "镜头 1 的五类关联素材" });
    const firstSourceButton = within(firstFrameColumn).getByRole("button", { name: "融合镜头角色" });
    await waitFor(() => expect(firstSourceButton).toBeEnabled());
    await user.click(firstSourceButton);
    const fusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    await within(fusedRegion).findByText(/已回读来源/);
    await user.click(within(fusedRegion).getByRole("button", { name: "应用来源到当前草稿" }));
    const originalTarget = within(fusedRegion).getByLabelText("当前分镜目标");
    expect(originalTarget).toHaveTextContent(`${demoSeries[0]!.id}-frame-opening`);

    await user.click(within(fusedRegion).getByRole("button", { name: "提交融合生成" }));
    await waitFor(() => expect(submittedInputs).toHaveLength(1));
    const originalRequest = submittedInputs[0];
    if (originalRequest === undefined) throw new Error("The original fused request was not captured.");
    const originalInput = JSON.parse(JSON.stringify(originalRequest)) as FusedGenerationInput;
    const originalKey = submittedKeys[0];
    if (originalKey === undefined) throw new Error("The original fused submission key was not captured.");
    expect(originalKey.length).toBeGreaterThan(0);
    expect(Object.isFrozen(originalRequest)).toBe(true);
    expect(await within(fusedRegion).findByRole("button", { name: "正在提交…" })).toBeDisabled();

    await user.click(within(topbar).getByRole("button", { name: "返回剧集工作台" }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(confirm).toHaveBeenCalledWith("融合写入或目录核对仍在进行。离开后原请求和本地任务记录会保留，服务器结果仍可能需要核对。仍要离开吗？");
    const secondChapterButton = await screen.findByRole("button", { name: /第二章 · 来信/ });
    await user.click(secondChapterButton);
    const nextFrameColumn = await screen.findByRole("region", { name: "镜头 1 的五类关联素材" });
    const nextSourceButton = within(nextFrameColumn).getByRole("button", { name: "融合镜头角色" });
    await waitFor(() => expect(nextSourceButton).toBeEnabled());
    await user.click(nextSourceButton);

    const returnedFusedRegion = await screen.findByRole("region", { name: "雾港来信 · 融合素材" });
    await within(returnedFusedRegion).findByText(/已回读来源/);
    expect(await within(returnedFusedRegion).findByText("提交中")).toBeInTheDocument();
    expect(within(returnedFusedRegion).getByRole("button", { name: "正在提交…" })).toBeDisabled();
    const applyNextSource = within(returnedFusedRegion).getByRole("button", { name: "应用来源到当前草稿" });
    expect(applyNextSource).toBeDisabled();
    expect(within(returnedFusedRegion).getByLabelText("当前分镜目标")).toHaveTextContent(`${demoSeries[0]!.id}-frame-opening`);
    expect(submittedInputs).toHaveLength(1);
    expect(submittedInputs[0]).toBe(originalRequest);
    expect(submittedInputs[0]).toEqual(originalInput);
    expect(submittedKeys).toEqual([originalKey]);

    confirm.mockReturnValue(false);
    await user.click(within(topbar).getByRole("button", { name: "返回剧集工作台" }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("region", { name: "雾港来信 · 融合素材" })).toBeInTheDocument();
    expect(within(returnedFusedRegion).getByText("提交中")).toBeInTheDocument();
    expect(submittedInputs).toHaveLength(1);
    expect(submittedInputs[0]).toBe(originalRequest);
    expect(submittedKeys).toEqual([originalKey]);

    await act(async () => {
      resolveSubmit({ taskId: "fused-pending-task", messageId: null });
      await response;
    });
    await waitFor(() => expect(applyNextSource).toBeEnabled());
    await user.click(applyNextSource);
    expect(await within(returnedFusedRegion).findByLabelText("当前分镜目标")).toHaveTextContent(`${demoSeries[0]!.id}-frame-letter-second`);
    expect(submittedInputs).toHaveLength(1);
    expect(submittedInputs[0]).toBe(originalRequest);
    expect(submittedInputs[0]).toEqual(originalInput);
    expect(submittedKeys).toEqual([originalKey]);
  }, 20_000);


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
    const sourceAssetLibrary = await screen.findByRole("region", { name: sourceSeries.name + " · 素材库" });
    await user.click(within(sourceAssetLibrary).getByRole("button", { name: "查看关联镜头：来源素材角色" }));

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

    const sourceChapterBrowser = await screen.findByRole("region", { name: sourceSeries.name });
    await user.click(within(sourceChapterBrowser).getByRole("button", { name: "返回剧集列表" }));
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
    const seriesRegion = await screen.findByRole("region", { name: "我的剧集" });
    const seriesFilters = within(seriesRegion).getByRole("group", { name: "剧集筛选" });
    await within(seriesRegion).findAllByRole("article");

    await user.click(within(seriesFilters).getByRole("button", { name: "团队剧集" }));
    await user.click(within(seriesFilters).getByRole("button", { name: /全部剧集/ }));
    await user.click(within(seriesRegion).getByRole("button", { name: /加载更多/ }));
    expect(within(seriesRegion).getByText("显示 24 / 24 部")).toBeInTheDocument();

    const firstCard = within(seriesRegion).getAllByRole("article")[0]!;
    await user.click(within(firstCard).getByRole("button", { name: /只读查看章节/ }));
    const chapterRegion = await screen.findByRole("region", { name: "雾港来信" });
    expect(await within(chapterRegion).findByRole("heading", { name: "第一章 · 雾起" }))
      .toBeInTheDocument();
    expect(within(chapterRegion).getByText(/当前由 周编剧 编辑/)).toBeInTheDocument();
    expect(within(chapterRegion).getByRole("button", { name: "重新读取章节" })).toBeInTheDocument();

    const hiddenList = container.querySelector(".series-list-view") as HTMLElement;
    expect(hiddenList.hidden).toBe(true);
    expect(hiddenList.hasAttribute("inert")).toBe(true);
    expect(screen.queryByRole("button", { name: "团队剧集" })).not.toBeInTheDocument();
    // jsdom does not simulate native focus movement when a focused subtree becomes hidden; the browser check covers it.

    const chapterList = within(chapterRegion).getByRole("complementary", { name: "章节列表" });
    await user.click(within(chapterList).getByRole("button", { name: /第二章/ }));
    expect(await within(chapterRegion).findByText(/她拆开信封/)).toBeInTheDocument();
    await user.click(within(chapterRegion).getByRole("button", { name: "返回剧集列表" }));

    const returnedList = await screen.findByRole("region", { name: "我的剧集" });
    expect(await within(returnedList).findByRole("heading", { name: "我的剧集" }))
      .toBeInTheDocument();
    expect(within(returnedList).getByText("显示 24 / 24 部")).toBeInTheDocument();
    const returnedFilters = within(returnedList).getByRole("group", { name: "剧集筛选" });
    expect(within(returnedFilters).getByRole("button", { name: /全部剧集/ }))
      .toHaveAttribute("aria-pressed", "true");
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
        const heading = await within(seriesList).findByText(name, { selector: "h3", exact: true });
        expect(heading).toBeVisible();
        expect(heading).toHaveAccessibleName(name);
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

      const firstChapterReturn = within(firstChapterRegion).getByText("返回剧集列表", { selector: "button" });
      expect(firstChapterReturn).toBeVisible();
      expect(firstChapterReturn).toHaveAccessibleName("返回剧集列表");
      await user.click(firstChapterReturn);
      seriesList = await screen.findByRole("region", { name: "我的剧集" });
      const secondCard = await getSeriesCard(secondSeries.name);
      await user.click(within(secondCard).getByRole("button", { name: /只读查看章节/ }));
      const otherSeriesChapterRegion = await screen.findByRole("region", { name: secondSeries.name });
      await user.click(within(otherSeriesChapterRegion).getByRole("button", { name: "查看我的制作记录" }));
      await waitFor(() => expect(pendingRequests).toHaveLength(1));

      const otherSeriesReturn = within(otherSeriesChapterRegion).getByText("返回剧集列表", { selector: "button" });
      expect(otherSeriesReturn).toBeVisible();
      expect(otherSeriesReturn).toHaveAccessibleName("返回剧集列表");
      await user.click(otherSeriesReturn);
      const exitButton = screen.getByText("退出", { selector: "button" });
      expect(exitButton).toBeVisible();
      expect(exitButton).toHaveAccessibleName("退出");
      await user.click(exitButton);
      const loginCard = await screen.findByRole("region", { name: "欢迎回来" });
      const accountInput = within(loginCard).getByLabelText("账号");
      const passwordInput = within(loginCard).getByLabelText("密码");
      await user.click(accountInput);
      await user.paste("second");
      await user.click(passwordInput);
      await user.paste("pass");
      await user.click(within(loginCard).getByRole("button", { name: "登录工作台" }));
      seriesList = await screen.findByRole("region", { name: "我的剧集" });
      const seriesFilterGroup = within(seriesList).getByRole("group", { name: "剧集筛选" });
      const teamFilter = within(seriesFilterGroup).getByRole("button", { name: "团队剧集" });
      expect(teamFilter).toBeVisible();
      expect(teamFilter).toHaveAccessibleName("团队剧集");
      await user.click(teamFilter);
      const allFilter = within(seriesFilterGroup).getByRole("button", { name: /全部剧集/ });
      expect(allFilter).toBeVisible();
      expect(allFilter).toHaveAccessibleName(/全部剧集/);
      await user.click(allFilter);
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

        const tasksRegion = screen.getByRole("region", { name: "我的任务" });
        const tasksReturn = within(tasksRegion).getByText("返回剧集列表", { selector: "button" });
        expect(tasksReturn).toBeVisible();
        expect(tasksReturn).toHaveAccessibleName("返回剧集列表");
        await user.click(tasksReturn);
        expect(await screen.findByRole("heading", { name: "我的剧集" })).toBeInTheDocument();
        expect(within(seriesList).getByText("显示 24 / 24 部")).toBeInTheDocument();
        expect(within(seriesFilterGroup).getByRole("button", { name: /全部剧集/ })).toHaveAttribute("aria-pressed", "true");
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

  it("shows the empty-list state and exposes series creation without opening unfinished production actions", async () => {
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
    expect(screen.getByRole("button", { name: "创建剧集" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^(认领|制作|生成)$/ })).not.toBeInTheDocument();
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
      const chapterBrowserRegion = await screen.findByRole("region", { name: series.name });

      if (category === "characters") {
        const sameChapterButtons = within(chapterBrowserRegion).getAllByRole("button", { name: /同名章节/ });
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
        await user.click(within(chapterBrowserRegion).getByRole("button", { name: "重新读取章节" }));
        await waitFor(() => expect(listChapters).toHaveBeenCalledTimes(3));
        expect(chapterNavigationProbe.intent).toBeNull();
        expect(chapterNavigationProbe.consumedEpochs).toEqual([acceptedEpoch]);
        expect(scrollProbe.targets).toHaveLength(0);
      }

      await user.click(within(chapterBrowserRegion).getByRole("button", { name: "返回剧集列表" }));
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

  it("routes into team management and preserves its draft when a shared-sidebar exit is cancelled", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(API_TOKEN_KEY, "synthetic-team-session");
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const createdTeam = {
      id: "team-created-through-api",
      name: "真实API团队",
      owner_id: "fixture-user",
      created_at: "2026-10-05T10:00:00",
    };
    let teamCreated = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/teams/my" && init?.method === "GET") {
        return jsonResponse(teamCreated ? [{ ...createdTeam, member_count: 1, my_role: "owner" }] : []);
      }
      if (url.pathname === "/api/teams" && init?.method === "POST") {
        teamCreated = true;
        return jsonResponse(createdTeam, 201);
      }
      if (url.pathname === `/api/teams/${createdTeam.id}` && init?.method === "GET") {
        return jsonResponse({
          ...createdTeam,
          members: [{
            user_id: "fixture-user",
            username: "演示创作者",
            avatar_url: null,
            role: "owner",
            permissions: [],
            joined_at: "2026-10-05T10:00:00",
          }],
          my_role: "owner",
          my_permissions: [],
        });
      }
      throw new Error(`Unexpected team API request: ${init?.method ?? "GET"} ${url.pathname}`);
    });
    const base = createDemoServices(window.localStorage);
    await base.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    const apiServices = createApiServices(apiBaseUrl, {
      storage: window.localStorage,
      fetcher,
    });
    if (apiServices.teamManagement === undefined) {
      throw new Error("API services did not expose the team management adapter.");
    }
    const services: WorkspaceServices = {
      ...base,
      mode: "api",
      apiBaseUrl,
      restore: async () => authenticatedFixtureUser(),
      teamManagement: apiServices.teamManagement,
    };
    renderWorkspace(services);
    await screen.findAllByRole("article");
    const nav = screen.getByRole("navigation", { name: "主要导航" });
    await user.click(within(nav).getByRole("button", { name: "我的团队" }));
    await screen.findByRole("heading", { name: "团队管理" });
    await screen.findByText("当前账号还没有加入团队。");
    expect(fetcher).toHaveBeenCalled();
    const nameInput = screen.getByLabelText("团队名称");
    await user.type(nameInput, "保留这份草稿");
    const blockedUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(blockedUnload);
    expect(blockedUnload.defaultPrevented).toBe(true);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    await user.click(within(nav).getByRole("button", { name: "我的任务" }));
    expect(screen.getByRole("heading", { name: "团队管理" })).toBeInTheDocument();
    expect(screen.getByLabelText("团队名称")).toHaveValue("保留这份草稿");
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    vi.mocked(window.confirm).mockReturnValue(true);
    await user.click(within(nav).getByRole("button", { name: "我的任务" }));
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    const allowedUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(allowedUnload);
    expect(allowedUnload.defaultPrevented).toBe(false);

    await user.click(within(nav).getByRole("button", { name: "我的团队" }));
    await screen.findByText("当前账号还没有加入团队。");
    await user.type(screen.getByLabelText("团队名称"), " 真实API团队 ");
    await user.click(screen.getByRole("button", { name: "创建团队" }));
    expect(await screen.findByRole("heading", { name: "真实API团队" })).toBeInTheDocument();
    expect(fetcher.mock.calls.map(([input]) => new URL(String(input)).pathname)).toEqual(
      expect.arrayContaining([
        "/api/teams/my",
        "/api/teams",
        `/api/teams/${createdTeam.id}`,
      ]),
    );
    const createRequest = fetcher.mock.calls.find(([input, init]) =>
      new URL(String(input)).pathname === "/api/teams" && init?.method === "POST",
    );
    expect(createRequest?.[1]?.method).toBe("POST");
    expect(new Headers(createRequest?.[1]?.headers).get("authorization")).toBe("Bearer synthetic-team-session");
    expect(JSON.parse(String(createRequest?.[1]?.body))).toEqual({ name: " 真实API团队 " });
  });

  it("protects the series-share selection across canceled navigation and restores its unload guard on return", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const series = {
      ...demoSeries[0]!,
      id: "restricted-owned-series",
      user_id: "fixture-user",
      team_id: null,
      team_name: null,
      can_enter: false,
    };
    let shareOutcomeUnknown = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/series/restricted-owned-series/share") {
        if (shareOutcomeUnknown) return jsonResponse({ detail: "提交后服务暂时不可用" }, 503);
        return jsonResponse({ message: "已分享到团队「当前团队」", team_id: "team-current", team_name: "当前团队" });
      }
      throw new Error(`Unexpected workspace API request: ${url.pathname}`);
    });
    window.localStorage.setItem(API_TOKEN_KEY, "series-share-workspace-session");
    window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticatedFixtureUser()));
    const base = createDemoServices(window.localStorage);
    const apiServices = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher });
    const services: WorkspaceServices = {
      ...base,
      ...apiServices,
      mode: "api",
      apiBaseUrl,
      restore: async () => authenticatedFixtureUser(),
      listSeries: async () => [series],
      listMyTeams: async () => [teamDirectoryItem("team-current", "当前团队")],
      listMyTasks: async () => myTaskPage([], 0),
    };
    renderWorkspace(services);

    await screen.findAllByRole("article");
    await user.click(screen.getByRole("button", { name: "团队共享：雾港来信" }));
    await user.selectOptions(await screen.findByLabelText("分享给团队"), "team-current");

    const dirtyUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirtyUnload);
    expect(dirtyUnload.defaultPrevented).toBe(true);

    vi.spyOn(window, "confirm").mockReturnValue(false);
    const navigation = screen.getByRole("navigation", { name: "主要导航" });
    await user.click(within(navigation).getByRole("button", { name: "我的任务" }));
    expect(screen.getByRole("dialog", { name: "分享到团队" })).toBeInTheDocument();
    expect(screen.getByLabelText("分享给团队")).toHaveValue("team-current");
    expect(fetcher).not.toHaveBeenCalled();

    vi.mocked(window.confirm).mockReturnValue(true);
    await user.click(within(navigation).getByRole("button", { name: "我的任务" }));
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    const allowedUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(allowedUnload);
    expect(allowedUnload.defaultPrevented).toBe(true);

    await user.click(within(navigation).getByRole("button", { name: "我的剧集" }));
    expect(await screen.findByRole("dialog", { name: "分享到团队" })).toBeInTheDocument();
    const restoredUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(restoredUnload);
    expect(restoredUnload.defaultPrevented).toBe(true);

    shareOutcomeUnknown = true;
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    expect(await screen.findByLabelText("待核对的团队共享操作")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);

    await user.click(within(navigation).getByRole("button", { name: "我的任务" }));
    expect(await screen.findByRole("heading", { name: "我的任务" })).toBeInTheDocument();
    const hiddenUnknownUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(hiddenUnknownUnload);
    expect(hiddenUnknownUnload.defaultPrevented).toBe(true);

    await user.click(within(navigation).getByRole("button", { name: "我的剧集" }));
    expect(await screen.findByLabelText("待核对的团队共享操作")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "读取当前剧集事实" }));
    expect(await screen.findByText("当前剧集列表显示：个人剧集。")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "明确放弃本地状态" }));

    const abandonedUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(abandonedUnload);
    expect(abandonedUnload.defaultPrevented).toBe(false);
    await user.click(screen.getByRole("button", { name: "关闭团队共享" }));
    expect(screen.queryByRole("dialog", { name: "分享到团队" })).not.toBeInTheDocument();
    const cleanUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(cleanUnload);
    expect(cleanUnload.defaultPrevented).toBe(false);
  });

  it("shares one pending and unknown write permit across the hidden series dialog and Teams workspace", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const series = {
      ...demoSeries[0]!,
      id: "permit-owned-series",
      user_id: "fixture-user",
      team_id: null,
      team_name: null,
      can_enter: false,
    };
    const createdTeam = {
      id: "permit-created-team",
      name: "许可团队",
      owner_id: "fixture-user",
      created_at: "2026-10-19T12:00:00",
    };
    const apiServices = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher: vi.fn() });
    const managementPort = apiServices.teamManagement as TeamManagementServicePort;
    const createTeam = vi.spyOn(managementPort, "createTeam").mockResolvedValue(createdTeam);
    vi.spyOn(managementPort, "listTeams").mockResolvedValue([]);
    vi.spyOn(managementPort, "getTeam").mockResolvedValue({
      ...createdTeam,
      my_role: "owner",
      my_permissions: [],
      members: [],
    });
    const seriesPort = apiServices.teamSeries as TeamSeriesServicePort;
    const share = deferred<Awaited<ReturnType<TeamSeriesServicePort["shareSeries"]>>>();
    const shareSeries = vi.spyOn(seriesPort, "shareSeries").mockReturnValue(share.promise);

    window.localStorage.setItem(API_TOKEN_KEY, "team-collaboration-permit-session");
    window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticatedFixtureUser()));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      ...apiServices,
      mode: "api",
      apiBaseUrl,
      restore: async () => authenticatedFixtureUser(),
      listSeries: async () => [series],
      listMyTeams: async () => [teamDirectoryItem("permit-target-team", "目标团队")],
    };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace(services);

    await screen.findByRole("button", { name: "团队共享：雾港来信" });
    await user.click(screen.getByRole("button", { name: "团队共享：雾港来信" }));
    await user.selectOptions(await screen.findByLabelText("分享给团队"), "permit-target-team");
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    expect(shareSeries).toHaveBeenCalledTimes(1);

    const pendingUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(pendingUnload);
    expect(pendingUnload.defaultPrevented).toBe(true);

    const navigation = screen.getByRole("navigation", { name: "主要导航" });
    await user.click(within(navigation).getByRole("button", { name: "我的团队" }));
    await screen.findByRole("heading", { name: "团队管理" });
    expect(screen.getByLabelText("团队协作写入限制")).toHaveTextContent("仍在请求中");
    await user.type(screen.getByLabelText("团队名称"), "放弃后新建");
    await user.click(screen.getByRole("button", { name: "创建团队" }));
    expect(createTeam).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("当前操作没有发送");

    await act(async () => {
      share.reject(new ApiError("http", "服务暂不可用", 503));
      await Promise.resolve();
    });
    expect(await screen.findByLabelText("团队协作写入限制")).toHaveTextContent("结果待核对");
    const unknownUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unknownUnload);
    expect(unknownUnload.defaultPrevented).toBe(true);
    await user.click(screen.getByRole("button", { name: "明确放弃本地状态" }));
    await waitFor(() => expect(screen.queryByLabelText("团队协作写入限制")).not.toBeInTheDocument());
    expect(shareSeries).toHaveBeenCalledTimes(1);

    expect(screen.getByLabelText("团队名称")).toHaveValue("放弃后新建");
    await user.click(screen.getByRole("button", { name: "创建团队" }));
    await screen.findByRole("heading", { name: "许可团队" });
    expect(createTeam).toHaveBeenCalledTimes(1);
  });

  it("preserves an unknown share on canceled close and releases only its lease on confirmed close", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const series = {
      ...demoSeries[0]!,
      id: "close-unknown-share-series",
      user_id: "fixture-user",
      team_id: null,
      team_name: null,
      can_enter: false,
    };
    let shareRequests = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      if (url.pathname === `/api/series/${series.id}/share` && init?.method === "POST") {
        shareRequests += 1;
        return shareRequests === 1
          ? jsonResponse({ detail: "提交后服务暂时不可用" }, 503)
          : jsonResponse({ message: "已分享到团队「关闭测试团队」", team_id: "close-target-team", team_name: "关闭测试团队" });
      }
      throw new Error(`Unexpected workspace API request: ${init?.method ?? "GET"} ${url.pathname}`);
    });
    window.localStorage.setItem(API_TOKEN_KEY, "close-unknown-share-session");
    window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticatedFixtureUser()));
    const apiServices = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher });
    const listSeries = vi.fn(async () => [series]);
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      ...apiServices,
      mode: "api",
      apiBaseUrl,
      restore: async () => authenticatedFixtureUser(),
      listSeries,
      listMyTeams: async () => [teamDirectoryItem("close-target-team", "关闭测试团队")],
    };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace(services);

    await user.click(await screen.findByRole("button", { name: "团队共享：雾港来信" }));
    await user.selectOptions(await screen.findByLabelText("分享给团队"), "close-target-team");
    await user.click(screen.getByLabelText("同时由我认领"));
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    expect(await screen.findByLabelText("待核对的团队共享操作")).toBeInTheDocument();
    expect(shareRequests).toBe(1);

    await user.click(screen.getByRole("button", { name: "读取当前剧集事实" }));
    expect(await screen.findByText("当前剧集列表显示：个人剧集。")).toBeInTheDocument();
    vi.mocked(window.confirm).mockReturnValue(false);
    await user.click(screen.getByRole("button", { name: "关闭团队共享" }));
    expect(screen.getByLabelText("分享给团队")).toHaveValue("close-target-team");
    expect(screen.getByLabelText("同时由我认领")).toBeChecked();
    expect(screen.getByLabelText("待核对的团队共享操作")).toBeInTheDocument();
    expect(screen.getByText("当前剧集列表显示：个人剧集。")).toBeInTheDocument();
    const canceledCloseUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(canceledCloseUnload);
    expect(canceledCloseUnload.defaultPrevented).toBe(true);
    expect(shareRequests).toBe(1);

    vi.mocked(window.confirm).mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: "关闭团队共享" }));
    expect(screen.queryByRole("dialog", { name: "分享到团队" })).not.toBeInTheDocument();
    const confirmedCloseUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(confirmedCloseUnload);
    expect(confirmedCloseUnload.defaultPrevented).toBe(false);
    expect(shareRequests).toBe(1);

    await user.click(screen.getByRole("button", { name: "团队共享：雾港来信" }));
    await user.selectOptions(await screen.findByLabelText("分享给团队"), "close-target-team");
    expect(screen.getByLabelText("同时由我认领")).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    expect(await screen.findByText(/已分享到团队「关闭测试团队」/)).toBeInTheDocument();
    expect(shareRequests).toBe(2);
    expect(listSeries).toHaveBeenCalled();
  });

  it.each([
    ["success", "ack"] as const,
    ["401", 401] as const,
    ["403", 403] as const,
  ])("retires a share guard when teamSeries is removed and ignores a late %s receipt", async (label, outcome) => {
    const user = userEvent.setup();
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const series = {
      ...demoSeries[0]!,
      id: `removed-port-${label}-series`,
      user_id: "fixture-user",
      team_id: null,
      team_name: null,
      can_enter: false,
    };
    const apiServices = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher: vi.fn() });
    if (apiServices.teamSeries === undefined) throw new Error("API services did not expose the team series port.");
    const originalPort = apiServices.teamSeries;
    const request = deferred<Awaited<ReturnType<TeamSeriesServicePort["shareSeries"]>>>();
    vi.spyOn(originalPort, "shareSeries").mockReturnValue(request.promise);
    const portRef: { current: TeamSeriesServicePort | undefined } = { current: originalPort };
    const listSeries = vi.fn(async () => [series]);
    const services = {
      ...createDemoServices(window.localStorage),
      ...apiServices,
      mode: "api" as const,
      apiBaseUrl,
      restore: async () => authenticatedFixtureUser(),
      listSeries,
      listMyTeams: async () => [teamDirectoryItem("removed-port-team", "端口切换团队")],
    } satisfies WorkspaceServices;
    Object.defineProperty(services, "teamSeries", { enumerable: true, get: () => portRef.current });
    window.localStorage.setItem(API_TOKEN_KEY, `removed-port-${label}-session`);
    window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticatedFixtureUser()));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const view = renderWorkspace(services);

    await user.click(await screen.findByRole("button", { name: "团队共享：雾港来信" }));
    await user.selectOptions(await screen.findByLabelText("分享给团队"), "removed-port-team");
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    expect(await screen.findByText("正在执行：分享剧集")).toBeInTheDocument();
    const pendingUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(pendingUnload);
    expect(pendingUnload.defaultPrevented).toBe(true);

    portRef.current = undefined;
    view.rerender(<AuthProvider services={services}><Workspace /></AuthProvider>);
    expect(screen.queryByRole("dialog", { name: "分享到团队" })).not.toBeInTheDocument();
    const retiredUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(retiredUnload);
    expect(retiredUnload.defaultPrevented).toBe(false);

    portRef.current = { ...originalPort, shareSeries: vi.fn(originalPort.shareSeries) };
    view.rerender(<AuthProvider services={services}><Workspace /></AuthProvider>);
    await user.selectOptions(await screen.findByLabelText("分享给团队"), "removed-port-team");
    const newScopeUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(newScopeUnload);
    expect(newScopeUnload.defaultPrevented).toBe(true);
    const refreshCount = listSeries.mock.calls.length;

    await act(async () => {
      if (outcome === "ack") {
        request.resolve({ message: "迟到的旧 ACK", team_id: "removed-port-team", team_name: "端口切换团队" });
      } else {
        request.reject(new ApiError("http", `late ${outcome}`, outcome));
      }
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByLabelText("分享给团队")).toHaveValue("removed-port-team");
    expect(within(screen.getByRole("dialog", { name: "分享到团队" })).queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText("迟到的旧 ACK")).not.toBeInTheDocument();
    expect(originalPort.shareSeries).toHaveBeenCalledTimes(1);
    expect(listSeries).toHaveBeenCalledTimes(refreshCount);
    expect(screen.getByRole("button", { name: "退出" })).toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe(`removed-port-${label}-session`);
    const newScopeStillDirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(newScopeStillDirty);
    expect(newScopeStillDirty.defaultPrevented).toBe(true);
  });

  it("refreshes the visible team-series page after a hidden share ACK without accepting an old revision read", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const team = {
      id: "revision-team",
      name: "版本团队",
      owner_id: "fixture-user",
      created_at: "2026-10-19T12:00:00",
    };
    const teamDetail: TeamDetail = {
      ...team,
      my_role: "owner",
      my_permissions: [],
      members: [
        { user_id: "fixture-user", username: "团队作者", avatar_url: null, role: "owner", permissions: [], joined_at: team.created_at },
        { user_id: "revision-target", username: "目标成员", avatar_url: null, role: "member", permissions: [], joined_at: team.created_at },
      ],
    };
    const makeRow = (id: string, name: string): TeamSeriesRecord => ({
      id,
      name,
      description: null,
      image_url: null,
      user_id: "fixture-user",
      owner_name: "团队作者",
      owner_avatar_url: null,
      claimed_by: null,
      claimed_by_username: "",
      claimed_by_avatar_url: null,
      created_at: team.created_at,
      updated_at: team.created_at,
      chapter_count: 0,
      asset_counts: { characters: 0, scenes: 0, props: 0, storyboard: 0 },
    });
    const page = (items: TeamSeriesRecord[], pageNumber: number, total: number): TeamSeriesPage => ({
      items,
      total,
      page: pageNumber,
      page_size: 10,
      total_pages: 2,
    });
    const firstPage = page(Array.from({ length: 10 }, (_, index) => makeRow(`revision-${index + 1}`, `第${index + 1}部`)), 1, 11);
    const oldPage = page([makeRow("revision-11", "刷新前剧集")], 2, 11);
    const revisionPage = deferred<TeamSeriesPage>();
    const stalePage = deferred<TeamSeriesPage>();
    let listCall = 0;
    const apiServices = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher: vi.fn() });
    const managementPort = apiServices.teamManagement as TeamManagementServicePort;
    vi.spyOn(managementPort, "listTeams").mockResolvedValue([{
      ...team,
      member_count: 2,
      my_role: "owner",
    }]);
    vi.spyOn(managementPort, "getTeam").mockResolvedValue(teamDetail);
    const seriesPort = apiServices.teamSeries as TeamSeriesServicePort;
    const listTeamSeries = vi.spyOn(seriesPort, "listTeamSeries").mockImplementation(() => {
      listCall += 1;
      if (listCall === 1) return Promise.resolve(firstPage);
      if (listCall === 2) return Promise.resolve(oldPage);
      if (listCall === 3) return stalePage.promise;
      if (listCall === 4) return revisionPage.promise;
      throw new Error(`Unexpected team-series read ${listCall}`);
    });
    const shareAck = deferred<Awaited<ReturnType<TeamSeriesServicePort["shareSeries"]>>>();
    const shareSeries = vi.spyOn(seriesPort, "shareSeries").mockReturnValue(shareAck.promise);
    const series = {
      ...demoSeries[0]!,
      id: "revision-owned-series",
      name: "待共享剧集",
      user_id: "fixture-user",
      team_id: null,
      team_name: null,
      can_enter: false,
    };
    window.localStorage.setItem(API_TOKEN_KEY, "team-series-revision-session");
    window.localStorage.setItem(API_USER_KEY, JSON.stringify(authenticatedFixtureUser()));
    const services: WorkspaceServices = {
      ...createDemoServices(window.localStorage),
      ...apiServices,
      mode: "api",
      apiBaseUrl,
      restore: async () => authenticatedFixtureUser(),
      listSeries: async () => [series],
      listMyTeams: async () => [teamDirectoryItem(team.id, team.name)],
      listSeriesStylePrompts: async () => [],
    };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace(services);

    await screen.findByRole("button", { name: "团队共享：待共享剧集" });
    await user.click(screen.getByRole("button", { name: "团队共享：待共享剧集" }));
    await user.selectOptions(await screen.findByLabelText("分享给团队"), team.id);
    await user.click(screen.getByRole("button", { name: "分享剧集" }));
    expect(shareSeries).toHaveBeenCalledTimes(1);
    const navigation = screen.getByRole("navigation", { name: "主要导航" });
    await user.click(within(navigation).getByRole("button", { name: "我的团队" }));
    await screen.findByRole("heading", { name: "团队管理" });
    await user.click(await screen.findByRole("button", { name: /版本团队/ }));
    await screen.findByRole("heading", { name: "版本团队" });
    await user.click(screen.getByRole("tab", { name: "团队剧集" }));
    await screen.findByText("第 1 / 2 页 · 共 11 部");
    expect(listTeamSeries).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("刷新前剧集");
    expect(listTeamSeries).toHaveBeenCalledTimes(2);

    await user.click(screen.getByRole("button", { name: "刷新列表" }));
    await waitFor(() => expect(listTeamSeries).toHaveBeenCalledTimes(3));
    expect(listTeamSeries.mock.calls[2]?.[1]).toBe(2);
    await act(async () => {
      shareAck.resolve({ message: "已分享到团队「版本团队」", team_id: team.id, team_name: team.name });
      await Promise.resolve();
    });
    await waitFor(() => expect(listTeamSeries).toHaveBeenCalledTimes(4));
    expect(listTeamSeries.mock.calls[3]?.[1]).toBe(2);

    await user.type(screen.getByLabelText("名称"), "保留创建草稿");
    await user.selectOptions(screen.getByLabelText("转交给成员"), "revision-target");
    await act(async () => {
      revisionPage.resolve(page([makeRow("revision-11", "分享后新剧集")], 2, 11));
      await Promise.resolve();
    });
    await screen.findByText("分享后新剧集");
    expect(screen.getByText("第 2 / 2 页 · 共 11 部")).toBeInTheDocument();
    expect(screen.getByLabelText("名称")).toHaveValue("保留创建草稿");
    expect(screen.getByLabelText("转交给成员")).toHaveValue("revision-target");

    await act(async () => {
      stalePage.resolve(page([makeRow("revision-11", "旧 revision 迟到结果")], 2, 11));
      await Promise.resolve();
    });
    expect(screen.getByText("分享后新剧集")).toBeInTheDocument();
    expect(screen.queryByText("旧 revision 迟到结果")).not.toBeInTheDocument();
  });

  it("ignores a late team-invite 401 after a view switch but logs out for the current invite view", async () => {
    const user = userEvent.setup();
    const apiBaseUrl = "http://127.0.0.1:4175/api";
    const team = {
      id: "host-team",
      name: "宿主团队",
      owner_id: "fixture-user",
      created_at: "2026-10-05T10:00:00",
    };
    const teamDetail = {
      ...team,
      members: [
        {
          user_id: "fixture-user",
          username: "演示创作者",
          avatar_url: null,
          role: "owner",
          permissions: [],
          joined_at: "2026-10-05T10:00:00",
        },
        {
          user_id: "member-1",
          username: "合成成员",
          avatar_url: null,
          role: "member",
          permissions: [],
          joined_at: "2026-10-05T10:00:00",
        },
      ],
      my_role: "owner",
      my_permissions: [],
    };
    const firstInviteResponse = deferred<Response>();
    let inviteReadCount = 0;
    const inviteSignals: AbortSignal[] = [];
    const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      if (url.pathname === "/api/teams/my" && init?.method === "GET") {
        return jsonResponse([{ ...team, member_count: 2, my_role: "owner" }]);
      }
      if (url.pathname === `/api/teams/${team.id}` && init?.method === "GET") {
        return jsonResponse(teamDetail);
      }
      if (url.pathname === `/api/teams/${team.id}/invites` && init?.method === "GET") {
        inviteReadCount += 1;
        if (inviteReadCount === 1) {
          inviteSignals.push(init.signal as AbortSignal);
          return firstInviteResponse.promise;
        }
        return jsonResponse({ detail: "合成401" }, 401);
      }
      throw new Error(`Unexpected team API request: ${init?.method ?? "GET"} ${url.pathname}`);
    });
    const base = createDemoServices(window.localStorage);
    await base.login({ username: "demo", password: "demo123" }, new AbortController().signal);
    window.localStorage.setItem(API_TOKEN_KEY, "synthetic-host-session");
    const apiServices = createApiServices(apiBaseUrl, {
      storage: window.localStorage,
      fetcher,
    });
    const services: WorkspaceServices = {
      ...base,
      ...apiServices,
      restore: async () => authenticatedFixtureUser(),
      listSeries: base.listSeries,
      listMyTeams: base.listMyTeams,
      listMyTasks: base.listMyTasks,
    };
    renderWorkspace(services);
    await screen.findAllByRole("article");
    const navigation = screen.getByRole("navigation", { name: "主要导航" });
    await user.click(within(navigation).getByRole("button", { name: "我的团队" }));
    const teamDirectory = await screen.findByRole("region", { name: "我的团队目录" });
    await user.click(await within(teamDirectory).findByRole("button", { name: /宿主团队/ }));
    await screen.findByRole("heading", { name: "宿主团队" });
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    const firstInvitePanel = await screen.findByRole("tabpanel", { name: "邀请码管理" });
    await user.click(within(firstInvitePanel).getByRole("button", { name: "读取邀请码" }));
    await waitFor(() => expect(inviteReadCount).toBe(1));

    await user.click(screen.getByRole("tab", { name: "成员与权限" }));
    await user.selectOptions(screen.getByLabelText("角色"), "admin");
    const dirtyUnload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirtyUnload);
    expect(dirtyUnload.defaultPrevented).toBe(true);
    expect(inviteSignals[0]?.aborted).toBe(true);

    await act(async () => {
      firstInviteResponse.resolve(jsonResponse({ detail: "old session" }, 401));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByRole("heading", { name: "团队管理" })).toBeInTheDocument();
    expect(screen.getByLabelText("角色")).toHaveValue("admin");
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBe("synthetic-host-session");

    vi.spyOn(window, "confirm").mockReturnValue(true);
    await user.click(screen.getByRole("tab", { name: "邀请码" }));
    const secondInvitePanel = await screen.findByRole("tabpanel", { name: "邀请码管理" });
    await user.click(within(secondInvitePanel).getByRole("button", { name: "读取邀请码" }));
    expect(await screen.findByRole("button", { name: "登录工作台" })).toBeInTheDocument();
    expect(window.localStorage.getItem(API_TOKEN_KEY)).toBeNull();
    expect(inviteReadCount).toBe(2);
  });
});
