import { ApiConfigurationError, resolveApiBaseUrl } from "./config";
import {
  classifyMediaExportUrl,
  MediaExportReaderError,
  readMediaExportSource,
  type MediaExportUrlRoute,
} from "./mediaExportReader";
import {
  AssetGenerationHistoryError,
  type AssetGenerationHistoryMessage,
  type AssetGenerationHistoryPort,
  type AssetGenerationHistoryRunContext,
} from "./assetGenerationHistory";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  browserStorage,
  type StorageLike,
} from "./storage";

const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const RETRY_OSS_TIMEOUT_MS = 610_000;
const IMAGE_PREVIEW_TIMEOUT_MS = 30_000;
const IMAGE_PREVIEW_MAX_BYTES = 50 * 1024 * 1024;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;

const CANONICAL_IMAGE_MIME: Readonly<Record<string, string>> = {
  "image/jpeg": "image/jpeg",
  "image/pjpeg": "image/jpeg",
  "image/png": "image/png",
  "image/gif": "image/gif",
  "image/webp": "image/webp",
  "image/bmp": "image/bmp",
  "image/x-ms-bmp": "image/bmp",
};

export interface AssetGenerationHistoryHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly retryOssTimeoutMs?: number;
  readonly imagePreviewTimeoutMs?: number;
  readonly pageUrl?: string;
}

interface TimedRequest {
  readonly controller: AbortController;
  didTimeout(): boolean;
  dispose(): void;
}

function fail(
  kind: AssetGenerationHistoryError["kind"],
  message: string,
  status: number | null = null,
): never {
  throw new AssetGenerationHistoryError(kind, message, status);
}

function isIdentifier(value: unknown): value is string {
  return typeof value === "string"
    && value.trim() === value
    && value.length > 0
    && value.length <= 128
    && !CONTROL_CHARACTERS.test(value);
}

function actorIdFromStorage(storage: StorageLike): string | null {
  try {
    const serialized = storage.getItem(API_USER_KEY);
    if (serialized === null) return null;
    const user: unknown = JSON.parse(serialized);
    if (typeof user !== "object" || user === null || Array.isArray(user)) return null;
    const id = (user as Record<string, unknown>).id;
    return isIdentifier(id) ? id : null;
  } catch {
    return null;
  }
}

function parseMessage(value: unknown): AssetGenerationHistoryMessage {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail("invalid-response", "生成历史消息格式无法识别。");
  }
  const record = value as Record<string, unknown>;
  const stringFields = ["id", "chapter_id", "chat_mode", "role", "content", "created_at"] as const;
  for (const key of stringFields) {
    if (typeof record[key] !== "string") fail("invalid-response", "生成历史消息字段无法识别。");
  }
  const nullableStringFields = ["asset_type", "asset_id", "model_name"] as const;
  for (const key of nullableStringFields) {
    if (record[key] !== null && typeof record[key] !== "string") {
      fail("invalid-response", "生成历史消息字段无法识别。");
    }
  }
  if (!isIdentifier(record.id) || !isIdentifier(record.chapter_id)) {
    fail("invalid-response", "生成历史消息身份无法核对。");
  }
  return {
    id: record.id,
    chapter_id: record.chapter_id,
    asset_type: record.asset_type as string | null,
    asset_id: record.asset_id as string | null,
    chat_mode: record.chat_mode as string,
    role: record.role as string,
    content: record.content as string,
    model_name: record.model_name as string | null,
    created_at: record.created_at as string,
  };
}

function parseMessages(value: unknown): AssetGenerationHistoryMessage[] {
  if (!Array.isArray(value)) fail("invalid-response", "生成历史必须是消息列表。");
  return value.map(parseMessage);
}

function parseMediaUrl(value: unknown): string {
  if (typeof value !== "string" || value.trim() !== value || value === "" || CONTROL_CHARACTERS.test(value)) {
    fail("invalid-response", "转存服务返回的地址无法识别。");
  }
  try {
    const url = new URL(value);
    if ((url.protocol !== "http:" && url.protocol !== "https:")
      || url.hostname === ""
      || url.username !== ""
      || url.password !== ""
      || url.hash !== "") {
      fail("invalid-response", "转存服务返回的地址无法识别。");
    }
  } catch (error) {
    if (error instanceof AssetGenerationHistoryError) throw error;
    fail("invalid-response", "转存服务返回的地址无法识别。");
  }
  return value;
}

function createTimedRequest(signal: AbortSignal, timeoutMs: number): TimedRequest {
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(signal.reason);
  signal.addEventListener("abort", abortFromCaller, { once: true });
  if (signal.aborted) abortFromCaller();
  const timer = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException("Request timed out", "TimeoutError"));
  }, timeoutMs);
  return {
    controller,
    didTimeout: () => timedOut,
    dispose() {
      globalThis.clearTimeout(timer);
      signal.removeEventListener("abort", abortFromCaller);
    },
  };
}

function raceWithAbort<T>(promise: Promise<T>, timed: TimedRequest): Promise<T> {
  const aborted = new Promise<never>((_resolve, reject) => {
    const rejectAbort = () => reject(timed.controller.signal.reason ?? new DOMException("Request aborted", "AbortError"));
    if (timed.controller.signal.aborted) {
      rejectAbort();
      return;
    }
    timed.controller.signal.addEventListener("abort", rejectAbort, { once: true });
    void promise.then(
      () => timed.controller.signal.removeEventListener("abort", rejectAbort),
      () => timed.controller.signal.removeEventListener("abort", rejectAbort),
    );
  });
  return Promise.race([promise, aborted]);
}

function parseContentLength(response: Response): number | null {
  const value = response.headers.get("Content-Length");
  if (value === null) return null;
  if (!/^\d+$/u.test(value)) fail("read-error", "媒体响应长度无法核对。");
  const length = Number(value);
  if (!Number.isSafeInteger(length)) fail("read-error", "媒体响应长度无法核对。");
  return length;
}

function imageSignatureMatches(mimeType: string, bytes: Uint8Array): boolean {
  switch (mimeType) {
    case "image/jpeg": return bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
    case "image/png": return bytes.length >= 8
      && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47
      && bytes[4] === 0x0d && bytes[5] === 0x0a && bytes[6] === 0x1a && bytes[7] === 0x0a;
    case "image/gif": {
      if (bytes.length < 6) return false;
      const signature = new TextDecoder().decode(bytes.subarray(0, 6));
      return signature === "GIF89a" || signature === "GIF87a";
    }
    case "image/webp": return bytes.length >= 12
      && new TextDecoder().decode(bytes.subarray(0, 4)) === "RIFF"
      && new TextDecoder().decode(bytes.subarray(8, 12)) === "WEBP";
    case "image/bmp": return bytes.length >= 2 && bytes[0] === 0x42 && bytes[1] === 0x4d;
    default: return false;
  }
}

async function readImageResponse(
  response: Response,
  mimeType: string,
  signal: AbortSignal,
  maxBytes: number,
): Promise<Blob> {
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    fail("read-error", "无法读取这张生成图片。", response.status);
  }
  const canonicalMimeType = CANONICAL_IMAGE_MIME[mimeType];
  if (canonicalMimeType === undefined) {
    await response.body?.cancel().catch(() => undefined);
    fail("read-error", "来源内容不是受支持的图片格式。");
  }
  const declaredLength = parseContentLength(response);
  const contentEncoding = response.headers.get("Content-Encoding");
  const compareDeclaredLength = contentEncoding !== null
    ? contentEncoding.trim().toLowerCase() === "" || contentEncoding.trim().toLowerCase() === "identity"
    : response.type !== "cors";
  if (declaredLength !== null && declaredLength > maxBytes) {
    await response.body?.cancel().catch(() => undefined);
    fail("read-error", "生成图片超过预览大小限制。");
  }
  if (response.body === null) fail("read-error", "图片响应没有可读取的内容。");

  const reader = response.body.getReader();
  const cancelReader = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener("abort", cancelReader, { once: true });
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  let completed = false;
  try {
    while (true) {
      if (signal.aborted) fail("read-error", "图片读取已停止。");
      let next: ReadableStreamReadResult<Uint8Array>;
      try {
        next = await reader.read();
      } catch {
        fail("read-error", "图片读取中断。");
      }
      if (next.done) break;
      totalBytes += next.value.byteLength;
      if (totalBytes > maxBytes) fail("read-error", "生成图片超过预览大小限制。");
      const chunk = new Uint8Array(next.value.byteLength);
      chunk.set(next.value);
      chunks.push(chunk);
    }
    if (totalBytes === 0) fail("read-error", "图片响应内容为空。");
    if (compareDeclaredLength && declaredLength !== null && totalBytes !== declaredLength) {
      fail("read-error", "图片响应长度与实际内容不一致。");
    }
    const bytes = new Uint8Array(totalBytes);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    if (!imageSignatureMatches(canonicalMimeType, bytes)) fail("read-error", "图片内容与响应类型不匹配。");
    completed = true;
    return new Blob([bytes], { type: canonicalMimeType });
  } finally {
    signal.removeEventListener("abort", cancelReader);
    if (!completed) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function createAssetGenerationHistoryHttpPort(
  apiBaseUrl: string,
  options: AssetGenerationHistoryHttpOptions = {},
): AssetGenerationHistoryPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  const retryOssTimeoutMs = options.retryOssTimeoutMs ?? RETRY_OSS_TIMEOUT_MS;
  const imagePreviewTimeoutMs = options.imagePreviewTimeoutMs ?? IMAGE_PREVIEW_TIMEOUT_MS;
  for (const timeout of [timeoutMs, retryOssTimeoutMs, imagePreviewTimeoutMs]) {
    if (!Number.isFinite(timeout) || timeout <= 0) fail("not-sent", "生成历史请求超时配置无效。");
  }
  if (retryOssTimeoutMs > RETRY_OSS_TIMEOUT_MS) {
    fail("not-sent", "转存等待上限配置无效。");
  }

  let baseUrl: string;
  try {
    baseUrl = resolveApiBaseUrl(apiBaseUrl, options.pageUrl ?? "http://127.0.0.1/");
  } catch (error) {
    if (error instanceof ApiConfigurationError) fail("not-sent", "生成历史服务地址无效。");
    throw error;
  }
  return {
    beginRun(context: AssetGenerationHistoryRunContext) {
      if (!isIdentifier(context.actorId)
        || !isIdentifier(context.seriesId)
        || !isIdentifier(context.chapterId)
        || !isIdentifier(context.assetId)
        || (context.assetType !== "character" && context.assetType !== "scene" && context.assetType !== "prop")
        || (context.mode !== "image" && context.mode !== "video")
        || typeof context.isContextCurrent !== "function") {
        fail("not-sent", "历史查询身份无法核对。");
      }

      let token: string | null = null;
      try {
        token = storage.getItem(API_TOKEN_KEY);
      } catch {
        // An inaccessible session cannot authorize an asset-history request.
      }
      if (token === null || token.trim() === "" || token.trim() !== token || CONTROL_CHARACTERS.test(token)
        || actorIdFromStorage(storage) !== context.actorId) {
        fail("not-sent", "当前登录身份无法核对。");
      }

      const capturedActorId = context.actorId;
      const capturedSeriesId = context.seriesId;
      const capturedChapterId = context.chapterId;
      const capturedAssetType = context.assetType;
      const capturedAssetId = context.assetId;
      const capturedMode = context.mode;
      const capturedContextCheck = context.isContextCurrent;
      const contextIsCurrent = capturedContextCheck.bind(context);

      function isCurrent(): boolean {
        try {
          return context.actorId === capturedActorId
            && context.seriesId === capturedSeriesId
            && context.chapterId === capturedChapterId
            && context.assetType === capturedAssetType
            && context.assetId === capturedAssetId
            && context.mode === capturedMode
            && context.isContextCurrent === capturedContextCheck
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && actorIdFromStorage(storage) === capturedActorId;
        } catch {
          return false;
        }
      }

      function assertCurrent(stage: "read" | "write", dispatched: boolean, signal: AbortSignal): void {
        if (signal.aborted) {
          fail(dispatched && stage === "write" ? "unknown" : dispatched ? "read-error" : "not-sent",
            dispatched ? "请求已停止，结果需要重新核对。" : "请求已停止，未发送。");
        }
        if (!isCurrent()) {
          fail(dispatched && stage === "write" ? "unknown" : dispatched ? "read-error" : "not-sent",
            dispatched ? "登录或页面范围已变化，结果需要重新核对。" : "登录或页面范围已变化，未发送请求。");
        }
      }

      async function request(
        path: string,
        signal: AbortSignal,
        requestOptions: {
          readonly method: "GET" | "POST" | "PUT" | "DELETE";
          readonly body?: unknown;
          readonly expectedStatus: number;
          readonly timeoutMs?: number;
        },
      ): Promise<unknown> {
        const stage = requestOptions.method === "GET" ? "read" : "write";
        assertCurrent(stage, false, signal);
        const timed = createTimedRequest(signal, requestOptions.timeoutMs ?? timeoutMs);
        const headers: Record<string, string> = {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
        };
        const body = requestOptions.body;
        if (body !== undefined) headers["Content-Type"] = "application/json";
        const url = new URL(`${baseUrl}${path}`);
        let dispatched = false;
        let response: Response | null = null;
        try {
          assertCurrent(stage, false, timed.controller.signal);
          dispatched = true;
          const responsePromise = fetcher(url, {
            method: requestOptions.method,
            headers,
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            signal: timed.controller.signal,
            credentials: "omit",
            cache: "no-store",
            redirect: "error",
            referrerPolicy: "no-referrer",
          });
          void responsePromise.then((lateResponse) => {
            if (timed.controller.signal.aborted) void lateResponse.body?.cancel().catch(() => undefined);
          }, () => undefined);
          try {
            response = await raceWithAbort(responsePromise, timed);
          } catch {
            if (!isCurrent() || signal.aborted || timed.controller.signal.aborted) {
              fail(stage === "write" ? "unknown" : "read-error", "请求已发出，但结果未能确认。");
            }
            fail(stage === "write" ? "unknown" : "read-error", "连接中断，结果未能确认。");
          }
          assertCurrent(stage, dispatched, timed.controller.signal);
          if (response.status === 401) {
            fail("unauthorized", "登录状态已失效，请重新登录。", 401);
          }
          if (response.status !== requestOptions.expectedStatus) {
            await response.body?.cancel().catch(() => undefined);
            const status = response.status;
            const isKnownRejection = status >= 400 && status < 500
              && !(stage === "write" && status === 408);
            fail(isKnownRejection ? "rejected" : stage === "write" ? "unknown" : "read-error",
              "服务未能确认本次历史操作。", status);
          }
          if (requestOptions.expectedStatus === 204) return undefined;
          let payload: unknown;
          try {
            payload = await raceWithAbort(response.json(), timed);
          } catch {
            fail(stage === "write" ? "unknown" : "invalid-response", "服务响应无法读取。");
          }
          assertCurrent(stage, dispatched, timed.controller.signal);
          return payload;
        } catch (error) {
          if (error instanceof AssetGenerationHistoryError) throw error;
          if (stage === "write" && dispatched) {
            fail("unknown", timed.didTimeout() ? "请求等待超时，服务端结果未知。" : "请求已发出，服务端结果未知。");
          }
          fail("read-error", timed.didTimeout() ? "读取历史超时。" : "无法读取历史记录。");
        } finally {
          if (response?.body !== null && response?.body !== undefined && !response.bodyUsed) {
            await response.body.cancel().catch(() => undefined);
          }
          timed.dispose();
        }
      }

      function messagesPath(): string {
        const params = new URLSearchParams({
          asset_type: capturedAssetType,
          asset_id: capturedAssetId,
          chat_mode: capturedMode,
        });
        return `/chapters/${encodeURIComponent(capturedChapterId)}/asset-chat-messages?${params.toString()}`;
      }

      function messagePath(messageId: string, suffix = ""): string {
        if (!isIdentifier(messageId)) fail("not-sent", "消息身份无法核对，未发送请求。");
        return `/chapters/${encodeURIComponent(capturedChapterId)}/asset-chat-messages${suffix}/${encodeURIComponent(messageId)}`;
      }

      async function readImage(
        route: MediaExportUrlRoute,
        signal: AbortSignal,
      ): Promise<Blob> {
        assertCurrent("read", false, signal);
        const timed = createTimedRequest(signal, imagePreviewTimeoutMs);
        const headers: Record<string, string> = { Accept: Object.keys(CANONICAL_IMAGE_MIME).join(", ") };
        if (route.kind === "protected-uploads") headers.Authorization = `Bearer ${token}`;
        const requestMode: RequestMode = route.kind === "protected-uploads" ? "same-origin" : "cors";
        let response: Response | null = null;
        try {
          const responsePromise = fetcher(route.url, {
            method: "GET",
            headers,
            signal: timed.controller.signal,
            mode: requestMode,
            credentials: "omit",
            cache: "no-store",
            redirect: route.kind === "protected-uploads" ? "error" : "follow",
            referrerPolicy: "no-referrer",
          });
          void responsePromise.then((lateResponse) => {
            if (timed.controller.signal.aborted) void lateResponse.body?.cancel().catch(() => undefined);
          }, () => undefined);
          try {
            response = await raceWithAbort(responsePromise, timed);
          } catch {
            fail("read-error", timed.didTimeout() ? "图片读取超时。" : "无法读取这张图片。");
          }
          assertCurrent("read", true, timed.controller.signal);
          if (route.kind === "protected-uploads" && response.status === 401) {
            await response.body?.cancel().catch(() => undefined);
            fail("unauthorized", "登录状态已失效，请重新登录。", 401);
          }
          const contentType = response.headers.get("Content-Type") ?? "";
          const mimeType = contentType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
          const finalUrl = response.url;
          if (finalUrl === "") fail("read-error", "图片响应来源无法核对。");
          let finalRoute: MediaExportUrlRoute;
          try {
            finalRoute = classifyMediaExportUrl(finalUrl, baseUrl);
          } catch {
            fail("read-error", "图片来源跳转到不允许的地址。");
          }
          if (route.kind === "protected-uploads") {
            if (finalRoute.kind !== "protected-uploads" || finalRoute.url !== route.url) {
              fail("read-error", "受保护图片发生了未允许的跳转。");
            }
          } else if (finalRoute.kind !== "anonymous-public" || new URL(finalRoute.url).protocol !== "https:") {
            fail("read-error", "公开图片发生了未允许的跳转。");
          }
          const blob = await readImageResponse(response, mimeType, timed.controller.signal, IMAGE_PREVIEW_MAX_BYTES);
          assertCurrent("read", true, timed.controller.signal);
          return blob;
        } catch (error) {
          if (error instanceof AssetGenerationHistoryError) throw error;
          return fail("read-error", timed.didTimeout() ? "图片读取超时。" : "图片读取失败。");
        } finally {
          if (response?.body !== null && response?.body !== undefined && !response.bodyUsed) {
            await response.body.cancel().catch(() => undefined);
          }
          timed.dispose();
        }
      }

      return {
        isCurrent,
        async readMessages(signal): Promise<readonly AssetGenerationHistoryMessage[]> {
          const payload = await request(messagesPath(), signal, { method: "GET", expectedStatus: 200 });
          return parseMessages(payload);
        },
        async readMedia(url, signal): Promise<Blob> {
          assertCurrent("read", false, signal);
          let route: MediaExportUrlRoute;
          try {
            route = classifyMediaExportUrl(url, baseUrl);
          } catch {
            fail("read-error", "媒体来源地址不符合预览要求。");
          }
          if (route.kind === "anonymous-public" && new URL(route.url).protocol !== "https:") {
            fail("read-error", "公开媒体预览只允许 HTTPS 来源。");
          }
          if (capturedMode === "image") return await readImage(route, signal);
          try {
            const media = await readMediaExportSource({
              mediaUrl: url,
              apiBaseUrl: baseUrl,
              token,
              fetcher,
              signal,
            });
            assertCurrent("read", true, signal);
            return media.blob;
          } catch (error) {
            if (error instanceof AssetGenerationHistoryError) throw error;
            if (route.kind === "protected-uploads"
              && error instanceof MediaExportReaderError
              && error.status === 401
              && error.source === "application") {
              fail("unauthorized", "登录状态已失效，请重新登录。", 401);
            }
            const status = error instanceof MediaExportReaderError ? error.status ?? null : null;
            fail("read-error", "无法读取这段生成视频。", status);
          }
        },
        async retryOssUpload(url, signal): Promise<string> {
          const payload = await request("/upload/retry-oss", signal, {
            method: "POST",
            body: { url },
            expectedStatus: 200,
            timeoutMs: retryOssTimeoutMs,
          });
          if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
            fail("unknown", "转存已发送，但回执无法识别；不会自动重发。");
          }
          try {
            return parseMediaUrl((payload as Record<string, unknown>).url);
          } catch {
            fail("unknown", "转存已响应，但地址无法核对；不会自动重发。");
          }
        },
        async updateMessageContent(messageId, content, signal): Promise<void> {
          const payload = await request(messagePath(messageId), signal, {
            method: "PUT",
            body: { content },
            expectedStatus: 200,
          });
          let message: AssetGenerationHistoryMessage;
          try {
            message = parseMessage(payload);
          } catch {
            fail("unknown", "消息PUT已响应，但回执身份无法核对；不会自动再次写入。");
          }
          if (message.id !== messageId
            || message.chapter_id !== capturedChapterId
            || message.asset_type !== capturedAssetType
            || message.asset_id !== capturedAssetId
            || message.chat_mode !== capturedMode
            || message.role !== "assistant"
            || message.content !== content) {
            fail("unknown", "消息已写回，但回执身份无法核对；不会自动再次写入。");
          }
        },
        async deleteMessage(messageId, signal): Promise<void> {
          await request(messagePath(messageId, "/single"), signal, {
            method: "DELETE",
            expectedStatus: 204,
          });
        },
      };
    },
  };
}
