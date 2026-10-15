import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { Chapter, ChapterEditLockLeaseHandle, ChapterEditLockState, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import {
  FrameAssetReferencesPanel,
  type FrameAssetReferenceAssetTarget,
  type FrameAssetReferenceOwner,
} from "./FrameAssetReferencesPanel";
import {
  PersonalProductionNotesPanel,
  type PersonalProductionNoteFrameTarget,
  type PersonalProductionNoteReadIdentity,
  type PersonalProductionResumeTarget,
} from "./PersonalProductionNotesPanel";
import {
  PersonalRoughCutPanel,
  type PersonalRoughCutFrameTarget,
  type PersonalRoughCutReadIdentity,
} from "./PersonalRoughCutPanel";
import {
  findChapterFramePosition,
  findUniqueChapterStoryboardAsset,
  type ChapterFrameNavigationTarget,
} from "./assetFrameNavigation";
import { projectStoryboardFrames, type TextField } from "./storyboardProjection";
import { findRoughCutFramePosition } from "./roughCutFrameNavigation";
import { resolveProductionNoteFrameTarget } from "./productionNoteFrameNavigation";
import { ChapterManagementDialog, type ChapterManagementAction } from "./ChapterManagementDialog";
import { ChapterOrderDialog } from "./ChapterOrderDialog";
import { FrameTextEditor, type FrameTextSaveResult } from "./FrameTextEditor";
import { StoryboardGenerationDialog } from "../storyboard-generation/StoryboardGenerationDialog";
import {
  buildChapterReorderInput,
  inspectStoryboardReferences,
  isSyncedFrameContentValid,
  sameChapterContent,
  sameChapterOrderSnapshot,
  updateStoryboardFrameText,
} from "./chapterEditing";
import "./chapterEditing.css";

interface ChapterBrowserProps {
  series: Series;
  services: WorkspaceServices;
  userId: string;
  onBack(): void;
  onUnauthorized(): void;
  navigationIntent?: ChapterNavigationIntent | null;
  onNavigationConsumed?(navigationEpoch: number): void;
  onNavigationAbandoned?(navigationEpoch: number): void;
  onNavigateToAsset?(target: FrameAssetReferenceAssetTarget): void;
}

export interface ChapterNavigationIntent {
  userId: string;
  services: WorkspaceServices;
  seriesId: string;
  chapterId: string;
  navigationEpoch: number;
  frameTarget?: ChapterFrameNavigationTarget;
}

interface ChapterBrowserScope {
  userId: string;
  services: WorkspaceServices;
  seriesId: string;
}

interface StoryboardGenerationDialogOwner {
  scope: ChapterBrowserScope;
}

interface ChapterDirectorySnapshot {
  scope: ChapterBrowserScope;
  requestGeneration: number;
  chapters: Chapter[] | null;
}

interface ChapterMutationSelectionIntent {
  scope: ChapterBrowserScope;
  chapterId: string | null;
  fallbackIndex: number;
  message: string | null;
}

interface ChapterManagementRequest {
  action: ChapterManagementAction;
  scope: ChapterBrowserScope;
  chapter: Chapter | null;
}

interface ChapterOrderEditorContext {
  scope: ChapterBrowserScope;
  snapshot: Chapter[];
  requestGeneration: number;
}

interface FrameTextEditorContext {
  scope: ChapterBrowserScope;
  chapter: Chapter;
  position: number;
  initialText: string;
  requestGeneration: number;
  lifecycleController: AbortController;
  newlyAcquiredLock: boolean;
  lockNotice: string | null;
  lockLease: ChapterEditLockLeaseHandle | null;
  lockIdentity: { acquiredAt: string; userId: string } | null;
}

interface FrameEditingInvalidationOptions {
  deferLockRelease?: boolean;
  updateState?: boolean;
  refreshLockSnapshot?: boolean;
}

type NavigationIssue = "missing" | "duplicate" | "foreign" | "frame";

interface PendingFrameNavigation {
  epoch: number;
  target: ChapterFrameNavigationTarget;
  chapter: Chapter;
  chapterRequestGeneration: number;
  status: "awaiting-assets" | "unresolved";
  message?: string;
}

interface FrameNavigationFeedback {
  chapter: Chapter;
  assetSnapshot: NotesMediaSnapshot;
  position: number;
}

function sameChapterBrowserScope(left: ChapterBrowserScope, right: ChapterBrowserScope): boolean {
  return left.userId === right.userId
    && left.services === right.services
    && left.seriesId === right.seriesId;
}

function samePersonalNotesReadIdentity(
  left: PersonalProductionNoteReadIdentity,
  right: PersonalProductionNoteReadIdentity,
): boolean {
  return left.requestGeneration === right.requestGeneration
    && left.readout === right.readout;
}

function isVisibleStoryboardElement(element: HTMLElement): boolean {
  if (!element.isConnected || element.closest("[hidden], [inert], [aria-hidden='true']") !== null) {
    return false;
  }

  try {
    let current: Element | null = element;
    while (current !== null) {
      const style = window.getComputedStyle(current);
      if (
        style.display === "none"
        || style.visibility === "hidden"
        || style.visibility === "collapse"
        || style.contentVisibility === "hidden"
        || (style.opacity.trim() !== "" && Number(style.opacity) === 0)
      ) {
        return false;
      }
      current = current.parentElement;
    }
  } catch {
    return false;
  }

  return true;
}

export function isChapterSelectableFromDirectory(
  chapters: Chapter[] | null,
  chapterId: string,
  seriesId: string,
): boolean {
  if (chapters === null || chapterId.trim() === "") {
    return false;
  }

  const matches = chapters.filter((chapter) => chapter.id === chapterId);
  return matches.length === 1 && matches[0]?.series_id === seriesId;
}

function resolveNavigationTarget(
  chapters: Chapter[],
  intent: ChapterNavigationIntent,
  seriesId: string,
): { chapter: Chapter; issue: null } | { chapter: null; issue: NavigationIssue } {
  const matches = chapters.filter((chapter) => chapter.id === intent.chapterId);
  if (matches.length === 0) {
    return { chapter: null, issue: "missing" };
  }
  if (matches.length > 1) {
    return { chapter: null, issue: "duplicate" };
  }

  const [chapter] = matches;
  if (intent.seriesId !== seriesId || chapter?.series_id !== seriesId) {
    return { chapter: null, issue: "foreign" };
  }
  return chapter === undefined
    ? { chapter: null, issue: "missing" }
    : { chapter, issue: null };
}

function navigationIssueMessage(issue: NavigationIssue): string {
  switch (issue) {
    case "missing":
      return "目标章节已不在当前目录中。可重新读取目录，或从左侧选择其他章节。";
    case "duplicate":
      return "当前目录中有多个同 ID 章节，无法安全定位。可重新读取目录，或手动选择章节。";
    case "foreign":
      return "目标章节不属于当前剧集，无法打开。可从左侧选择当前剧集中的章节。";
    case "frame":
      return "目标镜头在当前章节快照中无法核对。可重新读取目录，或从左侧选择其他章节。";
  }
}

type ChapterErrorKind = "membership" | "forbidden" | "request";

interface ErrorPresentation {
  title: string;
  message: string;
  kind: ChapterErrorKind;
}

type AssetResult =
  | { chapterId: string; status: "loading" }
  | { chapterId: string; status: "ready"; assets: StoryboardAsset[] }
  | { chapterId: string; status: "error"; error: ApiError };

type NotesMediaSnapshot = Exclude<AssetResult, { status: "loading" }>;

interface PersonalNotesContext {
  userId: string;
  seriesId: string;
  services: WorkspaceServices;
  chapter: Chapter;
  assetSnapshot: NotesMediaSnapshot;
  chapterRequestGeneration: number;
  assetRequestGeneration: number;
  openEpoch: number;
}

interface ResumeLocationFeedback {
  owner: PersonalNotesContext;
  target: PersonalProductionResumeTarget;
}

interface RegisteredPersonalNotesRead {
  owner: PersonalNotesContext;
  identity: PersonalProductionNoteReadIdentity;
}

interface PersonalNoteFrameNavigationFeedback {
  owner: PersonalNotesContext;
  identity: PersonalProductionNoteReadIdentity;
  target: PersonalProductionNoteFrameTarget;
}

interface PersonalNotesReadWatermark {
  owner: PersonalNotesContext | null;
  requestGeneration: number;
  invalidatedGenerations: Set<number>;
}

interface PersonalRoughCutContext {
  userId: string;
  seriesId: string;
  chapter: Chapter;
  services: WorkspaceServices;
  openEpoch: number;
}

type ReadyAssetResult = Extract<AssetResult, { status: "ready" }>;

interface RoughCutFrameNavigationFeedback {
  owner: PersonalRoughCutContext;
  assetSnapshot: ReadyAssetResult;
  assetRequestGeneration: number;
  target: PersonalRoughCutFrameTarget;
}

const EMPTY_STORYBOARD_ASSETS: StoryboardAsset[] = [];

function utcTimestampMilliseconds(value: string): number {
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : value + "Z";
  return Date.parse(normalized);
}

function validOwnedChapterLock(lock: ChapterEditLockState, userId: string): boolean {
  return lock.locked
    && lock.is_mine === true
    && lock.user_id === userId
    && typeof lock.acquired_at === "string"
    && typeof lock.expires_at === "string"
    && utcTimestampMilliseconds(lock.expires_at) > Date.now();
}

function chapterEditLockIdentity(lock: ChapterEditLockState): FrameTextEditorContext["lockIdentity"] {
  if (lock.acquired_at === undefined || lock.user_id === undefined) return null;
  return { acquiredAt: lock.acquired_at, userId: lock.user_id };
}

function chapterLockSnapshot(lock: ChapterEditLockState): Record<string, unknown> | null {
  if (!lock.locked) return null;
  const snapshot: Record<string, unknown> = { locked: true };
  if (lock.username !== undefined) snapshot.locked_by_username = lock.username;
  if (lock.is_mine !== undefined) snapshot.is_mine = lock.is_mine;
  if (lock.expires_at !== undefined) snapshot.expires_at = lock.expires_at;
  return snapshot;
}

function sameChapterEditLockIdentity(
  left: FrameTextEditorContext["lockIdentity"],
  lock: ChapterEditLockState,
): boolean {
  return left !== null
    && lock.acquired_at === left.acquiredAt
    && lock.user_id === left.userId;
}

function readEditableFrameText(chapter: Chapter, position: number): string | null {
  const frame = chapter.content?.[position - 1];
  if (frame === undefined) return null;
  const value = frame.text;
  return value === undefined || value === null || typeof value === "string" ? value ?? "" : null;
}

function errorPresentation(error: ApiError): ErrorPresentation {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const isMembership = ["会员", "membership", "premium", "subscription"].some((word) => detail.includes(word));
    return isMembership
      ? {
          title: "当前账号暂不可查看内容",
          message: error.detail ?? "当前账号的访问资格暂不可用。",
          kind: "membership",
        }
      : {
          title: "没有访问权限",
          message: error.detail ?? "当前账号无权查看此剧集。",
          kind: "forbidden",
        };
  }
  if (error.status === 404) {
    return {
      title: "内容不存在",
      message: error.detail ?? "剧集或章节可能已被移除。",
      kind: "request",
    };
  }
  if (error.status === 422) {
    return {
      title: "请求未通过校验",
      message: error.detail ?? "本地服务无法识别当前请求。",
      kind: "request",
    };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "暂时无法读取内容", message: "本地服务暂时不可用，请稍后重试。", kind: "request" };
  }
  if (error.kind === "timeout") {
    return { title: "请求超时", message: "本地服务响应较慢，请重试。", kind: "request" };
  }
  if (error.kind === "network") {
    return { title: "无法连接本地服务", message: "请确认本地服务正在运行，然后重试。", kind: "request" };
  }
  if (error.kind === "invalid-response") {
    return { title: "服务返回的数据无法识别", message: error.message, kind: "request" };
  }
  return { title: "暂时无法读取内容", message: error.detail ?? error.message, kind: "request" };
}

function formatDate(value: string): string {
  const source = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : value + "Z";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(new Date(source));
}

function displayChapterTitle(chapter: Chapter): string {
  return chapter.title.trim() === "" ? "未命名章节" : chapter.title;
}

function lockSummary(lock: Record<string, unknown> | null): string | null {
  if (lock === null || lock.locked !== true) {
    return null;
  }
  const username = typeof lock.locked_by_username === "string" && lock.locked_by_username.trim() !== ""
    ? lock.locked_by_username
    : null;
  if (lock.is_mine === true) {
    return "你在其他位置持有编辑锁";
  }
  return username === null ? "当前有人正在编辑" : "当前由 " + username + " 编辑";
}

function textContents(field: TextField, missingMessage: string, invalidMessage: string): string {
  if (field.kind === "missing") {
    return missingMessage;
  }
  if (field.kind === "invalid") {
    return invalidMessage;
  }
  return field.value;
}

function ChapterLoadError({
  error,
  onRetry,
  onBack,
}: {
  error: ApiError;
  onRetry(): void;
  onBack(): void;
}) {
  const presentation = errorPresentation(error);
  return (
    <div className={"state-panel error-panel " + presentation.kind} role="alert">
      <div className="state-icon" aria-hidden="true">!</div>
      <div className="state-copy">
        <h2>{presentation.title}</h2>
        <p>{presentation.message}</p>
      </div>
      <button className="secondary-button" onClick={onRetry} type="button">重试读取</button>
      <button className="text-button chapter-error-back" onClick={onBack} type="button">返回剧集列表</button>
    </div>
  );
}

export function ChapterBrowser({
  series,
  services,
  userId,
  onBack,
  onUnauthorized,
  navigationIntent = null,
  onNavigationConsumed,
  onNavigationAbandoned,
  onNavigateToAsset,
}: ChapterBrowserProps) {
  const [chapters, setChapters] = useState<Chapter[] | null>(null);
  const [chaptersLoading, setChaptersLoading] = useState(true);
  const [chaptersError, setChaptersError] = useState<ApiError | null>(null);
  const [chapterManagement, setChapterManagement] = useState<ChapterManagementRequest | null>(null);
  const [storyboardGenerationDialog, setStoryboardGenerationDialog] = useState<StoryboardGenerationDialogOwner | null>(null);
  const [chapterOrderEditor, setChapterOrderEditor] = useState<ChapterOrderEditorContext | null>(null);
  const chapterOrderEditorRef = useRef<ChapterOrderEditorContext | null>(null);
  chapterOrderEditorRef.current = chapterOrderEditor;
  const [frameTextEditor, setFrameTextEditor] = useState<FrameTextEditorContext | null>(null);
  const frameTextEditorRef = useRef<FrameTextEditorContext | null>(null);
  frameTextEditorRef.current = frameTextEditor;
  const [frameEditOpening, setFrameEditOpening] = useState(false);
  const frameEditOpeningController = useRef<AbortController | null>(null);
  const frameEditOpeningChapterIdRef = useRef<string | null>(null);
  const invalidateFrameEditingWorkflowRef = useRef<(options?: FrameEditingInvalidationOptions) => void>(() => undefined);
  const pendingFrameLockReleaseRef = useRef<FrameTextEditorContext | null>(null);
  const [chapterMutationNotice, setChapterMutationNotice] = useState<string | null>(null);
  const [navigationIssue, setNavigationIssue] = useState<{ epoch: number; issue: NavigationIssue } | null>(null);
  const [pendingFrameNavigation, setPendingFrameNavigation] = useState<PendingFrameNavigation | null>(null);
  const pendingFrameNavigationRef = useRef<PendingFrameNavigation | null>(null);
  pendingFrameNavigationRef.current = pendingFrameNavigation;
  const [frameNavigationFeedback, setFrameNavigationFeedback] = useState<FrameNavigationFeedback | null>(null);
  const [chapterReloadKey, setChapterReloadKey] = useState(0);
  const chapterReloadSequence = useRef(0);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const selectedChapterIdRef = useRef(selectedChapterId);
  selectedChapterIdRef.current = selectedChapterId;
  const [assetResult, setAssetResult] = useState<AssetResult | null>(null);
  const assetResultRef = useRef<AssetResult | null>(assetResult);
  assetResultRef.current = assetResult;
  const [assetReloadKey, setAssetReloadKey] = useState(0);
  const [failedImagePositions, setFailedImagePositions] = useState<Set<number>>(() => new Set());
  const [personalNotesContext, setPersonalNotesContext] = useState<PersonalNotesContext | null>(null);
  const [resumeLocation, setResumeLocation] = useState<ResumeLocationFeedback | null>(null);
  const [personalNotesReadRegistration, setPersonalNotesReadRegistration] = useState<RegisteredPersonalNotesRead | null>(null);
  const personalNotesReadRegistrationRef = useRef<RegisteredPersonalNotesRead | null>(null);
  const personalNotesReadWatermarkRef = useRef<PersonalNotesReadWatermark>({ owner: null, requestGeneration: -1, invalidatedGenerations: new Set() });
  const [personalNoteFrameFeedback, setPersonalNoteFrameFeedback] = useState<PersonalNoteFrameNavigationFeedback | null>(null);
  const [frameAssetReferenceOwner, setFrameAssetReferenceOwner] = useState<FrameAssetReferenceOwner | null>(null);
  const [personalRoughCutContext, setPersonalRoughCutContext] = useState<PersonalRoughCutContext | null>(null);
  const [roughCutFrameFeedback, setRoughCutFrameFeedback] = useState<RoughCutFrameNavigationFeedback | null>(null);
  const roughCutContextRef = useRef<PersonalRoughCutContext | null>(personalRoughCutContext);
  roughCutContextRef.current = personalRoughCutContext;
  const roughCutOpenEpoch = useRef(0);
  const frameEditWorkflowEpoch = useRef(0);
  const chapterRequestGeneration = useRef(0);
  const chapterMutationSelectionIntent = useRef<ChapterMutationSelectionIntent | null>(null);
  const assetRequestGeneration = useRef(0);
  const personalNotesOpenEpoch = useRef(0);
  const personalNotesContextRef = useRef<PersonalNotesContext | null>(null);
  const frameAssetReferencesOpenEpoch = useRef(0);
  const frameAssetReferenceOwnerRef = useRef<FrameAssetReferenceOwner | null>(null);
  const assetCache = useRef(new Map<string, StoryboardAsset[]>());
  const storyboardFrameElements = useRef(new Map<number, HTMLElement>());
  const storyboardListElement = useRef<HTMLDivElement | null>(null);
  const activeScopeRef = useRef<ChapterBrowserScope>({ userId, services, seriesId: series.id });
  const chapterDirectoryRef = useRef<ChapterDirectorySnapshot>({
    scope: { userId, services, seriesId: series.id },
    requestGeneration: chapterRequestGeneration.current,
    chapters: null,
  });
  const navigationIntentRef = useRef(navigationIntent);
  navigationIntentRef.current = navigationIntent;
  const navigationIntentEpoch = navigationIntent?.navigationEpoch ?? null;
  const navigationIntentEpochRef = useRef(navigationIntentEpoch);
  const finalizedNavigationEpochsRef = useRef(new Set<number>());
  const unauthorizedHandlerRef = useRef(onUnauthorized);
  unauthorizedHandlerRef.current = onUnauthorized;
  const navigationConsumedHandlerRef = useRef(onNavigationConsumed);
  navigationConsumedHandlerRef.current = onNavigationConsumed;
  const navigationAbandonedHandlerRef = useRef(onNavigationAbandoned);
  navigationAbandonedHandlerRef.current = onNavigationAbandoned;

  const currentScope: ChapterBrowserScope = { userId, services, seriesId: series.id };
  if (!sameChapterBrowserScope(activeScopeRef.current, currentScope)) {
    if (navigationIntent !== null) {
      finalizedNavigationEpochsRef.current.add(navigationIntent.navigationEpoch);
    }
    activeScopeRef.current = currentScope;
    chapterRequestGeneration.current += 1;
    assetRequestGeneration.current += 1;
    assetCache.current.clear();
    personalNotesContextRef.current = null;
    personalNotesReadRegistrationRef.current = null;
    personalNotesReadWatermarkRef.current = { owner: null, requestGeneration: -1, invalidatedGenerations: new Set() };
    frameAssetReferenceOwnerRef.current = null;
    personalNotesOpenEpoch.current += 1;
    frameAssetReferencesOpenEpoch.current += 1;
    roughCutContextRef.current = null;
    roughCutOpenEpoch.current += 1;
    setChapters(null);
    setChaptersLoading(true);
    setChaptersError(null);
    setChapterManagement(null);
    setStoryboardGenerationDialog(null);
    chapterOrderEditorRef.current = null;
    setChapterOrderEditor(null);
    invalidateFrameEditingWorkflow({ deferLockRelease: true });
    setChapterMutationNotice(null);
    chapterMutationSelectionIntent.current = null;
    setNavigationIssue(null);
    setSelectedChapterId(null);
    selectedChapterIdRef.current = null;
    setAssetResult(null);
    setPendingFrameNavigation(null);
    setFrameNavigationFeedback(null);
    setPersonalNotesContext(null);
    setResumeLocation(null);
    setPersonalNotesReadRegistration(null);
    setPersonalNoteFrameFeedback(null);
    setFrameAssetReferenceOwner(null);
    setPersonalRoughCutContext(null);
    setRoughCutFrameFeedback(null);
  }
  chapterDirectoryRef.current = {
    scope: currentScope,
    requestGeneration: chapterRequestGeneration.current,
    chapters,
  };

  const closePersonalNotes = useCallback((
    expectedOwner?: PersonalNotesContext,
    expectedReadIdentity?: PersonalProductionNoteReadIdentity | null,
  ) => {
    if (expectedOwner !== undefined && personalNotesContextRef.current !== expectedOwner) {
      return;
    }
    if (expectedOwner !== undefined && expectedReadIdentity !== undefined) {
      const registered = personalNotesReadRegistrationRef.current;
      if (expectedReadIdentity === null) {
        if (registered?.owner === expectedOwner) {
          return;
        }
      } else if (
        registered?.owner !== expectedOwner
        || !samePersonalNotesReadIdentity(registered.identity, expectedReadIdentity)
      ) {
        return;
      }
    }
    personalNotesOpenEpoch.current += 1;
    personalNotesContextRef.current = null;
    personalNotesReadRegistrationRef.current = null;
    setPersonalNotesContext(null);
    setPersonalNotesReadRegistration(null);
    setPersonalNoteFrameFeedback(null);
    setResumeLocation(null);
  }, []);

  const closeFrameAssetReferences = useCallback((expectedOwner?: FrameAssetReferenceOwner) => {
    if (expectedOwner !== undefined && frameAssetReferenceOwnerRef.current !== expectedOwner) {
      return;
    }
    frameAssetReferencesOpenEpoch.current += 1;
    frameAssetReferenceOwnerRef.current = null;
    setFrameAssetReferenceOwner(null);
  }, []);

  useEffect(() => {
    const requestScope = { userId, services, seriesId: series.id };
    const requestGeneration = ++chapterRequestGeneration.current;
    const suppliedIntent = navigationIntent;
    const requestIntent = suppliedIntent !== null
      && !finalizedNavigationEpochsRef.current.has(suppliedIntent.navigationEpoch)
      ? suppliedIntent
      : null;
    const mutationSelection = suppliedIntent === null
      && chapterMutationSelectionIntent.current !== null
      && sameChapterBrowserScope(chapterMutationSelectionIntent.current.scope, requestScope)
      ? chapterMutationSelectionIntent.current
      : null;
    const controller = new AbortController();
    let current = true;
    assetCache.current.clear();
    if (mutationSelection === null) {
      setChapters(null);
      setSelectedChapterId(null);
      setAssetResult(null);
    }
    setChaptersError(null);
    setNavigationIssue(null);
    setPendingFrameNavigation(null);
    setFrameNavigationFeedback(null);
    setChapterMutationNotice(null);
    setChaptersLoading(true);

    const isCurrent = () => current
      && !controller.signal.aborted
      && chapterRequestGeneration.current === requestGeneration
      && sameChapterBrowserScope(requestScope, activeScopeRef.current);

    // Skip the first StrictMode probe before it can issue a duplicate GET.
    void Promise.resolve().then(() => {
      if (!isCurrent()) {
        return undefined;
      }
      return services.listChapters(series.id, controller.signal);
    }).then((result) => {
      if (!isCurrent() || result === undefined) {
        return;
      }
      setChapters(result);
      if (suppliedIntent === null) {
        setNavigationIssue(null);
        if (mutationSelection !== null) {
          const selectedById = mutationSelection.chapterId === null
            ? undefined
            : result.find((chapter) => chapter.id === mutationSelection.chapterId);
          const fallback = result.length === 0
            ? undefined
            : result[Math.min(mutationSelection.fallbackIndex, result.length - 1)];
          const nextSelected = selectedById ?? fallback ?? null;
          setSelectedChapterId(nextSelected?.id ?? null);
          selectedChapterIdRef.current = nextSelected?.id ?? null;
          setAssetResult(null);
          if (mutationSelection.message !== null) {
            setChapterMutationNotice(mutationSelection.message);
          }
          if (chapterMutationSelectionIntent.current === mutationSelection) {
            chapterMutationSelectionIntent.current = null;
          }
        } else {
          setSelectedChapterId(result[0]?.id ?? null);
          selectedChapterIdRef.current = result[0]?.id ?? null;
        }
      } else if (requestIntent === null) {
        setSelectedChapterId(null);
        selectedChapterIdRef.current = null;
        setNavigationIssue({ epoch: suppliedIntent.navigationEpoch, issue: "foreign" });
      } else {
        const intentMatchesScope = requestIntent.userId === userId
          && requestIntent.services === services
          && requestIntent.seriesId === series.id;
        if (!intentMatchesScope) {
          finalizedNavigationEpochsRef.current.add(requestIntent.navigationEpoch);
          setSelectedChapterId(null);
          selectedChapterIdRef.current = null;
          setNavigationIssue({ epoch: requestIntent.navigationEpoch, issue: "foreign" });
          navigationAbandonedHandlerRef.current?.(requestIntent.navigationEpoch);
        } else {
          const resolution = resolveNavigationTarget(result, requestIntent, series.id);
          if (resolution.chapter === null) {
            setSelectedChapterId(null);
            selectedChapterIdRef.current = null;
            setNavigationIssue({ epoch: requestIntent.navigationEpoch, issue: resolution.issue });
          } else if (requestIntent.frameTarget !== undefined) {
            const framePosition = findChapterFramePosition(resolution.chapter, requestIntent.frameTarget);
            if (framePosition === null) {
              setSelectedChapterId(null);
              selectedChapterIdRef.current = null;
              setNavigationIssue({ epoch: requestIntent.navigationEpoch, issue: "frame" });
            } else {
              setSelectedChapterId(resolution.chapter.id);
              selectedChapterIdRef.current = resolution.chapter.id;
              setNavigationIssue(null);
              setPendingFrameNavigation({
                epoch: requestIntent.navigationEpoch,
                target: requestIntent.frameTarget,
                chapter: resolution.chapter,
                chapterRequestGeneration: requestGeneration,
                status: "awaiting-assets",
              });
            }
          } else {
            setSelectedChapterId(resolution.chapter.id);
            selectedChapterIdRef.current = resolution.chapter.id;
            setNavigationIssue(null);
            finalizedNavigationEpochsRef.current.add(requestIntent.navigationEpoch);
            navigationConsumedHandlerRef.current?.(requestIntent.navigationEpoch);
          }
        }
      }
      setChaptersLoading(false);
    }).catch((cause: unknown) => {
      if (!isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        unauthorizedHandlerRef.current();
        return;
      }
      if (mutationSelection !== null) {
        if (mutationSelection.message !== null) {
          setChapterMutationNotice(`${mutationSelection.message}，但章节列表刷新失败，请重试读取。`);
        }
        if (chapterMutationSelectionIntent.current === mutationSelection) {
          chapterMutationSelectionIntent.current = null;
        }
      }
      if (suppliedIntent?.frameTarget !== undefined && requestIntent !== null) {
        setNavigationIssue({ epoch: requestIntent.navigationEpoch, issue: "frame" });
      }
      setChaptersError(error);
      setChaptersLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
      if (chapterRequestGeneration.current === requestGeneration) {
        chapterRequestGeneration.current += 1;
      }
    };
  }, [chapterReloadKey, series.id, services, userId]);

  const selectedChapter = useMemo(
    () => chapters?.find((chapter) => chapter.id === selectedChapterId) ?? null,
    [chapters, selectedChapterId],
  );
  const currentAssetResult = selectedChapter !== null && assetResult?.chapterId === selectedChapter.id
    ? assetResult
    : null;
  const frames = selectedChapter?.content ?? [];
  const personalNotesOpen = selectedChapter !== null
    && personalNotesContext !== null
    && personalNotesContextRef.current === personalNotesContext
    && personalNotesContext.userId === userId
    && personalNotesContext.seriesId === series.id
    && personalNotesContext.services === services
    && personalNotesContext.chapter === selectedChapter
    && personalNotesContext.assetSnapshot === currentAssetResult
    && personalNotesContext.openEpoch === personalNotesOpenEpoch.current;
  if (personalNotesContext !== null && !personalNotesOpen) {
    if (personalNotesContextRef.current === personalNotesContext) {
      personalNotesContextRef.current = null;
      personalNotesOpenEpoch.current += 1;
    }
    if (personalNotesReadRegistrationRef.current?.owner === personalNotesContext) {
      personalNotesReadRegistrationRef.current = null;
      setPersonalNotesReadRegistration(null);
    }
    setPersonalNotesContext(null);
    setResumeLocation(null);
    setPersonalNoteFrameFeedback((current) => current?.owner === personalNotesContext ? null : current);
  }
  const personalRoughCutScopeMatches = selectedChapter !== null
    && personalRoughCutContext?.userId === userId
    && personalRoughCutContext.seriesId === series.id
    && personalRoughCutContext.chapter === selectedChapter
    && personalRoughCutContext.services === services;
  if (personalRoughCutContext !== null && !personalRoughCutScopeMatches) {
    if (roughCutContextRef.current === personalRoughCutContext) {
      roughCutContextRef.current = null;
      roughCutOpenEpoch.current += 1;
    }
    setPersonalRoughCutContext(null);
    setRoughCutFrameFeedback((current) => current?.owner === personalRoughCutContext ? null : current);
  }
  const personalRoughCutOpen = selectedChapter !== null
    && personalRoughCutScopeMatches;
  const frameAssetReferenceScopeMatches = selectedChapter !== null
    && frameAssetReferenceOwner !== null
    && frameAssetReferenceOwnerRef.current === frameAssetReferenceOwner
    && frameAssetReferenceOwner.openEpoch === frameAssetReferencesOpenEpoch.current
    && frameAssetReferenceOwner.userId === userId
    && frameAssetReferenceOwner.seriesId === series.id
    && frameAssetReferenceOwner.services === services
    && frameAssetReferenceOwner.chapter === selectedChapter
    && frames[frameAssetReferenceOwner.position - 1] === frameAssetReferenceOwner.frame;
  if (frameAssetReferenceOwner !== null && !frameAssetReferenceScopeMatches) {
    if (frameAssetReferenceOwnerRef.current === frameAssetReferenceOwner) {
      frameAssetReferenceOwnerRef.current = null;
      frameAssetReferencesOpenEpoch.current += 1;
    }
    setFrameAssetReferenceOwner(null);
  }
  const frameAssetReferencesOpen = frameAssetReferenceOwner !== null
    && frameAssetReferenceScopeMatches;

  useEffect(() => {
    if (selectedChapter === null) {
      setAssetResult(null);
      return undefined;
    }

    const chapterId = selectedChapter.id;
    if ((selectedChapter.content?.length ?? 0) === 0) {
      setAssetResult({ chapterId, status: "ready", assets: [] });
      return undefined;
    }

    const cached = assetCache.current.get(chapterId);
    if (cached !== undefined) {
      setAssetResult({ chapterId, status: "ready", assets: cached });
      return undefined;
    }

    const controller = new AbortController();
    let current = true;
    const requestGeneration = ++assetRequestGeneration.current;
    setAssetResult({ chapterId, status: "loading" });

    const isCurrent = () => current
      && !controller.signal.aborted
      && assetRequestGeneration.current === requestGeneration
      && sameChapterBrowserScope(currentScope, activeScopeRef.current);

    void Promise.resolve().then(() => {
      if (!isCurrent()) {
        return undefined;
      }
      return services.listStoryboardAssets(series.id, chapterId, controller.signal);
    }).then((assets) => {
      if (!isCurrent() || assets === undefined) {
        return;
      }
      assetCache.current.set(chapterId, assets);
      setAssetResult({ chapterId, status: "ready", assets });
    }).catch((cause: unknown) => {
      if (!isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        unauthorizedHandlerRef.current();
        return;
      }
      setAssetResult({ chapterId, status: "error", error });
      const pending = pendingFrameNavigationRef.current;
      if (
        pending !== null
        && pending.status === "awaiting-assets"
        && pending.chapter === selectedChapter
        && pending.chapterRequestGeneration === chapterRequestGeneration.current
      ) {
        const unresolved: PendingFrameNavigation = {
          ...pending,
          status: "unresolved",
          message: "原图目录暂时无法读取，目标镜头尚未定位。可重新核对目标镜头。",
        };
        pendingFrameNavigationRef.current = unresolved;
        setPendingFrameNavigation(unresolved);
      }
    });

    return () => {
      current = false;
      controller.abort();
      if (assetRequestGeneration.current === requestGeneration) {
        assetRequestGeneration.current += 1;
      }
    };
  }, [assetReloadKey, selectedChapter, series.id, services, userId]);

  useEffect(() => {
    setFailedImagePositions(new Set());
  }, [assetReloadKey, selectedChapterId]);

  const frameViews = useMemo(
    () => selectedChapter === null
      ? []
      : projectStoryboardFrames(
          selectedChapter,
          currentAssetResult?.status === "ready" ? currentAssetResult.assets : [],
          services.apiBaseUrl,
        ),
    [currentAssetResult, selectedChapter, services.apiBaseUrl],
  );

  useEffect(() => {
    const pending = pendingFrameNavigation;
    const intent = navigationIntentRef.current;
    if (
      pending === null
      || pending.status !== "awaiting-assets"
      || intent === null
      || intent.navigationEpoch !== pending.epoch
      || intent.frameTarget === undefined
      || intent.frameTarget.storyboardAssetId !== pending.target.storyboardAssetId
      || intent.frameTarget.category !== pending.target.category
      || intent.frameTarget.assetId !== pending.target.assetId
      || selectedChapter !== pending.chapter
      || selectedChapterId !== pending.chapter.id
      || chapters === null
      || chapters.find((chapter) => chapter.id === pending.chapter.id) !== pending.chapter
      || pending.chapterRequestGeneration !== chapterRequestGeneration.current
      || chapterDirectoryRef.current.requestGeneration !== pending.chapterRequestGeneration
      || !sameChapterBrowserScope(chapterDirectoryRef.current.scope, activeScopeRef.current)
      || currentAssetResult?.status !== "ready"
    ) {
      return;
    }

    const framePosition = findChapterFramePosition(pending.chapter, pending.target);
    const matchedAsset = findUniqueChapterStoryboardAsset(
      currentAssetResult.assets,
      series.id,
      pending.chapter.id,
      pending.target,
    );
    const markUnresolved = (message: string) => {
      if (pendingFrameNavigationRef.current !== pending) {
        return;
      }
      const unresolved: PendingFrameNavigation = { ...pending, status: "unresolved", message };
      pendingFrameNavigationRef.current = unresolved;
      setPendingFrameNavigation(unresolved);
    };

    if (framePosition === null || matchedAsset === null) {
      markUnresolved("当前镜头快照与素材目录无法对应，目标镜头尚未定位。可重新核对目标镜头。");
      return;
    }

    const storyboardList = storyboardListElement.current;
    const frameElement = storyboardFrameElements.current.get(framePosition);
    if (
      storyboardList === null
      || frameElement === undefined
      || !frameElement.isConnected
      || !storyboardList.contains(frameElement)
      || frameElement.closest("[hidden], [inert]") !== null
      || !frameViews.some((frame) => frame.position === framePosition)
    ) {
      markUnresolved("目标镜头当前不可见，尚未完成定位。可重新核对目标镜头。");
      return;
    }

    frameElement.focus({ preventScroll: true });
    if (
      document.activeElement !== frameElement
      || pendingFrameNavigationRef.current !== pending
      || navigationIntentRef.current?.navigationEpoch !== pending.epoch
      || chapterRequestGeneration.current !== pending.chapterRequestGeneration
      || selectedChapterIdRef.current !== pending.chapter.id
      || selectedChapter !== pending.chapter
      || currentAssetResult !== assetResult
      || !frameElement.isConnected
      || !storyboardList.contains(frameElement)
      || frameElement.closest("[hidden], [inert]") !== null
    ) {
      markUnresolved("浏览器未能将焦点移到目标镜头，尚未完成定位。可重新核对目标镜头。");
      return;
    }

    frameElement.scrollIntoView?.({ behavior: "auto", block: "center" });
    setFrameNavigationFeedback({
      chapter: pending.chapter,
      assetSnapshot: currentAssetResult,
      position: framePosition,
    });
    finalizedNavigationEpochsRef.current.add(pending.epoch);
    pendingFrameNavigationRef.current = null;
    setPendingFrameNavigation(null);
    navigationConsumedHandlerRef.current?.(pending.epoch);
  }, [
    assetResult,
    chapterRequestGeneration,
    chapters,
    currentAssetResult,
    frameViews,
    pendingFrameNavigation,
    selectedChapter,
    selectedChapterId,
    series.id,
  ]);

  const isCurrentPersonalNotesOwner = useCallback((owner: PersonalNotesContext): boolean => (
    personalNotesContextRef.current === owner
    && owner.openEpoch === personalNotesOpenEpoch.current
    && owner.userId === userId
    && owner.seriesId === series.id
    && owner.services === services
    && owner.chapter === selectedChapter
    && owner.chapterRequestGeneration === chapterRequestGeneration.current
    && owner.assetRequestGeneration === assetRequestGeneration.current
    && owner.assetSnapshot === currentAssetResult
    && owner.assetSnapshot === assetResultRef.current
    && chapterDirectoryRef.current.requestGeneration === owner.chapterRequestGeneration
    && sameChapterBrowserScope(chapterDirectoryRef.current.scope, activeScopeRef.current)
    && chapterDirectoryRef.current.chapters === chapters
    && chapterDirectoryRef.current.chapters?.filter((chapter) => chapter.id === owner.chapter.id).length === 1
    && chapterDirectoryRef.current.chapters?.find((chapter) => chapter.id === owner.chapter.id) === owner.chapter
    && selectedChapterIdRef.current === owner.chapter.id
  ), [assetRequestGeneration, chapters, currentAssetResult, selectedChapter, series.id, services, userId]);

  const registerPersonalNotesFrameRead = useCallback((identity: PersonalProductionNoteReadIdentity) => {
    const owner = personalNotesContext;
    if (owner === null || !isCurrentPersonalNotesOwner(owner)) {
      return;
    }

    let watermark = personalNotesReadWatermarkRef.current;
    if (watermark.owner !== owner) {
      watermark = { owner, requestGeneration: -1, invalidatedGenerations: new Set() };
    }
    if (
      identity.requestGeneration < watermark.requestGeneration
      || watermark.invalidatedGenerations.has(identity.requestGeneration)
    ) {
      return;
    }
    if (identity.requestGeneration > watermark.requestGeneration) {
      watermark = {
        owner,
        requestGeneration: identity.requestGeneration,
        invalidatedGenerations: watermark.invalidatedGenerations,
      };
      personalNotesReadWatermarkRef.current = watermark;
    } else if (personalNotesReadWatermarkRef.current.owner !== owner) {
      personalNotesReadWatermarkRef.current = watermark;
    }

    const current = personalNotesReadRegistrationRef.current;
    if (current?.owner === owner && current.identity.requestGeneration === identity.requestGeneration) {
      if (current.identity.readout !== null && identity.readout === null) {
        return;
      }
      if (current.identity.readout !== null && current.identity.readout !== identity.readout) {
        return;
      }
      if (samePersonalNotesReadIdentity(current.identity, identity)) {
        return;
      }
    }

    const next = { owner, identity };
    personalNotesReadRegistrationRef.current = next;
    setPersonalNotesReadRegistration(next);
    setPersonalNoteFrameFeedback((feedback) => (
      feedback?.owner === owner
      && feedback.identity.requestGeneration !== identity.requestGeneration
        ? null
        : feedback
    ));
  }, [isCurrentPersonalNotesOwner, personalNotesContext]);

  const invalidatePersonalNotesFrameRead = useCallback((identity: PersonalProductionNoteReadIdentity) => {
    const owner = personalNotesContext;
    if (owner === null || !isCurrentPersonalNotesOwner(owner)) {
      return;
    }
    const current = personalNotesReadRegistrationRef.current;
    if (
      current?.owner !== owner
      || !samePersonalNotesReadIdentity(current.identity, identity)
    ) {
      return;
    }

    const invalidatedIdentity = {
      readout: null,
      requestGeneration: identity.requestGeneration,
    };
    const watermark = personalNotesReadWatermarkRef.current;
    if (watermark.owner === owner) {
      personalNotesReadWatermarkRef.current = {
        ...watermark,
        invalidatedGenerations: new Set([...watermark.invalidatedGenerations, identity.requestGeneration]),
      };
    }
    const next = { owner, identity: invalidatedIdentity };
    personalNotesReadRegistrationRef.current = next;
    setPersonalNotesReadRegistration(next);
    setPersonalNoteFrameFeedback((feedback) => (
      feedback?.owner === owner
      && samePersonalNotesReadIdentity(feedback.identity, identity)
        ? null
        : feedback
    ));
  }, [isCurrentPersonalNotesOwner, personalNotesContext]);

  const onLocateProductionNoteFrame = useCallback((target: PersonalProductionNoteFrameTarget): boolean => {
    const owner = personalNotesContext;
    const registration = personalNotesReadRegistrationRef.current;
    if (
      owner === null
      || !isCurrentPersonalNotesOwner(owner)
      || registration?.owner !== owner
      || registration.identity.readout === null
      || !samePersonalNotesReadIdentity(registration.identity, target.readIdentity)
      || registration.identity.readout !== target.readout
      || !target.isCurrent()
      || target.scope.contextToken !== owner
      || target.scope.assetSnapshotToken !== owner.assetSnapshot
      || target.scope.userId !== owner.userId
      || target.scope.seriesId !== owner.seriesId
      || target.scope.services !== owner.services
      || target.scope.chapter !== owner.chapter
      || target.scope.generation !== owner.openEpoch
      || target.scope.assets !== (owner.assetSnapshot.status === "ready" ? owner.assetSnapshot.assets : EMPTY_STORYBOARD_ASSETS)
      || owner.assetSnapshot.status !== "ready"
      || !target.scope.mediaSnapshotAvailable
    ) {
      return false;
    }

    const resolveCurrentTarget = () => resolveProductionNoteFrameTarget(
      owner.chapter,
      owner.seriesId,
      owner.assetSnapshot.status === "ready" ? owner.assetSnapshot.assets : [],
      registration.identity.readout!,
      target.row,
    );
    const isCurrent = () => {
      const latest = personalNotesReadRegistrationRef.current;
      const resolved = resolveCurrentTarget();
      return isCurrentPersonalNotesOwner(owner)
        && latest?.owner === owner
        && samePersonalNotesReadIdentity(latest.identity, target.readIdentity)
        && latest.identity.readout === target.readout
        && target.isCurrent()
        && resolved !== null
        && resolved.position === target.position
        && resolved.storyboardAssetId === target.storyboardAssetId
        && resolved.frame === target.frame;
    };
    if (!isCurrent() || !frameViews.some((frame) => frame.position === target.position)) {
      return false;
    }

    const storyboardList = storyboardListElement.current;
    const frameElement = storyboardFrameElements.current.get(target.position);
    if (
      storyboardList === null
      || frameElement === undefined
      || !frameElement.isConnected
      || !storyboardList.contains(frameElement)
      || !isVisibleStoryboardElement(frameElement)
    ) {
      return false;
    }

    try {
      frameElement.focus({ preventScroll: true });
    } catch {
      return false;
    }
    if (
      document.activeElement !== frameElement
      || !isCurrent()
      || storyboardFrameElements.current.get(target.position) !== frameElement
      || !isVisibleStoryboardElement(frameElement)
      || !storyboardList.contains(frameElement)
      || !frameViews.some((frame) => frame.position === target.position)
    ) {
      return false;
    }

    try {
      frameElement.scrollIntoView?.({ behavior: "auto", block: "center" });
    } catch {
      return false;
    }
    setPersonalNoteFrameFeedback({ owner, identity: target.readIdentity, target });
    return true;
  }, [frameViews, isCurrentPersonalNotesOwner, personalNotesContext]);

  const onClosePersonalNotes = useCallback((identity: PersonalProductionNoteReadIdentity | null) => {
    const owner = personalNotesContext;
    if (owner !== null && isCurrentPersonalNotesOwner(owner)) {
      closePersonalNotes(owner, identity);
    }
  }, [closePersonalNotes, isCurrentPersonalNotesOwner, personalNotesContext]);

  const onLocateResume = useCallback((target: PersonalProductionResumeTarget) => {
    const owner = personalNotesContext;
    if (
      owner === null
      || !isCurrentPersonalNotesOwner(owner)
      || !target.isCurrent()
      || !Number.isSafeInteger(target.position)
      || target.position < 1
      || !frameViews.some((frame) => frame.position === target.position)
    ) {
      return;
    }

    const storyboardList = storyboardListElement.current;
    const frameElement = storyboardFrameElements.current.get(target.position);
    if (
      storyboardList === null
      || frameElement === undefined
      || !frameElement.isConnected
      || !storyboardList.contains(frameElement)
      || frameElement.closest("[hidden], [inert]") !== null
    ) {
      return;
    }

    frameElement.focus({ preventScroll: true });
    if (!isCurrentPersonalNotesOwner(owner) || !target.isCurrent()) {
      return;
    }
    frameElement.scrollIntoView?.({ behavior: "auto", block: "center" });
    setResumeLocation({ owner, target });
  }, [frameViews, isCurrentPersonalNotesOwner, personalNotesContext]);

  const onResumeInvalidated = useCallback(() => {
    const owner = personalNotesContext;
    if (owner === null || !isCurrentPersonalNotesOwner(owner)) {
      return;
    }
    setResumeLocation((current) => current?.owner === owner ? null : current);
  }, [isCurrentPersonalNotesOwner, personalNotesContext]);

  const onRoughCutFrameNavigationInvalidated = useCallback((identity: PersonalRoughCutReadIdentity) => {
    const owner = personalRoughCutContext;
    if (owner === null || roughCutContextRef.current !== owner) {
      return;
    }
    setRoughCutFrameFeedback((current) => (
      current?.owner === owner
      && current.target.snapshot === identity.snapshot
      && current.target.roughCutRequestGeneration === identity.requestGeneration
        ? null
        : current
    ));
  }, [personalRoughCutContext]);

  const onLocateRoughCutFrame = useCallback((target: PersonalRoughCutFrameTarget): boolean => {
    const owner = personalRoughCutContext;
    const assetSnapshot = currentAssetResult;
    const requestGeneration = assetRequestGeneration.current;
    if (
      owner === null
      || assetSnapshot === null
      || assetSnapshot.status !== "ready"
      || target.chapter !== owner.chapter
      || target.assetSnapshotToken !== assetSnapshot
      || target.assets !== assetSnapshot.assets
      || target.assetRequestGeneration !== requestGeneration
      || !Number.isSafeInteger(target.roughCutRequestGeneration)
      || target.roughCutRequestGeneration < 1
      || !Number.isSafeInteger(target.position)
      || target.position < 1
      || !frameViews.some((frame) => frame.position === target.position)
    ) {
      return false;
    }

    const targetMatchesCurrentSnapshot = () => target.chapter === owner.chapter
      && target.assetSnapshotToken === assetSnapshot
      && target.assets === assetSnapshot.assets
      && target.assetRequestGeneration === requestGeneration
      && findRoughCutFramePosition(
        owner.chapter,
        owner.seriesId,
        target.snapshot,
        target.row,
        target.assets,
      ) === target.position;
    const isCurrent = () => roughCutContextRef.current === owner
      && owner.openEpoch === roughCutOpenEpoch.current
      && owner.userId === userId
      && owner.services === services
      && owner.seriesId === series.id
      && owner.chapter === selectedChapter
      && selectedChapterIdRef.current === owner.chapter.id
      && chapterDirectoryRef.current.requestGeneration === chapterRequestGeneration.current
      && assetResultRef.current === assetSnapshot
      && assetSnapshot.chapterId === owner.chapter.id
      && assetSnapshot.status === "ready"
      && assetRequestGeneration.current === requestGeneration
      && targetMatchesCurrentSnapshot()
      && target.isCurrent();
    if (!isCurrent()) {
      return false;
    }

    const storyboardList = storyboardListElement.current;
    const frameElement = storyboardFrameElements.current.get(target.position);
    if (
      storyboardList === null
      || frameElement === undefined
      || !frameElement.isConnected
      || !storyboardList.contains(frameElement)
      || frameElement.closest("[hidden], [inert]") !== null
    ) {
      return false;
    }

    try {
      frameElement.focus({ preventScroll: true });
    } catch {
      return false;
    }

    if (
      document.activeElement !== frameElement
      || !isCurrent()
      || !targetMatchesCurrentSnapshot()
      || storyboardFrameElements.current.get(target.position) !== frameElement
      || !frameElement.isConnected
      || !storyboardList.contains(frameElement)
      || frameElement.closest("[hidden], [inert]") !== null
    ) {
      return false;
    }

    frameElement.scrollIntoView?.({ behavior: "auto", block: "center" });
    setRoughCutFrameFeedback({ owner, assetSnapshot, assetRequestGeneration: requestGeneration, target });
    return true;
  }, [currentAssetResult, frameViews, personalRoughCutContext, selectedChapter, series.id, services, userId]);

  const currentResumeLocation = resumeLocation !== null
    && resumeLocation.owner === personalNotesContext
    && personalNotesOpen
    && resumeLocation.target.isCurrent()
    ? resumeLocation
    : null;
  const currentPersonalNoteFrameFeedback = personalNoteFrameFeedback !== null
    && personalNoteFrameFeedback.owner === personalNotesContext
    && personalNotesOpen
    && personalNotesReadRegistration?.owner === personalNotesContext
    && samePersonalNotesReadIdentity(personalNotesReadRegistration.identity, personalNoteFrameFeedback.identity)
    && personalNoteFrameFeedback.target.isCurrent()
    ? personalNoteFrameFeedback
    : null;

  function retryChapters() {
    invalidateFrameEditingWorkflow();
    chapterRequestGeneration.current += 1;
    assetRequestGeneration.current += 1;
    selectedChapterIdRef.current = null;
    pendingFrameNavigationRef.current = null;
    setPendingFrameNavigation(null);
    setFrameNavigationFeedback(null);
    closePersonalNotes();
    closeFrameAssetReferences();
    closePersonalRoughCut();
    assetCache.current.clear();
    setChapters(null);
    setSelectedChapterId(null);
    setAssetResult(null);
    requestChapterDirectoryReload(null);
  }

  function requestChapterDirectoryReload(selection: ChapterMutationSelectionIntent | null): void {
    chapterMutationSelectionIntent.current = selection;
    chapterReloadSequence.current += 1;
    setChapterReloadKey(chapterReloadSequence.current);
  }

  useLayoutEffect(() => {
    const previousEpoch = navigationIntentEpochRef.current;
    navigationIntentEpochRef.current = navigationIntentEpoch;
    if (navigationIntentEpoch === null || navigationIntentEpoch === previousEpoch) return;

    const intent = navigationIntentRef.current;
    if (intent === null || finalizedNavigationEpochsRef.current.has(intent.navigationEpoch)
      || !sameChapterBrowserScope({
        userId: intent.userId,
        services: intent.services,
        seriesId: intent.seriesId,
      }, activeScopeRef.current)) return;

    retryChapters();
  }, [navigationIntentEpoch]);

  function retryAssets() {
    const pending = pendingFrameNavigationRef.current;
    if (
      pending !== null
      && navigationIntentRef.current?.navigationEpoch === pending.epoch
    ) {
      retryChapters();
      return;
    }
    closePersonalNotes();
    closeFrameAssetReferences();
    closePersonalRoughCut();
    if (selectedChapter === null) {
      return;
    }
    assetRequestGeneration.current += 1;
    assetCache.current.delete(selectedChapter.id);
    setAssetResult({ chapterId: selectedChapter.id, status: "loading" });
    setAssetReloadKey((key) => key + 1);
  }

  function selectChapter(chapterId: string, expectedRequestGeneration: number) {
    const directory = chapterDirectoryRef.current;
    if (
      expectedRequestGeneration !== chapterRequestGeneration.current
      || expectedRequestGeneration !== directory.requestGeneration
      || !sameChapterBrowserScope(directory.scope, activeScopeRef.current)
      || !isChapterSelectableFromDirectory(directory.chapters, chapterId, directory.scope.seriesId)
      || selectedChapterIdRef.current === chapterId
    ) {
      return;
    }

    invalidateFrameEditingWorkflow();

    const pendingIntent = navigationIntentRef.current;
    if (pendingIntent !== null && !finalizedNavigationEpochsRef.current.has(pendingIntent.navigationEpoch)) {
      finalizedNavigationEpochsRef.current.add(pendingIntent.navigationEpoch);
      navigationAbandonedHandlerRef.current?.(pendingIntent.navigationEpoch);
    }
    pendingFrameNavigationRef.current = null;
    setPendingFrameNavigation(null);
    setFrameNavigationFeedback(null);
    assetRequestGeneration.current += 1;
    closePersonalNotes();
    closeFrameAssetReferences();
    closePersonalRoughCut();
    selectedChapterIdRef.current = chapterId;
    setSelectedChapterId(chapterId);
  }

  function closePersonalRoughCut(expectedOwner?: PersonalRoughCutContext) {
    if (expectedOwner !== undefined && roughCutContextRef.current !== expectedOwner) {
      return;
    }
    roughCutOpenEpoch.current += 1;
    roughCutContextRef.current = null;
    setPersonalRoughCutContext(null);
    setRoughCutFrameFeedback((current) => (
      expectedOwner === undefined || current?.owner === expectedOwner ? null : current
    ));
  }

  function openFrameAssetReferences(frame: StoryboardFrame, position: number) {
    if (selectedChapter === null || frames[position - 1] !== frame) {
      return;
    }
    closePersonalNotes();
    closePersonalRoughCut();
    closeFrameAssetReferences();
    const owner: FrameAssetReferenceOwner = {
      userId,
      seriesId: series.id,
      services,
      chapter: selectedChapter,
      frame,
      position,
      openEpoch: frameAssetReferencesOpenEpoch.current,
    };
    frameAssetReferenceOwnerRef.current = owner;
    setFrameAssetReferenceOwner(owner);
  }

  function openPersonalNotes() {
    if (
      selectedChapter === null
      || currentAssetResult === null
      || currentAssetResult.status === "loading"
    ) {
      return;
    }
    closeFrameAssetReferences();
    closePersonalRoughCut();
    closePersonalNotes();
    const context: PersonalNotesContext = {
      userId,
      seriesId: series.id,
      services,
      chapter: selectedChapter,
      assetSnapshot: currentAssetResult,
      chapterRequestGeneration: chapterRequestGeneration.current,
      assetRequestGeneration: assetRequestGeneration.current,
      openEpoch: personalNotesOpenEpoch.current,
    };
    personalNotesContextRef.current = context;
    personalNotesReadRegistrationRef.current = null;
    personalNotesReadWatermarkRef.current = { owner: context, requestGeneration: -1, invalidatedGenerations: new Set() };
    setPersonalNotesContext(context);
    setPersonalNotesReadRegistration(null);
    setPersonalNoteFrameFeedback(null);
  }

  function openPersonalRoughCut() {
    if (selectedChapter === null) {
      return;
    }
    closePersonalNotes();
    closeFrameAssetReferences();
    const context: PersonalRoughCutContext = {
      userId,
      seriesId: series.id,
      chapter: selectedChapter,
      services,
      openEpoch: ++roughCutOpenEpoch.current,
    };
    roughCutContextRef.current = context;
    setRoughCutFrameFeedback(null);
    setPersonalRoughCutContext(context);
  }

  function backToSeries() {
    const pendingIntent = navigationIntentRef.current;
    if (pendingIntent !== null && !finalizedNavigationEpochsRef.current.has(pendingIntent.navigationEpoch)) {
      finalizedNavigationEpochsRef.current.add(pendingIntent.navigationEpoch);
      navigationAbandonedHandlerRef.current?.(pendingIntent.navigationEpoch);
    }
    chapterRequestGeneration.current += 1;
    assetRequestGeneration.current += 1;
    invalidateFrameEditingWorkflow();
    pendingFrameNavigationRef.current = null;
    setPendingFrameNavigation(null);
    setFrameNavigationFeedback(null);
    closePersonalNotes();
    closeFrameAssetReferences();
    closePersonalRoughCut();
    onBack();
  }

  function applyFreshChapterDirectory(nextChapters: Chapter[], scope: ChapterBrowserScope): void {
    if (!sameChapterBrowserScope(scope, activeScopeRef.current)) return;
    const requestGeneration = chapterRequestGeneration.current;
    chapterDirectoryRef.current = { scope, requestGeneration, chapters: nextChapters };
    setChapters(nextChapters);
    setChaptersError(null);
    setChaptersLoading(false);
  }

  function refreshChapterOrderEditorSnapshot(context: ChapterOrderEditorContext, nextChapters: Chapter[]): void {
    if (chapterOrderEditorRef.current !== context) return;
    const refreshed = { ...context, snapshot: nextChapters };
    chapterOrderEditorRef.current = refreshed;
    setChapterOrderEditor(refreshed);
  }

  async function releaseNewlyAcquiredChapterLock(
    owner: FrameTextEditorContext,
    options: { refreshLockSnapshot?: boolean } = {},
  ): Promise<void> {
    if (!owner.newlyAcquiredLock || owner.lockLease === null
      || owner.scope.services.releaseChapterEditLock === undefined) return;
    const controller = new AbortController();
    const directoryAtRelease = options.refreshLockSnapshot ? chapterDirectoryRef.current : null;
    const workflowEpoch = frameEditWorkflowEpoch.current;
    const canRefreshLockSnapshot = (): boolean => {
      const directory = chapterDirectoryRef.current;
      const matchingChapters = directory.chapters?.filter((chapter) => chapter.id === owner.chapter.id) ?? [];
      return directoryAtRelease !== null
        && sameChapterBrowserScope(directoryAtRelease.scope, owner.scope)
        && directoryAtRelease.requestGeneration === owner.requestGeneration
        && directory.chapters === directoryAtRelease.chapters
        && directory.requestGeneration === directoryAtRelease.requestGeneration
        && directory.requestGeneration === owner.requestGeneration
        && chapterRequestGeneration.current === owner.requestGeneration
        && sameChapterBrowserScope(directory.scope, owner.scope)
        && sameChapterBrowserScope(owner.scope, activeScopeRef.current)
        && selectedChapterIdRef.current === owner.chapter.id
        && matchingChapters.length === 1
        && matchingChapters[0]?.series_id === owner.scope.seriesId
        && frameEditWorkflowEpoch.current === workflowEpoch
        && frameTextEditorRef.current === null
        && frameEditOpeningController.current === null
        && frameEditOpeningChapterIdRef.current === null
        && chapterOrderEditorRef.current === null;
    };
    try {
      await owner.scope.services.releaseChapterEditLock(owner.chapter.id, controller.signal, owner.lockLease);
    } catch (cause) {
      const error = normalizeApiError(cause);
      if (error.status === 401 && sameChapterBrowserScope(owner.scope, activeScopeRef.current)) {
        unauthorizedHandlerRef.current();
      }
      // Release is best-effort; a failed release must not hide the editing result.
      return;
    }

    const getChapterEditLock = owner.scope.services.getChapterEditLock;
    if (getChapterEditLock === undefined || !canRefreshLockSnapshot()) return;
    let currentLock: ChapterEditLockState;
    try {
      // The successful DELETE consumes the opaque lease, so this read is deliberately unleased.
      currentLock = await getChapterEditLock(owner.chapter.id, controller.signal);
    } catch (cause) {
      if (normalizeApiError(cause).status === 401 && canRefreshLockSnapshot()) {
        unauthorizedHandlerRef.current();
      }
      return;
    }
    if (!canRefreshLockSnapshot()) return;

    const currentDirectory = chapterDirectoryRef.current;
    const currentChapters = currentDirectory.chapters;
    if (currentChapters === null) return;
    const nextLock = chapterLockSnapshot(currentLock);
    const nextChapters = currentChapters.map((chapter) => chapter.id === owner.chapter.id
      ? { ...chapter, lock: nextLock }
      : chapter);
    applyFreshChapterDirectory(nextChapters, owner.scope);
  }

  function invalidateFrameEditingWorkflow(options: FrameEditingInvalidationOptions = {}): void {
    frameEditWorkflowEpoch.current += 1;
    const openingController = frameEditOpeningController.current;
    frameEditOpeningController.current = null;
    frameEditOpeningChapterIdRef.current = null;
    openingController?.abort();

    const owner = frameTextEditorRef.current;
    const pendingOwner = pendingFrameLockReleaseRef.current;
    frameTextEditorRef.current = null;
    pendingFrameLockReleaseRef.current = null;
    owner?.lifecycleController.abort();
    const cleanupOwner = owner ?? pendingOwner;
    if (cleanupOwner?.newlyAcquiredLock) {
      if (options.deferLockRelease) pendingFrameLockReleaseRef.current = cleanupOwner;
      else void releaseNewlyAcquiredChapterLock(cleanupOwner, {
        refreshLockSnapshot: options.refreshLockSnapshot === true && owner === cleanupOwner,
      });
    }

    if (options.updateState !== false) {
      setFrameTextEditor(null);
      setFrameEditOpening(false);
    }
  }

  invalidateFrameEditingWorkflowRef.current = invalidateFrameEditingWorkflow;

  useLayoutEffect(() => {
    const owner = frameTextEditorRef.current;
    const openingChapterId = frameEditOpeningChapterIdRef.current;
    if ((owner !== null && owner.chapter.id !== selectedChapterId)
      || (openingChapterId !== null && openingChapterId !== selectedChapterId)) {
      invalidateFrameEditingWorkflowRef.current();
    }
  }, [selectedChapterId]);

  useEffect(() => () => {
    invalidateFrameEditingWorkflowRef.current({ updateState: false });
  }, []);

  useEffect(() => {
    const owner = pendingFrameLockReleaseRef.current;
    pendingFrameLockReleaseRef.current = null;
    if (owner !== null) void releaseNewlyAcquiredChapterLock(owner);
  }, [services, series.id, userId]);

  function closeFrameTextEditor(expected?: FrameTextEditorContext): void {
    const current = frameTextEditorRef.current;
    if (current === null || (expected !== undefined && current !== expected)) return;
    invalidateFrameEditingWorkflow({ refreshLockSnapshot: true });
  }

  function openChapterOrderEditor(): void {
    const directory = chapterDirectoryRef.current;
    if (!canReorderChapters || chapterManagement !== null || chapterManagementBlocked || frameEditOpening || chapters === null
      || chaptersLoading || chaptersError !== null || chapterOrderEditorRef.current !== null
      || frameTextEditorRef.current !== null
      || !sameChapterBrowserScope(directory.scope, currentScope)
      || directory.requestGeneration !== chapterRequestGeneration.current || directory.chapters !== chapters) return;
    const context: ChapterOrderEditorContext = {
      scope: currentScope,
      snapshot: chapters,
      requestGeneration: directory.requestGeneration,
    };
    chapterOrderEditorRef.current = context;
    setChapterOrderEditor(context);
    setChapterMutationNotice(null);
  }

  function isCurrentOrderEditor(context: ChapterOrderEditorContext, signal: AbortSignal): boolean {
    const directory = chapterDirectoryRef.current;
    return !signal.aborted
      && chapterOrderEditorRef.current === context
      && sameChapterBrowserScope(context.scope, activeScopeRef.current)
      && context.requestGeneration === chapterRequestGeneration.current
      && sameChapterBrowserScope(directory.scope, context.scope)
      && directory.requestGeneration === context.requestGeneration;
  }

  async function saveChapterOrder(orderedIds: string[], signal: AbortSignal): Promise<void> {
    const context = chapterOrderEditorRef.current;
    if (context === null || !isCurrentOrderEditor(context, signal)) {
      throw new ApiError("aborted", "章节目录已变化，请重新打开排序窗口。", null);
    }
    if (context.scope.services.reorderChapters === undefined) {
      throw new ApiError("invalid-response", "当前服务不支持章节排序。", null);
    }
    const proposed = buildChapterReorderInput(context.snapshot, orderedIds, context.scope.seriesId);
    if (proposed === null) throw new ApiError("http", "排序草稿包含重复或缺失的章节，未发送修改。", 409);

    let currentDirectory: Chapter[];
    try {
      currentDirectory = await context.scope.services.listChapters(context.scope.seriesId, signal);
    } catch (cause) {
      if (!isCurrentOrderEditor(context, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (normalizeApiError(cause).status === 401) unauthorizedHandlerRef.current();
      throw cause;
    }
    if (!isCurrentOrderEditor(context, signal)) throw new ApiError("aborted", "Request was cancelled.");
    if (!sameChapterOrderSnapshot(context.snapshot, currentDirectory, context.scope.seriesId)) {
      applyFreshChapterDirectory(currentDirectory, context.scope);
      refreshChapterOrderEditorSnapshot(context, currentDirectory);
      throw new ApiError("http", "章节目录或顺序已变化；草稿仍保留，请核对最新目录后再调整。", 409);
    }
    const currentInput = buildChapterReorderInput(currentDirectory, orderedIds, context.scope.seriesId);
    if (currentInput === null) throw new ApiError("http", "最新目录无法与排序草稿一一对应，未发送修改。", 409);
    const orderIsUnchanged = currentDirectory.length === orderedIds.length
      && currentDirectory.every((chapter, index) => chapter.id === orderedIds[index]);
    if (orderIsUnchanged) {
      applyFreshChapterDirectory(currentDirectory, context.scope);
      return;
    }

    let writeError: ApiError | null = null;
    try {
      await context.scope.services.reorderChapters(context.scope.seriesId, currentInput, signal);
    } catch (cause) {
      if (!isCurrentOrderEditor(context, signal)) throw new ApiError("aborted", "Request was cancelled.");
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedHandlerRef.current();
      if (error.kind === "aborted") throw cause;
      if (error.kind === "http" && error.status !== null && error.status < 500) throw cause;
      writeError = error;
    }
    if (!isCurrentOrderEditor(context, signal)) throw new ApiError("aborted", "Request was cancelled.");

    let verifiedDirectory: Chapter[];
    try {
      verifiedDirectory = await context.scope.services.listChapters(context.scope.seriesId, signal);
    } catch (cause) {
      if (!isCurrentOrderEditor(context, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (normalizeApiError(cause).status === 401) unauthorizedHandlerRef.current();
      throw new ApiError("network", "排序请求已发送，但最新目录无法读取；结果尚未确认。草稿已保留，请重新读取后核对。", null);
    }
    if (!isCurrentOrderEditor(context, signal)) throw new ApiError("aborted", "Request was cancelled.");
    applyFreshChapterDirectory(verifiedDirectory, context.scope);
    const verifiedInput = buildChapterReorderInput(verifiedDirectory, orderedIds, context.scope.seriesId);
    const confirmed = verifiedInput !== null
      && verifiedDirectory.length === orderedIds.length
      && verifiedDirectory.every((chapter, index) => chapter.id === orderedIds[index])
      && verifiedInput.chapters.every((entry) => verifiedDirectory.find((chapter) => chapter.id === entry.id)?.order === entry.order);
    if (!confirmed) {
      refreshChapterOrderEditorSnapshot(context, verifiedDirectory);
      const reason = writeError === null ? "服务器返回后，目录顺序与草稿不一致。" : "请求结果不确定，最新目录没有显示草稿顺序。";
      throw new ApiError("http", reason + "未自动重发，请核对最新目录。", 409);
    }
    setChapterMutationNotice("章节顺序已重新读取并核对。");
  }

  function isFrameEditorCurrent(owner: FrameTextEditorContext, signal: AbortSignal): boolean {
    const directory = chapterDirectoryRef.current;
    const matches = directory.chapters?.filter((chapter) => chapter.id === owner.chapter.id) ?? [];
    return !signal.aborted
      && !owner.lifecycleController.signal.aborted
      && frameTextEditorRef.current === owner
      && sameChapterBrowserScope(owner.scope, activeScopeRef.current)
      && owner.requestGeneration === chapterRequestGeneration.current
      && selectedChapterIdRef.current === owner.chapter.id
      && sameChapterBrowserScope(directory.scope, owner.scope)
      && directory.requestGeneration === owner.requestGeneration
      && matches.length === 1
      && matches[0]?.series_id === owner.scope.seriesId;
  }

  function refreshFrameEditorBase(
    owner: FrameTextEditorContext,
    directory: Chapter[],
    latestChapter: Chapter,
  ): FrameTextEditorContext {
    applyFreshChapterDirectory(directory, owner.scope);
    const nextOwner = { ...owner, chapter: latestChapter };
    if (frameTextEditorRef.current === owner) {
      frameTextEditorRef.current = nextOwner;
      setFrameTextEditor(nextOwner);
    }
    return nextOwner;
  }

  function refreshAssetsAfterFrameRead(chapterId: string, assets: StoryboardAsset[]): void {
    assetCache.current.set(chapterId, assets);
    assetRequestGeneration.current += 1;
    setAssetResult({ chapterId, status: "ready", assets });
  }

  async function openFrameTextEditor(position: number): Promise<void> {
    const directory = chapterDirectoryRef.current;
    if (!canEditStoryboard || chapterManagement !== null || chapterManagementBlocked || chapterOrderEditorRef.current !== null
      || frameTextEditorRef.current !== null || frameEditOpening || chaptersLoading
      || chapters === null || chaptersError !== null || selectedChapter === null
      || !sameChapterBrowserScope(directory.scope, currentScope)
      || directory.requestGeneration !== chapterRequestGeneration.current || directory.chapters !== chapters
      || !isChapterSelectableFromDirectory(directory.chapters, selectedChapter.id, series.id)) return;
    const initialText = readEditableFrameText(selectedChapter, position);
    if (initialText === null) {
      setChapterMutationNotice("当前镜头的文字结构无法识别，未打开编辑器。");
      return;
    }
    frameEditWorkflowEpoch.current += 1;
    const controller = new AbortController();
    const base = {
      scope: currentScope,
      chapter: selectedChapter,
      position,
      initialText,
      requestGeneration: directory.requestGeneration,
      lifecycleController: controller,
      newlyAcquiredLock: false,
      lockNotice: null as string | null,
      lockLease: null as ChapterEditLockLeaseHandle | null,
      lockIdentity: null as FrameTextEditorContext["lockIdentity"],
    };
    frameEditOpeningController.current = controller;
    frameEditOpeningChapterIdRef.current = base.chapter.id;
    setFrameEditOpening(true);
    setChapterMutationNotice(null);
    let newlyAcquiredLock = false;
    let lockLease: ChapterEditLockLeaseHandle | null = null;
    let lockIdentity: FrameTextEditorContext["lockIdentity"] = null;
    const isCurrent = () => frameEditOpeningController.current === controller
      && frameEditOpeningChapterIdRef.current === base.chapter.id
      && !controller.signal.aborted
      && sameChapterBrowserScope(currentScope, activeScopeRef.current)
      && base.requestGeneration === chapterRequestGeneration.current
      && selectedChapterIdRef.current === base.chapter.id
      && chapterDirectoryRef.current.chapters?.filter((chapter) => chapter.id === base.chapter.id).length === 1;
    try {
      let lockNotice: string | null = null;
      if (typeof series.team_id === "string" && series.team_id !== "") {
        const { getChapterEditLock, acquireChapterEditLock } = services;
        if (getChapterEditLock === undefined || acquireChapterEditLock === undefined
          || services.releaseChapterEditLock === undefined) {
          throw new ApiError("invalid-response", "团队章节锁能力未就绪，已停止编辑。", null);
        }
        const existing = await getChapterEditLock(base.chapter.id, controller.signal);
        if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
        if (existing.locked) {
          if (!validOwnedChapterLock(existing, userId)) {
            throw new ApiError("http", existing.username
              ? `当前章节由 ${existing.username} 编辑，暂不能修改。`
              : "当前章节由其他创作者编辑，暂不能修改。", 409);
          }
          lockIdentity = chapterEditLockIdentity(existing);
          lockNotice = "当前账号已持有有效编辑锁。";
        } else {
          newlyAcquiredLock = true;
          const acquisition = await acquireChapterEditLock(base.chapter.id, controller.signal);
          const acquired = acquisition.state;
          lockLease = acquisition.lease;
          if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
          if (!acquired.locked || lockLease === null || typeof acquired.expires_at !== "string"
            || utcTimestampMilliseconds(acquired.expires_at) <= Date.now()) {
            throw new ApiError("http", acquired.message ?? "未能取得有效章节编辑锁。", 409);
          }
          const verified = await getChapterEditLock(base.chapter.id, controller.signal, lockLease);
          if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
          if (!validOwnedChapterLock(verified, userId)) {
            throw new ApiError("http", "取得锁后无法核对当前账号、所有权和有效期限，已停止编辑。", 409);
          }
          lockIdentity = chapterEditLockIdentity(verified);
          lockNotice = "已取得章节编辑锁，有效期至 " + verified.expires_at;
        }
      }
      if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
      const context: FrameTextEditorContext = { ...base, newlyAcquiredLock, lockNotice, lockLease, lockIdentity };
      frameTextEditorRef.current = context;
      setFrameTextEditor(context);
    } catch (cause) {
      if (isCurrent()) {
        const error = normalizeApiError(cause);
        if (error.status === 401) unauthorizedHandlerRef.current();
        else if (error.kind !== "aborted") setChapterMutationNotice(error.detail ?? error.message);
      }
      if (newlyAcquiredLock) {
        await releaseNewlyAcquiredChapterLock({
          ...base, newlyAcquiredLock, lockNotice: null, lockLease, lockIdentity,
        });
      }
    } finally {
      if (frameEditOpeningController.current === controller) {
        frameEditOpeningController.current = null;
        frameEditOpeningChapterIdRef.current = null;
        setFrameEditOpening(false);
      }
    }
  }

  async function saveFrameText(
    text: string,
    signal: AbortSignal,
    explicitOverwrite: boolean,
    expectedServerText: string | null | undefined,
  ): Promise<FrameTextSaveResult> {
    const currentOwner = frameTextEditorRef.current;
    if (currentOwner === null || !isFrameEditorCurrent(currentOwner, signal)) {
      throw new ApiError("aborted", "编辑内容已切换。", null);
    }
    let owner: FrameTextEditorContext = currentOwner;
    try {
      return await performFrameTextSave(owner, text, signal, explicitOverwrite, expectedServerText);
    } catch (cause) {
      const activeOwner = frameTextEditorRef.current;
      if (activeOwner === null
        || !sameChapterBrowserScope(activeOwner.scope, currentOwner.scope)
        || activeOwner.chapter.id !== currentOwner.chapter.id
        || !isFrameEditorCurrent(activeOwner, signal)) {
        throw new ApiError("aborted", "编辑内容已切换。", null);
      }
      if (normalizeApiError(cause).status === 401) unauthorizedHandlerRef.current();
      throw cause;
    }
  }

  async function performFrameTextSave(
    initialOwner: FrameTextEditorContext,
    text: string,
    signal: AbortSignal,
    explicitOverwrite: boolean,
    expectedServerText: string | null | undefined,
  ): Promise<FrameTextSaveResult> {
    let owner = initialOwner;
    let changed = updateStoryboardFrameText(owner.chapter.content, owner.position, text);
    if (!changed.ok) throw new ApiError("http", changed.message, 409);

    const directory = await owner.scope.services.listChapters(owner.scope.seriesId, signal);
    if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
    const targetMatches = directory.filter((chapter) => chapter.id === owner.chapter.id);
    const matchedChapter = targetMatches[0];
    if (targetMatches.length !== 1 || matchedChapter === undefined || matchedChapter.series_id !== owner.scope.seriesId) {
      throw new ApiError("http", "最新目录无法唯一确认当前章节，草稿已保留。", 409);
    }
    let latestChapter = matchedChapter;
    if (latestChapter.order !== owner.chapter.order || !sameChapterContent(latestChapter.content, owner.chapter.content)) {
      const currentText = readEditableFrameText(latestChapter, owner.position);
      if (!explicitOverwrite || currentText === null || currentText !== expectedServerText) {
        owner = refreshFrameEditorBase(owner, directory, latestChapter);
        return {
          status: "conflict",
          serverText: currentText,
          message: explicitOverwrite
            ? "服务器文字再次变化。草稿仍保留；请核对当前值后重新决定是否覆盖。"
            : "章节顺序或内容基线已变化。草稿仍保留；确认服务器当前值后，可选择以草稿覆盖此镜头文字。",
        };
      }
      owner = refreshFrameEditorBase(owner, directory, latestChapter);
      changed = updateStoryboardFrameText(latestChapter.content, owner.position, text);
      if (!changed.ok) throw new ApiError("http", changed.message, 409);
    }
    if (!changed.changed) return { status: "unchanged" };
    if (owner.scope.services.listStoryboardAssets === undefined) {
      throw new ApiError("invalid-response", "当前服务无法读取素材目录，已停止保存。", null);
    }
    const priorAssets = await owner.scope.services.listStoryboardAssets(owner.scope.seriesId, owner.chapter.id, signal);
    if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
    const references = inspectStoryboardReferences(owner.chapter.content, priorAssets, owner.scope.seriesId, owner.chapter.id);
    if (!references.ok) throw new ApiError("http", references.message, 409);

    if (typeof series.team_id === "string" && series.team_id !== "") {
      if (owner.scope.services.getChapterEditLock === undefined) {
        throw new ApiError("invalid-response", "团队章节锁能力不可用，已停止保存。", null);
      }
      const lock = await owner.scope.services.getChapterEditLock(owner.chapter.id, signal, owner.lockLease ?? undefined);
      if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (!validOwnedChapterLock(lock, owner.scope.userId)
        || !sameChapterEditLockIdentity(owner.lockIdentity, lock)) {
        throw new ApiError("http", "保存前无法确认当前账号仍持有有效章节锁；草稿已保留。", 409);
      }
    }

    let writeError: ApiError | null = null;
    try {
      if (owner.scope.services.updateChapter === undefined) throw new ApiError("invalid-response", "当前服务不支持分镜文字修改。", null);
      await owner.scope.services.updateChapter(
        owner.scope.seriesId,
        owner.chapter.id,
        { content: changed.content },
        signal,
      );
    } catch (cause) {
      const error = normalizeApiError(cause);
      if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (error.kind === "aborted") throw cause;
      if (error.kind === "http" && error.status !== null && error.status < 500) throw cause;
      writeError = error;
    }
    if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");

    let latestDirectory: Chapter[];
    let latestAssets: StoryboardAsset[];
    try {
      latestDirectory = await owner.scope.services.listChapters(owner.scope.seriesId, signal);
      if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      const latestMatches = latestDirectory.filter((chapter) => chapter.id === owner.chapter.id);
      const latestMatch = latestMatches[0];
      if (latestMatches.length !== 1 || latestMatch === undefined || latestMatch.series_id !== owner.scope.seriesId) {
        throw new ApiError("invalid-response", "最新目录无法唯一确认当前章节。", null);
      }
      latestChapter = latestMatch;
      latestAssets = await owner.scope.services.listStoryboardAssets(owner.scope.seriesId, owner.chapter.id, signal);
      if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
    } catch (cause) {
      const error = normalizeApiError(cause);
      if (!isFrameEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (error.status === 401) unauthorizedHandlerRef.current();
      if (error.kind === "aborted") throw cause;
      throw new ApiError("network", "文字修改请求已发送，但章节和素材目录无法重新读取；结果尚未确认，草稿仍保留。", null);
    }

    const confirmed = isSyncedFrameContentValid(
      owner.chapter.content,
      changed.content,
      latestChapter.content,
      references.missingPositions,
      latestAssets,
      owner.scope.seriesId,
      owner.chapter.id,
      owner.position,
      text,
    );
    refreshAssetsAfterFrameRead(owner.chapter.id, latestAssets);
    if (confirmed) {
      applyFreshChapterDirectory(latestDirectory, owner.scope);
      setChapterMutationNotice(`镜头 ${owner.position} 的文字已重新读取并核对${writeError === null ? "" : "；请求确认已丢失，但服务器内容已核对"}。`);
      return { status: "saved" };
    }
    owner = refreshFrameEditorBase(owner, latestDirectory, latestChapter);
    const currentText = readEditableFrameText(latestChapter, owner.position);
    return {
      status: "conflict",
      serverText: currentText,
      message: writeError === null
        ? "服务器返回的章节结构或文字与预期不一致。草稿仍保留，请核对服务器当前值后再决定是否覆盖文字。"
        : "请求结果未确认，最新章节结构没有通过核对。草稿仍保留；请查看服务器当前值。",
    };
  }

  const selectedIndex = chapters?.findIndex((chapter) => chapter.id === selectedChapterId) ?? -1;
  const chapterPanelsOpen = personalNotesOpen || personalRoughCutOpen || frameAssetReferencesOpen;
  const chapterManagementBlocked = chapterPanelsOpen
    || chapterOrderEditor !== null || frameTextEditor !== null || frameEditOpening
    || storyboardGenerationDialog !== null;
  const canEditStoryboard = series.can_enter && services.updateChapter !== undefined;
  const canReorderChapters = series.can_enter && services.reorderChapters !== undefined;
  const canManageChapters = series.can_enter
    && services.createChapter !== undefined
    && services.updateChapter !== undefined
    && services.deleteChapter !== undefined;

  function openStoryboardGeneration(): void {
    if (!canManageChapters || services.storyboardGeneration === undefined
      || chapterManagementBlocked || chapterManagement !== null
      || chaptersLoading || chaptersError !== null || chapters === null) return;
    setStoryboardGenerationDialog({ scope: currentScope });
  }

  function openChapterManagement(action: ChapterManagementAction, chapter: Chapter | null = null): void {
    if (!canManageChapters || chapterManagementBlocked || chaptersLoading || chapters === null || chaptersError !== null) {
      return;
    }
    if (chapter !== null && !isChapterSelectableFromDirectory(chapters, chapter.id, series.id)) {
      return;
    }
    setChapterMutationNotice(null);
    setChapterManagement({ action, scope: currentScope, chapter });
  }

  function managementRequestIsCurrent(
    request: ChapterManagementRequest,
    directory: ChapterDirectorySnapshot,
    signal: AbortSignal,
  ): boolean {
    const latest = chapterDirectoryRef.current;
    const matchingTargets = request.chapter === null
      ? []
      : latest.chapters?.filter((chapter) => chapter.id === request.chapter?.id) ?? [];
    return !signal.aborted
      && sameChapterBrowserScope(request.scope, activeScopeRef.current)
      && sameChapterBrowserScope(directory.scope, latest.scope)
      && directory.requestGeneration === latest.requestGeneration
      && directory.requestGeneration === chapterRequestGeneration.current
      && directory.chapters === latest.chapters
      && (request.chapter === null || (
        selectedChapterIdRef.current === request.chapter.id
        && matchingTargets.length === 1
        && matchingTargets[0]?.series_id === request.scope.seriesId
        && matchingTargets[0]?.title === request.chapter.title
      ));
  }

  async function confirmChapterManagement(
    request: ChapterManagementRequest,
    title: string,
    signal: AbortSignal,
  ): Promise<void> {
    const directory = chapterDirectoryRef.current;
    const currentContext = !signal.aborted
      && sameChapterBrowserScope(request.scope, activeScopeRef.current)
      && sameChapterBrowserScope(request.scope, directory.scope)
      && directory.requestGeneration === chapterRequestGeneration.current;
    if (!currentContext) {
      throw new ApiError("aborted", "Request was cancelled.");
    }

    const target = request.chapter;
    const matchingTargets = target === null
      ? []
      : directory.chapters?.filter((chapter) => chapter.id === target.id) ?? [];
    if (target !== null && matchingTargets.length !== 1) {
      throw new ApiError("http", "当前章节已不在最新列表中，未发送本次操作。", 404);
    }
    const latestTarget = matchingTargets[0];
    if (target !== null && (
      latestTarget?.series_id !== request.scope.seriesId
      || latestTarget.title !== target.title
      || selectedChapterIdRef.current !== target.id
    )) {
      throw new ApiError("http", "当前章节信息已变化，未发送本次操作，请重新核对。", 409);
    }
    const currentIndex = target === null
      ? directory.chapters?.length ?? 0
      : directory.chapters?.findIndex((chapter) => chapter.id === target.id) ?? -1;
    try {
      if (request.action === "create") {
        if (target !== null || services.createChapter === undefined) {
          throw new ApiError("invalid-response", "当前无法创建章节。");
        }
        const created = await services.createChapter(
          series.id,
          { title, content: [{ text: "" }], order: 0 },
          signal,
        );
        if (!managementRequestIsCurrent(request, directory, signal)) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        setChapterMutationNotice(null);
        requestChapterDirectoryReload({
          scope: request.scope,
          chapterId: created.id,
          fallbackIndex: currentIndex,
          message: `《${created.title}》已创建`,
        });
        return;
      }
      if (target === null || currentIndex < 0) {
        throw new ApiError("invalid-response", "当前章节身份无法核对。");
      }
      if (request.action === "rename") {
        if (services.updateChapter === undefined) {
          throw new ApiError("invalid-response", "当前无法修改章节名称。");
        }
        await services.updateChapter(series.id, target.id, { title }, signal);
        if (!managementRequestIsCurrent(request, directory, signal)) {
          throw new ApiError("aborted", "Request was cancelled.");
        }
        setChapterMutationNotice(null);
        requestChapterDirectoryReload({
          scope: request.scope,
          chapterId: target.id,
          fallbackIndex: currentIndex,
          message: `《${title}》名称已保存`,
        });
        return;
      }
      if (services.deleteChapter === undefined) {
        throw new ApiError("invalid-response", "当前无法删除章节。");
      }
      await services.deleteChapter(series.id, target.id, signal);
      if (!managementRequestIsCurrent(request, directory, signal)) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      assetCache.current.delete(target.id);
      setChapterMutationNotice(null);
      requestChapterDirectoryReload({
        scope: request.scope,
        chapterId: null,
        fallbackIndex: currentIndex,
        message: `《${target.title}》已删除`,
      });
    } catch (cause) {
      if (managementRequestIsCurrent(request, directory, signal)) {
        const error = normalizeApiError(cause);
        if (error.status === 401) {
          unauthorizedHandlerRef.current();
        }
      }
      throw cause;
    }
  }

  async function verifyChapterManagement(
    request: ChapterManagementRequest,
    signal: AbortSignal,
  ): Promise<{ message: string; canRetry: boolean; chapters: Chapter[] }> {
    const directory = chapterDirectoryRef.current;
    if (!managementRequestIsCurrent(request, directory, signal)) {
      throw new ApiError("aborted", "Request was cancelled.");
    }
    const targetId = request.chapter?.id ?? selectedChapterIdRef.current;
    const targetIndex = targetId === null
      ? 0
      : directory.chapters?.findIndex((chapter) => chapter.id === targetId) ?? 0;
    try {
      const result = await services.listChapters(series.id, signal);
      if (!managementRequestIsCurrent(request, directory, signal)) {
        throw new ApiError("aborted", "Request was cancelled.");
      }
      const nextSelected = targetId === null
        ? result[Math.min(Math.max(0, targetIndex), result.length - 1)]
        : result.find((chapter) => chapter.id === targetId)
          ?? result[Math.min(Math.max(0, targetIndex), result.length - 1)];
      chapterRequestGeneration.current += 1;
      chapterDirectoryRef.current = {
        scope: request.scope,
        requestGeneration: chapterRequestGeneration.current,
        chapters: result,
      };
      setChapters(result);
      setSelectedChapterId(nextSelected?.id ?? null);
      selectedChapterIdRef.current = nextSelected?.id ?? null;
      setChaptersError(null);
      setChaptersLoading(false);
      setAssetResult(null);
      const refreshedTarget = request.chapter === null
        ? undefined
        : result.find((chapter) => chapter.id === request.chapter?.id);
      if (request.action === "create") {
        return {
          message: "章节列表已重新读取；请自行核对新建结果，名称相同也不能证明是本次创建。",
          canRetry: true,
          chapters: result,
        };
      }
      if (refreshedTarget === undefined) {
        return {
          message: `《${request.chapter?.title ?? "当前章节"}》已不在最新列表中；无法确认原请求结果，也不能再次提交。`,
          canRetry: false,
          chapters: result,
        };
      }
      if (refreshedTarget.title !== request.chapter?.title) {
        return {
          message: `最新列表中的章节名称已变为《${refreshedTarget.title}》；请关闭窗口并重新核对当前章节。`,
          canRetry: false,
          chapters: result,
        };
      }
      setChapterManagement((current) => (
        current !== null
        && current.action === request.action
        && current.chapter?.id === request.chapter?.id
        && sameChapterBrowserScope(current.scope, request.scope)
          ? { ...current, chapter: refreshedTarget }
          : current
      ));
      return {
        message: `最新列表仍包含《${refreshedTarget.title}》。`,
        canRetry: true,
        chapters: result,
      };
    } catch (cause) {
      const error = normalizeApiError(cause);
      if (error.status === 401 && managementRequestIsCurrent(request, directory, signal)) {
        unauthorizedHandlerRef.current();
      }
      throw cause;
    }
  }

  const currentNavigationIssue = navigationIssue !== null
    && navigationIntent?.navigationEpoch === navigationIssue.epoch
    ? navigationIssue.issue
    : null;
  const currentPendingFrameNavigation = pendingFrameNavigation !== null
    && navigationIntent?.navigationEpoch === pendingFrameNavigation.epoch
    && pendingFrameNavigation.chapter === selectedChapter
    ? pendingFrameNavigation
    : null;
  const currentFrameNavigationFeedback = frameNavigationFeedback !== null
    && frameNavigationFeedback.chapter === selectedChapter
    && frameNavigationFeedback.assetSnapshot === currentAssetResult
    && selectedChapterId === frameNavigationFeedback.chapter.id
    ? frameNavigationFeedback
    : null;
  const currentRoughCutFrameFeedback = roughCutFrameFeedback !== null
    && roughCutFrameFeedback.owner === personalRoughCutContext
    && personalRoughCutOpen
    && roughCutFrameFeedback.owner.chapter === selectedChapter
    && roughCutFrameFeedback.assetSnapshot === currentAssetResult
    && roughCutFrameFeedback.assetRequestGeneration === assetRequestGeneration.current
    && roughCutFrameFeedback.target.isCurrent()
    ? roughCutFrameFeedback
    : null;
  const lockMessage = selectedChapter === null ? null : lockSummary(selectedChapter.lock);
  const chapterErrorPresentation = chaptersError === null ? null : errorPresentation(chaptersError);
  const assetErrorPresentation = currentAssetResult?.status === "error"
    ? errorPresentation(currentAssetResult.error)
    : null;

  return (
    <section className="chapter-browser" aria-labelledby="chapter-browser-heading">
      <div className="chapter-browser-heading">
        <div>
          <p className="eyebrow">{canEditStoryboard ? "章节与分镜" : "只读浏览"}</p>
          <h1 id="chapter-browser-heading">{series.name}</h1>
          <p>查看已有章节、分镜与个人制作记录。</p>
        </div>
        <button className="secondary-button chapter-back-button" onClick={backToSeries} type="button">
          <span aria-hidden="true">←</span> 返回剧集列表
        </button>
      </div>

      <div className="chapter-browser-layout">
        <aside className="chapter-list-panel" aria-label="章节列表">
          <div className="chapter-list-heading">
            <div>
              <p className="eyebrow">剧集章节</p>
              <h2>章节列表</h2>
            </div>
            <div className="chapter-list-heading-actions">
              {!chaptersLoading && chaptersError === null && chapters !== null && (
                <span className="chapter-count">{chapters.length} 章</span>
              )}
              {canReorderChapters && chapters !== null && chapters.length > 1 && (
                <button
                  className="text-button chapter-reorder-button"
                  disabled={chaptersLoading || chaptersError !== null || chapterManagementBlocked || chapterManagement !== null}
                  onClick={openChapterOrderEditor}
                  title={chapterManagementBlocked ? "请先关闭当前制作记录或编辑窗口" : undefined}
                  type="button"
                >调整顺序</button>
              )}
              {canManageChapters && (
                <button
                  className="primary-button chapter-create-button"
                  disabled={chaptersLoading || chapters === null || chaptersError !== null || chapterManagementBlocked}
                  onClick={() => openChapterManagement("create")}
                  title={chapterManagementBlocked ? "请先关闭当前制作记录或原图引用面板" : undefined}
                  type="button"
                >新建章节</button>
              )}
              {canManageChapters && services.storyboardGeneration !== undefined && (
                <button
                  className="secondary-button chapter-ai-generation-button"
                  disabled={chaptersLoading || chaptersError !== null || chapterManagementBlocked || chapterManagement !== null}
                  onClick={openStoryboardGeneration}
                  title={chapterManagementBlocked ? "请先关闭当前制作记录、镜头编辑或章节管理窗口" : undefined}
                  type="button"
                >AI 生成分镜</button>
              )}
            </div>
          </div>

          {chapterPanelsOpen && (
            <p className="chapter-management-blocked" role="status">
              请先关闭当前制作记录或原图引用面板，再管理章节。
            </p>
          )}
          {chapterMutationNotice !== null && (
            <p className="chapter-management-notice" role="status">{chapterMutationNotice}</p>
          )}

          {chaptersLoading && (
            <div className="chapter-list-state" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              <span>正在读取章节…</span>
            </div>
          )}

          {!chaptersLoading && chapterErrorPresentation !== null && (
            <div className="chapter-list-error" role="alert">
              <strong>{chapterErrorPresentation.title}</strong>
              <p>{chapterErrorPresentation.message}</p>
              <button className="secondary-button" onClick={retryChapters} type="button">重试读取</button>
            </div>
          )}

          {!chaptersLoading && chaptersError === null && chapters?.length === 0 && currentNavigationIssue === null && (
            <div className="chapter-list-empty" role="status">
              <span className="empty-icon" aria-hidden="true">◌</span>
              <strong>暂无章节</strong>
              <span>该剧集当前没有可读章节。</span>
            </div>
          )}

          {!chaptersLoading && chaptersError === null && chapters !== null && chapters.length > 0 && (
            <div className="chapter-list">
              {chapters.map((chapter, index) => {
                const active = chapter.id === selectedChapterId;
                const directory = chapterDirectoryRef.current;
                const requestGeneration = directory.requestGeneration;
                const canSelectChapter = requestGeneration === chapterRequestGeneration.current
                  && directory.chapters === chapters
                  && sameChapterBrowserScope(directory.scope, activeScopeRef.current)
                  && isChapterSelectableFromDirectory(directory.chapters, chapter.id, series.id);
                return (
                  <button
                    aria-current={active ? "true" : undefined}
                    className={active ? "chapter-list-item active" : "chapter-list-item"}
                    disabled={!canSelectChapter}
                    key={chapter.id + ":" + index}
                    onClick={() => selectChapter(chapter.id, requestGeneration)}
                    type="button"
                  >
                    <span className="chapter-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className="chapter-list-copy">
                      <strong>{displayChapterTitle(chapter)}</strong>
                      <small>{chapter.content?.length ?? 0} 个分镜 · 更新于 {formatDate(chapter.updated_at)}</small>
                      {!canSelectChapter && <small className="chapter-identity-warning">章节身份无法核对，不能打开。</small>}
                    </span>
                    <span className="chapter-chevron" aria-hidden="true">›</span>
                  </button>
                );
              })}
            </div>
          )}
        </aside>

        <div className="chapter-content-panel">
          {chaptersLoading && (
            <div className="chapter-content-state" role="status" aria-live="polite">
              <span className="spinner" aria-hidden="true" />
              <span>正在读取章节内容…</span>
            </div>
          )}

          {!chaptersLoading && chaptersError !== null && (
            <ChapterLoadError error={chaptersError} onBack={backToSeries} onRetry={retryChapters} />
          )}

          {!chaptersLoading && chaptersError === null && currentNavigationIssue !== null && selectedChapter === null && (
            <div className="chapter-navigation-issue" role="alert">
              <h2>{currentNavigationIssue === "frame" ? "无法确认目标镜头" : "无法确认目标章节"}</h2>
              <p>{navigationIssueMessage(currentNavigationIssue)}</p>
              <button className="secondary-button" onClick={retryChapters} type="button">
                {currentNavigationIssue === "frame" ? "重新核对目标镜头" : "重新核对目标章节"}
              </button>
            </div>
          )}

          {!chaptersLoading && chaptersError === null && chapters?.length === 0 && currentNavigationIssue === null && (
            <div className="chapter-empty-detail" role="status">
              <div className="empty-icon" aria-hidden="true">◌</div>
              <h2>这个剧集还没有章节</h2>
              <p>章节内容准备好后会显示在这里。</p>
            </div>
          )}

          {!chaptersLoading && chaptersError === null && selectedChapter !== null && (
            <>
              <header className="selected-chapter-header">
                <div>
                  <p className="eyebrow">
                    {selectedIndex >= 0 ? "章节列表第" + (selectedIndex + 1) + "项" : "章节"}
                    <span aria-hidden="true"> · </span>
                    {series.name}
                  </p>
                  <h2>{displayChapterTitle(selectedChapter)}</h2>
                  <p>{frames.length} 个分镜 · 更新于 {formatDate(selectedChapter.updated_at)}</p>
                </div>
                <div className="selected-chapter-actions">
                  <span className="readonly-pill">{canEditStoryboard ? "可编辑分镜文字" : "只读"}</span>
                  {canManageChapters && (
                    <>
                      <button
                        className="text-button"
                        disabled={chaptersLoading || chapterManagementBlocked}
                        onClick={() => openChapterManagement("rename", selectedChapter)}
                        title={chapterManagementBlocked ? "请先关闭当前制作记录或原图引用面板" : undefined}
                        type="button"
                      >修改名称</button>
                      <button
                        className="text-button chapter-delete-action"
                        disabled={chaptersLoading || chapterManagementBlocked}
                        onClick={() => openChapterManagement("delete", selectedChapter)}
                        title={chapterManagementBlocked ? "请先关闭当前制作记录或原图引用面板" : undefined}
                        type="button"
                      >删除章节</button>
                    </>
                  )}
                  <button
                    className="text-button chapter-refresh-button"
                    onClick={retryChapters}
                    type="button"
                  >
                    重新读取章节
                  </button>
                  {!personalNotesOpen && (
                    <button
                      className="text-button personal-production-open-button"
                      disabled={currentAssetResult === null || currentAssetResult.status === "loading"}
                      onClick={openPersonalNotes}
                      type="button"
                    >
                      查看我的制作记录
                    </button>
                  )}
                  {!personalRoughCutOpen && (
                    <button
                      className="text-button personal-rough-cut-open-button"
                      onClick={openPersonalRoughCut}
                      type="button"
                    >
                      查看我的粗剪草稿
                    </button>
                  )}
                </div>
              </header>

              <div className="chapter-readonly-note">
                <span aria-hidden="true">◉</span>
                <span>{canEditStoryboard
                  ? "可以编辑各镜头文字；原文、分镜素材关系和其他镜头字段会保留。"
                  : "此页面提供只读展示。"}</span>
              </div>

              {lockMessage !== null && (
                <div className="chapter-lock-note" role="note">
                  <span className="lock-glyph" aria-hidden="true">▣</span>
                  <span>{lockMessage} · {canEditStoryboard
                    ? "编辑前会重新核对团队锁状态。"
                    : "这是读取时的状态快照，当前页面始终只读。"}</span>
                </div>
              )}

              {currentPendingFrameNavigation?.status === "awaiting-assets" && (
                <p className="frame-navigation-pending" role="status" aria-live="polite">
                  正在核对目标镜头…
                </p>
              )}
              {currentPendingFrameNavigation?.status === "unresolved" && (
                <div className="frame-navigation-issue" role="alert">
                  <p>{currentPendingFrameNavigation.message ?? "目标镜头尚未定位。"}</p>
                  <button className="secondary-button" onClick={retryChapters} type="button">
                    重新核对目标镜头
                  </button>
                </div>
              )}

              {personalNotesOpen && personalNotesContext !== null && selectedChapter !== null && currentAssetResult !== null && (
                <PersonalProductionNotesPanel
                  key={personalNotesContext.openEpoch}
                  assets={currentAssetResult.status === "ready" ? currentAssetResult.assets : EMPTY_STORYBOARD_ASSETS}
                  assetSnapshotToken={personalNotesContext.assetSnapshot}
                  chapter={selectedChapter}
                  contextToken={personalNotesContext}
                  generation={personalNotesContext.openEpoch}
                  mediaSnapshotAvailable={frames.length === 0 || currentAssetResult.status === "ready"}
                  onClose={onClosePersonalNotes}
                  onFrameReadInvalidated={invalidatePersonalNotesFrameRead}
                  onLocateProductionNoteFrame={onLocateProductionNoteFrame}
                  onLocateResume={onLocateResume}
                  onRetryChapter={retryChapters}
                  onRegisterFrameRead={registerPersonalNotesFrameRead}
                  onResumeInvalidated={onResumeInvalidated}
                  onUnauthorized={onUnauthorized}
                  services={services}
                  seriesId={series.id}
                  userId={userId}
                />
              )}

              {personalRoughCutOpen && personalRoughCutContext !== null && selectedChapter !== null && (
                <PersonalRoughCutPanel
                  key={personalRoughCutContext.openEpoch}
                  assetDirectoryStatus={currentAssetResult === null ? "loading" : currentAssetResult.status}
                  assetRequestGeneration={assetRequestGeneration.current}
                  assetSnapshotToken={currentAssetResult}
                  assets={currentAssetResult?.status === "ready" ? currentAssetResult.assets : null}
                  chapter={personalRoughCutContext.chapter}
                  onClose={() => closePersonalRoughCut(personalRoughCutContext)}
                  onFrameNavigationInvalidated={onRoughCutFrameNavigationInvalidated}
                  onLocateFrame={onLocateRoughCutFrame}
                  onUnauthorized={onUnauthorized}
                  seriesId={personalRoughCutContext.seriesId}
                  services={personalRoughCutContext.services}
                  userId={personalRoughCutContext.userId}
                />
              )}

              {frames.length === 0 ? (
                <div className="chapter-empty-detail storyboard-empty" role="status">
                  <div className="empty-icon" aria-hidden="true">◌</div>
                  <h2>本章暂无分镜</h2>
                  <p>章节文字内容为空。</p>
                </div>
              ) : (
                <>
                  {currentAssetResult?.status === "loading" && (
                    <div className="asset-load-note" role="status" aria-live="polite">
                      <span className="spinner" aria-hidden="true" />
                      <span>正在读取本章原图…</span>
                    </div>
                  )}
                  {assetErrorPresentation !== null && (
                    <div className={"state-panel error-panel " + assetErrorPresentation.kind} role="alert">
                      <div className="state-icon" aria-hidden="true">!</div>
                      <div className="state-copy">
                        <h2>{assetErrorPresentation.title}</h2>
                        <p>{assetErrorPresentation.message} 镜头文字仍可阅读。</p>
                      </div>
                      <button className="secondary-button" onClick={retryAssets} type="button">
                        {currentPendingFrameNavigation === null ? "重试读取原图" : "重新核对目标镜头"}
                      </button>
                    </div>
                  )}
                  <div className="storyboard-list" ref={storyboardListElement} aria-label="分镜内容">
                    {currentResumeLocation !== null && (
                      <p className="resume-location-feedback" role="status" aria-live="polite">
                        已定位到续作镜头 {currentResumeLocation.target.position}。
                      </p>
                    )}
                    {currentPersonalNoteFrameFeedback !== null && (
                      <p className="personal-note-frame-navigation-feedback" role="status" aria-live="polite">
                        已定位到记录镜头 {currentPersonalNoteFrameFeedback.target.position}。
                      </p>
                    )}
                    {currentFrameNavigationFeedback !== null && (
                      <p className="frame-navigation-feedback" role="status" aria-live="polite">
                        已定位到目标镜头 {currentFrameNavigationFeedback.position}。
                      </p>
                    )}
                    {currentRoughCutFrameFeedback !== null && (
                      <p className="rough-cut-frame-navigation-feedback" role="status" aria-live="polite">
                        已定位到粗剪镜头 {currentRoughCutFrameFeedback.target.position}。
                      </p>
                    )}
                    {frameViews.map((frame) => {
                      const sourceFrame = frames[frame.position - 1];
                      const imageAvailable = frame.imageUrl !== null && !failedImagePositions.has(frame.position);
                      const text = textContents(frame.text, "尚未填写镜头文字。", "这条镜头文字暂时无法识别。");
                      const isResumeTarget = currentResumeLocation?.target.position === frame.position;
                      const isPersonalNoteFrameTarget = currentPersonalNoteFrameFeedback?.target.position === frame.position;
                      const isFrameNavigationTarget = currentFrameNavigationFeedback?.position === frame.position;
                      const isRoughCutFrameTarget = currentRoughCutFrameFeedback?.target.position === frame.position;
                      const isReferenceOwner = frameAssetReferencesOpen
                        && frameAssetReferenceOwner?.frame === sourceFrame
                        && frameAssetReferenceOwner.position === frame.position;
                      return (
                        <article
                          aria-label={displayChapterTitle(selectedChapter) + " · 镜头 " + frame.position}
                          className={[
                            "storyboard-frame",
                            isResumeTarget ? "is-resume-target" : "",
                            isPersonalNoteFrameTarget ? "is-personal-note-frame-target" : "",
                            isFrameNavigationTarget ? "is-frame-navigation-target" : "",
                            isRoughCutFrameTarget ? "is-rough-cut-frame-target" : "",
                          ].filter(Boolean).join(" ")}
                          key={frame.position}
                          ref={(element) => {
                            if (element === null) {
                              storyboardFrameElements.current.delete(frame.position);
                            } else {
                              storyboardFrameElements.current.set(frame.position, element);
                            }
                          }}
                          tabIndex={-1}
                          onBlur={() => {
                            setFrameNavigationFeedback((current) => (
                              current?.chapter === selectedChapter && current.position === frame.position
                                ? null
                                : current
                            ));
                            setRoughCutFrameFeedback((current) => (
                              current?.owner.chapter === selectedChapter
                              && current.target.position === frame.position
                                ? null
                                : current
                            ));
                            setPersonalNoteFrameFeedback((current) => {
                              const registration = personalNotesReadRegistrationRef.current;
                              return current?.owner === personalNotesContext
                                && registration?.owner === personalNotesContext
                                && samePersonalNotesReadIdentity(current.identity, registration.identity)
                                && current.target.position === frame.position
                                  ? null
                                  : current;
                            });
                          }}
                        >
                          <div className="storyboard-frame-heading">
                            <span className="storyboard-position">镜头 {frame.position}</span>
                            <div className="storyboard-frame-heading-actions">
                              <span className="frame-preview-state">
                                {frame.hasPreview ? "已有预览" : "暂无预览"}
                              </span>
                              {canEditStoryboard && sourceFrame !== undefined && frame.text.kind !== "invalid" && (
                                <button
                                  className="text-button frame-text-edit-button"
                                  disabled={chapterManagementBlocked || chapterManagement !== null || frameEditOpening}
                                  onClick={() => void openFrameTextEditor(frame.position)}
                                  title={chapterPanelsOpen ? "请先关闭当前制作记录或原图引用面板" : undefined}
                                  type="button"
                                >{frameEditOpening ? "正在核对…" : "编辑文字"}</button>
                              )}
                            </div>
                          </div>
                          <div className="storyboard-frame-body">
                            <div className="storyboard-image-wrap">
                              {imageAvailable ? (
                                <img
                                  alt={"镜头 " + frame.position + " 原图"}
                                  loading="lazy"
                                  onError={() => setFailedImagePositions((current) => {
                                    const next = new Set(current);
                                    next.add(frame.position);
                                    return next;
                                  })}
                                  referrerPolicy="no-referrer"
                                  src={frame.imageUrl ?? undefined}
                                />
                              ) : (
                                <div className="storyboard-image-placeholder" role="img" aria-label={"镜头 " + frame.position + " 暂无可展示原图"}>
                                  <span className="image-placeholder-glyph" aria-hidden="true">▧</span>
                                  <span>
                                    {failedImagePositions.has(frame.position)
                                      ? "原图暂时无法加载"
                                      : frame.imageStatus === "missing"
                                        ? "暂无可安全展示的原图"
                                        : "原图关系暂不可核对"}
                                  </span>
                                </div>
                              )}
                            </div>
                            <div className="storyboard-frame-copy">
                              <h3>镜头内容</h3>
                              <p className={frame.text.kind === "value" ? "frame-text" : "frame-text frame-text-placeholder"}>
                                {text}
                              </p>
                              {frame.originalText.kind !== "missing" && (
                                <div className="original-text-block">
                                  <span>原文</span>
                                  <p className={frame.originalText.kind === "value" ? "" : "frame-text-placeholder"}>
                                    {textContents(frame.originalText, "", "原文字段暂时无法识别。")}
                                  </p>
                                </div>
                              )}
                              <div className="frame-reference-summary">
                                <span>已关联素材</span>
                                <strong>{frame.referenceCount} 项</strong>
                              </div>
                              {sourceFrame !== undefined && !isReferenceOwner && (
                                <button
                                  className="text-button frame-asset-references-open-button"
                                  onClick={() => openFrameAssetReferences(sourceFrame, frame.position)}
                                  type="button"
                                >
                                  查看镜头 {frame.position} 的关联素材
                                </button>
                              )}
                              {sourceFrame !== undefined && isReferenceOwner && frameAssetReferenceOwner !== null && (
                                <FrameAssetReferencesPanel
                                  key={frameAssetReferenceOwner.openEpoch}
                                  currentScope={{
                                    userId,
                                    seriesId: series.id,
                                    services,
                                    chapter: selectedChapter,
                                    frame: sourceFrame,
                                    position: frame.position,
                                  }}
                                  onClose={closeFrameAssetReferences}
                                  {...(onNavigateToAsset !== undefined && series.can_enter === true
                                    ? {
                                      onNavigateToAsset: (target: FrameAssetReferenceAssetTarget) => {
                                        const owner = frameAssetReferenceOwner;
                                        if (
                                          owner === null
                                          || !frameAssetReferencesOpen
                                          || frameAssetReferenceOwnerRef.current !== owner
                                          || owner.openEpoch !== frameAssetReferencesOpenEpoch.current
                                          || owner.userId !== userId
                                          || owner.seriesId !== series.id
                                          || owner.services !== services
                                          || owner.chapter !== selectedChapter
                                          || frames[owner.position - 1] !== owner.frame
                                          || target.seriesId !== series.id
                                          || !target.isCurrent()
                                        ) {
                                          return;
                                        }
                                        onNavigateToAsset(target);
                                      },
                                    }
                                    : {})}
                                  onUnauthorized={onUnauthorized}
                                  owner={frameAssetReferenceOwner}
                                />
                              )}
                              {frame.imageStatus === "unavailable" && !failedImagePositions.has(frame.position) && (
                                <p className="frame-media-note">原图与镜头的对应关系暂不可核对。</p>
                              )}
                              {frame.imageStatus === "missing" && !failedImagePositions.has(frame.position) && (
                                <p className="frame-media-note">当前没有可安全展示的原图。</p>
                              )}
                            </div>
                          </div>
                        </article>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
      {chapterOrderEditor !== null && sameChapterBrowserScope(chapterOrderEditor.scope, currentScope) && (
        <ChapterOrderDialog
          key={`${chapterOrderEditor.scope.userId}:${chapterOrderEditor.scope.seriesId}:${chapterOrderEditor.requestGeneration}`}
          chapters={chapterOrderEditor.snapshot}
          onClose={() => {
            chapterOrderEditorRef.current = null;
            setChapterOrderEditor(null);
          }}
          onSave={saveChapterOrder}
          seriesId={chapterOrderEditor.scope.seriesId}
        />
      )}
      {frameTextEditor !== null && sameChapterBrowserScope(frameTextEditor.scope, currentScope) && (
        <FrameTextEditor
          key={`${frameTextEditor.scope.userId}:${frameTextEditor.scope.seriesId}:${frameTextEditor.chapter.id}:${frameTextEditor.position}:${frameTextEditor.requestGeneration}`}
          chapter={frameTextEditor.chapter}
          initialText={frameTextEditor.initialText}
          lifecycleSignal={frameTextEditor.lifecycleController.signal}
          lockNotice={frameTextEditor.lockNotice}
          onClose={() => closeFrameTextEditor(frameTextEditor)}
          onSave={saveFrameText}
          position={frameTextEditor.position}
        />
      )}
      {chapterManagement !== null && sameChapterBrowserScope(chapterManagement.scope, currentScope) && (
        <ChapterManagementDialog
          action={chapterManagement.action}
          key={`${chapterManagement.scope.userId}:${chapterManagement.scope.seriesId}:${chapterManagement.action}:${chapterManagement.chapter?.id ?? "new"}`}
          onClose={() => setChapterManagement(null)}
          onConfirm={(title, signal) => confirmChapterManagement(chapterManagement, title, signal)}
          onVerify={(signal) => verifyChapterManagement(chapterManagement, signal)}
          {...(chapterManagement.chapter === null ? {} : { chapter: chapterManagement.chapter })}
        />
      )}
      {storyboardGenerationDialog !== null
        && sameChapterBrowserScope(storyboardGenerationDialog.scope, currentScope) && (
        <StoryboardGenerationDialog
          key={`${storyboardGenerationDialog.scope.userId}:${storyboardGenerationDialog.scope.seriesId}`}
          onClose={() => setStoryboardGenerationDialog((current) => (
            current !== null && sameChapterBrowserScope(current.scope, storyboardGenerationDialog.scope)
              ? null
              : current
          ))}
          onCreated={(chapter) => {
            if (!sameChapterBrowserScope(storyboardGenerationDialog.scope, activeScopeRef.current)
              || chapter.series_id !== storyboardGenerationDialog.scope.seriesId) return;
            setStoryboardGenerationDialog(null);
            assetCache.current.delete(chapter.id);
            requestChapterDirectoryReload({
              scope: storyboardGenerationDialog.scope,
              chapterId: chapter.id,
              fallbackIndex: Math.max(selectedIndex, 0),
              message: `《${chapter.title}》已创建`,
            });
          }}
          onUnauthorized={onUnauthorized}
          seriesId={storyboardGenerationDialog.scope.seriesId}
          seriesName={series.name}
          services={storyboardGenerationDialog.scope.services}
          userId={storyboardGenerationDialog.scope.userId}
        />
      )}
    </section>
  );
}
