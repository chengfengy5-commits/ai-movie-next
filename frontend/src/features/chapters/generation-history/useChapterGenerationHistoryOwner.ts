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

export function useChapterGenerationHistoryOwner(owner: ChapterGenerationHistoryOwner) {
  const [targetKind, setTargetKind] = useState<ChapterGenerationHistoryTargetKind>("frame-preview");
  const [mode, setMode] = useState<ChapterGenerationHistoryMode>("image");
  const [page, setPage] = useState(0);
  const [history, setHistory] = useState<HistoryData>({ status: "loading" });
  const historyRef = useRef<HistoryData>(history);
  const [confirmation, setConfirmation] = useState<ChapterGenerationHistoryConfirmation | null>(null);
  const confirmationRef = useRef<ChapterGenerationHistoryConfirmation | null>(confirmation);
  const [notice, setNotice] = useState<ChapterGenerationHistoryNotice | null>(null);
  const noticeRef = useRef<ChapterGenerationHistoryNotice | null>(notice);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const pendingCandidateRef = useRef<PendingCandidate | null>(null);
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

  useEffect(() => {
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    assignHistory({ status: "loading" });
    assignConfirmation(null);
    pendingCandidateRef.current = null;
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
    const pending = pendingCandidateRef.current;
    const current = historyRef.current;
    if (busyRef.current || pending === null || current.status !== "ready" || !owner.isOwnerCurrent()) return;
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
    busy,
    notice,
    previewAssetName: `镜头 ${owner.frameIndex + 1}`,
    isCurrent,
    readMedia,
    onTargetKindChange,
    onModeChange,
    onPageChange,
    onPrepareAdoption: prepareAdoption,
    onCancelConfirmation: cancelConfirmation,
    onConfirmAdoption: confirmAdoption,
    onVerifyUnknown: verifyUnknown,
    onUnauthorized: owner.onUnauthorized,
  };
}
