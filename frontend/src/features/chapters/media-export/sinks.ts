import {
  MediaExportSinkError,
  type MediaExportMedia,
  type MediaExportQueueItem,
  type MediaExportSink,
  type MediaExportSinkSaveContext,
  type MediaExportSaveResult,
} from "../../../shared/api/mediaExport";

interface WritableFileLike {
  write(data: Blob): Promise<void>;
  close(): Promise<void>;
  abort?(reason?: unknown): Promise<void>;
}

interface FileHandleLike {
  createWritable(options?: { readonly keepExistingData?: boolean }): Promise<WritableFileLike>;
}

export interface DirectoryHandleLike {
  getDirectoryHandle(
    name: string,
    options?: { readonly create?: boolean },
  ): Promise<DirectoryHandleLike>;
  getFileHandle(
    name: string,
    options?: { readonly create?: boolean },
  ): Promise<FileHandleLike>;
}

export interface CreateDirectoryMediaExportSinkOptions {
  readonly runId: string;
  readonly pickDirectory: () => Promise<DirectoryHandleLike>;
  readonly context?: MediaExportSinkSaveContext;
}

function isNotFound(error: unknown): boolean {
  return error instanceof DOMException && error.name === "NotFoundError";
}

function safeRunDirectoryName(runId: string): string {
  const safeId = runId.replace(/[^a-zA-Z0-9_-]/gu, "_").slice(0, 80);
  if (safeId === "") {
    throw new MediaExportSinkError("failed", "directory", "导出运行身份无效。");
  }
  return "media-export-" + safeId;
}

function isSafeFilename(filename: string): boolean {
  return filename !== ""
    && filename !== "."
    && filename !== ".."
    && !filename.includes("/")
    && !filename.includes("\\")
    && !/[\u0000-\u001f\u007f]/u.test(filename);
}

function isSaveContextCurrent(context?: MediaExportSinkSaveContext): boolean {
  return context === undefined || (!context.signal.aborted && context.isCurrent());
}

function requireSaveContextCurrent(
  context: MediaExportSinkSaveContext | undefined,
  stage: "write" | "download",
): void {
  if (!isSaveContextCurrent(context)) {
    throw new MediaExportSinkError("failed", stage, "当前页面身份已变化，已停止写入文件。");
  }
}

function requireDirectoryPreparationCurrent(context: MediaExportSinkSaveContext | undefined): void {
  if (!isSaveContextCurrent(context)) {
    throw new MediaExportSinkError("failed", "directory", "当前页面身份已变化，已停止准备目标目录。");
  }
}

function directoryCreationOutcomeUnknown(): MediaExportSinkError {
  return new MediaExportSinkError(
    "unknown",
    "directory",
    "页面身份变化时已发出导出目录创建请求；目录结果无法确认，本次导出已停止。",
  );
}

/** Call from a user gesture after directory permission has been requested. */
export async function createDirectoryMediaExportSink(
  options: CreateDirectoryMediaExportSinkOptions,
): Promise<MediaExportSink> {
  requireDirectoryPreparationCurrent(options.context);
  let parent: DirectoryHandleLike;
  try {
    parent = await options.pickDirectory();
  } catch (error) {
    throw new MediaExportSinkError(
      "failed",
      "permission",
      error instanceof DOMException && error.name === "AbortError"
        ? "用户取消了目录选择。"
        : "无法取得目标目录权限。",
    );
  }
  requireDirectoryPreparationCurrent(options.context);

  const runDirectoryName = safeRunDirectoryName(options.runId);
  try {
    requireDirectoryPreparationCurrent(options.context);
    let existing: DirectoryHandleLike | null = null;
    try {
      existing = await parent.getDirectoryHandle(runDirectoryName);
    } catch (error) {
      requireDirectoryPreparationCurrent(options.context);
      if (!isNotFound(error)) {
        throw new MediaExportSinkError("failed", "directory", "无法检查本次导出目录状态。");
      }
    }
    requireDirectoryPreparationCurrent(options.context);
    if (existing !== null) {
      throw new MediaExportSinkError(
        "failed",
        "conflict",
        "本次导出目录已存在；为避免覆盖，请新建一次导出运行。",
      );
    }
    requireDirectoryPreparationCurrent(options.context);
    let runDirectory: DirectoryHandleLike;
    try {
      runDirectory = await parent.getDirectoryHandle(runDirectoryName, { create: true });
    } catch (error) {
      if (!isSaveContextCurrent(options.context)) {
        throw directoryCreationOutcomeUnknown();
      }
      throw error;
    }
    if (!isSaveContextCurrent(options.context)) {
      throw directoryCreationOutcomeUnknown();
    }
    return {
      async save(
        item: MediaExportQueueItem,
        media: MediaExportMedia,
        context?: MediaExportSinkSaveContext,
      ): Promise<MediaExportSaveResult> {
        if (!isSafeFilename(item.filename)) {
          throw new MediaExportSinkError("failed", "write", "目标文件名不安全。");
        }
        requireSaveContextCurrent(context, "write");
        try {
          await runDirectory.getFileHandle(item.filename);
          throw new MediaExportSinkError(
            "failed",
            "conflict",
            "目标文件已存在；没有覆盖现有文件。",
          );
        } catch (error) {
          if (error instanceof MediaExportSinkError) {
            throw error;
          }
          if (!isNotFound(error)) {
            throw new MediaExportSinkError("failed", "write", "无法检查目标文件状态。");
          }
        }
        requireSaveContextCurrent(context, "write");

        let file: FileHandleLike;
        try {
          requireSaveContextCurrent(context, "write");
          file = await runDirectory.getFileHandle(item.filename, { create: true });
        } catch {
          throw new MediaExportSinkError("failed", "write", "无法创建目标文件。");
        }
        if (!isSaveContextCurrent(context)) {
          throw new MediaExportSinkError(
            "unknown",
            "write",
            "页面身份变化时目标文件句柄已创建，结果未知；不会自动覆盖或重试。",
          );
        }

        let writable: WritableFileLike;
        try {
          requireSaveContextCurrent(context, "write");
          writable = await file.createWritable({ keepExistingData: false });
        } catch {
          throw new MediaExportSinkError("failed", "write", "无法打开目标文件写入器。");
        }
        let abortPromise: Promise<void> | null = null;
        const abortWriter = (): Promise<void> => {
          if (abortPromise === null) {
            abortPromise = (async () => {
              try {
                await writable.abort?.();
              } catch {
                // Aborting is best effort; the result remains unknown after writing begins.
              }
            })();
          }
          return abortPromise;
        };
        const abortOnSignal = () => {
          void abortWriter();
        };
        context?.signal.addEventListener("abort", abortOnSignal, { once: true });
        try {
          if (!isSaveContextCurrent(context)) {
            await abortWriter();
            throw new MediaExportSinkError(
              "unknown",
              "write",
              "页面身份变化时写入器已创建，结果未知；不会自动覆盖或重试。",
            );
          }
          try {
            await writable.write(media.blob);
          } catch {
            await abortWriter();
            throw new MediaExportSinkError(
              "unknown",
              "write",
              "写入未能确认完成；文件结果未知，系统不会自动覆盖或重试。",
            );
          }
          if (!isSaveContextCurrent(context)) {
            await abortWriter();
            throw new MediaExportSinkError(
              "unknown",
              "write",
              "页面身份变化时写入未关闭，结果未知；不会自动覆盖或重试。",
            );
          }
          try {
            await writable.close();
          } catch {
            await abortWriter();
            throw new MediaExportSinkError(
              "unknown",
              "close",
              "关闭未能确认完成；文件结果未知，系统不会自动覆盖或重试。",
            );
          }
          return { kind: "saved" };
        } catch (error) {
          if (error instanceof MediaExportSinkError) {
            throw error;
          }
          throw new MediaExportSinkError(
            "unknown",
            "close",
            "关闭未能确认完成；文件结果未知，系统不会自动覆盖或重试。",
          );
        } finally {
          context?.signal.removeEventListener("abort", abortOnSignal);
        }
      },
    };
  } catch (error) {
    if (error instanceof MediaExportSinkError) {
      throw error;
    }
    throw new MediaExportSinkError("failed", "directory", "无法创建本次独立导出目录。");
  }
}

export interface BrowserAnchorLike {
  href: string;
  download: string;
  click(): void;
  remove(): void;
}

export interface CreateBrowserMediaExportSinkOptions {
  readonly createAnchor?: () => BrowserAnchorLike;
  readonly appendAnchor?: (anchor: BrowserAnchorLike) => void;
  readonly createObjectURL?: (blob: Blob) => string;
  readonly revokeObjectURL?: (url: string) => void;
  readonly scheduleRevoke?: (callback: () => void) => unknown;
}

function defaultAnchor(): BrowserAnchorLike {
  const anchor = document.createElement("a");
  anchor.style.display = "none";
  return anchor;
}

/** This fallback only confirms that the browser download was initiated. */
export function createBrowserMediaExportSink(
  options: CreateBrowserMediaExportSinkOptions = {},
): MediaExportSink {
  const createAnchor = options.createAnchor ?? defaultAnchor;
  const appendAnchor = options.appendAnchor ?? ((anchor: BrowserAnchorLike) => {
    document.body.appendChild(anchor as HTMLAnchorElement);
  });
  const createObjectURL = options.createObjectURL ?? ((blob: Blob) => URL.createObjectURL(blob));
  const revokeObjectURL = options.revokeObjectURL ?? ((url: string) => URL.revokeObjectURL(url));
  const scheduleRevoke = options.scheduleRevoke ?? ((callback: () => void) => setTimeout(callback, 1000));

  return {
    async save(
      item: MediaExportQueueItem,
      media: MediaExportMedia,
      context?: MediaExportSinkSaveContext,
    ): Promise<MediaExportSaveResult> {
      if (!isSafeFilename(item.filename)) {
        throw new MediaExportSinkError("failed", "download", "目标文件名不安全。");
      }
      requireSaveContextCurrent(context, "download");
      let objectUrl: string | null = null;
      let anchor: BrowserAnchorLike | null = null;
      try {
        objectUrl = createObjectURL(media.blob);
        requireSaveContextCurrent(context, "download");
        anchor = createAnchor();
        anchor.href = objectUrl;
        anchor.download = item.filename;
        appendAnchor(anchor);
        requireSaveContextCurrent(context, "download");
        anchor.click();
        scheduleRevoke(() => {
          if (objectUrl !== null) {
            revokeObjectURL(objectUrl);
          }
          anchor?.remove();
        });
        return { kind: "download-started" };
      } catch {
        if (objectUrl !== null) {
          revokeObjectURL(objectUrl);
        }
        anchor?.remove();
        throw new MediaExportSinkError("failed", "download", "浏览器未能启动此文件下载。");
      }
    },
  };
}
