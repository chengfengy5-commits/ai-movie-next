import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../../shared/api/errors";
import { classifyMediaExportUrl } from "../../../shared/api/mediaExportReader";
import {
  ChapterGenerationHistoryError,
  type ChapterGenerationHistoryAdoptInput,
  type ChapterGenerationHistoryContext,
  type ChapterGenerationHistoryLockIdentity,
  type ChapterGenerationHistoryMessage,
  type ChapterGenerationHistoryMode,
  type ChapterGenerationHistoryPort,
  type ChapterGenerationHistoryRun,
  type ChapterGenerationHistorySnapshot,
  type ChapterGenerationHistoryTargetKind,
} from "../../../shared/api/chapterGenerationHistory";
import type {
  ChapterGenerationHistoryConfirmation,
  ChapterGenerationHistoryEntry,
  ChapterGenerationHistoryLoadState,
  ChapterGenerationHistoryNotice,
} from "./ChapterGenerationHistoryDialog";

const HISTORY_PAGE_SIZE = 10;
const VIDEO_EXTENSIONS = new Set(["mp4", "webm", "mov", "avi", "mkv", "m4v", "ogv"]);
const IMAGE_EXTENSIONS = new Set(["jpg", "jpeg", "png", "gif", "webp", "bmp"]);

export interface ChapterGenerationHistoryWriteLease {
  readonly identity: ChapterGenerationHistoryLockIdentity | null;
  release(): Promise<void>;
}

export interface ChapterGenerationHistoryOwner {
  readonly ownerKey: string;
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly chapterTitle: string;
  readonly frameIndex: number;
  readonly storyboardAssetId: string | null;
  readonly apiBaseUrl: string | null;
  readonly sessionGeneration: number;
  readonly requestGeneration: number;
  readonly port: ChapterGenerationHistoryPort | undefined;
  readonly isOwnerCurrent: () => boolean;
  readonly isAdoptionAllowed: () => boolean;
  readonly adoptionBlockMessage: () => string | null;
  prepareWrite(signal: AbortSignal, isCurrent: () => boolean): Promise<ChapterGenerationHistoryWriteLease>;
  refreshAfterAdoption(
    signal: AbortSignal,
    isCurrent: () => boolean,
    verifiedSnapshot: ChapterGenerationHistorySnapshot,
  ): Promise<void>;
  readonly onUnauthorized: () => void;
}

interface HistoryReady {
  readonly status: "ready";
  readonly snapshot: ChapterGenerationHistorySnapshot;
  readonly entries: readonly ChapterGenerationHistoryEntry[];
  readonly run: ChapterGenerationHistoryRun;
}

type HistoryData = { readonly status: "loading" }
  | { readonly status: "error"; readonly message: string }
  | HistoryReady;

interface PendingCandidate {
  readonly messageId: string;
  readonly messageDigest: string;
  readonly candidateUrl: string;
  readonly baseline: ChapterGenerationHistorySnapshot;
}

type PendingHistoryAction =
  | {
    readonly kind: "transfer";
    readonly run: ChapterGenerationHistoryRun;
    readonly message: ChapterGenerationHistoryMessage;
    readonly baseline: ChapterGenerationHistorySnapshot;
    readonly transferredUrl: null;
    readonly acknowledged: false;
  }
  | {
    readonly kind: "transfer-result";
    readonly run: ChapterGenerationHistoryRun;
    readonly message: ChapterGenerationHistoryMessage;
    readonly baseline: ChapterGenerationHistorySnapshot;
    readonly transferredUrl: string;
    readonly acknowledged: true;
  }
  | {
    readonly kind: "message-update";
    readonly run: ChapterGenerationHistoryRun;
    readonly message: ChapterGenerationHistoryMessage;
    readonly baseline: ChapterGenerationHistorySnapshot;
    readonly transferredUrl: string;
    readonly acknowledged: boolean;
  }
  | {
    readonly kind: "delete";
    readonly run: ChapterGenerationHistoryRun;
    readonly message: ChapterGenerationHistoryMessage;
    readonly baseline: ChapterGenerationHistorySnapshot;
    readonly acknowledged: boolean;
  };

function errorText(error: unknown, fallback: string): string {
  if (error instanceof ChapterGenerationHistoryError) return error.message || fallback;
  if (error instanceof ApiError) return error.detail ?? error.message ?? fallback;
  return error instanceof Error ? error.message : fallback;
}

function isUnauthorized(error: unknown): boolean {
  return error instanceof ChapterGenerationHistoryError
    ? error.kind === "unauthorized"
    : error instanceof ApiError && error.status === 401;
}

function historyNotice(error: unknown): ChapterGenerationHistoryNotice {
  if (error instanceof ChapterGenerationHistoryError && error.kind === "unknown") {
    return { kind: "unknown", message: error.message, canVerify: true };
  }
  return { kind: "error", message: errorText(error, "无法处理生成历史。"), canVerify: false };
}

function safeMediaEntry(
  message: ChapterGenerationHistoryMessage,
  snapshot: ChapterGenerationHistorySnapshot,
  mode: ChapterGenerationHistoryMode,
  apiBaseUrl: string | null,
): ChapterGenerationHistoryEntry {
  if (message.role !== "assistant" || !/^(?:https?:\/\/|\/?uploads\/)/iu.test(message.content)) {
    return { message, mediaUrl: null, unsafeMedia: false, canAdopt: false };
  }
  if (apiBaseUrl === null) return { message, mediaUrl: null, unsafeMedia: true, canAdopt: false };
  try {
    const route = classifyMediaExportUrl(message.content, apiBaseUrl);
    if (route.kind === "anonymous-public" && new URL(route.url).protocol !== "https:") {
      return { message, mediaUrl: null, unsafeMedia: true, canAdopt: false };
    }
    const extension = new URL(route.url).pathname.split(".").at(-1)?.toLowerCase() ?? "";
    if (extension === "svg") return { message, mediaUrl: null, unsafeMedia: true, canAdopt: false };
    const extensionMode = VIDEO_EXTENSIONS.has(extension)
      ? "video"
      : IMAGE_EXTENSIONS.has(extension) ? "image" : mode;
    if (extensionMode !== mode) return { message, mediaUrl: null, unsafeMedia: true, canAdopt: false };
    return {
      message,
      mediaUrl: route.url,
      unsafeMedia: false,
      canAdopt: snapshot.writable && snapshot.target_proof !== null,
    };
  } catch {
    return { message, mediaUrl: null, unsafeMedia: true, canAdopt: false };
  }
}

function snapshotEntries(
  snapshot: ChapterGenerationHistorySnapshot,
  mode: ChapterGenerationHistoryMode,
  apiBaseUrl: string | null,
): readonly ChapterGenerationHistoryEntry[] {
  return [...snapshot.messages]
    .reverse()
    .map((message) => safeMediaEntry(message, snapshot, mode, apiBaseUrl));
}

function messageInSnapshot(
  snapshot: ChapterGenerationHistorySnapshot,
  messageId: string,
): ChapterGenerationHistoryMessage | null {
  return snapshot.messages.find((message) => message.id === messageId) ?? null;
}

function sameCandidateSnapshot(
  before: ChapterGenerationHistorySnapshot,
  after: ChapterGenerationHistorySnapshot,
  pending: PendingCandidate,
): boolean {
  const beforeMessage = messageInSnapshot(before, pending.messageId);
  const afterMessage = messageInSnapshot(after, pending.messageId);
  return before.writable
    && after.writable
    && before.target_proof !== null
    && after.target_proof !== null
    && before.series_id === after.series_id
    && before.chapter_id === after.chapter_id
    && before.target_kind === after.target_kind
    && before.storyboard_asset_id === after.storyboard_asset_id
    && before.frame_index === after.frame_index
    && before.chat_mode === after.chat_mode
    && before.target_proof.mapping_digest === after.target_proof.mapping_digest
    && before.target_proof.target_digest === after.target_proof.target_digest
    && before.target_proof.media_url === after.target_proof.media_url
    && beforeMessage?.message_digest === pending.messageDigest
    && afterMessage?.message_digest === pending.messageDigest
    && afterMessage.role === "assistant"
    && afterMessage.content === pending.candidateUrl;
}

function sameOperationTarget(
  expected: ChapterGenerationHistorySnapshot,
  observed: ChapterGenerationHistorySnapshot,
): boolean {
  return expected.writable
    && observed.writable
    && expected.target_proof !== null
    && observed.target_proof !== null
    && expected.series_id === observed.series_id
    && expected.chapter_id === observed.chapter_id
    && expected.target_kind === observed.target_kind
    && expected.storyboard_asset_id === observed.storyboard_asset_id
    && expected.frame_index === observed.frame_index
    && expected.chat_mode === observed.chat_mode
    && expected.target_proof.mapping_digest === observed.target_proof.mapping_digest;
}

function sameHistoryScope(
  expected: ChapterGenerationHistorySnapshot,
  observed: ChapterGenerationHistorySnapshot,
): boolean {
  return expected.series_id === observed.series_id
    && expected.chapter_id === observed.chapter_id
    && expected.target_kind === observed.target_kind
    && expected.storyboard_asset_id === observed.storyboard_asset_id
    && expected.frame_index === observed.frame_index
    && expected.chat_mode === observed.chat_mode;
}

function sameHistoryTarget(
  expected: ChapterGenerationHistorySnapshot,
  observed: ChapterGenerationHistorySnapshot,
): boolean {
  return sameOperationTarget(expected, observed)
    && expected.target_proof?.target_digest === observed.target_proof?.target_digest
    && expected.target_proof?.media_url === observed.target_proof?.media_url;
}

function sameMessageTarget(
  expected: ChapterGenerationHistoryMessage,
  observed: ChapterGenerationHistoryMessage | null | undefined,
): boolean {
  return observed != null
    && expected.id === observed.id
    && expected.chapter_id === observed.chapter_id
    && expected.frame_index === observed.frame_index
    && expected.asset_type === observed.asset_type
    && expected.asset_id === observed.asset_id
    && expected.chat_mode === observed.chat_mode
    && expected.role === observed.role;
}

function sameMessage(
  expected: ChapterGenerationHistoryMessage,
  observed: ChapterGenerationHistoryMessage | null | undefined,
): boolean {
  return sameMessageTarget(expected, observed)
    && expected.message_digest === observed?.message_digest
    && expected.content === observed?.content;
}

function messageHasContent(
  expected: ChapterGenerationHistoryMessage,
  observed: ChapterGenerationHistoryMessage | null | undefined,
  content: string,
): boolean {
  return sameMessageTarget(expected, observed) && observed?.content === content;
}

function safeFilenamePart(value: string, fallback: string): string {
  const safe = value
    .replace(/[<>:"/\\|?*\u0000-\u001f\u007f-\u009f]/gu, "_")
    .replace(/\s+/gu, " ")
    .trim()
    .replace(/[. ]+$/gu, "")
    .slice(0, 80);
  return safe === "" ? fallback : safe;
}

function historyDownloadFilename(
  chapterTitle: string,
  frameIndex: number,
  message: ChapterGenerationHistoryMessage,
  mode: ChapterGenerationHistoryMode,
  blob: Blob,
): string | null {
  const mimeType = blob.type.split(";", 1)[0]?.trim().toLowerCase() ?? "";
  const extensions = mode === "image"
    ? { "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp" }
    : { "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-msvideo": "avi", "video/x-matroska": "mkv", "video/x-m4v": "m4v", "video/ogg": "ogv" };
  const extension = extensions[mimeType as keyof typeof extensions];
  if (extension === undefined) return null;
  return `${safeFilenamePart(chapterTitle, "章节")}-镜头${frameIndex + 1}-${safeFilenamePart(message.id, "history")}.${extension}`;
}

function startHistoryBlobDownload(blob: Blob, filename: string, isCurrent: () => boolean): boolean {
  if (!isCurrent()) return false;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    try {
      URL.revokeObjectURL(objectUrl);
    } finally {
      anchor.remove();
    }
  };
  try {
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.rel = "noopener";
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    if (!isCurrent()) {
      release();
      return false;
    }
    anchor.click();
    globalThis.setTimeout(release, 1000);
    return true;
  } catch {
    release();
    throw new Error("浏览器未能启动此文件下载。");
  }
}

export function useChapterGenerationHistoryOwner(owner: ChapterGenerationHistoryOwner) {
  const [targetKind, setTargetKind] = useState<ChapterGenerationHistoryTargetKind>("frame-preview");
  const [mode, setMode] = useState<ChapterGenerationHistoryMode>("image");
  const [page, setPage] = useState(0);
  const [history, setHistory] = useState<HistoryData>({ status: "loading" });
  const historyRef = useRef<HistoryData>(history);
  const [confirmation, setConfirmation] = useState<ChapterGenerationHistoryConfirmation | null>(null);
  const confirmationRef = useRef<ChapterGenerationHistoryConfirmation | null>(confirmation);
  const [deleteConfirmationMessageId, setDeleteConfirmationMessageId] = useState<string | null>(null);
  const [notice, setNotice] = useState<ChapterGenerationHistoryNotice | null>(null);
  const noticeRef = useRef<ChapterGenerationHistoryNotice | null>(notice);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const pendingCandidateRef = useRef<PendingCandidate | null>(null);
  const pendingHistoryActionRef = useRef<PendingHistoryAction | null>(null);
  const requestSequence = useRef(0);
  const activeActionController = useRef<AbortController | null>(null);

  historyRef.current = history;
  confirmationRef.current = confirmation;
  noticeRef.current = notice;

  const assignHistory = useCallback((next: HistoryData) => {
    historyRef.current = next;
    setHistory(next);
  }, []);

  const assignConfirmation = useCallback((next: ChapterGenerationHistoryConfirmation | null) => {
    confirmationRef.current = next;
    setConfirmation(next);
  }, []);

  const assignNotice = useCallback((next: ChapterGenerationHistoryNotice | null) => {
    noticeRef.current = next;
    setNotice(next);
  }, []);

  const setBusyState = useCallback((next: boolean) => {
    busyRef.current = next;
    setBusy(next);
  }, []);

  function assignSnapshot(run: ChapterGenerationHistoryRun, snapshot: ChapterGenerationHistorySnapshot): void {
    const entries = snapshotEntries(snapshot, mode, owner.apiBaseUrl);
    const lastPage = Math.max(0, Math.ceil(entries.length / HISTORY_PAGE_SIZE) - 1);
    setPage((currentPage) => Math.min(currentPage, lastPage));
    assignHistory({ status: "ready", snapshot, entries, run });
  }

  function beginAction(
    current: HistoryReady,
    progressMessage: string,
  ): { readonly controller: AbortController; readonly isCurrent: () => boolean } | null {
    if (busyRef.current || noticeRef.current?.kind === "unknown"
      || !owner.isOwnerCurrent() || !current.run.isCurrent()) return null;
    const controller = new AbortController();
    activeActionController.current = controller;
    setBusyState(true);
    assignNotice({ kind: "progress", message: progressMessage, canVerify: false });
    const isCurrent = () => !controller.signal.aborted
      && owner.isOwnerCurrent()
      && current.run.isCurrent()
      && activeActionController.current === controller;
    return { controller, isCurrent };
  }

  async function reconcilePendingHistoryAction(
    pending: PendingHistoryAction,
    signal: AbortSignal,
    isCurrent: () => boolean,
  ): Promise<void> {
    try {
      const snapshot = await pending.run.readSnapshot(signal);
      if (!isCurrent()) return;
      assignSnapshot(pending.run, snapshot);
      const currentMessage = messageInSnapshot(snapshot, pending.message.id);
      if (!sameHistoryTarget(pending.baseline, snapshot)) {
        assignNotice({
          kind: "unknown",
          message: "目标映射已变化，无法确认这次操作结果；没有重复发送请求。",
          canVerify: true,
        });
        return;
      }

      if (pending.kind === "delete") {
        if (currentMessage === null) {
          pendingHistoryActionRef.current = null;
          setDeleteConfirmationMessageId(null);
          assignNotice({
            kind: "success",
            message: pending.acknowledged
              ? "删除已响应，独立读取确认这条历史消息已不存在。"
              : "当前历史消息已不存在；无法确认本次删除是否完成，没有重复删除。",
            canVerify: false,
          });
        } else {
          assignNotice({
            kind: "unknown",
            message: sameMessage(pending.message, currentMessage)
              ? "删除结果仍未确认，消息仍可见；不会自动再次删除。"
              : "消息内容已变化，无法确认删除结果；不会自动再次删除。",
            canVerify: true,
          });
        }
        return;
      }

      if (pending.kind === "message-update") {
        if (messageHasContent(pending.message, currentMessage, pending.transferredUrl)) {
          pendingHistoryActionRef.current = null;
          assignNotice({
            kind: "success",
            message: pending.acknowledged
              ? "转存后的地址已由独立读取确认写回原消息。"
              : "当前原消息已核对为转存地址；无法证明先前写回是否完成，没有重复写入。",
            canVerify: false,
          });
        } else {
          assignNotice({
            kind: "unknown",
            message: currentMessage === null
              ? "原消息已不存在，无法确认写回结果；不会自动再次写入。"
              : sameMessage(pending.message, currentMessage)
                ? "原消息仍保持原内容，PUT结果未知；不会自动再次写入。"
                : "原消息已有其他变化，PUT结果未知；不会自动再次写入。",
            canVerify: true,
          });
        }
        return;
      }

      if (pending.kind === "transfer-result") {
        if (pending.transferredUrl === pending.message.content && sameMessage(pending.message, currentMessage)) {
          pendingHistoryActionRef.current = null;
          assignNotice({
            kind: "success",
            message: "转存服务返回原地址，独立读取确认原消息保持不变。",
            canVerify: false,
          });
        } else {
          assignNotice({
            kind: "unknown",
            message: "转存地址已返回，但原消息当前状态无法核对；没有自动写回或重发转存。",
            canVerify: true,
          });
        }
        return;
      }

      assignNotice({
        kind: "unknown",
        message: currentMessage === null
          ? "转存请求结果未知，原消息已不存在；没有地址可写回，也不会重发。"
          : sameMessage(pending.message, currentMessage)
            ? "转存请求结果未知，原消息仍是原地址；无法核对转存后的地址，不会重发或写回。"
            : "转存请求结果未知且原消息已变化；不会重发转存或覆盖新内容。",
        canVerify: true,
      });
    } catch (error) {
      if (!isCurrent()) return;
      if (isUnauthorized(error)) owner.onUnauthorized();
      assignNotice({ kind: "unknown", message: errorText(error, "只读核对失败；不会重发写入请求。"), canVerify: true });
    }
  }

  useEffect(() => {
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    assignHistory({ status: "loading" });
    assignConfirmation(null);
    setDeleteConfirmationMessageId(null);
    pendingCandidateRef.current = null;
    pendingHistoryActionRef.current = null;
    assignNotice(null);
    setPage(0);
    if (owner.port === undefined) {
      assignHistory({ status: "error", message: "当前运行环境没有接入镜头生成历史服务。" });
      return () => controller.abort();
    }
    if (targetKind === "storyboard-image" && owner.storyboardAssetId === null) {
      assignHistory({ status: "error", message: "当前镜头没有稳定的故事板素材编号，无法读取故事板素材历史。" });
      return () => controller.abort();
    }

    const context: ChapterGenerationHistoryContext = {
      actorId: owner.actorId,
      seriesId: owner.seriesId,
      chapterId: owner.chapterId,
      targetKind,
      storyboardAssetId: owner.storyboardAssetId,
      frameIndex: owner.frameIndex,
      mode,
      isContextCurrent: () => requestSequence.current === sequence
        && !controller.signal.aborted
        && owner.isOwnerCurrent(),
    };
    let run: ChapterGenerationHistoryRun;
    try {
      run = owner.port.beginRun(context);
    } catch (error) {
      if (isUnauthorized(error) && owner.isOwnerCurrent()) owner.onUnauthorized();
      assignHistory({ status: "error", message: errorText(error, "无法打开生成历史。") });
      return () => controller.abort();
    }

    void run.readSnapshot(controller.signal).then((snapshot) => {
      if (controller.signal.aborted || requestSequence.current !== sequence || !run.isCurrent() || !owner.isOwnerCurrent()) return;
      assignHistory({ status: "ready", snapshot, entries: snapshotEntries(snapshot, mode, owner.apiBaseUrl), run });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || requestSequence.current !== sequence || !owner.isOwnerCurrent()) return;
      if (isUnauthorized(error)) owner.onUnauthorized();
      assignHistory({ status: "error", message: errorText(error, "无法读取生成历史。") });
    });

    return () => {
      controller.abort();
    };
  }, [assignConfirmation, assignHistory, assignNotice, mode, owner, targetKind]);

  useEffect(() => () => {
    requestSequence.current += 1;
    activeActionController.current?.abort();
  }, []);

  const currentRun = history.status === "ready" ? history.run : null;
  const isCurrent = useCallback((): boolean => owner.isOwnerCurrent()
    && currentRun !== null
    && currentRun.isCurrent(), [currentRun, owner]);

  const readMedia = useCallback((url: string, signal: AbortSignal): Promise<Blob> => {
    if (currentRun === null || !currentRun.isCurrent() || !owner.isOwnerCurrent()) {
      return Promise.reject(new ChapterGenerationHistoryError("read-error", "历史预览范围已变化。"));
    }
    return currentRun.readMedia(url, signal);
  }, [currentRun, owner]);

  async function downloadMessage(message: ChapterGenerationHistoryMessage): Promise<void> {
    const current = historyRef.current;
    if (current.status !== "ready" || message.role !== "assistant"
      || !sameMessage(message, messageInSnapshot(current.snapshot, message.id))) return;
    const initialEntry = current.entries.find((entry) => entry.message.id === message.id);
    if (initialEntry?.mediaUrl === null || initialEntry === undefined || initialEntry.unsafeMedia) return;
    const action = beginAction(current, "正在核对历史消息并读取媒体；尚未启动浏览器下载。");
    if (action === null) return;
    try {
      const fresh = await current.run.readSnapshot(action.controller.signal);
      if (!action.isCurrent()) return;
      const freshMessage = messageInSnapshot(fresh, message.id);
      if (!sameHistoryScope(current.snapshot, fresh) || !sameMessage(message, freshMessage)) {
        assignSnapshot(current.run, fresh);
        assignNotice({ kind: "error", message: "历史消息已变化，未启动下载。", canVerify: false });
        return;
      }
      assignSnapshot(current.run, fresh);
      const freshEntry = safeMediaEntry(freshMessage!, fresh, mode, owner.apiBaseUrl);
      if (freshEntry.mediaUrl === null || freshEntry.unsafeMedia) {
        assignNotice({ kind: "error", message: "媒体地址无法安全读取，未启动下载。", canVerify: false });
        return;
      }
      const blob = await current.run.readMedia(freshEntry.mediaUrl, action.controller.signal);
      if (!action.isCurrent()) return;
      const filename = historyDownloadFilename(owner.chapterTitle, owner.frameIndex, freshMessage!, mode, blob);
      if (filename === null) {
        assignNotice({ kind: "error", message: "读取成功，但媒体类型不受支持，未启动下载。", canVerify: false });
        return;
      }
      if (!startHistoryBlobDownload(blob, filename, action.isCurrent)) return;
      assignNotice({ kind: "success", message: "已交给浏览器下载；本地无法确认文件是否已写入磁盘。", canVerify: false });
    } catch (error) {
      if (!action.isCurrent()) return;
      if (isUnauthorized(error)) owner.onUnauthorized();
      assignNotice({ kind: "error", message: errorText(error, "媒体读取失败，未启动下载。"), canVerify: false });
    } finally {
      if (activeActionController.current === action.controller) activeActionController.current = null;
      if (owner.isOwnerCurrent()) setBusyState(false);
    }
  }

  function currentWritableMessage(message: ChapterGenerationHistoryMessage): HistoryReady | null {
    const current = historyRef.current;
    if (busyRef.current || noticeRef.current?.kind === "unknown") return null;
    if (current.status !== "ready" || !owner.isOwnerCurrent() || !owner.isAdoptionAllowed()) {
      assignNotice({
        kind: "error",
        message: owner.adoptionBlockMessage() ?? "当前编辑状态不允许修改历史消息。",
        canVerify: false,
      });
      return null;
    }
    if (!current.snapshot.writable || current.snapshot.target_proof === null
      || !sameMessage(message, messageInSnapshot(current.snapshot, message.id))) {
      assignNotice({ kind: "error", message: "历史目标或消息无法唯一核对，未发送写入请求。", canVerify: false });
      return null;
    }
    return current;
  }

  function prepareDelete(message: ChapterGenerationHistoryMessage): void {
    if (currentWritableMessage(message) === null) return;
    setDeleteConfirmationMessageId(message.id);
    assignNotice(null);
  }

  function cancelDelete(): void {
    if (!busyRef.current && noticeRef.current?.kind !== "unknown") setDeleteConfirmationMessageId(null);
  }

  async function reconcileActionImmediately(
    pending: PendingHistoryAction,
    action: { readonly controller: AbortController; readonly isCurrent: () => boolean },
  ): Promise<void> {
    pendingHistoryActionRef.current = pending;
    await reconcilePendingHistoryAction(pending, action.controller.signal, action.isCurrent);
  }

  async function confirmDelete(): Promise<void> {
    const messageId = deleteConfirmationMessageId;
    const initial = historyRef.current;
    if (messageId === null || initial.status !== "ready") return;
    const message = messageInSnapshot(initial.snapshot, messageId);
    if (message === null) {
      setDeleteConfirmationMessageId(null);
      assignNotice({ kind: "error", message: "这条历史消息已不在当前列表中，请重新读取。", canVerify: false });
      return;
    }
    const current = currentWritableMessage(message);
    if (current === null) return;
    const action = beginAction(current, "正在取得编辑权限并核对历史消息；尚未发送删除请求。");
    if (action === null) return;
    setDeleteConfirmationMessageId(null);
    let lease: ChapterGenerationHistoryWriteLease | null = null;
    let baseline = current.snapshot;
    try {
      lease = await owner.prepareWrite(action.controller.signal, action.isCurrent);
      if (!action.isCurrent() || !owner.isAdoptionAllowed()) {
        throw new ChapterGenerationHistoryError("not-sent", owner.adoptionBlockMessage() ?? "当前编辑范围已变化，未删除。");
      }
      const fresh = await current.run.readSnapshot(action.controller.signal);
      if (!action.isCurrent()) return;
      const latestMessage = messageInSnapshot(fresh, message.id);
      if (!sameHistoryTarget(current.snapshot, fresh) || !sameMessage(message, latestMessage)) {
        assignSnapshot(current.run, fresh);
        throw new ChapterGenerationHistoryError("conflict", "目标或历史消息已变化，未发送删除。", 409);
      }
      baseline = fresh;
      assignSnapshot(current.run, fresh);
      try {
        await current.run.deleteMessage(message, action.controller.signal);
      } catch (error) {
        const uncertain = error instanceof ChapterGenerationHistoryError
          && (error.kind === "unknown" || (error.kind === "rejected" && error.status === 404));
        if (!uncertain) throw error;
        await reconcileActionImmediately({
          kind: "delete",
          run: current.run,
          message,
          baseline,
          acknowledged: false,
        }, action);
        return;
      }
      await reconcileActionImmediately({
        kind: "delete",
        run: current.run,
        message,
        baseline,
        acknowledged: true,
      }, action);
    } catch (error) {
      if (!action.isCurrent()) return;
      if (isUnauthorized(error)) owner.onUnauthorized();
      assignNotice(historyNotice(error));
    } finally {
      if (lease !== null) {
        try {
          await lease.release();
        } catch {
          if (action.isCurrent() && noticeRef.current?.kind !== "unknown") {
            assignNotice({ kind: "error", message: "删除状态已核对，但本次取得的章节编辑锁未能释放。", canVerify: false });
          }
        }
      }
      if (activeActionController.current === action.controller) activeActionController.current = null;
      if (owner.isOwnerCurrent()) setBusyState(false);
    }
  }

  async function transferMessage(message: ChapterGenerationHistoryMessage): Promise<void> {
    const current = currentWritableMessage(message);
    if (current === null || message.role !== "assistant") return;
    const entry = current.entries.find((item) => item.message.id === message.id);
    if (entry?.mediaUrl === null || entry === undefined || entry.unsafeMedia) return;
    const action = beginAction(current, "正在取得编辑权限并核对原消息；尚未发送转存请求。");
    if (action === null) return;
    let lease: ChapterGenerationHistoryWriteLease | null = null;
    let baseline = current.snapshot;
    let transferStarted = false;
    let transferCompleted = false;
    try {
      lease = await owner.prepareWrite(action.controller.signal, action.isCurrent);
      if (!action.isCurrent() || !owner.isAdoptionAllowed()) {
        throw new ChapterGenerationHistoryError("not-sent", owner.adoptionBlockMessage() ?? "当前编辑范围已变化，未转存。");
      }
      const fresh = await current.run.readSnapshot(action.controller.signal);
      if (!action.isCurrent()) return;
      const latestMessage = messageInSnapshot(fresh, message.id);
      if (!sameHistoryTarget(current.snapshot, fresh) || !sameMessage(message, latestMessage)) {
        assignSnapshot(current.run, fresh);
        throw new ChapterGenerationHistoryError("conflict", "目标或历史消息已变化，未发送转存。", 409);
      }
      baseline = fresh;
      assignSnapshot(current.run, fresh);

      transferStarted = true;
      const transferredUrl = await current.run.retryOssUpload(message, action.controller.signal);
      transferCompleted = true;
      if (!action.isCurrent()) return;
      if (transferredUrl === message.content) {
        await reconcileActionImmediately({
          kind: "transfer-result",
          run: current.run,
          message,
          baseline,
          transferredUrl,
          acknowledged: true,
        }, action);
        return;
      }

      const beforeUpdate = await current.run.readSnapshot(action.controller.signal);
      if (!action.isCurrent()) return;
      const messageBeforeUpdate = messageInSnapshot(beforeUpdate, message.id);
      if (!sameHistoryTarget(baseline, beforeUpdate) || !sameMessage(message, messageBeforeUpdate)) {
        assignSnapshot(current.run, beforeUpdate);
        assignNotice({ kind: "error", message: "OSS 已返回新地址，但原消息已变化；没有写回或重试。", canVerify: false });
        return;
      }
      baseline = beforeUpdate;
      assignSnapshot(current.run, beforeUpdate);
      try {
        await current.run.updateMessageContent(message, transferredUrl, action.controller.signal);
      } catch (error) {
        if (!(error instanceof ChapterGenerationHistoryError) || error.kind !== "unknown") throw error;
        await reconcileActionImmediately({
          kind: "message-update",
          run: current.run,
          message,
          baseline,
          transferredUrl,
          acknowledged: false,
        }, action);
        return;
      }
      await reconcileActionImmediately({
        kind: "message-update",
        run: current.run,
        message,
        baseline,
        transferredUrl,
        acknowledged: true,
      }, action);
    } catch (error) {
      if (!action.isCurrent()) return;
      if (transferStarted && error instanceof ChapterGenerationHistoryError && error.kind === "unknown") {
        await reconcileActionImmediately({
          kind: "transfer",
          run: current.run,
          message,
          baseline,
          transferredUrl: null,
          acknowledged: false,
        }, action);
      } else {
        if (isUnauthorized(error)) owner.onUnauthorized();
        assignNotice({
          kind: "error",
          message: transferCompleted
            ? `转存已成功，但原消息更新未完成：${errorText(error, "没有自动重试。")}`
            : errorText(error, "转存失败，原消息未更新。"),
          canVerify: false,
        });
      }
    } finally {
      if (lease !== null) {
        try {
          await lease.release();
        } catch {
          if (action.isCurrent() && noticeRef.current?.kind !== "unknown") {
            assignNotice({ kind: "error", message: "消息状态已核对，但本次取得的章节编辑锁未能释放。", canVerify: false });
          }
        }
      }
      if (activeActionController.current === action.controller) activeActionController.current = null;
      if (owner.isOwnerCurrent()) setBusyState(false);
    }
  }

  const visibleHistory = useMemo<ChapterGenerationHistoryLoadState>(() => {
    if (history.status !== "ready") return history;
    const start = page * HISTORY_PAGE_SIZE;
    return { status: "ready", entries: history.entries.slice(start, start + HISTORY_PAGE_SIZE) };
  }, [history, page]);

  const pageCount = history.status === "ready" ? Math.ceil(history.entries.length / HISTORY_PAGE_SIZE) : 0;
  const writable = history.status === "ready"
    && history.snapshot.writable
    && history.snapshot.target_proof !== null
    && owner.isAdoptionAllowed()
    && owner.isOwnerCurrent();
  const readOnlyReason = history.status === "ready"
    ? history.snapshot.read_only_reason ?? (owner.isAdoptionAllowed() ? null : owner.adoptionBlockMessage())
    : owner.isAdoptionAllowed() ? null : owner.adoptionBlockMessage();

  const prepareAdoption = useCallback((message: ChapterGenerationHistoryMessage): void => {
    if (busyRef.current || noticeRef.current?.kind === "unknown") return;
    if (!owner.isOwnerCurrent() || !owner.isAdoptionAllowed()) {
      assignNotice({
        kind: "error",
        message: owner.adoptionBlockMessage() ?? "当前镜头来源已变化，不能选用此结果。",
        canVerify: false,
      });
      return;
    }
    const current = historyRef.current;
    if (current.status !== "ready" || !current.snapshot.writable || current.snapshot.target_proof === null) return;
    const displayed = messageInSnapshot(current.snapshot, message.id);
    if (displayed === null || displayed.message_digest !== message.message_digest || displayed.content !== message.content
      || displayed.role !== "assistant") {
      assignNotice({ kind: "error", message: "此历史记录已变化，请重新读取后再选用。", canVerify: false });
      return;
    }

    const controller = new AbortController();
    activeActionController.current = controller;
    setBusyState(true);
    void current.run.readSnapshot(controller.signal).then((freshSnapshot) => {
      if (controller.signal.aborted || !current.run.isCurrent() || !owner.isOwnerCurrent()) return;
      const pending: PendingCandidate = {
        messageId: message.id,
        messageDigest: message.message_digest,
        candidateUrl: message.content,
        baseline: current.snapshot,
      };
      if (!sameCandidateSnapshot(current.snapshot, freshSnapshot, pending)) {
        assignConfirmation(null);
        pendingCandidateRef.current = null;
        assignNotice({ kind: "error", message: "历史消息或当前目标已变化，请重新打开历史后核对。", canVerify: false });
        assignHistory({ status: "ready", snapshot: freshSnapshot, entries: snapshotEntries(freshSnapshot, mode, owner.apiBaseUrl), run: current.run });
        return;
      }
      const freshMessage = messageInSnapshot(freshSnapshot, message.id);
      const proof = freshSnapshot.target_proof;
      if (freshMessage === null || proof === null) {
        assignNotice({ kind: "error", message: "当前历史结果无法核对。", canVerify: false });
        return;
      }
      pendingCandidateRef.current = { ...pending, baseline: freshSnapshot };
      assignConfirmation({
        messageId: freshMessage.id,
        currentMediaUrl: proof.media_url,
        alreadyCurrent: proof.media_url === freshMessage.content,
      });
      assignNotice(null);
      assignHistory({ status: "ready", snapshot: freshSnapshot, entries: snapshotEntries(freshSnapshot, mode, owner.apiBaseUrl), run: current.run });
    }).catch((error: unknown) => {
      if (controller.signal.aborted || !owner.isOwnerCurrent()) return;
      if (isUnauthorized(error)) owner.onUnauthorized();
      assignConfirmation(null);
      assignNotice(historyNotice(error));
    }).finally(() => {
      if (activeActionController.current === controller) activeActionController.current = null;
      if (owner.isOwnerCurrent()) setBusyState(false);
    });
  }, [assignConfirmation, assignHistory, assignNotice, mode, owner, setBusyState]);

  const cancelConfirmation = useCallback((): void => {
    if (busyRef.current || noticeRef.current?.kind === "unknown") return;
    assignConfirmation(null);
    pendingCandidateRef.current = null;
  }, [assignConfirmation]);

  const verifyUnknown = useCallback((): void => {
    const pendingAction = pendingHistoryActionRef.current;
    const current = historyRef.current;
    if (busyRef.current || current.status !== "ready" || !owner.isOwnerCurrent()) return;
    if (pendingAction !== null) {
      if (current.run !== pendingAction.run || !pendingAction.run.isCurrent()) return;
      const controller = new AbortController();
      activeActionController.current = controller;
      setBusyState(true);
      assignNotice({ kind: "progress", message: "正在只读核对原历史消息；不会重复发送写入请求。", canVerify: false });
      const operationIsCurrent = () => !controller.signal.aborted
        && owner.isOwnerCurrent()
        && pendingAction.run.isCurrent()
        && activeActionController.current === controller;
      void reconcilePendingHistoryAction(pendingAction, controller.signal, operationIsCurrent).finally(() => {
        if (activeActionController.current === controller) activeActionController.current = null;
        if (owner.isOwnerCurrent()) setBusyState(false);
      });
      return;
    }
    const pending = pendingCandidateRef.current;
    if (pending === null) return;
    const controller = new AbortController();
    activeActionController.current = controller;
    setBusyState(true);
    const operationIsCurrent = () => !controller.signal.aborted
      && owner.isOwnerCurrent()
      && current.run.isCurrent()
      && activeActionController.current === controller;
    void (async () => {
      try {
        const snapshot = await current.run.readSnapshot(controller.signal);
        if (!operationIsCurrent()) return;
        const candidate = messageInSnapshot(snapshot, pending.messageId);
        const targetMatches = snapshot.writable
          && snapshot.target_proof !== null
          && snapshot.target_proof.mapping_digest === pending.baseline.target_proof?.mapping_digest
          && snapshot.target_proof.media_url === pending.candidateUrl
          && candidate?.role === "assistant"
          && candidate.message_digest === pending.messageDigest
          && candidate.content === pending.candidateUrl;
        if (targetMatches) {
          await owner.refreshAfterAdoption(controller.signal, operationIsCurrent, snapshot);
          if (!operationIsCurrent()) return;
          pendingCandidateRef.current = null;
          assignConfirmation(null);
          assignNotice({
            kind: "success",
            message: `当前${snapshot.target_kind === "frame-preview" ? "镜头预览" : "故事板原图"}已核对为所选历史结果；无法证明此前请求是否完成。`,
            canVerify: false,
          });
        } else {
          assignNotice({
            kind: "unknown",
            message: "当前值未能与该历史结果核对；未重发写入请求。",
            canVerify: true,
          });
        }
        assignHistory({ status: "ready", snapshot, entries: snapshotEntries(snapshot, mode, owner.apiBaseUrl), run: current.run });
      } catch (error) {
        if (!operationIsCurrent()) return;
        if (isUnauthorized(error)) owner.onUnauthorized();
        assignNotice({ kind: "unknown", message: errorText(error, "只读核对失败；未重发写入请求。"), canVerify: true });
      } finally {
        if (activeActionController.current === controller) activeActionController.current = null;
        if (owner.isOwnerCurrent()) setBusyState(false);
      }
    })();
  }, [assignConfirmation, assignHistory, assignNotice, mode, owner, setBusyState]);

  const confirmAdoption = useCallback((): void => {
    const pending = pendingCandidateRef.current;
    const current = historyRef.current;
    if (busyRef.current || noticeRef.current?.kind === "unknown" || pending === null || current.status !== "ready") return;
    if (!owner.isOwnerCurrent() || !owner.isAdoptionAllowed()) {
      assignNotice({ kind: "error", message: owner.adoptionBlockMessage() ?? "当前编辑状态不允许选用历史结果。", canVerify: false });
      assignConfirmation(null);
      pendingCandidateRef.current = null;
      return;
    }

    const confirmationNow = confirmationRef.current;
    if (confirmationNow?.messageId !== pending.messageId) return;
    const controller = new AbortController();
    activeActionController.current = controller;
    setBusyState(true);
    let lease: ChapterGenerationHistoryWriteLease | null = null;
    let verifiedCurrentTarget = false;
    let writeResponded = false;

    const operationIsCurrent = () => !controller.signal.aborted
      && owner.isOwnerCurrent()
      && current.run.isCurrent()
      && activeActionController.current === controller;

    void (async () => {
      try {
        if (!confirmationNow.alreadyCurrent) {
          lease = await owner.prepareWrite(controller.signal, operationIsCurrent);
          if (!operationIsCurrent() || !owner.isAdoptionAllowed()) {
            throw new ChapterGenerationHistoryError("not-sent", "当前编辑状态或目标已变化，未发送选用请求。");
          }
        }

        const latest = await current.run.readSnapshot(controller.signal);
        if (!operationIsCurrent()) return;
        const latestMessage = messageInSnapshot(latest, pending.messageId);
        if (!sameCandidateSnapshot(pending.baseline, latest, pending)
          || latestMessage === null
          || latest.target_proof === null) {
          throw new ChapterGenerationHistoryError("conflict", "历史消息或当前目标已变化，请重新打开历史后核对。", 409);
        }

        if (latest.target_proof.media_url === pending.candidateUrl) {
          verifiedCurrentTarget = true;
          assignHistory({ status: "ready", snapshot: latest, entries: snapshotEntries(latest, mode, owner.apiBaseUrl), run: current.run });
          await owner.refreshAfterAdoption(controller.signal, operationIsCurrent, latest);
          if (!operationIsCurrent()) return;
          pendingCandidateRef.current = null;
          assignConfirmation(null);
          assignNotice({
            kind: "success",
            message: `当前${latest.target_kind === "frame-preview" ? "镜头预览" : "故事板原图"}已核对为所选历史结果。`,
            canVerify: false,
          });
          return;
        }

        const adoptInput: ChapterGenerationHistoryAdoptInput = {
          messageId: latestMessage.id,
          expectedMessageDigest: latestMessage.message_digest,
          expectedMappingDigest: latest.target_proof.mapping_digest,
          expectedTargetDigest: latest.target_proof.target_digest,
          expectedMediaUrl: latest.target_proof.media_url,
          expectedLockIdentity: lease?.identity ?? null,
        };
        await current.run.adopt(adoptInput, controller.signal);
        writeResponded = true;
        if (!operationIsCurrent()) return;

        const verified = await current.run.readSnapshot(controller.signal);
        if (!operationIsCurrent()) return;
        const verifiedMessage = messageInSnapshot(verified, pending.messageId);
        const matchesCurrent = sameOperationTarget(latest, verified)
          && verified.target_proof?.media_url === pending.candidateUrl
          && verifiedMessage?.message_digest === pending.messageDigest
          && verifiedMessage.content === pending.candidateUrl;
        if (!matchesCurrent) {
          throw new ChapterGenerationHistoryError("unknown", "选用请求已响应，但独立读取未能核对当前字段；请只读核对。", 200);
        }
        verifiedCurrentTarget = true;
        assignHistory({ status: "ready", snapshot: verified, entries: snapshotEntries(verified, mode, owner.apiBaseUrl), run: current.run });
        pendingCandidateRef.current = null;
        assignConfirmation(null);
        assignNotice({
          kind: "success",
          message: `当前${verified.target_kind === "frame-preview" ? "镜头预览" : "故事板原图"}已核对为所选历史结果。`,
          canVerify: false,
        });
        let lockReleaseFailed = false;
        if (lease !== null) {
          const acquiredLease = lease;
          lease = null;
          try {
            await acquiredLease.release();
          } catch {
            lockReleaseFailed = true;
          }
        }
        try {
          await owner.refreshAfterAdoption(controller.signal, operationIsCurrent, verified);
        } catch {
          if (operationIsCurrent()) {
            assignNotice({
              kind: "error",
              message: lockReleaseFailed
                ? "当前字段已核对为所选历史结果，但编辑锁释放或章节、素材列表刷新失败。"
                : "当前字段已核对为所选历史结果，但章节或素材列表刷新失败。",
              canVerify: false,
            });
          }
        }
        if (lockReleaseFailed && operationIsCurrent()) {
          assignNotice({
            kind: "error",
            message: "当前字段已核对为所选历史结果，但本次取得的章节编辑锁未能释放。",
            canVerify: false,
          });
        }
      } catch (error) {
        if (!operationIsCurrent()) return;
        if (isUnauthorized(error)) owner.onUnauthorized();
        const nextNotice = writeResponded
          ? { kind: "unknown" as const, message: `选用请求已响应，但独立核对未完成；请只读核对，不要重发。${errorText(error, "")}`, canVerify: true }
          : historyNotice(error);
        assignNotice(nextNotice);
        if (nextNotice.kind !== "unknown") {
          assignConfirmation(null);
          pendingCandidateRef.current = null;
        }
      } finally {
        if (lease !== null) {
          try {
            await lease.release();
          } catch {
            if (operationIsCurrent() && verifiedCurrentTarget) {
              assignNotice({
                kind: "error",
                message: "当前字段已核对；本次取得的章节锁未能立即释放。",
                canVerify: false,
              });
            }
          }
        }
        if (activeActionController.current === controller) activeActionController.current = null;
        if (owner.isOwnerCurrent()) setBusyState(false);
      }
    })();
  }, [assignConfirmation, assignHistory, assignNotice, mode, owner, setBusyState]);

  const onTargetKindChange = useCallback((next: ChapterGenerationHistoryTargetKind): void => {
    if (busyRef.current || noticeRef.current?.kind === "unknown") return;
    setTargetKind(next);
  }, []);

  const onModeChange = useCallback((next: ChapterGenerationHistoryMode): void => {
    if (busyRef.current || noticeRef.current?.kind === "unknown") return;
    setMode(next);
  }, []);

  const onPageChange = useCallback((next: number): void => {
    if (busyRef.current || noticeRef.current?.kind === "unknown") return;
    const count = historyRef.current.status === "ready"
      ? Math.ceil(historyRef.current.entries.length / HISTORY_PAGE_SIZE)
      : 0;
    if (Number.isSafeInteger(next) && next >= 0 && next < count) setPage(next);
  }, []);

  return {
    ownerKey: owner.ownerKey,
    chapterTitle: owner.chapterTitle,
    frameIndex: owner.frameIndex,
    targetKind,
    mode,
    loadState: visibleHistory,
    page,
    pageCount,
    writable,
    readOnlyReason,
    confirmation,
    deleteConfirmationMessageId,
    busy,
    notice,
    previewAssetName: `镜头 ${owner.frameIndex + 1}`,
    isCurrent,
    readMedia,
    onTargetKindChange,
    onModeChange,
    onPageChange,
    onPrepareAdoption: prepareAdoption,
    onDownload: downloadMessage,
    onTransfer: transferMessage,
    onPrepareDelete: prepareDelete,
    onCancelDelete: cancelDelete,
    onConfirmDelete: confirmDelete,
    onCancelConfirmation: cancelConfirmation,
    onConfirmAdoption: confirmAdoption,
    onVerifyUnknown: verifyUnknown,
    onUnauthorized: owner.onUnauthorized,
  };
}
