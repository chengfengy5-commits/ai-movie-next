import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  MediaGenerationError,
  type BatchImageGenerationReceipt,
  type BatchImageTaskReceipt,
  type MediaGenerationAssetType,
  type MediaGenerationBatchInput,
  type MediaGenerationFailureKind,
  type MediaGenerationLookupExpectation,
  type MediaGenerationOperation,
  type MediaGenerationPort,
  type MediaGenerationReceipt,
  type MediaGenerationRun,
  type MediaGenerationRunContext,
  type MediaGenerationTaskStatus,
  type MediaGenerationBillingStatus,
  type MediaGenerationVideoInput,
  type VideoGenerationReceipt,
} from "./mediaGeneration";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  browserStorage,
  clearApiSession,
  type StorageLike,
} from "./storage";

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const VIDEO_RECEIPT_KEYS = [
  "submission_id",
  "operation",
  "task_id",
  "message_id",
  "status",
  "quoted_amount",
  "billing_status",
] as const;
const BATCH_TASK_KEYS = ["type", "asset_id", "asset_name", "message_id", "task_id"] as const;
const TASK_STATUSES: readonly MediaGenerationTaskStatus[] = [
  "queued",
  "processing",
  "cancelling",
  "reconciling",
  "completed",
  "failed",
];
const BILLING_STATUSES: readonly MediaGenerationBillingStatus[] = [
  "legacy_unreconciled",
  "unbilled",
  "charged",
  "partially_refunded",
  "refunded",
  "no_charge",
];
const ASSET_TYPES: readonly MediaGenerationAssetType[] = ["character", "scene", "prop", "storyboard"];
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;

export interface MediaGenerationHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

interface PendingRequest {
  readonly controller: AbortController;
  readonly didTimeout: () => boolean;
  wait<T>(promise: Promise<T>): Promise<T>;
  dispose(): void;
}

function fail(kind: MediaGenerationFailureKind, message: string, status: number | null = null): never {
  throw new MediaGenerationError(kind, message, status);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return Object.getOwnPropertySymbols(value).length === 0
    && actual.length === keys.length
    && actual.every((key) => keys.includes(key));
}

function isIdentifier(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const length = Array.from(value).length;
  return length >= 1
    && length <= 36
    && value.trim() === value
    && !CONTROL_CHARACTERS.test(value)
    && !value.includes("/")
    && !value.includes("\\")
    && value !== "."
    && value !== "..";
}

function isIdempotencyKey(value: unknown): value is string {
  if (typeof value !== "string" || value.length < 1 || value.length > 255
    || value.trim() === "" || CONTROL_CHARACTERS.test(value)) return false;
  try {
    return new Headers({ "Idempotency-Key": value }).get("Idempotency-Key") === value;
  } catch {
    return false;
  }
}

function isBearerToken(value: unknown): value is string {
  if (typeof value !== "string" || value === "" || value.trim() === "" || value.trim() !== value
    || CONTROL_CHARACTERS.test(value)) return false;
  try {
    const authorization = `Bearer ${value}`;
    return new Headers({ Authorization: authorization }).get("Authorization") === authorization;
  } catch {
    return false;
  }
}

function storedActorId(storage: StorageLike): string | null {
  try {
    const serialized = storage.getItem(API_USER_KEY);
    if (serialized === null) return null;
    const user: unknown = JSON.parse(serialized);
    return isRecord(user) && isIdentifier(user.id) ? user.id : null;
  } catch {
    return null;
  }
}

function parseVideoReceipt(value: unknown): VideoGenerationReceipt | null {
  if (!isRecord(value) || !hasExactKeys(value, VIDEO_RECEIPT_KEYS)
    || !isIdentifier(value.submission_id)
    || value.operation !== "video.single"
    || !isIdentifier(value.task_id)
    || (value.message_id !== null && !isIdentifier(value.message_id))
    || !TASK_STATUSES.includes(value.status as MediaGenerationTaskStatus)
    || typeof value.quoted_amount !== "number"
    || !Number.isSafeInteger(value.quoted_amount)
    || value.quoted_amount < 0
    || !BILLING_STATUSES.includes(value.billing_status as MediaGenerationBillingStatus)) return null;

  return Object.freeze({
    operation: "video.single",
    submissionId: value.submission_id,
    taskId: value.task_id,
    messageId: value.message_id,
    status: value.status as MediaGenerationTaskStatus,
    quotedAmount: value.quoted_amount,
    billingStatus: value.billing_status as MediaGenerationBillingStatus,
  });
}

function parseBatchReceipt(
  value: unknown,
  expected?: readonly Pick<MediaGenerationBatchInput["assets"][number], "type" | "id">[],
): BatchImageGenerationReceipt | null {
  if (!isRecord(value) || !hasExactKeys(value, ["tasks"]) || !Array.isArray(value.tasks)
    || value.tasks.length < 1 || value.tasks.length > 64) return null;

  const tasks: BatchImageTaskReceipt[] = [];
  for (const rawTask of value.tasks) {
    if (!isRecord(rawTask) || !hasExactKeys(rawTask, BATCH_TASK_KEYS)
      || !ASSET_TYPES.includes(rawTask.type as MediaGenerationAssetType)
      || !isIdentifier(rawTask.asset_id)
      || typeof rawTask.asset_name !== "string"
      || !isIdentifier(rawTask.message_id)
      || !isIdentifier(rawTask.task_id)) return null;
    tasks.push(Object.freeze({
      type: rawTask.type as MediaGenerationAssetType,
      assetId: rawTask.asset_id,
      assetName: rawTask.asset_name,
      messageId: rawTask.message_id,
      taskId: rawTask.task_id,
    }));
  }

  if (expected !== undefined && (tasks.length !== expected.length
    || tasks.some((task, index) => task.type !== expected[index]?.type || task.assetId !== expected[index]?.id))) {
    return null;
  }
  return Object.freeze({ operation: "image.batch", tasks: Object.freeze(tasks) });
}

function createPendingRequest(signal: AbortSignal, timeoutMs: number): PendingRequest {
  const controller = new AbortController();
  let timedOut = false;
  let rejectAbort: (reason?: unknown) => void = () => undefined;
  const aborted = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  void aborted.catch(() => undefined);
  const rejectOnAbort = () => rejectAbort(controller.signal.reason ?? new DOMException("Request aborted", "AbortError"));
  controller.signal.addEventListener("abort", rejectOnAbort, { once: true });
  const abortFromCaller = () => controller.abort(signal.reason);
  signal.addEventListener("abort", abortFromCaller, { once: true });
  if (signal.aborted) abortFromCaller();
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);
  return {
    controller,
    didTimeout: () => timedOut,
    wait<T>(promise: Promise<T>) {
      return Promise.race([promise, aborted]);
    },
    dispose() {
      globalThis.clearTimeout(timeout);
      signal.removeEventListener("abort", abortFromCaller);
      controller.signal.removeEventListener("abort", rejectOnAbort);
    },
  };
}

function readFailureDetail(value: unknown): string | null {
  if (!isRecord(value) || typeof value.detail !== "string" || value.detail.trim() === "") return null;
  return value.detail;
}

export function createMediaGenerationHttpPort(
  apiBaseUrl: string,
  options: MediaGenerationHttpOptions = {},
): MediaGenerationPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) fail("not-sent", "生成请求超时配置无效。", null);

  let resolvedApiBaseUrl: string;
  try {
    resolvedApiBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) fail("not-sent", "生成服务地址无效，未发送请求。");
    throw error;
  }

  return {
    beginRun(context: MediaGenerationRunContext): MediaGenerationRun {
      if (!isIdentifier(context.actorId) || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "登录或页面身份无法核对，未创建生成请求。");
      }
      let token: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
      } catch {
        // An inaccessible local session is not verified.
      }
      if (!isBearerToken(token) || storedActorId(storage) !== context.actorId) {
        fail("not-sent", "当前登录身份无法核对，未创建生成请求。");
      }

      const capturedActorId = context.actorId;
      const capturedContextCheck = context.isContextCurrent;
      const contextIsCurrent = capturedContextCheck.bind(context);
      const isCurrent = () => {
        try {
          return context.actorId === capturedActorId
            && context.isContextCurrent === capturedContextCheck
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && storedActorId(storage) === capturedActorId;
        } catch {
          return false;
        }
      };
      if (!isCurrent()) fail("not-sent", "当前登录或生成页面已变化，未创建请求。");

      const request = async (
        method: "POST" | "GET",
        operation: MediaGenerationOperation,
        idempotencyKey: string,
        signal: AbortSignal,
        body?: object,
        expectedBatchAssets?: readonly Pick<MediaGenerationBatchInput["assets"][number], "type" | "id">[],
      ): Promise<MediaGenerationReceipt> => {
        if (!isIdempotencyKey(idempotencyKey)) fail("not-sent", "幂等键无效，未发送请求。");
        if (!isCurrent() || signal.aborted) fail("not-sent", "登录或生成目标已变化，未发送请求。");

        const pending = createPendingRequest(signal, timeoutMs);
        const url = new URL(
          `${resolvedApiBaseUrl}/${method === "POST"
            ? operation === "video.single" ? "chat/video" : "chat/batch-generate-images"
            : `chat/submissions/${operation}`}`,
        );
        if (method === "GET") url.searchParams.set("idempotency_key", idempotencyKey);
        const headers = new Headers({ Accept: "application/json", Authorization: `Bearer ${token}` });
        if (method === "POST") {
          headers.set("Content-Type", "application/json");
          headers.set("Idempotency-Key", idempotencyKey);
          if (headers.get("Idempotency-Key") !== idempotencyKey) {
            pending.dispose();
            fail("not-sent", "幂等键无法原样通过 HTTP 请求头，未发送请求。");
          }
        }

        let dispatched = false;
        let response: Response | null = null;
        try {
          if (!isCurrent() || pending.controller.signal.aborted) {
            fail("not-sent", "登录或生成目标已变化，未发送请求。");
          }
          dispatched = true;
          const responsePromise = fetcher(url, {
            method,
            headers,
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            signal: pending.controller.signal,
            credentials: "omit",
            cache: "no-store",
            redirect: "error",
            referrerPolicy: "no-referrer",
          });
          void responsePromise.then((lateResponse) => {
            if (pending.controller.signal.aborted) {
              void lateResponse.body?.cancel().catch(() => undefined);
            }
          }, () => undefined);
          response = await pending.wait(responsePromise);

          let payload: unknown;
          try {
            payload = await pending.wait(response.json());
          } catch {
            payload = null;
          }
          if (!isCurrent()) fail(method === "POST" ? "unknown-outcome" : "read-error", "登录或生成目标已变化，结果未写入当前页面。", response.status);
          if (response.status !== 200) {
            const detail = readFailureDetail(payload);
            if (response.status === 401) {
              try {
                if (storage.getItem(API_TOKEN_KEY) === token) clearApiSession(storage);
              } catch {
                // The caller still receives the authentication failure.
              }
              fail("unauthorized", detail ?? "登录状态已失效，请重新登录。", response.status);
            }
            const uncertainPost = method === "POST"
              && (response.status >= 500 || (response.status >= 200 && response.status < 300));
            fail(
              method === "POST" && !uncertainPost ? "rejected" : method === "POST" ? "unknown-outcome" : "read-error",
              detail ?? (method === "POST" ? "生成服务拒绝了提交。" : "暂时无法读取原提交回执。"),
              response.status,
            );
          }

          const receipt = operation === "video.single"
            ? parseVideoReceipt(payload)
            : parseBatchReceipt(payload, expectedBatchAssets);
          if (receipt === null) {
            fail(
              method === "POST" ? "unknown-outcome" : "invalid-response",
              "生成服务返回了无法核对的原提交回执。",
              response.status,
            );
          }
          return receipt;
        } catch (error) {
          if (error instanceof MediaGenerationError) throw error;
          if (signal.aborted) {
            fail(
              method === "POST" && dispatched ? "unknown-outcome" : "read-error",
              method === "POST" && dispatched ? "生成请求已发送但结果未确认，请只用原幂等键核对。" : "请求已取消。",
              response?.status ?? null,
            );
          }
          if (pending.didTimeout()) {
            fail(method === "POST" ? "unknown-outcome" : "read-error", "请求超时。", response?.status ?? null);
          }
          if (!isCurrent()) {
            fail(method === "POST" ? "unknown-outcome" : "read-error", "登录或生成目标已变化。", response?.status ?? null);
          }
          fail(
            dispatched && method === "POST" ? "unknown-outcome" : dispatched ? "read-error" : "not-sent",
            dispatched && method === "POST"
              ? "生成请求已发送但结果未确认，请只用原幂等键核对。"
              : dispatched ? "暂时无法读取原提交回执。" : "请求未发送。",
            response?.status ?? null,
          );
        } finally {
          if (pending.controller.signal.aborted && response?.body !== null && response?.body !== undefined) {
            void response.body.cancel().catch(() => undefined);
          }
          pending.dispose();
        }
      };

      return {
        isCurrent,
        submitVideo(input: MediaGenerationVideoInput, signal: AbortSignal) {
          if (!isRecord(input.target)) fail("not-sent", "视频目标无效，未发送请求。");
          if (input.target.kind === "standalone") {
            if (!hasExactKeys(input.target, ["kind"])) fail("not-sent", "独立视频目标无效，未发送请求。");
          } else if (input.target.kind === "frame_message") {
            if (!hasExactKeys(input.target, ["kind", "chapter_id", "storyboard_asset_id"])
              || !isIdentifier(input.target.chapter_id) || !isIdentifier(input.target.storyboard_asset_id)) {
              fail("not-sent", "视频分镜目标无效，未发送请求。");
            }
          } else if (input.target.kind === "asset_message") {
            if (!hasExactKeys(input.target, ["kind", "chapter_id", "asset_type", "asset_id"])
              || !isIdentifier(input.target.chapter_id)
              || !ASSET_TYPES.includes(input.target.asset_type as MediaGenerationAssetType)
              || !isIdentifier(input.target.asset_id)) {
              fail("not-sent", "视频素材目标无效，未发送请求。");
            }
          } else {
            fail("not-sent", "视频生成不支持此目标类型，未发送请求。");
          }
          if (!isIdentifier(input.modelId) || typeof input.prompt !== "string" || input.prompt.trim() === ""
            || (input.size !== undefined && typeof input.size !== "string")
            || (input.duration !== undefined && (!Number.isSafeInteger(input.duration) || input.duration < 1))
            || (input.resolution !== undefined && typeof input.resolution !== "string")) {
            fail("not-sent", "视频生成参数无效，未发送请求。");
          }
          const refs: Array<{ values: readonly string[]; limit: number }> = [
            { values: input.referenceImages, limit: 64 },
            { values: input.referenceAudios, limit: 5 },
            { values: input.referenceVideos, limit: 5 },
          ];
          if (refs.some(({ values, limit }) => !Array.isArray(values) || values.length > limit
            || values.some((value) => typeof value !== "string" || value.trim() === ""))) {
            fail("not-sent", "视频参考素材列表无效，未发送请求。");
          }
          const body = {
            model_id: input.modelId,
            prompt: input.prompt,
            target: input.target,
            ...(input.size === undefined ? {} : { size: input.size }),
            ...(input.duration === undefined ? {} : { duration: input.duration }),
            ...(input.resolution === undefined ? {} : { resolution: input.resolution }),
            reference_images: [...input.referenceImages],
            reference_audios: [...input.referenceAudios],
            reference_videos: [...input.referenceVideos],
          };
          return request("POST", "video.single", input.idempotencyKey, signal, body) as Promise<VideoGenerationReceipt>;
        },
        submitBatch(input: MediaGenerationBatchInput, signal: AbortSignal) {
          if (!isIdentifier(input.chapterId) || input.assets.length < 1 || input.assets.length > 64) {
            fail("not-sent", "批量生成章节或素材顺序无效，未发送请求。");
          }
          const seen = new Set<string>();
          for (const asset of input.assets) {
            const key = `${asset.type}:${asset.id}`;
            if (!ASSET_TYPES.includes(asset.type) || !isIdentifier(asset.id) || seen.has(key)
              || typeof asset.name !== "string" || typeof asset.description !== "string"
              || !isIdentifier(asset.modelId) || typeof asset.promptId !== "string"
              || asset.promptId.length > 36 || (asset.size !== undefined && typeof asset.size !== "string")
              || !Array.isArray(asset.referenceImages) || asset.referenceImages.length > 64
              || asset.referenceImages.some((value) => typeof value !== "string" || value.trim() === "")) {
              fail("not-sent", "批量生成素材或顺序无效，未发送请求。");
            }
            seen.add(key);
          }
          const expected = input.assets.map((asset) => Object.freeze({ type: asset.type, id: asset.id }));
          const body = {
            chapter_id: input.chapterId,
            assets: input.assets.map((asset) => ({
              type: asset.type,
              id: asset.id,
              name: asset.name,
              description: asset.description,
              model_id: asset.modelId,
              prompt_id: asset.promptId,
              ...(asset.size === undefined ? {} : { size: asset.size }),
              reference_images: [...asset.referenceImages],
            })),
          };
          return request("POST", "image.batch", input.idempotencyKey, signal, body, expected) as Promise<BatchImageGenerationReceipt>;
        },
        lookup(expectation: MediaGenerationLookupExpectation, signal: AbortSignal) {
          if (expectation.operation !== "video.single" && expectation.operation !== "image.batch") {
            fail("read-error", "原提交操作无效，无法读取回执。", null);
          }
          if (expectation.operation === "image.batch" && expectation.batchAssets === undefined) {
            fail("read-error", "批量回执缺少冻结的素材顺序，无法核对。", null);
          }
          return request(
            "GET",
            expectation.operation,
            expectation.idempotencyKey,
            signal,
            undefined,
            expectation.batchAssets,
          );
        },
      };
    },
  };
}
