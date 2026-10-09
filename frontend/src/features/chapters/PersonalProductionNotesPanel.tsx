import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ApiError } from "../../shared/api/errors";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import type { WorkspaceServices } from "../../shared/api/services";
import { normalizeApiError } from "../../shared/api/services";
import type {
  PersonalProductionNoteUpdate,
  PersonalProductionResumeUpdate,
  PersonalProductionSnapshot,
} from "../../shared/api/personalProductionNotes";
import {
  capturePersonalProductionMedia,
  projectPersonalProductionSnapshot,
  type CapturedPersonalProductionMedia,
  type PersonalProductionFrameView,
  type PersonalProductionReadout,
} from "./personal-production/projection";
import {
  resolveProductionNoteFrameTarget,
  type ProductionNoteFrameTarget,
} from "./productionNoteFrameNavigation";
import {
  countProductionNoteStatuses,
  filterProductionNoteFrames,
  productionNoteStatusFilterOptions,
  type ProductionNoteStatusFilter,
} from "./productionNoteStatusFilter";
import { PersonalProductionNoteEditor, type PersonalProductionNoteEditorDraft } from "./PersonalProductionNoteEditor";
import {
  createPersonalProductionNoteUpdate,
  resolvePersonalProductionNoteEditCandidate,
  validatePersonalProductionNoteSaveResponse,
  type PersonalProductionNoteEditCandidate,
} from "./personal-production/noteEdit";
import {
  canClearPersonalProductionResume,
  createPersonalProductionResumeUpdate,
  resolvePersonalProductionResumeCandidates,
  validatePersonalProductionResumeSaveResponse,
  type PersonalProductionResumeCandidate,
} from "./personal-production/resumeEdit";

export interface PersonalProductionResumeTarget {
  position: number;
  isCurrent(): boolean;
}

export interface PersonalProductionNoteReadIdentity {
  readout: PersonalProductionReadout | null;
  requestGeneration: number;
}

export interface PersonalProductionNoteFrameTarget extends ProductionNoteFrameTarget {
  row: PersonalProductionFrameView;
  readout: PersonalProductionReadout;
  readIdentity: PersonalProductionNoteReadIdentity;
  scope: PersonalProductionNotesScope;
  isCurrent(): boolean;
}

interface PersonalProductionNotesScope {
  contextToken: object;
  assetSnapshotToken: object;
  userId: string;
  seriesId: string;
  chapter: Chapter;
  assets: StoryboardAsset[];
  mediaSnapshotAvailable: boolean;
  services: WorkspaceServices;
  generation: number;
}

interface PersonalProductionNotesReadTicket {
  scope: PersonalProductionNotesScope;
  generation: number;
}

interface PersonalProductionNotesPanelProps extends PersonalProductionNotesScope {
  onClose(readIdentity: PersonalProductionNoteReadIdentity | null): void;
  onRetryChapter(): void;
  onUnauthorized(): void;
  onLocateResume(target: PersonalProductionResumeTarget): void;
  onResumeInvalidated(): void;
  onRegisterFrameRead(readIdentity: PersonalProductionNoteReadIdentity): void;
  onFrameReadInvalidated(readIdentity: PersonalProductionNoteReadIdentity): void;
  onLocateProductionNoteFrame(target: PersonalProductionNoteFrameTarget): boolean;
}

type ReadState =
  | { status: "loading"; ticket: PersonalProductionNotesReadTicket | null }
  | { status: "checking"; ticket: PersonalProductionNotesReadTicket }
  | {
      status: "ready";
      ticket: PersonalProductionNotesReadTicket;
      readout: PersonalProductionReadout;
      snapshot: PersonalProductionSnapshot;
      captured: CapturedPersonalProductionMedia;
    }
  | { status: "error"; ticket: PersonalProductionNotesReadTicket; error: ApiError };

type SaveState = null
  | {
      kind: "note";
      status: "pending";
      candidate: PersonalProductionNoteEditCandidate;
      update: PersonalProductionNoteUpdate;
    }
  | {
      kind: "note";
      status: "must-reread";
      candidate: PersonalProductionNoteEditCandidate;
      message: string;
      uncertain: boolean;
    }
  | {
      kind: "resume";
      status: "pending";
      candidate: PersonalProductionResumeCandidate | null;
      update: PersonalProductionResumeUpdate;
      displayState: Extract<ReadState, { status: "ready" }>;
      description: string;
    }
  | {
      kind: "resume";
      status: "must-reread";
      candidate: PersonalProductionResumeCandidate | null;
      update: PersonalProductionResumeUpdate;
      displayState: Extract<ReadState, { status: "ready" }>;
      description: string;
      message: string;
      uncertain: boolean;
    };

interface PersonalProductionNoteDraft extends PersonalProductionNoteEditorDraft {}

interface PersonalProductionNoteSaveOperation {
  generation: number;
  ticket: PersonalProductionNotesReadTicket;
  candidate: PersonalProductionNoteEditCandidate;
  update: ReturnType<typeof createPersonalProductionNoteUpdate>;
  controller: AbortController;
}

interface PersonalProductionResumeSaveOperation {
  generation: number;
  readGeneration: number;
  ticket: PersonalProductionNotesReadTicket;
  candidate: PersonalProductionResumeCandidate | null;
  update: PersonalProductionResumeUpdate;
  controller: AbortController;
  displayState: Extract<ReadState, { status: "ready" }>;
  description: string;
}

interface PersonalProductionNoteEditorSession {
  candidate: PersonalProductionNoteEditCandidate;
  ticket: PersonalProductionNotesReadTicket;
  generation: number;
}

interface ProductionNoteStatusSelection {
  ticket: PersonalProductionNotesReadTicket;
  readout: PersonalProductionReadout;
  filter: ProductionNoteStatusFilter;
}

interface PersonalProductionResumeSelection {
  ticket: PersonalProductionNotesReadTicket;
  snapshot: PersonalProductionSnapshot;
  candidate: PersonalProductionResumeCandidate;
}

function sameScope(
  left: PersonalProductionNotesScope,
  right: PersonalProductionNotesScope,
): boolean {
  return left.contextToken === right.contextToken
    && left.assetSnapshotToken === right.assetSnapshotToken
    && left.userId === right.userId
    && left.seriesId === right.seriesId
    && left.chapter === right.chapter
    && left.assets === right.assets
    && left.mediaSnapshotAvailable === right.mediaSnapshotAvailable
    && left.services === right.services
    && left.generation === right.generation;
}

function ticketMatchesScope(
  ticket: PersonalProductionNotesReadTicket,
  scope: PersonalProductionNotesScope,
): boolean {
  return sameScope(ticket.scope, scope);
}

function sameEditCandidate(
  left: PersonalProductionNoteEditCandidate,
  right: PersonalProductionNoteEditCandidate,
): boolean {
  return left.chapterId === right.chapterId
    && left.storyboardAssetId === right.storyboardAssetId
    && left.frame === right.frame
    && left.row === right.row
    && left.snapshot === right.snapshot
    && left.captured === right.captured
    && left.readout === right.readout;
}

function hasVerifiedResume(readout: PersonalProductionReadout): boolean {
  return readout.resumePosition !== null
    && readout.frames.some((frame) => frame.position === readout.resumePosition && frame.verified);
}

function readIdentityForState(state: ReadState): PersonalProductionNoteReadIdentity | null {
  if (state.ticket === null) {
    return null;
  }
  return {
    readout: state.status === "ready" ? state.readout : null,
    requestGeneration: state.ticket.generation,
  };
}

function sameReadIdentity(
  left: PersonalProductionNoteReadIdentity | null,
  right: PersonalProductionNoteReadIdentity | null,
): boolean {
  return left === right || (
    left !== null
    && right !== null
    && left.requestGeneration === right.requestGeneration
    && left.readout === right.readout
  );
}

function errorMessage(error: ApiError): { title: string; message: string } {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const membership = ["会员", "membership", "premium", "subscription"]
      .some((word) => detail.includes(word));
    return {
      title: membership ? "当前账号暂不可查看记录" : "没有访问权限",
      message: error.detail ?? (membership ? "当前账号的访问资格暂不可用。" : "当前账号无权查看此章节记录。"),
    };
  }
  if (error.status === 404) {
    return { title: "章节记录不存在", message: error.detail ?? "章节可能已被移除。" };
  }
  if (error.status === 422) {
    return { title: "请求未通过校验", message: error.detail ?? "本地服务无法识别当前章节。" };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "暂时无法读取记录", message: "本地服务暂时不可用，请稍后重试。" };
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
  return { title: "暂时无法读取记录", message: error.detail ?? error.message };
}

function statusLabel(status: PersonalProductionReadout["frames"][number]["status"]): string {
  switch (status) {
    case "approved":
      return "已认可";
    case "needs_revision":
      return "待修";
    case "needs_reconfirmation":
      return "待重新确认";
    case "unverifiable":
      return "当前媒体暂不可核对";
    case "unreadable":
      return "记录不可识别";
    case "unmarked":
      return "未标记";
  }
}

function orphanStatusLabel(status: string): string {
  switch (status) {
    case "approved":
      return "历史曾认可（不代表当前状态）";
    case "needs_revision":
      return "旧记录 · 待修";
    case "unmarked":
      return "旧记录 · 未标记";
    default:
      return "旧记录";
  }
}

export function PersonalProductionNotesPanel({
  contextToken,
  assetSnapshotToken,
  generation: openEpoch,
  chapter,
  assets,
  mediaSnapshotAvailable,
  services,
  userId,
  seriesId,
  onClose,
  onRetryChapter,
  onUnauthorized,
  onLocateResume,
  onResumeInvalidated,
  onRegisterFrameRead,
  onFrameReadInvalidated,
  onLocateProductionNoteFrame,
}: PersonalProductionNotesPanelProps) {
  const [readState, setReadState] = useState<ReadState>({ status: "loading", ticket: null });
  const [frameNavigationNotice, setFrameNavigationNotice] = useState<number | null>(null);
  const [statusSelection, setStatusSelection] = useState<ProductionNoteStatusSelection | null>(null);
  const [resumeSelection, setResumeSelection] = useState<PersonalProductionResumeSelection | null>(null);
  const resumeSelectionRef = useRef(resumeSelection);
  resumeSelectionRef.current = resumeSelection;
  const [reloadGeneration, setReloadGeneration] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>(null);
  const [editingFrameId, setEditingFrameId] = useState<string | null>(null);
  const [noteDrafts, setNoteDrafts] = useState<Map<string, PersonalProductionNoteDraft>>(() => new Map());
  const noteDraftsRef = useRef(noteDrafts);
  noteDraftsRef.current = noteDrafts;
  const editorGeneration = useRef(0);
  const editorSessionRef = useRef<PersonalProductionNoteEditorSession | null>(null);
  const requestGeneration = useRef(0);
  const saveGeneration = useRef(0);
  const saveOperationRef = useRef<PersonalProductionNoteSaveOperation | null>(null);
  const resumeSaveOperationRef = useRef<PersonalProductionResumeSaveOperation | null>(null);
  const saveGateRef = useRef<SaveState>(null);
  const saveControllerRef = useRef<AbortController | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const scopeInvalidated = useRef(false);
  const unauthorizedCallback = useRef(onUnauthorized);
  const locateCallback = useRef(onLocateResume);
  const invalidatedCallback = useRef(onResumeInvalidated);
  const registerFrameReadCallback = useRef(onRegisterFrameRead);
  const frameReadInvalidatedCallback = useRef(onFrameReadInvalidated);
  const locateProductionNoteFrameCallback = useRef(onLocateProductionNoteFrame);
  const closeCallback = useRef(onClose);
  const scopeInvalidatedCallback = useRef(onResumeInvalidated);
  const scopeCloseCallback = useRef(onClose);
  unauthorizedCallback.current = onUnauthorized;
  locateCallback.current = onLocateResume;
  invalidatedCallback.current = onResumeInvalidated;
  registerFrameReadCallback.current = onRegisterFrameRead;
  frameReadInvalidatedCallback.current = onFrameReadInvalidated;
  locateProductionNoteFrameCallback.current = onLocateProductionNoteFrame;
  closeCallback.current = onClose;

  const currentScope: PersonalProductionNotesScope = {
    contextToken,
    assetSnapshotToken,
    userId,
    seriesId,
    chapter,
    assets,
    mediaSnapshotAvailable,
    services,
    generation: openEpoch,
  };
  const currentScopeRef = useRef(currentScope);
  currentScopeRef.current = currentScope;
  const initialScopeRef = useRef(currentScope);
  const readStateRef = useRef(readState);
  readStateRef.current = readState;
  const readIdentityRef = useRef<PersonalProductionNoteReadIdentity | null>(null);
  const renderedReadIdentity = readIdentityForState(readState);
  const renderedRegisteredReadIdentity = readIdentityRef.current;
  const scopeMatchesInitial = sameScope(initialScopeRef.current, currentScope);

  function ticketIsCurrent(ticket: PersonalProductionNotesReadTicket): boolean {
    return requestGeneration.current === ticket.generation
      && !scopeInvalidated.current
      && sameScope(initialScopeRef.current, currentScopeRef.current)
      && ticketMatchesScope(ticket, currentScopeRef.current);
  }

  function updateNoteDrafts(
    update: (current: Map<string, PersonalProductionNoteDraft>) => Map<string, PersonalProductionNoteDraft>,
  ): void {
    const next = update(noteDraftsRef.current);
    noteDraftsRef.current = next;
    setNoteDrafts(next);
  }

  function invalidateEditorSession(): void {
    editorGeneration.current += 1;
    editorSessionRef.current = null;
  }

  function editorSessionIsCurrent(
    candidate: PersonalProductionNoteEditCandidate,
    generation: number,
  ): boolean {
    const session = editorSessionRef.current;
    const latest = readStateRef.current;
    return session !== null
      && session.generation === generation
      && sameEditCandidate(session.candidate, candidate)
      && latest.status === "ready"
      && latest.ticket === session.ticket
      && ticketIsCurrent(session.ticket)
      && latest.snapshot === candidate.snapshot
      && latest.captured === candidate.captured
      && latest.readout === candidate.readout;
  }

  function invalidateActiveRead(
    invalidateFrameRead = true,
    preserveMustRereadGate = false,
  ): PersonalProductionNoteReadIdentity | null {
    invalidateEditorSession();
    const identity = readIdentityForState(readStateRef.current);
    const preserveDraftForVerification = preserveMustRereadGate
      && saveGateRef.current?.status === "must-reread";
    if (invalidateFrameRead && identity !== null) {
      frameReadInvalidatedCallback.current(identity);
    }
    requestGeneration.current += 1;
    activeController.current?.abort();
    activeController.current = null;
    if (!preserveDraftForVerification) {
      updateNoteDrafts(() => new Map());
    }
    if (!preserveDraftForVerification) {
      saveGeneration.current += 1;
      saveControllerRef.current?.abort();
      saveControllerRef.current = null;
      saveOperationRef.current = null;
      resumeSaveOperationRef.current = null;
      saveGateRef.current = null;
      setSaveState(null);
    }
    readIdentityRef.current = null;
    setEditingFrameId(null);
    setResumeSelection(null);
    setFrameNavigationNotice(null);
    invalidatedCallback.current();
    return identity;
  }

  useLayoutEffect(() => {
    if (scopeMatchesInitial || scopeInvalidated.current) {
      return;
    }
    scopeInvalidated.current = true;
    invalidateEditorSession();
    const identity = readIdentityForState(readStateRef.current);
    requestGeneration.current += 1;
    activeController.current?.abort();
    activeController.current = null;
    saveGeneration.current += 1;
    saveControllerRef.current?.abort();
    saveControllerRef.current = null;
    saveOperationRef.current = null;
    resumeSaveOperationRef.current = null;
    saveGateRef.current = null;
    setSaveState(null);
    readIdentityRef.current = null;
    setResumeSelection(null);
    setFrameNavigationNotice(null);
    scopeInvalidatedCallback.current();
    setReadState({ status: "loading", ticket: null });
    if (identity !== null) {
      scopeCloseCallback.current(identity);
      frameReadInvalidatedCallback.current(identity);
    }
  }, [scopeMatchesInitial]);

  useEffect(() => {
    if (
      scopeInvalidated.current
      || !sameScope(initialScopeRef.current, currentScopeRef.current)
    ) {
      return undefined;
    }
    const generation = ++requestGeneration.current;
    const ticket: PersonalProductionNotesReadTicket = {
      scope: currentScopeRef.current,
      generation,
    };
    let readIdentity: PersonalProductionNoteReadIdentity = {
      readout: null,
      requestGeneration: generation,
    };
    readIdentityRef.current = readIdentity;
    registerFrameReadCallback.current(readIdentity);
    setFrameNavigationNotice(null);
    setResumeSelection(null);
    const controller = new AbortController();
    activeController.current = controller;
    let mounted = true;
    const isCurrent = () => mounted
      && !controller.signal.aborted
      && ticketIsCurrent(ticket);
    scopeInvalidatedCallback.current();
    setReadState({ status: "loading", ticket });

    void ticket.scope.services.getPersonalProductionNotes(ticket.scope.chapter.id, controller.signal).then(async (snapshot) => {
      if (!isCurrent()) {
        return;
      }
      setReadState({ status: "checking", ticket });
      const captured = await capturePersonalProductionMedia(ticket.scope.chapter, ticket.scope.assets);
      if (!isCurrent()) {
        return;
      }
      const nextReadout = projectPersonalProductionSnapshot(ticket.scope.chapter, captured, snapshot);
      readIdentity = { readout: nextReadout, requestGeneration: generation };
      readIdentityRef.current = readIdentity;
      const nextState: Extract<ReadState, { status: "ready" }> = {
        status: "ready",
        ticket,
        readout: nextReadout,
        snapshot,
        captured,
      };
      const failedNoteSave = saveGateRef.current?.kind === "note"
        && saveGateRef.current.status === "must-reread"
        ? saveGateRef.current
        : null;
      const verifiedSaveTarget = failedNoteSave === null
        ? null
        : (() => {
            const row = nextReadout.frames.find((frame) => (
              frame.position === failedNoteSave.candidate.position
            ));
            return row === undefined ? null : candidateForRow(nextState, row);
          })();
      if (
        failedNoteSave !== null
        && verifiedSaveTarget?.storyboardAssetId === failedNoteSave.candidate.storyboardAssetId
      ) {
        saveGateRef.current = null;
        setSaveState(null);
        invalidateEditorSession();
        setEditingFrameId(null);
        updateNoteDrafts((current) => new Map([...current.entries()].map(([id, draft]) => [id, {
          ...draft,
          status: null,
          requiresStatusConfirmation: true,
        }])));
      } else if (
        saveGateRef.current?.kind === "resume"
        && saveGateRef.current.status === "must-reread"
      ) {
        saveGateRef.current = null;
        setSaveState(null);
        setResumeSelection(null);
        setStatusSelection(null);
        updateNoteDrafts(() => new Map());
      }
      setReadState(nextState);
      registerFrameReadCallback.current(readIdentity);
    }).catch((cause: unknown) => {
      if (!isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        unauthorizedCallback.current();
        return;
      }
      setReadState({ status: "error", ticket, error });
    });

    return () => {
      mounted = false;
      controller.abort();
      if (activeController.current === controller) {
        activeController.current = null;
      }
      if (requestGeneration.current === generation) {
        requestGeneration.current += 1;
      }
      frameReadInvalidatedCallback.current(readIdentity);
      scopeInvalidatedCallback.current();
    };
  }, [
    assetSnapshotToken,
    assets,
    chapter,
    contextToken,
    mediaSnapshotAvailable,
    openEpoch,
    reloadGeneration,
    seriesId,
    services,
    userId,
  ]);

  useEffect(() => () => {
    invalidateEditorSession();
    saveGeneration.current += 1;
    saveControllerRef.current?.abort();
    saveControllerRef.current = null;
    saveOperationRef.current = null;
    resumeSaveOperationRef.current = null;
    saveGateRef.current = null;
  }, []);

  function closePanel() {
    if (
      !sameReadIdentity(renderedRegisteredReadIdentity, readIdentityRef.current)
      || !sameReadIdentity(renderedReadIdentity, readIdentityForState(readStateRef.current))
    ) {
      return;
    }
    const identity = renderedRegisteredReadIdentity;
    invalidateActiveRead(false);
    closeCallback.current(identity);
    if (identity !== null) {
      frameReadInvalidatedCallback.current(identity);
    }
    scopeInvalidated.current = true;
    setReadState({ status: "loading", ticket: null });
  }

  function retry() {
    if (
      saveGateRef.current?.status === "pending"
      || scopeInvalidated.current
      || !sameScope(initialScopeRef.current, currentScopeRef.current)
      || !sameReadIdentity(renderedRegisteredReadIdentity, readIdentityRef.current)
      || !sameReadIdentity(renderedReadIdentity, readIdentityForState(readStateRef.current))
    ) {
      return;
    }
    invalidateActiveRead(true, saveGateRef.current?.status === "must-reread");
    setReadState({ status: "loading", ticket: null });
    setReloadGeneration((current) => current + 1);
  }

  const requestedReadState = readState.ticket === null || ticketIsCurrent(readState.ticket)
    ? readState
    : { status: "loading" as const, ticket: null };
  const retainedResumeState = saveState?.kind === "resume"
    && !scopeInvalidated.current
    && sameScope(initialScopeRef.current, currentScopeRef.current)
    ? saveState.displayState
    : null;
  const visibleReadState = retainedResumeState ?? requestedReadState;
  const readout = visibleReadState.status === "ready" ? visibleReadState.readout : null;
  const error = requestedReadState.status === "error" ? errorMessage(requestedReadState.error) : null;
  const saveGate = saveState;
  const retainedSaveDraft = saveGate?.kind === "note" && saveGate.status === "must-reread"
    ? noteDrafts.get(saveGate.candidate.storyboardAssetId) ?? null
    : null;
  const canShowStatusFilters = visibleReadState.status === "ready"
    && readout !== null
    && !readout.noSavedRecord
    && readout.frames.length > 0
    && ticketIsCurrent(visibleReadState.ticket);
  const statusCounts = canShowStatusFilters && readout !== null
    ? countProductionNoteStatuses(readout.frames)
    : null;
  const statusSelectionMatches = canShowStatusFilters
    && statusSelection !== null
    && visibleReadState.status === "ready"
    && statusSelection.ticket === visibleReadState.ticket
    && statusSelection.readout === readout
    && ticketIsCurrent(statusSelection.ticket);
  const selectedStatusFilter: ProductionNoteStatusFilter = statusSelectionMatches && statusSelection !== null
    ? statusSelection.filter
    : "all";
  const visibleFrames = readout === null
    ? []
    : filterProductionNoteFrames(readout.frames, selectedStatusFilter);
  const selectedStatusLabel = productionNoteStatusFilterOptions.find((option) => option.value === selectedStatusFilter)?.label
    ?? "所选状态";
  const hasUnverifiedFrames = readout?.frames.some((frame) => !frame.verified) ?? false;
  const requiresChapterReload = readout?.mediaState === "mismatch"
    || readout?.mediaState === "unreadable"
    || !mediaSnapshotAvailable;
  const canLocateResume = readout !== null
    && readout.mediaState === "ready"
    && mediaSnapshotAvailable
    && hasVerifiedResume(readout)
    && !readout.resumeIsInvalid
    && visibleReadState.status === "ready"
    && ticketIsCurrent(visibleReadState.ticket);
  const resumeCandidates = visibleReadState.status === "ready"
    ? resolvePersonalProductionResumeCandidates({
        chapter: visibleReadState.ticket.scope.chapter,
        seriesId: visibleReadState.ticket.scope.seriesId,
        assets: visibleReadState.ticket.scope.assets,
        captured: visibleReadState.captured,
        snapshot: visibleReadState.snapshot,
        readout: visibleReadState.readout,
        mediaSnapshotAvailable: visibleReadState.ticket.scope.mediaSnapshotAvailable,
      })
    : [];
  const resumeSelectionMatches = resumeSelection !== null
    && visibleReadState.status === "ready"
    && resumeSelection.ticket === visibleReadState.ticket
    && resumeSelection.snapshot === visibleReadState.snapshot
    && ticketIsCurrent(resumeSelection.ticket)
    && resumeCandidates.some((candidate) => sameResumeCandidate(candidate, resumeSelection.candidate));
  const selectedResumeCandidate = resumeSelectionMatches ? resumeSelection?.candidate ?? null : null;
  const savedResumeCandidate = visibleReadState.status === "ready"
    && visibleReadState.snapshot.resume_frame_id !== null
    ? resumeCandidates.find((candidate) => (
        candidate.storyboardAssetId === visibleReadState.snapshot.resume_frame_id
      )) ?? null
    : null;
  const readyReadIsCurrent = visibleReadState.status === "ready"
    && readStateRef.current === visibleReadState
    && ticketIsCurrent(visibleReadState.ticket)
    && sameReadIdentity(renderedReadIdentity, readIdentityForState(readStateRef.current))
    && sameReadIdentity(renderedRegisteredReadIdentity, readIdentityRef.current);
  const resumeWritesAvailable = services.savePersonalProductionResume !== undefined;
  const noteDraftsBlockResume = readyReadIsCurrent
    && hasUnsavedNoteChanges(visibleReadState);
  const canChangeResume = readyReadIsCurrent
    && saveGate === null
    && resumeWritesAvailable
    && !noteDraftsBlockResume;
  const canClearResume = canChangeResume
    && visibleReadState.status === "ready"
    && canClearPersonalProductionResume(chapter.id, visibleReadState.snapshot);

  function selectStatusFilter(
    filter: ProductionNoteStatusFilter,
    sourceTicket: PersonalProductionNotesReadTicket,
    sourceReadout: PersonalProductionReadout,
  ) {
    const latest = readStateRef.current;
    if (
      latest.status !== "ready"
      || latest.ticket !== sourceTicket
      || latest.readout !== sourceReadout
      || !ticketIsCurrent(sourceTicket)
    ) {
      return;
    }

    setStatusSelection((current) => (
      current?.ticket === sourceTicket
      && current.readout === sourceReadout
      && current.filter === filter
        ? current
        : { ticket: sourceTicket, readout: sourceReadout, filter }
    ));
  }

  function candidateForRow(
    state: Extract<ReadState, { status: "ready" }>,
    row: PersonalProductionFrameView,
  ): PersonalProductionNoteEditCandidate | null {
    return resolvePersonalProductionNoteEditCandidate({
      chapter: state.ticket.scope.chapter,
      assets: state.ticket.scope.assets,
      captured: state.captured,
      snapshot: state.snapshot,
      readout: state.readout,
      row,
      mediaSnapshotAvailable: state.ticket.scope.mediaSnapshotAvailable,
    });
  }

  function sameResumeCandidate(
    left: PersonalProductionResumeCandidate,
    right: PersonalProductionResumeCandidate,
  ): boolean {
    return left.chapterId === right.chapterId
      && left.seriesId === right.seriesId
      && left.frameIndex === right.frameIndex
      && left.position === right.position
      && left.storyboardAssetId === right.storyboardAssetId
      && left.row === right.row
      && left.frame === right.frame
      && left.capturedFrame === right.capturedFrame
      && left.serverFrame === right.serverFrame
      && left.snapshot === right.snapshot
      && left.captured === right.captured
      && left.readout === right.readout;
  }

  function hasUnsavedNoteChanges(state: Extract<ReadState, { status: "ready" }>): boolean {
    if (editingFrameId !== null || editorSessionRef.current !== null) {
      return true;
    }

    const currentCandidates = state.readout.frames
      .map((row) => candidateForRow(state, row))
      .filter((candidate): candidate is PersonalProductionNoteEditCandidate => candidate !== null);
    for (const [storyboardAssetId, draft] of noteDraftsRef.current) {
      const candidate = currentCandidates.find((entry) => entry.storyboardAssetId === storyboardAssetId);
      if (
        candidate === undefined
        || draft.note !== candidate.note
        || draft.status !== candidate.initialStatus
      ) {
        return true;
      }
    }
    return false;
  }

  function selectResumeCandidate(
    candidateId: string,
    sourceTicket: PersonalProductionNotesReadTicket,
    sourceSnapshot: PersonalProductionSnapshot,
  ): void {
    const latest = readStateRef.current;
    if (
      saveGateRef.current !== null
      || latest.status !== "ready"
      || latest.ticket !== sourceTicket
      || latest.snapshot !== sourceSnapshot
      || !ticketIsCurrent(sourceTicket)
      || !sameReadIdentity(readIdentityRef.current, readIdentityForState(latest))
      || hasUnsavedNoteChanges(latest)
    ) {
      return;
    }

    if (candidateId === "") {
      setResumeSelection(null);
      return;
    }
    const candidate = resolvePersonalProductionResumeCandidates({
      chapter: latest.ticket.scope.chapter,
      seriesId: latest.ticket.scope.seriesId,
      assets: latest.ticket.scope.assets,
      captured: latest.captured,
      snapshot: latest.snapshot,
      readout: latest.readout,
      mediaSnapshotAvailable: latest.ticket.scope.mediaSnapshotAvailable,
    }).find((entry) => entry.storyboardAssetId === candidateId);
    if (candidate === undefined) {
      return;
    }
    setResumeSelection({ ticket: latest.ticket, snapshot: latest.snapshot, candidate });
  }

  function openEditor(candidate: PersonalProductionNoteEditCandidate): void {
    const state = readStateRef.current;
    if (
      saveGateRef.current !== null
      || services.savePersonalProductionNote === undefined
      || state.status !== "ready"
      || !ticketIsCurrent(state.ticket)
      || state.snapshot !== candidate.snapshot
      || state.captured !== candidate.captured
      || state.readout !== candidate.readout
      || !state.readout.frames.includes(candidate.row)
    ) {
      return;
    }

    if (!noteDraftsRef.current.has(candidate.storyboardAssetId)) {
      updateNoteDrafts((current) => {
        const next = new Map(current);
        next.set(candidate.storyboardAssetId, {
          status: candidate.initialStatus,
          note: candidate.note,
          requiresStatusConfirmation: false,
        });
        return next;
      });
    }
    const generation = ++editorGeneration.current;
    editorSessionRef.current = { candidate, ticket: state.ticket, generation };
    setEditingFrameId(candidate.storyboardAssetId);
  }

  function updateDraft(
    candidate: PersonalProductionNoteEditCandidate,
    generation: number,
    update: (draft: PersonalProductionNoteDraft) => PersonalProductionNoteDraft,
  ): void {
    if (
      saveGateRef.current !== null
      || !editorSessionIsCurrent(candidate, generation)
    ) {
      return;
    }
    updateNoteDrafts((current) => {
      const existing = current.get(candidate.storyboardAssetId) ?? {
        status: candidate.initialStatus,
        note: candidate.note,
        requiresStatusConfirmation: false,
      };
      const next = new Map(current);
      next.set(candidate.storyboardAssetId, update(existing));
      return next;
    });
  }

  function cancelEditor(candidate: PersonalProductionNoteEditCandidate, generation: number): void {
    if (!editorSessionIsCurrent(candidate, generation)) {
      return;
    }
    invalidateEditorSession();
    setEditingFrameId(null);
  }

  function saveIsCurrent(operation: PersonalProductionNoteSaveOperation): boolean {
    const latest = readStateRef.current;
    return saveOperationRef.current === operation
      && saveGeneration.current === operation.generation
      && !operation.controller.signal.aborted
      && !scopeInvalidated.current
      && sameScope(initialScopeRef.current, currentScopeRef.current)
      && ticketMatchesScope(operation.ticket, currentScopeRef.current)
      && latest.status === "ready"
      && latest.ticket === operation.ticket
      && latest.snapshot === operation.candidate.snapshot
      && latest.readout === operation.candidate.readout
      && latest.captured === operation.candidate.captured;
  }

  function saveNote(candidate: PersonalProductionNoteEditCandidate, editGeneration: number): void {
    const state = readStateRef.current;
    if (
      saveGateRef.current !== null
      || state.status !== "ready"
      || !editorSessionIsCurrent(candidate, editGeneration)
    ) {
      return;
    }
    const save = state.ticket.scope.services.savePersonalProductionNote;
    if (save === undefined) {
      return;
    }
    const currentCandidate = candidateForRow(state, candidate.row);
    if (
      currentCandidate === null
      || currentCandidate.frame !== candidate.frame
      || currentCandidate.storyboardAssetId !== candidate.storyboardAssetId
    ) {
      return;
    }
    const draft = noteDraftsRef.current.get(candidate.storyboardAssetId) ?? {
      status: candidate.initialStatus,
      note: candidate.note,
      requiresStatusConfirmation: false,
    };
    if (draft.status === null || draft.requiresStatusConfirmation) {
      return;
    }

    let update: ReturnType<typeof createPersonalProductionNoteUpdate>;
    try {
      update = createPersonalProductionNoteUpdate(currentCandidate, draft.status, draft.note);
    } catch {
      return;
    }

    invalidateEditorSession();
    const controller = new AbortController();
    const operation: PersonalProductionNoteSaveOperation = {
      generation: ++saveGeneration.current,
      ticket: state.ticket,
      candidate: currentCandidate,
      update,
      controller,
    };
    saveOperationRef.current = operation;
    resumeSaveOperationRef.current = null;
    saveControllerRef.current = controller;
    const pendingState: SaveState = {
      kind: "note",
      status: "pending",
      candidate: currentCandidate,
      update,
    };
    saveGateRef.current = pendingState;
    requestGeneration.current += 1;
    activeController.current?.abort();
    activeController.current = null;
    const identity = readIdentityRef.current;
    if (identity !== null) {
      frameReadInvalidatedCallback.current(identity);
    }
    const pendingReadIdentity: PersonalProductionNoteReadIdentity = {
      readout: null,
      requestGeneration: requestGeneration.current,
    };
    readIdentityRef.current = pendingReadIdentity;
    registerFrameReadCallback.current(pendingReadIdentity);
    invalidatedCallback.current();
    setFrameNavigationNotice(null);
    setStatusSelection(null);
    setResumeSelection(null);
    setSaveState(pendingState);

    void save(currentCandidate.chapterId, update, controller.signal).then((value) => {
      if (!saveIsCurrent(operation)) {
        return;
      }
      const snapshot = validatePersonalProductionNoteSaveResponse({
        candidate: currentCandidate,
        update,
        value,
      });
      const readout = projectPersonalProductionSnapshot(
        operation.ticket.scope.chapter,
        currentCandidate.captured,
        snapshot,
      );
      const ticket: PersonalProductionNotesReadTicket = {
        scope: operation.ticket.scope,
        generation: ++requestGeneration.current,
      };
      const identity: PersonalProductionNoteReadIdentity = {
        readout,
        requestGeneration: ticket.generation,
      };
      const nextState: Extract<ReadState, { status: "ready" }> = {
        status: "ready",
        ticket,
        readout,
        snapshot,
        captured: currentCandidate.captured,
      };
      saveOperationRef.current = null;
      saveControllerRef.current = null;
      saveGateRef.current = null;
      readIdentityRef.current = identity;
      readStateRef.current = nextState;
      setSaveState(null);
      setReadState(nextState);
      invalidateEditorSession();
      setEditingFrameId(null);
      setStatusSelection(null);
      setResumeSelection(null);
      updateNoteDrafts((current) => {
        const next = new Map(current);
        next.delete(currentCandidate.storyboardAssetId);
        return next;
      });
      registerFrameReadCallback.current(identity);
    }).catch((cause: unknown) => {
      if (!saveIsCurrent(operation)) {
        return;
      }
      const error = normalizeApiError(cause);
      saveOperationRef.current = null;
      saveControllerRef.current = null;
      if (error.status === 401) {
        saveGateRef.current = null;
        setSaveState(null);
        unauthorizedCallback.current();
        return;
      }

      const uncertain = error.kind !== "http" || error.status === null || error.status >= 500;
      const message = uncertain
        ? "无法确认保存是否已生效。"
        : error.detail ?? error.message;
      const failedState: SaveState = {
        kind: "note",
        status: "must-reread",
        candidate: currentCandidate,
        message,
        uncertain,
      };
      saveGateRef.current = failedState;
      setSaveState(failedState);
      invalidateEditorSession();
      setEditingFrameId(null);
    });
  }

  function resumeSaveIsCurrent(operation: PersonalProductionResumeSaveOperation): boolean {
    const latest = readStateRef.current;
    const pendingIdentity = readIdentityRef.current;
    return resumeSaveOperationRef.current === operation
      && saveGeneration.current === operation.generation
      && requestGeneration.current === operation.readGeneration
      && !operation.controller.signal.aborted
      && !scopeInvalidated.current
      && sameScope(initialScopeRef.current, currentScopeRef.current)
      && ticketMatchesScope(operation.ticket, currentScopeRef.current)
      && latest.status === "ready"
      && latest.ticket === operation.ticket
      && latest.snapshot === (operation.candidate?.snapshot ?? operation.displayState.snapshot)
      && latest.readout === operation.displayState.readout
      && latest.captured === operation.displayState.captured
      && pendingIdentity?.requestGeneration === operation.readGeneration
      && pendingIdentity.readout === null;
  }

  function saveResumePosition(
    candidate: PersonalProductionResumeCandidate | null,
    sourceSelection: PersonalProductionResumeSelection | null,
  ): void {
    const state = readStateRef.current;
    const save = state.status === "ready"
      ? state.ticket.scope.services.savePersonalProductionResume
      : undefined;
    if (
      saveGateRef.current !== null
      || save === undefined
      || state.status !== "ready"
      || !ticketIsCurrent(state.ticket)
      || !sameReadIdentity(renderedReadIdentity, readIdentityForState(state))
      || !sameReadIdentity(renderedRegisteredReadIdentity, readIdentityRef.current)
      || hasUnsavedNoteChanges(state)
    ) {
      return;
    }

    let currentCandidate: PersonalProductionResumeCandidate | null = null;
    if (candidate !== null) {
      if (
        sourceSelection === null
        || resumeSelectionRef.current !== sourceSelection
        || sourceSelection.ticket !== state.ticket
        || sourceSelection.snapshot !== state.snapshot
        || !sameResumeCandidate(sourceSelection.candidate, candidate)
        || state.snapshot.resume_frame_id === candidate.storyboardAssetId
      ) {
        return;
      }
      currentCandidate = resolvePersonalProductionResumeCandidates({
        chapter: state.ticket.scope.chapter,
        seriesId: state.ticket.scope.seriesId,
        assets: state.ticket.scope.assets,
        captured: state.captured,
        snapshot: state.snapshot,
        readout: state.readout,
        mediaSnapshotAvailable: state.ticket.scope.mediaSnapshotAvailable,
      }).find((entry) => sameResumeCandidate(entry, candidate)) ?? null;
      if (currentCandidate === null) {
        return;
      }
    } else if (!canClearPersonalProductionResume(state.ticket.scope.chapter.id, state.snapshot)) {
      return;
    }

    let update: PersonalProductionResumeUpdate;
    try {
      update = createPersonalProductionResumeUpdate(state.snapshot, currentCandidate);
    } catch {
      return;
    }

    const description = currentCandidate === null
      ? "清除续作位置"
      : `设置为镜头 ${currentCandidate.position}`;
    const controller = new AbortController();
    const operation: PersonalProductionResumeSaveOperation = {
      generation: ++saveGeneration.current,
      readGeneration: 0,
      ticket: state.ticket,
      candidate: currentCandidate,
      update,
      controller,
      displayState: state,
      description,
    };
    resumeSaveOperationRef.current = operation;
    saveOperationRef.current = null;
    saveControllerRef.current = controller;
    requestGeneration.current += 1;
    operation.readGeneration = requestGeneration.current;

    const pendingState: SaveState = {
      kind: "resume",
      status: "pending",
      candidate: currentCandidate,
      update,
      displayState: state,
      description,
    };
    // Lock both write paths before abort listeners or parent callbacks can run.
    saveGateRef.current = pendingState;
    setSaveState(pendingState);

    activeController.current?.abort();
    activeController.current = null;

    const previousIdentity = readIdentityRef.current;
    if (previousIdentity !== null) {
      frameReadInvalidatedCallback.current(previousIdentity);
    }
    const pendingIdentity: PersonalProductionNoteReadIdentity = {
      readout: null,
      requestGeneration: operation.readGeneration,
    };
    readIdentityRef.current = pendingIdentity;
    registerFrameReadCallback.current(pendingIdentity);
    invalidatedCallback.current();
    setFrameNavigationNotice(null);
    setStatusSelection(null);
    setResumeSelection(null);

    void Promise.resolve()
      .then(() => save(state.ticket.scope.chapter.id, update, controller.signal))
      .then((value) => {
        if (!resumeSaveIsCurrent(operation)) {
          return;
        }
        const snapshot = validatePersonalProductionResumeSaveResponse({
          chapterId: state.ticket.scope.chapter.id,
          update,
          candidate: currentCandidate,
          value,
        });
        const readout = projectPersonalProductionSnapshot(
          state.ticket.scope.chapter,
          state.captured,
          snapshot,
        );
        const ticket: PersonalProductionNotesReadTicket = {
          scope: state.ticket.scope,
          generation: ++requestGeneration.current,
        };
        const identity: PersonalProductionNoteReadIdentity = {
          readout,
          requestGeneration: ticket.generation,
        };
        const nextState: Extract<ReadState, { status: "ready" }> = {
          status: "ready",
          ticket,
          readout,
          snapshot,
          captured: state.captured,
        };

        resumeSaveOperationRef.current = null;
        saveControllerRef.current = null;
        saveGateRef.current = null;
        readIdentityRef.current = identity;
        readStateRef.current = nextState;
        setSaveState(null);
        setReadState(nextState);
        setStatusSelection(null);
        setResumeSelection(null);
        setFrameNavigationNotice(null);
        updateNoteDrafts(() => new Map());
        registerFrameReadCallback.current(identity);
      })
      .catch((cause: unknown) => {
        if (!resumeSaveIsCurrent(operation)) {
          return;
        }
        const error = normalizeApiError(cause);
        resumeSaveOperationRef.current = null;
        saveControllerRef.current = null;
        if (error.status === 401) {
          saveGateRef.current = null;
          setSaveState(null);
          unauthorizedCallback.current();
          return;
        }

        const uncertain = error.kind !== "http" || error.status === null || error.status >= 500;
        const failedState: SaveState = {
          kind: "resume",
          status: "must-reread",
          candidate: currentCandidate,
          update,
          displayState: state,
          description,
          message: error.detail ?? error.message,
          uncertain,
        };
        saveGateRef.current = failedState;
        setSaveState(failedState);
      });
  }

  function locateProductionNoteFrame(row: PersonalProductionFrameView) {
    const state = readStateRef.current;
    if (
      visibleReadState.status !== "ready"
      || state.status !== "ready"
      || state.ticket !== visibleReadState.ticket
      || !ticketIsCurrent(state.ticket)
      || !state.ticket.scope.mediaSnapshotAvailable
      || !sameReadIdentity(renderedReadIdentity, readIdentityForState(state))
    ) {
      return;
    }

    const resolved = resolveProductionNoteFrameTarget(
      state.ticket.scope.chapter,
      state.ticket.scope.seriesId,
      state.ticket.scope.assets,
      state.readout,
      row,
    );
    const identity = readIdentityRef.current;
    if (
      resolved === null
      || identity === null
      || identity.requestGeneration !== state.ticket.generation
      || identity.readout !== state.readout
    ) {
      return;
    }

    const ticket = state.ticket;
    const verifiedReadout = state.readout;
    const target: PersonalProductionNoteFrameTarget = {
      ...resolved,
      row,
      readout: verifiedReadout,
      readIdentity: identity,
      scope: ticket.scope,
      isCurrent: () => {
        const latest = readStateRef.current;
        const latestTarget = resolveProductionNoteFrameTarget(
          ticket.scope.chapter,
          ticket.scope.seriesId,
          ticket.scope.assets,
          verifiedReadout,
          row,
        );
        return ticketIsCurrent(ticket)
          && latest.status === "ready"
          && latest.ticket === ticket
          && latest.readout === verifiedReadout
          && readIdentityRef.current === identity
          && ticket.scope.mediaSnapshotAvailable
          && latestTarget !== null
          && latestTarget.storyboardAssetId === target.storyboardAssetId
          && latestTarget.position === target.position
          && latestTarget.frame === target.frame;
      },
    };
    if (!target.isCurrent()) {
      return;
    }

    const located = locateProductionNoteFrameCallback.current(target);
    if (!target.isCurrent()) {
      return;
    }
    setFrameNavigationNotice(located ? null : row.position);
  }

  function locateResume() {
    if (
      visibleReadState.status !== "ready"
      || !ticketIsCurrent(visibleReadState.ticket)
    ) {
      return;
    }
    const ticket = visibleReadState.ticket;
    const verifiedReadout = visibleReadState.readout;
    const position = verifiedReadout.resumePosition;
    if (
      !mediaSnapshotAvailable
      || verifiedReadout.mediaState !== "ready"
      || position === null
      || verifiedReadout.resumeIsInvalid
      || !hasVerifiedResume(verifiedReadout)
    ) {
      return;
    }
    const target: PersonalProductionResumeTarget = {
      position,
      isCurrent: () => {
        const latestReadState = readStateRef.current;
        return ticketIsCurrent(ticket)
          && latestReadState.status === "ready"
          && latestReadState.ticket === ticket
          && latestReadState.readout === verifiedReadout
          && latestReadState.readout.mediaState === "ready"
          && latestReadState.readout.resumePosition === target.position
          && hasVerifiedResume(latestReadState.readout)
          && !latestReadState.readout.resumeIsInvalid
          && ticket.scope.mediaSnapshotAvailable;
      },
    };
    if (target.isCurrent()) {
      locateCallback.current(target);
    }
  }

  const canCreateFirstNotes = readout !== null
    && readout.noSavedRecord
    && readout.mediaState === "ready"
    && mediaSnapshotAvailable
    && services.savePersonalProductionNote !== undefined;
  const rowsForDisplay = readout === null
    ? []
    : readout.noSavedRecord
      ? canCreateFirstNotes ? readout.frames : []
      : visibleFrames;
  const frameEntries: Array<{
    frame: PersonalProductionFrameView;
    candidate: PersonalProductionNoteEditCandidate | null;
  }> = [];
  if (visibleReadState.status === "ready") {
    for (const frame of rowsForDisplay) {
      const candidate = candidateForRow(visibleReadState, frame);
      if (!canCreateFirstNotes || candidate !== null) {
        frameEntries.push({ frame, candidate });
      }
    }
  }

  if (
    scopeInvalidated.current
    || !sameScope(initialScopeRef.current, currentScopeRef.current)
  ) {
    return null;
  }

  return (
    <section className="personal-production-notes-panel" aria-labelledby="personal-production-notes-heading">
      <header className="personal-production-notes-heading">
        <div>
          <p className="eyebrow">个人记录</p>
          <h3 id="personal-production-notes-heading">我的制作记录</h3>
          <p>编辑与保存只会在你明确操作后进行；页面不会自动修改或重新认可记录。</p>
        </div>
        <div className="personal-production-notes-actions">
          <button
            className="text-button"
            disabled={saveGate?.status === "pending"}
            onClick={retry}
            type="button"
          >
            重新读取记录
          </button>
          <button className="secondary-button" onClick={closePanel} type="button">关闭记录</button>
        </div>
      </header>

      {saveState === null && visibleReadState.status === "loading" && (
        <div className="personal-production-notes-state" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>正在读取个人制作记录…</span>
        </div>
      )}
      {visibleReadState.status === "checking" && (
        <div className="personal-production-notes-state" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>正在本机核对已加载的媒体信息…</span>
        </div>
      )}
      {error !== null && (
        <div className="state-panel error-panel request" role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h4>{error.title}</h4>
            <p>{error.message}</p>
          </div>
          <button className="secondary-button" onClick={retry} type="button">重试读取</button>
        </div>
      )}

      {saveState?.kind === "note" && saveState.status === "pending" && (
        <div className="personal-production-note-save-state" role="status" aria-live="polite">
          <p>正在保存镜头 {saveState.candidate.position} 的个人记录…</p>
          <div className="personal-production-note-retained-draft">
            <strong>正在保存的内容 · 镜头 {saveState.candidate.position}</strong>
            <p>状态：{statusLabel(saveState.update.frames[0]?.status ?? "unmarked")}</p>
            <p>文字备注：{saveState.update.frames[0]?.note ?? "（空备注）"}</p>
          </div>
        </div>
      )}
      {saveState?.kind === "note" && saveState.status === "must-reread" && (
        <div className="state-panel error-panel request personal-production-note-save-error" role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h4>{saveState.uncertain ? "无法确定是否已保存" : "保存未完成"}</h4>
            <p>{saveState.message}</p>
            <p>草稿仍保留。请重新读取记录核实后再继续。</p>
            <div className="personal-production-note-retained-draft">
              <strong>保留的草稿 · 镜头 {saveState.candidate.position}</strong>
              <p>状态：{retainedSaveDraft?.status == null
                ? "尚未选择"
                : statusLabel(retainedSaveDraft.status)}</p>
              <p>文字备注：{retainedSaveDraft?.note ?? "（空备注）"}</p>
            </div>
          </div>
          <button className="secondary-button" onClick={retry} type="button">
            重新读取并核实
          </button>
        </div>
      )}

      {saveState?.kind === "resume" && saveState.status === "pending" && (
        <div className="personal-production-note-save-state" role="status" aria-live="polite">
          <p>正在保存续作位置…</p>
          <div className="personal-production-note-retained-draft">
            <strong>正在处理的续作位置</strong>
            <p>{saveState.description}</p>
            <p>当前记录仍只读显示；保存结果不会自动跳转。</p>
          </div>
        </div>
      )}
      {saveState?.kind === "resume" && saveState.status === "must-reread" && (
        <div className="state-panel error-panel request personal-production-note-save-error" role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h4>{saveState.uncertain ? "无法确定续作位置是否已保存" : "续作位置保存未完成"}</h4>
            <p>{saveState.message}</p>
            <p>操作意图已保留为只读。请显式重新读取后再决定是否重试。</p>
            <div className="personal-production-note-retained-draft">
              <strong>保留的续作操作</strong>
              <p>{saveState.description}</p>
            </div>
          </div>
          <button className="secondary-button" onClick={retry} type="button">
            重新读取并核实
          </button>
        </div>
      )}

      {readout !== null && (
        <div className="personal-production-notes-content">
          {readout.noSavedRecord && (
            <p className="personal-production-notes-callout" role="status">你还没有保存个人制作记录。</p>
          )}
          {readout.mediaState === "empty" && (
            <p className="personal-production-notes-callout" role="status">本章暂无分镜。</p>
          )}
          {readout.mediaState === "unreadable" && (
            <div className="personal-production-notes-callout" role="status">
              本章分镜或媒体信息暂不可读取，个人记录可查看，但无法核对当前状态。
              {requiresChapterReload && (
                <button className="text-button" onClick={onRetryChapter} type="button">重新读取章节</button>
              )}
            </div>
          )}
          {readout.mediaState === "mismatch" && (
            <div className="personal-production-notes-callout" role="status">
              章节分镜或媒体快照与记录不一致，无法确认当前状态。
              <button className="text-button" onClick={onRetryChapter} type="button">重新读取章节</button>
            </div>
          )}
          {!mediaSnapshotAvailable && readout.mediaState !== "unreadable" && (
            <div className="personal-production-notes-callout" role="status">
              原图列表尚未成功读取，个人记录可查看，但当前媒体无法核对。
              {requiresChapterReload && (
                <button className="text-button" onClick={onRetryChapter} type="button">重新读取章节</button>
              )}
            </div>
          )}
          {mediaSnapshotAvailable && hasUnverifiedFrames && readout.mediaState === "ready" && (
            <div className="personal-production-notes-callout" role="status">
              部分镜头与当前媒体快照无法核对，不会显示为当前已认可。
              <button className="text-button" onClick={onRetryChapter} type="button">重新读取章节</button>
            </div>
          )}

          {canShowStatusFilters && statusCounts !== null && visibleReadState.status === "ready" && (
            <div className="personal-production-status-filters" role="group" aria-label="按状态筛选">
              {productionNoteStatusFilterOptions.map((option) => {
                const count = statusCounts[option.value];
                const selected = selectedStatusFilter === option.value;
                return (
                  <button
                    aria-label={`${option.label} ${count}`}
                    aria-pressed={selected}
                    className={selected ? "is-selected" : ""}
                    key={option.value}
                    onClick={() => selectStatusFilter(option.value, visibleReadState.ticket, readout)}
                    type="button"
                  >
                    <span>{option.label}</span>
                    <span className="personal-production-status-filter-count" aria-hidden="true">{count}</span>
                  </button>
                );
              })}
            </div>
          )}

          {canShowStatusFilters && visibleFrames.length === 0 && (
            <div className="personal-production-filter-empty" role="status" aria-live="polite">
              <span>这份当前镜头记录中没有“{selectedStatusLabel}”状态。</span>
              <button
                className="text-button"
                onClick={() => {
                  if (visibleReadState.status === "ready" && readout !== null) {
                    selectStatusFilter("all", visibleReadState.ticket, readout);
                  }
                }}
                type="button"
              >
                查看全部记录
              </button>
            </div>
          )}

          {frameEntries.length > 0 && (
            <ol
              className="personal-production-notes-list"
              aria-label={canCreateFirstNotes ? "可新增个人记录的镜头" : "当前镜头制作记录"}
            >
              {frameEntries.map(({ frame, candidate }) => {
                const draft = candidate === null
                  ? null
                  : noteDrafts.get(candidate.storyboardAssetId) ?? {
                      status: candidate.initialStatus,
                      note: candidate.note,
                      requiresStatusConfirmation: false,
                    };
                const activeEditor = editorSessionRef.current;
                const editorSessionGeneration = candidate !== null
                  && activeEditor !== null
                  && sameEditCandidate(activeEditor.candidate, candidate)
                  ? activeEditor.generation
                  : null;
                const isEditing = candidate !== null
                  && editingFrameId === candidate.storyboardAssetId
                  && editorSessionGeneration !== null
                  && saveGate === null;

                return (
                <li className="personal-production-note-row" key={frame.position}>
                  <div className="personal-production-note-title">
                    <strong>镜头 {frame.position}</strong>
                    <span className={"personal-production-status status-" + frame.status}>
                      {statusLabel(frame.status)}
                    </span>
                  </div>
                  {frame.hasSavedNote && frame.status === "unreadable" ? (
                    <p className="personal-production-note-copy">这条记录无法识别，未计入当前状态。</p>
                  ) : frame.note !== "" ? (
                    <p className="personal-production-note-copy">{frame.note}</p>
                  ) : null}
                  {services.savePersonalProductionNote !== undefined
                    && candidate !== null
                    && draft !== null
                    && !isEditing
                    && saveGate === null && (
                    <button
                      className="text-button personal-production-note-edit-trigger"
                      onClick={() => openEditor(candidate)}
                      type="button"
                    >
                      {candidate.row.hasSavedNote
                        ? `编辑镜头 ${frame.position} 的个人记录`
                        : `新增镜头 ${frame.position} 的个人记录`}
                    </button>
                  )}
                  {services.savePersonalProductionNote !== undefined
                    && candidate !== null
                    && draft !== null
                    && isEditing
                    && editorSessionGeneration !== null && (
                    <PersonalProductionNoteEditor
                      candidate={candidate}
                      draft={draft}
                      disabled={saveState !== null}
                      onStatusChange={(status) => updateDraft(candidate, editorSessionGeneration, (current) => ({
                        ...current,
                        status,
                        requiresStatusConfirmation: false,
                      }))}
                      onNoteChange={(note) => updateDraft(candidate, editorSessionGeneration, (current) => ({ ...current, note }))}
                      onSave={() => saveNote(candidate, editorSessionGeneration)}
                      onCancel={() => cancelEditor(candidate, editorSessionGeneration)}
                    />
                  )}
                  {mediaSnapshotAvailable
                    && resolveProductionNoteFrameTarget(chapter, seriesId, assets, readout, frame) !== null && (
                    <button
                      className="text-button personal-production-locate-frame"
                      onClick={() => locateProductionNoteFrame(frame)}
                      type="button"
                    >
                      定位到记录镜头 {frame.position}
                    </button>
                  )}
                  {frameNavigationNotice === frame.position && (
                    <p className="personal-production-locate-frame-notice" role="status" aria-live="polite">
                      未能定位当前记录镜头，请确认镜头仍可见后重试。
                    </p>
                  )}
                </li>
                );
              })}
            </ol>
          )}

          {readout.orphanNotes.length > 0 && (
            <section className="personal-production-orphans" aria-labelledby="personal-production-orphans-heading">
              <h4 id="personal-production-orphans-heading">无法对应到当前镜头的旧记录</h4>
              <p>这些内容仅供查看，不计入当前制作状态。</p>
              <ul className="personal-production-notes-list">
                {readout.orphanNotes.map(({ projection }, index) => (
                  <li className="personal-production-note-row" key={index}>
                    {projection.kind === "unreadable" ? (
                      <p className="personal-production-note-copy">旧记录无法识别，未计入当前状态。</p>
                    ) : (
                      <>
                        <strong>{orphanStatusLabel(projection.value.status)}</strong>
                        {projection.value.note !== "" && (
                          <p className="personal-production-note-copy">{projection.value.note}</p>
                        )}
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="personal-production-resume-panel" aria-labelledby="personal-production-resume-heading">
            <h4 id="personal-production-resume-heading">续作位置</h4>
            {readout.resumePosition !== null ? (
              <div className="personal-production-resume-actions">
                <p className="personal-production-resume" role="note">
                  续作位置：镜头 {readout.resumePosition}。页面不会自动跳转。
                </p>
                {canLocateResume ? (
                  <button className="secondary-button" onClick={locateResume} type="button">
                    定位到续作镜头
                  </button>
                ) : (
                  <p className="personal-production-resume-unavailable" role="note">
                    续作位置已保存，但当前无法定位。
                  </p>
                )}
              </div>
            ) : savedResumeCandidate !== null ? (
              <div className="personal-production-resume-actions">
                <p className="personal-production-resume" role="note">
                  续作位置：镜头 {savedResumeCandidate.position}。页面不会自动跳转。
                </p>
                <p className="personal-production-resume-unavailable" role="note">
                  续作位置已保存，但当前无法定位。
                </p>
              </div>
            ) : readout.resumeIsInvalid || visibleReadState.status === "ready" && visibleReadState.snapshot.resume_frame_id !== null ? (
              <p className="personal-production-resume" role="note">续作位置无法与当前章节对应。</p>
            ) : visibleReadState.status === "ready" && visibleReadState.snapshot.revision > 0 ? (
              <p className="personal-production-resume" role="note">当前没有可对应的续作位置。</p>
            ) : (
              <p className="personal-production-resume" role="note">尚未设置续作位置。</p>
            )}

            {resumeWritesAvailable && visibleReadState.status === "ready" && (
              <div className="personal-production-resume-controls">
                <label htmlFor="personal-production-resume-target">选择续作镜头</label>
                <select
                  id="personal-production-resume-target"
                  aria-label="选择续作镜头"
                  disabled={!canChangeResume || resumeCandidates.length === 0}
                  onChange={(event) => selectResumeCandidate(
                    event.currentTarget.value,
                    visibleReadState.ticket,
                    visibleReadState.snapshot,
                  )}
                  value={selectedResumeCandidate?.storyboardAssetId ?? ""}
                >
                  <option value="">请选择有效镜头</option>
                  {resumeCandidates.map((candidate) => (
                    <option key={candidate.storyboardAssetId} value={candidate.storyboardAssetId}>
                      镜头 {candidate.position}
                    </option>
                  ))}
                </select>
                <button
                  className="secondary-button"
                  disabled={
                    !canChangeResume
                    || selectedResumeCandidate === null
                    || visibleReadState.snapshot.resume_frame_id === selectedResumeCandidate.storyboardAssetId
                  }
                  onClick={() => saveResumePosition(selectedResumeCandidate, resumeSelection)}
                  type="button"
                >
                  保存续作位置
                </button>
                {visibleReadState.snapshot.resume_frame_id !== null && (
                  <button
                    className="text-button"
                    disabled={!canClearResume}
                    onClick={() => saveResumePosition(null, null)}
                    type="button"
                  >
                    清除续作位置
                  </button>
                )}
              </div>
            )}
            {noteDraftsBlockResume && (
              <p className="personal-production-resume-blocked" role="note">
                请先保存备注，或重新读取以放弃未保存的个人记录修改，再修改续作位置。
              </p>
            )}
            {saveGate?.kind === "resume" && saveGate.status === "must-reread" && (
              <p className="personal-production-resume-blocked" role="note">
                请重新读取并核实后，再重新选择续作位置或确认清除。
              </p>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
