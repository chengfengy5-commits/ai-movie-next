import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import { classifyMediaExportUrl } from "./mediaExportReader";
import {
  createGenerationHistoryMediaReader,
} from "./assetGenerationHistoryHttp";
import {
  ChapterGenerationHistoryError,
  type ChapterGenerationHistoryAdoptInput,
  type ChapterGenerationHistoryAdoptResult,
  type ChapterGenerationHistoryContext,
  type ChapterGenerationHistoryMessage,
  type ChapterGenerationHistoryPort,
  type ChapterGenerationHistoryReadOnlyReason,
  type ChapterGenerationHistoryRun,
  type ChapterGenerationHistorySnapshot,
  type ChapterGenerationHistoryTargetProof,
} from "./chapterGenerationHistory";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const SHA256_HEX = /^[0-9a-f]{64}$/u;
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

export interface ChapterGenerationHistoryHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly imagePreviewTimeoutMs?: number;
  readonly pageUrl?: string;
}

function fail(
  kind: ChapterGenerationHistoryError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new ChapterGenerationHistoryError(kind, message, status);
}

function isIdentifier(value: unknown): value is string {
  return typeof value === "string"
    && value.length > 0
    && value.length <= 128
    && value.trim() === value
    && !CONTROL_CHARACTERS.test(value);
}

function actorIdFromStorage(storage: StorageLike): string | null {
  try {
    const serialized = storage.getItem(API_USER_KEY);
    if (serialized === null) return null;
    const parsed: unknown = JSON.parse(serialized);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
    const actorId = (parsed as Record<string, unknown>).id;
    return isIdentifier(actorId) ? actorId : null;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseReadOnlyReason(value: unknown): ChapterGenerationHistoryReadOnlyReason | null {
  if (value === null) return null;
  switch (value) {
    case "unreadable_chapter":
    case "missing_storyboard_id":
    case "duplicate_storyboard_id":
    case "asset_not_in_chapter":
    case "ambiguous_storyboard_asset":
    case "storyboard_index_mismatch":
    case "invalid_media_value":
      return value as ChapterGenerationHistoryReadOnlyReason;
    default:
      fail("invalid-response", "历史目标只读原因无法识别。");
  }
}

function parseTargetProof(value: unknown): ChapterGenerationHistoryTargetProof | null {
  if (value === null) return null;
  if (!isRecord(value)
    || typeof value.mapping_digest !== "string"
    || !SHA256_HEX.test(value.mapping_digest)
    || typeof value.target_digest !== "string"
    || !SHA256_HEX.test(value.target_digest)
    || (value.media_url !== null && typeof value.media_url !== "string")) {
    fail("invalid-response", "历史目标证明无法核对。");
  }
  return {
    mapping_digest: value.mapping_digest,
    target_digest: value.target_digest,
    media_url: value.media_url,
  };
}

function parseMessage(value: unknown, context: ChapterGenerationHistoryContext): ChapterGenerationHistoryMessage {
  if (!isRecord(value)) fail("invalid-response", "生成历史消息格式无法识别。");
  const nullableStrings = ["asset_type", "asset_id", "model_name"] as const;
  if (!isIdentifier(value.id)
    || value.chapter_id !== context.chapterId
    || (value.frame_index !== null && (!Number.isSafeInteger(value.frame_index) || (value.frame_index as number) < 0))
    || nullableStrings.some((key) => value[key] !== null && typeof value[key] !== "string")
    || typeof value.chat_mode !== "string"
    || typeof value.role !== "string"
    || typeof value.content !== "string"
    || typeof value.created_at !== "string"
    || !Number.isFinite(Date.parse(value.created_at))
    || typeof value.message_digest !== "string"
    || !SHA256_HEX.test(value.message_digest)) {
    fail("invalid-response", "生成历史消息字段无法核对。");
  }

  const expectedTupleMatches = context.targetKind === "frame-preview"
    ? value.frame_index === context.frameIndex && value.asset_type === null && value.asset_id === null
    : value.frame_index === null && value.asset_type === "storyboard" && value.asset_id === context.storyboardAssetId;
  if (!expectedTupleMatches || value.chat_mode !== context.mode) {
    fail("invalid-response", "历史记录与当前镜头、来源或生成类型不一致。");
  }
  return {
    id: value.id,
    chapter_id: value.chapter_id as string,
    frame_index: value.frame_index as number | null,
    asset_type: value.asset_type as string | null,
    asset_id: value.asset_id as string | null,
    chat_mode: value.chat_mode,
    role: value.role,
    content: value.content,
    model_name: value.model_name as string | null,
    created_at: value.created_at,
    message_digest: value.message_digest,
  };
}

function parseSnapshot(value: unknown, context: ChapterGenerationHistoryContext): ChapterGenerationHistorySnapshot {
  if (!isRecord(value)
    || value.protocol_version !== 1
    || value.series_id !== context.seriesId
    || value.chapter_id !== context.chapterId
    || value.target_kind !== context.targetKind
    || value.storyboard_asset_id !== context.storyboardAssetId
    || value.frame_index !== context.frameIndex
    || value.chat_mode !== context.mode
    || typeof value.writable !== "boolean"
    || !Array.isArray(value.messages)) {
    fail("invalid-response", "生成历史快照与当前镜头范围不一致。");
  }
  const readOnlyReason = parseReadOnlyReason(value.read_only_reason);
  const targetProof = parseTargetProof(value.target_proof);
  if (value.writable && (readOnlyReason !== null || targetProof === null || context.storyboardAssetId === null)) {
    fail("invalid-response", "可写历史快照缺少稳定目标证明。");
  }
  const messages = value.messages.map((message) => parseMessage(message, context));
  const ids = new Set<string>();
  for (const message of messages) {
    if (ids.has(message.id)) fail("invalid-response", "生成历史包含重复消息编号。");
    ids.add(message.id);
  }
  return {
    protocol_version: 1,
    series_id: context.seriesId,
    chapter_id: context.chapterId,
    target_kind: context.targetKind,
    storyboard_asset_id: context.storyboardAssetId,
    frame_index: context.frameIndex,
    chat_mode: context.mode,
    writable: value.writable,
    read_only_reason: readOnlyReason,
    target_proof: targetProof,
    messages,
  };
}

function parseAdoptResult(
  value: unknown,
  context: ChapterGenerationHistoryContext,
  candidateUrl: string,
): ChapterGenerationHistoryAdoptResult {
  if (!isRecord(value)
    || value.protocol_version !== 1
    || (value.outcome !== "applied" && value.outcome !== "already_current")
    || value.series_id !== context.seriesId
    || value.chapter_id !== context.chapterId
    || value.target_kind !== context.targetKind
    || value.storyboard_asset_id !== context.storyboardAssetId
    || value.frame_index !== context.frameIndex) {
    fail("unknown", "选用请求已响应，但目标回执无法核对；请只读核对当前值。", 200);
  }
  let targetProof: ChapterGenerationHistoryTargetProof | null;
  try {
    targetProof = parseTargetProof(value.target_proof);
  } catch {
    fail("unknown", "选用请求已响应，但目标证明无法核对；请只读核对当前值。", 200);
  }
  if (targetProof === null || targetProof.media_url !== candidateUrl) {
    fail("unknown", "选用请求已响应，但媒体结果无法核对；请只读核对当前值。", 200);
  }
  return {
    protocol_version: 1,
    outcome: value.outcome,
    series_id: context.seriesId,
    chapter_id: context.chapterId,
    target_kind: context.targetKind,
    storyboard_asset_id: context.storyboardAssetId!,
    frame_index: context.frameIndex,
    target_proof: targetProof,
  };
}

function errorMessage(status: number, stage: "read" | "write", detailCode: string | null = null): string {
  if (status === 401) return "登录状态已失效，请重新登录。";
  if (status === 403) return "当前账号无权访问此剧集。";
  if (status === 404) return "章节已不存在或无法访问。";
  if (status === 409) return stage === "write" ? "历史记录或当前目标已变化，请重新打开后核对。" : "当前镜头位置已变化，请重新打开历史。";
  if (status === 422 && detailCode === "storyboard_media_url_too_long") {
    return "该图片地址超过故事板素材字段长度限制，无法写入。";
  }
  if (status === 422) return "历史请求字段无法核对，请重新打开历史。";
  return stage === "write" ? "请求已发送，当前结果未知；请只读核对，不要自动重试。" : "暂时无法读取生成历史。";
}

export function createChapterGenerationHistoryHttpPort(
  apiBaseUrl: string,
  options: ChapterGenerationHistoryHttpOptions = {},
): ChapterGenerationHistoryPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) fail("not-sent", "历史请求超时配置无效。");

  let baseUrl: string;
  try {
    baseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) fail("not-sent", "历史服务地址无效。");
    throw error;
  }

  return {
    beginRun(context: ChapterGenerationHistoryContext): ChapterGenerationHistoryRun {
      if (!isIdentifier(context.actorId)
        || !isIdentifier(context.seriesId)
        || !isIdentifier(context.chapterId)
        || !Number.isSafeInteger(context.frameIndex)
        || context.frameIndex < 0
        || (context.targetKind !== "frame-preview" && context.targetKind !== "storyboard-image")
        || (context.mode !== "image" && context.mode !== "video")
        || (context.storyboardAssetId !== null && !isIdentifier(context.storyboardAssetId))
        || (context.targetKind === "storyboard-image" && context.storyboardAssetId === null)
        || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "历史查询身份无法核对。");
      }

      let token: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
      } catch {
        // An inaccessible session cannot authorize a history request.
      }
      if (token === null || token.trim() === "" || token.trim() !== token || CONTROL_CHARACTERS.test(token)
        || actorIdFromStorage(storage) !== context.actorId) {
        fail("not-sent", "当前登录身份无法核对。");
      }

      const captured = {
        actorId: context.actorId,
        seriesId: context.seriesId,
        chapterId: context.chapterId,
        targetKind: context.targetKind,
        storyboardAssetId: context.storyboardAssetId,
        frameIndex: context.frameIndex,
        mode: context.mode,
        contextCheck: context.isContextCurrent,
      } as const;
      const contextIsCurrent = captured.contextCheck.bind(context);
      const currentSnapshot = { value: null as ChapterGenerationHistorySnapshot | null };
      let snapshotReadGeneration = 0;

      function isCurrent(): boolean {
        try {
          return context.actorId === captured.actorId
            && context.seriesId === captured.seriesId
            && context.chapterId === captured.chapterId
            && context.targetKind === captured.targetKind
            && context.storyboardAssetId === captured.storyboardAssetId
            && context.frameIndex === captured.frameIndex
            && context.mode === captured.mode
            && context.isContextCurrent === captured.contextCheck
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && actorIdFromStorage(storage) === captured.actorId;
        } catch {
          return false;
        }
      }

      const readMedia = createGenerationHistoryMediaReader(baseUrl, {
        token,
        fetcher,
        ...(options.imagePreviewTimeoutMs === undefined ? {} : { imagePreviewTimeoutMs: options.imagePreviewTimeoutMs }),
        isCurrent,
      });

      async function request(
        path: string,
        method: "GET" | "POST",
        signal: AbortSignal,
        body?: unknown,
      ): Promise<unknown> {
        if (signal.aborted || !isCurrent()) fail("not-sent", "历史请求未发送：当前页面或登录范围已变化。");
        const controller = new AbortController();
        const abortFromCaller = () => controller.abort(signal.reason);
        signal.addEventListener("abort", abortFromCaller, { once: true });
        if (signal.aborted) abortFromCaller();
        let timedOut = false;
        const timeout = globalThis.setTimeout(() => {
          timedOut = true;
          controller.abort(new DOMException("Request timed out", "TimeoutError"));
        }, timeoutMs);
        const dispatched = { value: false };
        let response: Response | null = null;
        try {
          if (controller.signal.aborted || !isCurrent()) fail("not-sent", "历史请求未发送：当前页面或登录范围已变化。");
          dispatched.value = true;
          const responsePromise = fetcher(new URL(`${baseUrl}${path}`), {
            method,
            headers: {
              Accept: "application/json",
              Authorization: `Bearer ${token}`,
              ...(body === undefined ? {} : { "Content-Type": "application/json" }),
            },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            signal: controller.signal,
            credentials: "omit",
            cache: "no-store",
            redirect: "error",
            referrerPolicy: "no-referrer",
          });
          void responsePromise.then((lateResponse) => {
            if (controller.signal.aborted) void lateResponse.body?.cancel().catch(() => undefined);
          }, () => undefined);
          try {
            response = await Promise.race([
              responsePromise,
              new Promise<never>((_resolve, reject) => {
                const rejectAbort = () => reject(controller.signal.reason ?? new DOMException("Request aborted", "AbortError"));
                if (controller.signal.aborted) {
                  rejectAbort();
                  return;
                }
                controller.signal.addEventListener("abort", rejectAbort, { once: true });
                void responsePromise.finally(() => controller.signal.removeEventListener("abort", rejectAbort)).catch(() => undefined);
              }),
            ]);
          } catch {
            fail(method === "POST" && dispatched.value ? "unknown" : "read-error",
              method === "POST" && dispatched.value
                ? (timedOut ? "选用请求等待超时，结果未知；请只读核对当前值。" : "选用请求已发送，结果未知；请只读核对当前值。")
                : "无法读取生成历史。");
          }
          if (!isCurrent() || signal.aborted || controller.signal.aborted) {
            fail(method === "POST" ? "unknown" : "read-error",
              method === "POST" ? "请求已发送，但当前范围已变化；结果未知。" : "读取范围已变化，结果已忽略。");
          }
          if (response.status === 401) fail("unauthorized", errorMessage(401, method === "POST" ? "write" : "read"), 401);
          if (response.status !== 200) {
            const status = response.status;
            let detailCode: string | null = null;
            if (status === 422) {
              try {
                const errorPayload: unknown = await response.json();
                if (isRecord(errorPayload) && typeof errorPayload.detail === "string") {
                  detailCode = errorPayload.detail;
                }
              } catch {
                detailCode = null;
              }
            }
            await response.body?.cancel().catch(() => undefined);
            if (method === "POST" && status >= 400 && status < 500 && status !== 408) {
              fail(status === 409 ? "conflict" : "rejected", errorMessage(status, "write", detailCode), status);
            }
            if (method === "POST" && (status === 408 || status >= 500)) {
              fail("unknown", errorMessage(status, "write"), status);
            }
            fail(status === 409 ? "conflict" : "read-error", errorMessage(status, "read"), status);
          }
          let payload: unknown;
          try {
            payload = await response.json();
          } catch {
            fail(method === "POST" ? "unknown" : "invalid-response",
              method === "POST" ? "选用请求已响应，但回执无法读取；请只读核对当前值。" : "历史响应格式无法读取。", 200);
          }
          if (!isCurrent() || signal.aborted) {
            fail(method === "POST" ? "unknown" : "read-error",
              method === "POST" ? "请求已发送，但当前范围已变化；结果未知。" : "读取范围已变化，结果已忽略。");
          }
          return payload;
        } catch (error) {
          if (error instanceof ChapterGenerationHistoryError) throw error;
          if (method === "POST" && dispatched.value) {
            fail("unknown", timedOut ? "选用请求等待超时，结果未知；请只读核对当前值。" : "选用请求已发送，结果未知；请只读核对当前值。");
          }
          fail("read-error", timedOut ? "读取历史超时。" : "无法读取生成历史。");
        } finally {
          if (response?.body !== null && response?.body !== undefined && !response.bodyUsed) {
            await response.body.cancel().catch(() => undefined);
          }
          globalThis.clearTimeout(timeout);
          signal.removeEventListener("abort", abortFromCaller);
        }
      }

      function snapshotPath(): string {
        const query = new URLSearchParams({
          target_kind: captured.targetKind,
          frame_index: String(captured.frameIndex),
          chat_mode: captured.mode,
        });
        if (captured.storyboardAssetId !== null) query.set("storyboard_asset_id", captured.storyboardAssetId);
        return `/chapters/${encodeURIComponent(captured.chapterId)}/generation-history?${query.toString()}`;
      }

      return {
        isCurrent,
        async readSnapshot(signal): Promise<ChapterGenerationHistorySnapshot> {
          const generation = ++snapshotReadGeneration;
          const payload = await request(snapshotPath(), "GET", signal);
          const snapshot = parseSnapshot(payload, context);
          if (!isCurrent() || signal.aborted || generation !== snapshotReadGeneration) {
            fail("read-error", "历史读取范围已变化，结果已忽略。");
          }
          currentSnapshot.value = snapshot;
          return snapshot;
        },
        async adopt(input: ChapterGenerationHistoryAdoptInput, signal): Promise<ChapterGenerationHistoryAdoptResult> {
          if (!isCurrent() || signal.aborted) fail("not-sent", "选用未发送：当前镜头范围已变化。");
          if (!isIdentifier(input.messageId)
            || !SHA256_HEX.test(input.expectedMessageDigest)
            || !SHA256_HEX.test(input.expectedMappingDigest)
            || !SHA256_HEX.test(input.expectedTargetDigest)
            || (input.expectedMediaUrl !== null && typeof input.expectedMediaUrl !== "string")
            || (input.expectedLockIdentity !== null
              && (!isIdentifier(input.expectedLockIdentity.user_id)
                || typeof input.expectedLockIdentity.acquired_at !== "string"
                || !Number.isFinite(Date.parse(input.expectedLockIdentity.acquired_at))))) {
            fail("not-sent", "选用所需的历史证明不完整，未发送请求。");
          }
          const snapshot = currentSnapshot.value;
          const candidate = snapshot?.messages.find((message) => message.id === input.messageId);
          if (snapshot === null || !snapshot.writable || snapshot.target_proof === null || candidate === undefined
            || candidate.role !== "assistant"
            || candidate.message_digest !== input.expectedMessageDigest
            || snapshot.target_proof.mapping_digest !== input.expectedMappingDigest
            || snapshot.target_proof.target_digest !== input.expectedTargetDigest
            || snapshot.target_proof.media_url !== input.expectedMediaUrl) {
            fail("not-sent", "选用候选与最新历史核对不一致，未发送请求。");
          }
          const candidateUrl = candidate.content;
          if (candidateUrl.trim() !== candidateUrl || candidateUrl === "" || CONTROL_CHARACTERS.test(candidateUrl)) {
            fail("not-sent", "历史媒体地址无法核对，未发送请求。");
          }
          try {
            classifyMediaExportUrl(candidateUrl, baseUrl);
          } catch {
            fail("not-sent", "历史媒体地址不符合安全选用要求，未发送请求。");
          }
          const expectedLockIdentity = input.expectedLockIdentity === null
            ? null
            : { user_id: input.expectedLockIdentity.user_id, acquired_at: input.expectedLockIdentity.acquired_at };
          const body = {
            protocol_version: 1,
            expected_series_id: captured.seriesId,
            target_kind: captured.targetKind,
            storyboard_asset_id: captured.storyboardAssetId,
            expected_frame_index: captured.frameIndex,
            chat_mode: captured.mode,
            message_id: candidate.id,
            expected_message_digest: candidate.message_digest,
            expected_mapping_digest: snapshot.target_proof.mapping_digest,
            expected_target_digest: snapshot.target_proof.target_digest,
            expected_media_url: snapshot.target_proof.media_url,
            expected_lock_identity: expectedLockIdentity,
          };
          const payload = await request(
            `/chapters/${encodeURIComponent(captured.chapterId)}/generation-history/adopt`,
            "POST",
            signal,
            body,
          );
          return parseAdoptResult(payload, context, candidateUrl);
        },
        async readMedia(url, signal): Promise<Blob> {
          return await readMedia(url, captured.mode, signal);
        },
      };
    },
  };
}
