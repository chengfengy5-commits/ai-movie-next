import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  CanvasImageGenerationError,
  isValidCanvasImageGenerationSize,
  type CanvasImageGenerationBillingStatus,
  type CanvasImageGenerationPort,
  type CanvasImageGenerationReceipt,
  type CanvasImageGenerationRun,
  type CanvasImageGenerationRunContext,
  type CanvasImageGenerationSubmitInput,
  type CanvasImageGenerationTaskStatus,
} from "./canvasImageGeneration";
import type { CanvasOwnedResultsTarget } from "./canvasOwnedResults";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const MAX_RESPONSE_BYTES = 512 * 1024;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const LOWER_SHA256 = /^[0-9a-f]{64}$/u;
const RECEIPT_KEYS = [
  "submission_id",
  "operation",
  "task_id",
  "message_id",
  "status",
  "quoted_amount",
  "billing_status",
] as const;
const TASK_STATUSES: readonly CanvasImageGenerationTaskStatus[] = [
  "queued",
  "processing",
  "cancelling",
  "reconciling",
  "completed",
  "failed",
];
const BILLING_STATUSES: readonly CanvasImageGenerationBillingStatus[] = [
  "legacy_unreconciled",
  "unbilled",
  "charged",
  "partially_refunded",
  "refunded",
  "no_charge",
];

export interface CanvasImageGenerationHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

interface RequestControl {
  readonly controller: AbortController;
  check(dispatched: boolean): void;
  setResponseStatus(status: number | null): void;
  wait<T>(promise: Promise<T>, dispatched: boolean): Promise<T>;
  cleanup(): void;
}

interface NormalizedSubmitInput {
  readonly modelId: string;
  readonly prompt: string;
  readonly destination: CanvasOwnedResultsTarget;
  readonly size?: string;
  readonly idempotencyKey: string;
}

type RequestMode = "submit" | "lookup";

function fail(
  kind: CanvasImageGenerationError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new CanvasImageGenerationError(kind, message, status);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value);
  return Object.getOwnPropertySymbols(value).length === 0
    && actual.length === expected.length
    && actual.every((key) => expected.includes(key));
}

function hasNoControlCharacters(value: string): boolean {
  return !CONTROL_CHARACTERS.test(value);
}

function isValidIdentifier(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const length = Array.from(value).length;
  return length >= 1
    && length <= 36
    && value.trim() === value
    && hasNoControlCharacters(value)
    && !value.includes("/")
    && !value.includes("\\")
    && value !== "."
    && value !== "..";
}

function isValidIdempotencyKey(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const length = Array.from(value).length;
  if (length < 1 || length > 255 || value.trim() === "" || !hasNoControlCharacters(value)) {
    return false;
  }

  try {
    const header = new Headers({ "Idempotency-Key": value });
    return header.get("Idempotency-Key") === value;
  } catch {
    return false;
  }
}

function isValidBearerToken(value: unknown): value is string {
  if (typeof value !== "string"
    || value === ""
    || value.trim() !== value
    || !hasNoControlCharacters(value)) {
    return false;
  }
  try {
    const authorization = `Bearer ${value}`;
    return new Headers({ Authorization: authorization }).get("Authorization") === authorization;
  } catch {
    return false;
  }
}

function isNonBlankText(value: unknown, maximumLength?: number): value is string {
  if (typeof value !== "string" || value.trim() === "") return false;
  return maximumLength === undefined || Array.from(value).length <= maximumLength;
}

function parseDestination(value: unknown, chapterId: string): CanvasOwnedResultsTarget | null {
  if (!isRecord(value) || value.chapter_id !== chapterId || !isValidIdentifier(value.chapter_id)) {
    return null;
  }

  if (value.kind === "frame_preview") {
    if ("storyboard_asset_id" in value) {
      if (!hasExactKeys(value, ["kind", "chapter_id", "storyboard_asset_id"])
        || !isValidIdentifier(value.storyboard_asset_id)) {
        return null;
      }
      return Object.freeze({
        kind: "frame_preview",
        chapter_id: chapterId,
        storyboard_asset_id: value.storyboard_asset_id,
      });
    }

    if (!hasExactKeys(value, [
      "kind",
      "chapter_id",
      "identity_mode",
      "frame_index",
      "expected_chapter_digest",
    ])
      || value.identity_mode !== "strict-no-id-v1"
      || typeof value.frame_index !== "number"
      || !Number.isSafeInteger(value.frame_index)
      || value.frame_index < 0
      || typeof value.expected_chapter_digest !== "string"
      || !LOWER_SHA256.test(value.expected_chapter_digest)) {
      return null;
    }
    return Object.freeze({
      kind: "frame_preview",
      chapter_id: chapterId,
      identity_mode: "strict-no-id-v1",
      frame_index: value.frame_index,
      expected_chapter_digest: value.expected_chapter_digest,
    });
  }

  if (value.kind === "asset_image"
    && hasExactKeys(value, ["kind", "chapter_id", "asset_type", "asset_id"])
    && (value.asset_type === "character" || value.asset_type === "scene" || value.asset_type === "prop")
    && isValidIdentifier(value.asset_id)) {
    return Object.freeze({
      kind: "asset_image",
      chapter_id: chapterId,
      asset_type: value.asset_type,
      asset_id: value.asset_id,
    });
  }

  return null;
}

function normalizeSubmitInput(value: unknown, chapterId: string): NormalizedSubmitInput | null {
  const hasSize = isRecord(value) && Object.prototype.hasOwnProperty.call(value, "size");
  if (!isRecord(value)
    || !hasExactKeys(value, hasSize
      ? ["modelId", "prompt", "destination", "size", "idempotencyKey"]
      : ["modelId", "prompt", "destination", "idempotencyKey"])
    || !isNonBlankText(value.modelId, 255)
    || !isNonBlankText(value.prompt)
    || (value.size !== undefined && !isValidCanvasImageGenerationSize(value.size))
    || !isValidIdempotencyKey(value.idempotencyKey)) {
    return null;
  }
  const destination = parseDestination(value.destination, chapterId);
  if (destination === null) return null;

  return Object.freeze({
    modelId: value.modelId,
    prompt: value.prompt,
    destination,
    ...(value.size === undefined ? {} : { size: value.size }),
    idempotencyKey: value.idempotencyKey,
  });
}

function storedActorId(storage: StorageLike): string | null {
  try {
    const serialized = storage.getItem(API_USER_KEY);
    if (serialized === null) return null;
    const user: unknown = JSON.parse(serialized);
    if (!isRecord(user) || !isValidIdentifier(user.id)) return null;
    return user.id;
  } catch {
    return null;
  }
}

function parseReceipt(value: unknown): CanvasImageGenerationReceipt | null {
  if (!isRecord(value) || !hasExactKeys(value, RECEIPT_KEYS)
    || !isValidIdentifier(value.submission_id)
    || value.operation !== "image.single"
    || !isValidIdentifier(value.task_id)
    || (value.message_id !== null && !isValidIdentifier(value.message_id))
    || !TASK_STATUSES.includes(value.status as CanvasImageGenerationTaskStatus)
    || typeof value.quoted_amount !== "number"
    || !Number.isSafeInteger(value.quoted_amount)
    || value.quoted_amount < 0
    || !BILLING_STATUSES.includes(value.billing_status as CanvasImageGenerationBillingStatus)) {
    return null;
  }

  return Object.freeze({
    submissionId: value.submission_id,
    operation: "image.single",
    taskId: value.task_id,
    messageId: value.message_id,
    status: value.status as CanvasImageGenerationTaskStatus,
    quotedAmount: value.quoted_amount,
    billingStatus: value.billing_status as CanvasImageGenerationBillingStatus,
  });
}

function outcomeKind(mode: RequestMode, dispatched: boolean): CanvasImageGenerationError["kind"] {
  if (!dispatched) return "not-sent";
  return mode === "submit" ? "unknown-outcome" : "read-error";
}

function interruptedMessage(mode: RequestMode, timedOut: boolean): string {
  if (mode === "submit") {
    return timedOut
      ? "生成请求超时，提交结果尚未确认。请保留原幂等键后再核对。"
      : "生成请求已取消或登录画布上下文已变化，提交结果尚未确认。请保留原幂等键后再核对。";
  }
  return timedOut
    ? "提交状态查询超时，请稍后重新查询。"
    : "提交状态查询已取消或登录画布上下文已变化。";
}

function newRequestControl(
  mode: RequestMode,
  isCurrent: () => boolean,
  callerSignal: AbortSignal,
  timeoutMs: number,
): RequestControl {
  if (callerSignal.aborted) fail("not-sent", "请求已取消，未发送。", null);

  const controller = new AbortController();
  let timedOut = false;
  let responseStatus: number | null = null;
  let rejectAbort: (reason?: unknown) => void = () => undefined;
  const abortPromise = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  void abortPromise.catch(() => undefined);
  const check = (dispatched: boolean) => {
    if (timedOut || callerSignal.aborted || controller.signal.aborted) {
      fail(outcomeKind(mode, dispatched), interruptedMessage(mode, timedOut), responseStatus);
    }
    let current = false;
    try {
      current = isCurrent();
    } catch {
      current = false;
    }
    if (!current) {
      fail(outcomeKind(mode, dispatched), interruptedMessage(mode, false), responseStatus);
    }
  };
  const onControllerAbort = () => {
    try {
      check(true);
    } catch (error) {
      rejectAbort(error);
    }
  };
  const abortFromCaller = () => controller.abort(callerSignal.reason);
  controller.signal.addEventListener("abort", onControllerAbort, { once: true });
  callerSignal.addEventListener("abort", abortFromCaller, { once: true });
  if (callerSignal.aborted) abortFromCaller();
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  return {
    controller,
    check,
    setResponseStatus(status) {
      responseStatus = status;
    },
    async wait<T>(promise: Promise<T>, dispatched: boolean): Promise<T> {
      check(dispatched);
      try {
        const value = await Promise.race([promise, abortPromise]);
        check(dispatched);
        return value;
      } catch (error) {
        if (error instanceof CanvasImageGenerationError) throw error;
        check(dispatched);
        throw error;
      }
    },
    cleanup() {
      globalThis.clearTimeout(timeout);
      callerSignal.removeEventListener("abort", abortFromCaller);
      controller.signal.removeEventListener("abort", onControllerAbort);
    },
  };
}

function cancelLateResponse(response: Response): void {
  if (response.body === null || response.bodyUsed) return;
  try {
    void response.body.cancel().catch(() => undefined);
  } catch {
    // Fetch may already have closed the body while an abort is in progress.
  }
}

async function cancelResponseBody(response: Response | null, control: RequestControl): Promise<void> {
  if (response === null || response.body === null || response.bodyUsed) {
    control.check(true);
    return;
  }
  let cancellation: Promise<void>;
  try {
    cancellation = response.body.cancel();
  } catch {
    control.check(true);
    return;
  }
  try {
    await control.wait(cancellation, true);
  } catch {
    control.check(true);
  }
  control.check(true);
}

function cancelReader(reader: ReadableStreamDefaultReader<Uint8Array>): void {
  try {
    void reader.cancel().catch(() => {
      // The reader may already have been cancelled by fetch abort handling.
    });
  } catch {
    // The reader may already have been released.
  }
}

function bodyFailureKind(mode: RequestMode): CanvasImageGenerationError["kind"] {
  return mode === "submit" ? "unknown-outcome" : "invalid-response";
}

async function readJsonBody(
  response: Response,
  control: RequestControl,
  mode: RequestMode,
): Promise<unknown> {
  const contentLength = response.headers.get("content-length");
  if (contentLength !== null) {
    const declaredLength = Number(contentLength);
    if (Number.isFinite(declaredLength) && declaredLength > MAX_RESPONSE_BYTES) {
      await cancelResponseBody(response, control);
      control.check(true);
      fail(bodyFailureKind(mode), "生成服务响应超过 512 KiB。", response.status);
    }
  }
  if (response.body === null) {
    fail(bodyFailureKind(mode), "生成服务未返回响应正文。", response.status);
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let bodyLength = 0;
  let completed = false;
  const abortReader = () => cancelReader(reader);
  control.controller.signal.addEventListener("abort", abortReader, { once: true });

  try {
    try {
      while (true) {
        control.check(true);
        let result: ReadableStreamReadResult<Uint8Array>;
        try {
          result = await control.wait(reader.read(), true);
        } catch {
          control.check(true);
          fail(
            mode === "submit" ? "unknown-outcome" : "read-error",
            "读取生成服务响应时连接中断。",
            response.status,
          );
        }
        if (result.done) {
          completed = true;
          break;
        }
        if (bodyLength + result.value.byteLength > MAX_RESPONSE_BYTES) {
          cancelReader(reader);
          control.check(true);
          fail(bodyFailureKind(mode), "生成服务响应超过 512 KiB。", response.status);
        }
        chunks.push(result.value);
        bodyLength += result.value.byteLength;
      }
    } catch (error) {
      if (error instanceof CanvasImageGenerationError) throw error;
      control.check(true);
      fail(
        mode === "submit" ? "unknown-outcome" : "read-error",
        "读取生成服务响应时连接中断。",
        response.status,
      );
    }

    control.check(true);
    const bytes = new Uint8Array(bodyLength);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    try {
      return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
    } catch {
      fail(bodyFailureKind(mode), "生成服务返回了无效 JSON。", response.status);
    }
  } finally {
    control.controller.signal.removeEventListener("abort", abortReader);
    if (!completed) cancelReader(reader);
    try {
      reader.releaseLock();
    } catch {
      // A pending read releases its lock once cancellation settles.
    }
  }
}

function responseError(mode: RequestMode, status: number): CanvasImageGenerationError {
  if (mode === "submit") {
    if (status >= 400 && status < 500) {
      return new CanvasImageGenerationError("rejected", "生成请求被服务端拒绝。", status);
    }
    return new CanvasImageGenerationError("unknown-outcome", "生成请求已发送，但提交结果尚未确认。", status);
  }

  if (status === 401) return new CanvasImageGenerationError("unauthorized", "登录身份无法查询该提交。", status);
  if (status === 403) return new CanvasImageGenerationError("forbidden", "当前身份无权查询该提交。", status);
  if (status === 404) return new CanvasImageGenerationError("not-found", "此时未找到该幂等键对应的提交。", status);
  if (status >= 500) return new CanvasImageGenerationError("unavailable", "提交状态服务暂时不可用。", status);
  if (status >= 200 && status < 300) {
    return new CanvasImageGenerationError("invalid-response", "提交状态服务返回了无法识别的成功状态。", status);
  }
  return new CanvasImageGenerationError("read-error", "提交状态查询未能完成。", status);
}

function buildRequestUrl(baseUrl: string, path: string): URL {
  const url = new URL(`${baseUrl}${path}`);
  if (url.origin !== new URL(baseUrl).origin) {
    fail("not-sent", "生成服务地址与当前 API 不一致，未发送请求。");
  }
  return url;
}

export function createCanvasImageGenerationHttpPort(
  apiBaseUrl: string,
  options: CanvasImageGenerationHttpOptions = {},
): CanvasImageGenerationPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    fail("not-sent", "生成请求超时配置无效，未发送请求。");
  }

  let resolvedApiBaseUrl: string;
  try {
    resolvedApiBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) {
      fail("not-sent", "生成服务地址无效，未发送请求。");
    }
    throw error;
  }

  return {
    beginRun(context: CanvasImageGenerationRunContext): CanvasImageGenerationRun {
      if (!isValidIdentifier(context.actorId)
        || !isValidIdentifier(context.seriesId)
        || !isValidIdentifier(context.chapterId)
        || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "登录、剧集或章节身份无法核对，未创建生成请求。");
      }

      let token: string | null = null;
      let actor: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
        actor = storedActorId(storage);
      } catch {
        // Treat inaccessible browser storage as an unverified local session.
      }
      if (!isValidBearerToken(token) || actor !== context.actorId) {
        fail("not-sent", "当前登录身份无法核对，未创建生成请求。");
      }

      const capturedActorId = context.actorId;
      const capturedSeriesId = context.seriesId;
      const capturedChapterId = context.chapterId;
      const capturedContextCheck = context.isContextCurrent;
      const contextIsCurrent = capturedContextCheck.bind(context);
      const isCurrent = () => {
        try {
          return context.actorId === capturedActorId
            && context.seriesId === capturedSeriesId
            && context.chapterId === capturedChapterId
            && context.isContextCurrent === capturedContextCheck
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && storedActorId(storage) === capturedActorId;
        } catch {
          return false;
        }
      };
      if (!isCurrent()) fail("not-sent", "当前登录或画布上下文已变化，未创建生成请求。");

      const request = async (
        mode: RequestMode,
        idempotencyKey: string,
        signal: AbortSignal,
        input?: NormalizedSubmitInput,
      ): Promise<CanvasImageGenerationReceipt> => {
        if (!isValidIdempotencyKey(idempotencyKey)) {
          fail("not-sent", "幂等键无效，未发送请求。");
        }
        const control = newRequestControl(mode, isCurrent, signal, timeoutMs);
        let response: Response | null = null;
        let dispatched = false;
        try {
          control.check(false);
          const endpoint = mode === "submit"
            ? buildRequestUrl(resolvedApiBaseUrl, "/chat/image")
            : buildRequestUrl(resolvedApiBaseUrl, "/submissions/image.single");
          if (mode === "lookup") endpoint.searchParams.set("idempotency_key", idempotencyKey);

          const headers = new Headers({
            Accept: "application/json",
            Authorization: `Bearer ${token}`,
          });
          if (mode === "submit") {
            headers.set("Content-Type", "application/json");
            headers.set("Idempotency-Key", idempotencyKey);
            if (headers.get("Idempotency-Key") !== idempotencyKey) {
              fail("not-sent", "幂等键无法通过 HTTP 请求头原样传递，未发送请求。");
            }
          }

          const body = mode === "submit"
            ? JSON.stringify({
              model_id: input?.modelId,
              prompt: input?.prompt,
              ...(input?.size === undefined ? {} : { size: input.size }),
              target: { kind: "canvas_apply", destination: input?.destination },
            })
            : undefined;
          control.check(false);
          dispatched = true;
          const requestPromise = fetcher(endpoint, {
            method: mode === "submit" ? "POST" : "GET",
            headers,
            ...(body === undefined ? {} : { body }),
            signal: control.controller.signal,
            credentials: "omit",
            cache: "no-store",
            redirect: "error",
            referrerPolicy: "no-referrer",
          });
          const responseWithCleanup = requestPromise.then((received) => {
            control.setResponseStatus(received.status);
            try {
              control.check(true);
            } catch {
              cancelLateResponse(received);
            }
            return received;
          });
          response = await control.wait(responseWithCleanup, true);

          if (response.status !== 200) {
            const error = responseError(mode, response.status);
            await cancelResponseBody(response, control);
            control.check(true);
            throw error;
          }

          const payload = await readJsonBody(response, control, mode);
          control.check(true);
          const receipt = parseReceipt(payload);
          if (receipt === null) {
            fail(
              mode === "submit" ? "unknown-outcome" : "invalid-response",
              "生成服务返回了无法核对的提交回执。",
              response.status,
            );
          }
          return receipt;
        } catch (error) {
          if (response !== null) {
            try {
              await cancelResponseBody(response, control);
            } catch (cancelError) {
              if (cancelError instanceof CanvasImageGenerationError) throw cancelError;
            }
          }
          if (error instanceof CanvasImageGenerationError) {
            throw error;
          }
          control.check(dispatched);
          const message = !dispatched
            ? "请求校验未通过，未发送请求。"
            : mode === "submit"
              ? "生成请求已发送，但网络结果无法确认。请保留原幂等键后再核对。"
              : "提交状态读取暂时无法完成。";
          fail(
            outcomeKind(mode, dispatched),
            message,
            response?.status ?? null,
          );
        } finally {
          control.cleanup();
        }
      };

      return {
        isCurrent,
        async submit(input: CanvasImageGenerationSubmitInput, signal: AbortSignal) {
          const normalized = normalizeSubmitInput(input, capturedChapterId);
          if (normalized === null) fail("not-sent", "单图生成请求格式无效，未发送请求。");
          return request("submit", normalized.idempotencyKey, signal, normalized);
        },
        async lookup(idempotencyKey: string, signal: AbortSignal) {
          return request("lookup", idempotencyKey, signal);
        },
      };
    },
  };
}
