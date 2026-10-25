import type { Chapter, StoryboardFrame } from "../../../shared/api/contracts";
import type { PersonalRoughCutSnapshot } from "../../../shared/api/personalRoughCut";
import {
  MediaExportSinkError,
  type MediaExportFailureStage,
  type MediaExportItemState,
  type MediaExportQueue,
  type MediaExportQueueItem,
  type MediaExportSink,
} from "../../../shared/api/mediaExport";
import { MediaExportReaderError } from "../../../shared/api/mediaExportReader";
import type { MediaExportSessionTransport } from "../../../shared/api/mediaExportSession";

const FILE_EXTENSION = /\.(mp4|webm|mov|avi|mkv|m4v|ogv)$/iu;

interface QueueBaseInput {
  readonly runId: string;
  readonly chapter: Chapter;
  readonly seriesName: string;
}

export interface BuildChapterMediaExportQueueInput extends QueueBaseInput {
  readonly selectedFrameIndexes: readonly number[];
  readonly sourceRevision: string | null;
}

export interface BuildSavedRoughCutMediaExportQueueInput extends QueueBaseInput {
  readonly snapshot: PersonalRoughCutSnapshot;
}

export interface StartMediaExportOptions {
  readonly transport: MediaExportSessionTransport;
  readonly sink: MediaExportSink;
  readonly signal?: AbortSignal;
  readonly onItem?: (item: MediaExportQueueItem) => void;
  readonly onUnauthorized?: (error: MediaExportReaderError) => void;
}

export interface RetryFailedMediaExportOptions {
  readonly itemIds: readonly string[];
  readonly outputDirectoryId: string;
  readonly confirmedNewTarget: true;
}

export interface MediaExportRun {
  readonly done: Promise<MediaExportQueue>;
  stopAfterCurrent(): void;
  abort(): void;
}

function stableAssetId(frame: StoryboardFrame): string | null {
  const storyboard = frame.storyboard;
  const value = Array.isArray(storyboard) ? storyboard[0] : undefined;
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function videoExtension(url: string | null): string {
  if (url === null) {
    return "mp4";
  }
  try {
    const parsed = new URL(url, "https://media-export.invalid");
    const extension = parsed.pathname.match(FILE_EXTENSION)?.[1]?.toLowerCase();
    return extension ?? "mp4";
  } catch {
    return "mp4";
  }
}

function safeNamePart(value: string, fallback: string): string {
  const sanitized = Array.from(value
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/gu, "_")
    .replace(/_{2,}/gu, "_")
    .trim())
    .slice(0, 60)
    .join("")
    .replace(/^_+|_+$/gu, "");
  return sanitized || fallback;
}

function uniqueFilename(filename: string, used: Set<string>): string {
  if (!used.has(filename)) {
    used.add(filename);
    return filename;
  }
  const extensionMatch = filename.match(/\.([a-z\d]+)$/iu);
  const extension = extensionMatch?.[0] ?? "";
  const base = extension === "" ? filename : filename.slice(0, -extension.length);
  let suffix = 2;
  let candidate = "";
  do {
    candidate = base + " (" + suffix + ")" + extension;
    suffix += 1;
  } while (used.has(candidate));
  used.add(candidate);
  return candidate;
}

function freezeQueue(
  input: QueueBaseInput,
  sourceKind: MediaExportQueue["sourceKind"],
  sourceRevision: string | number | null,
  items: MediaExportQueueItem[],
): MediaExportQueue {
  const frozenItems = items.map((item) => Object.freeze({ ...item }));
  return Object.freeze({
    runId: input.runId,
    outputDirectoryId: input.runId,
    sourceKind,
    chapterId: input.chapter.id,
    seriesId: input.chapter.series_id,
    chapterName: input.chapter.title,
    seriesName: input.seriesName,
    sourceRevision,
    items: Object.freeze(frozenItems),
  });
}

function itemBase(
  input: QueueBaseInput,
  sourceKind: MediaExportQueue["sourceKind"],
  sourceIndex: number,
  frameIndex: number,
  assetId: string | null,
  sourceUrl: string | null,
  filename: string,
  eligibility: MediaExportQueueItem["eligibility"],
): MediaExportQueueItem {
  return {
    id: input.runId + ":" + sourceKind + ":" + sourceIndex,
    sourceKind,
    chapterId: input.chapter.id,
    sourceIndex,
    frameIndex,
    assetId,
    sourceUrl,
    filename,
    eligibility,
    state: "queued",
    message: null,
  };
}

function countIdentities(ids: readonly (string | null)[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const id of ids) {
    if (id !== null) {
      counts.set(id, (counts.get(id) ?? 0) + 1);
    }
  }
  return counts;
}

function validSourceUrl(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function buildChapterMediaExportQueue(
  input: BuildChapterMediaExportQueueInput,
): MediaExportQueue {
  if (input.runId.trim() === "") {
    throw new TypeError("媒体导出必须有独立的运行身份。");
  }
  const frames = input.chapter.content ?? [];
  const selected = new Set(input.selectedFrameIndexes.filter((index) => (
    Number.isSafeInteger(index) && index >= 0 && index < frames.length
  )));
  const ids = frames.map(stableAssetId);
  const identityCounts = countIdentities(ids);
  const safeChapter = safeNamePart(input.chapter.title, "本章");
  const safeSeries = safeNamePart(input.seriesName, "剧集");
  const usedFilenames = new Set<string>();
  const items = frames.map((frame, index) => {
    const assetId = stableAssetId(frame);
    const sourceUrl = validSourceUrl(frame.preview);
    let eligibility: MediaExportQueueItem["eligibility"];
    if (!selected.has(index)) {
      eligibility = "excluded";
    } else if (assetId !== null && identityCounts.get(assetId) !== 1) {
      eligibility = "duplicate";
    } else if (sourceUrl === null) {
      eligibility = "missing";
    } else {
      eligibility = "ready";
    }
    const filename = uniqueFilename(
      String(index + 1) + "_" + safeChapter + "_" + safeSeries + "." + videoExtension(sourceUrl),
      usedFilenames,
    );
    return itemBase(input, "chapter", index, index, assetId, sourceUrl, filename, eligibility);
  });
  return freezeQueue(input, "chapter", input.sourceRevision, items);
}

export function buildSavedRoughCutMediaExportQueue(
  input: BuildSavedRoughCutMediaExportQueueInput,
): MediaExportQueue {
  if (input.runId.trim() === "") {
    throw new TypeError("媒体导出必须有独立的运行身份。");
  }
  if (!input.snapshot.saved || input.snapshot.chapter_id !== input.chapter.id) {
    throw new TypeError("只能从当前章节已保存的粗剪创建导出队列。");
  }
  if (!Number.isSafeInteger(input.snapshot.revision) || input.snapshot.revision < 1) {
    throw new TypeError("已保存粗剪缺少可用的版本。");
  }
  const identityCounts = countIdentities(input.snapshot.frames.map((frame) => (
    typeof frame.asset_id === "string" && frame.asset_id.trim() !== "" ? frame.asset_id : null
  )));
  const safeChapter = safeNamePart(input.chapter.title, "本章");
  const safeSeries = safeNamePart(input.seriesName, "剧集");
  const usedFilenames = new Set<string>();
  let exportOrdinal = 0;
  const items = input.snapshot.frames.map((frame, index) => {
    const assetId = typeof frame.asset_id === "string" && frame.asset_id.trim() !== ""
      ? frame.asset_id
      : null;
    const sourceUrl = validSourceUrl(frame.preview_url);
    const validFrameIndex = Number.isSafeInteger(frame.frame_index) && frame.frame_index >= 0;
    let eligibility: MediaExportQueueItem["eligibility"];
    if (!frame.included) {
      eligibility = "excluded";
    } else if (frame.pending) {
      eligibility = "pending";
    } else if (assetId === null || !validFrameIndex) {
      eligibility = "invalid";
    } else if (identityCounts.get(assetId) !== 1) {
      eligibility = "duplicate";
    } else if (sourceUrl === null) {
      eligibility = "missing";
    } else {
      eligibility = "ready";
    }
    const filenameOrder = eligibility === "ready" ? ++exportOrdinal : exportOrdinal + 1;
    const projectedFilename =
      "粗剪" + String(filenameOrder).padStart(3, "0")
        + "_分镜" + String(validFrameIndex ? frame.frame_index + 1 : index + 1).padStart(3, "0")
        + "_" + safeChapter + "_" + safeSeries + "." + videoExtension(sourceUrl);
    const filename = eligibility === "ready"
      ? uniqueFilename(projectedFilename, usedFilenames)
      : projectedFilename;
    return itemBase(
      input,
      "rough-cut",
      index,
      validFrameIndex ? frame.frame_index : index,
      assetId,
      sourceUrl,
      filename,
      eligibility,
    );
  });
  return freezeQueue(input, "rough-cut", input.snapshot.revision, items);
}

export function createFailedMediaExportRetryQueue(
  previousResult: MediaExportQueue,
  options: RetryFailedMediaExportOptions,
): MediaExportQueue {
  const previousDirectoryId = (previousResult.outputDirectoryId ?? previousResult.runId)
    .replace(/[^a-zA-Z0-9_-]/gu, "_")
    .slice(0, 80);
  if (options.confirmedNewTarget !== true) {
    throw new TypeError("重试必须明确确认使用新的目标目录。");
  }
  if (
    !/^[a-zA-Z0-9_-]{1,80}$/u.test(options.outputDirectoryId)
    || options.outputDirectoryId === previousDirectoryId
  ) {
    throw new TypeError("重试必须使用新的导出目标目录。");
  }
  const selectedIds = new Set(options.itemIds);
  if (selectedIds.size === 0 || selectedIds.size !== options.itemIds.length) {
    throw new TypeError("请明确选择要重试的失败文件。");
  }
  if (previousResult.items.some((item) => (
    item.state === "queued"
    || item.state === "resolving"
    || item.state === "reading"
    || item.state === "writing"
  ))) {
    throw new TypeError("当前导出尚未结束，不能启动重试。");
  }

  for (const itemId of selectedIds) {
    const matches = previousResult.items.filter((item) => item.id === itemId);
    const item = matches[0];
    if (matches.length !== 1 || item?.eligibility !== "ready" || item.state !== "failed") {
      throw new TypeError("只能选择本次导出中已知失败的文件重试。");
    }
  }

  const items = previousResult.items.map((item) => Object.freeze(
    selectedIds.has(item.id)
      ? cloneItem(item, "queued", null)
      : { ...item },
  ));
  return Object.freeze({
    ...previousResult,
    outputDirectoryId: options.outputDirectoryId,
    items: Object.freeze(items),
  });
}

function failureMessage(error: unknown): string {
  if (error instanceof MediaExportReaderError) {
    return error.message;
  }
  if (error instanceof MediaExportSinkError) {
    return error.message;
  }
  return "此文件导出失败；确认来源和文件状态后可手动重试。";
}

function failureStage(
  error: unknown,
  fallback: "resolve" | "read" | "write",
): MediaExportFailureStage {
  return error instanceof MediaExportSinkError ? error.stage : fallback;
}

function cloneItem(
  item: MediaExportQueueItem,
  state: MediaExportItemState,
  message: string | null,
  filename = item.filename,
  failedAt?: MediaExportFailureStage,
): MediaExportQueueItem {
  const { failureStage: previousFailureStage, ...base } = item;
  const persistedFailureStage = failedAt ?? (
    state === "failed" || state === "unknown" ? previousFailureStage : undefined
  );
  return {
    ...base,
    ...(persistedFailureStage === undefined ? {} : { failureStage: persistedFailureStage }),
    filename,
    state,
    message,
  };
}

export function startMediaExport(
  queue: MediaExportQueue,
  options: StartMediaExportOptions,
): MediaExportRun {
  const controller = new AbortController();
  let stopRequested = false;
  let currentIndex = -1;
  let unauthorizedReported = false;
  const results = queue.items.map((item) => (
    item.state === "queued" && item.eligibility !== "ready"
      ? cloneItem(item, "not-started", item.message)
      : { ...item }
  ));
  const externalSignal = options.signal;
  const abortFromOutside = () => controller.abort();
  externalSignal?.addEventListener("abort", abortFromOutside, { once: true });
  if (externalSignal?.aborted) {
    controller.abort();
  }

  const emit = (
    index: number,
    state: MediaExportItemState,
    message: string | null = null,
    filename?: string,
    failedAt?: MediaExportFailureStage,
  ) => {
    const current = results[index];
    if (current === undefined) {
      return;
    }
    const updated = cloneItem(current, state, message, filename ?? current.filename, failedAt);
    results[index] = updated;
    if (options.transport.isCurrent()) {
      options.onItem?.(updated);
    }
  };

  const reportCurrentUnauthorized = (error: unknown): boolean => {
    if (
      unauthorizedReported
      || controller.signal.aborted
      || !options.transport.isCurrent()
      || !(error instanceof MediaExportReaderError)
      || error.kind !== "http"
      || error.status !== 401
      || error.source !== "application"
    ) {
      return false;
    }
    unauthorizedReported = true;
    options.onUnauthorized?.(error);
    return true;
  };

  const markQueuedReady = (state: MediaExportItemState, message: string) => {
    for (let index = 0; index < queue.items.length; index += 1) {
      const item = queue.items[index];
      if (item?.eligibility === "ready" && item.state === "queued") {
        emit(index, state, message);
      }
    }
  };

  const done = (async (): Promise<MediaExportQueue> => {
    try {
      if (!options.transport.isCurrent() || controller.signal.aborted) {
        markQueuedReady("stopped", "当前页面身份已变化，未启动导出。");
        return { ...queue, items: results };
      }
      const candidateIndexes = queue.items.flatMap((item, index) => (
        item.eligibility === "ready" && item.state === "queued" && item.sourceUrl !== null ? [index] : []
      ));
      const safeCandidateIndexes: number[] = [];
      const candidates = candidateIndexes.flatMap((index) => {
        const item = queue.items[index];
        if (item?.sourceUrl === null || item === undefined) {
          return [];
        }
        try {
          const route = options.transport.classifyUrl(item.sourceUrl);
          safeCandidateIndexes.push(index);
          return [{ url: route.url, filename: item.filename }];
        } catch {
          emit(index, "failed", "此视频来源地址无效，未发送下载地址解析请求。", undefined, "resolve");
          return [];
        }
      });
      for (const index of safeCandidateIndexes) {
        emit(index, "resolving");
      }
      if (controller.signal.aborted || !options.transport.isCurrent()) {
        controller.abort();
        for (const index of safeCandidateIndexes) {
          emit(index, "stopped", "当前页面身份已变化，未启动下载地址解析。");
        }
        return { ...queue, items: results };
      }
      let resolutions;
      try {
        resolutions = await options.transport.resolveDownloadLinks(candidates, controller.signal);
      } catch (error) {
        const unauthorized = reportCurrentUnauthorized(error);
        for (const index of safeCandidateIndexes) {
          emit(
            index,
            unauthorized || (!controller.signal.aborted && options.transport.isCurrent()) ? "failed" : "stopped",
            unauthorized || (!controller.signal.aborted && options.transport.isCurrent())
              ? failureMessage(error)
              : "用户已停止或当前页面已离开。",
            undefined,
            unauthorized || (!controller.signal.aborted && options.transport.isCurrent())
              ? failureStage(error, "resolve")
              : undefined,
          );
        }
        return { ...queue, items: results };
      }
      if (resolutions.length !== safeCandidateIndexes.length) {
        for (const index of safeCandidateIndexes) {
          emit(index, "failed", "下载地址解析回执与队列数量不匹配。", undefined, "resolve");
        }
        return { ...queue, items: results };
      }
      const usedFinalFilenames = new Set(
        queue.items
          .filter((item) => item.state !== "queued")
          .map((item) => item.filename),
      );
      for (let candidatePosition = 0; candidatePosition < safeCandidateIndexes.length; candidatePosition += 1) {
        const index = safeCandidateIndexes[candidatePosition];
        const original = queue.items[index ?? -1];
        const resolved = resolutions[candidatePosition];
        if (index === undefined || original === undefined || resolved === undefined) {
          continue;
        }
        if (stopRequested) {
          for (let rest = candidatePosition; rest < safeCandidateIndexes.length; rest += 1) {
            const stoppedIndex = safeCandidateIndexes[rest];
            if (stoppedIndex !== undefined) {
              emit(stoppedIndex, "stopped", "用户已停止剩余文件。");
            }
          }
          break;
        }
        if (controller.signal.aborted || !options.transport.isCurrent()) {
          emit(index, "stopped", "当前页面身份已变化，未写入此文件。");
          controller.abort();
          continue;
        }
        currentIndex = index;
        emit(index, "reading");
        if (controller.signal.aborted || !options.transport.isCurrent()) {
          emit(index, "stopped", "当前页面身份已变化，未读取此文件。");
          controller.abort();
          continue;
        }
        let currentStage: "read" | "write" = "read";
        try {
          const media = await options.transport.readMedia(resolved.url, controller.signal);
          if (controller.signal.aborted || !options.transport.isCurrent()) {
            emit(index, "stopped", "当前页面身份已变化，未写入此文件。");
            controller.abort();
            continue;
          }
          const finalFilename = uniqueFilename(
            filenameForMedia(original.filename, media.extension),
            usedFinalFilenames,
          );
          emit(index, "writing", null, finalFilename);
          currentStage = "write";
          if (controller.signal.aborted || !options.transport.isCurrent()) {
            emit(index, "stopped", "当前页面身份已变化，未写入此文件。", finalFilename);
            controller.abort();
            continue;
          }
          const result = await options.sink.save(
            { ...results[index]!, filename: finalFilename },
            media,
            { signal: controller.signal, isCurrent: options.transport.isCurrent },
          );
          emit(index, result.kind === "saved" ? "saved" : "download-started", null, finalFilename);
        } catch (error) {
          const unauthorized = reportCurrentUnauthorized(error);
          const failureAt = failureStage(error, currentStage);
          const state = error instanceof MediaExportSinkError && error.kind === "unknown"
            ? "unknown"
            : controller.signal.aborted || !options.transport.isCurrent()
              ? "stopped"
              : "failed";
          emit(
            index,
            state,
            failureMessage(error),
            undefined,
            state === "failed" || state === "unknown" ? failureAt : undefined,
          );
          if (unauthorized || controller.signal.aborted || !options.transport.isCurrent()) {
            controller.abort();
          }
        }
      }
      for (let candidatePosition = 0; candidatePosition < safeCandidateIndexes.length; candidatePosition += 1) {
        const index = safeCandidateIndexes[candidatePosition];
        const item = index === undefined ? undefined : results[index];
        if (index !== undefined && item?.state === "queued") {
          emit(index, "stopped", "剩余文件未启动。");
        }
      }
      return { ...queue, items: results };
    } finally {
      externalSignal?.removeEventListener("abort", abortFromOutside);
    }
  })();

  return {
    done,
    stopAfterCurrent() {
      stopRequested = true;
      if (currentIndex < 0) {
        controller.abort();
      }
    },
    abort() {
      controller.abort();
    },
  };
}

function filenameForMedia(filename: string, extension: string): string {
  const current = filename.match(/\.[a-z\d]+$/iu)?.[0] ?? "";
  const base = current === "" ? filename : filename.slice(0, -current.length);
  return base + "." + extension;
}
