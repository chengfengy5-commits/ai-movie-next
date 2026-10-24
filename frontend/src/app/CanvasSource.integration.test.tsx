import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../features/auth/AuthProvider";
import { demoSeries } from "../features/series/demoSeries";
import type { Character, Chapter, Prop, Scene, Series, StoryboardAsset, User } from "../shared/api/contracts";
import { API_TOKEN_KEY } from "../shared/api/storage";
import { createApiServices } from "../shared/api/services";
import { Workspace } from "./Workspace";

const apiBaseUrl = "http://127.0.0.1:4175/api";
const token = "canvas-source-workspace-token";
const initialVersion = "1111111111111111111111111111111111111111111111111111111111111111";
const updatedVersion = "2222222222222222222222222222222222222222222222222222222222222222";
const insertedSourceVersion = "3333333333333333333333333333333333333333333333333333333333333333";
const undoneSourceVersion = "4444444444444444444444444444444444444444444444444444444444444444";
const insertedFrameId = "46e667ea-c192-4d6a-a096-be538d3a9762";
const creationReceiptToken = "synthetic-opaque-creation-proof";
const createdCharacterId = "7fcd0a88-7a5f-445e-9b5d-3d6998f24ce1";
const createdSceneId = "scene-created-id";
const createdPropId = "prop-created-id";
const initialCharacterId = "character-existing-id";
const user: User = {
  id: "canvas-source-user",
  username: "画布作者",
  email: "canvas-source@example.invalid",
  is_superuser: false,
  membership_type: "free",
  membership_expires_at: null,
  avatar_url: null,
  bio: null,
  created_at: "2026-10-01T00:00:00Z",
};

const personalSeries: Series = {
  ...demoSeries[0]!,
  id: "canvas-source-series",
  user_id: user.id,
  name: "画布来源集成剧",
  team_id: null,
  team_name: null,
  owner_name: user.username,
  can_enter: true,
};

const initialChapter: Chapter = {
  id: "canvas-source-chapter",
  series_id: personalSeries.id,
  title: "第一章",
  content: [{ text: "雨夜，主角推开旧书店的门。", storyboard: ["board-a"], custom: "保留字段" }],
  order: 0,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  lock: null,
};

const initialCharacter: Character = {
  id: initialCharacterId,
  series_id: personalSeries.id,
  name: "小满",
  gender: null,
  age: null,
  role: "主人公",
  appearance: null,
  description: null,
  image_url: null,
  audio_url: null,
  voice_ref: null,
  aliases: [],
  canonical_key: "小满",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};
const initialScene: Scene = {
  id: "scene-existing-id",
  series_id: personalSeries.id,
  title: "旧书店",
  description: null,
  image_url: null,
  aliases: [],
  canonical_key: "旧书店",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};
const initialProp: Prop = {
  id: "prop-existing-id",
  series_id: personalSeries.id,
  name: "铜钥匙",
  description: null,
  image_url: null,
  aliases: [],
  canonical_key: "铜钥匙",
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};

type LockScenario = "personal" | "team-acquire" | "team-other" | "team-expired";

interface RecordedRequest {
  url: URL;
  method: string;
  body: unknown;
  headers: Headers;
}

function jsonResponse(payload: unknown, status = 200): Response {
  return Response.json(payload, { status });
}

function makeSourceSnapshot(
  series: Series,
  chapter: Chapter,
  version: string,
  requiresLock: boolean,
  characters: Character[] = [],
  scenes: Scene[] = [],
  props: Prop[] = [],
  extraStoryboardAssets: StoryboardAsset[] = [],
) {
  const storyboardAssets = (chapter.content ?? []).flatMap((frame, frameIndex) => {
    const ids = Array.isArray(frame.storyboard)
      ? frame.storyboard.filter((id): id is string => typeof id === "string" && id.trim() !== "")
      : [];
    const description = typeof frame.text === "string" ? frame.text : "";
    return ids.map((id) => ({
      id,
      series_id: series.id,
      chapter_id: chapter.id,
      frame_index: frameIndex,
      name: `分镜 ${frameIndex + 1}`,
      description,
      image_url: null,
      created_at: "2026-10-01T00:00:00Z",
      updated_at: "2026-10-01T00:00:00Z",
    }));
  });
  return {
    capability: "canvas-source-v1",
    series_id: series.id,
    chapter_id: chapter.id,
    version,
    requires_lock: requiresLock,
    chapter,
    storyboard_assets: [...storyboardAssets, ...extraStoryboardAssets],
    characters,
    scenes,
    props,
  };
}

interface WorkspaceHarnessOptions {
  chapterContent?: Chapter["content"];
  initialCanvasDocument?: Record<string, unknown>;
  characters?: Character[];
  scenes?: Scene[];
  props?: Prop[];
  loseNextSourcePostReply?: boolean;
  sourceReadFailureStatus?: number;
  sourcePostFailureStatus?: number;
  lockReadFailureStatus?: number;
  undoFailureStatus?: number;
  extraStoryboardAssets?: StoryboardAsset[];
}

function createWorkspaceHarness(lockScenario: LockScenario, options: WorkspaceHarnessOptions = {}) {
  const userEvents = userEvent.setup();
  const series: Series = lockScenario === "personal"
    ? personalSeries
    : { ...personalSeries, team_id: "canvas-source-team", team_name: "画布来源团队" };
  const chapter: Chapter = {
    ...initialChapter,
    series_id: series.id,
    content: options.chapterContent ?? initialChapter.content,
  };
  const requiresLock = lockScenario !== "personal";
  const state = {
    sourceVersion: initialVersion,
    chapterContent: chapter.content,
    characters: options.characters ?? [],
    scenes: options.scenes ?? [],
    props: options.props ?? [],
    extraStoryboardAssets: options.extraStoryboardAssets ?? [],
    canvasId: null as string | null,
    canvasVersion: 1,
    canvasDocument: options.initialCanvasDocument
      ?? { version: 1, viewport: { x: 0, y: 0, zoom: 1 }, nodes: [], edges: [], notes: [] },
    lockReads: 0,
    lockAcquired: false,
    creationToken: null as string | null,
    loseNextSourcePostReply: options.loseNextSourcePostReply ?? false,
    sourceReadFailureStatus: options.sourceReadFailureStatus ?? null,
    sourcePostFailureStatus: options.sourcePostFailureStatus ?? null,
    lockReadFailureStatus: options.lockReadFailureStatus ?? null,
    undoFailureStatus: options.undoFailureStatus ?? null,
  };
  const requests: RecordedRequest[] = [];
  const fetcher = vi.fn<typeof fetch>(async (input, init = {}) => {
    const url = new URL(String(input));
    const method = init.method ?? "GET";
    const body = init.body === undefined ? null : JSON.parse(String(init.body)) as unknown;
    requests.push({ url, method, body, headers: new Headers(init.headers) });

    if (url.pathname === "/api/auth/me") return jsonResponse(user);
    if (url.pathname === "/api/series" && method === "GET") return jsonResponse([series]);
    if (url.pathname === `/api/series/${series.id}/chapters` && method === "GET") {
      return jsonResponse([{ ...chapter, content: state.chapterContent }]);
    }
    if (url.pathname === `/api/chapters/${chapter.id}/canvas-source` && method === "GET") {
      if (state.sourceReadFailureStatus !== null) {
        const status = state.sourceReadFailureStatus;
        state.sourceReadFailureStatus = null;
        return jsonResponse({ detail: "合成来源读取被拒绝。" }, status);
      }
      return jsonResponse(makeSourceSnapshot(
        series,
        { ...chapter, content: state.chapterContent },
        state.sourceVersion,
        requiresLock,
        state.characters,
        state.scenes,
        state.props,
        state.extraStoryboardAssets,
      ));
    }
    if (url.pathname === `/api/chapters/${chapter.id}/canvas-source` && method === "POST") {
      const request = body as {
        request_id: string;
        expected_series_id: string;
        expected_version: string;
        command: {
          type: string;
          target?: { index: number; storyboard_id: string | null };
          after?: { index: number; storyboard_id: string | null } | null;
          frame?: Record<string, unknown>;
          text?: string;
          kind?: "character" | "scene" | "prop";
          id?: string;
          values?: Record<string, string>;
          field?: "preview" | "video";
          slot?: number | null;
          value?: string;
          receipt?: string;
        };
      };
      if (request.command.type === "creation.undo" && state.undoFailureStatus !== null) {
        const status = state.undoFailureStatus;
        state.undoFailureStatus = null;
        return jsonResponse({ detail: "合成撤销被拒绝。" }, status);
      }
      if (state.sourcePostFailureStatus !== null) {
        const status = state.sourcePostFailureStatus;
        state.sourcePostFailureStatus = null;
        return jsonResponse({ detail: "合成来源写入被拒绝。" }, status);
      }
      if (request.expected_series_id !== series.id || request.expected_version !== state.sourceVersion) {
        return jsonResponse({ detail: "章节来源版本已变化。" }, 409);
      }
      let creationReceipt: unknown = null;
      if (request.command.type === "frame.update" && request.command.target?.index === 0
        && request.command.text !== undefined
        && (() => {
          const storyboard = state.chapterContent?.[0]?.storyboard;
          const first = Array.isArray(storyboard) ? storyboard[0] : undefined;
          const expectedId = typeof first === "string" && first.trim() !== "" ? first
            : storyboard === undefined || Array.isArray(storyboard) && storyboard.every((reference) => (
              reference === null || typeof reference === "string" && reference.trim() === ""
            )) ? null : undefined;
          return expectedId !== undefined && request.command.target?.storyboard_id === expectedId;
        })()) {
        const frames = [...(state.chapterContent ?? [])];
        frames[0] = { ...frames[0]!, text: request.command.text };
        state.chapterContent = frames;
      } else if (request.command.type === "frame.insert" && request.command.after === null
        && request.command.frame !== undefined && state.creationToken === null) {
        const insertedFrame = { ...request.command.frame, storyboard: [insertedFrameId] };
        state.chapterContent = [insertedFrame, ...(state.chapterContent ?? [])];
        state.creationToken = creationReceiptToken;
        creationReceipt = {
          token: creationReceiptToken,
          kind: "frame",
          target: { id: insertedFrameId, storyboard_id: insertedFrameId, index: 0 },
        };
      } else if (request.command.type === "frame.delete" && request.command.target?.storyboard_id === "board-b"
        && request.command.target.index === 1) {
        const frames = [...(state.chapterContent ?? [])];
        frames.splice(request.command.target.index, 1);
        state.chapterContent = frames;
      } else if (request.command.type === "asset.create" && request.command.values !== undefined) {
        const values = request.command.values;
        const id = request.command.kind === "character" ? createdCharacterId
          : request.command.kind === "scene" ? createdSceneId : createdPropId;
        if (request.command.kind === "character" && state.characters.some((item) => item.id === id)
          || request.command.kind === "scene" && state.scenes.some((item) => item.id === id)
          || request.command.kind === "prop" && state.props.some((item) => item.id === id)) {
          return jsonResponse({ detail: "合成素材 ID 已存在。" }, 409);
        }
        if (request.command.kind === "character") {
          state.characters = [...state.characters, {
            ...initialCharacter,
            id,
            series_id: series.id,
            name: values.name ?? "",
            role: values.role ?? null,
            image_url: values.image_url ?? null,
          }];
        } else if (request.command.kind === "scene") {
          state.scenes = [...state.scenes, {
            id,
            series_id: series.id,
            title: values.title ?? "",
            description: values.description ?? null,
            image_url: values.image_url ?? null,
            aliases: [],
            canonical_key: values.title ?? null,
            created_at: "2026-10-01T00:00:00Z",
            updated_at: "2026-10-01T00:00:00Z",
          }];
        } else {
          state.props = [...state.props, {
            id,
            series_id: series.id,
            name: values.name ?? "",
            description: values.description ?? null,
            image_url: values.image_url ?? null,
            aliases: [],
            canonical_key: values.name ?? null,
            created_at: "2026-10-01T00:00:00Z",
            updated_at: "2026-10-01T00:00:00Z",
          }];
        }
        state.creationToken = `synthetic-opaque-${request.command.kind}-proof`;
        creationReceipt = {
          token: state.creationToken,
          kind: request.command.kind,
          target: { id },
        };
      } else if (request.command.type === "asset.update"
        && request.command.id !== undefined && request.command.values !== undefined) {
        if (request.command.kind === "character") {
          state.characters = state.characters.map((item) => item.id === request.command.id
            ? { ...item, ...request.command.values }
            : item);
        } else if (request.command.kind === "scene") {
          state.scenes = state.scenes.map((item) => item.id === request.command.id
            ? { ...item, ...request.command.values }
            : item);
        } else if (request.command.kind === "prop") {
          state.props = state.props.map((item) => item.id === request.command.id
            ? { ...item, ...request.command.values }
            : item);
        }
      } else if (request.command.type === "media.update" && request.command.target?.storyboard_id === "board-a"
        && request.command.target.index === 0 && request.command.value !== undefined) {
        const frames = [...(state.chapterContent ?? [])];
        const current = frames[0] as Record<string, unknown>;
        if (request.command.field === "preview" && request.command.slot === null) {
          frames[0] = { ...current, preview: request.command.value };
        } else if (request.command.field === "video" && request.command.slot !== null
          && request.command.slot !== undefined
          && Number.isSafeInteger(request.command.slot) && Array.isArray(current.videos)
          && request.command.slot >= 0 && request.command.slot < current.videos.length) {
          const videos = [...current.videos];
          videos[request.command.slot] = request.command.value;
          frames[0] = { ...current, videos };
        } else {
          return jsonResponse({ detail: "媒体槽位与当前章节来源不匹配。" }, 409);
        }
        state.chapterContent = frames;
      } else if (request.command.type === "media.append" && request.command.target?.storyboard_id === "board-a"
        && request.command.target.index === 0 && request.command.value !== undefined) {
        const frames = [...(state.chapterContent ?? [])];
        const current = frames[0] as Record<string, unknown>;
        const videos = Array.isArray(current.videos) ? [...current.videos] : [];
        const slot = videos.length;
        videos.push(request.command.value);
        frames[0] = { ...current, videos };
        state.chapterContent = frames;
        state.creationToken = "synthetic-opaque-video-proof";
        creationReceipt = {
          token: state.creationToken,
          kind: "video",
          target: { storyboard_id: "board-a", index: 0, slot, value: request.command.value },
        };
      } else if (request.command.type === "creation.undo" && request.command.receipt === state.creationToken) {
        state.chapterContent = (state.chapterContent ?? []).filter((frame) => (
          !Array.isArray(frame.storyboard) || !frame.storyboard.includes(insertedFrameId)
        ));
        state.creationToken = null;
      } else {
        return jsonResponse({ detail: "合成服务未识别此来源操作。" }, 422);
      }
      state.sourceVersion = request.command.type === "frame.update" ? updatedVersion
        : request.command.type === "frame.insert" ? insertedSourceVersion
          : request.command.type === "creation.undo" ? undoneSourceVersion
            : request.command.type === "frame.delete" ? "5555555555555555555555555555555555555555555555555555555555555555"
              : request.command.type === "asset.create" ? "6666666666666666666666666666666666666666666666666666666666666666"
                : request.command.type === "asset.update" ? "7777777777777777777777777777777777777777777777777777777777777777"
                  : request.command.type === "media.append" ? "8888888888888888888888888888888888888888888888888888888888888888"
                    : "9999999999999999999999999999999999999999999999999999999999999999";
      const responsePayload = {
        capability: "canvas-source-v1",
        request_id: request.request_id,
        command_type: request.command.type,
        snapshot: makeSourceSnapshot(
          series,
          { ...chapter, content: state.chapterContent },
          state.sourceVersion,
          requiresLock,
          state.characters,
          state.scenes,
          state.props,
          state.extraStoryboardAssets,
        ),
        creation_receipt: creationReceipt,
      };
      if (state.loseNextSourcePostReply) {
        state.loseNextSourcePostReply = false;
        throw new TypeError("Synthetic response lost after server-side commit.");
      }
      return jsonResponse(responsePayload);
    }
    if (url.pathname === `/api/chapters/${chapter.id}/lock`) {
      if (lockScenario === "personal") {
        throw new Error("个人章节不应查询或取得章节锁。");
      }
      if (method === "POST" && lockScenario === "team-acquire") {
        state.lockAcquired = true;
        return jsonResponse({
          locked: true,
          username: user.username,
          expires_at: "2099-10-24T00:00:00Z",
        });
      }
      if (method === "POST" && lockScenario === "team-expired") {
        state.lockAcquired = true;
        return jsonResponse({
          locked: true,
          username: user.username,
          expires_at: "2099-10-24T00:00:00Z",
        });
      }
      if (method === "GET") {
        if (state.lockReadFailureStatus !== null) {
          const status = state.lockReadFailureStatus;
          state.lockReadFailureStatus = null;
          return jsonResponse({ detail: "合成章节锁读取被拒绝。" }, status);
        }
        state.lockReads += 1;
        if ((lockScenario === "team-acquire" || lockScenario === "team-expired") && !state.lockAcquired) {
          return jsonResponse({ locked: false, message: "未取得章节锁" });
        }
        if (lockScenario === "team-other") {
          return jsonResponse({
            locked: true,
            user_id: "another-team-member",
            username: "其他成员",
            is_mine: false,
            expires_at: "2099-10-24T00:00:00Z",
          });
        }
        if (lockScenario === "team-expired") {
          return jsonResponse({
            locked: true,
            user_id: user.id,
            username: user.username,
            is_mine: true,
            expires_at: "2000-01-01T00:00:00Z",
          });
        }
        return jsonResponse({
          locked: true,
          user_id: user.id,
          username: user.username,
          is_mine: true,
          acquired_at: "2026-10-24T00:00:00Z",
          expires_at: "2099-10-24T00:00:00Z",
        });
      }
    }
    if (url.pathname === `/api/chapters/${chapter.id}/canvas` && method === "GET") {
      return jsonResponse({
        id: state.canvasId,
        chapter_id: chapter.id,
        version: state.canvasVersion,
        document_json: state.canvasDocument,
        updated_by: state.canvasId === null ? null : user.id,
        updated_at: state.canvasId === null ? null : "2026-10-24T12:30:00Z",
        save_capability: "guarded-v1",
      });
    }
    if (url.pathname === `/api/chapters/${chapter.id}/canvas` && method === "PUT") {
      const request = body as {
        document_json: Record<string, unknown>;
        save_guard: { id: string | null; version: number };
      };
      state.canvasId ??= "canvas-source-layout-a";
      state.canvasVersion = request.save_guard.id === null ? 1 : request.save_guard.version + 1;
      state.canvasDocument = request.document_json;
      return jsonResponse({
        id: state.canvasId,
        chapter_id: chapter.id,
        version: state.canvasVersion,
        document_json: state.canvasDocument,
        updated_by: user.id,
        updated_at: "2026-10-24T12:30:00Z",
        save_receipt: { mode: "guarded-v1", id: state.canvasId, version: state.canvasVersion },
      });
    }
    if (url.pathname === `/api/series/${series.id}/storyboard-assets` && method === "GET") {
      return jsonResponse([]);
    }
    if (["characters", "scenes", "props"].some((name) => url.pathname.endsWith(`/${name}`))
      && method === "GET") return jsonResponse([]);
    if (url.pathname === "/api/teams/my" && method === "GET") return jsonResponse([]);

    throw new Error(`Unexpected request: ${method} ${url.pathname}`);
  });

  window.localStorage.clear();
  window.localStorage.setItem(API_TOKEN_KEY, token);
  const services = createApiServices(apiBaseUrl, { storage: window.localStorage, fetcher });
  const view = render(
    <AuthProvider services={services}>
      <Workspace />
    </AuthProvider>,
  );
  return { userEvents, requests, state, view };
}

async function openCanvas(userEvents: ReturnType<typeof userEvent.setup>) {
  const canvasEntry = screen.queryByRole("button", { name: "创作画布" });
  if (canvasEntry === null) {
    const seriesEntry = await screen.findByRole("button", {
      name: `只读查看章节：${personalSeries.name}`,
    });
    await userEvents.click(seriesEntry);
  }
  const chapterRegion = await screen.findByRole("region", { name: personalSeries.name });
  await userEvents.click(within(chapterRegion).getByRole("button", { name: "创作画布" }));
  const region = await screen.findByRole("region", { name: "画布工作区" });
  const dialog = region.closest('[role="dialog"]');
  if (!(dialog instanceof HTMLElement)) throw new Error("Canvas dialog was not rendered.");
  return { region, dialog, canvas: within(dialog) };
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("canvas source workspace integration", () => {
  it.each([
    {
      label: "a unique first storyboard ID with extra references",
      storyboard: ["board-a", "legacy-reference"],
      expectedStoryboardId: "board-a",
      extraStoryboardAssets: [] as StoryboardAsset[],
    },
    {
      label: "an all-empty reference array beside an orphan row at the same index",
      storyboard: [null, ""],
      expectedStoryboardId: null,
      extraStoryboardAssets: [{
        id: "orphan-board",
        series_id: personalSeries.id,
        chapter_id: initialChapter.id,
        frame_index: 0,
        name: "未绑定分镜素材",
        description: null,
        image_url: null,
        created_at: "2026-10-01T00:00:00Z",
        updated_at: "2026-10-01T00:00:00Z",
      }],
    },
  ])("sends the exact backend frame target for $label through the authenticated route", async ({
    storyboard, expectedStoryboardId, extraStoryboardAssets,
  }) => {
    const chapterContent: Chapter["content"] = [{
      text: "原始分镜",
      storyboard: storyboard as unknown as string[],
      custom_frame_field: { retain: true },
    }];
    const { userEvents, requests, state } = createWorkspaceHarness("personal", {
      chapterContent,
      extraStoryboardAssets,
    });
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "更新后的分镜" } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    await canvas.findByText("章节来源写入已获服务器回执，独立读取也确认了同一版本。");
    const sourcePost = requests.find(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`);
    expect(sourcePost?.body).toMatchObject({
      expected_series_id: personalSeries.id,
      expected_version: initialVersion,
      command: { type: "frame.update", target: { index: 0, storyboard_id: expectedStoryboardId } },
    });
    expect(state.chapterContent?.[0]).toMatchObject({
      text: "更新后的分镜",
      custom_frame_field: { retain: true },
      storyboard: storyboard as unknown as string[],
    });
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
  });

  it("keeps proven preview and duplicate-video bindings through the authenticated source route, save, and reopen", async () => {
    const oldPreview = `media:${initialVersion}:frame:board-a:preview`;
    const oldVideo0 = `media:${initialVersion}:frame:board-a:video:0`;
    const oldVideo1 = `media:${initialVersion}:frame:board-a:video:1`;
    const newPreview = `canvas-source-binding:${encodeURIComponent(oldPreview)}`;
    const chapterContent: Chapter["content"] = [{
      ...initialChapter.content![0]!,
      preview: "/uploads/source-preview.png",
      videos: ["same.mp4", "same.mp4"],
    }];
    const initialCanvasDocument = {
      nodeLayouts: {
        [oldPreview]: { x: 2000, y: 300, custom: "preserve" },
        [oldVideo0]: { x: 2300, y: 400 },
        [oldVideo1]: { x: 2600, y: 500 },
      },
      manualEdges: [
        { id: "preview-to-frame", sourceRef: oldPreview, targetRef: "frame:board-a", relation: "manual", vendor: 4 },
        { id: "future-dangling", sourceRef: "removed-node", targetRef: "unknown-target", relation: "future", extra: true },
      ],
      notes: [],
      cards: [],
      hiddenRelations: [],
      viewport: { x: 0, y: 0, zoom: 1 },
      unknown: { preserve: true },
    };
    const { userEvents, requests, state } = createWorkspaceHarness("personal", {
      chapterContent,
      initialCanvasDocument,
    });
    const { canvas, dialog: firstDialog, region: firstRegion } = await openCanvas(userEvents);
    const viewport = canvas.getByRole("region", { name: "画布工作区" });
    const preview = await canvas.findByRole("article", { name: "分镜预览" });
    fireEvent.pointerDown(preview, { pointerId: 50, button: 0, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(viewport, { pointerId: 50, clientX: 80, clientY: 40 });
    fireEvent.pointerUp(viewport, { pointerId: 50 });
    await waitFor(() => expect((canvas.getByRole("article", { name: "分镜预览" }) as HTMLElement).style.left)
      .toBe("2080px"));

    await userEvents.click(canvas.getByRole("button", { name: "手动连线" }));
    await userEvents.click(canvas.getByRole("article", { name: "分镜预览" }));
    await userEvents.click(canvas.getByRole("article", { name: "分镜 1" }));
    expect(firstDialog.querySelector("path.is-manual")).not.toBeNull();
    await userEvents.click(canvas.getByRole("button", { name: "保存布局" }));
    await canvas.findByText("已保存");

    await userEvents.click(canvas.getByRole("article", { name: "分镜预览" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑选中媒体来源" }));
    const editor = await screen.findByRole("dialog", { name: "编辑预览 URL" });
    const changedPreview = "/uploads/updated-source-preview.png";
    fireEvent.change(within(editor).getByLabelText("预览 URL"), { target: { value: changedPreview } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存来源" }));
    await canvas.findByText("章节来源写入已获服务器回执，独立读取也确认了同一版本。");

    expect(state.chapterContent?.[0]?.preview).toBe(changedPreview);
    const migratedPreview = canvas.getByRole("article", { name: "分镜预览" }) as HTMLElement;
    expect(migratedPreview.style.left).toBe("2080px");
    expect(migratedPreview.className).toContain("is-selected");
    expect((canvas.getByRole("article", { name: "视频 1" }) as HTMLElement).style.left).toBe("2300px");
    expect((canvas.getByRole("article", { name: "视频 2" }) as HTMLElement).style.left).toBe("2600px");
    expect(firstDialog.querySelector("path.is-manual")).not.toBeNull();

    await userEvents.click(canvas.getByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const frameEditor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(frameEditor).getByLabelText("镜头文字"), {
      target: { value: "普通文字更新不改变媒体身份" },
    });
    await userEvents.click(within(frameEditor).getByRole("button", { name: "保存文字" }));
    await canvas.findByText("章节来源写入已获服务器回执，独立读取也确认了同一版本。");
    expect(state.chapterContent?.[0]?.text).toBe("普通文字更新不改变媒体身份");

    await userEvents.click(canvas.getByRole("button", { name: "保存布局" }));
    await canvas.findByText("已保存");
    expect(state.canvasDocument.nodeLayouts).toMatchObject({
      [newPreview]: { x: 2080, y: 340, custom: "preserve" },
      [`canvas-source-binding:${encodeURIComponent(oldVideo0)}`]: { x: 2300, y: 400 },
      [`canvas-source-binding:${encodeURIComponent(oldVideo1)}`]: { x: 2600, y: 500 },
    });
    expect(state.canvasDocument.manualEdges).toEqual(expect.arrayContaining([
      expect.objectContaining({ sourceRef: newPreview, targetRef: "frame:board-a", relation: "manual" }),
      expect.objectContaining({ id: "future-dangling", extra: true }),
    ]));
    expect(state.canvasDocument.unknown).toEqual({ preserve: true });

    await userEvents.click(canvas.getByRole("button", { name: "关闭创作画布" }));
    await waitFor(() => expect(firstDialog.isConnected).toBe(false));
    const reopened = await openCanvas(userEvents);
    expect(reopened.region).not.toBe(firstRegion);
    expect((await reopened.canvas.findByRole("article", { name: "分镜预览" }) as HTMLElement).style.left).toBe("2080px");
    expect((reopened.canvas.getByRole("article", { name: "视频 1" }) as HTMLElement).style.left).toBe("2300px");
    expect((reopened.canvas.getByRole("article", { name: "视频 2" }) as HTMLElement).style.left).toBe("2600px");
    const layoutSaves = requests.filter(({ url, method }) => method === "PUT"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas`);
    expect(layoutSaves).toHaveLength(2);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
  });

  it("edits the frozen personal chapter source, verifies the independent read, then saves layout and reopens", async () => {
    const { userEvents, requests, state } = createWorkspaceHarness("personal");
    const { region: firstRegion, dialog: firstDialog, canvas } = await openCanvas(userEvents);
    await canvas.findByRole("article", { name: "分镜 1" });

    await userEvents.click(canvas.getByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const textInput = within(editor).getByLabelText("镜头文字");
    const changedText = "改后：雨夜，主角推开旧书店的门。";
    fireEvent.change(textInput, { target: { value: changedText } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    await waitFor(() => {
      expect(requests.filter(({ url, method }) => method === "POST"
        && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
    });
    const sourcePost = requests.find(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`);
    expect(sourcePost?.headers.get("Authorization")).toBe(`Bearer ${token}`);
    expect(sourcePost?.body).toMatchObject({
      capability: "canvas-source-v1",
      expected_series_id: personalSeries.id,
      expected_version: initialVersion,
      command: {
        type: "frame.update",
        target: { index: 0, storyboard_id: "board-a" },
        text: changedText,
      },
    });
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
    expect(requests.some(({ url }) => url.pathname === `/api/chapters/${initialChapter.id}/lock`)).toBe(false);
    expect(canvas.getByText("章节来源写入已获服务器回执，独立读取也确认了同一版本。")).toBeTruthy();
    expect(canvas.getByText(changedText)).toBeTruthy();

    await userEvents.click(canvas.getByRole("button", { name: "新增文本卡片" }));
    const cardText = await canvas.findByLabelText("文本卡片文字");
    fireEvent.change(cardText, { target: { value: "只保存在布局中的构图说明" } });
    await userEvents.click(canvas.getByRole("button", { name: "保存布局" }));
    await canvas.findByText("已保存");
    const layoutPut = requests.find(({ url, method }) => method === "PUT"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas`);
    expect(layoutPut?.body).toMatchObject({
      save_guard: { mode: "guarded-v1", id: null, version: 1 },
      document_json: { cards: [expect.objectContaining({ text: "只保存在布局中的构图说明" })] },
    });

    await userEvents.click(canvas.getByRole("button", { name: "关闭创作画布" }));
    await waitFor(() => expect(firstDialog.isConnected).toBe(false));
    const reopened = await openCanvas(userEvents);
    expect(reopened.region).not.toBe(firstRegion);
    expect(await reopened.canvas.findByText(changedText)).toBeTruthy();
    expect(await reopened.canvas.findByDisplayValue("只保存在布局中的构图说明")).toBeTruthy();
    await waitFor(() => {
      expect(requests.filter(({ url, method }) => method === "GET"
        && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(3);
    });
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
    expect(requests.filter(({ url, method }) => method === "PUT"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas`)).toHaveLength(1);
    expect(state.sourceVersion).toBe(updatedVersion);
  });

  it("preserves an explicit 409 source conflict without retrying the frozen write", async () => {
    const { userEvents, requests } = createWorkspaceHarness("personal", { sourcePostFailureStatus: 409 });
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "保留冲突意图" } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    await within(editor).findByText(/章节来源已变化或当前编辑锁失效/u);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
    expect(canvas.getByText("来源已冲突。原意图仍绑定旧版本，不会自动改绑到最新镜头或文字。")).toBeTruthy();
  });

  it("does not send the source write when the real team-lock preflight returns 401", async () => {
    const { userEvents, requests } = createWorkspaceHarness("team-acquire", { lockReadFailureStatus: 401 });
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "锁读取未授权" } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(window.localStorage.getItem(API_TOKEN_KEY)).toBeNull());
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/lock`)).toHaveLength(1);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(0);
  });

  it("keeps a source POST acknowledged once when its independent GET returns 401", async () => {
    const { userEvents, requests, state } = createWorkspaceHarness("personal");
    const { canvas } = await openCanvas(userEvents);
    state.sourceReadFailureStatus = 401;
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "已确认但回读未授权" } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(window.localStorage.getItem(API_TOKEN_KEY)).toBeNull());
    expect(state.chapterContent?.[0]?.text).toBe("已确认但回读未授权");
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
  });

  it("keeps a rejected creation undo visible with its source and receipt intact", async () => {
    const { userEvents, requests, state } = createWorkspaceHarness("personal", { undoFailureStatus: 403 });
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(canvas.getByRole("button", { name: "插入分镜" }));
    const editor = await screen.findByRole("dialog", { name: "插入分镜" });
    fireEvent.change(within(editor).getByLabelText("分镜文字"), { target: { value: "受控撤销对象" } });
    await userEvents.click(within(editor).getByRole("button", { name: "确认插入" }));
    const undoButton = await canvas.findByRole("button", { name: "撤销刚创建的分镜" });

    await userEvents.click(undoButton);
    await canvas.findByText("合成撤销被拒绝。");
    expect(canvas.getByRole("button", { name: "撤销刚创建的分镜" })).toBeTruthy();
    expect(canvas.getByText("受控撤销对象")).toBeTruthy();
    expect(state.creationToken).toBe(creationReceiptToken);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
  });

  it("acquires then independently verifies a team lock before posting the source command", async () => {
    const { userEvents, requests } = createWorkspaceHarness("team-acquire");
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "团队锁后提交" } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    await waitFor(() => expect(requests.some(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toBe(true));
    const lockFlow = requests.filter(({ url }) => url.pathname === `/api/chapters/${initialChapter.id}/lock`)
      .map(({ method }) => method);
    expect(lockFlow).toEqual(["GET", "POST", "GET"]);
    const sourcePost = requests.find(({ url, method }) => url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`
      && method === "POST");
    expect(sourcePost).toMatchObject({ method: "POST" });
  });

  it("uses the server-created frame ID and exact creation receipt for an explicit undo", async () => {
    const { userEvents, requests } = createWorkspaceHarness("personal");
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(canvas.getByRole("button", { name: "插入分镜" }));
    const editor = await screen.findByRole("dialog", { name: "插入分镜" });
    fireEvent.change(within(editor).getByLabelText("分镜文字"), { target: { value: "服务端创建的新镜头" } });
    await userEvents.click(within(editor).getByRole("button", { name: "确认插入" }));

    const undoButton = await canvas.findByRole("button", { name: "撤销刚创建的分镜" });
    expect(canvas.getByText("服务端创建的新镜头")).toBeTruthy();
    const insertPost = requests.find(({ url, method, body }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`
      && (body as { command?: { type?: string } }).command?.type === "frame.insert");
    expect(insertPost?.body).toMatchObject({
      command: {
        type: "frame.insert",
        after: null,
        frame: { text: "服务端创建的新镜头", storyboard: [] },
      },
    });

    await userEvents.click(undoButton);
    await waitFor(() => expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2));
    const sourcePosts = requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`);
    expect(sourcePosts[0]?.body).toMatchObject({
      command: { type: "frame.insert" },
    });
    expect(sourcePosts[1]?.body).toMatchObject({
      expected_version: insertedSourceVersion,
      command: { type: "creation.undo", receipt: creationReceiptToken },
    });
    expect(canvas.queryByText("服务端创建的新镜头")).toBeNull();
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(3);
  });

  it("deletes a selected frame with its frozen storyboard ID and index", async () => {
    const chapterContent = [
      ...initialChapter.content!,
      { text: "第二镜", storyboard: ["board-b"], custom: "也保留来源数据" },
    ];
    const { userEvents, requests, state } = createWorkspaceHarness("personal", { chapterContent });
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 2" }));
    await userEvents.click(canvas.getByRole("button", { name: "删除选中分镜" }));
    const editor = await screen.findByRole("dialog", { name: "删除分镜" });
    await userEvents.click(within(editor).getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(state.chapterContent).toHaveLength(1));
    const deletePost = requests.find(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`);
    expect(deletePost?.body).toMatchObject({
      expected_series_id: personalSeries.id,
      expected_version: initialVersion,
      command: {
        type: "frame.delete",
        target: { index: 1, storyboard_id: "board-b" },
      },
    });
    expect(await canvas.findByRole("article", { name: "分镜 1" })).toBeTruthy();
    expect(canvas.queryByRole("article", { name: "分镜 2" })).toBeNull();
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
  });

  it.each([
    { kind: "character" as const, button: "新增角色素材", dialog: "新增角色", nameLabel: "角色名称", name: "小满", field: "name", id: createdCharacterId },
    { kind: "scene" as const, button: "新增场景素材", dialog: "新增场景", nameLabel: "场景名称", name: "旧书店", field: "title", id: createdSceneId },
    { kind: "prop" as const, button: "新增道具素材", dialog: "新增道具", nameLabel: "道具名称", name: "铜钥匙", field: "name", id: createdPropId },
  ])("creates a $kind through the Workspace HTTP adapter and confirms its server ID", async ({
    kind,
    button,
    dialog: dialogName,
    nameLabel,
    name,
    field,
    id,
  }) => {
    const { userEvents, requests, state } = createWorkspaceHarness("personal");
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(canvas.getByRole("button", { name: button }));
    const editor = await screen.findByRole("dialog", { name: dialogName });
    fireEvent.change(within(editor).getByLabelText(new RegExp(nameLabel, "u")), { target: { value: name } });
    if (kind === "character") {
      fireEvent.change(within(editor).getByLabelText("身份"), { target: { value: "主人公" } });
    }
    fireEvent.change(within(editor).getByLabelText("图片 URL"), {
      target: { value: `https://example.test/${kind}.png` },
    });
    await userEvents.click(within(editor).getByRole("button", { name: "创建素材" }));

    const businessLabel = kind === "character" ? "角色" : kind === "scene" ? "场景" : "道具";
    await waitFor(() => expect(canvas.getByText(`服务器已确认${businessLabel}素材“${name}”创建。`)).toBeTruthy());
    expect(canvas.queryByText(new RegExp(id, "u"))).toBeNull();
    const createPost = requests.find(({ url, method, body }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`
      && (body as { command?: { type?: string } }).command?.type === "asset.create");
    expect(createPost?.body).toMatchObject({
      capability: "canvas-source-v1",
      expected_series_id: personalSeries.id,
      expected_version: initialVersion,
      command: {
        type: "asset.create",
        kind,
        values: expect.objectContaining({
          [field]: name,
          ...(kind === "character" ? { role: "主人公" } : {}),
          image_url: `https://example.test/${kind}.png`,
        }),
      },
    });
    const created = kind === "character" ? state.characters.find((item) => item.id === id)
      : kind === "scene" ? state.scenes.find((item) => item.id === id)
        : state.props.find((item) => item.id === id);
    expect(created).toMatchObject({
      id,
      series_id: personalSeries.id,
      image_url: `https://example.test/${kind}.png`,
    });
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
  });

  it.each([
    { kind: "character" as const, item: initialCharacter, category: "角色", label: initialCharacter.name },
    { kind: "scene" as const, item: initialScene, category: "场景", label: initialScene.title },
    { kind: "prop" as const, item: initialProp, category: "道具", label: initialProp.name },
  ])("edits a selected $kind image URL by its frozen server ID", async ({ kind, item, category, label }) => {
    const chapterContent = [{ ...initialChapter.content![0]!, [kind]: [item.id] }];
    const { userEvents, requests, state } = createWorkspaceHarness("personal", {
      chapterContent,
      characters: kind === "character" ? [item as Character] : [],
      scenes: kind === "scene" ? [item as Scene] : [],
      props: kind === "prop" ? [item as Prop] : [],
    });
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: label }));
    const editButton = canvas.getByRole("button", { name: "编辑选中素材" });
    expect(editButton).toBeEnabled();
    await userEvents.click(editButton);
    const editor = await screen.findByRole("dialog", { name: `编辑${category}` });
    const updatedImageUrl = `https://example.test/${kind}-updated.png`;
    fireEvent.change(within(editor).getByLabelText("图片 URL"), {
      target: { value: updatedImageUrl },
    });
    await userEvents.click(within(editor).getByRole("button", { name: "保存更改" }));

    await waitFor(() => {
      const updated = kind === "character" ? state.characters.find((asset) => asset.id === item.id)
        : kind === "scene" ? state.scenes.find((asset) => asset.id === item.id)
          : state.props.find((asset) => asset.id === item.id);
      expect(updated?.image_url).toBe(updatedImageUrl);
    });
    const updatePost = requests.find(({ url, method, body }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`
      && (body as { command?: { type?: string } }).command?.type === "asset.update");
    expect(updatePost?.body).toMatchObject({
      expected_version: initialVersion,
      command: {
        type: "asset.update",
        kind,
        id: item.id,
        values: { image_url: updatedImageUrl },
      },
    });
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
  });

  it("writes preview and video URLs through exact media fields and slots", async () => {
    const { userEvents, requests, state } = createWorkspaceHarness("personal");
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "设置镜头预览 URL" }));
    const previewEditor = await screen.findByRole("dialog", { name: "编辑预览 URL" });
    fireEvent.change(within(previewEditor).getByLabelText("预览 URL"), {
      target: { value: "https://example.test/preview.jpg" },
    });
    await userEvents.click(within(previewEditor).getByRole("button", { name: "保存来源" }));

    await waitFor(() => expect((state.chapterContent?.[0] as Record<string, unknown>).preview)
      .toBe("https://example.test/preview.jpg"));
    const previewPost = requests.find(({ url, method, body }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`
      && (body as { command?: { field?: string } }).command?.field === "preview");
    expect(previewPost?.body).toMatchObject({
      command: {
        type: "media.update",
        target: { index: 0, storyboard_id: "board-a" },
        field: "preview",
        slot: null,
        value: "https://example.test/preview.jpg",
      },
    });

    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "追加视频 URL 或 pending" }));
    const appendEditor = await screen.findByRole("dialog", { name: "追加视频来源" });
    fireEvent.change(within(appendEditor).getByLabelText("视频 URL 或 pending"), { target: { value: "pending" } });
    await userEvents.click(within(appendEditor).getByRole("button", { name: "保存来源" }));

    await waitFor(() => expect((state.chapterContent?.[0] as Record<string, unknown>).videos).toEqual(["pending"]));
    const appendPost = requests.find(({ url, method, body }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`
      && (body as { command?: { type?: string } }).command?.type === "media.append");
    expect(appendPost?.body).toMatchObject({
      command: {
        type: "media.append",
        target: { index: 0, storyboard_id: "board-a" },
        value: "pending",
      },
    });
    expect(await canvas.findByRole("button", { name: "撤销刚创建的视频" })).toBeTruthy();

    await userEvents.click(await canvas.findByRole("article", { name: "视频 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑选中媒体来源" }));
    const videoEditor = await screen.findByRole("dialog", { name: "编辑视频槽 1" });
    fireEvent.change(within(videoEditor).getByLabelText("视频 URL 或 pending"), {
      target: { value: "https://example.test/trailer.mp4" },
    });
    await userEvents.click(within(videoEditor).getByRole("button", { name: "保存来源" }));

    await waitFor(() => expect((state.chapterContent?.[0] as Record<string, unknown>).videos)
      .toEqual(["https://example.test/trailer.mp4"]));
    const videoUpdatePost = requests.find(({ url, method, body }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`
      && (body as { command?: { type?: string; field?: string } }).command?.type === "media.update"
      && (body as { command?: { field?: string } }).command?.field === "video");
    expect(videoUpdatePost?.body).toMatchObject({
      command: {
        type: "media.update",
        target: { index: 0, storyboard_id: "board-a" },
        field: "video",
        slot: 0,
        value: "https://example.test/trailer.mp4",
      },
    });
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(4);
  });

  it("keeps an unknown source intent and only performs a read when the server committed but the POST reply was lost", async () => {
    const { userEvents, requests, state } = createWorkspaceHarness("personal", { loseNextSourcePostReply: true });
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    const changedText = "服务器可能已提交，但客户端未收到回执";
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: changedText } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    await within(editor).findByRole("button", { name: "只读核对来源" });
    expect(state.chapterContent?.[0]?.text).toBe(changedText);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
    await userEvents.click(within(editor).getByRole("button", { name: "只读核对来源" }));
    await within(editor).findByText(/只读读取已发现不同来源版本/u);
    expect(requests.filter(({ url, method }) => method === "GET"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(2);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
    await userEvents.click(within(editor).getByRole("button", { name: "取消" }));
    await canvas.findByRole("button", { name: "采用当前读取并放弃原意图" });
    expect(canvas.getByText(initialChapter.content![0]!.text as string)).toBeTruthy();

    await userEvents.click(canvas.getByRole("button", { name: "采用当前读取并放弃原意图" }));
    expect(canvas.getByText(changedText)).toBeTruthy();
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(1);
  });

  it.each(["team-other", "team-expired"] as const)("does not post a source command when a required team lock is %s", async (scenario) => {
    const { userEvents, requests } = createWorkspaceHarness(scenario);
    const { canvas } = await openCanvas(userEvents);
    await userEvents.click(await canvas.findByRole("article", { name: "分镜 1" }));
    await userEvents.click(canvas.getByRole("button", { name: "编辑分镜文字" }));
    const editor = await screen.findByRole("dialog", { name: "编辑分镜文字" });
    fireEvent.change(within(editor).getByLabelText("镜头文字"), { target: { value: "不得提交" } });
    await userEvents.click(within(editor).getByRole("button", { name: "保存文字" }));

    const expectedLockMessage = scenario === "team-other"
      ? "章节当前由其他成员编辑，未发送来源变更。"
      : "未能确认当前账号持有有效章节锁，未发送来源变更。";
    await within(editor).findByText(expectedLockMessage);
    expect(requests.filter(({ url, method }) => method === "POST"
      && url.pathname === `/api/chapters/${initialChapter.id}/canvas-source`)).toHaveLength(0);
    expect(requests.filter(({ url, method }) => url.pathname === `/api/chapters/${initialChapter.id}/lock`
      && method === "POST")).toHaveLength(scenario === "team-expired" ? 1 : 0);
  });
});
