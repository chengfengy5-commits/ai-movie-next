import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  CanvasOwnedResultsError,
  type CanvasOwnedResultApplicationProof,
  type CanvasOwnedResultEligibility,
  type CanvasOwnedResultItem,
  type CanvasOwnedResultTargetSnapshot,
  type CanvasOwnedResultsPage,
  type CanvasOwnedResultsPageOptions,
  type CanvasOwnedResultsPort,
  type CanvasOwnedResultsRun,
  type CanvasOwnedResultsRunContext,
  type CanvasOwnedResultsTarget,
} from "./canvasOwnedResults";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const DEFAULT_PAGE_LIMIT = 20;
const MAX_PAGE_LIMIT = 50;
const MAX_CURSOR_LENGTH = 2048;
const MAX_RESPONSE_BYTES = 512 * 1024;
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const LOWER_SHA256 = /^[0-9a-f]{64}$/u;
const URL_SAFE_BASE64 = /^[A-Za-z0-9_-]+={0,2}$/u;
const TIMESTAMP_WITH_TIMEZONE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/u;
const UTC_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|\+00:00)$/u;

export interface CanvasOwnedResultsHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

interface NormalizedPageOptions {
  readonly limit: number;
  readonly cursor: string | null;
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

const TARGET_ID_FIELDS = [
  "chapter_id",
  "series_id",
  "stable_storyboard_asset_id",
  "asset_id",
] as const;

const TARGET_HASH_FIELDS = [
  "expected_text_digest",
  "content_digest",
  "full_frame_digest",
  "full_chapter_digest",
  "same_series_target_digest",
] as const;

const TARGET_INTEGER_FIELDS = ["compat_frame_index", "notes_revision"] as const;

function fail(
  kind: CanvasOwnedResultsError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new CanvasOwnedResultsError(kind, message, status);
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

function hasOnlyKeys(value: Record<string, unknown>, allowed: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function hasOwn(value: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function isValidIdentifier(value: unknown): value is string {
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

function isLowerSha256(value: unknown): value is string {
  return typeof value === "string" && LOWER_SHA256.test(value);
}

function isValidOpaqueCursor(value: unknown): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_CURSOR_LENGTH) return false;
  if (!URL_SAFE_BASE64.test(value)) return false;
  const unpaddedLength = value.replace(/=+$/u, "").length;
  const paddingLength = value.length - unpaddedLength;
  if (unpaddedLength % 4 === 1) return false;
  if (paddingLength === 0) return true;
  return value.length % 4 === 0
    && ((paddingLength === 1 && unpaddedLength % 4 === 3)
      || (paddingLength === 2 && unpaddedLength % 4 === 2));
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

function parseTargetSelector(
  value: unknown,
  chapterId: string,
  errorKind: CanvasOwnedResultsError["kind"],
): CanvasOwnedResultsTarget {
  const invalid = (): never => fail(
    errorKind,
    "画布结果目标格式无效。",
    errorKind === "invalid-response" ? 200 : null,
  );
  if (!isRecord(value) || !isValidIdentifier(value.chapter_id) || value.chapter_id !== chapterId) {
    return invalid();
  }

  if (value.kind === "frame_preview") {
    if (hasExactKeys(value, ["kind", "chapter_id", "storyboard_asset_id"]) && isValidIdentifier(value.storyboard_asset_id)) {
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
    return invalid();
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

  return invalid();
}

function parsePageOptions(value: CanvasOwnedResultsPageOptions | undefined): NormalizedPageOptions {
  if (value === undefined) return { limit: DEFAULT_PAGE_LIMIT, cursor: null };
  if (!isRecord(value) || !hasOnlyKeys(value, ["limit", "cursor"])) {
    fail("not-sent", "分页参数无效，未发送画布结果查询。");
  }

  const limit = hasOwn(value, "limit") ? value.limit : DEFAULT_PAGE_LIMIT;
  const cursor = hasOwn(value, "cursor") ? value.cursor : undefined;
  if (typeof limit !== "number" || !Number.isSafeInteger(limit) || limit < 1 || limit > MAX_PAGE_LIMIT) {
    fail("not-sent", "分页数量必须是1到50之间的整数，未发送画布结果查询。");
  }
  if (cursor !== undefined && !isValidOpaqueCursor(cursor)) {
    fail("not-sent", "分页游标格式无效，未发送画布结果查询。");
  }
  return { limit, cursor: cursor ?? null };
}

function setSelectorQuery(url: URL, target: CanvasOwnedResultsTarget): void {
  url.searchParams.set("target_kind", target.kind);
  if (target.kind === "frame_preview" && "storyboard_asset_id" in target) {
    url.searchParams.set("storyboard_asset_id", target.storyboard_asset_id);
  } else if (target.kind === "frame_preview") {
    url.searchParams.set("identity_mode", target.identity_mode);
    url.searchParams.set("frame_index", String(target.frame_index));
    url.searchParams.set("expected_chapter_digest", target.expected_chapter_digest);
  } else {
    url.searchParams.set("asset_type", target.asset_type);
    url.searchParams.set("asset_id", target.asset_id);
  }
}

function isValidTimestamp(value: unknown, requireUtc: boolean): value is string {
  if (typeof value !== "string") return false;
  const pattern = requireUtc ? UTC_TIMESTAMP : TIMESTAMP_WITH_TIMEZONE;
  const match = pattern.exec(value);
  if (match === null) return false;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) return false;
  if (!requireUtc) {
    const timezone = match[8];
    if (timezone === undefined) return false;
    if (timezone !== "Z") {
      const offsetHour = Number(timezone.slice(1, 3));
      const offsetMinute = Number(timezone.slice(4, 6));
      if (offsetHour > 23 || offsetMinute > 59) return false;
    }
  }

  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const monthDays = daysInMonth[month - 1];
  return monthDays !== undefined
    && day >= 1
    && day <= monthDays
    && Number.isFinite(Date.parse(value));
}

function parseTargetSnapshot(
  value: unknown,
  selector: CanvasOwnedResultsTarget,
  seriesId: string,
): CanvasOwnedResultTargetSnapshot {
  if (!isRecord(value)
    || !hasExactKeys(value, TARGET_SNAPSHOT_KEYS)) {
    fail("invalid-response", "画布结果的原始目标快照格式无效。", 200);
  }

  const normalized: Record<string, unknown> = {};
  for (const key of TARGET_SNAPSHOT_KEYS) normalized[key] = value[key];

  for (const field of TARGET_STRING_FIELDS) {
    const fieldValue = normalized[field];
    if (fieldValue !== null && typeof fieldValue !== "string") {
      fail("invalid-response", "画布结果的原始目标快照包含无效字段。", 200);
    }
    if (typeof fieldValue === "string" && CONTROL_CHARACTERS.test(fieldValue)) {
      fail("invalid-response", "画布结果的原始目标快照包含无效字符。", 200);
    }
  }
  for (const field of TARGET_ID_FIELDS) {
    const fieldValue = normalized[field];
    if (fieldValue !== null && !isValidIdentifier(fieldValue)) {
      fail("invalid-response", "画布结果的原始身份字段无效。", 200);
    }
  }
  for (const field of TARGET_HASH_FIELDS) {
    const fieldValue = normalized[field];
    if (fieldValue !== null && !isLowerSha256(fieldValue)) {
      fail("invalid-response", "画布结果的原始摘要字段无效。", 200);
    }
  }
  for (const field of TARGET_INTEGER_FIELDS) {
    const fieldValue = normalized[field];
    if (fieldValue !== null && (typeof fieldValue !== "number" || !Number.isSafeInteger(fieldValue) || fieldValue < 0)) {
      fail("invalid-response", "画布结果的原始数字字段无效。", 200);
    }
  }

  if (!isValidIdentifier(normalized.chapter_id)
    || normalized.chapter_id !== selector.chapter_id
    || !isValidIdentifier(normalized.series_id)
    || normalized.series_id !== seriesId) {
    fail("invalid-response", "画布结果的原始章节或剧集身份与当前查询不匹配。", 200);
  }

  if (selector.kind === "frame_preview") {
    if (normalized.kind !== "canvas_frame_preview"
      || !isLowerSha256(normalized.full_frame_digest)) {
      fail("invalid-response", "画布分镜结果的原始目标快照无效。", 200);
    }
    if ("storyboard_asset_id" in selector) {
      if (normalized.stable_storyboard_asset_id !== selector.storyboard_asset_id
        || (normalized.asset_type !== null && normalized.asset_type !== "storyboard")) {
        fail("invalid-response", "画布结果未绑定到请求的稳定分镜身份。", 200);
      }
    } else if (normalized.stable_storyboard_asset_id !== null
      || normalized.asset_type !== null
      || normalized.asset_id !== null
      || normalized.compat_frame_index !== selector.frame_index
      || normalized.full_chapter_digest !== selector.expected_chapter_digest) {
      fail("invalid-response", "无稳定ID画布结果未绑定到请求的章节摘要与帧索引。", 200);
    }
  } else if (normalized.kind !== "canvas_asset_image"
    || normalized.asset_type !== selector.asset_type
    || normalized.asset_id !== selector.asset_id
    || normalized.stable_storyboard_asset_id !== null
    || normalized.compat_frame_index !== null
    || !isLowerSha256(normalized.same_series_target_digest)) {
    fail("invalid-response", "画布素材结果未绑定到请求的原始素材身份。", 200);
  }

  return Object.freeze(normalized) as unknown as CanvasOwnedResultTargetSnapshot;
}

function parseApplicationProof(value: unknown): CanvasOwnedResultApplicationProof {
  if (!isRecord(value)
    || !hasExactKeys(value, ["application_ref", "target_revision", "committed_at"])
    || !isValidIdentifier(value.application_ref)
    || !isLowerSha256(value.target_revision)
    || !isValidTimestamp(value.committed_at, false)) {
    fail("invalid-response", "画布结果包含无效或非最小的应用证明。", 200);
  }
  return Object.freeze({
    application_ref: value.application_ref,
    target_revision: value.target_revision,
    committed_at: value.committed_at,
  });
}

function parseItem(
  value: unknown,
  selector: CanvasOwnedResultsTarget,
  seriesId: string,
): CanvasOwnedResultItem {
  const keys = [
    "task_id",
    "result_ref",
    "operation",
    "completed_at",
    "expected_target",
    "application_key",
    "eligibility",
    "application",
  ] as const;
  if (!isRecord(value) || !hasExactKeys(value, keys)) {
    fail("invalid-response", "画布结果项包含缺失或多余字段。", 200);
  }
  if (!isValidIdentifier(value.task_id)
    || !isValidIdentifier(value.result_ref)
    || (value.operation !== "image.single" && value.operation !== "image.batch")
    || !isValidTimestamp(value.completed_at, true)) {
    fail("invalid-response", "画布结果项身份、操作或完成时间无效。", 200);
  }

  const eligibility = value.eligibility as CanvasOwnedResultEligibility;
  if (eligibility !== "ready"
    && eligibility !== "already_applied"
    && eligibility !== "stale"
    && eligibility !== "target_deleted"
    && eligibility !== "unready") {
    fail("invalid-response", "画布结果项资格状态无效。", 200);
  }

  if (eligibility === "unready") {
    if (value.expected_target !== null || value.application_key !== null || value.application !== null) {
      fail("invalid-response", "未就绪画布结果不得包含目标或应用证明。", 200);
    }
    return Object.freeze({
      task_id: value.task_id,
      result_ref: value.result_ref,
      operation: value.operation,
      completed_at: value.completed_at,
      expected_target: null,
      application_key: null,
      eligibility,
      application: null,
    });
  }

  if (!isLowerSha256(value.application_key)) {
    fail("invalid-response", "画布结果项的应用键无效。", 200);
  }
  const expectedTarget = parseTargetSnapshot(value.expected_target, selector, seriesId);
  let application: CanvasOwnedResultApplicationProof | null = null;
  if (eligibility === "already_applied") {
    application = parseApplicationProof(value.application);
  } else if (value.application !== null) {
    fail("invalid-response", "未应用画布结果不得包含历史应用证明。", 200);
  }

  return Object.freeze({
    task_id: value.task_id,
    result_ref: value.result_ref,
    operation: value.operation,
    completed_at: value.completed_at,
    expected_target: expectedTarget,
    application_key: value.application_key,
    eligibility,
    application,
  });
}

function targetsEqual(left: CanvasOwnedResultsTarget, right: CanvasOwnedResultsTarget): boolean {
  if (left.kind !== right.kind || left.chapter_id !== right.chapter_id) return false;
  if (left.kind === "asset_image" && right.kind === "asset_image") {
    return left.asset_type === right.asset_type && left.asset_id === right.asset_id;
  }
  if (left.kind !== "frame_preview" || right.kind !== "frame_preview") return false;
  if ("storyboard_asset_id" in left && "storyboard_asset_id" in right) {
    return left.storyboard_asset_id === right.storyboard_asset_id;
  }
  if ("identity_mode" in left && "identity_mode" in right) {
    return left.identity_mode === right.identity_mode
      && left.frame_index === right.frame_index
      && left.expected_chapter_digest === right.expected_chapter_digest;
  }
  return false;
}

function parsePage(
  value: unknown,
  selector: CanvasOwnedResultsTarget,
  seriesId: string,
  limit: number,
  requestedCursor: string | null,
): CanvasOwnedResultsPage {
  if (!isRecord(value)
    || !hasExactKeys(value, ["chapter_id", "target", "items", "next_cursor"])
    || value.chapter_id !== selector.chapter_id) {
    fail("invalid-response", "画布结果分页响应格式无效。", 200);
  }

  const targetEcho = parseTargetSelector(value.target, selector.chapter_id, "invalid-response");
  if (!targetsEqual(targetEcho, selector)) {
    fail("invalid-response", "画布结果分页目标与请求不一致。", 200);
  }
  if (!Array.isArray(value.items) || value.items.length > limit) {
    fail("invalid-response", "画布结果分页数量超过请求上限。", 200);
  }
  let nextCursor: string | null;
  if (value.next_cursor === null) {
    nextCursor = null;
  } else if (isValidOpaqueCursor(value.next_cursor) && value.next_cursor !== requestedCursor) {
    nextCursor = value.next_cursor;
  } else {
    fail("invalid-response", "画布结果分页游标无效。", 200);
  }

  const refs = new Set<string>();
  const items = value.items.map((item) => {
    const parsed = parseItem(item, selector, seriesId);
    if (refs.has(parsed.result_ref)) {
      fail("invalid-response", "画布结果分页包含重复结果引用。", 200);
    }
    refs.add(parsed.result_ref);
    return parsed;
  });

  return Object.freeze({
    chapter_id: selector.chapter_id,
    target: selector,
    items: Object.freeze(items),
    next_cursor: nextCursor,
  });
}

function statusError(status: number): CanvasOwnedResultsError {
  if (status === 401) return new CanvasOwnedResultsError("unauthorized", "登录身份无法通过画布结果查询校验。", status);
  if (status === 403) return new CanvasOwnedResultsError("forbidden", "当前身份无权读取该画布结果。", status);
  if (status === 404) return new CanvasOwnedResultsError("not-found", "找不到可读取的画布结果。", status);
  if (status === 422) return new CanvasOwnedResultsError("invalid-query", "画布结果查询条件未被服务接受。", status);
  if (status >= 500) return new CanvasOwnedResultsError("unavailable", "画布结果查询服务暂时不可用。", status);
  if (status >= 200 && status < 300) {
    return new CanvasOwnedResultsError("invalid-response", "画布结果查询服务返回了无法识别的成功状态。", status);
  }
  return new CanvasOwnedResultsError("read-error", "画布结果查询未能完成。", status);
}

function createRequestControl(
  contextIsCurrent: () => boolean,
  storage: StorageLike,
  token: string,
  actorId: string,
  callerSignal: AbortSignal,
  timeoutMs: number,
): RequestControl {
  if (callerSignal.aborted) fail("not-sent", "画布结果查询已取消，未发送请求。");

  const controller = new AbortController();
  let timedOut = false;
  let rejectAbort: (reason?: unknown) => void = () => undefined;
  const abortPromise = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  const onControllerAbort = () => {
    try {
      check(true);
    } catch (error) {
      rejectAbort(error);
    }
  };
  const abortFromCaller = () => controller.abort(callerSignal.reason);
  const check = (dispatched: boolean) => {
    if (timedOut) {
      fail(dispatched ? "read-error" : "not-sent", "画布结果查询超过15秒期限。");
    }
    if (callerSignal.aborted || controller.signal.aborted) {
      fail(dispatched ? "read-error" : "not-sent", dispatched
        ? "画布结果读取已取消，请重新核对。"
        : "画布结果查询已取消，未发送请求。");
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
      fail(dispatched ? "read-error" : "not-sent", dispatched
        ? "登录或画布上下文已变化，结果不能用于当前页面。"
        : "登录或画布上下文已变化，未发送查询。");
    }
  };
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
        return await Promise.race([promise, abortPromise]);
      } catch (error) {
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
    // The transport may have already closed the body.
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
    fail("invalid-response", "画布结果查询没有返回响应正文。", 200);
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let bodyLength = 0;
  let completed = false;
  const cancelReader = () => {
    try {
      void reader.cancel().catch(() => undefined);
    } catch {
      // Abort can settle the underlying body before this reader is cancelled.
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
        fail("invalid-response", "画布结果分页响应超过512KiB。", 200);
      }
      chunks.push(chunk.value);
      bodyLength += chunk.value.byteLength;
    }

    control.check(true);
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
      fail("invalid-response", "画布结果分页响应不是有效UTF-8。", 200);
    }
    try {
      return JSON.parse(decoded) as unknown;
    } catch {
      fail("invalid-response", "画布结果分页响应不是有效JSON。", 200);
    }
  } finally {
    control.controller.signal.removeEventListener("abort", cancelReader);
    if (!completed) cancelReader();
    try {
      reader.releaseLock();
    } catch {
      // A cancelled underlying read releases its lock when it settles.
    }
  }
}

export function createCanvasOwnedResultsHttpPort(
  apiBaseUrl: string,
  options: CanvasOwnedResultsHttpOptions = {},
): CanvasOwnedResultsPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    fail("not-sent", "画布结果查询超时配置无效，未创建查询服务。");
  }

  let resolvedApiBaseUrl: string;
  try {
    resolvedApiBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) {
      fail("not-sent", "画布结果查询服务地址无效，未创建查询服务。");
    }
    throw error;
  }

  return {
    beginRun(context: CanvasOwnedResultsRunContext): CanvasOwnedResultsRun {
      if (!isValidIdentifier(context.actorId)
        || !isValidIdentifier(context.seriesId)
        || !isValidIdentifier(context.chapterId)
        || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "登录或章节身份无法核对，未创建画布结果查询。");
      }

      let token: string | null = null;
      let actor: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
        actor = storedActorId(storage);
      } catch {
        // Unavailable session storage is treated as an unverified identity.
      }
      if (token === null || token === "" || token.trim() !== token || actor !== context.actorId) {
        fail("not-sent", "当前登录用户无法核对，未创建画布结果查询。");
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
      if (!isCurrent()) fail("not-sent", "登录或画布上下文已经变化，未创建查询。");

      return {
        isCurrent,
        async page(target, signal, pageOptions) {
          const selector = parseTargetSelector(target, capturedChapterId, "not-sent");
          const normalizedOptions = parsePageOptions(pageOptions);
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
            const endpoint = new URL(
              `${resolvedApiBaseUrl}/chapters/${encodeURIComponent(capturedChapterId)}/canvas-owned-results`,
            );
            setSelectorQuery(endpoint, selector);
            endpoint.searchParams.set("limit", String(normalizedOptions.limit));
            if (normalizedOptions.cursor !== null) endpoint.searchParams.set("cursor", normalizedOptions.cursor);

            control.check(false);
            const request = Promise.resolve().then(() => fetcher(endpoint, {
              method: "GET",
              headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
              credentials: "omit",
              cache: "no-store",
              redirect: "error",
              referrerPolicy: "no-referrer",
              signal: control.controller.signal,
            }));
            const responseWithCleanup = request.then((received) => {
              try {
                control.check(true);
              } catch {
                cancelLateResponse(received);
              }
              return received;
            });
            dispatched = true;
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
            const page = parsePage(
              payload,
              selector,
              capturedSeriesId,
              normalizedOptions.limit,
              normalizedOptions.cursor,
            );
            control.check(true);
            return page;
          } catch (error) {
            if (response !== null) {
              try {
                await cancelResponseBody(response, control);
              } catch (cancelError) {
                if (cancelError instanceof CanvasOwnedResultsError) throw cancelError;
              }
            }
            if (error instanceof CanvasOwnedResultsError) throw error;
            control.check(dispatched);
            fail("read-error", "画布结果查询未能完成，请重新进行只读核对。");
          } finally {
            control.cleanup();
          }
        },
      };
    },
  };
}
