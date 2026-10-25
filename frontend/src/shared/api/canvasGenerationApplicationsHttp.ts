import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  CanvasGenerationApplicationError,
  type CanvasGenerationApplicationCandidate,
  type CanvasGenerationApplicationDestination,
  type CanvasGenerationApplicationResult,
  type CanvasGenerationApplicationRun,
  type CanvasGenerationApplicationRunContext,
  type CanvasGenerationApplicationsPort,
} from "./canvasGenerationApplications";
import type { CanvasOwnedResultTargetSnapshot } from "./canvasOwnedResults";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const MAX_RESPONSE_BYTES = 512 * 1024;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const LOWER_SHA256 = /^[0-9a-f]{64}$/u;
const TIMESTAMP_WITH_TIMEZONE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/iu;

export interface CanvasGenerationApplicationsHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

interface RequestControl {
  readonly controller: AbortController;
  check(dispatched: boolean): void;
  wait<T>(promise: Promise<T>, dispatched: boolean): Promise<T>;
  cleanup(): void;
}

const TARGET_SNAPSHOT_KEYS = [
  "kind",
  "chapter_id",
  "series_id",
  "stable_storyboard_asset_id",
  "asset_type",
  "asset_id",
  "compat_frame_index",
  "expected_text_digest",
  "media_identity",
  "content_digest",
  "notes_revision",
  "full_frame_digest",
  "full_chapter_digest",
  "expected_asset_image_url",
  "same_series_target_digest",
] as const;

const TARGET_STRING_FIELDS = [
  "kind",
  "chapter_id",
  "series_id",
  "stable_storyboard_asset_id",
  "asset_type",
  "asset_id",
  "expected_text_digest",
  "media_identity",
  "content_digest",
  "full_frame_digest",
  "full_chapter_digest",
  "expected_asset_image_url",
  "same_series_target_digest",
] as const;

const TARGET_IDENTIFIER_FIELDS = [
  "chapter_id",
  "series_id",
  "stable_storyboard_asset_id",
  "asset_id",
] as const;

const TARGET_DIGEST_FIELDS = [
  "expected_text_digest",
  "content_digest",
  "full_frame_digest",
  "full_chapter_digest",
  "same_series_target_digest",
] as const;

const TARGET_INTEGER_FIELDS = ["compat_frame_index", "notes_revision"] as const;

function fail(
  kind: CanvasGenerationApplicationError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new CanvasGenerationApplicationError(kind, message, status);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === expected.length && actual.every((key) => expected.includes(key));
}

function isValidIdentifier(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const codePointLength = Array.from(value).length;
  return codePointLength >= 1
    && codePointLength <= 36
    && value.trim() === value
    && !CONTROL_CHARACTERS.test(value)
    && !value.includes("/")
    && !value.includes("\\")
    && value !== "."
    && value !== "..";
}

function isLowerSha256(value: unknown): value is string {
  return typeof value === "string" && LOWER_SHA256.test(value);
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

function isValidTimestamp(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = TIMESTAMP_WITH_TIMEZONE.exec(value);
  if (match === null) return false;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const timezone = match[8];
  if (timezone === undefined
    || month < 1 || month > 12
    || hour > 23 || minute > 59 || second > 59) return false;
  if (timezone.toUpperCase() !== "Z") {
    const offsetHour = Number(timezone.slice(1, 3));
    const offsetMinute = Number(timezone.slice(4, 6));
    if (offsetHour > 23 || offsetMinute > 59) return false;
  }

  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const monthDays = daysInMonth[month - 1];
  return monthDays !== undefined
    && day >= 1
    && day <= monthDays
    && Number.isFinite(Date.parse(value.replace("t", "T").replace("z", "Z")));
}

function parseDestination(
  value: unknown,
  chapterId: string,
): CanvasGenerationApplicationDestination {
  if (!isRecord(value) || value.chapter_id !== chapterId || !isValidIdentifier(value.chapter_id)) {
    fail("not-sent", "画布目标与当前章节不匹配，未发送应用请求。");
  }

  if (value.kind === "frame_preview") {
    if (hasExactKeys(value, ["kind", "chapter_id", "storyboard_asset_id"])
      && isValidIdentifier(value.storyboard_asset_id)) {
      return Object.freeze({
        kind: "frame_preview",
        chapter_id: chapterId,
        storyboard_asset_id: value.storyboard_asset_id,
      });
    }
    if (hasExactKeys(value, ["kind", "chapter_id", "identity_mode", "frame_index", "expected_chapter_digest"])
      && value.identity_mode === "strict-no-id-v1"
      && typeof value.frame_index === "number"
      && Number.isSafeInteger(value.frame_index)
      && value.frame_index >= 0
      && isLowerSha256(value.expected_chapter_digest)) {
      return Object.freeze({
        kind: "frame_preview",
        chapter_id: chapterId,
        identity_mode: "strict-no-id-v1",
        frame_index: value.frame_index,
        expected_chapter_digest: value.expected_chapter_digest,
      });
    }
    fail("not-sent", "画布分镜目标格式无效，未发送应用请求。");
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

  fail("not-sent", "画布素材目标格式无效，未发送应用请求。");
}

function isValidOriginalTargetSnapshot(value: unknown): value is CanvasOwnedResultTargetSnapshot {
  if (!isRecord(value) || !hasExactKeys(value, TARGET_SNAPSHOT_KEYS)) {
    return false;
  }

  for (const field of TARGET_STRING_FIELDS) {
    const fieldValue = value[field];
    if (fieldValue !== null
      && (typeof fieldValue !== "string" || CONTROL_CHARACTERS.test(fieldValue))) return false;
  }
  for (const field of TARGET_IDENTIFIER_FIELDS) {
    const fieldValue = value[field];
    if (fieldValue !== null && !isValidIdentifier(fieldValue)) return false;
  }
  for (const field of TARGET_DIGEST_FIELDS) {
    const fieldValue = value[field];
    if (fieldValue !== null && !isLowerSha256(fieldValue)) return false;
  }
  for (const field of TARGET_INTEGER_FIELDS) {
    const fieldValue = value[field];
    if (fieldValue !== null
      && (typeof fieldValue !== "number" || !Number.isSafeInteger(fieldValue) || fieldValue < 0)) return false;
  }
  return value.kind === "canvas_frame_preview" || value.kind === "canvas_asset_image";
}

function validateOriginalTarget(
  value: unknown,
  destination: CanvasGenerationApplicationDestination,
  seriesId: string,
): CanvasOwnedResultTargetSnapshot {
  if (!isValidOriginalTargetSnapshot(value)) {
    fail("not-sent", "原始生成目标必须是完整有效的15字段快照，未发送应用请求。");
  }

  if (!isValidIdentifier(value.chapter_id)
    || value.chapter_id !== destination.chapter_id
    || !isValidIdentifier(value.series_id)
    || value.series_id !== seriesId) {
    fail("not-sent", "原始生成目标的剧集或章节身份不匹配，未发送应用请求。");
  }

  if (destination.kind === "frame_preview") {
    if (value.kind !== "canvas_frame_preview" || !isLowerSha256(value.full_frame_digest)) {
      fail("not-sent", "原始分镜目标快照无效，未发送应用请求。");
    }
    if ("storyboard_asset_id" in destination) {
      if (value.stable_storyboard_asset_id !== destination.storyboard_asset_id
        || (value.asset_type !== null && value.asset_type !== "storyboard")) {
        fail("not-sent", "原始稳定分镜身份与应用目标不一致，未发送应用请求。");
      }
    } else if (value.stable_storyboard_asset_id !== null
      || value.asset_type !== null
      || value.asset_id !== null
      || value.compat_frame_index !== destination.frame_index
      || value.full_chapter_digest !== destination.expected_chapter_digest) {
      fail("not-sent", "原始无稳定ID分镜身份与应用目标不一致，未发送应用请求。");
    }
    return value;
  }

  if (value.kind !== "canvas_asset_image"
    || value.asset_type !== destination.asset_type
    || value.asset_id !== destination.asset_id
    || value.stable_storyboard_asset_id !== null
    || value.compat_frame_index !== null
    || !isLowerSha256(value.same_series_target_digest)) {
    fail("not-sent", "原始素材身份与应用目标不一致，未发送应用请求。");
  }
  return value;
}

function validateCandidate(
  value: unknown,
  destination: CanvasGenerationApplicationDestination,
  seriesId: string,
): CanvasGenerationApplicationCandidate {
  if (!isRecord(value)
    || !isValidIdentifier(value.task_id)
    || !isValidIdentifier(value.result_ref)
    || !isLowerSha256(value.application_key)) {
    fail("not-sent", "画布候选身份或服务端应用键无效，未发送应用请求。");
  }
  const expectedTarget = validateOriginalTarget(value.expected_target, destination, seriesId);
  return Object.freeze({
    task_id: value.task_id,
    result_ref: value.result_ref,
    expected_target: expectedTarget,
    application_key: value.application_key,
  });
}

function routeFor(destination: CanvasGenerationApplicationDestination): string {
  if (destination.kind === "frame_preview") {
    return `/chapters/${encodeURIComponent(destination.chapter_id)}`;
  }
  const resource = destination.asset_type === "character"
    ? "characters"
    : destination.asset_type === "scene" ? "scenes" : "props";
  return `/${resource}/${encodeURIComponent(destination.asset_id)}`;
}

function parseResult(
  value: unknown,
  taskId: string,
  resultRef: string,
): CanvasGenerationApplicationResult {
  if (!isRecord(value)
    || !hasExactKeys(value, ["task_id", "result_ref", "status", "application"])
    || value.task_id !== taskId
    || value.result_ref !== resultRef
    || (value.status !== "applied" && value.status !== "already_applied")) {
    fail("unknown-outcome", "应用请求已发送，但服务器回执无法与原始候选核对。", 200);
  }

  const application = value.application;
  if (!isRecord(application)
    || !hasExactKeys(application, ["application_ref", "target_revision", "committed_at"])
    || !isValidIdentifier(application.application_ref)
    || !isLowerSha256(application.target_revision)
    || !isValidTimestamp(application.committed_at)) {
    fail("unknown-outcome", "应用请求已发送，但服务器回执不完整或无效。", 200);
  }

  return Object.freeze({
    task_id: taskId,
    result_ref: resultRef,
    status: value.status,
    application: Object.freeze({
      application_ref: application.application_ref,
      target_revision: application.target_revision,
      committed_at: application.committed_at,
    }),
  });
}

function statusError(status: number): CanvasGenerationApplicationError {
  if (status === 401) return new CanvasGenerationApplicationError("unauthorized", "登录身份未通过画布应用校验。", status);
  if (status === 403) return new CanvasGenerationApplicationError("forbidden", "当前身份无权应用该画布结果。", status);
  if (status === 404) return new CanvasGenerationApplicationError("not-found", "画布目标或候选结果不存在。", status);
  if (status === 409) return new CanvasGenerationApplicationError("conflict", "画布目标、原始版本或编辑锁已变化，请重新核对。", status);
  if (status === 422) return new CanvasGenerationApplicationError("invalid-request", "画布应用请求未被服务接受。", status);
  return new CanvasGenerationApplicationError("unknown-outcome", "画布应用结果尚未确认，请查询原始应用回执。", status);
}

function createRequestControl(
  contextIsCurrent: () => boolean,
  storage: StorageLike,
  token: string,
  actorId: string,
  callerSignal: AbortSignal,
  timeoutMs: number,
): RequestControl {
  if (callerSignal.aborted) fail("not-sent", "画布应用已取消，未发送请求。");

  const controller = new AbortController();
  let timedOut = false;
  let rejectAbort: (reason?: unknown) => void = () => undefined;
  const abortPromise = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  const check = (dispatched: boolean) => {
    const outcomeKind = dispatched ? "unknown-outcome" : "not-sent";
    if (timedOut) {
      fail(outcomeKind, dispatched
        ? "画布应用请求已发送但超过期限，结果尚未确认。"
        : "画布应用在发送前超过期限，未写入。");
    }
    if (callerSignal.aborted || controller.signal.aborted) {
      fail(outcomeKind, dispatched
        ? "画布应用请求已发送后取消，结果尚未确认。"
        : "画布应用已取消，未发送请求。");
    }

    let current = false;
    try {
      current = contextIsCurrent()
        && storage.getItem(API_TOKEN_KEY) === token
        && storedActorId(storage) === actorId;
    } catch {
      current = false;
    }
    if (!current) {
      fail(outcomeKind, dispatched
        ? "画布应用请求已发送后登录或页面上下文变化，结果尚未确认。"
        : "登录或画布上下文已变化，未发送应用请求。");
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
    async wait<T>(promise: Promise<T>, dispatched: boolean): Promise<T> {
      check(dispatched);
      try {
        const value = await Promise.race([promise, abortPromise]);
        check(dispatched);
        return value;
      } catch (error) {
        if (error instanceof CanvasGenerationApplicationError) throw error;
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
    // Fetch may already have closed the response body.
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

async function readJsonBody(response: Response, control: RequestControl): Promise<unknown> {
  if (response.body === null) {
    fail("unknown-outcome", "画布应用请求已发送，但服务器没有返回回执正文。", 200);
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let bodyLength = 0;
  let completed = false;
  const cancelReader = () => {
    try {
      void reader.cancel().catch(() => undefined);
    } catch {
      // Abort may close the stream before cancellation starts.
    }
  };
  control.controller.signal.addEventListener("abort", cancelReader, { once: true });

  try {
    while (true) {
      control.check(true);
      const chunk = await control.wait(reader.read(), true);
      control.check(true);
      if (chunk.done) {
        completed = true;
        break;
      }
      if (bodyLength + chunk.value.byteLength > MAX_RESPONSE_BYTES) {
        cancelReader();
        fail("unknown-outcome", "画布应用请求已发送，但回执超过512KiB上限。", 200);
      }
      chunks.push(chunk.value);
      bodyLength += chunk.value.byteLength;
    }

    const bytes = new Uint8Array(bodyLength);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    let decoded: string;
    try {
      decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      fail("unknown-outcome", "画布应用请求已发送，但回执不是有效UTF-8。", 200);
    }
    try {
      return JSON.parse(decoded) as unknown;
    } catch {
      fail("unknown-outcome", "画布应用请求已发送，但回执不是有效JSON。", 200);
    }
  } catch (error) {
    if (error instanceof CanvasGenerationApplicationError) throw error;
    control.check(true);
    fail("unknown-outcome", "读取画布应用回执时连接中断，结果尚未确认。", 200);
  } finally {
    control.controller.signal.removeEventListener("abort", cancelReader);
    if (!completed) cancelReader();
    try {
      reader.releaseLock();
    } catch {
      // The pending read releases the lock as the stream cancellation settles.
    }
  }
}

export function createCanvasGenerationApplicationsHttpPort(
  apiBaseUrl: string,
  options: CanvasGenerationApplicationsHttpOptions = {},
): CanvasGenerationApplicationsPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    fail("not-sent", "画布应用超时配置无效，未发送请求。");
  }

  let resolvedApiBaseUrl: string;
  try {
    resolvedApiBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) {
      fail("not-sent", "画布应用服务地址无效，未发送请求。");
    }
    throw error;
  }

  return {
    beginRun(context: CanvasGenerationApplicationRunContext): CanvasGenerationApplicationRun {
      if (!isValidIdentifier(context.actorId)
        || !isValidIdentifier(context.seriesId)
        || !isValidIdentifier(context.chapterId)
        || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "登录或画布上下文无法核对，未创建应用服务。");
      }

      let token: string | null = null;
      let actorId: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
        actorId = storedActorId(storage);
      } catch {
        // An unreadable session is not sufficient authority to send a write.
      }
      if (token === null || token === "" || token.trim() !== token || actorId !== context.actorId) {
        fail("not-sent", "当前登录身份无法核对，未创建画布应用服务。");
      }

      const capturedActorId = context.actorId;
      const capturedSeriesId = context.seriesId;
      const capturedChapterId = context.chapterId;
      const contextIsCurrent = context.isContextCurrent.bind(context);
      const isCurrent = () => {
        try {
          return context.actorId === capturedActorId
            && context.seriesId === capturedSeriesId
            && context.chapterId === capturedChapterId
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && storedActorId(storage) === capturedActorId;
        } catch {
          return false;
        }
      };
      if (!isCurrent()) fail("not-sent", "登录或画布上下文已变化，未创建画布应用服务。");

      return {
        isCurrent,
        async apply(destinationInput, candidateInput, signal) {
          const destination = parseDestination(destinationInput, capturedChapterId);
          const candidate = validateCandidate(candidateInput, destination, capturedSeriesId);
          const endpoint = new URL(`${resolvedApiBaseUrl}${routeFor(destination)}`);
          const requestBody = JSON.stringify({
            generation_application: {
              task_id: candidate.task_id,
              result_ref: candidate.result_ref,
              expected_target: candidate.expected_target,
              application_key: candidate.application_key,
            },
          });
          const control = createRequestControl(
            isCurrent,
            storage,
            token,
            capturedActorId,
            signal,
            timeoutMs,
          );
          let response: Response | null = null;
          let dispatched = false;
          try {
            control.check(false);
            dispatched = true;
            const request = fetcher(endpoint, {
              method: "PUT",
              headers: {
                Accept: "application/json",
                Authorization: `Bearer ${token}`,
                "Content-Type": "application/json",
              },
              body: requestBody,
              credentials: "omit",
              cache: "no-store",
              redirect: "error",
              referrerPolicy: "no-referrer",
              signal: control.controller.signal,
            });
            const responseWithCleanup = request.then((received) => {
              try {
                control.check(true);
              } catch {
                cancelLateResponse(received);
              }
              return received;
            });
            response = await control.wait(responseWithCleanup, true);

            if (response.status !== 200) {
              const error = statusError(response.status);
              await cancelResponseBody(response, control);
              control.check(true);
              throw error;
            }

            control.check(true);
            const payload = await readJsonBody(response, control);
            control.check(true);
            const result = parseResult(payload, candidate.task_id, candidate.result_ref);
            control.check(true);
            return result;
          } catch (error) {
            if (response !== null) {
              try {
                await cancelResponseBody(response, control);
              } catch (cancelError) {
                if (cancelError instanceof CanvasGenerationApplicationError) throw cancelError;
              }
            }
            if (error instanceof CanvasGenerationApplicationError) throw error;
            control.check(dispatched);
            fail("unknown-outcome", "画布应用请求已发送，但结果尚未确认，请查询原始应用回执。");
          } finally {
            control.cleanup();
          }
        },
      };
    },
  };
}
