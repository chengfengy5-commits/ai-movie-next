import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  CanvasOwnedImageError,
  type CanvasOwnedImageRun,
  type CanvasOwnedImageRunContext,
  type CanvasOwnedImageSelector,
  type CanvasOwnedImagePort,
} from "./canvasOwnedImage";
import { API_TOKEN_KEY, API_USER_KEY, browserStorage, type StorageLike } from "./storage";

const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const DEFAULT_READ_DEADLINE_MS = 35_000;
const ALLOWED_CONTENT_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;

export interface CanvasOwnedImageHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly pageUrl?: string;
}

interface RequestControl {
  readonly controller: AbortController;
  check(dispatched: boolean): void;
  wait<T>(promise: Promise<T>, dispatched: boolean): Promise<T>;
  cleanup(): void;
}

function fail(
  kind: CanvasOwnedImageError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new CanvasOwnedImageError(kind, message, status);
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

function isWellFormedUnicode(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const codeUnit = value.charCodeAt(index);
    if (codeUnit >= 0xd800 && codeUnit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!Number.isInteger(next) || next < 0xdc00 || next > 0xdfff) return false;
      index += 1;
    } else if (codeUnit >= 0xdc00 && codeUnit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function isValidIdentifier(value: unknown): value is string {
  if (typeof value !== "string" || !isWellFormedUnicode(value)) return false;
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

function isValidBearerToken(value: unknown): value is string {
  if (typeof value !== "string"
    || value === ""
    || value.trim() !== value
    || CONTROL_CHARACTERS.test(value)) {
    return false;
  }
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
    if (!isRecord(user) || !isValidIdentifier(user.id)) return null;
    return user.id;
  } catch {
    return null;
  }
}

function parseSelector(value: unknown): CanvasOwnedImageSelector {
  if (!isRecord(value)
    || !hasExactKeys(value, ["taskId", "resultRef"])
    || !isValidIdentifier(value.taskId)
    || !isValidIdentifier(value.resultRef)) {
    fail("not-sent", "画布图片记录身份无效，未发送读取请求。");
  }
  return Object.freeze({ taskId: value.taskId, resultRef: value.resultRef });
}

function statusError(status: number): CanvasOwnedImageError {
  if (status === 401) return new CanvasOwnedImageError("unauthorized", "登录身份未通过画布图片读取校验。", status);
  if (status === 403) return new CanvasOwnedImageError("forbidden", "当前身份无权读取该画布图片。", status);
  if (status === 404) return new CanvasOwnedImageError("not-found", "找不到可读取的画布图片。", status);
  if (status === 409 || status === 425) {
    return new CanvasOwnedImageError("unready", "该画布结果当前尚无可读取的图片。", status);
  }
  if (status >= 500) return new CanvasOwnedImageError("unavailable", "画布图片读取服务暂时不可用。", status);
  if (status >= 200 && status < 300) {
    return new CanvasOwnedImageError("invalid-response", "画布图片读取服务返回了无法识别的成功状态。", status);
  }
  return new CanvasOwnedImageError("read-error", "画布图片读取未能完成。", status);
}

function createRequestControl(
  contextIsCurrent: () => boolean,
  storage: StorageLike,
  token: string,
  actorId: string,
  callerSignal: AbortSignal,
  deadlineMs: number,
): RequestControl {
  if (callerSignal.aborted) fail("not-sent", "画布图片读取已取消，未发送请求。");

  const controller = new AbortController();
  let timedOut = false;
  let rejectAbort: (reason?: unknown) => void = () => undefined;
  const abortPromise = new Promise<never>((_resolve, reject) => { rejectAbort = reject; });
  // A synchronous fetcher can abort before wait() attaches the race handlers.
  void abortPromise.catch(() => undefined);
  const check = (dispatched: boolean) => {
    const kind = dispatched ? "read-error" : "not-sent";
    if (timedOut) fail(kind, "画布图片读取超过35秒期限。");
    if (callerSignal.aborted || controller.signal.aborted) {
      fail(kind, dispatched
        ? "画布图片读取已取消，请重新核对当前画布。"
        : "画布图片读取已取消，未发送请求。");
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
      fail(kind, dispatched
        ? "登录或画布上下文已变化，图片不能用于当前页面。"
        : "登录或画布上下文已变化，未发送读取请求。");
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
  }, deadlineMs);

  return {
    controller,
    check,
    async wait<T>(promise: Promise<T>, dispatched: boolean): Promise<T> {
      // A pre-aborted request may fail this check before the promise enters the race.
      void promise.catch(() => undefined);
      check(dispatched);
      try {
        const value = await Promise.race([promise, abortPromise]);
        check(dispatched);
        return value;
      } catch (error) {
        if (error instanceof CanvasOwnedImageError) throw error;
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

async function cancelResponseBody(response: Response, control: RequestControl): Promise<void> {
  if (response.body === null || response.bodyUsed) {
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

function declaredContentLength(response: Response, control: RequestControl): number | null {
  const value = response.headers.get("content-length");
  if (value === null) return null;
  if (!/^\d+$/u.test(value)) {
    fail("invalid-response", "画布图片响应长度标记无效。", response.status);
  }
  const length = Number(value);
  if (!Number.isSafeInteger(length) || length > MAX_IMAGE_BYTES) {
    fail("invalid-response", "画布图片响应超过10MiB上限。", response.status);
  }
  control.check(true);
  return length;
}

async function readImageBody(response: Response, control: RequestControl): Promise<Blob> {
  const contentType = response.headers.get("content-type");
  if (contentType === null || !ALLOWED_CONTENT_TYPES.has(contentType)) {
    await cancelResponseBody(response, control);
    fail("invalid-response", "画布图片格式不受支持。", response.status);
  }

  let expectedLength: number | null;
  try {
    expectedLength = declaredContentLength(response, control);
  } catch (error) {
    await cancelResponseBody(response, control);
    throw error;
  }

  if (response.body === null) {
    fail("invalid-response", "画布图片响应没有正文。", response.status);
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  let completed = false;
  const cancelReader = () => {
    try {
      void reader.cancel().catch(() => undefined);
    } catch {
      // The stream may already have completed or been cancelled.
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
      if (totalBytes + chunk.value.byteLength > MAX_IMAGE_BYTES) {
        cancelReader();
        fail("invalid-response", "画布图片响应超过10MiB上限。", response.status);
      }
      chunks.push(chunk.value);
      totalBytes += chunk.value.byteLength;
    }

    if (totalBytes === 0 || (expectedLength !== null && totalBytes !== expectedLength)) {
      fail("invalid-response", "画布图片正文为空或与响应长度不一致。", response.status);
    }
    control.check(true);
    const safeParts: BlobPart[] = chunks.map((chunk) => {
      const copy = new Uint8Array(chunk.byteLength);
      copy.set(chunk);
      return copy;
    });
    const blob = new Blob(safeParts, { type: contentType });
    if (blob.size === 0 || blob.size > MAX_IMAGE_BYTES) {
      fail("invalid-response", "画布图片响应超过允许的大小。", response.status);
    }
    control.check(true);
    return blob;
  } catch (error) {
    if (error instanceof CanvasOwnedImageError) throw error;
    control.check(true);
    return fail("read-error", "读取画布图片正文时连接中断。", response.status);
  } finally {
    control.controller.signal.removeEventListener("abort", cancelReader);
    if (!completed) cancelReader();
    try {
      reader.releaseLock();
    } catch {
      // A pending read releases its lock after stream cancellation settles.
    }
  }
}

export function createCanvasOwnedImageHttpPort(
  apiBaseUrl: string,
  options: CanvasOwnedImageHttpOptions = {},
): CanvasOwnedImagePort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);

  let resolvedApiBaseUrl: string;
  try {
    resolvedApiBaseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) {
      fail("not-sent", "画布图片读取服务地址无效，未创建读取服务。");
    }
    throw error;
  }

  return {
    beginRun(context: CanvasOwnedImageRunContext): CanvasOwnedImageRun {
      if (!isValidIdentifier(context.actorId)
        || !isValidIdentifier(context.seriesId)
        || !isValidIdentifier(context.chapterId)
        || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "登录或画布身份无法核对，未创建图片读取服务。");
      }

      let token: string | null = null;
      let actorId: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
        actorId = storedActorId(storage);
      } catch {
        // An unreadable session cannot establish the identity for a private image read.
      }
      if (!isValidBearerToken(token) || actorId !== context.actorId) {
        fail("not-sent", "当前登录身份无法核对，未创建图片读取服务。");
      }

      const capturedActorId = context.actorId;
      const capturedSeriesId = context.seriesId;
      const capturedChapterId = context.chapterId;
      const contextCheck = context.isContextCurrent;
      const contextIsCurrent = contextCheck.bind(context);
      const isCurrent = () => {
        try {
          return context.actorId === capturedActorId
            && context.seriesId === capturedSeriesId
            && context.chapterId === capturedChapterId
            && context.isContextCurrent === contextCheck
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && storedActorId(storage) === capturedActorId;
        } catch {
          return false;
        }
      };
      if (!isCurrent()) fail("not-sent", "登录或画布上下文已变化，未创建图片读取服务。");

      return {
        isCurrent,
        async read(selectorInput: CanvasOwnedImageSelector, signal: AbortSignal): Promise<Blob> {
          const selector = parseSelector(selectorInput);
          const endpoint = new URL(
            `${resolvedApiBaseUrl}/chapters/${encodeURIComponent(capturedChapterId)}`
            + `/canvas-owned-results/${encodeURIComponent(selector.resultRef)}/image`,
          );
          endpoint.searchParams.set("task_id", selector.taskId);
          const control = createRequestControl(
            isCurrent,
            storage,
            token,
            capturedActorId,
            signal,
            DEFAULT_READ_DEADLINE_MS,
          );
          let response: Response | null = null;
          let dispatched = false;
          try {
            control.check(false);
            dispatched = true;
            const request = Promise.resolve(fetcher(endpoint, {
              method: "GET",
              headers: {
                Accept: "image/png, image/jpeg, image/webp",
                Authorization: `Bearer ${token}`,
              },
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
            response = await control.wait(responseWithCleanup, true);

            if (response.status !== 200) {
              const error = statusError(response.status);
              await cancelResponseBody(response, control);
              control.check(true);
              throw error;
            }

            control.check(true);
            const blob = await readImageBody(response, control);
            control.check(true);
            return blob;
          } catch (error) {
            if (response !== null) {
              try {
                await cancelResponseBody(response, control);
              } catch (cancelError) {
                if (cancelError instanceof CanvasOwnedImageError) throw cancelError;
              }
            }
            if (error instanceof CanvasOwnedImageError) throw error;
            control.check(dispatched);
            fail("read-error", "画布图片读取未能完成，请重新核对。");
          } finally {
            control.cleanup();
          }
        },
      };
    },
  };
}
