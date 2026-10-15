import { demoSeries } from "../../features/series/demoSeries";
import { demoTeams } from "../../features/series/demoTeams";
import { getDemoChapterData, type DemoChapterData } from "../../features/chapters/demoChapters";
import { getDemoAssetLibraryData, type DemoAssetLibraryData } from "../../features/assets/demoAssets";
import { getDemoPersonalProductionNotes } from "../../features/chapters/demoPersonalProductionNotes";
import { clonePersonalRoughCutSnapshot, getDemoPersonalRoughCut } from "../../features/chapters/demoPersonalRoughCut";
import { ApiConfigurationError, readEnvironmentConfiguration, resolveApiBaseUrl, type AppMode } from "./config";
import { ApiError, isAbortError } from "./errors";
import {
  parseCreatedCharacter,
  parseCreatedProp,
  parseCreatedScene,
  parseUpdatedCharacter,
  parseUpdatedProp,
  parseUpdatedScene,
  type CharacterCreateInput,
  type CharacterUpdateInput,
  type PropCreateInput,
  type PropUpdateInput,
  type SceneCreateInput,
  type SceneUpdateInput,
} from "./assetManagement";
import {
  InvalidResponseError,
  parseChapterEditLockState,
  parseChapterList,
  parseChapterOrderResponse,
  parseChapterResponse,
  parseCharacterList,
  parseErrorDetail,
  parseLoginResponse,
  parsePropList,
  parseSeries,
  parseSeriesList,
  parseSceneList,
  parseStoryboardAssetList,
  parseUser,
  type Character,
  type Chapter,
  type ChapterEditLockAcquisition,
  type ChapterEditLockLeaseHandle,
  type ChapterEditLockState,
  type CreateChapterInput,
  type ReorderChaptersInput,
  type Credentials,
  type Prop,
  type Scene,
  type Series,
  type StoryboardAsset,
  type StoryboardFrame,
  type UpdateChapterInput,
  type User,
} from "./contracts";
import {
  parsePersonalProductionNoteUpdate,
  parsePersonalProductionResumeUpdate,
  parsePersonalProductionSnapshot,
  type PersonalProductionNoteUpdate,
  type PersonalProductionResumeUpdate,
  type PersonalProductionSnapshot,
} from "./personalProductionNotes";
import {
  parsePersonalRoughCutSaveResponse,
  parsePersonalRoughCutSnapshot,
  parsePersonalRoughCutUpdate,
  type PersonalRoughCutSnapshot,
  type PersonalRoughCutUpdate,
} from "./personalRoughCut";
import { inspectRoughCutSource } from "../../features/chapters/personal-production/roughCutEdit";
import { demoTaskRecords, getDemoMyTaskPage } from "../../features/tasks/demoTasks";
import { MY_TASK_PAGE_SIZE, parseMyTaskPage, type MyTaskPage } from "./myTasks";
import { parseTaskReceipt, parseTaskRequestRecord, type TaskReceipt, type TaskRequestRecord } from "./taskDetails";
import { parseMyTeamList, type MyTeam } from "./teams";
import {
  parseStoryboardModels,
  parseStoryboardPrompts,
  parseTwoPassQuote,
  parseTwoPassSubmission,
  parseTwoPassTask,
  type StoryboardGenerationServicePort,
  type TwoPassQuoteInput,
} from "../../features/storyboard-generation/contracts";
import { parseTwoPassSse } from "../../features/storyboard-generation/sse";
import {
  parseImportSeriesResponse,
  type ImportSeriesInput,
  type ImportSeriesResponse,
} from "./seriesExchange";
import {
  parseStylePromptChoices,
  parseUploadedSeriesCover,
  type CreateSeriesInput,
  type StylePromptChoice,
  type UpdateSeriesInput,
} from "./seriesManagement";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  browserStorage,
  clearApiSession,
  clearMockSession,
  MOCK_SESSION_KEY,
  MOCK_USER_KEY,
  type StorageLike,
} from "./storage";

export interface WorkspaceServices {
  readonly mode: AppMode;
  readonly apiBaseUrl: string | null;
  restore(signal: AbortSignal): Promise<User | null>;
  login(credentials: Credentials, signal: AbortSignal): Promise<User>;
  listSeries(signal: AbortSignal): Promise<Series[]>;
  getSeries?(seriesId: string, signal: AbortSignal): Promise<Series>;
  createSeries?(input: CreateSeriesInput, signal: AbortSignal): Promise<Series>;
  updateSeries?(seriesId: string, update: UpdateSeriesInput, signal: AbortSignal): Promise<Series>;
  deleteSeries?(seriesId: string, signal: AbortSignal): Promise<void>;
  listSeriesStylePrompts?(signal: AbortSignal): Promise<StylePromptChoice[]>;
  uploadSeriesCover?(file: File, signal: AbortSignal): Promise<string>;
  listMyTeams(signal: AbortSignal): Promise<MyTeam[]>;
  listChapters(seriesId: string, signal: AbortSignal): Promise<Chapter[]>;
  createChapter?(seriesId: string, input: CreateChapterInput, signal: AbortSignal): Promise<Chapter>;
  reorderChapters?(seriesId: string, input: ReorderChaptersInput, signal: AbortSignal): Promise<{ message: string }>;
  getChapterEditLock?(
    chapterId: string,
    signal: AbortSignal,
    lease?: ChapterEditLockLeaseHandle,
  ): Promise<ChapterEditLockState>;
  acquireChapterEditLock?(chapterId: string, signal: AbortSignal): Promise<ChapterEditLockAcquisition>;
  releaseChapterEditLock?(
    chapterId: string,
    signal: AbortSignal,
    lease: ChapterEditLockLeaseHandle,
  ): Promise<{ message: string }>;
  updateChapter?(
    seriesId: string,
    chapterId: string,
    update: UpdateChapterInput,
    signal: AbortSignal,
  ): Promise<Chapter>;
  deleteChapter?(seriesId: string, chapterId: string, signal: AbortSignal): Promise<void>;
  listStoryboardAssets(
    seriesId: string,
    chapterId: string,
    signal: AbortSignal,
  ): Promise<StoryboardAsset[]>;
  listCharacters(seriesId: string, signal: AbortSignal): Promise<Character[]>;
  listScenes(seriesId: string, signal: AbortSignal): Promise<Scene[]>;
  listProps(seriesId: string, signal: AbortSignal): Promise<Prop[]>;
  createCharacter?(seriesId: string, input: CharacterCreateInput, signal: AbortSignal): Promise<Character>;
  updateCharacter?(seriesId: string, assetId: string, update: CharacterUpdateInput, signal: AbortSignal): Promise<Character>;
  deleteCharacter?(assetId: string, signal: AbortSignal): Promise<void>;
  createScene?(seriesId: string, input: SceneCreateInput, signal: AbortSignal): Promise<Scene>;
  updateScene?(seriesId: string, assetId: string, update: SceneUpdateInput, signal: AbortSignal): Promise<Scene>;
  deleteScene?(assetId: string, signal: AbortSignal): Promise<void>;
  createProp?(seriesId: string, input: PropCreateInput, signal: AbortSignal): Promise<Prop>;
  updateProp?(seriesId: string, assetId: string, update: PropUpdateInput, signal: AbortSignal): Promise<Prop>;
  deleteProp?(assetId: string, signal: AbortSignal): Promise<void>;
  getPersonalProductionNotes(chapterId: string, signal: AbortSignal): Promise<PersonalProductionSnapshot>;
  savePersonalProductionNote?(
    chapterId: string,
    update: PersonalProductionNoteUpdate,
    signal: AbortSignal,
  ): Promise<PersonalProductionSnapshot>;
  savePersonalProductionResume?(
    chapterId: string,
    update: PersonalProductionResumeUpdate,
    signal: AbortSignal,
  ): Promise<PersonalProductionSnapshot>;
  getPersonalRoughCut(chapterId: string, signal: AbortSignal): Promise<PersonalRoughCutSnapshot>;
  savePersonalRoughCut?(
    chapterId: string,
    update: PersonalRoughCutUpdate,
    signal: AbortSignal,
  ): Promise<PersonalRoughCutSnapshot>;
  listMyTasks(page: number, signal: AbortSignal): Promise<MyTaskPage>;
  getTaskReceipt?(taskId: string, signal: AbortSignal): Promise<TaskReceipt>;
  getTaskRequest?(taskId: string, signal: AbortSignal): Promise<TaskRequestRecord>;
  readonly storyboardGeneration?: StoryboardGenerationServicePort;
  exportSeries?(seriesId: string, signal: AbortSignal): Promise<unknown>;
  importSeries?(input: ImportSeriesInput, signal: AbortSignal): Promise<ImportSeriesResponse>;
  logout(): void;
}

export interface ServiceOptions {
  storage?: StorageLike;
  fetcher?: typeof fetch;
  timeoutMs?: number;
}

const demoUser: User = {
  id: "demo-user",
  username: "演示创作者",
  email: "demo@example.invalid",
  is_superuser: false,
  membership_type: "demo",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00",
};

function readStoredDemoUser(storage: StorageLike): User | null {
  if (storage.getItem(MOCK_SESSION_KEY) !== "demo-session") {
    return null;
  }
  const serialized = storage.getItem(MOCK_USER_KEY);
  if (serialized === null) {
    clearMockSession(storage);
    return null;
  }
  try {
    const value: unknown = JSON.parse(serialized);
    const user = parseUser(value);
    return user.id === demoUser.id ? user : null;
  } catch {
    clearMockSession(storage);
    return null;
  }
}

function ensureDemoSession(storage: StorageLike): void {
  if (readStoredDemoUser(storage) === null) {
    throw new ApiError("http", "演示会话已失效，请重新登录。", 401);
  }
}

function demoNotFound(): ApiError {
  return new ApiError("http", "当前内容不存在。", 404);
}

const demoSeriesStyles: StylePromptChoice[] = [
  { id: "demo-style-urban", name: "都市悬疑", is_system: true, user_id: null },
  { id: "demo-style-fantasy", name: "奇幻冒险", is_system: false, user_id: demoUser.id },
];

function requireApiToken(storage: StorageLike): string {
  const token = storage.getItem(API_TOKEN_KEY);
  if (!token) {
    throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
  }
  return token;
}

function ensureCurrentApiSession(storage: StorageLike, token: string, signal: AbortSignal): void {
  if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
    throw new ApiError("aborted", "Request was cancelled.");
  }
}

function cloneDemoNoteValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(cloneDemoNoteValue);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, cloneDemoNoteValue(entry)]));
  }
  return value;
}

function cloneDemoChapter(chapter: Chapter): Chapter {
  return {
    ...chapter,
    content: chapter.content?.map((frame) => cloneDemoNoteValue(frame) as StoryboardFrame) ?? null,
    lock: chapter.lock === null ? null : { ...chapter.lock },
  };
}

function cloneDemoChapterData(data: DemoChapterData): DemoChapterData {
  return {
    chapters: data.chapters.map(cloneDemoChapter),
    assetsByChapter: Object.fromEntries(Object.entries(data.assetsByChapter).map(([chapterId, assets]) => [
      chapterId,
      assets.map((asset) => ({ ...asset })),
    ])),
  };
}

function removeDemoChapterAssetReferences(
  data: DemoChapterData | undefined,
  category: "characters" | "scenes" | "props",
  assetId: string,
): void {
  if (data === undefined) {
    return;
  }
  const referenceField = category === "characters" ? "character" : category === "scenes" ? "scene" : "prop";
  for (const chapter of data.chapters) {
    if (chapter.content === null) {
      continue;
    }
    let changed = false;
    chapter.content = chapter.content.map((frame) => {
      const references = frame[referenceField];
      if (!Array.isArray(references) || !references.includes(assetId)) {
        return frame;
      }
      changed = true;
      return { ...frame, [referenceField]: references.filter((reference) => reference !== assetId) };
    });
    if (changed) {
      chapter.updated_at = new Date().toISOString();
    }
  }
}

function parseDemoTaskRequestData(value: string | null): unknown {
  if (value === null) {
    return null;
  }
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return { _raw: value };
  }
}

function cloneDemoAssetData(data: DemoAssetLibraryData): DemoAssetLibraryData {
  return {
    characters: data.characters.map((asset) => ({
      ...asset,
      aliases: asset.aliases === null ? null : [...asset.aliases],
    })),
    scenes: data.scenes.map((asset) => ({
      ...asset,
      aliases: asset.aliases === null ? null : [...asset.aliases],
    })),
    props: data.props.map((asset) => ({
      ...asset,
      aliases: asset.aliases === null ? null : [...asset.aliases],
    })),
  };
}

function definedValues<T extends object>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== undefined)) as Partial<T>;
}

function findDemoAssetRow(
  dataBySeries: Map<string, DemoAssetLibraryData>,
  category: "characters",
  assetId: string,
): { rows: Character[]; index: number; asset: Character } | null;
function findDemoAssetRow(
  dataBySeries: Map<string, DemoAssetLibraryData>,
  category: "scenes",
  assetId: string,
): { rows: Scene[]; index: number; asset: Scene } | null;
function findDemoAssetRow(
  dataBySeries: Map<string, DemoAssetLibraryData>,
  category: "props",
  assetId: string,
): { rows: Prop[]; index: number; asset: Prop } | null;
function findDemoAssetRow(
  dataBySeries: Map<string, DemoAssetLibraryData>,
  category: "characters" | "scenes" | "props",
  assetId: string,
): { rows: Character[] | Scene[] | Prop[]; index: number; asset: Character | Scene | Prop } | null {
  for (const data of dataBySeries.values()) {
    const rows = data[category];
    const index = rows.findIndex((asset) => asset.id === assetId);
    const asset = rows[index];
    if (asset !== undefined) {
      return { rows, index, asset } as {
        rows: Character[] | Scene[] | Prop[];
        index: number;
        asset: Character | Scene | Prop;
      };
    }
  }
  return null;
}

function cloneDemoPersonalProductionSnapshot(
  snapshot: PersonalProductionSnapshot,
): PersonalProductionSnapshot {
  return {
    ...snapshot,
    frames: snapshot.frames.map((frame) => ({ ...frame })),
    frame_notes: new Map([...snapshot.frame_notes.entries()].map(([id, value]) => [
      id,
      cloneDemoNoteValue(value),
    ])),
  };
}

export function createDemoServices(storage: StorageLike = browserStorage()): WorkspaceServices {
  const seriesRows = demoSeries.map((series) => ({ ...series }));
  const customSeriesIds = new Set<string>();
  const assetDataBySeries = new Map<string, DemoAssetLibraryData>();
  for (const series of seriesRows) {
    const data = getDemoAssetLibraryData(series.id);
    if (data !== null) {
      assetDataBySeries.set(series.id, cloneDemoAssetData(data));
    }
  }
  let nextSeriesId = 1;
  let nextChapterId = 1;
  const chapterDataBySeries = new Map<string, DemoChapterData>();
  for (const series of demoSeries) {
    const seed = getDemoChapterData(series.id);
    if (seed !== null) {
      chapterDataBySeries.set(series.id, cloneDemoChapterData(seed));
    }
  }
  const personalNoteSnapshots = new Map<string, PersonalProductionSnapshot>();
  const personalRoughCutSnapshots = new Map<string, PersonalRoughCutSnapshot>();
  const demoChapterLocks = new Map<string, { userId: string; username: string; acquiredAt: string; lastActiveAt: string; expiresAt: string }>();
  const demoChapterLockLeases = new Map<ChapterEditLockLeaseHandle, {
    chapterId: string;
    session: string | null;
    acquiredAt: string | null;
    expiresAt: string | null;
    userId: string | null;
  }>();
  let nextDemoStoryboardAssetId = 1;
  let nextDemoManagedAssetId = 1;

  function findDemoAssetData(seriesId: string): DemoAssetLibraryData | null {
    const existing = assetDataBySeries.get(seriesId);
    if (existing !== undefined) {
      return existing;
    }
    if (!customSeriesIds.has(seriesId)) {
      return null;
    }
    const empty: DemoAssetLibraryData = { characters: [], scenes: [], props: [] };
    assetDataBySeries.set(seriesId, empty);
    return empty;
  }

  function findDemoChapter(chapterId: string): { seriesId: string; data: DemoChapterData; chapter: Chapter } | null {
    for (const [seriesId, data] of chapterDataBySeries) {
      const chapter = data.chapters.find((candidate) => candidate.id === chapterId);
      if (chapter !== undefined) {
        return { seriesId, data, chapter };
      }
    }
    return null;
  }

  return {
    mode: "demo",
    apiBaseUrl: null,
    async restore(signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      return readStoredDemoUser(storage);
    },
    async login(credentials, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      if (credentials.username !== "demo" || credentials.password !== "demo123") {
        throw new ApiError("http", "账号或密码不正确。", 401, "账号或密码不正确。");
      }
      storage.setItem(MOCK_SESSION_KEY, "demo-session");
      storage.setItem(MOCK_USER_KEY, JSON.stringify(demoUser));
      return demoUser;
    },
    async listMyTeams(signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      return demoTeams.map((team) => ({ ...team }));
    },
    async listSeries(signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      return seriesRows.map((series) => ({ ...series }));
    },
    async getSeries(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const found = seriesRows.find((series) => series.id === seriesId);
      if (found === undefined) {
        throw demoNotFound();
      }
      return { ...found };
    },
    async createSeries(input, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const id = `demo-created-series-${nextSeriesId++}`;
      const timestamp = new Date().toISOString();
      const style = demoSeriesStyles.find((choice) => choice.id === input.style_prompt_id);
      const created: Series = {
        id,
        user_id: demoUser.id,
        name: input.name,
        description: input.description,
        image_url: input.image_url,
        style_prompt_id: input.style_prompt_id,
        style_prompt: null,
        style_prompt_name: style?.name ?? null,
        style_prompt_owner_name: style === undefined ? null : style.is_system ? "系统" : demoUser.username,
        team_id: null,
        team_name: null,
        owner_name: demoUser.username,
        claimed_by: null,
        claimed_by_username: null,
        claimed_by_avatar_url: null,
        can_enter: true,
        created_at: timestamp,
        updated_at: timestamp,
      };
      customSeriesIds.add(id);
      chapterDataBySeries.set(id, { chapters: [], assetsByChapter: {} });
      assetDataBySeries.set(id, { characters: [], scenes: [], props: [] });
      seriesRows.unshift(created);
      return { ...created };
    },
    async updateSeries(seriesId, update, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const index = seriesRows.findIndex((series) => series.id === seriesId);
      const current = seriesRows[index];
      if (current === undefined) {
        throw demoNotFound();
      }
      const nextStyleId = update.style_prompt_id === undefined ? current.style_prompt_id : update.style_prompt_id;
      const style = demoSeriesStyles.find((choice) => choice.id === nextStyleId);
      const updated: Series = {
        ...current,
        ...update,
        style_prompt_id: nextStyleId,
        style_prompt_name: update.style_prompt_id === undefined
          ? current.style_prompt_name
          : style?.name ?? (nextStyleId === "" ? null : current.style_prompt_name),
        style_prompt_owner_name: update.style_prompt_id === undefined
          ? current.style_prompt_owner_name
          : nextStyleId === ""
            ? null
            : style === undefined
              ? current.style_prompt_owner_name
              : style.is_system ? "系统" : demoUser.username,
        updated_at: new Date().toISOString(),
      };
      seriesRows[index] = updated;
      return { ...updated };
    },
    async deleteSeries(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const index = seriesRows.findIndex((series) => series.id === seriesId);
      if (index < 0) {
        throw demoNotFound();
      }
      seriesRows.splice(index, 1);
      customSeriesIds.delete(seriesId);
      chapterDataBySeries.delete(seriesId);
      assetDataBySeries.delete(seriesId);
    },
    async listSeriesStylePrompts(signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      return demoSeriesStyles.map((choice) => ({ ...choice }));
    },
    async listChapters(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = chapterDataBySeries.get(seriesId);
      if (data === undefined) {
        throw demoNotFound();
      }
      return cloneDemoChapterData(data).chapters.sort((left, right) => (
        right.order - left.order || Date.parse(right.created_at) - Date.parse(left.created_at)
      ));
    },
    async createChapter(seriesId, input, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = chapterDataBySeries.get(seriesId);
      if (data === undefined) {
        throw demoNotFound();
      }
      const timestamp = new Date().toISOString();
      const chapter: Chapter = {
        id: `demo-created-chapter-${nextChapterId++}`,
        series_id: seriesId,
        title: input.title,
        content: input.content.map((frame) => cloneDemoNoteValue(frame) as StoryboardFrame),
        order: input.order === 0
          ? (data.chapters.length === 0
            ? 0
            : Math.max(...data.chapters.map((existing) => existing.order))) + 1
          : input.order,
        created_at: timestamp,
        updated_at: timestamp,
        lock: null,
      };
      data.chapters.push(chapter);
      data.assetsByChapter[chapter.id] = [];
      const snapshotKey = demoUser.id + ":" + chapter.id;
      const withoutSeedOpening = { ...chapter, title: "" };
      personalNoteSnapshots.set(snapshotKey, cloneDemoPersonalProductionSnapshot(
        getDemoPersonalProductionNotes(chapter.id, withoutSeedOpening, []),
      ));
      personalRoughCutSnapshots.set(snapshotKey, clonePersonalRoughCutSnapshot(
        getDemoPersonalRoughCut(withoutSeedOpening, []),
      ));
      return cloneDemoChapter(chapter);
    },
    async reorderChapters(seriesId, input, signal) {
      if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
      ensureDemoSession(storage);
      const data = chapterDataBySeries.get(seriesId);
      if (data === undefined) throw demoNotFound();
      const ids = input.chapters.map((entry) => entry.id);
      const currentIds = data.chapters.map((chapter) => chapter.id);
      const orders = input.chapters.map((entry) => entry.order);
      if (ids.length !== currentIds.length || new Set(ids).size !== ids.length
        || ids.some((id) => !currentIds.includes(id))
        || new Set(orders).size !== currentIds.length
        || orders.some((order) => !Number.isInteger(order) || order < 1 || order > currentIds.length)) {
        throw new ApiError("http", "章节列表已变化，请重新读取后再排序。", 409);
      }
      const byId = new Map(data.chapters.map((chapter) => [chapter.id, chapter]));
      const timestamp = new Date().toISOString();
      data.chapters = input.chapters.map((entry) => {
        const chapter = byId.get(entry.id);
        if (chapter === undefined) throw new ApiError("http", "章节列表已变化，请重新读取后再排序。", 409);
        return { ...chapter, order: entry.order, updated_at: timestamp };
      });
      return { message: "排序已更新" };
    },
    async getChapterEditLock(chapterId, signal, leaseHandle) {
      if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
      ensureDemoSession(storage);
      const lease = leaseHandle === undefined ? undefined : demoChapterLockLeases.get(leaseHandle);
      if (leaseHandle !== undefined && (lease === undefined || lease.chapterId !== chapterId
        || lease.session !== storage.getItem(MOCK_SESSION_KEY))) {
        throw new ApiError("aborted", "章节锁句柄不属于当前编辑会话。", null);
      }
      const match = findDemoChapter(chapterId);
      if (match === null) throw demoNotFound();
      const series = seriesRows.find((row) => row.id === match.seriesId);
      if (!series?.team_id) return { locked: false };
      const lock = demoChapterLocks.get(chapterId);
      if (lock === undefined || Date.parse(lock.expiresAt) <= Date.now()) {
        demoChapterLocks.delete(chapterId);
        return { locked: false };
      }
      const state: ChapterEditLockState = {
        locked: true,
        user_id: lock.userId,
        username: lock.username,
        is_mine: lock.userId === demoUser.id,
        acquired_at: lock.acquiredAt,
        last_active_at: lock.lastActiveAt,
        expires_at: lock.expiresAt,
      };
      if (lease !== undefined && (lease.acquiredAt === null || lease.userId === null
        || lease.acquiredAt !== lock.acquiredAt || lease.userId !== lock.userId
        || lease.expiresAt !== lock.expiresAt)) {
        throw new ApiError("aborted", "章节锁身份已变化，未继续使用旧编辑句柄。", null);
      }
      return state;
    },
    async acquireChapterEditLock(chapterId, signal) {
      if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
      ensureDemoSession(storage);
      const match = findDemoChapter(chapterId);
      if (match === null) throw demoNotFound();
      const series = seriesRows.find((row) => row.id === match.seriesId);
      if (!series?.team_id) return { state: { locked: false, message: "个人剧集无需加锁" }, lease: null };
      const existing = demoChapterLocks.get(chapterId);
      if (existing !== undefined && Date.parse(existing.expiresAt) > Date.now()
        && existing.userId !== demoUser.id) {
        throw new ApiError("http", "章节当前由其他创作者编辑。", 409);
      }
      if (existing !== undefined && Date.parse(existing.expiresAt) > Date.now()) {
        return {
          state: {
            locked: true,
            user_id: existing.userId,
            username: existing.username,
            is_mine: existing.userId === demoUser.id,
            acquired_at: existing.acquiredAt,
            expires_at: existing.expiresAt,
          },
          lease: null,
        };
      }
      const now = new Date().toISOString();
      const expiresAt = new Date(Date.now() + 15 * 60_000).toISOString();
      demoChapterLocks.set(chapterId, {
        userId: demoUser.id, username: demoUser.username, acquiredAt: now, lastActiveAt: now, expiresAt,
      });
      const lease = {} as ChapterEditLockLeaseHandle;
      demoChapterLockLeases.set(lease, {
        chapterId,
        session: storage.getItem(MOCK_SESSION_KEY),
        acquiredAt: now,
        expiresAt,
        userId: demoUser.id,
      });
      return {
        state: {
          locked: true,
          user_id: demoUser.id,
          username: demoUser.username,
          is_mine: true,
          acquired_at: now,
          expires_at: expiresAt,
          idle_minutes: 15,
        },
        lease,
      };
    },
    async releaseChapterEditLock(chapterId, signal, leaseHandle) {
      if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
      ensureDemoSession(storage);
      const lease = demoChapterLockLeases.get(leaseHandle);
      const existing = demoChapterLocks.get(chapterId);
      if (lease === undefined || lease.chapterId !== chapterId
        || lease.session !== storage.getItem(MOCK_SESSION_KEY) || lease.acquiredAt === null
        || existing === undefined || existing.userId !== lease.userId
        || existing.acquiredAt !== lease.acquiredAt || existing.expiresAt !== lease.expiresAt
        || existing.userId !== demoUser.id) {
        throw new ApiError("aborted", "章节锁不属于当前编辑会话，未释放。");
      }
      demoChapterLocks.delete(chapterId);
      demoChapterLockLeases.delete(leaseHandle);
      return { message: "锁已释放" };
    },
    async updateChapter(seriesId, chapterId, update, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const match = findDemoChapter(chapterId);
      if (match === null || match.seriesId !== seriesId) {
        throw demoNotFound();
      }
      const snapshotKey = demoUser.id + ":" + chapterId;
      if (!personalNoteSnapshots.has(snapshotKey)) {
        personalNoteSnapshots.set(snapshotKey, cloneDemoPersonalProductionSnapshot(
          getDemoPersonalProductionNotes(
            chapterId,
            match.chapter,
            match.data.assetsByChapter[chapterId] ?? [],
          ),
        ));
      }
      if (!personalRoughCutSnapshots.has(snapshotKey)) {
        personalRoughCutSnapshots.set(snapshotKey, clonePersonalRoughCutSnapshot(
          getDemoPersonalRoughCut(match.chapter, match.data.assetsByChapter[chapterId] ?? []),
        ));
      }
      let normalizedUpdate = update;
      if (update.content !== undefined) {
        const timestamp = new Date().toISOString();
        const content = update.content.map((frame, frameIndex) => {
          const nextFrame = { ...(cloneDemoNoteValue(frame) as StoryboardFrame) } as Record<string, unknown>;
          const refs = nextFrame.storyboard;
          if (refs === undefined || refs === null || (Array.isArray(refs) && refs.length === 0)) {
            const id = `demo-storyboard-${nextDemoStoryboardAssetId++}`;
            const text = typeof nextFrame.text === "string" ? nextFrame.text : "";
            nextFrame.storyboard = [id];
            const chapterAssets = match.data.assetsByChapter[chapterId]
              ?? (match.data.assetsByChapter[chapterId] = []);
            chapterAssets.push({
              id,
              series_id: seriesId,
              chapter_id: chapterId,
              frame_index: frameIndex,
              name: `分镜 ${frameIndex + 1}`,
              description: text || (typeof nextFrame.original_text === "string" ? nextFrame.original_text : ""),
              image_url: null,
              created_at: timestamp,
              updated_at: timestamp,
            });
          } else if (Array.isArray(refs) && typeof refs[0] === "string") {
            const asset = (match.data.assetsByChapter[chapterId] ?? []).find((candidate) => candidate.id === refs[0]);
            if (asset !== undefined) {
              asset.frame_index = frameIndex;
              asset.name = `分镜 ${frameIndex + 1}`;
              asset.description = typeof nextFrame.text === "string" ? nextFrame.text : asset.description;
              asset.updated_at = timestamp;
            }
          }
          return nextFrame as StoryboardFrame;
        });
        normalizedUpdate = { ...update, content };
      }
      const updated = { ...match.chapter, ...normalizedUpdate, updated_at: new Date().toISOString() };
      match.data.chapters = match.data.chapters.map((chapter) => chapter.id === chapterId ? updated : chapter);
      return cloneDemoChapter(updated);
    },
    async deleteChapter(seriesId, chapterId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const match = findDemoChapter(chapterId);
      if (match === null || match.seriesId !== seriesId) {
        throw demoNotFound();
      }
      match.data.chapters = match.data.chapters.filter((chapter) => chapter.id !== chapterId);
      delete match.data.assetsByChapter[chapterId];
      const key = demoUser.id + ":" + chapterId;
      personalNoteSnapshots.delete(key);
      personalRoughCutSnapshots.delete(key);
    },
    async listStoryboardAssets(seriesId, chapterId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = chapterDataBySeries.get(seriesId);
      if (data === undefined) {
        throw demoNotFound();
      }
      if (!data.chapters.some((chapter) => chapter.id === chapterId)) {
        throw demoNotFound();
      }
      return (data.assetsByChapter[chapterId] ?? []).map((asset) => ({ ...asset }));
    },
    async listCharacters(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = findDemoAssetData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      return data.characters.map((character) => ({
        ...character,
        aliases: character.aliases === null ? null : [...character.aliases],
      }));
    },
    async listScenes(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = findDemoAssetData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      return data.scenes.map((scene) => ({
        ...scene,
        aliases: scene.aliases === null ? null : [...scene.aliases],
      }));
    },
    async listProps(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = findDemoAssetData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      return data.props.map((prop) => ({
        ...prop,
        aliases: prop.aliases === null ? null : [...prop.aliases],
      }));
    },
    async createCharacter(seriesId, input, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = findDemoAssetData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      const timestamp = new Date().toISOString();
      const created: Character = {
        id: `demo-character-${nextDemoManagedAssetId++}`,
        series_id: seriesId,
        name: input.name,
        gender: input.gender ?? null,
        age: input.age ?? null,
        role: input.role ?? null,
        appearance: input.appearance ?? null,
        description: input.description ?? null,
        image_url: input.image_url ?? null,
        audio_url: input.audio_url ?? null,
        voice_ref: input.voice_ref ?? null,
        aliases: null,
        canonical_key: input.name.trim() || null,
        created_at: timestamp,
        updated_at: timestamp,
      };
      data.characters.push(created);
      return { ...created };
    },
    async updateCharacter(seriesId, assetId, update, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const found = findDemoAssetRow(assetDataBySeries, "characters", assetId);
      if (found === null || found.asset.series_id !== seriesId) {
        throw demoNotFound();
      }
      const updated: Character = {
        ...found.asset,
        ...definedValues(update),
        canonical_key: update.name === undefined ? found.asset.canonical_key : update.name.trim() || null,
        updated_at: new Date().toISOString(),
      };
      found.rows[found.index] = updated;
      return { ...updated, aliases: updated.aliases === null ? null : [...updated.aliases] };
    },
    async deleteCharacter(assetId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const found = findDemoAssetRow(assetDataBySeries, "characters", assetId);
      if (found === null) {
        throw demoNotFound();
      }
      removeDemoChapterAssetReferences(chapterDataBySeries.get(found.asset.series_id), "characters", assetId);
      found.rows.splice(found.index, 1);
    },
    async createScene(seriesId, input, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = findDemoAssetData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      const timestamp = new Date().toISOString();
      const created: Scene = {
        id: `demo-scene-${nextDemoManagedAssetId++}`,
        series_id: seriesId,
        title: input.title,
        description: input.description ?? null,
        image_url: input.image_url ?? null,
        aliases: null,
        canonical_key: input.title.trim() || null,
        created_at: timestamp,
        updated_at: timestamp,
      };
      data.scenes.push(created);
      return { ...created };
    },
    async updateScene(seriesId, assetId, update, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const found = findDemoAssetRow(assetDataBySeries, "scenes", assetId);
      if (found === null || found.asset.series_id !== seriesId) {
        throw demoNotFound();
      }
      const updated: Scene = {
        ...found.asset,
        ...definedValues(update),
        canonical_key: update.title === undefined ? found.asset.canonical_key : update.title.trim() || null,
        updated_at: new Date().toISOString(),
      };
      found.rows[found.index] = updated;
      return { ...updated, aliases: updated.aliases === null ? null : [...updated.aliases] };
    },
    async deleteScene(assetId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const found = findDemoAssetRow(assetDataBySeries, "scenes", assetId);
      if (found === null) {
        throw demoNotFound();
      }
      removeDemoChapterAssetReferences(chapterDataBySeries.get(found.asset.series_id), "scenes", assetId);
      found.rows.splice(found.index, 1);
    },
    async createProp(seriesId, input, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = findDemoAssetData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      const timestamp = new Date().toISOString();
      const created: Prop = {
        id: `demo-prop-${nextDemoManagedAssetId++}`,
        series_id: seriesId,
        name: input.name,
        description: input.description ?? null,
        image_url: input.image_url ?? null,
        aliases: null,
        canonical_key: input.name.trim() || null,
        created_at: timestamp,
        updated_at: timestamp,
      };
      data.props.push(created);
      return { ...created };
    },
    async updateProp(seriesId, assetId, update, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const found = findDemoAssetRow(assetDataBySeries, "props", assetId);
      if (found === null || found.asset.series_id !== seriesId) {
        throw demoNotFound();
      }
      const updated: Prop = {
        ...found.asset,
        ...definedValues(update),
        canonical_key: update.name === undefined ? found.asset.canonical_key : update.name.trim() || null,
        updated_at: new Date().toISOString(),
      };
      found.rows[found.index] = updated;
      return { ...updated, aliases: updated.aliases === null ? null : [...updated.aliases] };
    },
    async deleteProp(assetId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const found = findDemoAssetRow(assetDataBySeries, "props", assetId);
      if (found === null) {
        throw demoNotFound();
      }
      removeDemoChapterAssetReferences(chapterDataBySeries.get(found.asset.series_id), "props", assetId);
      found.rows.splice(found.index, 1);
    },
    async getPersonalProductionNotes(chapterId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      for (const [, data] of chapterDataBySeries) {
        const chapter = data.chapters.find((candidate) => candidate.id === chapterId);
        if (chapter !== undefined) {
          const key = demoUser.id + ":" + chapterId;
          let snapshot = personalNoteSnapshots.get(key);
          if (snapshot === undefined) {
            snapshot = cloneDemoPersonalProductionSnapshot(
              getDemoPersonalProductionNotes(chapterId, chapter, data.assetsByChapter[chapterId] ?? []),
            );
            personalNoteSnapshots.set(key, snapshot);
          }
          return cloneDemoPersonalProductionSnapshot(snapshot);
        }
      }
      throw demoNotFound();
    },
    async savePersonalProductionNote(chapterId, updateValue, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const update = parsePersonalProductionNoteUpdate(updateValue);
      for (const [, data] of chapterDataBySeries) {
        const chapter = data.chapters.find((candidate) => candidate.id === chapterId);
        if (chapter === undefined) {
          continue;
        }

        const key = demoUser.id + ":" + chapterId;
        let current = personalNoteSnapshots.get(key);
        if (current === undefined) {
          current = cloneDemoPersonalProductionSnapshot(
            getDemoPersonalProductionNotes(chapterId, chapter, data.assetsByChapter[chapterId] ?? []),
          );
          personalNoteSnapshots.set(key, current);
        }
        if (current.revision !== update.expected_revision) {
          throw new ApiError("http", "个人记录版本已变化，请重新读取。", 409);
        }
        if (current.media_state !== "ready") {
          throw new ApiError("http", "当前章节镜头无法核对。", 422);
        }

        const patch = update.frames[0];
        const matches = current.frames.filter((frame) => frame.storyboard_asset_id === patch.storyboard_asset_id);
        if (matches.length !== 1) {
          throw new ApiError("http", "当前镜头身份无法核对。", 422);
        }
        const currentFrame = matches[0];
        if (
          currentFrame === undefined
          || !currentFrame.source_valid
          || currentFrame.media_revision !== patch.expected_media_revision
        ) {
          throw new ApiError("http", "镜头媒体版本已变化，请重新读取。", 409);
        }
        if (
          patch.status === "approved"
          && currentFrame.asset_image_digest === null
          && currentFrame.preview_digest === null
        ) {
          throw new ApiError("http", "当前媒体没有可核对的摘要，不能认可。", 422);
        }

        const frameNotes = new Map(current.frame_notes);
        const previousNote = current.frame_notes.get(patch.storyboard_asset_id);
        const savedNote = previousNote !== null
          && typeof previousNote === "object"
          && !Array.isArray(previousNote)
          ? cloneDemoNoteValue(previousNote) as Record<string, unknown>
          : {};
        frameNotes.set(patch.storyboard_asset_id, {
          ...savedNote,
          status: patch.status,
          note: patch.note,
          approved_media_revision: patch.status === "approved" ? patch.expected_media_revision : null,
          needs_reconfirmation: false,
        });
        const next: PersonalProductionSnapshot = {
          ...current,
          revision: current.revision + 1,
          frames: current.frames.map((frame) => ({ ...frame })),
          frame_notes: frameNotes,
        };
        personalNoteSnapshots.set(key, cloneDemoPersonalProductionSnapshot(next));
        return cloneDemoPersonalProductionSnapshot(next);
      }
      throw demoNotFound();
    },
    async savePersonalProductionResume(chapterId, updateValue, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const update = parsePersonalProductionResumeUpdate(updateValue);
      for (const [seriesId, data] of chapterDataBySeries) {
        const chapter = data.chapters.find((candidate) => candidate.id === chapterId);
        if (chapter === undefined) {
          continue;
        }

        const key = demoUser.id + ":" + chapterId;
        let current = personalNoteSnapshots.get(key);
        if (current === undefined) {
          current = cloneDemoPersonalProductionSnapshot(
            getDemoPersonalProductionNotes(chapterId, chapter, data.assetsByChapter[chapterId] ?? []),
          );
          personalNoteSnapshots.set(key, current);
        }
        if (current.revision !== update.expected_revision) {
          throw new ApiError("http", "个人记录版本已变化，请重新读取。", 409);
        }

        const resumeFrameId = update.resume_frame_id;
        if (resumeFrameId !== null) {
          const assets = data.assetsByChapter[chapterId] ?? [];
          const matchingPositions = chapter.content === null
            ? []
            : chapter.content.flatMap((frame, index) => {
                const rawId = Array.isArray(frame.storyboard) ? frame.storyboard[0] : null;
                return rawId === resumeFrameId ? [index] : [];
              });
          const matchingAssets = assets.filter((asset) => asset.id === resumeFrameId);
          const matchingFrames = current.frames.filter(
            (frame) => frame.storyboard_asset_id === resumeFrameId,
          );
          const frameIndex = matchingPositions.length === 1 ? matchingPositions[0] : undefined;
          const asset = matchingAssets.length === 1 ? matchingAssets[0] : undefined;
          const serverFrame = matchingFrames.length === 1 ? matchingFrames[0] : undefined;
          if (
            current.media_state !== "ready"
            || chapter.series_id !== seriesId
            || frameIndex === undefined
            || asset === undefined
            || asset.chapter_id !== chapter.id
            || asset.series_id !== seriesId
            || serverFrame === undefined
            || serverFrame.frame_index !== frameIndex
            || serverFrame.source_valid !== true
          ) {
            throw new ApiError("http", "当前镜头身份无法核对。", 422);
          }
        }

        const next: PersonalProductionSnapshot = {
          ...current,
          revision: current.revision + 1,
          frames: current.frames.map((frame) => ({ ...frame })),
          resume_frame_id: update.resume_frame_id,
          frame_notes: new Map([...current.frame_notes.entries()].map(([id, value]) => [
            id,
            cloneDemoNoteValue(value),
          ])),
        };
        personalNoteSnapshots.set(key, cloneDemoPersonalProductionSnapshot(next));
        return cloneDemoPersonalProductionSnapshot(next);
      }
      throw demoNotFound();
    },
    async getPersonalRoughCut(chapterId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      for (const [, data] of chapterDataBySeries) {
        const chapter = data.chapters.find((candidate) => candidate.id === chapterId);
        if (chapter !== undefined) {
          const key = demoUser.id + ":" + chapterId;
          const saved = personalRoughCutSnapshots.get(key);
          return clonePersonalRoughCutSnapshot(saved ?? getDemoPersonalRoughCut(
            chapter,
            data.assetsByChapter[chapterId] ?? [],
          ));
        }
      }
      throw demoNotFound();
    },
    async savePersonalRoughCut(chapterId, updateValue, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const update = parsePersonalRoughCutUpdate(updateValue);
      for (const [, data] of chapterDataBySeries) {
        const chapter = data.chapters.find((candidate) => candidate.id === chapterId);
        if (chapter === undefined) {
          continue;
        }

        const key = demoUser.id + ":" + chapterId;
        const assets = data.assetsByChapter[chapterId] ?? [];
        const current = personalRoughCutSnapshots.get(key)
          ?? getDemoPersonalRoughCut(chapter, assets);
        if (current.revision !== update.expected_revision) {
          throw new ApiError("http", "粗剪版本已变化，请重新读取后再编辑。", 409);
        }

        const sourceCheck = inspectRoughCutSource(chapter, current, assets, "ready");
        if (!sourceCheck.valid) {
          throw new ApiError("http", sourceCheck.reason ?? "当前章节镜头身份无法核对。", 422);
        }
        const sourceIds = new Set(sourceCheck.sourceIds);
        const currentFramesById = new Map(current.frames.map((frame) => [frame.asset_id, frame]));
        if (
          update.frames.length !== sourceIds.size
          || update.frames.some((frame) => !sourceIds.has(frame.asset_id) || !currentFramesById.has(frame.asset_id))
        ) {
          throw new ApiError("http", "粗剪保存内容未包含当前完整镜头集合。", 422);
        }

        const next: PersonalRoughCutSnapshot = {
          ...current,
          revision: current.revision + 1,
          saved: true,
          frames: update.frames.map((frame) => {
            const sourceFrame = currentFramesById.get(frame.asset_id);
            if (sourceFrame === undefined) {
              throw new ApiError("http", "当前镜头身份无法核对。", 422);
            }
            return { ...sourceFrame, included: frame.included, pending: false };
          }),
          removed_asset_ids: [],
        };
        const validated = parsePersonalRoughCutSaveResponse(next, chapterId, update);
        personalRoughCutSnapshots.set(key, clonePersonalRoughCutSnapshot(validated));
        return clonePersonalRoughCutSnapshot(validated);
      }
      throw demoNotFound();
    },
    async listMyTasks(page, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      if (!Number.isSafeInteger(page) || page < 1) {
        throw new ApiError("invalid-response", "任务页码无效。");
      }
      return parseMyTaskPage(getDemoMyTaskPage(page), page);
    },
    async getTaskReceipt(taskId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const record = demoTaskRecords.find((candidate) => candidate.id === taskId);
      if (record === undefined) {
        throw demoNotFound();
      }
      return parseTaskReceipt({
        id: record.id,
        type: record.type,
        message_id: record.message_id,
        status: record.status,
        result: record.result,
        billing_status: null,
        quoted_amount: null,
        progress: Number.isSafeInteger(record.progress) && record.progress >= 0 ? record.progress : null,
        progress_message: record.progress_message,
        external_task_id: null,
        external_provider: null,
        created_at: record.created_at,
        updated_at: record.updated_at,
      }, taskId);
    },
    async getTaskRequest(taskId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const record = demoTaskRecords.find((candidate) => candidate.id === taskId);
      if (record === undefined) {
        throw demoNotFound();
      }
      return parseTaskRequestRecord({
        id: record.id,
        type: record.type,
        status: record.status,
        model_name: null,
        credit_cost: null,
        external_task_id: null,
        external_provider: null,
        created_at: record.created_at,
        request: parseDemoTaskRequestData(record.request_data),
      }, taskId);
    },
    logout() {
      clearMockSession(storage);
    },
  };
}

function timeoutSignal(parent: AbortSignal, timeoutMs: number): {
  signal: AbortSignal;
  didTimeout(): boolean;
  dispose(): void;
} {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromParent = () => controller.abort();

  if (parent.aborted) {
    controller.abort();
  } else {
    parent.addEventListener("abort", abortFromParent, { once: true });
  }

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    dispose() {
      clearTimeout(timer);
      parent.removeEventListener("abort", abortFromParent);
    },
  };
}

const STORYBOARD_STREAM_TOTAL_TIMEOUT_MS = 5 * 60_000;
const STORYBOARD_STREAM_IDLE_TIMEOUT_MS = 45_000;

function storyboardStreamTimeout(parent: AbortSignal): {
  signal: AbortSignal;
  didTimeout(): boolean;
  resetIdle(): void;
  dispose(): void;
} {
  const controller = new AbortController();
  let timedOut = false;
  const totalTimer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, STORYBOARD_STREAM_TOTAL_TIMEOUT_MS);
  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  const resetIdle = () => {
    if (idleTimer !== null) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, STORYBOARD_STREAM_IDLE_TIMEOUT_MS);
  };
  const abortFromParent = () => controller.abort();
  if (parent.aborted) controller.abort();
  else parent.addEventListener("abort", abortFromParent, { once: true });
  resetIdle();
  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    resetIdle,
    dispose() {
      clearTimeout(totalTimer);
      if (idleTimer !== null) clearTimeout(idleTimer);
      parent.removeEventListener("abort", abortFromParent);
    },
  };
}

function errorMessageForStatus(status: number, detail: string | null): string {
  if (detail !== null) {
    return detail;
  }
  if (status === 401) {
    return "登录状态已失效，请重新登录。";
  }
  if (status === 403) {
    return "当前账号没有访问权限。";
  }
  if (status === 422) {
    return "提交内容未通过服务器校验。";
  }
  return "服务暂时不可用，请稍后重试。";
}

function validateApiPathId(value: string | undefined, label: string): string {
  if (!value || value === "." || value === "..") {
    throw new ApiError("invalid-response", label + "编号无效，无法读取内容。");
  }
  return value;
}

function encodeApiPathId(value: string | undefined, label: string): string {
  return encodeURIComponent(validateApiPathId(value, label));
}

type ApiEndpoint =
  | "login"
  | "me"
  | "series"
  | "seriesDetail"
  | "createSeries"
  | "updateSeries"
  | "deleteSeries"
  | "stylePrompts"
  | "storyboardModels"
  | "storyboardPrompts"
  | "quoteTwoPassStoryboard"
  | "streamTwoPassStoryboard"
  | "twoPassSubmission"
  | "twoPassTask"
  | "exportSeries"
  | "importSeries"
  | "uploadSeriesCover"
  | "chapters"
  | "createChapter"
  | "reorderChapters"
  | "getChapterEditLock"
  | "acquireChapterEditLock"
  | "releaseChapterEditLock"
  | "updateChapter"
  | "deleteChapter"
  | "storyboardAssets"
  | "characters"
  | "scenes"
  | "props"
  | "createCharacter"
  | "updateCharacter"
  | "deleteCharacter"
  | "createScene"
  | "updateScene"
  | "deleteScene"
  | "createProp"
  | "updateProp"
  | "deleteProp"
  | "personalProductionNotes"
  | "savePersonalProductionNote"
  | "savePersonalProductionResume"
  | "personalRoughCut"
  | "savePersonalRoughCut"
  | "myTasks"
  | "taskRequest"
  | "myTeams";

interface ApiRequestDefinition {
  path: string;
  method: "GET" | "POST" | "PUT" | "DELETE";
  expectedStatus?: number;
}

function apiRequestDefinition(
  endpoint: ApiEndpoint,
  identifiers?: {
    seriesId?: string;
    chapterId?: string;
    page?: number;
    idempotencyKey?: string;
    taskId?: string;
    assetId?: string;
  },
): ApiRequestDefinition {
  if (endpoint === "login") {
    return { path: "/auth/login", method: "POST" };
  }
  if (endpoint === "me") {
    return { path: "/auth/me", method: "GET" };
  }
  if (endpoint === "series") {
    return { path: "/series", method: "GET" };
  }
  if (endpoint === "createSeries") {
    return { path: "/series", method: "POST", expectedStatus: 201 };
  }
  if (endpoint === "exportSeries") {
    const seriesId = encodeApiPathId(identifiers?.seriesId, "剧集");
    return { path: "/series/" + seriesId + "/export", method: "GET" };
  }
  if (endpoint === "importSeries") {
    return { path: "/series/import", method: "POST", expectedStatus: 200 };
  }
  if (endpoint === "stylePrompts") {
    return { path: "/prompts?category=style", method: "GET" };
  }
  if (endpoint === "storyboardModels") return { path: "/models", method: "GET" };
  if (endpoint === "storyboardPrompts") return { path: "/prompts", method: "GET" };
  if (endpoint === "quoteTwoPassStoryboard") {
    return { path: "/storyboard/two-pass/quote", method: "POST" };
  }
  if (endpoint === "streamTwoPassStoryboard") {
    return { path: "/storyboard/two-pass/stream", method: "POST" };
  }
  if (endpoint === "twoPassSubmission") {
    const query = new URLSearchParams({ idempotency_key: identifiers?.idempotencyKey ?? "" });
    return { path: "/chat/submissions/storyboard.two_pass?" + query.toString(), method: "GET" };
  }
  if (endpoint === "twoPassTask") {
    const query = new URLSearchParams({ task_id: identifiers?.taskId ?? "" });
    return { path: "/chat/tasks?" + query.toString(), method: "GET" };
  }
  if (endpoint === "taskRequest") {
    const taskId = encodeApiPathId(identifiers?.taskId, "任务");
    return { path: "/chat/tasks/" + taskId + "/request", method: "GET" };
  }
  if (endpoint === "uploadSeriesCover") {
    return { path: "/upload/image", method: "POST" };
  }
  if (endpoint === "seriesDetail" || endpoint === "updateSeries" || endpoint === "deleteSeries") {
    const seriesId = encodeApiPathId(identifiers?.seriesId, "剧集");
    return {
      path: "/series/" + seriesId,
      method: endpoint === "seriesDetail" ? "GET" : endpoint === "updateSeries" ? "PUT" : "DELETE",
      ...(endpoint === "updateSeries" ? { expectedStatus: 200 } : {}),
      ...(endpoint === "deleteSeries" ? { expectedStatus: 204 } : {}),
    };
  }
  if (endpoint === "myTeams") {
    return { path: "/teams/my", method: "GET" };
  }
  if (
    endpoint === "createCharacter" || endpoint === "updateCharacter" || endpoint === "deleteCharacter"
    || endpoint === "createScene" || endpoint === "updateScene" || endpoint === "deleteScene"
    || endpoint === "createProp" || endpoint === "updateProp" || endpoint === "deleteProp"
  ) {
    const assetType = endpoint.endsWith("Character")
      ? "characters"
      : endpoint.endsWith("Scene") ? "scenes" : "props";
    if (endpoint.startsWith("create")) {
      const seriesId = encodeApiPathId(identifiers?.seriesId, "剧集");
      return { path: "/series/" + seriesId + "/" + assetType, method: "POST", expectedStatus: 201 };
    }
    const assetId = encodeApiPathId(identifiers?.assetId, "素材");
    return {
      path: "/" + assetType + "/" + assetId,
      method: endpoint.startsWith("update") ? "PUT" : "DELETE",
      ...(endpoint.startsWith("update") ? { expectedStatus: 200 } : { expectedStatus: 204 }),
    };
  }
  if (endpoint === "createChapter") {
    const seriesId = encodeApiPathId(identifiers?.seriesId, "剧集");
    return { path: "/series/" + seriesId + "/chapters", method: "POST", expectedStatus: 201 };
  }
  if (endpoint === "reorderChapters") {
    const seriesId = encodeApiPathId(identifiers?.seriesId, "剧集");
    return { path: "/series/" + seriesId + "/chapters/reorder", method: "PUT", expectedStatus: 200 };
  }
  if (
    endpoint === "getChapterEditLock"
    || endpoint === "acquireChapterEditLock"
    || endpoint === "releaseChapterEditLock"
  ) {
    const chapterId = encodeApiPathId(identifiers?.chapterId, "章节");
    return {
      path: "/chapters/" + chapterId + "/lock",
      method: endpoint === "getChapterEditLock" ? "GET" : endpoint === "acquireChapterEditLock" ? "POST" : "DELETE",
      expectedStatus: 200,
    };
  }
  if (endpoint === "updateChapter" || endpoint === "deleteChapter") {
    const chapterId = encodeApiPathId(identifiers?.chapterId, "章节");
    return {
      path: "/chapters/" + chapterId,
      method: endpoint === "updateChapter" ? "PUT" : "DELETE",
      ...(endpoint === "updateChapter" ? { expectedStatus: 200 } : { expectedStatus: 204 }),
    };
  }
  if (endpoint === "personalProductionNotes") {
    const chapterId = encodeApiPathId(identifiers?.chapterId, "章节");
    return { path: "/chapters/" + chapterId + "/personal-production-notes", method: "GET" };
  }
  if (endpoint === "savePersonalProductionNote" || endpoint === "savePersonalProductionResume") {
    const chapterId = encodeApiPathId(identifiers?.chapterId, "章节");
    return { path: "/chapters/" + chapterId + "/personal-production-notes", method: "PUT" };
  }
  if (endpoint === "personalRoughCut" || endpoint === "savePersonalRoughCut") {
    const chapterId = encodeApiPathId(identifiers?.chapterId, "章节");
    return {
      path: "/chapters/" + chapterId + "/rough-cut",
      method: endpoint === "personalRoughCut" ? "GET" : "PUT",
    };
  }
  if (endpoint === "myTasks") {
    const page = identifiers?.page;
    if (page === undefined || !Number.isSafeInteger(page) || page < 1) {
      throw new ApiError("invalid-response", "任务页码无效。");
    }
    const query = new URLSearchParams({ page: String(page), page_size: String(MY_TASK_PAGE_SIZE) });
    return { path: "/chat/tasks/list?" + query.toString(), method: "GET" };
  }

  const seriesId = encodeApiPathId(identifiers?.seriesId, "剧集");
  if (endpoint === "chapters") {
    return { path: "/series/" + seriesId + "/chapters", method: "GET" };
  }
  if (endpoint === "storyboardAssets") {
    const chapterId = validateApiPathId(identifiers?.chapterId, "章节");
    const query = new URLSearchParams({ chapter_id: chapterId });
    return {
      path: "/series/" + seriesId + "/storyboard-assets?" + query.toString(),
      method: "GET",
    };
  }
  return { path: "/series/" + seriesId + "/" + endpoint, method: "GET" };
}

export function createApiServices(
  apiBaseUrl: string,
  options: ServiceOptions = {},
): WorkspaceServices {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? window.fetch.bind(window);
  const timeoutMs = options.timeoutMs ?? 15_000;
  const chapterEditLockLeases = new Map<ChapterEditLockLeaseHandle, {
    chapterId: string;
    token: string;
    acquiredAt: string | null;
    userId: string | null;
    expiresAt: string | null;
  }>();

  function recordCurrentChapterLockLease(
    lease: NonNullable<ReturnType<typeof chapterEditLockLeases.get>>,
    state: ChapterEditLockState,
  ): boolean {
    if (!state.locked || state.is_mine !== true || state.acquired_at === undefined
      || state.user_id === undefined || state.expires_at === undefined) return false;
    const expiresAt = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(state.expires_at)
      ? state.expires_at
      : state.expires_at + "Z";
    const expiry = Date.parse(expiresAt);
    if (!Number.isFinite(expiry) || expiry <= Date.now()) return false;
    if (lease.acquiredAt === null) {
      if (state.expires_at !== lease.expiresAt || (lease.userId !== null && state.user_id !== lease.userId)) return false;
      lease.acquiredAt = state.acquired_at;
      lease.userId = state.user_id;
      return true;
    }
    const previousExpiry = lease.expiresAt === null
      ? Number.NaN
      : Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(lease.expiresAt) ? lease.expiresAt : lease.expiresAt + "Z");
    if (state.acquired_at !== lease.acquiredAt || state.user_id !== lease.userId
      || !Number.isFinite(previousExpiry) || expiry < previousExpiry) return false;
    lease.expiresAt = state.expires_at;
    return true;
  }

  async function request(
    endpoint: ApiEndpoint,
    token: string | null,
    signal: AbortSignal,
    body?: object | FormData,
    identifiers?: {
      seriesId?: string;
      chapterId?: string;
      page?: number;
      idempotencyKey?: string;
      taskId?: string;
      assetId?: string;
    },
    requestBaseUrl = apiBaseUrl,
  ): Promise<unknown> {
    const requestDefinition = apiRequestDefinition(endpoint, identifiers);
    const url = new URL(`${requestBaseUrl}${requestDefinition.path}`);
    const timed = timeoutSignal(signal, timeoutMs);
    const headers = new Headers({ Accept: "application/json" });
    const multipartBody = typeof FormData !== "undefined" && body instanceof FormData ? body : undefined;
    if (body !== undefined && multipartBody === undefined) {
      headers.set("Content-Type", "application/json");
    }
    if (token !== null) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    let response: Response;
    try {
      response = await fetcher(url, {
        method: requestDefinition.method,
        headers,
        ...(body === undefined ? {} : { body: multipartBody ?? JSON.stringify(body) }),
        signal: timed.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
    } catch (error) {
      timed.dispose();
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      if (timed.didTimeout()) {
        throw new ApiError("timeout", "请求超时，请稍后重试。");
      }
      if (isAbortError(error)) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      throw new ApiError("network", "无法连接本地服务，请确认服务正在运行。");
    }

    if (
      response.ok
      && requestDefinition.expectedStatus !== undefined
      && response.status !== requestDefinition.expectedStatus
    ) {
      timed.dispose();
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      if (timed.didTimeout()) {
        throw new ApiError("timeout", "请求超时，请稍后重试。");
      }
      throw new ApiError("invalid-response", "服务器返回了与剧集操作不匹配的状态。");
    }

    if (response.status === 204) {
      timed.dispose();
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      if (timed.didTimeout()) {
        throw new ApiError("timeout", "请求超时，请稍后重试。");
      }
      if (!response.ok) {
        throw new ApiError("http", errorMessageForStatus(response.status, null), response.status);
      }
      return undefined;
    }

    let payload: unknown;
    try {
      payload = await response.json();
    } catch (error) {
      timed.dispose();
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      if (timed.didTimeout()) {
        throw new ApiError("timeout", "请求超时，请稍后重试。");
      }
      if (isAbortError(error)) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      if (!response.ok) {
        throw new ApiError("http", errorMessageForStatus(response.status, null), response.status);
      }
      throw new ApiError("invalid-response", "服务器返回了无法识别的数据。");
    }
    timed.dispose();

    if (signal.aborted) {
      throw new ApiError("aborted", "Request was cancelled.");
    }
    if (timed.didTimeout()) {
      throw new ApiError("timeout", "请求超时，请稍后重试。");
    }
    if (!response.ok) {
      const detail = parseErrorDetail(payload);
      throw new ApiError(
        "http",
        errorMessageForStatus(response.status, detail),
        response.status,
        detail,
      );
    }
    return payload;
  }

  function saveAuthenticatedUser(token: string, user: User): void {
    storage.setItem(API_TOKEN_KEY, token);
    storage.setItem(API_USER_KEY, JSON.stringify(user));
  }

  async function requestForCurrentApiSession(
    endpoint: ApiEndpoint,
    token: string,
    signal: AbortSignal,
    body?: object | FormData,
    identifiers?: {
      seriesId?: string;
      chapterId?: string;
      page?: number;
      idempotencyKey?: string;
      taskId?: string;
      assetId?: string;
    },
  ): Promise<unknown> {
    try {
      ensureCurrentApiSession(storage, token, signal);
      const result = await request(endpoint, token, signal, body, identifiers);
      ensureCurrentApiSession(storage, token, signal);
      return result;
    } catch (error) {
      ensureCurrentApiSession(storage, token, signal);
      throw error;
    }
  }

  async function storyboardJsonRequest(
    endpoint: ApiEndpoint,
    signal: AbortSignal,
    body?: object,
    identifiers?: { idempotencyKey?: string; taskId?: string },
  ): Promise<unknown> {
    const token = requireApiToken(storage);
    try {
      return await requestForCurrentApiSession(endpoint, token, signal, body, identifiers);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        ensureCurrentApiSession(storage, token, signal);
        if (storage.getItem(API_TOKEN_KEY) === token) clearApiSession(storage);
      }
      throw error;
    }
  }

  const storyboardGeneration: StoryboardGenerationServicePort = {
    async listModels(signal) {
      return parseStoryboardModels(await storyboardJsonRequest("storyboardModels", signal));
    },
    async listPrompts(signal) {
      return parseStoryboardPrompts(await storyboardJsonRequest("storyboardPrompts", signal));
    },
    async quote(input: TwoPassQuoteInput, signal) {
      return parseTwoPassQuote(await storyboardJsonRequest("quoteTwoPassStoryboard", signal, input));
    },
    async *stream(quoteId, idempotencyKey, signal) {
      if (quoteId.trim() === "" || idempotencyKey.trim() === "" || idempotencyKey.length > 255) {
        throw new ApiError("invalid-response", "报价或生成请求标识无效。");
      }
      const token = requireApiToken(storage);
      ensureCurrentApiSession(storage, token, signal);
      const endpoint = apiRequestDefinition("streamTwoPassStoryboard");
      const url = new URL(`${apiBaseUrl}${endpoint.path}`);
      const timed = storyboardStreamTimeout(signal);
      const headers = new Headers({
        Accept: "text/event-stream",
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "Idempotency-Key": idempotencyKey,
      });
      let response: Response;
      try {
        response = await fetcher(url, {
          method: "POST",
          headers,
          body: JSON.stringify({ quote_id: quoteId }),
          signal: timed.signal,
          credentials: "omit",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
        });
        ensureCurrentApiSession(storage, token, signal);
      } catch (error) {
        timed.dispose();
        if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
        if (timed.didTimeout()) throw new ApiError("timeout", "分镜生成连接超时，可查询任务状态后恢复。");
        if (error instanceof ApiError) throw error;
        if (isAbortError(error)) throw new ApiError("aborted", "Request was cancelled.");
        throw new ApiError("network", "无法连接分镜生成服务，可查询任务状态后恢复。");
      }

      if (response.status !== 200) {
        let detail: string | null = null;
        try {
          detail = parseErrorDetail(await response.json());
        } catch {
          detail = null;
        }
        try {
          if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
          if (timed.didTimeout()) {
            throw new ApiError("timeout", "分镜生成连接超时，可查询任务状态后恢复。");
          }
          ensureCurrentApiSession(storage, token, signal);
          if (response.status === 401 && storage.getItem(API_TOKEN_KEY) === token) clearApiSession(storage);
          throw new ApiError("http", errorMessageForStatus(response.status, detail), response.status, detail);
        } finally {
          timed.dispose();
        }
      }
      const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
      if (!contentType.includes("text/event-stream") || response.body === null) {
        await response.body?.cancel().catch(() => undefined);
        timed.dispose();
        throw new ApiError("invalid-response", "分镜生成服务没有返回有效的事件流，可查询任务状态后恢复。");
      }

      const reader = response.body.getReader();
      let finished = false;
      const guardedReader: Pick<ReadableStreamDefaultReader<Uint8Array>, "read"> = {
        async read() {
          const result = await reader.read();
          ensureCurrentApiSession(storage, token, signal);
          if (!result.done) timed.resetIdle();
          else finished = true;
          return result;
        },
      };
      try {
        for await (const event of parseTwoPassSse(guardedReader, timed.signal)) {
          ensureCurrentApiSession(storage, token, signal);
          yield event;
        }
      } catch (error) {
        if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
        if (timed.didTimeout()) throw new ApiError("timeout", "分镜生成等待超时，可查询任务状态后恢复。");
        throw error;
      } finally {
        if (!finished) {
          try {
            await reader.cancel();
          } catch {
            // The stream may already be closed or aborted.
          }
        }
        reader.releaseLock();
        timed.dispose();
      }
    },
    async getSubmission(idempotencyKey, signal) {
      if (idempotencyKey.trim() === "" || idempotencyKey.length > 255) {
        throw new ApiError("invalid-response", "生成请求标识无效。");
      }
      return parseTwoPassSubmission(await storyboardJsonRequest(
        "twoPassSubmission", signal, undefined, { idempotencyKey },
      ));
    },
    async getTask(taskId, signal) {
      const task = parseTwoPassTask(await storyboardJsonRequest("twoPassTask", signal, undefined, { taskId }));
      if (task.id !== taskId) throw new InvalidResponseError("服务器返回了其他分镜任务。");
      return task;
    },
  };

  return {
    mode: "api",
    apiBaseUrl,
    async restore(signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        return null;
      }
      try {
        const user = parseUser(await request("me", token, signal));
        if (signal.aborted) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        if (storage.getItem(API_TOKEN_KEY) !== token) {
          return null;
        }
        storage.setItem(API_USER_KEY, JSON.stringify(user));
        return user;
      } catch (error) {
        if (signal.aborted) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        if (error instanceof ApiError && error.status === 401) {
          if (storage.getItem(API_TOKEN_KEY) === token) {
            clearApiSession(storage);
          }
          return null;
        }
        throw error;
      }
    },
    async login(credentials, signal) {
      const response = parseLoginResponse(await request("login", null, signal, credentials));
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      saveAuthenticatedUser(response.token, response.user);
      return response.user;
    },
    async listMyTeams(signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseMyTeamList(await requestForCurrentApiSession("myTeams", token, signal));
    },
    async listSeries(signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseSeriesList(await requestForCurrentApiSession("series", token, signal));
    },
    async getSeries(seriesId, signal) {
      const token = requireApiToken(storage);
      const result = parseSeries(await requestForCurrentApiSession("seriesDetail", token, signal, undefined, { seriesId }));
      ensureCurrentApiSession(storage, token, signal);
      if (result.id !== seriesId) {
        throw new InvalidResponseError("服务器返回了其他剧集的详情。");
      }
      return result;
    },
    async createSeries(input, signal) {
      const token = requireApiToken(storage);
      const result = parseSeries(await requestForCurrentApiSession("createSeries", token, signal, input));
      ensureCurrentApiSession(storage, token, signal);
      return result;
    },
    async updateSeries(seriesId, update, signal) {
      const token = requireApiToken(storage);
      const result = parseSeries(await requestForCurrentApiSession("updateSeries", token, signal, update, { seriesId }));
      ensureCurrentApiSession(storage, token, signal);
      if (result.id !== seriesId) {
        throw new InvalidResponseError("服务器返回了其他剧集的详情。");
      }
      return result;
    },
    async deleteSeries(seriesId, signal) {
      const token = requireApiToken(storage);
      const result = await requestForCurrentApiSession("deleteSeries", token, signal, undefined, { seriesId });
      ensureCurrentApiSession(storage, token, signal);
      if (result !== undefined) {
        throw new InvalidResponseError("服务器删除剧集时返回了非预期内容。");
      }
    },
    async listSeriesStylePrompts(signal) {
      const token = requireApiToken(storage);
      const result = parseStylePromptChoices(await requestForCurrentApiSession("stylePrompts", token, signal));
      ensureCurrentApiSession(storage, token, signal);
      return result;
    },
    async uploadSeriesCover(file, signal) {
      const token = requireApiToken(storage);
      const form = new FormData();
      form.append("file", file);
      const result = parseUploadedSeriesCover(await requestForCurrentApiSession("uploadSeriesCover", token, signal, form));
      ensureCurrentApiSession(storage, token, signal);
      return result;
    },
    async listChapters(seriesId, signal) {
      const token = requireApiToken(storage);
      return parseChapterList(
        await requestForCurrentApiSession("chapters", token, signal, undefined, { seriesId }),
        seriesId,
      );
    },
    async createChapter(seriesId, input, signal) {
      const token = requireApiToken(storage);
      return parseChapterResponse(
        await requestForCurrentApiSession("createChapter", token, signal, input, { seriesId }),
        seriesId,
      );
    },
    async reorderChapters(seriesId, input, signal) {
      const token = requireApiToken(storage);
      const response = await requestForCurrentApiSession(
        "reorderChapters", token, signal, input, { seriesId },
      );
      return parseChapterOrderResponse(response);
    },
    async getChapterEditLock(chapterId, signal, leaseHandle) {
      const token = requireApiToken(storage);
      const lease = leaseHandle === undefined ? undefined : chapterEditLockLeases.get(leaseHandle);
      if (leaseHandle !== undefined && (lease === undefined || lease.chapterId !== chapterId || lease.token !== token)) {
        throw new ApiError("aborted", "登录会话已变化，未继续使用原章节锁。", null);
      }
      const state = parseChapterEditLockState(await requestForCurrentApiSession(
        "getChapterEditLock", token, signal, undefined, { chapterId },
      ));
      if (lease !== undefined && !recordCurrentChapterLockLease(lease, state)) {
        throw new ApiError("aborted", "章节锁无法与本次取得记录对应，未继续使用旧编辑句柄。", null);
      }
      return state;
    },
    async acquireChapterEditLock(chapterId, signal) {
      const token = requireApiToken(storage);
      const state = parseChapterEditLockState(await requestForCurrentApiSession(
        "acquireChapterEditLock", token, signal, undefined, { chapterId },
      ));
      if (!state.locked) return { state, lease: null };
      if (state.expires_at === undefined) {
        throw new ApiError("invalid-response", "章节锁取得结果缺少有效期限。", null);
      }
      const lease: ChapterEditLockLeaseHandle = {} as ChapterEditLockLeaseHandle;
      chapterEditLockLeases.set(lease, {
        chapterId,
        token,
        acquiredAt: state.acquired_at ?? null,
        userId: state.user_id ?? null,
        expiresAt: state.expires_at,
      });
      return { state, lease };
    },
    async releaseChapterEditLock(chapterId, signal, leaseHandle) {
      const token = requireApiToken(storage);
      const lease = chapterEditLockLeases.get(leaseHandle);
      if (lease === undefined || lease.chapterId !== chapterId || lease.token !== token) {
        throw new ApiError("aborted", "当前会话没有可安全释放的章节锁。");
      }
      if (lease.acquiredAt === null || lease.userId === null) {
        chapterEditLockLeases.delete(leaseHandle);
        throw new ApiError("aborted", "尚未记录本次锁的取得时间，未发送释放请求。", null);
      }
      const current = parseChapterEditLockState(await requestForCurrentApiSession(
        "getChapterEditLock", token, signal, undefined, { chapterId },
      ));
      const normalizedExpiry = current.expires_at === undefined
        ? Number.NaN
        : Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(current.expires_at)
          ? current.expires_at : current.expires_at + "Z");
      if (!recordCurrentChapterLockLease(lease, current)
        || !Number.isFinite(normalizedExpiry) || normalizedExpiry <= Date.now()) {
        throw new ApiError("aborted", "章节锁已变化或过期，未释放当前锁。", null);
      }
      const response = await requestForCurrentApiSession(
        "releaseChapterEditLock", token, signal, undefined, { chapterId },
      );
      const parsed = parseChapterOrderResponse(response);
      chapterEditLockLeases.delete(leaseHandle);
      return parsed;
    },
    async updateChapter(seriesId, chapterId, update, signal) {
      const token = requireApiToken(storage);
      const chapter = await requestForCurrentApiSession(
        "updateChapter",
        token,
        signal,
        update,
        { seriesId, chapterId },
      );
      const parsed = parseChapterResponse(chapter, seriesId, chapterId);
      ensureCurrentApiSession(storage, token, signal);
      return parsed;
    },
    async deleteChapter(seriesId, chapterId, signal) {
      const token = requireApiToken(storage);
      const result = await requestForCurrentApiSession(
        "deleteChapter",
        token,
        signal,
        undefined,
        { seriesId, chapterId },
      );
      if (result !== undefined) {
        throw new InvalidResponseError("服务器删除章节时返回了非预期内容。");
      }
    },
    async listStoryboardAssets(seriesId, chapterId, signal) {
      const token = requireApiToken(storage);
      return parseStoryboardAssetList(await requestForCurrentApiSession(
        "storyboardAssets",
        token,
        signal,
        undefined,
        { seriesId, chapterId },
      ));
    },
    async listCharacters(seriesId, signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseCharacterList(
        await request("characters", token, signal, undefined, { seriesId }),
        seriesId,
      );
    },
    async listScenes(seriesId, signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseSceneList(
        await request("scenes", token, signal, undefined, { seriesId }),
        seriesId,
      );
    },
    async listProps(seriesId, signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parsePropList(
        await request("props", token, signal, undefined, { seriesId }),
        seriesId,
      );
    },
    async createCharacter(seriesId, input, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("createCharacter", token, signal, input, { seriesId });
      const character = parseCreatedCharacter(value, seriesId);
      ensureCurrentApiSession(storage, token, signal);
      return character;
    },
    async updateCharacter(seriesId, assetId, update, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("updateCharacter", token, signal, update, { assetId });
      const character = parseUpdatedCharacter(value, seriesId, assetId);
      ensureCurrentApiSession(storage, token, signal);
      return character;
    },
    async deleteCharacter(assetId, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("deleteCharacter", token, signal, undefined, { assetId });
      if (value !== undefined) {
        throw new InvalidResponseError("服务器删除角色时返回了非预期内容。");
      }
    },
    async createScene(seriesId, input, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("createScene", token, signal, input, { seriesId });
      const scene = parseCreatedScene(value, seriesId);
      ensureCurrentApiSession(storage, token, signal);
      return scene;
    },
    async updateScene(seriesId, assetId, update, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("updateScene", token, signal, update, { assetId });
      const scene = parseUpdatedScene(value, seriesId, assetId);
      ensureCurrentApiSession(storage, token, signal);
      return scene;
    },
    async deleteScene(assetId, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("deleteScene", token, signal, undefined, { assetId });
      if (value !== undefined) {
        throw new InvalidResponseError("服务器删除场景时返回了非预期内容。");
      }
    },
    async createProp(seriesId, input, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("createProp", token, signal, input, { seriesId });
      const prop = parseCreatedProp(value, seriesId);
      ensureCurrentApiSession(storage, token, signal);
      return prop;
    },
    async updateProp(seriesId, assetId, update, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("updateProp", token, signal, update, { assetId });
      const prop = parseUpdatedProp(value, seriesId, assetId);
      ensureCurrentApiSession(storage, token, signal);
      return prop;
    },
    async deleteProp(assetId, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("deleteProp", token, signal, undefined, { assetId });
      if (value !== undefined) {
        throw new InvalidResponseError("服务器删除道具时返回了非预期内容。");
      }
    },
    async getPersonalProductionNotes(chapterId, signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parsePersonalProductionSnapshot(
        await request("personalProductionNotes", token, signal, undefined, { chapterId }),
        chapterId,
      );
    },
    async savePersonalProductionNote(chapterId, updateValue, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      const update = parsePersonalProductionNoteUpdate(updateValue);
      let safeBaseUrl: string;
      try {
        const pageUrl = typeof window === "undefined" ? apiBaseUrl : window.location.href;
        safeBaseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);
      } catch (error) {
        const message = error instanceof ApiConfigurationError
          ? error.message
          : "当前配置不允许发送保存请求。";
        throw new ApiError("invalid-response", message);
      }

      try {
        const payload = await request(
          "savePersonalProductionNote",
          token,
          signal,
          update,
          { chapterId },
          safeBaseUrl,
        );
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        return parsePersonalProductionSnapshot(payload, chapterId);
      } catch (error) {
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        throw error;
      }
    },
    async savePersonalProductionResume(chapterId, updateValue, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      const update = parsePersonalProductionResumeUpdate(updateValue);
      let safeBaseUrl: string;
      try {
        const pageUrl = typeof window === "undefined" ? apiBaseUrl : window.location.href;
        safeBaseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);
      } catch (error) {
        const message = error instanceof ApiConfigurationError
          ? error.message
          : "当前配置不允许发送保存请求。";
        throw new ApiError("invalid-response", message);
      }

      try {
        const payload = await request(
          "savePersonalProductionResume",
          token,
          signal,
          update,
          { chapterId },
          safeBaseUrl,
        );
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        return parsePersonalProductionSnapshot(payload, chapterId);
      } catch (error) {
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        throw error;
      }
    },
    async getPersonalRoughCut(chapterId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      try {
        const payload = await request("personalRoughCut", token, signal, undefined, { chapterId });
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        return parsePersonalRoughCutSnapshot(payload, chapterId);
      } catch (error) {
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        throw error;
      }
    },
    async savePersonalRoughCut(chapterId, updateValue, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      const update = parsePersonalRoughCutUpdate(updateValue);
      let safeBaseUrl: string;
      try {
        const pageUrl = typeof window === "undefined" ? apiBaseUrl : window.location.href;
        safeBaseUrl = resolveApiBaseUrl(apiBaseUrl, pageUrl);
      } catch (error) {
        const message = error instanceof ApiConfigurationError
          ? error.message
          : "当前配置不允许发送保存请求。";
        throw new ApiError("invalid-response", message);
      }

      try {
        const payload = await request(
          "savePersonalRoughCut",
          token,
          signal,
          update,
          { chapterId },
          safeBaseUrl,
        );
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        return parsePersonalRoughCutSaveResponse(payload, chapterId, update);
      } catch (error) {
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        throw error;
      }
    },
    async listMyTasks(page, signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseMyTaskPage(
        await request("myTasks", token, signal, undefined, { page }),
        page,
      );
    },
    async getTaskReceipt(taskId, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("twoPassTask", token, signal, undefined, { taskId });
      const receipt = parseTaskReceipt(value, taskId);
      ensureCurrentApiSession(storage, token, signal);
      return receipt;
    },
    async getTaskRequest(taskId, signal) {
      const token = requireApiToken(storage);
      const value = await requestForCurrentApiSession("taskRequest", token, signal, undefined, { taskId });
      const requestRecord = parseTaskRequestRecord(value, taskId);
      ensureCurrentApiSession(storage, token, signal);
      return requestRecord;
    },
    storyboardGeneration,
    async exportSeries(seriesId, signal) {
      const token = requireApiToken(storage);
      return requestForCurrentApiSession("exportSeries", token, signal, undefined, { seriesId });
    },
    async importSeries(input, signal) {
      const token = requireApiToken(storage);
      const payload = await requestForCurrentApiSession("importSeries", token, signal, input);
      return parseImportSeriesResponse(payload);
    },
    logout() {
      clearApiSession(storage);
    },
  };
}

export function createWorkspaceServices(
  configuration: { mode: "demo" } | { mode: "api"; apiBaseUrl: string },
  options: ServiceOptions = {},
): WorkspaceServices {
  if (configuration.mode === "demo") {
    return createDemoServices(options.storage);
  }
  return createApiServices(configuration.apiBaseUrl, options);
}

export function createServicesFromEnvironment(
  options: ServiceOptions = {},
): WorkspaceServices {
  const pageUrl = window.location.href;
  const config = readEnvironmentConfiguration(import.meta.env, pageUrl);
  if (config.mode === "demo") {
    return createDemoServices(options.storage);
  }
  if (!config.apiBaseUrl) {
    throw new ApiConfigurationError("API 地址未配置。");
  }
  return createApiServices(config.apiBaseUrl, options);
}

export function isCancelled(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}

export function normalizeApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (error instanceof InvalidResponseError) {
    return new ApiError("invalid-response", error.message);
  }
  return new ApiError("network", "发生意外错误，请重试。");
}
