import {
  parseChapterResponse,
  parseCharacterList,
  parsePropList,
  parseSceneList,
  parseStoryboardAssetList,
  type Chapter,
  type Character,
  type Prop,
  type Scene,
  type StoryboardAsset,
  type StoryboardFrame,
} from "./contracts";
import type {
  CharacterCreateInput,
  CharacterUpdateInput,
  PropCreateInput,
  PropUpdateInput,
  SceneCreateInput,
  SceneUpdateInput,
} from "./assetManagement";
import { API_TOKEN_KEY, API_USER_KEY, clearApiSession, type StorageLike } from "./storage";

export const CANVAS_SOURCE_CAPABILITY = "canvas-source-v1" as const;
const CHAPTER_CANONICAL_DIGEST_PATTERN = /^[0-9a-f]{64}$/u;
const GENERATION_IDENTIFIER_CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;

export interface CanvasSourceSnapshot {
  capability: typeof CANVAS_SOURCE_CAPABILITY;
  series_id: string;
  chapter_id: string;
  version: string;
  chapter_canonical_digest?: string | null;
  requires_lock: boolean;
  chapter: Chapter;
  storyboard_assets: StoryboardAsset[];
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
}

export interface CanvasFrameTarget {
  index: number;
  storyboard_id: string | null;
}

export type CanvasFrameGenerationDestination =
  | {
      kind: "frame_preview";
      chapter_id: string;
      storyboard_asset_id: string;
    }
  | {
      kind: "frame_preview";
      chapter_id: string;
      identity_mode: "strict-no-id-v1";
      frame_index: number;
      expected_chapter_digest: string;
    };

export function resolveCanvasFrameTarget(
  chapter: Chapter,
  storyboardAssets: StoryboardAsset[],
  index: number,
): CanvasFrameTarget | null {
  const frames = chapter.content;
  if (!Array.isArray(frames) || !Number.isSafeInteger(index) || index < 0 || index >= frames.length) return null;
  const frame = frames[index];
  if (!isRecord(frame)) return null;

  const storyboard = frame.storyboard;
  if (storyboard === undefined) {
    return { index, storyboard_id: null };
  }
  if (!Array.isArray(storyboard)) return null;
  const first = storyboard[0];
  if (first === undefined && storyboard.length === 0) return { index, storyboard_id: null };
  if (first === null || first === "") {
    return storyboard.every((reference) => reference === null || reference === "")
      ? { index, storyboard_id: null }
      : null;
  }
  if (typeof first !== "string" || first.trim() === "") return null;

  const storyboardId = first;
  const framesWithSameFirstId = frames.filter((candidate) => isRecord(candidate)
    && Array.isArray(candidate.storyboard) && candidate.storyboard[0] === storyboardId).length;
  const assets = storyboardAssets.filter((asset) => asset.id === storyboardId);
  const asset = assets.length === 1 ? assets[0] : undefined;
  if (framesWithSameFirstId !== 1 || asset === undefined
    || asset.series_id !== chapter.series_id || asset.chapter_id !== chapter.id || asset.frame_index !== index) {
    return null;
  }
  return { index, storyboard_id: storyboardId };
}

function isValidGenerationIdentifier(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const codePointLength = Array.from(value).length;
  return codePointLength >= 1
    && codePointLength <= 36
    && value.trim() === value
    && !GENERATION_IDENTIFIER_CONTROL_CHARACTERS.test(value)
    && !value.includes("/")
    && !value.includes("\\")
    && value !== "."
    && value !== "..";
}

export function resolveCanvasFrameGenerationDestination(
  snapshot: CanvasSourceSnapshot,
  index: number,
): CanvasFrameGenerationDestination | null {
  if (!isValidGenerationIdentifier(snapshot.chapter_id)
    || !isValidGenerationIdentifier(snapshot.series_id)
    || typeof snapshot.chapter !== "object" || snapshot.chapter === null
    || snapshot.chapter.id !== snapshot.chapter_id
    || snapshot.chapter.series_id !== snapshot.series_id
    || !Array.isArray(snapshot.storyboard_assets)
    || !snapshot.storyboard_assets.every(isRecord)) {
    return null;
  }

  const target = resolveCanvasFrameTarget(snapshot.chapter, snapshot.storyboard_assets, index);
  if (target === null) return null;
  if (target.storyboard_id !== null) {
    if (!isValidGenerationIdentifier(target.storyboard_id)) return null;
    return {
      kind: "frame_preview",
      chapter_id: snapshot.chapter_id,
      storyboard_asset_id: target.storyboard_id,
    };
  }

  const digest = snapshot.chapter_canonical_digest;
  if (typeof digest !== "string" || !CHAPTER_CANONICAL_DIGEST_PATTERN.test(digest)) return null;
  return {
    kind: "frame_preview",
    chapter_id: snapshot.chapter_id,
    identity_mode: "strict-no-id-v1",
    frame_index: index,
    expected_chapter_digest: digest,
  };
}

export type CanvasAssetKind = "character" | "scene" | "prop";

export type CanvasSourceCommand =
  | { type: "frame.update"; target: CanvasFrameTarget; text: string }
  | { type: "frame.insert"; after: CanvasFrameTarget | null; frame: StoryboardFrame }
  | { type: "frame.delete"; target: CanvasFrameTarget }
  | {
      type: "asset.create";
      kind: "character";
      values: CharacterCreateInput;
    }
  | { type: "asset.create"; kind: "scene"; values: SceneCreateInput }
  | { type: "asset.create"; kind: "prop"; values: PropCreateInput }
  | {
      type: "asset.update";
      kind: "character";
      id: string;
      values: CharacterUpdateInput;
    }
  | { type: "asset.update"; kind: "scene"; id: string; values: SceneUpdateInput }
  | { type: "asset.update"; kind: "prop"; id: string; values: PropUpdateInput }
  | { type: "media.append"; target: CanvasFrameTarget; value: string }
  | {
      type: "media.update";
      target: CanvasFrameTarget;
      field: "preview" | "video";
      slot: number | null;
      value: string;
    }
  | { type: "creation.undo"; receipt: string };

export interface CanvasCreationReceipt {
  token: string;
  kind: "frame" | "character" | "scene" | "prop" | "video";
  target: Record<string, unknown>;
}

export interface CanvasSourceWriteReceipt {
  capability: typeof CANVAS_SOURCE_CAPABILITY;
  request_id: string;
  command_type: CanvasSourceCommand["type"];
  snapshot: CanvasSourceSnapshot;
  creation_receipt: CanvasCreationReceipt | null;
}

export interface CanvasSourcePort {
  read(chapterId: string, signal: AbortSignal): Promise<CanvasSourceSnapshot>;
  write(
    chapterId: string,
    frozen: CanvasSourceSnapshot,
    command: CanvasSourceCommand,
    signal: AbortSignal,
  ): Promise<CanvasSourceWriteReceipt>;
}

export type CanvasSourceErrorKind =
  | "unsupported"
  | "conflict"
  | "unknown"
  | "rejected"
  | "unauthorized"
  | "aborted"
  | "network"
  | "timeout";

export class CanvasSourceError extends Error {
  constructor(
    readonly kind: CanvasSourceErrorKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "CanvasSourceError";
  }
}

export interface CanvasSourceHttpOptions {
  storage: StorageLike;
  fetcher: typeof fetch;
  timeoutMs?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readActorId(storage: StorageLike): string | null {
  const raw = storage.getItem(API_USER_KEY);
  if (raw === null) return null;
  try {
    const value: unknown = JSON.parse(raw);
    return isRecord(value) && typeof value.id === "string" && value.id.trim() !== ""
      ? value.id
      : null;
  } catch {
    return null;
  }
}

function readToken(storage: StorageLike): string {
  const token = storage.getItem(API_TOKEN_KEY);
  if (token === null || token === "") {
    throw new CanvasSourceError("unauthorized", "登录状态已失效，请重新登录。", 401);
  }
  return token;
}

function ensureCurrent(storage: StorageLike, token: string, actorId: string, signal: AbortSignal): void {
  if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token || readActorId(storage) !== actorId) {
    throw new CanvasSourceError("aborted", "登录状态或画布来源已变化，请重新打开画布。");
  }
}

function errorDetail(value: unknown): string | null {
  if (!isRecord(value)) return null;
  const detail = value.detail;
  return typeof detail === "string" && detail.trim() !== "" ? detail : null;
}

function parseSnapshot(value: unknown, chapterId: string): CanvasSourceSnapshot {
  if (!isRecord(value)
    || value.capability !== CANVAS_SOURCE_CAPABILITY
    || typeof value.series_id !== "string" || value.series_id.trim() === ""
    || value.chapter_id !== chapterId
    || typeof value.version !== "string" || !/^[0-9a-f]{64}$/u.test(value.version)
    || typeof value.requires_lock !== "boolean") {
    throw new CanvasSourceError("unknown", "画布来源服务返回了无法确认的章节快照。");
  }
  const hasChapterCanonicalDigest = Object.prototype.hasOwnProperty.call(value, "chapter_canonical_digest");
  const chapterCanonicalDigest = value.chapter_canonical_digest;
  if (hasChapterCanonicalDigest && chapterCanonicalDigest !== null
    && (typeof chapterCanonicalDigest !== "string"
      || !CHAPTER_CANONICAL_DIGEST_PATTERN.test(chapterCanonicalDigest))) {
    throw new CanvasSourceError("unknown", "画布来源服务返回了无效的章节内容摘要。");
  }
  const chapter = parseChapterResponse(value.chapter, value.series_id, chapterId);
  const storyboardAssets = parseStoryboardAssetList(value.storyboard_assets);
  const storyboardIds = new Set<string>();
  for (const asset of storyboardAssets) {
    if (asset.series_id !== value.series_id || asset.chapter_id !== chapterId
      || !Number.isSafeInteger(asset.frame_index) || asset.frame_index < 0
      || storyboardIds.has(asset.id)) {
      throw new CanvasSourceError("unknown", "画布来源服务返回了无法唯一确认的分镜素材。");
    }
    storyboardIds.add(asset.id);
  }
  try {
    return {
      capability: CANVAS_SOURCE_CAPABILITY,
      series_id: value.series_id,
      chapter_id: chapterId,
      version: value.version,
      ...(hasChapterCanonicalDigest
        ? { chapter_canonical_digest: chapterCanonicalDigest as string | null }
        : {}),
      requires_lock: value.requires_lock,
      chapter,
      storyboard_assets: storyboardAssets,
      characters: parseCharacterList(value.characters, value.series_id),
      scenes: parseSceneList(value.scenes, value.series_id),
      props: parsePropList(value.props, value.series_id),
    };
  } catch (cause) {
    if (cause instanceof CanvasSourceError) throw cause;
    throw new CanvasSourceError("unknown", "画布来源服务返回了无法确认的章节快照。");
  }
}

function isCreateCommand(command: CanvasSourceCommand): boolean {
  return command.type === "frame.insert" || command.type === "asset.create" || command.type === "media.append";
}

function isValidCreationReceipt(value: unknown): value is CanvasCreationReceipt {
  return isRecord(value)
    && typeof value.token === "string" && value.token.trim() !== ""
    && (value.kind === "frame" || value.kind === "character" || value.kind === "scene"
      || value.kind === "prop" || value.kind === "video")
    && isRecord(value.target);
}

function hasExactFrameTarget(snapshot: CanvasSourceSnapshot, target: CanvasFrameTarget): boolean {
  const actual = resolveCanvasFrameTarget(snapshot.chapter, snapshot.storyboard_assets, target.index);
  return actual !== null && actual.storyboard_id === target.storyboard_id;
}

function sameJsonValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((value, index) => sameJsonValue(value, right[index]));
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const leftKeys = Object.keys(left).sort();
    const rightKeys = Object.keys(right).sort();
    return leftKeys.length === rightKeys.length
      && leftKeys.every((key, index) => key === rightKeys[index] && sameJsonValue(left[key], right[key]));
  }
  return false;
}

function frameContent(snapshot: CanvasSourceSnapshot): unknown[] {
  return Array.isArray(snapshot.chapter.content) ? snapshot.chapter.content : [];
}

function existingStoryboardReferences(snapshot: CanvasSourceSnapshot): Set<string> {
  const ids = new Set(snapshot.storyboard_assets.map((asset) => asset.id));
  for (const frame of frameContent(snapshot)) {
    if (!isRecord(frame) || !Array.isArray(frame.storyboard)) continue;
    for (const id of frame.storyboard) if (typeof id === "string") ids.add(id);
  }
  return ids;
}

function insertIndex(snapshot: CanvasSourceSnapshot, after: CanvasFrameTarget | null): number | null {
  if (after === null) return 0;
  if (!hasExactFrameTarget(snapshot, after)) return null;
  return after.index + 1;
}

function insertedFramesMatchBaseline(
  baseline: CanvasSourceSnapshot,
  snapshot: CanvasSourceSnapshot,
  insertedIndex: number,
): boolean {
  const before = frameContent(baseline);
  const after = frameContent(snapshot);
  if (after.length !== before.length + 1 || insertedIndex < 0 || insertedIndex >= after.length) return false;
  const remaining = [...after.slice(0, insertedIndex), ...after.slice(insertedIndex + 1)];
  return sameJsonValue(before, remaining);
}

function insertedFrameMatchesCommand(
  snapshot: CanvasSourceSnapshot,
  command: Extract<CanvasSourceCommand, { type: "frame.insert" }>,
  insertedIndex: number,
  id: string,
): boolean {
  const frame = frameContent(snapshot)[insertedIndex];
  if (!isRecord(frame)) return false;
  const expected = { ...command.frame, storyboard: [id] };
  return sameJsonValue(expected, frame);
}

function creationReceiptMatches(
  baseline: CanvasSourceSnapshot,
  snapshot: CanvasSourceSnapshot,
  command: CanvasSourceCommand,
  receipt: CanvasCreationReceipt,
): boolean {
  const target = receipt.target;
  if (command.type === "frame.insert") {
    const id = target.id;
    const index = target.index;
    const expectedIndex = insertIndex(baseline, command.after);
    const idWasPresent = typeof id === "string" && existingStoryboardReferences(baseline).has(id);
    return receipt.kind === "frame"
      && typeof id === "string" && id.trim() !== ""
      && !idWasPresent
      && target.storyboard_id === id
      && typeof index === "number" && Number.isSafeInteger(index) && index >= 0
      && index === expectedIndex
      && insertedFramesMatchBaseline(baseline, snapshot, index)
      && insertedFrameMatchesCommand(snapshot, command, index, id)
      && hasExactFrameTarget(snapshot, { index, storyboard_id: id });
  }
  if (command.type === "asset.create") {
    if (receipt.kind !== command.kind || typeof target.id !== "string" || target.id.trim() === "") return false;
    const baselineCollection = command.kind === "character" ? baseline.characters
      : command.kind === "scene" ? baseline.scenes : baseline.props;
    if (baselineCollection.some((asset) => asset.id === target.id)) return false;
    const collection = command.kind === "character" ? snapshot.characters
      : command.kind === "scene" ? snapshot.scenes : snapshot.props;
    return collection.filter((asset) => asset.id === target.id && asset.series_id === snapshot.series_id).length === 1;
  }
  if (command.type === "media.append") {
    const index = target.index;
    const slot = target.slot;
    if (receipt.kind !== "video" || target.storyboard_id !== command.target.storyboard_id
      || target.value !== command.value || typeof index !== "number" || index !== command.target.index
      || typeof slot !== "number" || !Number.isSafeInteger(slot) || slot < 0
      || !hasExactFrameTarget(snapshot, command.target)) return false;
    if (!hasExactFrameTarget(baseline, command.target)) return false;
    const beforeFrame = frameContent(baseline)[index];
    const frame = snapshot.chapter.content?.[index];
    if (!isRecord(beforeFrame) || !isRecord(frame)) return false;
    const beforeVideos = Array.isArray(beforeFrame.videos) ? beforeFrame.videos : [];
    const afterVideos = Array.isArray(frame.videos) ? frame.videos : [];
    if (slot !== beforeVideos.length || afterVideos.length !== beforeVideos.length + 1
      || !sameJsonValue(afterVideos.slice(0, beforeVideos.length), beforeVideos)
      || afterVideos[slot] !== command.value) return false;
    const expectedFrame = { ...beforeFrame, videos: [...beforeVideos, command.value] };
    return sameJsonValue(expectedFrame, frame);
  }
  return false;
}

function parseWriteReceipt(
  value: unknown,
  chapterId: string,
  seriesId: string,
  requestId: string,
  baseline: CanvasSourceSnapshot,
  command: CanvasSourceCommand,
): CanvasSourceWriteReceipt {
  if (!isRecord(value)
    || value.capability !== CANVAS_SOURCE_CAPABILITY
    || value.request_id !== requestId
    || value.command_type !== command.type) {
    throw new CanvasSourceError("unknown", "写入结果暂时无法确认，请保留当前意图并核对章节来源。");
  }
  const snapshot = parseSnapshot(value.snapshot, chapterId);
  if (snapshot.series_id !== seriesId) {
    throw new CanvasSourceError("unknown", "写入结果指向了其他剧集，当前意图已保留。");
  }
  const creationReceipt = value.creation_receipt;
  if (isCreateCommand(command)) {
    if (!isValidCreationReceipt(creationReceipt) || !creationReceiptMatches(baseline, snapshot, command, creationReceipt)) {
      throw new CanvasSourceError("unknown", "创建结果缺少可核对的服务器回执，请保留当前意图并只读核对。");
    }
  } else if (creationReceipt !== null) {
    throw new CanvasSourceError("unknown", "写入回执与当前操作不匹配，请只读核对章节来源。");
  }
  return {
    capability: CANVAS_SOURCE_CAPABILITY,
    request_id: requestId,
    command_type: command.type,
    snapshot,
    creation_receipt: creationReceipt as CanvasCreationReceipt | null,
  };
}

export function createCanvasSourceHttpPort(
  apiBaseUrl: string,
  { storage, fetcher, timeoutMs = 15_000 }: CanvasSourceHttpOptions,
): CanvasSourcePort {
  const api = new URL(apiBaseUrl);

  async function request(
    chapterId: string,
    signal: AbortSignal,
    method: "GET" | "POST",
    body?: object,
  ): Promise<unknown> {
    const token = readToken(storage);
    const actorId = readActorId(storage);
    if (actorId === null) {
      throw new CanvasSourceError("rejected", "当前登录身份无法确认，请重新登录后再编辑来源。");
    }
    const controller = new AbortController();
    let didTimeout = false;
    const abortForCaller = () => controller.abort();
    signal.addEventListener("abort", abortForCaller, { once: true });
    const timer = setTimeout(() => {
      didTimeout = true;
      controller.abort();
    }, timeoutMs);
    const url = new URL(
      `${apiBaseUrl}/chapters/${encodeURIComponent(chapterId)}/canvas-source`,
      api.origin,
    );
    const cleanup = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", abortForCaller);
    };
    try {
      ensureCurrent(storage, token, actorId, signal);
      let response: Response;
      try {
        response = await fetcher(url, {
          method,
          headers: new Headers({
            Accept: "application/json",
            Authorization: `Bearer ${token}`,
            ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          }),
          credentials: "omit",
          signal: controller.signal,
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
      } catch (cause) {
        if (signal.aborted || storage.getItem(API_TOKEN_KEY) !== token || readActorId(storage) !== actorId) {
          throw new CanvasSourceError("aborted", "登录状态或画布来源已变化，请重新打开画布。");
        }
        if (didTimeout) {
          throw new CanvasSourceError(method === "POST" ? "unknown" : "timeout", method === "POST"
            ? "写入结果尚未确认；保留当前意图并只读核对，不会自动重发。"
            : "读取章节来源超时，请稍后重新读取。");
        }
        if (cause instanceof DOMException && cause.name === "AbortError") {
          throw new CanvasSourceError("aborted", "Request was cancelled.");
        }
        throw new CanvasSourceError(method === "POST" ? "unknown" : "network", method === "POST"
          ? "写入结果尚未确认；保留当前意图并只读核对，不会自动重发。"
          : "无法连接画布来源服务，请确认服务后重新读取。");
      }
      ensureCurrent(storage, token, actorId, signal);

      if (response.status === 401 && storage.getItem(API_TOKEN_KEY) === token) {
        clearApiSession(storage);
        throw new CanvasSourceError("unauthorized", "登录状态已失效，请重新登录。", 401);
      }
      if (response.status === 404 || response.status === 405 || response.status === 501) {
        throw new CanvasSourceError("unsupported", "当前服务未接入受控画布来源操作，不会改用旧接口写入。", response.status);
      }
      if (response.status === 409) {
        throw new CanvasSourceError("conflict", "章节来源已变化或当前编辑锁失效；保留当前意图，请重新读取核对。", 409);
      }

      let payload: unknown;
      try {
        payload = await response.json() as unknown;
      } catch {
        if (signal.aborted) throw new CanvasSourceError("aborted", "Request was cancelled.");
        if (didTimeout) {
          throw new CanvasSourceError(method === "POST" ? "unknown" : "timeout", method === "POST"
            ? "写入结果尚未确认；保留当前意图并只读核对，不会自动重发。"
            : "读取章节来源超时，请稍后重新读取。");
        }
        if (method === "POST" && response.ok) {
          throw new CanvasSourceError("unknown", "写入可能已完成，但回执无法识别；保留当前意图并只读核对。", response.status);
        }
        if (!response.ok && response.status >= 500) {
          throw new CanvasSourceError("unknown", "服务端暂时无法确认章节来源状态。", response.status);
        }
        throw new CanvasSourceError("rejected", "画布来源服务返回了无法识别的数据。", response.status);
      }
      ensureCurrent(storage, token, actorId, signal);
      if (!response.ok) {
        const detail = errorDetail(payload);
        if (response.status >= 500) {
          throw new CanvasSourceError("unknown", "服务端暂时无法确认章节来源状态。", response.status);
        }
        throw new CanvasSourceError("rejected", detail ?? "画布来源请求未被服务器接受。", response.status);
      }
      return payload;
    } finally {
      cleanup();
    }
  }

  return {
    async read(chapterId, signal) {
      try {
        return parseSnapshot(await request(chapterId, signal, "GET"), chapterId);
      } catch (cause) {
        if (cause instanceof CanvasSourceError) throw cause;
        throw new CanvasSourceError("unknown", "画布来源服务返回了无法确认的章节快照。");
      }
    },
    async write(chapterId, frozen, command, signal) {
      if (frozen.chapter_id !== chapterId || frozen.chapter.id !== chapterId
        || frozen.series_id !== frozen.chapter.series_id) {
        throw new CanvasSourceError("rejected", "当前章节身份与冻结的来源快照不匹配，未发送写入。");
      }
      const requestId = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : null;
      if (requestId === null) {
        throw new CanvasSourceError("rejected", "当前环境无法创建来源请求编号，未发送写入。");
      }
      const body = {
        capability: CANVAS_SOURCE_CAPABILITY,
        request_id: requestId,
        expected_series_id: frozen.series_id,
        expected_version: frozen.version,
        command,
      };
      const payload = await request(chapterId, signal, "POST", body);
      try {
        return parseWriteReceipt(payload, chapterId, frozen.series_id, requestId, frozen, command);
      } catch (cause) {
        if (cause instanceof CanvasSourceError) throw cause;
        throw new CanvasSourceError("unknown", "写入结果暂时无法确认，请保留当前意图并只读核对。");
      }
    },
  };
}
