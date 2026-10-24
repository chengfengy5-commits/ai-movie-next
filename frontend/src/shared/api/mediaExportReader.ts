import {
  MEDIA_EXPORT_MAX_BYTES,
  MEDIA_EXPORT_MAX_ITEMS_PER_REQUEST,
  MEDIA_EXPORT_LINK_BODY_MAX_BYTES,
  MEDIA_EXPORT_LINK_TIMEOUT_MS,
  MEDIA_EXPORT_TIMEOUT_MS,
  type MediaExportMedia,
} from "./mediaExport";

const CONTROL_OR_WHITESPACE = /[\s\u0000-\u001f\u007f]/u;
const AMBIGUOUS_PATH_ENCODING = /%(?:2f|5c|25)/iu;
const VIDEO_MIME_TO_EXTENSION: Readonly<Record<string, string>> = {
  "video/mp4": "mp4",
  "video/webm": "webm",
  "video/quicktime": "mov",
  "video/x-msvideo": "avi",
  "video/x-matroska": "mkv",
  "video/x-m4v": "m4v",
  "video/ogg": "ogv",
};
const VIDEO_EXTENSIONS = new Set(Object.values(VIDEO_MIME_TO_EXTENSION));

export type MediaExportUrlRoute =
  | { readonly kind: "protected-uploads"; readonly url: string }
  | { readonly kind: "anonymous-public"; readonly url: string };

export type MediaExportReaderErrorKind = "invalid-response" | "http" | "network" | "timeout" | "aborted";
export type MediaExportReaderErrorSource = "application" | "anonymous-media";

export class MediaExportReaderError extends Error {
  constructor(
    readonly kind: MediaExportReaderErrorKind,
    message: string,
    readonly status?: number,
    readonly source: MediaExportReaderErrorSource = "application",
  ) {
    super(message);
    this.name = "MediaExportReaderError";
  }
}

export interface MediaExportFetchOptions {
  readonly fetcher: typeof fetch;
  readonly signal: AbortSignal;
  readonly timeoutMs?: number;
  readonly maxBytes?: number;
}

export interface ProtectedMediaExportReadOptions extends MediaExportFetchOptions {
  readonly mediaUrl: string;
  readonly apiBaseUrl: string;
  readonly token: string;
}

export interface AnonymousMediaExportReadOptions extends MediaExportFetchOptions {
  readonly mediaUrl: string;
  readonly apiBaseUrl: string;
}

export interface MediaExportDownloadLinkRequestItem {
  readonly url: string;
  readonly filename: string;
}

export interface MediaExportDownloadLinkResult extends MediaExportDownloadLinkRequestItem {
  readonly signed: boolean;
}

export interface ResolveMediaExportDownloadLinksOptions {
  readonly items: readonly MediaExportDownloadLinkRequestItem[];
  readonly apiBaseUrl: string;
  readonly token: string;
  readonly fetcher: typeof fetch;
  readonly signal: AbortSignal;
  readonly isCurrent?: () => boolean;
  readonly timeoutMs?: number;
  readonly maxResponseBytes?: number;
}

function invalidReference(): MediaExportReaderError {
  return new MediaExportReaderError("invalid-response", "视频来源地址不符合导出要求。");
}

function parseApiBaseUrl(apiBaseUrl: string): URL {
  let api: URL;
  try {
    api = new URL(apiBaseUrl);
  } catch {
    throw new MediaExportReaderError("invalid-response", "当前 API 地址无效，无法导出素材。");
  }
  if (
    (api.protocol !== "http:" && api.protocol !== "https:")
    || api.username !== ""
    || api.password !== ""
    || api.search !== ""
    || api.hash !== ""
    || api.pathname.replace(/\/$/u, "") !== "/api"
  ) {
    throw new MediaExportReaderError("invalid-response", "当前 API 地址无效，无法导出素材。");
  }
  return api;
}

function rawPathFromAbsoluteUrl(value: string): string {
  const match = /^https?:\/\/[^/?#]+([^?#]*)/iu.exec(value);
  return match?.[1] || "/";
}

function validateUploadsPath(rawPath: string): string {
  const normalizedPath = rawPath.startsWith("/") ? rawPath : "/" + rawPath;
  if (
    !normalizedPath.startsWith("/uploads/")
    || normalizedPath.includes("\\")
    || AMBIGUOUS_PATH_ENCODING.test(normalizedPath)
  ) {
    throw invalidReference();
  }
  const segments = normalizedPath.slice(1).split("/");
  if (segments.some((segment) => segment === "")) {
    throw invalidReference();
  }
  for (const segment of segments) {
    let decoded: string;
    try {
      decoded = decodeURIComponent(segment);
    } catch {
      throw invalidReference();
    }
    if (
      decoded === "."
      || decoded === ".."
      || decoded.includes("/")
      || decoded.includes("\\")
      || /[\u0000-\u001f\u007f]/u.test(decoded)
    ) {
      throw invalidReference();
    }
  }
  return normalizedPath;
}

/** Route legacy local upload paths separately from canonical anonymous HTTP(S) sources. */
export function classifyMediaExportUrl(value: string, apiBaseUrl: string): MediaExportUrlRoute {
  if (
    typeof value !== "string"
    || value.length === 0
    || CONTROL_OR_WHITESPACE.test(value)
    || value.includes("\\")
    || value.includes("#")
  ) {
    throw invalidReference();
  }

  const api = parseApiBaseUrl(apiBaseUrl);
  const isAbsoluteHttpUrl = /^https?:\/\//iu.test(value);
  if (!isAbsoluteHttpUrl) {
    if (/^[a-z][a-z\d+.-]*:/iu.test(value) || value.startsWith("//") || value.includes("?")) {
      throw invalidReference();
    }
    const rawPath = value.startsWith("/") ? value : "/" + value;
    validateUploadsPath(rawPath);
    const url = new URL(rawPath, api.origin + "/");
    if (url.pathname !== rawPath) {
      throw invalidReference();
    }
    return { kind: "protected-uploads", url: url.href };
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw invalidReference();
  }
  if (
    (url.protocol !== "http:" && url.protocol !== "https:")
    || url.username !== ""
    || url.password !== ""
    || url.hash !== ""
    || url.href !== value
  ) {
    throw invalidReference();
  }

  const rawPath = rawPathFromAbsoluteUrl(value);
  if (url.origin === api.origin && rawPath.startsWith("/uploads/")) {
    if (url.search !== "") {
      throw invalidReference();
    }
    const validatedPath = validateUploadsPath(rawPath);
    if (url.pathname !== validatedPath) {
      throw invalidReference();
    }
    return { kind: "protected-uploads", url: url.href };
  }
  if (url.origin === api.origin && url.pathname.startsWith("/uploads/")) {
    // URL normalizes dot segments. Do not let that make an unsafe upload anonymous.
    throw invalidReference();
  }
  return { kind: "anonymous-public", url: url.href };
}

function abortError(timedOut: boolean): MediaExportReaderError {
  return timedOut
    ? new MediaExportReaderError("timeout", "视频读取超过 600 秒，当前文件未能完成导出。")
    : new MediaExportReaderError("aborted", "视频读取已取消。");
}

function parseContentLength(response: Response): number | null {
  const rawLength = response.headers.get("Content-Length");
  if (rawLength === null) {
    return null;
  }
  if (!/^\d+$/u.test(rawLength)) {
    throw new MediaExportReaderError("invalid-response", "视频响应长度无效。");
  }
  const length = Number(rawLength);
  if (!Number.isSafeInteger(length)) {
    throw new MediaExportReaderError("invalid-response", "视频响应长度无效。");
  }
  return length;
}

function safeFinalUrl(rawUrl: string): URL {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    throw invalidReference();
  }
  if (
    (url.protocol !== "http:" && url.protocol !== "https:")
    || url.username !== ""
    || url.password !== ""
    || url.hash !== ""
  ) {
    throw invalidReference();
  }
  return url;
}

function mediaTypeFor(response: Response, finalUrl: URL): { mimeType: string; extension: string } {
  const contentType = response.headers.get("Content-Type") ?? "";
  const mimeType = contentType.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  const urlExtension = finalUrl.pathname.match(/\.([a-z\d]+)$/iu)?.[1]?.toLowerCase();
  const knownUrlExtension = urlExtension !== undefined && VIDEO_EXTENSIONS.has(urlExtension)
    ? urlExtension
    : null;

  if (mimeType === "application/octet-stream" && knownUrlExtension !== null) {
    const mappedMimeType = Object.entries(VIDEO_MIME_TO_EXTENSION)
      .find(([, extension]) => extension === knownUrlExtension)?.[0];
    return { mimeType: mappedMimeType ?? "application/octet-stream", extension: knownUrlExtension };
  }
  const mimeExtension = VIDEO_MIME_TO_EXTENSION[mimeType];
  if (mimeExtension === undefined) {
    throw new MediaExportReaderError("invalid-response", "服务器返回的内容不是受支持的视频格式。");
  }
  return { mimeType, extension: mimeExtension };
}

function hasComparableContentLength(response: Response): boolean {
  const contentEncoding = response.headers.get("Content-Encoding");
  if (contentEncoding !== null) {
    const normalized = contentEncoding.trim().toLowerCase();
    return normalized === "" || normalized === "identity";
  }
  // Cross-origin fetch may hide Content-Encoding unless the server exposes it.
  // In that case Content-Length can describe compressed wire bytes while fetch
  // yields decoded bytes, so only the streamed output size is comparable.
  return response.type !== "cors";
}

async function readMediaResponse(
  response: Response,
  finalUrl: URL,
  options: MediaExportFetchOptions,
  controller: AbortController,
  didTimeout: () => boolean,
  errorSource: MediaExportReaderErrorSource,
): Promise<MediaExportMedia> {
  if (controller.signal.aborted || options.signal.aborted) {
    throw abortError(didTimeout());
  }
  if (!response.ok) {
    await response.body?.cancel().catch(() => undefined);
    throw new MediaExportReaderError("http", "无法读取此视频素材。", response.status, errorSource);
  }

  const mediaType = mediaTypeFor(response, finalUrl);
  const maxBytes = Math.min(options.maxBytes ?? MEDIA_EXPORT_MAX_BYTES, MEDIA_EXPORT_MAX_BYTES);
  const compareDeclaredLength = hasComparableContentLength(response);
  const declaredLength = parseContentLength(response);
  if (compareDeclaredLength && declaredLength !== null && declaredLength > maxBytes) {
    await response.body?.cancel().catch(() => undefined);
    throw new MediaExportReaderError("invalid-response", "视频超过 200 MiB，无法导出。");
  }
  if (response.body === null) {
    throw new MediaExportReaderError("invalid-response", "视频响应没有可读取的内容。");
  }

  const reader = response.body.getReader();
  const cancelReader = () => {
    void reader.cancel().catch(() => undefined);
  };
  controller.signal.addEventListener("abort", cancelReader, { once: true });
  const chunks: ArrayBuffer[] = [];
  let totalBytes = 0;
  let completed = false;
  try {
    while (true) {
      if (controller.signal.aborted || options.signal.aborted) {
        throw abortError(didTimeout());
      }
      let next: ReadableStreamReadResult<Uint8Array>;
      try {
        next = await reader.read();
      } catch {
        if (controller.signal.aborted || options.signal.aborted) {
          throw abortError(didTimeout());
        }
        throw new MediaExportReaderError("network", "视频读取中断。");
      }
      if (next.done) {
        break;
      }
      totalBytes += next.value.byteLength;
      if (totalBytes > maxBytes) {
        throw new MediaExportReaderError("invalid-response", "视频超过 200 MiB，无法导出。");
      }
      const chunk = new Uint8Array(next.value.byteLength);
      chunk.set(next.value);
      chunks.push(chunk.buffer);
    }
    if (controller.signal.aborted || options.signal.aborted) {
      throw abortError(didTimeout());
    }
    if (totalBytes === 0) {
      throw new MediaExportReaderError("invalid-response", "视频响应内容为空。");
    }
    if (compareDeclaredLength && declaredLength !== null && totalBytes !== declaredLength) {
      throw new MediaExportReaderError("invalid-response", "视频响应长度与实际内容不一致。");
    }
    completed = true;
    return {
      blob: new Blob(chunks, { type: mediaType.mimeType }),
      mimeType: mediaType.mimeType,
      extension: mediaType.extension,
      finalUrl: finalUrl.href,
    };
  } finally {
    controller.signal.removeEventListener("abort", cancelReader);
    if (!completed) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
}

async function fetchMediaExport(
  options: MediaExportFetchOptions,
  mediaUrl: string,
  headers: HeadersInit,
  redirect: RequestRedirect,
  mode?: RequestMode,
  errorSource: MediaExportReaderErrorSource = "application",
): Promise<MediaExportMedia> {
  if (options.signal.aborted) {
    throw abortError(false);
  }
  const sourceUrl = safeFinalUrl(mediaUrl);
  const controller = new AbortController();
  let timedOut = false;
  let response: Response | null = null;
  const cancelActiveBody = () => {
    if (response?.body !== null && response?.body !== undefined) {
      void response.body.cancel().catch(() => undefined);
    }
  };
  const onAbort = () => {
    controller.abort();
    cancelActiveBody();
  };
  options.signal.addEventListener("abort", onAbort, { once: true });
  const timeoutMs = Math.min(Math.max(1, options.timeoutMs ?? MEDIA_EXPORT_TIMEOUT_MS), MEDIA_EXPORT_TIMEOUT_MS);
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
    cancelActiveBody();
  }, timeoutMs);
  try {
    try {
      const requestInit: RequestInit = {
        method: "GET",
        headers,
        signal: controller.signal,
        credentials: "omit",
        cache: "no-store",
        redirect,
        referrerPolicy: "no-referrer",
        ...(mode === undefined ? {} : { mode }),
      };
      response = await options.fetcher(sourceUrl, requestInit);
    } catch (error) {
      if (controller.signal.aborted || options.signal.aborted) {
        throw abortError(timedOut);
      }
      if (error instanceof DOMException && error.name === "AbortError") {
        throw abortError(false);
      }
      throw new MediaExportReaderError("network", "无法读取视频来源，请检查来源访问权限。");
    }
    if (controller.signal.aborted || options.signal.aborted) {
      throw abortError(timedOut);
    }
    const finalUrl = safeFinalUrl(response.url);
    if (redirect === "error" && finalUrl.href !== sourceUrl.href) {
      await response.body?.cancel().catch(() => undefined);
      throw new MediaExportReaderError("invalid-response", "受保护的视频来源发生了未允许的跳转。");
    }
    return await readMediaResponse(response, finalUrl, options, controller, () => timedOut, errorSource);
  } finally {
    clearTimeout(timer);
    options.signal.removeEventListener("abort", onAbort);
    cancelActiveBody();
  }
}

export function readProtectedMediaExport(
  options: ProtectedMediaExportReadOptions,
): Promise<MediaExportMedia> {
  const route = classifyMediaExportUrl(options.mediaUrl, options.apiBaseUrl);
  if (route.kind !== "protected-uploads" || route.url !== options.mediaUrl) {
    return Promise.reject(invalidReference());
  }
  if (options.token.trim() === "") {
    return Promise.reject(new MediaExportReaderError("http", "登录状态已失效，请重新登录。", 401));
  }
  return fetchMediaExport(
    options,
    route.url,
    {
      Accept: "video/mp4, video/webm, video/quicktime, video/x-msvideo, video/x-matroska, video/x-m4v, video/ogg, application/octet-stream",
      Authorization: "Bearer " + options.token,
    },
    "error",
  );
}

export function readAnonymousMediaExport(
  options: AnonymousMediaExportReadOptions,
): Promise<MediaExportMedia> {
  const route = classifyMediaExportUrl(options.mediaUrl, options.apiBaseUrl);
  if (route.kind !== "anonymous-public" || route.url !== options.mediaUrl) {
    return Promise.reject(invalidReference());
  }
  return fetchMediaExport(
    options,
    route.url,
    {
      Accept: "video/mp4, video/webm, video/quicktime, video/x-msvideo, video/x-matroska, video/x-m4v, video/ogg, application/octet-stream",
    },
    "follow",
    "cors",
    "anonymous-media",
  );
}

export function readMediaExportSource(options: {
  readonly mediaUrl: string;
  readonly apiBaseUrl: string;
  readonly token: string;
  readonly fetcher: typeof fetch;
  readonly signal: AbortSignal;
  readonly timeoutMs?: number;
  readonly maxBytes?: number;
}): Promise<MediaExportMedia> {
  const route = classifyMediaExportUrl(options.mediaUrl, options.apiBaseUrl);
  if (route.kind === "protected-uploads") {
    return readProtectedMediaExport({
      ...options,
      mediaUrl: route.url,
      apiBaseUrl: options.apiBaseUrl,
    });
  }
  // The anonymous reader has no token parameter, so public fetches and redirects
  // cannot receive the application bearer.
  return readAnonymousMediaExport({
    mediaUrl: route.url,
    apiBaseUrl: options.apiBaseUrl,
    fetcher: options.fetcher,
    signal: options.signal,
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
    ...(options.maxBytes === undefined ? {} : { maxBytes: options.maxBytes }),
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function resolveMediaExportDownloadLinks(
  options: ResolveMediaExportDownloadLinksOptions,
): Promise<MediaExportDownloadLinkResult[]> {
  const api = parseApiBaseUrl(options.apiBaseUrl);
  if (options.token.trim() === "") {
    throw new MediaExportReaderError("http", "登录状态已失效，请重新登录。", 401);
  }
  const normalizedItems = options.items.map((item) => {
    if (typeof item.filename !== "string" || item.filename.trim() === "") {
      throw new MediaExportReaderError("invalid-response", "导出队列中的文件名无效。");
    }
    return {
      url: classifyMediaExportUrl(item.url, options.apiBaseUrl).url,
      filename: item.filename,
    };
  });

  const results: MediaExportDownloadLinkResult[] = [];
  for (let offset = 0; offset < normalizedItems.length; offset += MEDIA_EXPORT_MAX_ITEMS_PER_REQUEST) {
    if (options.signal.aborted || (options.isCurrent !== undefined && !options.isCurrent())) {
      throw new MediaExportReaderError("aborted", "当前页面身份已变化，已停止解析下载地址。");
    }
    const batch = normalizedItems.slice(offset, offset + MEDIA_EXPORT_MAX_ITEMS_PER_REQUEST);
    const requestBody = JSON.stringify({ items: batch });
    if (new TextEncoder().encode(requestBody).byteLength > MEDIA_EXPORT_LINK_BODY_MAX_BYTES) {
      throw new MediaExportReaderError("invalid-response", "下载地址解析请求超过允许大小。");
    }

    const controller = new AbortController();
    let timedOut = false;
    const abortFromOutside = () => controller.abort();
    options.signal.addEventListener("abort", abortFromOutside, { once: true });
    if (options.signal.aborted) {
      controller.abort();
    }
    const timeoutMs = Math.min(
      Math.max(1, options.timeoutMs ?? MEDIA_EXPORT_LINK_TIMEOUT_MS),
      MEDIA_EXPORT_LINK_TIMEOUT_MS,
    );
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeoutMs);
    try {
      let response: Response;
      try {
        response = await options.fetcher(new URL("/api/sign-download-urls", api.origin + "/"), {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            Authorization: "Bearer " + options.token,
          },
          body: requestBody,
          signal: controller.signal,
          credentials: "omit",
          cache: "no-store",
          redirect: "error",
          referrerPolicy: "no-referrer",
        });
      } catch {
        if (timedOut) {
          throw new MediaExportReaderError("timeout", "下载地址解析请求超过 15 秒，未自动重试。");
        }
        if (controller.signal.aborted || options.signal.aborted) {
          throw new MediaExportReaderError("aborted", "下载地址解析请求已取消。");
        }
        throw new MediaExportReaderError("network", "下载地址解析请求失败；可在确认后手动重试。");
      }
      try {
        if (controller.signal.aborted || options.signal.aborted) {
          await response.body?.cancel().catch(() => undefined);
          if (timedOut) {
            throw new MediaExportReaderError("timeout", "下载地址解析请求超过 15 秒，未自动重试。");
          }
          throw new MediaExportReaderError("aborted", "下载地址解析请求已取消。");
        }
        if (options.isCurrent !== undefined && !options.isCurrent()) {
          await response.body?.cancel().catch(() => undefined);
          throw new MediaExportReaderError("aborted", "当前页面身份已变化，已停止解析下载地址。");
        }
        if (!response.ok) {
          await response.body?.cancel().catch(() => undefined);
          throw new MediaExportReaderError(
            "http",
            "下载地址解析失败；可在确认后手动重试。",
            response.status,
            "application",
          );
        }
        const payload = await readBoundedLinkResponse(response, {
          signal: controller.signal,
          didTimeout: () => timedOut,
          ...(options.maxResponseBytes === undefined ? {} : { maxBytes: options.maxResponseBytes }),
        });
        if (options.isCurrent !== undefined && !options.isCurrent()) {
          throw new MediaExportReaderError("aborted", "当前页面身份已变化，已停止解析下载地址。");
        }
        if (!isRecord(payload) || !Array.isArray(payload.items) || payload.items.length !== batch.length) {
          throw new MediaExportReaderError("invalid-response", "下载地址解析回执数量与顺序无效。");
        }
        for (let index = 0; index < batch.length; index += 1) {
          const value = payload.items[index];
          const requested = batch[index];
          if (!isRecord(value) || requested === undefined) {
            throw new MediaExportReaderError("invalid-response", "下载地址解析回执格式无效。");
          }
          if (
            typeof value.url !== "string"
            || value.filename !== requested.filename
            || typeof value.signed !== "boolean"
            || value.signed !== (value.url !== requested.url)
          ) {
            throw new MediaExportReaderError("invalid-response", "下载地址解析回执与队列顺序不匹配。");
          }
          const resolvedUrl = classifyMediaExportUrl(value.url, options.apiBaseUrl).url;
          results.push({ url: resolvedUrl, filename: requested.filename, signed: value.signed });
        }
      } finally {
        clearTimeout(timer);
        options.signal.removeEventListener("abort", abortFromOutside);
      }
    } catch (error) {
      clearTimeout(timer);
      options.signal.removeEventListener("abort", abortFromOutside);
      throw error;
    }
  }
  return results;
}

async function readBoundedLinkResponse(
  response: Response,
  options: {
    readonly signal: AbortSignal;
    readonly didTimeout: () => boolean;
    readonly maxBytes?: number;
  },
): Promise<unknown> {
  const maxBytes = Math.min(
    options.maxBytes ?? MEDIA_EXPORT_LINK_BODY_MAX_BYTES,
    MEDIA_EXPORT_LINK_BODY_MAX_BYTES,
  );
  const declaredLength = parseContentLength(response);
  if (hasComparableContentLength(response) && declaredLength !== null && declaredLength > maxBytes) {
    await response.body?.cancel().catch(() => undefined);
    throw new MediaExportReaderError("invalid-response", "下载地址解析回执超过允许大小。");
  }
  if (response.body === null) {
    throw new MediaExportReaderError("invalid-response", "下载地址解析回执没有可读取的内容。");
  }
  const reader = response.body.getReader();
  const cancelReader = () => {
    void reader.cancel().catch(() => undefined);
  };
  options.signal.addEventListener("abort", cancelReader, { once: true });
  const chunks: Uint8Array[] = [];
  let byteLength = 0;
  let completed = false;
  try {
    while (true) {
      if (options.signal.aborted) {
        throw options.didTimeout()
          ? new MediaExportReaderError("timeout", "下载地址解析请求超过 15 秒，未自动重试。")
          : new MediaExportReaderError("aborted", "下载地址解析请求已取消。");
      }
      let next: ReadableStreamReadResult<Uint8Array>;
      try {
        next = await reader.read();
      } catch {
        if (options.signal.aborted) {
          throw options.didTimeout()
            ? new MediaExportReaderError("timeout", "下载地址解析请求超过 15 秒，未自动重试。")
            : new MediaExportReaderError("aborted", "下载地址解析请求已取消。");
        }
        throw new MediaExportReaderError("network", "下载地址解析回执读取中断。");
      }
      if (next.done) {
        break;
      }
      byteLength += next.value.byteLength;
      if (byteLength > maxBytes) {
        throw new MediaExportReaderError("invalid-response", "下载地址解析回执超过允许大小。");
      }
      const copy = new Uint8Array(next.value.byteLength);
      copy.set(next.value);
      chunks.push(copy);
    }
    if (options.signal.aborted) {
      throw options.didTimeout()
        ? new MediaExportReaderError("timeout", "下载地址解析请求超过 15 秒，未自动重试。")
        : new MediaExportReaderError("aborted", "下载地址解析请求已取消。");
    }
    const bytes = new Uint8Array(byteLength);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    let decoded: string;
    try {
      decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      throw new MediaExportReaderError("invalid-response", "下载地址解析回执不是有效UTF-8数据。");
    }
    let payload: unknown;
    try {
      payload = JSON.parse(decoded) as unknown;
    } catch {
      throw new MediaExportReaderError("invalid-response", "下载地址解析回执格式无效。");
    }
    completed = true;
    return payload;
  } finally {
    options.signal.removeEventListener("abort", cancelReader);
    if (!completed) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
}
