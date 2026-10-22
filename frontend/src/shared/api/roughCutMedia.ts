import { ApiError, isAbortError } from "./errors";

export const MAX_ROUGH_CUT_MEDIA_BYTES = 64 * 1024 * 1024;
export const ROUGH_CUT_MEDIA_TIMEOUT_MS = 30_000;

const VIDEO_MIME_TYPES = new Set([
  "video/mp4",
  "video/webm",
  "video/ogg",
  "video/quicktime",
  "video/x-m4v",
]);
const CONTROL_OR_WHITESPACE = /[\s\u0000-\u001f\u007f]/u;
const AMBIGUOUS_PATH_ENCODING = /%(?:2f|5c|25)/iu;

export interface RoughCutMediaReadOptions {
  previewUrl: string;
  apiBaseUrl: string;
  token: string;
  fetcher: typeof fetch;
  signal: AbortSignal;
  timeoutMs?: number;
}

function invalidMediaReference(): ApiError {
  return new ApiError("invalid-response", "当前镜头的视频引用不符合本地播放要求。");
}

function validateRawUploadsPath(rawPath: string): string {
  const normalizedPath = rawPath.startsWith("/") ? rawPath : `/${rawPath}`;
  if (
    !normalizedPath.startsWith("/uploads/")
    || normalizedPath.includes("\\")
    || AMBIGUOUS_PATH_ENCODING.test(normalizedPath)
  ) {
    throw invalidMediaReference();
  }

  const segments = normalizedPath.slice(1).split("/");
  if (segments.some((segment) => segment === "")) {
    throw invalidMediaReference();
  }
  for (const segment of segments) {
    let decoded: string;
    try {
      decoded = decodeURIComponent(segment);
    } catch {
      throw invalidMediaReference();
    }
    if (
      decoded === "."
      || decoded === ".."
      || decoded.includes("/")
      || decoded.includes("\\")
      || /[\u0000-\u001f\u007f]/u.test(decoded)
    ) {
      throw invalidMediaReference();
    }
  }
  return normalizedPath;
}

export function resolveRoughCutMediaUrl(previewUrl: string, apiBaseUrl: string): URL {
  if (
    typeof previewUrl !== "string"
    || previewUrl.length === 0
    || CONTROL_OR_WHITESPACE.test(previewUrl)
    || previewUrl.includes("\\")
    || previewUrl.includes("?")
    || previewUrl.includes("#")
  ) {
    throw invalidMediaReference();
  }

  let api: URL;
  try {
    api = new URL(apiBaseUrl);
  } catch {
    throw new ApiError("invalid-response", "当前 API 地址无法用于读取粗剪视频。");
  }
  if (
    (api.protocol !== "http:" && api.protocol !== "https:")
    || api.username !== ""
    || api.password !== ""
    || api.search !== ""
    || api.hash !== ""
    || api.pathname.replace(/\/$/u, "") !== "/api"
  ) {
    throw new ApiError("invalid-response", "当前 API 地址无法用于读取粗剪视频。");
  }

  const isAbsoluteHttpUrl = /^https?:\/\//iu.test(previewUrl);
  let rawPath: string;
  if (isAbsoluteHttpUrl) {
    const match = /^https?:\/\/[^/?#]+([^?#]*)$/iu.exec(previewUrl);
    if (match === null || match[1] === undefined || match[1] === "") {
      throw invalidMediaReference();
    }
    rawPath = match[1];
  } else {
    if (/^[a-z][a-z\d+.-]*:/iu.test(previewUrl) || previewUrl.startsWith("//")) {
      throw invalidMediaReference();
    }
    rawPath = previewUrl;
  }

  validateRawUploadsPath(rawPath);

  let mediaUrl: URL;
  try {
    mediaUrl = new URL(previewUrl, `${api.origin}/`);
  } catch {
    throw invalidMediaReference();
  }
  if (
    (mediaUrl.protocol !== "http:" && mediaUrl.protocol !== "https:")
    || mediaUrl.origin !== api.origin
    || mediaUrl.username !== ""
    || mediaUrl.password !== ""
    || mediaUrl.search !== ""
    || mediaUrl.hash !== ""
    || validateRawUploadsPath(mediaUrl.pathname) !== mediaUrl.pathname
  ) {
    throw invalidMediaReference();
  }
  return mediaUrl;
}

function abortError(didTimeout: boolean): ApiError {
  return didTimeout
    ? new ApiError("timeout", "视频读取超过 30 秒，请重试或跳过此镜。")
    : new ApiError("aborted", "视频读取已取消。");
}

function declaredContentLength(response: Response): number | null {
  const value = response.headers.get("Content-Length");
  if (value === null) {
    return null;
  }
  if (!/^\d+$/u.test(value)) {
    throw new ApiError("invalid-response", "视频响应的长度信息无效。");
  }
  const length = Number(value);
  if (!Number.isSafeInteger(length)) {
    throw new ApiError("invalid-response", "视频响应的长度信息无效。");
  }
  return length;
}

function responseMimeType(response: Response): string {
  const contentType = response.headers.get("Content-Type") ?? "";
  const mimeType = contentType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  if (!VIDEO_MIME_TYPES.has(mimeType)) {
    throw new ApiError("invalid-response", "服务返回的内容不是受支持的视频格式。");
  }
  return mimeType;
}

export async function readRoughCutMedia({
  previewUrl,
  apiBaseUrl,
  token,
  fetcher,
  signal,
  timeoutMs = ROUGH_CUT_MEDIA_TIMEOUT_MS,
}: RoughCutMediaReadOptions): Promise<Blob> {
  if (signal.aborted) {
    throw abortError(false);
  }
  if (token.trim() === "") {
    throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
  }

  const mediaUrl = resolveRoughCutMediaUrl(previewUrl, apiBaseUrl);
  const controller = new AbortController();
  let didTimeout = false;
  let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  let response: Response | null = null;
  let finished = false;
  const onExternalAbort = () => {
    controller.abort();
    if (reader !== null) {
      void reader.cancel().catch(() => undefined);
    }
  };
  signal.addEventListener("abort", onExternalAbort, { once: true });
  const boundedTimeout = Math.min(
    Math.max(1, timeoutMs),
    ROUGH_CUT_MEDIA_TIMEOUT_MS,
  );
  const timer = setTimeout(() => {
    didTimeout = true;
    controller.abort();
    if (reader !== null) {
      void reader.cancel().catch(() => undefined);
    }
  }, boundedTimeout);

  try {
    let requestResponse: Response;
    try {
      requestResponse = await fetcher(mediaUrl, {
        method: "GET",
        headers: {
          Accept: "video/mp4, video/webm, video/ogg, video/quicktime, video/x-m4v",
          Authorization: `Bearer ${token}`,
        },
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        referrerPolicy: "no-referrer",
      });
    } catch (error) {
      if (controller.signal.aborted || signal.aborted) {
        throw abortError(didTimeout);
      }
      if (isAbortError(error)) {
        throw abortError(false);
      }
      throw new ApiError("network", "无法读取此镜头的视频，请重试或跳过此镜。");
    }
    response = requestResponse;

    if (controller.signal.aborted || signal.aborted) {
      throw abortError(didTimeout);
    }
    if (!response.ok) {
      throw new ApiError("http", "无法读取此镜头的视频，请重试或跳过此镜。", response.status);
    }

    const mimeType = responseMimeType(response);
    const contentLength = declaredContentLength(response);
    if (contentLength !== null && contentLength > MAX_ROUGH_CUT_MEDIA_BYTES) {
      throw new ApiError("invalid-response", "视频超过 64 MiB，无法在此处播放。");
    }
    if (response.body === null) {
      throw new ApiError("invalid-response", "视频响应没有可读取的内容。");
    }

    reader = response.body.getReader();
    const chunks: ArrayBuffer[] = [];
    let totalBytes = 0;
    while (true) {
      if (controller.signal.aborted || signal.aborted) {
        throw abortError(didTimeout);
      }
      let next: ReadableStreamReadResult<Uint8Array>;
      try {
        next = await reader.read();
      } catch (error) {
        if (controller.signal.aborted || signal.aborted) {
          throw abortError(didTimeout);
        }
        throw new ApiError("network", "视频读取中断，请重试或跳过此镜。");
      }
      if (next.done) {
        break;
      }
      totalBytes += next.value.byteLength;
      if (totalBytes > MAX_ROUGH_CUT_MEDIA_BYTES) {
        throw new ApiError("invalid-response", "视频超过 64 MiB，无法在此处播放。");
      }
      const chunk = new Uint8Array(next.value.byteLength);
      chunk.set(next.value);
      chunks.push(chunk.buffer);
    }

    if (controller.signal.aborted || signal.aborted) {
      throw abortError(didTimeout);
    }
    finished = true;
    return new Blob(chunks, { type: mimeType });
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    if (controller.signal.aborted || signal.aborted) {
      throw abortError(didTimeout);
    }
    throw new ApiError("invalid-response", "视频响应无法读取，请重试或跳过此镜。");
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", onExternalAbort);
    if (!finished) {
      if (reader !== null) {
        await reader.cancel().catch(() => undefined);
      } else if (response?.body !== null && response?.body !== undefined) {
        await response.body.cancel().catch(() => undefined);
      }
    }
    reader?.releaseLock();
  }
}

export function isSafeRoughCutMediaReference(previewUrl: string, apiBaseUrl: string): boolean {
  try {
    resolveRoughCutMediaUrl(previewUrl, apiBaseUrl);
    return true;
  } catch {
    return false;
  }
}
