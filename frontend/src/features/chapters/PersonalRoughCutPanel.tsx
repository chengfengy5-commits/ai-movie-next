import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import {
  normalizeApiError,
  type PersonalRoughCutPlaybackReader,
  type WorkspaceServices,
} from "../../shared/api/services";
import type { PersonalRoughCutFrame, PersonalRoughCutSnapshot } from "../../shared/api/personalRoughCut";
import { findRoughCutFramePosition } from "./roughCutFrameNavigation";
import { PersonalRoughCutEditor } from "./PersonalRoughCutEditor";
import { PersonalRoughCutPlayback } from "./PersonalRoughCutPlayback";
import { usePersonalRoughCutEditor } from "./usePersonalRoughCutEditor";
import { inspectRoughCutSource } from "./personal-production/roughCutEdit";
import {
  countRoughCutStatusEntries,
  filterRoughCutStatusEntries,
  indexRoughCutFrames,
  type RoughCutStatusFilter,
} from "./roughCutStatusFilter";

export interface PersonalRoughCutReadIdentity {
  snapshot: PersonalRoughCutSnapshot | null;
  requestGeneration: number;
}

export interface PersonalRoughCutFrameTarget {
  position: number;
  chapter: Chapter;
  snapshot: PersonalRoughCutSnapshot;
  row: PersonalRoughCutFrame;
  assets: StoryboardAsset[];
  assetSnapshotToken: object;
  assetRequestGeneration: number;
  roughCutRequestGeneration: number;
  isCurrent(): boolean;
}

interface PersonalRoughCutPanelProps {
  chapter: Chapter;
  seriesId: string;
  services: WorkspaceServices;
  userId: string;
  onClose(): void;
  onUnauthorized(): void;
  onExportSavedSnapshot?(snapshot: PersonalRoughCutSnapshot, isCurrent: () => boolean): void;
  assets?: StoryboardAsset[] | null;
  assetSnapshotToken?: object | null;
  assetRequestGeneration?: number;
  assetDirectoryStatus?: "loading" | "ready" | "error";
  onLocateFrame?(target: PersonalRoughCutFrameTarget): boolean;
  onFrameNavigationInvalidated?(identity: PersonalRoughCutReadIdentity): void;
}

interface PanelScope {
  chapter: Chapter;
  seriesId: string;
  services: WorkspaceServices;
  userId: string;
}

interface RoughCutDirectoryIdentity {
  readonly assets: StoryboardAsset[] | null;
  readonly token: object | null;
  readonly requestGeneration: number;
  readonly status: "loading" | "ready" | "error";
}

interface RoughCutPortIdentity {
  readonly services: WorkspaceServices;
  readonly getPersonalRoughCut: WorkspaceServices["getPersonalRoughCut"];
  readonly getPersonalRoughCutForPlayback: WorkspaceServices["getPersonalRoughCutForPlayback"];
  readonly savePersonalRoughCut: WorkspaceServices["savePersonalRoughCut"];
  readonly apiBaseUrl: WorkspaceServices["apiBaseUrl"];
}

function sameDirectoryIdentity(
  left: RoughCutDirectoryIdentity,
  right: RoughCutDirectoryIdentity,
): boolean {
  return left.assets === right.assets
    && left.token === right.token
    && left.requestGeneration === right.requestGeneration
    && left.status === right.status;
}

function samePortIdentity(left: RoughCutPortIdentity, right: RoughCutPortIdentity): boolean {
  return left.services === right.services
    && left.getPersonalRoughCut === right.getPersonalRoughCut
    && left.getPersonalRoughCutForPlayback === right.getPersonalRoughCutForPlayback
    && left.savePersonalRoughCut === right.savePersonalRoughCut
    && left.apiBaseUrl === right.apiBaseUrl;
}

type ReadState =
  | {
      status: "loading";
      requestGeneration: number;
      portIdentity: RoughCutPortIdentity;
      directoryIdentity: RoughCutDirectoryIdentity;
    }
  | {
      status: "ready";
      snapshot: PersonalRoughCutSnapshot;
      reader: PersonalRoughCutPlaybackReader | null;
      requestGeneration: number;
      portIdentity: RoughCutPortIdentity;
      directoryIdentity: RoughCutDirectoryIdentity;
    }
  | {
      status: "error";
      error: ApiError;
      requestGeneration: number;
      portIdentity: RoughCutPortIdentity;
      directoryIdentity: RoughCutDirectoryIdentity;
    };

interface FilterSelection {
  snapshot: PersonalRoughCutSnapshot;
  requestGeneration: number;
  filter: RoughCutStatusFilter;
}

const FILTER_OPTIONS: readonly { value: RoughCutStatusFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "included", label: "已纳入" },
  { value: "excluded", label: "已排除" },
  { value: "pending", label: "待安排" },
];

function sameScope(left: PanelScope, right: PanelScope): boolean {
  return left.chapter === right.chapter
    && left.seriesId === right.seriesId
    && left.services === right.services
    && left.userId === right.userId;
}

function errorPresentation(error: ApiError): { title: string; message: string } {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const membership = ["会员", "membership", "premium", "subscription"].some((word) => detail.includes(word));
    return {
      title: membership ? "当前账号暂不可查看粗剪" : "没有访问权限",
      message: error.detail ?? (membership ? "当前账号的访问资格暂不可用。" : "当前账号无权查看此章节草稿。"),
    };
  }
  if (error.status === 404) {
    return { title: "章节草稿不存在", message: error.detail ?? "章节可能已被移除。" };
  }
  if (error.status === 422) {
    return { title: "请求未通过校验", message: error.detail ?? "本地服务无法识别当前章节。" };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "暂时无法读取粗剪", message: "本地服务暂时不可用，请稍后重试。" };
  }
  if (error.kind === "timeout") {
    return { title: "请求超时", message: "本地服务响应较慢，请重试。" };
  }
  if (error.kind === "network") {
    return { title: "无法连接本地服务", message: "请确认本地服务正在运行，然后重试。" };
  }
  if (error.kind === "invalid-response") {
    return { title: "服务返回的数据无法识别", message: error.message };
  }
  return { title: "暂时无法读取粗剪", message: error.detail ?? error.message };
}

function sourceAvailability(frame: PersonalRoughCutSnapshot["frames"][number]): string {
  if (frame.missing_reason !== null) {
    return frame.missing_reason;
  }
  if (frame.preview_url === null) {
    return "当前没有可用视频。";
  }
  return "读取时存在视频引用；本面板不会加载媒体。";
}

export function PersonalRoughCutPanel({
  chapter,
  seriesId,
  services,
  userId,
  onClose,
  onUnauthorized,
  onExportSavedSnapshot,
  assets = null,
  assetSnapshotToken = null,
  assetRequestGeneration = 0,
  assetDirectoryStatus = "loading",
  onLocateFrame,
  onFrameNavigationInvalidated,
}: PersonalRoughCutPanelProps) {
  const directoryInput: RoughCutDirectoryIdentity = {
    assets,
    token: assetSnapshotToken,
    requestGeneration: assetRequestGeneration,
    status: assetDirectoryStatus,
  };
  const directoryIdentityRef = useRef<RoughCutDirectoryIdentity | null>(null);
  if (directoryIdentityRef.current === null || !sameDirectoryIdentity(directoryIdentityRef.current, directoryInput)) {
    directoryIdentityRef.current = directoryInput;
  }
  const directoryIdentity = directoryIdentityRef.current!;
  const portInput: RoughCutPortIdentity = {
    services,
    getPersonalRoughCut: services.getPersonalRoughCut,
    getPersonalRoughCutForPlayback: services.getPersonalRoughCutForPlayback,
    savePersonalRoughCut: services.savePersonalRoughCut,
    apiBaseUrl: services.apiBaseUrl,
  };
  const portIdentityRef = useRef<RoughCutPortIdentity | null>(null);
  if (portIdentityRef.current === null || !samePortIdentity(portIdentityRef.current, portInput)) {
    portIdentityRef.current = portInput;
  }
  const portIdentity = portIdentityRef.current!;
  const currentDirectoryIdentityRef = useRef(directoryIdentity);
  currentDirectoryIdentityRef.current = directoryIdentity;
  const currentPortIdentityRef = useRef(portIdentity);
  currentPortIdentityRef.current = portIdentity;

  const initialScope = useRef<PanelScope | null>(null);
  if (initialScope.current === null) {
    initialScope.current = { chapter, seriesId, services, userId };
  }
  const boundScope = initialScope.current;
  const currentScope = useRef<PanelScope>(boundScope);
  currentScope.current = { chapter, seriesId, services, userId };

  const [scopeInvalidated, setScopeInvalidated] = useState(false);
  const scopeInvalidatedRef = useRef(false);
  if (scopeInvalidated) {
    scopeInvalidatedRef.current = true;
  } else if (!sameScope(boundScope, currentScope.current)) {
    scopeInvalidatedRef.current = true;
    setScopeInvalidated(true);
  }

  const requestGeneration = useRef(0);
  const closedRef = useRef(false);
  const [readState, setReadState] = useState<ReadState>(() => ({
    status: "loading",
    requestGeneration: 0,
    portIdentity,
    directoryIdentity,
  }));
  const [filterSelection, setFilterSelection] = useState<FilterSelection | null>(null);
  const readStateRef = useRef(readState);
  readStateRef.current = readState;
  const renderedReadState = readState;
  const renderedRequestGeneration = requestGeneration.current;
  const [reloadGeneration, setReloadGeneration] = useState(0);
  const [navigationNotice, setNavigationNotice] = useState<string | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const stopPlaybackRef = useRef<(() => void) | null>(null);
  const registerPlaybackStop = useCallback((stop: (() => void) | null) => {
    stopPlaybackRef.current = stop;
  }, []);
  const assetNavigationSnapshotRef = useRef({ assets, assetSnapshotToken, assetRequestGeneration });
  assetNavigationSnapshotRef.current = { assets, assetSnapshotToken, assetRequestGeneration };
  const activeReadIdentityRef = useRef<{
    portIdentity: RoughCutPortIdentity;
    directoryIdentity: RoughCutDirectoryIdentity;
  } | null>(null);
  const unauthorizedCallback = useRef(onUnauthorized);
  const frameNavigationInvalidatedCallback = useRef(onFrameNavigationInvalidated);
  unauthorizedCallback.current = onUnauthorized;
  frameNavigationInvalidatedCallback.current = onFrameNavigationInvalidated;

  const hasCurrentCanonicalRead = readState.status === "ready"
    && readState.portIdentity === portIdentity
    && readState.requestGeneration === requestGeneration.current;
  const hasCurrentPlaybackRead = hasCurrentCanonicalRead
    && readState.directoryIdentity === directoryIdentity;

  useLayoutEffect(() => {
    const activeReadIdentity = activeReadIdentityRef.current;
    if (
      activeReadIdentity !== null
      && (
        activeReadIdentity.portIdentity !== portIdentity
        || activeReadIdentity.directoryIdentity !== directoryIdentity
      )
    ) {
      activeController.current?.abort();
      activeController.current = null;
      activeReadIdentityRef.current = null;
    }
    if (!hasCurrentPlaybackRead) {
      stopPlaybackRef.current?.();
    }
  }, [directoryIdentity, hasCurrentPlaybackRead, portIdentity]);

  const roughCutEditor = usePersonalRoughCutEditor({
    chapterId: boundScope.chapter.id,
    save: boundScope.services.savePersonalRoughCut,
    sourceCheck: readState.status === "ready" && hasCurrentCanonicalRead
      ? inspectRoughCutSource(boundScope.chapter, readState.snapshot, assets, assetDirectoryStatus)
      : { valid: false, reason: "当前粗剪草稿尚未读取。", sourceIds: [] },
    sourceIdentity: { assets, token: assetSnapshotToken, requestGeneration: assetRequestGeneration, status: assetDirectoryStatus },
    isScopeCurrent: () => !closedRef.current && !scopeInvalidatedRef.current && sameScope(boundScope, currentScope.current),
    onBeforeSave: () => {
      stopPlaybackRef.current?.();
      invalidateCurrentFrameNavigation();
      setNavigationNotice(null);
    },
    onSaved: (snapshot, generation) => {
      stopPlaybackRef.current?.();
      const currentRead = readStateRef.current;
      if (
        currentRead.status !== "ready"
        || currentRead.portIdentity !== currentPortIdentityRef.current
        || currentRead.directoryIdentity !== currentDirectoryIdentityRef.current
      ) {
        return;
      }
      const nextState: ReadState = {
        status: "ready",
        snapshot,
        reader: currentRead.reader,
        requestGeneration: generation,
        portIdentity: currentRead.portIdentity,
        directoryIdentity: currentRead.directoryIdentity,
      };
      readStateRef.current = nextState;
      setReadState(nextState);
    },
    onUnauthorized: () => unauthorizedCallback.current(),
  });
  const renderedEditorGeneration = roughCutEditor.editorGeneration;
  const renderedDraftGeneration = roughCutEditor.draftGeneration;

  useEffect(() => {
    if (scopeInvalidated) {
      return undefined;
    }

    const portIdentityAtStart = portIdentity;
    const directoryIdentityAtStart = directoryIdentity;
    const generation = ++requestGeneration.current;
    if (!roughCutEditor.beginRead(generation)) {
      return undefined;
    }
    const controller = new AbortController();
    activeController.current = controller;
    activeReadIdentityRef.current = {
      portIdentity: portIdentityAtStart,
      directoryIdentity: directoryIdentityAtStart,
    };
    let mounted = true;
    const isCurrent = () => mounted
      && !closedRef.current
      && !controller.signal.aborted
      && requestGeneration.current === generation
      && sameScope(boundScope, currentScope.current)
      && currentPortIdentityRef.current === portIdentityAtStart
      && currentDirectoryIdentityRef.current === directoryIdentityAtStart;

    queueMicrotask(() => {
      if (!isCurrent()) {
        return;
      }
      const loadingState: ReadState = {
        status: "loading",
        requestGeneration: generation,
        portIdentity: portIdentityAtStart,
        directoryIdentity: directoryIdentityAtStart,
      };
      stopPlaybackRef.current?.();
      readStateRef.current = loadingState;
      setReadState(loadingState);
      const readForPlayback = portIdentityAtStart.getPersonalRoughCutForPlayback;
      const pendingRead = readForPlayback === undefined
        ? portIdentityAtStart.getPersonalRoughCut.call(
          portIdentityAtStart.services,
          boundScope.chapter.id,
          controller.signal,
        )
          .then((snapshot) => ({ snapshot, reader: null }))
        : readForPlayback.call(portIdentityAtStart.services, boundScope.chapter.id, controller.signal);
      void pendingRead.then(({ snapshot, reader }) => {
        if (isCurrent()) {
          roughCutEditor.acceptRead(snapshot, generation);
          const boundReader = reader === null ? null : Object.freeze({
            isCurrent: () => isCurrent() && reader.isCurrent(),
            read: async (previewUrl: string, signal: AbortSignal) => {
              if (!isCurrent() || !reader.isCurrent() || signal.aborted) {
                throw new ApiError("aborted", "Request was cancelled.");
              }
              try {
                const blob = await reader.read(previewUrl, signal);
                if (!isCurrent() || !reader.isCurrent() || signal.aborted) {
                  throw new ApiError("aborted", "Request was cancelled.");
                }
                return blob;
              } catch (cause) {
                if (!isCurrent() || !reader.isCurrent() || signal.aborted) {
                  throw new ApiError("aborted", "Request was cancelled.");
                }
                throw cause;
              }
            },
          });
          const nextState: ReadState = {
            status: "ready",
            snapshot,
            reader: boundReader,
            requestGeneration: generation,
            portIdentity: portIdentityAtStart,
            directoryIdentity: directoryIdentityAtStart,
          };
          readStateRef.current = nextState;
          setReadState(nextState);
        }
      }).catch((cause: unknown) => {
        if (!isCurrent()) {
          return;
        }
        const error = normalizeApiError(cause);
        if (error.status === 401) {
          unauthorizedCallback.current();
          return;
        }
        const errorState: ReadState = {
          status: "error",
          error,
          requestGeneration: generation,
          portIdentity: portIdentityAtStart,
          directoryIdentity: directoryIdentityAtStart,
        };
        readStateRef.current = errorState;
        setReadState(errorState);
      });
    });

    return () => {
      mounted = false;
      requestGeneration.current += 1;
      controller.abort();
      if (activeController.current === controller) {
        activeController.current = null;
      }
      if (activeReadIdentityRef.current?.portIdentity === portIdentityAtStart) {
        activeReadIdentityRef.current = null;
      }
    };
  }, [reloadGeneration, scopeInvalidated, roughCutEditor.acceptRead, roughCutEditor.beginRead]);

  function invalidateCurrentFrameNavigation() {
    const currentRead = readStateRef.current;
    frameNavigationInvalidatedCallback.current?.({
      snapshot: currentRead.status === "ready"
        && currentRead.portIdentity === currentPortIdentityRef.current
        && currentRead.directoryIdentity === currentDirectoryIdentityRef.current
        ? currentRead.snapshot
        : null,
      requestGeneration: requestGeneration.current,
    });
  }

  function retryRead() {
    if (
      closedRef.current
      || scopeInvalidatedRef.current
      || !sameScope(boundScope, currentScope.current)
      || requestGeneration.current !== renderedRequestGeneration
      || readStateRef.current !== renderedReadState
      || !roughCutEditor.isCurrentPanelAction(
        renderedRequestGeneration,
        renderedEditorGeneration,
        renderedDraftGeneration,
      )
    ) {
      return;
    }

    const nextGeneration = renderedRequestGeneration + 1;
    if (!roughCutEditor.beginRead(nextGeneration)) {
      return;
    }
    invalidateCurrentFrameNavigation();
    stopPlaybackRef.current?.();
    if (closedRef.current || scopeInvalidatedRef.current || !sameScope(boundScope, currentScope.current)) {
      return;
    }
    requestGeneration.current = nextGeneration;
    activeController.current?.abort();
    activeController.current = null;
    activeReadIdentityRef.current = null;
    setNavigationNotice(null);
    const nextState: ReadState = {
      status: "loading",
      requestGeneration: nextGeneration,
      portIdentity,
      directoryIdentity,
    };
    readStateRef.current = nextState;
    setReadState(nextState);
    setReloadGeneration((generation) => generation + 1);
  }

  if (scopeInvalidated || !sameScope(boundScope, currentScope.current)) {
    return null;
  }

  const presentation = readState.status === "error" ? errorPresentation(readState.error) : null;
  const canonicalSnapshot = readState.status === "ready" && hasCurrentCanonicalRead
    ? readState.snapshot
    : null;
  const snapshot = canonicalSnapshot;
  const indexedFrames = snapshot === null ? [] : indexRoughCutFrames(snapshot.frames);
  const statusCounts = countRoughCutStatusEntries(indexedFrames);
  const currentFilter = readState.status === "ready"
    && hasCurrentCanonicalRead
    && filterSelection !== null
    && filterSelection.snapshot === readState.snapshot
    && filterSelection.requestGeneration === readState.requestGeneration
    && readState.requestGeneration === requestGeneration.current
    ? filterSelection.filter
    : "all";
  const visibleFrames = filterRoughCutStatusEntries(indexedFrames, currentFilter);

  function chooseFilter(
    expectedSnapshot: PersonalRoughCutSnapshot,
    expectedGeneration: number,
    filter: RoughCutStatusFilter,
  ) {
    const currentRead = readStateRef.current;
    if (
      scopeInvalidatedRef.current
      || !sameScope(boundScope, currentScope.current)
      || requestGeneration.current !== expectedGeneration
      || currentRead.status !== "ready"
      || currentRead.snapshot !== expectedSnapshot
      || currentRead.requestGeneration !== expectedGeneration
      || currentRead.portIdentity !== currentPortIdentityRef.current
    ) {
      return;
    }
    if (
      filterSelection?.snapshot === expectedSnapshot
      && filterSelection.requestGeneration === expectedGeneration
      && filterSelection.filter === filter
    ) {
      return;
    }

    setFilterSelection({
      snapshot: expectedSnapshot,
      requestGeneration: expectedGeneration,
      filter,
    });
  }

  function exportSavedSnapshot(): void {
    if (
      snapshot === null
      || !snapshot.saved
      || readState.status !== "ready"
      || !hasCurrentCanonicalRead
      || roughCutEditor.editing
      || roughCutEditor.isSaving
      || roughCutEditor.isLocked
      || onExportSavedSnapshot === undefined
    ) {
      return;
    }
    const expectedSnapshot = snapshot;
    const expectedGeneration = readState.requestGeneration;
    const expectedPortIdentity = readState.portIdentity;
    const expectedDirectoryIdentity = readState.directoryIdentity;
    const expectedEditorGeneration = roughCutEditor.editorGeneration;
    const expectedDraftGeneration = roughCutEditor.draftGeneration;
    const isCurrent = (): boolean => (
      !closedRef.current
      && !scopeInvalidatedRef.current
      && sameScope(boundScope, currentScope.current)
      && requestGeneration.current === expectedGeneration
      && readStateRef.current.status === "ready"
      && readStateRef.current.snapshot === expectedSnapshot
      && readStateRef.current.requestGeneration === expectedGeneration
      && readStateRef.current.portIdentity === expectedPortIdentity
      && readStateRef.current.directoryIdentity === expectedDirectoryIdentity
      && currentPortIdentityRef.current === expectedPortIdentity
      && currentDirectoryIdentityRef.current === expectedDirectoryIdentity
      && roughCutEditor.isCurrentNavigationRead(expectedGeneration)
      && roughCutEditor.isCurrentPanelAction(
        expectedGeneration,
        expectedEditorGeneration,
        expectedDraftGeneration,
      )
    );
    if (isCurrent()) {
      onExportSavedSnapshot(expectedSnapshot, isCurrent);
    }
  }

  function roughCutTarget(
    currentSnapshot: PersonalRoughCutSnapshot,
    row: PersonalRoughCutFrame,
    position: number,
  ): PersonalRoughCutFrameTarget | null {
    if (assets === null || assetSnapshotToken === null || onLocateFrame === undefined) {
      return null;
    }
    const roughCutGeneration = requestGeneration.current;
    const currentAssets = assets;
    const assetToken = assetSnapshotToken;
    const assetGeneration = assetRequestGeneration;
    return {
      position,
      chapter: boundScope.chapter,
      snapshot: currentSnapshot,
      row,
      assets: currentAssets,
      assetSnapshotToken: assetToken,
      assetRequestGeneration: assetGeneration,
      roughCutRequestGeneration: roughCutGeneration,
      isCurrent: () => (
        !closedRef.current
        && !scopeInvalidatedRef.current
        && sameScope(boundScope, currentScope.current)
        && requestGeneration.current === roughCutGeneration
        && roughCutEditor.isCurrentNavigationRead(roughCutGeneration)
        && readStateRef.current.status === "ready"
        && readStateRef.current.snapshot === currentSnapshot
        && readStateRef.current.portIdentity === currentPortIdentityRef.current
        && readStateRef.current.directoryIdentity === currentDirectoryIdentityRef.current
        && assetNavigationSnapshotRef.current.assets === currentAssets
        && assetNavigationSnapshotRef.current.assetSnapshotToken === assetToken
        && assetNavigationSnapshotRef.current.assetRequestGeneration === assetGeneration
        && findRoughCutFramePosition(boundScope.chapter, boundScope.seriesId, currentSnapshot, row, currentAssets) === position
      ),
    };
  }

  function closePanel() {
    if (
      closedRef.current
      || scopeInvalidatedRef.current
      || !sameScope(boundScope, currentScope.current)
      || requestGeneration.current !== renderedRequestGeneration
      || readStateRef.current !== renderedReadState
      || !roughCutEditor.isCurrentPanelAction(
        renderedRequestGeneration,
        renderedEditorGeneration,
        renderedDraftGeneration,
      )
    ) {
      return;
    }
    const currentRead = renderedReadState;
    const identity: PersonalRoughCutReadIdentity = {
      snapshot: currentRead.status === "ready" ? currentRead.snapshot : null,
      requestGeneration: renderedRequestGeneration,
    };
    closedRef.current = true;
    requestGeneration.current += 1;
    stopPlaybackRef.current?.();
    const controller = activeController.current;
    activeController.current = null;
    frameNavigationInvalidatedCallback.current?.(identity);
    controller?.abort();
    setNavigationNotice(null);
    onClose();
  }

  return (
    <section
      aria-busy={readState.status === "loading"}
      aria-labelledby="personal-rough-cut-heading"
      className="personal-rough-cut-panel"
    >
      <header className="personal-rough-cut-heading">
        <div>
          <p className="eyebrow">个人草稿 · 只读核对</p>
          <h3 id="personal-rough-cut-heading">我的粗剪草稿</h3>
          <p>正文与视频引用来自读取时的章节；纳入状态不代表可播放、可导出或已认可。</p>
        </div>
        <div className="personal-rough-cut-actions">
          {onExportSavedSnapshot !== undefined && (
            <button
              className="secondary-button"
              disabled={!hasCurrentCanonicalRead || snapshot?.saved !== true || roughCutEditor.editing
                || roughCutEditor.isSaving || roughCutEditor.isLocked}
              onClick={exportSavedSnapshot}
              type="button"
            >
              导出已保存粗剪
            </button>
          )}
          {boundScope.services.savePersonalRoughCut !== undefined && (
            <button
              className="secondary-button"
              disabled={!hasCurrentCanonicalRead || !roughCutEditor.canEdit || roughCutEditor.editing}
              onClick={() => {
                stopPlaybackRef.current?.();
                roughCutEditor.openEditor();
              }}
              type="button"
            >
              编辑编排
            </button>
          )}
          <button className="secondary-button" disabled={roughCutEditor.isSaving} onClick={retryRead} type="button">
            重新读取
          </button>
          <button className="text-button" onClick={closePanel} type="button">
            关闭
          </button>
        </div>
      </header>

      {readState.status === "loading" && (
        <div className="personal-rough-cut-state" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>正在读取粗剪草稿…</span>
        </div>
      )}

      {navigationNotice !== null && (
        <p className="rough-cut-navigation-note" role="status" aria-live="polite">
          {navigationNotice}
        </p>
      )}

      {!hasCurrentCanonicalRead
        && (readState.portIdentity !== portIdentity || readState.directoryIdentity !== directoryIdentity) && (
        <p className="rough-cut-navigation-note" role="status" aria-live="polite">
          素材目录已变化或粗剪读取服务已更新；旧播放队列已停用，请显式重新读取。
        </p>
      )}

      {presentation !== null && (
        <div className="state-panel error-panel request personal-rough-cut-error" role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h4>{presentation.title}</h4>
            <p>{presentation.message}</p>
          </div>
        </div>
      )}

      {roughCutEditor.message !== null && (
        <div
          className={roughCutEditor.isSaving ? "personal-rough-cut-write-state" : "personal-rough-cut-write-state is-locked"}
          role={roughCutEditor.isSaving ? "status" : "alert"}
          aria-live="polite"
        >
          <strong>{roughCutEditor.isSaving ? "正在保存粗剪编排" : "粗剪编排已锁定"}</strong>
          <span>{roughCutEditor.message}</span>
          {roughCutEditor.isLocked && !roughCutEditor.isSaving && (
            <span>请使用上方“重新读取”核对服务端当前版本后再继续。</span>
          )}
        </div>
      )}

      {roughCutEditor.editing && hasCurrentCanonicalRead && canonicalSnapshot !== null && (
        <PersonalRoughCutEditor
          frames={canonicalSnapshot.frames}
          draft={roughCutEditor.draft}
          disabled={!roughCutEditor.canEdit}
          saveDisabled={!roughCutEditor.canSave}
          onMove={roughCutEditor.moveFrame}
          onIncludedChange={roughCutEditor.setIncluded}
          onSave={roughCutEditor.save}
          onCancel={roughCutEditor.cancelEditor}
        />
      )}

      {roughCutEditor.isLocked && roughCutEditor.displayedSnapshot !== null && (
        <section className="personal-rough-cut-submitted-intent" aria-label="本次粗剪保存意图">
          <h4>{roughCutEditor.isSaving ? "正在提交的只读意图" : "等待核实的只读意图"}</h4>
          <ol aria-label="已提交的粗剪顺序">
            {roughCutEditor.displayedSnapshot.frames.map((frame, index) => (
              <li key={`${frame.asset_id}-${index}`}>
                <span>提交顺序第 {index + 1} 项 · 原章节第 {frame.frame_index + 1} 个镜头</span>
                <span>{frame.included ? "纳入" : "排除"}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {hasCurrentCanonicalRead && snapshot !== null && (
        <div className="personal-rough-cut-content">
          <div className="personal-rough-cut-summary">
            <strong>
              {snapshot.saved
                ? "已保存草稿的当前投影 · 版本 " + snapshot.revision
                : "未保存的初始投影"}
            </strong>
            {snapshot.removed_asset_ids.length > 0 && (
              <span>{snapshot.removed_asset_ids.length} 个失效旧引用，仅显示数量。</span>
            )}
          </div>

          {hasCurrentPlaybackRead ? <PersonalRoughCutPlayback
            chapterId={boundScope.chapter.id}
            enabled={hasCurrentPlaybackRead
              && !roughCutEditor.editing
              && !roughCutEditor.isSaving
              && !roughCutEditor.isLocked}
            onUnauthorized={() => {
              if (readState.status === "ready" && hasCurrentPlaybackRead && readState.reader?.isCurrent()) {
                unauthorizedCallback.current();
              }
            }}
            reader={readState.status === "ready" && hasCurrentPlaybackRead ? readState.reader : null}
            registerStop={registerPlaybackStop}
            requestGeneration={readState.status === "ready" && hasCurrentPlaybackRead ? readState.requestGeneration : -1}
            services={boundScope.services}
            snapshot={snapshot}
            userId={boundScope.userId}
          /> : (
            <p className="rough-cut-navigation-note" role="status" aria-live="polite">
              素材目录已变化或粗剪读取服务已更新；旧播放队列已停用，请显式重新读取。
            </p>
          )}

          {snapshot.frames.length === 0 ? (
            <div className="personal-rough-cut-empty" role="status">
              <strong>当前投影没有镜头</strong>
              <span>这是一次有效读取的空列表。</span>
            </div>
          ) : (
            <>
              <div className="personal-rough-cut-filter">
                <div className="personal-rough-cut-filter-options" role="group" aria-label="按草稿状态筛选">
                  {FILTER_OPTIONS.map((option) => (
                    <button
                      aria-pressed={currentFilter === option.value}
                      className="personal-rough-cut-filter-button"
                      key={option.value}
                      onClick={() => chooseFilter(snapshot, readState.status === "ready" && hasCurrentCanonicalRead ? readState.requestGeneration : -1, option.value)}
                      type="button"
                    >
                      {option.label} {statusCounts[option.value]}
                    </button>
                  ))}
                </div>
                <p className="personal-rough-cut-filter-help">
                  待安排单独计数，可与已纳入或已排除重叠。
                </p>
              </div>

              {visibleFrames.length === 0 ? (
                <div className="personal-rough-cut-filter-empty" role="status">
                  <p>这份当前草稿投影中没有符合该条件的条目。</p>
                  <button
                    className="text-button"
                    onClick={() => chooseFilter(snapshot, readState.status === "ready" && hasCurrentCanonicalRead ? readState.requestGeneration : -1, "all")}
                    type="button"
                  >
                    查看全部条目
                  </button>
                </div>
              ) : (
                <ol className="personal-rough-cut-list" aria-label="当前粗剪草稿条目">
                  {visibleFrames.map(({ frame, originalIndex }) => {
                    const position = assets === null
                      ? null
                      : findRoughCutFramePosition(boundScope.chapter, boundScope.seriesId, snapshot, frame, assets);
                    const target = position === null ? null : roughCutTarget(snapshot, frame, position);
                    return (
                      <li className="personal-rough-cut-frame" key={originalIndex}>
                        <div className="personal-rough-cut-frame-heading">
                          <strong>
                            草稿列表第 {originalIndex + 1} 项 · 读取时章节第 {frame.frame_index + 1} 个镜头
                          </strong>
                          <div className="personal-rough-cut-tags" aria-label="草稿状态">
                            <span>{frame.included ? "已纳入草稿" : "已排除"}</span>
                            {frame.pending && <span>待安排</span>}
                          </div>
                        </div>
                        <p className={frame.text === "" ? "personal-rough-cut-text is-empty" : "personal-rough-cut-text"}>
                          {frame.text === "" ? "未提供正文" : frame.text}
                        </p>
                        <p className="personal-rough-cut-availability">{sourceAvailability(frame)}</p>
                        {onLocateFrame !== undefined && target !== null ? (
                          <button
                            className="text-button rough-cut-locate-button"
                            onClick={() => {
                              if (!target.isCurrent()) {
                                return;
                              }
                              const located = onLocateFrame(target);
                              setNavigationNotice(located
                                ? null
                                : "暂时无法定位到对应镜头，请确认该镜头仍在当前章节中后重试。");
                            }}
                            type="button"
                          >
                            定位到对应镜头 {position}
                          </button>
                        ) : onLocateFrame !== undefined && assets === null ? (
                          <p className="rough-cut-navigation-note" role="note">
                            {assetDirectoryStatus === "error"
                              ? "原图目录读取失败，暂不能核对定位。"
                              : "原图目录读取完成前，暂不能核对定位。"}
                          </p>
                        ) : onLocateFrame !== undefined ? (
                          <p className="rough-cut-navigation-note" role="note">当前条目的镜头身份无法核对，暂不能定位。</p>
                        ) : null}
                      </li>
                    );
                  })}
                </ol>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
