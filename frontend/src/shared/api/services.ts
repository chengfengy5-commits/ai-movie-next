import { demoSeries } from "../../features/series/demoSeries";
import { demoTeams } from "../../features/series/demoTeams";
import { getDemoChapterData } from "../../features/chapters/demoChapters";
import { getDemoAssetLibraryData } from "../../features/assets/demoAssets";
import { getDemoPersonalProductionNotes } from "../../features/chapters/demoPersonalProductionNotes";
import { clonePersonalRoughCutSnapshot, getDemoPersonalRoughCut } from "../../features/chapters/demoPersonalRoughCut";
import { ApiConfigurationError, readEnvironmentConfiguration, resolveApiBaseUrl, type AppMode } from "./config";
import { ApiError, isAbortError } from "./errors";
import {
  InvalidResponseError,
  parseChapterList,
  parseCharacterList,
  parseErrorDetail,
  parseLoginResponse,
  parsePropList,
  parseSeriesList,
  parseSceneList,
  parseStoryboardAssetList,
  parseUser,
  type Character,
  type Chapter,
  type Credentials,
  type Prop,
  type Scene,
  type Series,
  type StoryboardAsset,
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
import { getDemoMyTaskPage } from "../../features/tasks/demoTasks";
import { MY_TASK_PAGE_SIZE, parseMyTaskPage, type MyTaskPage } from "./myTasks";
import { parseMyTeamList, type MyTeam } from "./teams";
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
  listMyTeams(signal: AbortSignal): Promise<MyTeam[]>;
  listChapters(seriesId: string, signal: AbortSignal): Promise<Chapter[]>;
  listStoryboardAssets(
    seriesId: string,
    chapterId: string,
    signal: AbortSignal,
  ): Promise<StoryboardAsset[]>;
  listCharacters(seriesId: string, signal: AbortSignal): Promise<Character[]>;
  listScenes(seriesId: string, signal: AbortSignal): Promise<Scene[]>;
  listProps(seriesId: string, signal: AbortSignal): Promise<Prop[]>;
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

function cloneDemoNoteValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(cloneDemoNoteValue);
  }
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, cloneDemoNoteValue(entry)]));
  }
  return value;
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
  const personalNoteSnapshots = new Map<string, PersonalProductionSnapshot>();
  const personalRoughCutSnapshots = new Map<string, PersonalRoughCutSnapshot>();

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
      return demoSeries.map((series) => ({ ...series }));
    },
    async listChapters(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = getDemoChapterData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      return data.chapters.map((chapter) => ({
        ...chapter,
        content: chapter.content?.map((frame) => ({ ...frame })) ?? null,
        lock: chapter.lock === null ? null : { ...chapter.lock },
      }));
    },
    async listStoryboardAssets(seriesId, chapterId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = getDemoChapterData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      return (data.assetsByChapter[chapterId] ?? []).map((asset) => ({ ...asset }));
    },
    async listCharacters(seriesId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      const data = getDemoAssetLibraryData(seriesId);
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
      const data = getDemoAssetLibraryData(seriesId);
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
      const data = getDemoAssetLibraryData(seriesId);
      if (data === null) {
        throw demoNotFound();
      }
      return data.props.map((prop) => ({
        ...prop,
        aliases: prop.aliases === null ? null : [...prop.aliases],
      }));
    },
    async getPersonalProductionNotes(chapterId, signal) {
      if (signal.aborted) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      ensureDemoSession(storage);
      for (const series of demoSeries) {
        const data = getDemoChapterData(series.id);
        const chapter = data?.chapters.find((candidate) => candidate.id === chapterId);
        if (data !== null && data !== undefined && chapter !== undefined) {
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
      for (const series of demoSeries) {
        const data = getDemoChapterData(series.id);
        const chapter = data?.chapters.find((candidate) => candidate.id === chapterId);
        if (data === null || data === undefined || chapter === undefined) {
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
      for (const series of demoSeries) {
        const data = getDemoChapterData(series.id);
        const chapter = data?.chapters.find((candidate) => candidate.id === chapterId);
        if (data === null || data === undefined || chapter === undefined) {
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
            || chapter.series_id !== series.id
            || frameIndex === undefined
            || asset === undefined
            || asset.chapter_id !== chapter.id
            || asset.series_id !== series.id
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
      for (const series of demoSeries) {
        const data = getDemoChapterData(series.id);
        const chapter = data?.chapters.find((candidate) => candidate.id === chapterId);
        if (data !== null && data !== undefined && chapter !== undefined) {
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
      for (const series of demoSeries) {
        const data = getDemoChapterData(series.id);
        const chapter = data?.chapters.find((candidate) => candidate.id === chapterId);
        if (data === null || data === undefined || chapter === undefined) {
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
  | "chapters"
  | "storyboardAssets"
  | "characters"
  | "scenes"
  | "props"
  | "personalProductionNotes"
  | "savePersonalProductionNote"
  | "savePersonalProductionResume"
  | "personalRoughCut"
  | "savePersonalRoughCut"
  | "myTasks"
  | "myTeams";

interface ApiRequestDefinition {
  path: string;
  method: "GET" | "POST" | "PUT";
}

function apiRequestDefinition(
  endpoint: ApiEndpoint,
  identifiers?: { seriesId?: string; chapterId?: string; page?: number },
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
  if (endpoint === "myTeams") {
    return { path: "/teams/my", method: "GET" };
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

  async function request(
    endpoint: ApiEndpoint,
    token: string | null,
    signal: AbortSignal,
    body?: Credentials | PersonalProductionNoteUpdate | PersonalProductionResumeUpdate | PersonalRoughCutUpdate,
    identifiers?: { seriesId?: string; chapterId?: string; page?: number },
    requestBaseUrl = apiBaseUrl,
  ): Promise<unknown> {
    const requestDefinition = apiRequestDefinition(endpoint, identifiers);
    const url = new URL(`${requestBaseUrl}${requestDefinition.path}`);
    const timed = timeoutSignal(signal, timeoutMs);
    const headers = new Headers({ Accept: "application/json" });
    if (body !== undefined) {
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
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
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
      return parseMyTeamList(await request("myTeams", token, signal));
    },
    async listSeries(signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseSeriesList(await request("series", token, signal));
    },
    async listChapters(seriesId, signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseChapterList(
        await request("chapters", token, signal, undefined, { seriesId }),
        seriesId,
      );
    },
    async listStoryboardAssets(seriesId, chapterId, signal) {
      const token = storage.getItem(API_TOKEN_KEY);
      if (!token) {
        throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
      }
      return parseStoryboardAssetList(await request(
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
