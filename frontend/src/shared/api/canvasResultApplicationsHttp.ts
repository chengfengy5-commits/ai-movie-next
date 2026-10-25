import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  CanvasResultApplicationError,
  type CanvasResultApplicationLookup,
  type CanvasResultApplicationRun,
  type CanvasResultApplicationRunContext,
  type CanvasResultApplicationSelector,
  type CanvasResultApplicationsPort,
} from "./canvasResultApplications";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const DEFAULT_LOOKUP_TIMEOUT_MS = 15_000;
const MAX_RESPONSE_BYTES = 64 * 1024;
const APPLICATION_REVISION_PATTERN = /^[0-9a-f]{64}$/u;
const TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$/iu;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;

export interface CanvasResultApplicationsHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

interface RequestControl {
  readonly controller: AbortController;
  didTimeout(): boolean;
  check(dispatched: boolean): void;
  cleanup(): void;
}

function fail(
  kind: CanvasResultApplicationError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new CanvasResultApplicationError(kind, message, status);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === expected.length && actual.every((key) => expected.includes(key));
}

function isValidIdentifier(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const characterCount = Array.from(value).length;
  return characterCount > 0
    && characterCount <= 36
    && value.trim() === value
    && !CONTROL_CHARACTERS.test(value)
    && !value.includes("/")
    && !value.includes("\\")
    && value !== "."
    && value !== "..";
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

function isIso8601WithTimezone(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = TIMESTAMP_PATTERN.exec(value);
  if (match === null) return false;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const timezone = match[8];
  if (month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) return false;
  if (timezone === undefined) return false;
  if (timezone.toUpperCase() !== "Z") {
    const offsetHour = Number(timezone.slice(1, 3));
    const offsetMinute = Number(timezone.slice(4, 6));
    if (offsetHour > 23 || offsetMinute > 59) return false;
  }

  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const monthDays = daysInMonth[month - 1];
  if (monthDays === undefined || day < 1 || day > monthDays) return false;

  const normalized = value.replace("t", "T").replace("z", "Z");
  return Number.isFinite(Date.parse(normalized));
}

function parseLookupResponse(
  value: unknown,
  requestedTaskId: string,
  requestedResultRef: string,
): CanvasResultApplicationLookup {
  if (!isRecord(value)
    || !hasExactKeys(value, ["task_id", "result_ref", "status", "application"])
    || value.task_id !== requestedTaskId
    || value.result_ref !== requestedResultRef) {
    fail("invalid-response", "应用状态查询返回了无法核对的响应。", 200);
  }

  if (value.status === "not_applied" && value.application === null) {
    return Object.freeze({
      task_id: requestedTaskId,
      result_ref: requestedResultRef,
      status: "not_applied",
      application: null,
    });
  }

  if (value.status !== "applied" || !isRecord(value.application)
    || !hasExactKeys(value.application, ["application_ref", "target_revision", "committed_at"])
    || !isValidIdentifier(value.application.application_ref)
    || typeof value.application.target_revision !== "string"
    || !APPLICATION_REVISION_PATTERN.test(value.application.target_revision)
    || !isIso8601WithTimezone(value.application.committed_at)) {
    fail("invalid-response", "应用状态查询返回了无效或非最小的应用回执。", 200);
  }

  return Object.freeze({
    task_id: requestedTaskId,
    result_ref: requestedResultRef,
    status: "applied",
    application: Object.freeze({
      application_ref: value.application.application_ref,
      target_revision: value.application.target_revision,
      committed_at: value.application.committed_at,
    }),
  });
}

function statusError(status: number): CanvasResultApplicationError {
  if (status === 401) return new CanvasResultApplicationError("unauthorized", "登录身份无法通过应用状态查询校验。", status);
  if (status === 403) return new CanvasResultApplicationError("forbidden", "当前身份无权读取该应用状态。", status);
  if (status === 404) return new CanvasResultApplicationError("not-found", "找不到可读取的应用状态。", status);
  if (status === 409) return new CanvasResultApplicationError("unready", "应用状态暂时无法读取，请稍后重新核对。", status);
  if (status >= 500) return new CanvasResultApplicationError("unavailable", "应用状态服务暂时不可用。", status);
  if (status >= 200 && status < 300) {
    return new CanvasResultApplicationError("invalid-response", "应用状态服务返回了无法识别的成功状态。", status);
  }
  return new CanvasResultApplicationError("read-error", "应用状态查询未能完成。", status);
}

async function cancelResponseBody(response: Response | null): Promise<void> {
  if (response === null || response.body === null || response.bodyUsed) return;
  try {
    await response.body.cancel();
  } catch {
    // Fetch implementations can close the stream as part of abort handling.
  }
}

function newRequestControl(
  contextIsCurrent: () => boolean,
  storage: StorageLike,
  token: string,
  actorId: string,
  callerSignal: AbortSignal,
  timeoutMs: number,
): RequestControl {
  if (callerSignal.aborted) fail("not-sent", "查询已取消，未发送请求。");

  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(callerSignal.reason);
  callerSignal.addEventListener("abort", abortFromCaller, { once: true });
  if (callerSignal.aborted) abortFromCaller();
  const timeout = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  return {
    controller,
    didTimeout: () => timedOut,
    check(dispatched) {
      if (callerSignal.aborted || controller.signal.aborted || timedOut) {
        fail(dispatched ? "read-error" : "not-sent", timedOut
          ? "应用状态查询超时，请重新执行只读核对。"
          : dispatched ? "应用状态读取已取消，请重新核对。" : "查询已取消，未发送请求。");
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
          ? "登录或画布上下文已变化，应用状态读取结果不能用于当前页面。"
          : "登录或画布上下文已变化，未发送查询。");
      }
    },
    cleanup() {
      globalThis.clearTimeout(timeout);
      callerSignal.removeEventListener("abort", abortFromCaller);
    },
  };
}

async function readJsonBody(response: Response, control: RequestControl): Promise<unknown> {
  if (response.body === null) {
    fail("invalid-response", "应用状态查询没有返回响应正文。", 200);
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let bodyLength = 0;
  let completed = false;
  const cancelReader = () => {
    try {
      void reader.cancel().catch(() => {
        // The body may already have been cancelled by the fetch implementation.
      });
    } catch {
      // The reader may already have been released by abort handling.
    }
  };
  control.controller.signal.addEventListener("abort", cancelReader, { once: true });

  try {
    try {
      while (true) {
        const result = await reader.read();
        if (result.done) {
          completed = true;
          break;
        }
        if (bodyLength + result.value.byteLength > MAX_RESPONSE_BYTES) {
          cancelReader();
          fail("invalid-response", "应用状态查询响应超过 64 KiB。", 200);
        }
        chunks.push(result.value);
        bodyLength += result.value.byteLength;
      }
    } catch (error) {
      if (error instanceof CanvasResultApplicationError) throw error;
      control.check(true);
      fail("read-error", "读取应用状态响应正文时连接中断。", 200);
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
      fail("invalid-response", "应用状态查询返回了无效 JSON。", 200);
    }
  } finally {
    control.controller.signal.removeEventListener("abort", cancelReader);
    if (!completed) cancelReader();
    try {
      reader.releaseLock();
    } catch {
      // A pending read releases its lock when the cancelled stream settles.
    }
  }
}

export function createCanvasResultApplicationsHttpPort(
  apiBaseUrl: string,
  options: CanvasResultApplicationsHttpOptions = {},
): CanvasResultApplicationsPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_LOOKUP_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    fail("not-sent", "应用状态查询超时配置无效。未发送请求。");
  }

  let resolvedApiBaseUrl: string;
  try {
    resolvedApiBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) {
      fail("not-sent", "应用状态查询服务地址无效。未发送请求。");
    }
    throw error;
  }

  return {
    beginRun(context: CanvasResultApplicationRunContext): CanvasResultApplicationRun {
      if (!isValidIdentifier(context.actorId)
        || !isValidIdentifier(context.seriesId)
        || !isValidIdentifier(context.chapterId)
        || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "登录或章节身份无法核对，未创建应用状态查询。");
      }

      let token: string | null = null;
      let actor: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
        actor = storedActorId(storage);
      } catch {
        // Treat inaccessible session storage as an unverified local session.
      }
      if (token === null || token === "" || token.trim() !== token || actor !== context.actorId) {
        fail("not-sent", "当前登录用户无法核对，未创建应用状态查询。");
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

      return {
        isCurrent,
        async lookup(selector: CanvasResultApplicationSelector, signal: AbortSignal) {
          const requestedTaskId = selector.taskId;
          const requestedResultRef = selector.resultRef;
          if (!isValidIdentifier(requestedTaskId) || !isValidIdentifier(requestedResultRef)) {
            fail("not-sent", "任务或结果引用无效，未发送应用状态查询。");
          }

          const control = newRequestControl(isCurrent, storage, token, capturedActorId, signal, timeoutMs);
          let response: Response | null = null;
          let dispatched = false;
          try {
            control.check(false);
            const endpoint = new URL(
              `${resolvedApiBaseUrl}/chapters/${encodeURIComponent(capturedChapterId)}`
                + `/canvas-owned-results/${encodeURIComponent(requestedResultRef)}/application`,
            );
            endpoint.searchParams.set("task_id", requestedTaskId);

            dispatched = true;
            response = await fetcher(endpoint, {
              method: "GET",
              headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
              credentials: "omit",
              cache: "no-store",
              redirect: "error",
              referrerPolicy: "no-referrer",
              signal: control.controller.signal,
            });
            control.check(true);

            if (response.status !== 200) {
              const error = statusError(response.status);
              await cancelResponseBody(response);
              control.check(true);
              throw error;
            }

            const payload = await readJsonBody(response, control);
            control.check(true);
            return parseLookupResponse(payload, requestedTaskId, requestedResultRef);
          } catch (error) {
            await cancelResponseBody(response);
            if (error instanceof CanvasResultApplicationError) throw error;
            control.check(dispatched);
            fail("read-error", "应用状态查询暂时无法完成，请稍后重新进行只读核对。");
          } finally {
            control.cleanup();
          }
        },
      };
    },
  };
}
