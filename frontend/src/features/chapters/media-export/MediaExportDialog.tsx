import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Chapter } from "../../../shared/api/contracts";
import type { PersonalRoughCutSnapshot } from "../../../shared/api/personalRoughCut";
import {
  MediaExportSinkError,
  type MediaExportEligibility,
  type MediaExportQueue,
  type MediaExportQueueItem,
  type MediaExportSink,
} from "../../../shared/api/mediaExport";
import type { MediaExportReaderError } from "../../../shared/api/mediaExportReader";
import type { MediaExportSessionTransport } from "../../../shared/api/mediaExportSession";
import { useAuth } from "../../auth/AuthProvider";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import {
  buildChapterMediaExportQueue,
  buildSavedRoughCutMediaExportQueue,
  createFailedMediaExportRetryQueue,
  startMediaExport,
  type MediaExportRun,
} from "./queue";
import {
  clearSavedMediaExportRecords,
  countStaleMediaExportRecords,
  findMatchingSavedMediaExportRecord,
  fingerprintMediaExportCandidate,
  getMediaExportStorage,
  mediaExportPreferencesStorageKey,
  mediaExportRecordIdentity,
  mediaExportRecordsStorageKey,
  readSavedMediaExportRecords,
  readSkipSavedPreference,
  toSavedMediaExportRecord,
  writeSavedMediaExportRecord,
  writeSkipSavedPreference,
  type MediaExportPreferenceScope,
  type MediaExportRecordCandidate,
  type MediaExportRecordScope,
  type SavedMediaExportRecord,
} from "./exportRecords";
import {
  createBrowserMediaExportSink,
  createDirectoryMediaExportSink,
  type DirectoryHandleLike,
} from "./sinks";
import "./mediaExport.css";

type ExportSourceMode = "chapter" | "rough-cut";
type ExportPhase = "review" | "confirmed" | "preparing" | "running" | "complete";

interface MediaExportDialogProps {
  readonly chapter: Chapter;
  readonly seriesName: string;
  readonly sourceMode: ExportSourceMode;
  readonly sourceRevision?: string | null;
  readonly roughCutSnapshot?: PersonalRoughCutSnapshot | null;
  readonly isSourceCurrent?: () => boolean;
  readonly onClose: () => void;
  readonly onUnauthorized?: (error: MediaExportReaderError) => void;
}

interface DirectoryPickerWindow {
  showDirectoryPicker?: (options?: { readonly mode?: "readwrite" }) => Promise<DirectoryHandleLike>;
}

interface CapturedPageScope {
  readonly userId: string;
  readonly sessionGeneration: number;
  readonly services: ReturnType<typeof useAuth>["services"];
  readonly chapterId: string;
  readonly seriesId: string;
  readonly seriesName: string;
  readonly chapterTitle: string;
  readonly chapterUpdatedAt: string;
  readonly chapterContent: Chapter["content"];
  readonly sourceRevision: string | null;
  readonly sourceMode: ExportSourceMode;
  readonly roughCutSnapshot: PersonalRoughCutSnapshot | null | undefined;
}

interface RecordCandidateEntry {
  readonly identity: string;
  readonly candidate: MediaExportRecordCandidate;
}

interface LoadedRecordState {
  readonly scopeKey: string;
  readonly preferenceScopeKey: string;
  readonly generation: number;
  readonly records: readonly SavedMediaExportRecord[];
  readonly skipConfirmedSaved: boolean;
  readonly recordsAvailable: boolean;
  readonly preferenceAvailable: boolean;
}

interface FingerprintState {
  readonly scopeKey: string;
  readonly candidates: readonly RecordCandidateEntry[];
  readonly fingerprints: ReadonlyMap<string, string>;
  readonly unavailableCount: number;
}

function sourceTextForItem(
  sourceKind: ExportSourceMode,
  item: MediaExportQueueItem,
  chapter: Chapter,
  roughCutSnapshot: PersonalRoughCutSnapshot | null | undefined,
): string {
  const value = sourceKind === "chapter"
    ? chapter.content?.[item.frameIndex]?.text
    : roughCutSnapshot?.frames[item.sourceIndex]?.text;
  return typeof value === "string" ? value : "";
}

function reviewStatus(
  item: MediaExportQueueItem,
  selected: boolean,
  hasSavedRecord: boolean,
  skipSaved: boolean,
): string {
  if (item.eligibility === "ready" && item.assetId === null) {
    return selected
      ? "已选，可导出；缺少稳定镜头身份，不记本机记录"
      : "已排除；缺少稳定镜头身份，不记本机记录";
  }
  if (hasSavedRecord) {
    return skipSaved
      ? "本机记录确认已保存，已跳过；文件仍需自行核对"
      : "本机记录确认已保存，可重新导出";
  }
  return statusForEligibility(item.eligibility, selected);
}

let fallbackRunSequence = 0;

function createRunId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  const identity = uuid ?? `${Date.now()}-${++fallbackRunSequence}`;
  return `media-export-${identity}`;
}

function currentSourceRevision(props: MediaExportDialogProps): string | null {
  return props.sourceRevision === undefined ? props.chapter.updated_at : props.sourceRevision;
}

function allFrameIndexes(chapter: Chapter): number[] {
  return Array.from({ length: chapter.content?.length ?? 0 }, (_, index) => index);
}

function defaultChapterSelection(chapter: Chapter, seriesName: string, sourceRevision: string | null): Set<number> {
  const queue = buildChapterMediaExportQueue({
    runId: "media-export-selection-preview",
    chapter,
    seriesName,
    selectedFrameIndexes: allFrameIndexes(chapter),
    sourceRevision,
  });
  return new Set(queue.items
    .filter((item) => item.eligibility === "ready")
    .map((item) => item.frameIndex));
}

function statusForEligibility(eligibility: MediaExportEligibility, selected: boolean): string {
  switch (eligibility) {
    case "ready":
      return selected ? "已选，可导出" : "已排除";
    case "excluded":
      return "已排除";
    case "pending":
      return "粗剪仍在等待处理";
    case "missing":
      return "缺少视频";
    case "duplicate":
      return "素材身份重复，无法确认";
    case "invalid":
      return "素材身份无效";
  }
}

function statusForItem(item: MediaExportQueueItem, selected: boolean): string {
  switch (item.state) {
    case "resolving":
      return "正在解析下载地址";
    case "reading":
      return "正在读取视频字节";
    case "writing":
      return "正在写入目标文件";
    case "saved":
      return "已保存";
    case "download-started":
      return "已发起浏览器下载，磁盘结果未确认";
    case "failed":
      return item.message ?? "失败，可确认新目标后手动重试";
    case "unknown":
      return item.message ?? "保存结果未知，不能自动覆盖或重试";
    case "stopped":
      return item.message ?? "已停止";
    case "not-started":
    case "queued":
      return statusForEligibility(item.eligibility, selected);
  }
}

function frameDescription(
  item: MediaExportQueueItem,
  chapter: Chapter,
  roughCutSnapshot: PersonalRoughCutSnapshot | null | undefined,
  mode: ExportSourceMode,
): string {
  const rawText = mode === "chapter"
    ? chapter.content?.[item.frameIndex]?.text
    : roughCutSnapshot?.frames[item.sourceIndex]?.text;
  const text = typeof rawText === "string" ? rawText.trim() : "";
  return text.length > 0 ? text.slice(0, 120) : `分镜 ${item.frameIndex + 1}`;
}

function hasReadyItems(queue: MediaExportQueue): boolean {
  return queue.items.some((item) => item.eligibility === "ready" && item.state === "queued");
}

function canUseDirectoryPicker(): boolean {
  return typeof (window as unknown as DirectoryPickerWindow).showDirectoryPicker === "function";
}

export function MediaExportDialog({
  chapter,
  seriesName,
  sourceMode: initialSourceMode,
  sourceRevision,
  roughCutSnapshot,
  isSourceCurrent,
  onClose,
  onUnauthorized,
}: MediaExportDialogProps) {
  const { services, state, sessionGeneration } = useAuth();
  const userId = state.status === "authenticated" ? state.user.id : null;
  const recordScope = useMemo<MediaExportRecordScope | null>(() => (
    userId === null || services.apiBaseUrl === null
      ? null
      : {
        actorId: userId,
        apiBaseUrl: services.apiBaseUrl,
        seriesId: chapter.series_id,
        chapterId: chapter.id,
      }
  ), [chapter.id, chapter.series_id, services.apiBaseUrl, userId]);
  const preferenceScope = useMemo<MediaExportPreferenceScope | null>(() => (
    userId === null || services.apiBaseUrl === null
      ? null
      : { actorId: userId, apiBaseUrl: services.apiBaseUrl }
  ), [services.apiBaseUrl, userId]);
  const recordScopeKey = recordScope === null ? null : mediaExportRecordsStorageKey(recordScope);
  const preferenceScopeKey = preferenceScope === null
    ? null
    : mediaExportPreferencesStorageKey(preferenceScope);
  const dialogProps: MediaExportDialogProps = {
    chapter,
    seriesName,
    sourceMode: initialSourceMode,
    ...(sourceRevision === undefined ? {} : { sourceRevision }),
    ...(roughCutSnapshot === undefined ? {} : { roughCutSnapshot }),
    ...(isSourceCurrent === undefined ? {} : { isSourceCurrent }),
    onClose,
    ...(onUnauthorized === undefined ? {} : { onUnauthorized }),
  };
  const propsRef = useRef<MediaExportDialogProps>(dialogProps);
  propsRef.current = dialogProps;
  const authRef = useRef({ services, state, sessionGeneration });
  authRef.current = { services, state, sessionGeneration };
  const mountedRef = useRef(false);
  const identityInvalidatedRef = useRef(false);
  const controllerRef = useRef<AbortController | null>(null);
  const runRef = useRef<MediaExportRun | null>(null);
  const sessionTransportRef = useRef<MediaExportSessionTransport | null>(null);
  const previousScopeRef = useRef<CapturedPageScope | null>(null);
  const sourceModeRef = useRef<ExportSourceMode>("chapter");
  const [sourceMode, setSourceMode] = useState<ExportSourceMode>(initialSourceMode);
  sourceModeRef.current = sourceMode;
  const [phase, setPhase] = useState<ExportPhase>("review");
  const [draftRunId, setDraftRunId] = useState(createRunId);
  const [selectedFrameIndexes, setSelectedFrameIndexes] = useState<Set<number>>(
    () => defaultChapterSelection(chapter, seriesName, currentSourceRevision(propsRef.current)),
  );
  const [frozenQueue, setFrozenQueue] = useState<MediaExportQueue | null>(null);
  const [resultQueue, setResultQueue] = useState<MediaExportQueue | null>(null);
  const [confirmationChecked, setConfirmationChecked] = useState(false);
  const [retrySelectedIds, setRetrySelectedIds] = useState<Set<string>>(new Set());
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [runNotice, setRunNotice] = useState<string | null>(null);
  const [loadedRecordState, setLoadedRecordState] = useState<LoadedRecordState | null>(null);
  const [fingerprintState, setFingerprintState] = useState<FingerprintState | null>(null);
  const [recordWriteFailureScopeKey, setRecordWriteFailureScopeKey] = useState<string | null>(null);
  const [preferenceWriteFailureScopeKey, setPreferenceWriteFailureScopeKey] = useState<string | null>(null);
  const [recordActionNotice, setRecordActionNotice] = useState<{ scopeKey: string; message: string } | null>(null);
  const [frozenSavedSkipIds, setFrozenSavedSkipIds] = useState<ReadonlySet<string>>(new Set());
  const recordGenerationRef = useRef(new Map<string, number>());

  const focus = useSeriesDialogFocus({
    active: true,
    canDismiss: true,
    onDismiss: () => {
      controllerRef.current?.abort();
      runRef.current?.abort();
      propsRef.current.onClose();
    },
  });

  const invalidateIfSessionChanged = useCallback((): boolean => {
    const transport = sessionTransportRef.current;
    if (transport === null || transport.isCurrent()) {
      return false;
    }
    controllerRef.current?.abort();
    runRef.current?.abort();
    sessionTransportRef.current = null;
    controllerRef.current = null;
    runRef.current = null;
    identityInvalidatedRef.current = true;
    setFrozenQueue(null);
    setResultQueue(null);
    setFrozenSavedSkipIds(new Set());
    setPhase("review");
    setErrorMessage("登录凭据已变化，当前媒体导出已停止。请核对登录状态后重新开始。");
    propsRef.current.onClose();
    return true;
  }, []);

  useEffect(() => {
    const onStorage = () => {
      invalidateIfSessionChanged();
    };
    window.addEventListener("storage", onStorage);
    try {
      createTransport(sourceModeRef.current);
    } catch {
      // The dialog can still explain the session error when the user starts an export.
    }
    return () => {
      window.removeEventListener("storage", onStorage);
    };
  }, [invalidateIfSessionChanged]);

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      runRef.current?.abort();
      sessionTransportRef.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    const nextScope: CapturedPageScope | null = userId === null
      ? null
      : {
        userId,
        sessionGeneration,
        services,
        chapterId: chapter.id,
        seriesId: chapter.series_id,
        seriesName,
        chapterTitle: chapter.title,
        chapterUpdatedAt: chapter.updated_at,
        chapterContent: chapter.content,
        sourceRevision: currentSourceRevision(propsRef.current),
        sourceMode: sourceModeRef.current,
        roughCutSnapshot,
      };
    const previous = previousScopeRef.current;
    const changed = previous !== null && (
      nextScope === null
      || previous.userId !== nextScope.userId
      || previous.sessionGeneration !== nextScope.sessionGeneration
      || previous.services !== nextScope.services
      || previous.chapterId !== nextScope.chapterId
      || previous.seriesId !== nextScope.seriesId
      || previous.seriesName !== nextScope.seriesName
      || previous.chapterTitle !== nextScope.chapterTitle
      || previous.chapterUpdatedAt !== nextScope.chapterUpdatedAt
      || previous.chapterContent !== nextScope.chapterContent
      || previous.sourceRevision !== nextScope.sourceRevision
      || previous.roughCutSnapshot !== nextScope.roughCutSnapshot
    );
    previousScopeRef.current = nextScope;
    if (!changed) return;

    controllerRef.current?.abort();
    runRef.current?.abort();
    controllerRef.current = null;
    runRef.current = null;
    setSourceMode(propsRef.current.sourceMode);
    setSelectedFrameIndexes(defaultChapterSelection(chapter, seriesName, currentSourceRevision(propsRef.current)));
    setDraftRunId(createRunId());
    setFrozenQueue(null);
    setResultQueue(null);
    setFrozenSavedSkipIds(new Set());
    setPhase("review");
    setConfirmationChecked(false);
    setRetrySelectedIds(new Set());
    setRetryConfirmed(false);
    setErrorMessage(null);
    setRunNotice(null);
  }, [
    chapter.content,
    chapter.id,
    chapter.series_id,
    chapter.title,
    chapter.updated_at,
    roughCutSnapshot,
    seriesName,
    services,
    sessionGeneration,
    sourceRevision,
    userId,
  ]);

  const chapterAllQueue = buildChapterMediaExportQueue({
    runId: draftRunId,
    chapter,
    seriesName,
    selectedFrameIndexes: allFrameIndexes(chapter),
    sourceRevision: currentSourceRevision(propsRef.current),
  });
  let roughCutProjection: MediaExportQueue | null = null;
  let roughCutMessage: string | null = null;
  if (roughCutSnapshot !== undefined && roughCutSnapshot !== null) {
    try {
      roughCutProjection = buildSavedRoughCutMediaExportQueue({
        runId: draftRunId,
        chapter,
        seriesName,
        snapshot: roughCutSnapshot,
      });
    } catch {
      roughCutMessage = "当前粗剪未保存或不属于本章，不能创建导出队列。";
    }
  }

  useEffect(() => {
    if (recordScope === null || recordScopeKey === null || preferenceScope === null || preferenceScopeKey === null) {
      setLoadedRecordState(null);
      setFingerprintState(null);
      return;
    }
    const storage = getMediaExportStorage();
    const savedRecords = readSavedMediaExportRecords(storage, recordScope);
    const preference = readSkipSavedPreference(storage, preferenceScope);
    setLoadedRecordState({
      scopeKey: recordScopeKey,
      preferenceScopeKey,
      generation: savedRecords.status === "ready" ? savedRecords.value.generation : 0,
      records: savedRecords.status === "ready" ? savedRecords.value.records : [],
      skipConfirmedSaved: preference.status === "ready" ? preference.value : false,
      recordsAvailable: savedRecords.status === "ready",
      preferenceAvailable: preference.status === "ready",
    });
    setRecordWriteFailureScopeKey(null);
    setPreferenceWriteFailureScopeKey(null);
    setRecordActionNotice(null);
  }, [preferenceScope, preferenceScopeKey, recordScope, recordScopeKey]);

  const recordSourceSignature = JSON.stringify([
    sourceMode,
    chapter.id,
    chapter.series_id,
    chapter.title,
    seriesName,
    currentSourceRevision(propsRef.current),
    sourceMode === "chapter"
      ? chapter.content?.map((frame) => [
        Array.isArray(frame.storyboard) && typeof frame.storyboard[0] === "string"
          ? frame.storyboard[0]
          : null,
        typeof frame.preview === "string" ? frame.preview : null,
        typeof frame.text === "string" ? frame.text : null,
      ]) ?? []
      : null,
    sourceMode === "rough-cut" && roughCutSnapshot !== null && roughCutSnapshot !== undefined
      ? [
        roughCutSnapshot.saved,
        roughCutSnapshot.chapter_id,
        roughCutSnapshot.revision,
        roughCutSnapshot.frames.map((frame) => [
          frame.asset_id,
          frame.frame_index,
          frame.preview_url,
          frame.text,
          frame.included,
          frame.pending,
        ]),
      ]
      : null,
  ]);
  const recordCandidates = useMemo<readonly RecordCandidateEntry[]>(() => {
    let queue: MediaExportQueue | null = null;
    if (sourceMode === "chapter") {
      queue = buildChapterMediaExportQueue({
        runId: "media-export-record-preview",
        chapter,
        seriesName,
        selectedFrameIndexes: allFrameIndexes(chapter),
        sourceRevision: currentSourceRevision(propsRef.current),
      });
    } else if (roughCutSnapshot !== null && roughCutSnapshot !== undefined) {
      try {
        queue = buildSavedRoughCutMediaExportQueue({
          runId: "media-export-record-preview",
          chapter,
          seriesName,
          snapshot: roughCutSnapshot,
        });
      } catch {
        queue = null;
      }
    }
    if (queue === null) return [];
    return queue.items.flatMap((item) => {
      if (item.eligibility !== "ready" || item.assetId === null || item.sourceUrl === null) {
        return [];
      }
      const identity = mediaExportRecordIdentity(item.sourceKind, item.assetId);
      return [{
        identity,
        candidate: {
          sourceKind: item.sourceKind,
          assetId: item.assetId,
          sourceRevision: queue.sourceRevision,
          sourceUrl: item.sourceUrl,
          sourceText: sourceTextForItem(sourceMode, item, chapter, roughCutSnapshot),
          filename: item.filename,
        },
      }];
    });
  }, [
    chapter.content,
    chapter.id,
    chapter.series_id,
    chapter.title,
    chapter.updated_at,
    roughCutSnapshot,
    seriesName,
    sourceMode,
    recordSourceSignature,
    sourceRevision,
  ]);

  useEffect(() => {
    if (recordScopeKey === null) {
      setFingerprintState(null);
      return;
    }
    let current = true;
    setFingerprintState(null);
    void Promise.all(recordCandidates.map(async (entry) => ({
      identity: entry.identity,
      fingerprint: await fingerprintMediaExportCandidate(entry.candidate),
    }))).then((results) => {
      if (!current) return;
      const fingerprints = new Map<string, string>();
      let unavailableCount = 0;
      for (const result of results) {
        if (result.fingerprint === null) unavailableCount += 1;
        else fingerprints.set(result.identity, result.fingerprint);
      }
      setFingerprintState({
        scopeKey: recordScopeKey,
        candidates: recordCandidates,
        fingerprints,
        unavailableCount,
      });
    });
    return () => {
      current = false;
    };
  }, [recordCandidates, recordScopeKey]);

  const currentRecordState = loadedRecordState?.scopeKey === recordScopeKey
    && loadedRecordState.preferenceScopeKey === preferenceScopeKey
    ? loadedRecordState
    : null;
  const currentFingerprintState = fingerprintState?.scopeKey === recordScopeKey
    && fingerprintState.candidates === recordCandidates
    ? fingerprintState
    : null;
  const fingerprintsByIdentity = currentFingerprintState?.fingerprints ?? new Map<string, string>();
  const savedRecordIdentities = new Set<string>();
  if (currentRecordState?.recordsAvailable && currentFingerprintState !== null) {
    for (const entry of recordCandidates) {
      if (findMatchingSavedMediaExportRecord(
        currentRecordState.records,
        entry.candidate,
        fingerprintsByIdentity.get(entry.identity) ?? null,
      ) !== null) {
        savedRecordIdentities.add(entry.identity);
      }
    }
  }
  const skipConfirmedSaved = currentRecordState?.skipConfirmedSaved ?? false;
  const selectedIndexes = Array.from(selectedFrameIndexes).filter((frameIndex) => {
    if (!skipConfirmedSaved) return true;
    const item = chapterAllQueue.items.find((candidate) => candidate.frameIndex === frameIndex);
    return item?.assetId === null || item?.assetId === undefined
      || !savedRecordIdentities.has(mediaExportRecordIdentity(item.sourceKind, item.assetId));
  }).sort((left, right) => left - right);
  const chapterProjection = buildChapterMediaExportQueue({
    runId: draftRunId,
    chapter,
    seriesName,
    selectedFrameIndexes: selectedIndexes,
    sourceRevision: currentSourceRevision(propsRef.current),
  });
  const skippedSavedIdentities = skipConfirmedSaved ? savedRecordIdentities : new Set<string>();
  const roughCutProjectionForDisplay = roughCutProjection === null
    ? null
    : {
      ...roughCutProjection,
      items: Object.freeze(roughCutProjection.items.map((item) => (
        item.eligibility === "ready"
        && item.assetId !== null
        && skippedSavedIdentities.has(mediaExportRecordIdentity(item.sourceKind, item.assetId))
          ? Object.freeze({ ...item, eligibility: "excluded" as const })
          : item
      ))),
    };
  const activeProjection = sourceMode === "chapter" ? chapterProjection : roughCutProjectionForDisplay;
  const recordCheckReady = recordScopeKey === null
    || currentRecordState !== null && currentFingerprintState !== null;
  const staleRecordCount = currentRecordState?.recordsAvailable && currentFingerprintState !== null
    ? countStaleMediaExportRecords(
      currentRecordState.records,
      recordCandidates.map((entry) => entry.candidate),
      fingerprintsByIdentity,
    )
    : 0;
  const localRecordNotice = recordScopeKey === null
    ? null
    : currentRecordState === null
      ? "正在读取本机导出记录与偏好…"
      : !currentRecordState.recordsAvailable || !currentRecordState.preferenceAvailable
        ? "本机导出记录或偏好不可用；本次仍可手动导出，系统不会假报已记住。"
        : currentFingerprintState === null
          ? "正在安全核对当前来源与本机导出记录…"
          : currentFingerprintState.unavailableCount > 0
            ? "部分来源无法安全核对，本次不会为这些项目应用或写入本机记录。"
            : recordWriteFailureScopeKey === recordScopeKey
              ? "本机导出记录未能保存；本次文件状态仍按实际导出结果显示。"
              : preferenceWriteFailureScopeKey === preferenceScopeKey
                ? "跳过偏好未能持久化；当前勾选仅在本次打开期间生效。"
              : staleRecordCount > 0
                ? `${staleRecordCount} 条本机记录与当前镜头或版本不符，已失效。`
                : null;
  const queueForDisplay = resultQueue ?? frozenQueue ?? (
    sourceMode === "chapter" ? chapterAllQueue : roughCutProjectionForDisplay
  );
  const failedItems = (resultQueue?.items ?? []).filter((item) => (
    item.eligibility === "ready" && item.state === "failed"
  ));
  const chapterReadyCount = chapterAllQueue.items.filter((item) => item.eligibility === "ready").length;
  const chapterMissingCount = chapterAllQueue.items.filter((item) => item.eligibility === "missing").length;
  const chapterDuplicateCount = chapterAllQueue.items.filter((item) => item.eligibility === "duplicate").length;
  const chapterExcludedCount = chapterAllQueue.items.filter((item) => (
    item.eligibility === "ready" && !selectedIndexes.includes(item.frameIndex)
  )).length;
  const chapterSkippedSavedCount = skipConfirmedSaved
    ? chapterAllQueue.items.filter((item) => (
      item.eligibility === "ready"
      && item.assetId !== null
      && savedRecordIdentities.has(mediaExportRecordIdentity(item.sourceKind, item.assetId))
    )).length
    : 0;

  function changeSkipSavedPreference(skip: boolean): void {
    if (currentRecordState === null || preferenceScope === null || preferenceScopeKey === null) return;
    setLoadedRecordState((current) => current?.scopeKey === recordScopeKey
      ? { ...current, skipConfirmedSaved: skip }
      : current);
    const written = writeSkipSavedPreference(getMediaExportStorage(), preferenceScope, skip);
    if (written) {
      setLoadedRecordState((current) => current?.scopeKey === recordScopeKey
        ? { ...current, preferenceAvailable: true }
        : current);
      setPreferenceWriteFailureScopeKey(null);
      setRecordActionNotice(null);
    } else {
      setPreferenceWriteFailureScopeKey(preferenceScopeKey);
      setRecordActionNotice({
        scopeKey: recordScopeKey ?? "",
        message: "跳过偏好只在本次打开期间生效，未能写入本机存储。",
      });
    }
  }

  function clearCurrentChapterRecords(): void {
    if (currentRecordState === null || recordScope === null || recordScopeKey === null) return;
    const generation = clearSavedMediaExportRecords(getMediaExportStorage(), recordScope);
    if (generation === null) {
      setRecordWriteFailureScopeKey(recordScopeKey);
      setRecordActionNotice({
        scopeKey: recordScopeKey,
        message: "本章记录未能清除；已有记录状态没有改变。",
      });
      return;
    }
    setLoadedRecordState((current) => current?.scopeKey === recordScopeKey
      ? { ...current, generation, records: [], recordsAvailable: true }
      : current);
    recordGenerationRef.current.set(recordScopeKey, generation);
    setRecordWriteFailureScopeKey(null);
    setFrozenSavedSkipIds(new Set());
    setRecordActionNotice({
      scopeKey: recordScopeKey,
      message: "本章本机导出记录已清除；较早开始的写入不会恢复这些记录。",
    });
  }

  function createTransport(capturedMode: ExportSourceMode): MediaExportSessionTransport {
    const auth = authRef.current;
    const authState = auth.state;
    const apiBaseUrl = auth.services.apiBaseUrl;
    const props = propsRef.current;
    if (identityInvalidatedRef.current
      || authState.status !== "authenticated"
      || apiBaseUrl === null
      || auth.services.createMediaExportSession === undefined) {
      throw new Error("媒体导出需要已登录的 API 服务会话。");
    }
    const captured: CapturedPageScope = {
      userId: authState.user.id,
      sessionGeneration: auth.sessionGeneration,
      services: auth.services,
      chapterId: props.chapter.id,
      seriesId: props.chapter.series_id,
      seriesName: props.seriesName,
      chapterTitle: props.chapter.title,
      chapterUpdatedAt: props.chapter.updated_at,
      chapterContent: props.chapter.content,
      sourceRevision: currentSourceRevision(props),
      sourceMode: capturedMode,
      roughCutSnapshot: props.roughCutSnapshot,
    };
    const isCurrent = (): boolean => {
      const currentAuth = authRef.current;
      const currentProps = propsRef.current;
      if (
        !mountedRef.current
        || identityInvalidatedRef.current
        || currentAuth.state.status !== "authenticated"
        || currentAuth.state.user.id !== captured.userId
        || currentAuth.sessionGeneration !== captured.sessionGeneration
        || currentAuth.services !== captured.services
        || currentProps.chapter.id !== captured.chapterId
        || currentProps.chapter.series_id !== captured.seriesId
        || currentProps.seriesName !== captured.seriesName
        || currentProps.chapter.title !== captured.chapterTitle
        || currentProps.chapter.updated_at !== captured.chapterUpdatedAt
        || currentProps.chapter.content !== captured.chapterContent
        || currentSourceRevision(currentProps) !== captured.sourceRevision
        || sourceModeRef.current !== captured.sourceMode
      ) {
        return false;
      }
      if (captured.sourceMode === "rough-cut" && currentProps.roughCutSnapshot !== captured.roughCutSnapshot) {
        return false;
      }
      try {
        return currentProps.isSourceCurrent?.() !== false;
      } catch {
        return false;
      }
    };
    const transport = auth.services.createMediaExportSession(isCurrent);
    sessionTransportRef.current = transport;
    return transport;
  }

  function updateQueueItem(item: MediaExportQueueItem, transport: MediaExportSessionTransport): void {
    if (!transport.isCurrent()) return;
    setResultQueue((current) => {
      if (current === null || !transport.isCurrent()) return current;
      return {
        ...current,
        items: current.items.map((candidate) => candidate.id === item.id ? item : candidate),
      };
    });
  }

  function executeRun(
    queue: MediaExportQueue,
    sink: MediaExportSink,
    transport: MediaExportSessionTransport,
    controller: AbortController,
  ): void {
    const recordScopeAtStart = recordScope;
    const recordScopeKeyAtStart = recordScopeKey;
    const expectedRecordGeneration = currentRecordState?.generation ?? 0;
    const candidatesAtStart = new Map(recordCandidates.map((entry) => [entry.identity, entry.candidate]));
    const fingerprintsAtStart = new Map(fingerprintsByIdentity);
    const canRecordQueue = recordScopeAtStart !== null
      && recordScopeKeyAtStart !== null
      && queue.chapterId === recordScopeAtStart.chapterId
      && queue.seriesId === recordScopeAtStart.seriesId
      && queue.sourceKind === sourceModeRef.current;
    setFrozenQueue(queue);
    setResultQueue(queue);
    setPhase("running");
    setRunNotice(null);
    const run = startMediaExport(queue, {
      transport,
      sink,
      signal: controller.signal,
      onItem: (item) => updateQueueItem(item, transport),
      onSaved: (item) => {
        if (
          !canRecordQueue
          || recordScopeAtStart === null
          || recordScopeKeyAtStart === null
          || item.assetId === null
          || !transport.isCurrent()
          || (recordGenerationRef.current.get(recordScopeKeyAtStart) ?? expectedRecordGeneration)
            !== expectedRecordGeneration
        ) {
          return;
        }
        const identity = mediaExportRecordIdentity(item.sourceKind, item.assetId);
        const candidate = candidatesAtStart.get(identity);
        const fingerprint = fingerprintsAtStart.get(identity);
        if (candidate === undefined || candidate.sourceRevision !== queue.sourceRevision || fingerprint === undefined) {
          return;
        }
        const record = toSavedMediaExportRecord(candidate, fingerprint);
        if (record === null) return;
        const written = writeSavedMediaExportRecord(
          getMediaExportStorage(),
          recordScopeAtStart,
          record,
          expectedRecordGeneration,
        );
        if (!written) {
          const latestRecords = readSavedMediaExportRecords(getMediaExportStorage(), recordScopeAtStart);
          if (
            latestRecords.status === "ready"
            && latestRecords.value.generation !== expectedRecordGeneration
          ) {
            setLoadedRecordState((current) => current?.scopeKey === recordScopeKeyAtStart
              ? {
                ...current,
                generation: latestRecords.value.generation,
                records: latestRecords.value.records,
                recordsAvailable: true,
              }
              : current);
            return;
          }
          setRecordWriteFailureScopeKey(recordScopeKeyAtStart);
          setRecordActionNotice({
            scopeKey: recordScopeKeyAtStart,
            message: "文件已确认保存到目标目录，但本机记录写入失败。",
          });
          return;
        }
        setRecordWriteFailureScopeKey(null);
        setRecordActionNotice({
          scopeKey: recordScopeKeyAtStart,
          message: "本机已记下已保存的来源；这不代表文件目前仍存在。",
        });
        setLoadedRecordState((current) => {
          if (current?.scopeKey !== recordScopeKeyAtStart) return current;
          const savedIdentity = mediaExportRecordIdentity(record.sourceKind, record.assetId);
          return {
            ...current,
            records: [
              ...current.records.filter((saved) => (
                mediaExportRecordIdentity(saved.sourceKind, saved.assetId) !== savedIdentity
              )),
              record,
            ],
            recordsAvailable: true,
          };
        });
      },
      onUnauthorized: (error) => {
        if (!transport.isCurrent()) return;
        setErrorMessage("登录状态已失效，媒体导出已停止，请重新登录。");
        propsRef.current.onUnauthorized?.(error);
      },
    });
    runRef.current = run;
    void run.done.then((result) => {
      if (!transport.isCurrent()) return;
      setResultQueue(result);
      setPhase("complete");
    }).catch(() => {
      if (transport.isCurrent()) {
        setErrorMessage("媒体导出未能完成，请核对逐项状态后再处理。");
        setPhase("complete");
      }
    }).finally(() => {
      if (controllerRef.current === controller) controllerRef.current = null;
      if (runRef.current === run) runRef.current = null;
    });
  }

  async function startWithDirectory(queue: MediaExportQueue): Promise<void> {
    if (invalidateIfSessionChanged()) return;
    setErrorMessage(null);
    setPhase("preparing");
    let transport: MediaExportSessionTransport;
    try {
      transport = createTransport(queue.sourceKind === "rough-cut" ? "rough-cut" : "chapter");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法建立当前登录会话。");
      setPhase("confirmed");
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    const pickerWindow = window as unknown as DirectoryPickerWindow;
    try {
      const sink = await createDirectoryMediaExportSink({
        runId: queue.outputDirectoryId ?? queue.runId,
        context: { signal: controller.signal, isCurrent: transport.isCurrent },
        pickDirectory: async () => {
          const picker = pickerWindow.showDirectoryPicker;
          if (picker === undefined) {
            throw new DOMException("Directory picker is unavailable.", "NotSupportedError");
          }
          const directory = await picker.call(window, { mode: "readwrite" });
          if (controller.signal.aborted || !transport.isCurrent()) {
            throw new DOMException("The export scope changed.", "AbortError");
          }
          return directory;
        },
      });
      if (controller.signal.aborted || !transport.isCurrent()) return;
      executeRun(queue, sink, transport, controller);
    } catch (error) {
      if (!controller.signal.aborted && transport.isCurrent()) {
        setErrorMessage(error instanceof MediaExportSinkError ? error.message : "无法准备目标目录。");
        setPhase("confirmed");
      }
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }

  function startWithBrowser(queue: MediaExportQueue): void {
    if (invalidateIfSessionChanged()) return;
    setErrorMessage(null);
    let transport: MediaExportSessionTransport;
    try {
      transport = createTransport(queue.sourceKind === "rough-cut" ? "rough-cut" : "chapter");
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法建立当前登录会话。");
      setPhase("confirmed");
      return;
    }
    const controller = new AbortController();
    controllerRef.current = controller;
    executeRun(queue, createBrowserMediaExportSink({
      appendAnchor(anchor) {
        const dialog = focus.dialogRef.current;
        if (dialog === null) {
          throw new Error("The media export dialog is no longer mounted.");
        }
        dialog.appendChild(anchor as HTMLAnchorElement);
      },
    }), transport, controller);
  }

  function confirmProjection(): void {
    if (invalidateIfSessionChanged()) return;
    if (
      !recordCheckReady
      || activeProjection === null
      || !hasReadyItems(activeProjection)
    ) return;
    const skippedIds = skipConfirmedSaved
      ? activeProjection.items.filter((item) => (
        item.eligibility === "excluded"
        && item.assetId !== null
        && savedRecordIdentities.has(mediaExportRecordIdentity(item.sourceKind, item.assetId))
      )).map((item) => item.id)
      : [];
    setFrozenQueue(activeProjection);
    setFrozenSavedSkipIds(new Set(skippedIds));
    setResultQueue(null);
    setConfirmationChecked(false);
    setPhase("confirmed");
    setErrorMessage(null);
  }

  function selectFrame(frameIndex: number, checked: boolean): void {
    setSelectedFrameIndexes((current) => {
      const next = new Set(current);
      if (checked) next.add(frameIndex);
      else next.delete(frameIndex);
      return next;
    });
    setErrorMessage(null);
  }

  function makeRetryQueue(): MediaExportQueue | null {
    if (resultQueue === null || retrySelectedIds.size === 0 || !retryConfirmed) return null;
    const outputDirectoryId = createRunId();
    try {
      const queue = createFailedMediaExportRetryQueue(resultQueue, {
        itemIds: Array.from(retrySelectedIds),
        outputDirectoryId,
        confirmedNewTarget: true,
      });
      setFrozenQueue(queue);
      setResultQueue(queue);
      setConfirmationChecked(true);
      setErrorMessage(null);
      return queue;
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "无法创建失败项重试队列。");
      return null;
    }
  }

  async function retryWithDirectory(): Promise<void> {
    const queue = makeRetryQueue();
    if (queue === null) return;
    await startWithDirectory(queue);
    setRetrySelectedIds(new Set());
    setRetryConfirmed(false);
  }

  function retryWithBrowser(): void {
    const queue = makeRetryQueue();
    if (queue === null) return;
    startWithBrowser(queue);
    setRetrySelectedIds(new Set());
    setRetryConfirmed(false);
  }

  const displayedItems = queueForDisplay?.items ?? [];
  const isReviewing = phase === "review";
  const isActive = phase === "preparing" || phase === "running";
  const confirmationText = sourceMode === "rough-cut"
    ? "我已核对当前已保存粗剪的导出清单与顺序。"
    : "我已核对本次选择的分镜与导出清单。";

  return (
    <div className="media-export-backdrop">
      <section
        aria-labelledby="media-export-title"
        aria-modal="true"
        className="media-export-dialog"
        onKeyDown={(event) => event.stopPropagation()}
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="media-export-header">
          <div>
            <p className="media-export-eyebrow">逐项读取并保存视频文件</p>
            <h2 id="media-export-title">导出媒体素材</h2>
            <p>{chapter.title} · {seriesName}</p>
          </div>
          <button
            aria-label={isActive ? "停止当前导出并关闭" : "关闭媒体导出"}
            className="media-export-close"
            onClick={() => focus.dismiss()}
            type="button"
          >
            ×
          </button>
        </header>

        {state.status === "restoring" && (
          <p role="status">正在验证登录状态…</p>
        )}
        {state.status === "restore-error" && (
          <p role="alert">当前登录状态无法确认，请稍后重新打开导出。</p>
        )}
        {state.status === "anonymous" && (
          <p role="alert">请先登录，再导出媒体素材。</p>
        )}
        {state.status === "authenticated" && services.apiBaseUrl === null && (
          <p role="alert">当前运行模式没有连接媒体导出 API。</p>
        )}
        {errorMessage !== null && <p className="media-export-error" role="alert">{errorMessage}</p>}
        {runNotice !== null && <p className="media-export-notice" role="status">{runNotice}</p>}
        {localRecordNotice !== null && <p className="media-export-notice" role="status">{localRecordNotice}</p>}
        {recordActionNotice?.scopeKey === recordScopeKey && (
          <p className="media-export-notice" role="status">{recordActionNotice.message}</p>
        )}

        {isReviewing && state.status === "authenticated" && services.apiBaseUrl !== null && (
          <>
            <div className="media-export-source-choice" aria-label="导出来源">
              <strong>{sourceMode === "chapter" ? "导出来源：本章逐镜" : "导出来源：已保存粗剪"}</strong>
              {sourceMode === "rough-cut" && roughCutMessage !== null && <p role="status">{roughCutMessage}</p>}
              {sourceMode === "rough-cut" && (roughCutSnapshot === null || roughCutSnapshot === undefined)
                ? <p>当前没有已保存的粗剪快照。</p>
                : null}
            </div>

            <div className="media-export-record-controls">
              <label className="media-export-confirmation">
                <input
                  checked={skipConfirmedSaved}
                  disabled={currentRecordState === null || !currentRecordState.recordsAvailable}
                  onChange={(event) => changeSkipSavedPreference(event.currentTarget.checked)}
                  type="checkbox"
                />
                跳过本机记录中已确认保存到目标目录的项目
              </label>
              <button
                className="text-button"
                disabled={currentRecordState === null || !currentRecordState.recordsAvailable || currentRecordState.records.length === 0}
                onClick={clearCurrentChapterRecords}
                type="button"
              >
                清除本章本机导出记录（{currentRecordState?.records.length ?? 0}）
              </button>
              <span>仅在目标目录文件已成功写入并关闭后记录；浏览器下载“已发起”不算已保存。记录不证明文件仍存在。</span>
            </div>

            {sourceMode === "chapter" && (
              <div className="media-export-summary" aria-live="polite">
                <span>可导出 {selectedIndexes.filter((index) => (
                  chapterAllQueue.items[index]?.eligibility === "ready"
                )).length}/{chapterReadyCount}</span>
                <span>缺少视频 {chapterMissingCount}</span>
                <span>身份重复 {chapterDuplicateCount}</span>
                <span>已排除 {chapterExcludedCount}</span>
                {skipConfirmedSaved && <span>已跳过本机确认保存 {chapterSkippedSavedCount}</span>}
              </div>
            )}
            {sourceMode === "rough-cut" && roughCutProjection !== null && (
              <div className="media-export-summary" aria-live="polite">
                <span>可导出 {roughCutProjectionForDisplay?.items.filter((item) => item.eligibility === "ready").length ?? 0}</span>
                <span>缺少视频 {roughCutProjectionForDisplay?.items.filter((item) => item.eligibility === "missing").length ?? 0}</span>
                <span>仍在等待 {roughCutProjectionForDisplay?.items.filter((item) => item.eligibility === "pending").length ?? 0}</span>
                <span>身份重复 {roughCutProjectionForDisplay?.items.filter((item) => item.eligibility === "duplicate").length ?? 0}</span>
                <span>已排除 {roughCutProjectionForDisplay?.items.filter((item) => item.eligibility === "excluded").length ?? 0}</span>
                {skipConfirmedSaved && <span>本机确认保存 {savedRecordIdentities.size} 项已跳过</span>}
              </div>
            )}

            {sourceMode === "chapter" && chapterAllQueue.items.length === 0
              ? <p>本章没有可检查的分镜。</p>
              : null}
            {sourceMode === "rough-cut" && roughCutProjection === null
              ? <p>请先保存粗剪，再按本次保存的顺序确认导出清单。</p>
              : null}
            <ol aria-label="媒体导出预览" className="media-export-items">
              {(sourceMode === "chapter" ? chapterAllQueue.items : roughCutProjectionForDisplay?.items ?? []).map((item) => {
                const hasSavedRecord = item.assetId !== null
                  && savedRecordIdentities.has(mediaExportRecordIdentity(item.sourceKind, item.assetId));
                const skippedByPreference = skipConfirmedSaved && hasSavedRecord;
                const selected = sourceMode === "chapter" && selectedIndexes.includes(item.frameIndex);
                return (
                  <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                    {sourceMode === "chapter" && item.eligibility === "ready" && (
                      <label className="media-export-item-select">
                        <input
                          aria-label={`选择分镜 ${item.frameIndex + 1}`}
                          checked={selected}
                          disabled={skippedByPreference}
                          onChange={(event) => selectFrame(item.frameIndex, event.currentTarget.checked)}
                          type="checkbox"
                        />
                      </label>
                    )}
                    <div className="media-export-item-main">
                      <strong>{item.filename}</strong>
                      <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                    </div>
                    <span className={`media-export-status media-export-status-${item.eligibility}`}>
                      {reviewStatus(item, selected || sourceMode === "rough-cut", hasSavedRecord, skipConfirmedSaved)}
                    </span>
                  </li>
                );
              })}
            </ol>
            <div className="media-export-actions">
              <button
                className="primary-button"
                disabled={!recordCheckReady || activeProjection === null || !hasReadyItems(activeProjection)}
                onClick={confirmProjection}
                type="button"
              >
                确认导出清单
              </button>
            </div>
          </>
        )}

        {phase === "confirmed" && frozenQueue !== null && (
          <>
            <p className="media-export-confirmation-title">
              已确认 {frozenQueue.items.filter((item) => item.eligibility === "ready").length} 个可导出视频。开始后会按此顺序逐项读取。
            </p>
            <label className="media-export-confirmation">
              <input
                checked={confirmationChecked}
                onChange={(event) => setConfirmationChecked(event.currentTarget.checked)}
                type="checkbox"
              />
              {confirmationText}
            </label>
            <ol aria-label="已确认的导出清单" className="media-export-items">
              {frozenQueue.items.map((item) => (
                <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                  <div className="media-export-item-main">
                    <strong>{item.filename}</strong>
                    <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                  </div>
                  <span className={`media-export-status media-export-status-${item.eligibility}`}>
                    {frozenSavedSkipIds.has(item.id)
                      ? "本机记录确认已保存，已跳过；文件仍需自行核对"
                      : statusForEligibility(item.eligibility, item.eligibility === "ready")}
                  </span>
                </li>
              ))}
            </ol>
            <div className="media-export-actions">
              {canUseDirectoryPicker() && (
                <button
                  className="primary-button"
                  disabled={!confirmationChecked}
                  onClick={() => { void startWithDirectory(frozenQueue); }}
                  type="button"
                >
                  选择目录并开始
                </button>
              )}
              <button
                className="secondary-button"
                disabled={!confirmationChecked}
                onClick={() => startWithBrowser(frozenQueue)}
                type="button"
              >
                使用浏览器下载
              </button>
              <button className="secondary-button" onClick={() => setPhase("review")} type="button">
                返回修改选择
              </button>
            </div>
            {!canUseDirectoryPicker() && (
              <p className="media-export-notice" role="status">
                当前浏览器不支持目录授权；浏览器下载只表示已发起，不代表文件已落盘。
              </p>
            )}
          </>
        )}

        {phase === "preparing" && <p role="status">正在准备目标目录，请完成浏览器的目录选择。</p>}
        {phase === "running" && (
          <div className="media-export-run-controls">
            <p role="status">正在按已确认的顺序导出。</p>
            <button
              className="secondary-button"
              onClick={() => {
                runRef.current?.stopAfterCurrent();
                setRunNotice("当前文件结束后将停止剩余队列；关闭对话框会立即中止当前文件。");
              }}
              type="button"
            >
              停止剩余文件
            </button>
          </div>
        )}
        {isActive && queueForDisplay !== null && (
          <ol aria-label="媒体导出逐项状态" className="media-export-items">
            {displayedItems.map((item) => (
              <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                <div className="media-export-item-main">
                  <strong>{item.filename}</strong>
                  <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                </div>
                <span className={`media-export-status media-export-status-${item.state}`}>
                  {statusForItem(item, item.eligibility === "ready")}
                </span>
              </li>
            ))}
          </ol>
        )}
        {phase === "complete" && resultQueue !== null && (
          <>
            <p role="status">本次队列已结束。浏览器下载项目只确认已发起，目录项目仅在写入及关闭成功后标记已保存。</p>
            <ol aria-label="媒体导出逐项状态" className="media-export-items">
              {resultQueue.items.map((item) => (
                <li className="media-export-item" data-export-item-id={item.id} key={item.id}>
                  <div className="media-export-item-main">
                    <strong>{item.filename}</strong>
                    <span>{frameDescription(item, chapter, roughCutSnapshot, sourceMode)}</span>
                  </div>
                  <span className={`media-export-status media-export-status-${item.state}`}>
                    {statusForItem(item, item.eligibility === "ready")}
                  </span>
                </li>
              ))}
            </ol>
            {failedItems.length > 0 && (
              <section aria-label="手动重试已知失败项" className="media-export-retry">
                <h3>只重试确认失败的项目</h3>
                <p>成功、已发起下载、结果未知和已停止项目不会重放。重试需要确认新的目标。</p>
                {failedItems.map((item) => (
                  <label className="media-export-retry-item" key={item.id}>
                    <input
                      aria-label={`选择失败项 ${item.filename}`}
                      checked={retrySelectedIds.has(item.id)}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        setRetrySelectedIds((current) => {
                          const next = new Set(current);
                          if (checked) next.add(item.id);
                          else next.delete(item.id);
                          return next;
                        });
                        setRetryConfirmed(false);
                      }}
                      type="checkbox"
                    />
                    <span>{item.filename} · {item.message}</span>
                  </label>
                ))}
                <label className="media-export-confirmation">
                  <input
                    checked={retryConfirmed}
                    disabled={retrySelectedIds.size === 0}
                    onChange={(event) => setRetryConfirmed(event.currentTarget.checked)}
                    type="checkbox"
                  />
                  我确认只重试所选失败项，并使用一个新的目标目录或下载批次。
                </label>
                <div className="media-export-actions">
                  {canUseDirectoryPicker() && (
                    <button
                      className="primary-button"
                      disabled={!retryConfirmed || retrySelectedIds.size === 0}
                      onClick={() => { void retryWithDirectory(); }}
                      type="button"
                    >
                      选择新目录并重试
                    </button>
                  )}
                  <button
                    className="secondary-button"
                    disabled={!retryConfirmed || retrySelectedIds.size === 0}
                    onClick={retryWithBrowser}
                    type="button"
                  >
                    以新下载批次重试
                  </button>
                </div>
              </section>
            )}
          </>
        )}

      </section>
    </div>
  );
}
