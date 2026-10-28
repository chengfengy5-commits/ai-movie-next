import { ApiError } from "./errors";
import {
  classifyMediaExportUrl,
  MediaExportReaderError,
  readAnonymousMediaExport,
  readProtectedMediaExport,
  resolveMediaExportDownloadLinks,
} from "./mediaExportReader";

export const MAX_ROUGH_CUT_MEDIA_BYTES = 64 * 1024 * 1024;
export const ROUGH_CUT_MEDIA_TIMEOUT_MS = 30_000;

export interface RoughCutMediaReadOptions {
  previewUrl: string;
  apiBaseUrl: string;
  token: string;
  fetcher: typeof fetch;
  signal: AbortSignal;
  isCurrent?: () => boolean;
  timeoutMs?: number;
}

function invalidMediaReference(): ApiError {
  return new ApiError("invalid-response", "当前镜头的视频引用不符合粗剪试看要求。");
}

function classifyRoughCutMediaReference(previewUrl: string, apiBaseUrl: string) {
  try {
    return classifyMediaExportUrl(previewUrl, apiBaseUrl);
  } catch {
    throw invalidMediaReference();
  }
}

function mediaExportError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }
  if (!(error instanceof MediaExportReaderError)) {
    return new ApiError("invalid-response", "视频响应无法读取，请重试或跳过此镜。");
  }
  if (error.source === "application" && error.status === 401) {
    return new ApiError("http", "登录状态已失效，请重新登录。", 401);
  }
  if (error.kind === "aborted") {
    return new ApiError("aborted", "粗剪视频读取已取消。");
  }
  if (error.kind === "timeout") {
    return new ApiError("timeout", "粗剪视频读取超时，请重试或跳过此镜。");
  }
  if (error.kind === "network") {
    return new ApiError("network", "无法连接或读取此镜头的视频，请重试或跳过此镜。");
  }
  if (error.kind === "http") {
    const message = error.source === "anonymous-media"
      ? "远程视频读取失败，请重试或跳过此镜。"
      : "无法读取此镜头的视频，请重试或跳过此镜。";
    return new ApiError("http", message, error.source === "application" ? error.status ?? null : null);
  }
  return new ApiError("invalid-response", "视频来源或响应不符合粗剪试看要求，或超过 64 MiB。");
}

function abortedError(didTimeout: boolean): ApiError {
  return didTimeout
    ? new ApiError("timeout", "粗剪视频读取超时，请重试或跳过此镜。")
    : new ApiError("aborted", "粗剪视频读取已取消。");
}

export function resolveRoughCutMediaUrl(previewUrl: string, apiBaseUrl: string): URL {
  const route = classifyRoughCutMediaReference(previewUrl, apiBaseUrl);
  if (route.kind !== "protected-uploads") {
    throw invalidMediaReference();
  }
  return new URL(route.url);
}

function assertReadIsCurrent(options: {
  signal: AbortSignal;
  isCurrent?: () => boolean;
  deadline: number;
  didTimeout: () => boolean;
  markTimedOut: () => void;
  abortController: AbortController;
}): void {
  if (options.signal.aborted || options.abortController.signal.aborted) {
    throw abortedError(options.didTimeout());
  }
  if (options.isCurrent !== undefined && !options.isCurrent()) {
    throw new ApiError("aborted", "登录状态已变化，粗剪视频读取已停止。");
  }
  if (Date.now() >= options.deadline) {
    options.markTimedOut();
    options.abortController.abort();
    throw abortedError(true);
  }
}

export async function readRoughCutMedia({
  previewUrl,
  apiBaseUrl,
  token,
  fetcher,
  signal,
  isCurrent,
  timeoutMs = ROUGH_CUT_MEDIA_TIMEOUT_MS,
}: RoughCutMediaReadOptions): Promise<Blob> {
  if (signal.aborted) {
    throw abortedError(false);
  }
  if (token.trim() === "") {
    throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
  }
  if (isCurrent !== undefined && !isCurrent()) {
    throw new ApiError("aborted", "登录状态已变化，粗剪视频读取已停止。");
  }

  const route = classifyRoughCutMediaReference(previewUrl, apiBaseUrl);
  const boundedTimeout = Math.min(
    Math.max(1, timeoutMs),
    ROUGH_CUT_MEDIA_TIMEOUT_MS,
  );
  const deadline = Date.now() + boundedTimeout;
  const controller = new AbortController();
  let didTimeout = false;
  const abortFromOutside = () => controller.abort();
  signal.addEventListener("abort", abortFromOutside, { once: true });
  if (signal.aborted) {
    controller.abort();
  }
  const timer = setTimeout(() => {
    didTimeout = true;
    controller.abort();
  }, boundedTimeout);
  const readOptions = {
    signal: controller.signal,
    ...(isCurrent === undefined ? {} : { isCurrent }),
    deadline,
    didTimeout: () => didTimeout,
    markTimedOut: () => { didTimeout = true; },
    abortController: controller,
  };

  try {
    assertReadIsCurrent(readOptions);
    const remainingTimeoutMs = () => {
      assertReadIsCurrent(readOptions);
      return Math.max(1, Math.ceil(deadline - Date.now()));
    };

    if (route.kind === "protected-uploads") {
      const media = await readProtectedMediaExport({
        mediaUrl: route.url,
        apiBaseUrl,
        token,
        fetcher,
        signal: controller.signal,
        timeoutMs: remainingTimeoutMs(),
        maxBytes: MAX_ROUGH_CUT_MEDIA_BYTES,
      });
      assertReadIsCurrent(readOptions);
      return media.blob;
    }

    const links = await resolveMediaExportDownloadLinks({
      items: [{ url: route.url, filename: "rough-cut-preview.mp4" }],
      apiBaseUrl,
      token,
      fetcher,
      signal: controller.signal,
      ...(isCurrent === undefined ? {} : { isCurrent }),
      timeoutMs: remainingTimeoutMs(),
    });
    assertReadIsCurrent(readOptions);

    const link = links[0];
    if (links.length !== 1 || link === undefined) {
      throw new ApiError("invalid-response", "视频下载地址解析回执无效。");
    }
    const resolvedRoute = classifyRoughCutMediaReference(link.url, apiBaseUrl);
    if (resolvedRoute.kind !== "anonymous-public") {
      throw invalidMediaReference();
    }

    const media = await readAnonymousMediaExport({
      mediaUrl: resolvedRoute.url,
      apiBaseUrl,
      fetcher,
      signal: controller.signal,
      timeoutMs: remainingTimeoutMs(),
      maxBytes: MAX_ROUGH_CUT_MEDIA_BYTES,
    });
    assertReadIsCurrent(readOptions);
    return media.blob;
  } catch (error) {
    if (didTimeout) {
      throw abortedError(true);
    }
    if (signal.aborted || controller.signal.aborted) {
      throw abortedError(false);
    }
    if (isCurrent !== undefined && !isCurrent()) {
      throw new ApiError("aborted", "登录状态已变化，粗剪视频读取已停止。");
    }
    throw mediaExportError(error);
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", abortFromOutside);
  }
}

export function isSafeRoughCutMediaReference(previewUrl: string, apiBaseUrl: string): boolean {
  try {
    classifyMediaExportUrl(previewUrl, apiBaseUrl);
    return true;
  } catch {
    return false;
  }
}
