import type { MediaExportMedia } from "./mediaExport";
import {
  classifyMediaExportUrl,
  MediaExportReaderError,
  readAnonymousMediaExport,
  readProtectedMediaExport,
  resolveMediaExportDownloadLinks,
  type MediaExportDownloadLinkRequestItem,
  type MediaExportDownloadLinkResult,
  type MediaExportUrlRoute,
} from "./mediaExportReader";

export interface MediaExportSessionTransport {
  readonly apiBaseUrl: string;
  isCurrent(): boolean;
  classifyUrl(value: string): MediaExportUrlRoute;
  resolveDownloadLinks(
    items: readonly MediaExportDownloadLinkRequestItem[],
    signal: AbortSignal,
  ): Promise<MediaExportDownloadLinkResult[]>;
  readMedia(mediaUrl: string, signal: AbortSignal): Promise<MediaExportMedia>;
}

export interface CaptureMediaExportSessionOptions {
  readonly apiBaseUrl: string;
  readonly token: string;
  readonly fetcher: typeof fetch;
  readonly isCurrent: () => boolean;
}

/** Captures credentials in a closure; the returned transport never exposes them. */
export function captureMediaExportSession(
  options: CaptureMediaExportSessionOptions,
): MediaExportSessionTransport {
  const { apiBaseUrl, token, fetcher } = options;
  const isCurrent = (): boolean => {
    try {
      return options.isCurrent();
    } catch {
      return false;
    }
  };

  function requireCurrent(signal: AbortSignal): void {
    if (signal.aborted || !isCurrent()) {
      throw new MediaExportReaderError("aborted", "当前登录或页面身份已变化，已停止媒体导出。");
    }
  }

  return Object.freeze({
    apiBaseUrl,
    isCurrent,
    classifyUrl(value: string): MediaExportUrlRoute {
      return classifyMediaExportUrl(value, apiBaseUrl);
    },
    resolveDownloadLinks(
      items: readonly MediaExportDownloadLinkRequestItem[],
      signal: AbortSignal,
    ): Promise<MediaExportDownloadLinkResult[]> {
      try {
        requireCurrent(signal);
      } catch (error) {
        return Promise.reject(error);
      }
      return resolveMediaExportDownloadLinks({
        items,
        apiBaseUrl,
        token,
        fetcher,
        signal,
        isCurrent,
      });
    },
    async readMedia(mediaUrl: string, signal: AbortSignal): Promise<MediaExportMedia> {
      requireCurrent(signal);
      const route = classifyMediaExportUrl(mediaUrl, apiBaseUrl);
      const media = route.kind === "protected-uploads"
        ? await readProtectedMediaExport({
          mediaUrl: route.url,
          apiBaseUrl,
          token,
          fetcher,
          signal,
        })
        : await readAnonymousMediaExport({
          mediaUrl: route.url,
          apiBaseUrl,
          fetcher,
          signal,
        });
      requireCurrent(signal);
      return media;
    },
  });
}
