import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type {
  Chapter,
  ChapterEditLockLeaseHandle,
  ChapterEditLockState,
  Character,
  Prop,
  Scene,
  Series,
  StoryboardAsset,
  StoryboardFrame,
} from "../../shared/api/contracts";
import type { PersonalProductionSnapshot } from "../../shared/api/personalProductionNotes";
import type { PersonalRoughCutSnapshot } from "../../shared/api/personalRoughCut";
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
import { FrameReviewDialog, type FrameReviewSourceCheck } from "./FrameReviewDialog";
import type { FrameReviewResult } from "../../shared/api/frameReview";
import {
  FrameStructureEditor,
  type FrameStructureMode,
  type FrameStructureSubmitResult,
  type FrameStructureVerifyResult,
} from "./frame-structure/FrameStructureEditor";
import {
  areFrameStructureMappingsValid,
  buildInsertedFrameContent,
  firstStoryboardAssetId,
  isFrameStructureReadbackValid,
  resolveDeletableFrameTarget,
  type FrameStructureDraft,
} from "./frame-structure/contract";
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

type ChapterEditLockIdentity = { acquiredAt: string; userId: string } | null;

interface ChapterEditLockOwner {
  scope: ChapterBrowserScope;
  chapter: Chapter;
  requestGeneration: number;
  lifecycleController: AbortController;
  newlyAcquiredLock: boolean;
  refreshLockOnClose: boolean;
  lockNotice: string | null;
  lockLease: ChapterEditLockLeaseHandle | null;
  lockIdentity: ChapterEditLockIdentity;
}

interface FrameTextEditorContext extends ChapterEditLockOwner {
  position: number;
  initialText: string;
}

interface FrameReviewDialogOwner {
  scope: ChapterBrowserScope;
  chapter: Chapter;
  position: number;
  frameIndex: number;
  storyboardAssetId: string;
  sourceText: string;
  sourceVersion: string;
  requestGeneration: number;
}

interface FrameStructureEditorContext extends ChapterEditLockOwner {
  mode: FrameStructureMode;
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
  baseDirectory: Chapter[];
  baseContent: StoryboardFrame[] | null;
  baseAssets: StoryboardAsset[];
  deleteTarget: { frameIndex: number; storyboardAssetId: string } | null;
  writeAttemptStarted: boolean;
  pendingExpectedContent: StoryboardFrame[] | null;
  pendingRemovedAssetId: string | null;
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

function sameStoryboardAssetPositions(left: StoryboardAsset[], right: StoryboardAsset[]): boolean {
  if (left.length !== right.length) return false;
  const assetsById = new Map<string, StoryboardAsset[]>();
  for (const asset of right) {
    const matches = assetsById.get(asset.id) ?? [];
    matches.push(asset);
    assetsById.set(asset.id, matches);
  }
  return left.every((asset) => {
    const matches = assetsById.get(asset.id);
    const current = matches?.length === 1 ? matches[0] : undefined;
    return current !== undefined && current.frame_index === asset.frame_index
      && current.series_id === asset.series_id && current.chapter_id === asset.chapter_id;
  });
}

function snapshotStoryboardAssetPositions(assets: StoryboardAsset[]): StoryboardAsset[] {
  return assets.map((asset) => ({ ...asset }));
}

function areReferencedFrameAssetPositionsValid(
  chapter: Chapter,
  assets: StoryboardAsset[],
  seriesId: string,
): boolean {
  if (!Array.isArray(chapter.content)) return false;
  const seenIds = new Set<string>();
  return chapter.content.every((frame, frameIndex) => {
    const references = frame.storyboard;
    if (references === undefined || references === null
      || (Array.isArray(references) && references.length === 0)) return true;
    const assetId = firstStoryboardAssetId(frame);
    if (assetId === null || seenIds.has(assetId)) return false;
    seenIds.add(assetId);
    const matches = assets.filter((asset) => asset.id === assetId);
    return matches.length === 1
      && matches[0]?.series_id === seriesId
      && matches[0]?.chapter_id === chapter.id
      && matches[0]?.frame_index === frameIndex;
  });
}

function hasUniqueOwnedAssetIds(
  assets: Array<{ id: string; series_id: string }>,
  ids: string[],
  seriesId: string,
): boolean {
  return new Set(ids).size === ids.length && ids.every((id) => {
    const matches = assets.filter((asset) => asset.id === id);
    return matches.length === 1 && matches[0]?.series_id === seriesId;
  });
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

function chapterEditLockIdentity(lock: ChapterEditLockState): ChapterEditLockIdentity {
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
  left: ChapterEditLockIdentity,
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

function storyboardFrameId(frame: StoryboardFrame | undefined): string | null {
  if (frame === undefined || !Array.isArray(frame.storyboard)) return null;
  const first = frame.storyboard[0];
  return typeof first === "string" && first.trim() !== "" ? first : null;
}

function frameSourceVersion(chapter: Chapter, frame: StoryboardFrame): string {
  return `${chapter.updated_at}:${JSON.stringify(frame)}`;
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
  const [frameReviewDialog, setFrameReviewDialog] = useState<FrameReviewDialogOwner | null>(null);
  const frameReviewDialogRef = useRef<FrameReviewDialogOwner | null>(null);
  frameReviewDialogRef.current = frameReviewDialog;
  const frameReviewVerifiedSourceRef = useRef<FrameReviewDialogOwner | null>(null);
  const [frameStructureEditor, setFrameStructureEditor] = useState<FrameStructureEditorContext | null>(null);
  const frameStructureEditorRef = useRef<FrameStructureEditorContext | null>(null);
  frameStructureEditorRef.current = frameStructureEditor;
  const frameStructureDraftDirtyRef = useRef(false);
  const frameStructureDiscardRequestRef = useRef<(() => Promise<boolean>) | null>(null);
  const guardedNavigationSequence = useRef(0);
  const [frameEditOpening, setFrameEditOpening] = useState(false);
  const frameEditOpeningController = useRef<AbortController | null>(null);
  const frameEditOpeningChapterIdRef = useRef<string | null>(null);
  const invalidateFrameEditingWorkflowRef = useRef<(options?: FrameEditingInvalidationOptions) => void>(() => undefined);
  const pendingFrameLockReleaseRef = useRef<ChapterEditLockOwner | null>(null);
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
    frameReviewDialogRef.current = null;
    frameReviewVerifiedSourceRef.current = null;
    setFrameReviewDialog(null);
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
  useEffect(() => {
    const owner = frameReviewDialogRef.current;
    if (owner !== null && !isFrameReviewOwnerCurrent(owner)) {
      closeFrameReviewDialog(owner);
    }
  }, [selectedChapter, selectedChapterId, series.id, services, userId]);
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
    guardStructureDraftNavigation(() => retryChaptersNow());
  }

  function retryChaptersNow() {
    const pendingSelection = chapterMutationSelectionIntent.current;
    const selection = pendingSelection !== null
      && sameChapterBrowserScope(pendingSelection.scope, activeScopeRef.current)
      ? pendingSelection
      : null;
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
    closeFrameReviewDialog();
    assetCache.current.clear();
    setChapters(null);
    setSelectedChapterId(null);
    setAssetResult(null);
    requestChapterDirectoryReload(selection);
  }

  function requestChapterDirectoryReload(selection: ChapterMutationSelectionIntent | null): void {
    chapterMutationSelectionIntent.current = selection;
    chapterReloadSequence.current += 1;
    setChapterReloadKey(chapterReloadSequence.current);
  }

  function abandonNavigationIntent(epoch: number): void {
    if (finalizedNavigationEpochsRef.current.has(epoch)) return;
    finalizedNavigationEpochsRef.current.add(epoch);
    navigationAbandonedHandlerRef.current?.(epoch);
  }

  function guardStructureDraftNavigation(action: () => void, onCancel: () => void = () => undefined): void {
    const owner = frameStructureEditorRef.current;
    if (owner === null || !frameStructureDraftDirtyRef.current) {
      action();
      return;
    }
    const requestDiscard = frameStructureDiscardRequestRef.current;
    if (requestDiscard === null) return;

    const requestId = ++guardedNavigationSequence.current;
    const ownerScope = owner.scope;
    const ownerChapterId = owner.chapter.id;
    const ownerGeneration = owner.requestGeneration;
    void requestDiscard().then((discard) => {
      if (requestId !== guardedNavigationSequence.current
        || !sameChapterBrowserScope(ownerScope, activeScopeRef.current)
        || ownerGeneration !== chapterRequestGeneration.current
        || selectedChapterIdRef.current !== ownerChapterId) return;
      if (!discard) {
        onCancel();
        return;
      }
      action();
    }).catch(() => undefined);
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

    guardStructureDraftNavigation(
      () => retryChaptersNow(),
      () => abandonNavigationIntent(intent.navigationEpoch),
    );
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

    guardStructureDraftNavigation(() => selectChapterNow(chapterId, expectedRequestGeneration));
  }

  function selectChapterNow(chapterId: string, expectedRequestGeneration: number): void {
    const directory = chapterDirectoryRef.current;
    if (
      expectedRequestGeneration !== chapterRequestGeneration.current
      || expectedRequestGeneration !== directory.requestGeneration
      || !sameChapterBrowserScope(directory.scope, activeScopeRef.current)
      || !isChapterSelectableFromDirectory(directory.chapters, chapterId, directory.scope.seriesId)
      || selectedChapterIdRef.current === chapterId
    ) return;

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
    guardStructureDraftNavigation(() => backToSeriesNow());
  }

  function backToSeriesNow(): void {
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
    closeFrameReviewDialog();
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

  async function releaseOrRefreshChapterEditLock(
    owner: ChapterEditLockOwner,
    options: { refreshLockSnapshot?: boolean } = {},
  ): Promise<void> {
    const canRelease = owner.newlyAcquiredLock && owner.lockLease !== null
      && owner.scope.services.releaseChapterEditLock !== undefined;
    if (!canRelease && !(options.refreshLockSnapshot && owner.refreshLockOnClose)) return;
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
        && frameStructureEditorRef.current === null
        && ((frameEditOpeningController.current === null && frameEditOpeningChapterIdRef.current === null)
          || (frameEditOpeningController.current === owner.lifecycleController
            && frameEditOpeningChapterIdRef.current === owner.chapter.id))
        && chapterOrderEditorRef.current === null;
    };
    if (canRelease) {
      try {
        await owner.scope.services.releaseChapterEditLock!(owner.chapter.id, controller.signal, owner.lockLease!);
      } catch (cause) {
        const error = normalizeApiError(cause);
        if (error.status === 401 && sameChapterBrowserScope(owner.scope, activeScopeRef.current)) {
          unauthorizedHandlerRef.current();
          return;
        }
        // A failed release is followed by a read when the closing view requested a badge refresh.
        if (!options.refreshLockSnapshot) return;
      }
    }

    const getChapterEditLock = owner.scope.services.getChapterEditLock;
    if (getChapterEditLock === undefined || !canRefreshLockSnapshot()) return;
    let currentLock: ChapterEditLockState;
    try {
      // Lock reads after close are deliberately unleased so the badge reflects server state.
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
    const structureOwner = frameStructureEditorRef.current;
    const pendingOwner = pendingFrameLockReleaseRef.current;
    frameTextEditorRef.current = null;
    frameStructureEditorRef.current = null;
    pendingFrameLockReleaseRef.current = null;
    owner?.lifecycleController.abort();
    structureOwner?.lifecycleController.abort();
    const cleanupOwner = owner ?? structureOwner ?? pendingOwner;
    frameStructureDraftDirtyRef.current = false;
    frameStructureDiscardRequestRef.current = null;
    guardedNavigationSequence.current += 1;
    if (cleanupOwner !== null) {
      const hasProvenLease = cleanupOwner.newlyAcquiredLock && cleanupOwner.lockLease !== null;
      const refreshLockSnapshot = options.refreshLockSnapshot === true
        && (owner === cleanupOwner || structureOwner === cleanupOwner);
      if (options.deferLockRelease && hasProvenLease) {
        pendingFrameLockReleaseRef.current = cleanupOwner;
      } else if (hasProvenLease || (refreshLockSnapshot && cleanupOwner.refreshLockOnClose)) {
        void releaseOrRefreshChapterEditLock(cleanupOwner, { refreshLockSnapshot });
      }
    }

    if (options.updateState !== false) {
      setFrameTextEditor(null);
      setFrameStructureEditor(null);
      setFrameEditOpening(false);
    }
  }

  invalidateFrameEditingWorkflowRef.current = invalidateFrameEditingWorkflow;

  useLayoutEffect(() => {
    const owner = frameTextEditorRef.current;
    const structureOwner = frameStructureEditorRef.current;
    const openingChapterId = frameEditOpeningChapterIdRef.current;
    if ((owner !== null && owner.chapter.id !== selectedChapterId)
      || (structureOwner !== null && structureOwner.chapter.id !== selectedChapterId)
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
    if (owner !== null) void releaseOrRefreshChapterEditLock(owner);
  }, [services, series.id, userId]);

  function closeFrameTextEditor(expected?: FrameTextEditorContext): void {
    const current = frameTextEditorRef.current;
    if (current === null || (expected !== undefined && current !== expected)) return;
    invalidateFrameEditingWorkflow({ refreshLockSnapshot: true });
  }

  function closeFrameStructureEditor(expected?: FrameStructureEditorContext): void {
    const current = frameStructureEditorRef.current;
    if (current === null || (expected !== undefined && current !== expected)) return;
    invalidateFrameEditingWorkflow({ refreshLockSnapshot: true });
    if (current.writeAttemptStarted) {
      const currentIndex = current.baseDirectory.findIndex((chapter) => chapter.id === current.chapter.id);
      requestChapterDirectoryReload({
        scope: current.scope,
        chapterId: current.chapter.id,
        fallbackIndex: Math.max(0, currentIndex),
        message: "写入结果仍未确认，章节与素材已重新读取；请核对当前状态。",
      });
    }
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

  function isFrameStructureEditorCurrent(owner: FrameStructureEditorContext, signal: AbortSignal): boolean {
    const directory = chapterDirectoryRef.current;
    const matchingChapters = directory.chapters?.filter((chapter) => chapter.id === owner.chapter.id) ?? [];
    const directoryMatches = owner.writeAttemptStarted
      ? directory.chapters === null || (matchingChapters.length === 1
        && matchingChapters[0]?.series_id === owner.scope.seriesId)
      : matchingChapters.length === 1 && matchingChapters[0]?.series_id === owner.scope.seriesId;
    return !signal.aborted
      && !owner.lifecycleController.signal.aborted
      && frameStructureEditorRef.current === owner
      && sameChapterBrowserScope(owner.scope, activeScopeRef.current)
      && owner.requestGeneration === chapterRequestGeneration.current
      && selectedChapterIdRef.current === owner.chapter.id
      && sameChapterBrowserScope(directory.scope, owner.scope)
      && directory.requestGeneration === owner.requestGeneration
      && directoryMatches;
  }

  function refreshFrameStructureBase(
    owner: FrameStructureEditorContext,
    directory: Chapter[],
    latestChapter: Chapter,
    assets: StoryboardAsset[],
    resetWriteAttempt: boolean,
  ): FrameStructureEditorContext {
    applyFreshChapterDirectory(directory, owner.scope);
    refreshAssetsAfterFrameRead(owner.chapter.id, assets);
    const nextOwner: FrameStructureEditorContext = {
      ...owner,
      chapter: latestChapter,
      baseDirectory: directory,
      baseContent: latestChapter.content,
      baseAssets: snapshotStoryboardAssetPositions(assets),
      writeAttemptStarted: resetWriteAttempt ? false : owner.writeAttemptStarted,
      pendingExpectedContent: resetWriteAttempt ? null : owner.pendingExpectedContent,
      pendingRemovedAssetId: resetWriteAttempt ? null : owner.pendingRemovedAssetId,
    };
    if (frameStructureEditorRef.current === owner) {
      frameStructureEditorRef.current = nextOwner;
      setFrameStructureEditor(nextOwner);
    }
    return nextOwner;
  }

  function invalidateFrameStructureProjections(owner: FrameStructureEditorContext): void {
    const chapterId = owner.chapter.id;
    assetCache.current.delete(chapterId);
    assetRequestGeneration.current += 1;
    setAssetResult({ chapterId, status: "loading" });
    setFailedImagePositions(new Set());
    closePersonalNotes();
    closeFrameAssetReferences();
    closePersonalRoughCut();
    setResumeLocation(null);
    setPersonalNotesReadRegistration(null);
    personalNotesReadRegistrationRef.current = null;
    personalNotesReadWatermarkRef.current = { owner: null, requestGeneration: -1, invalidatedGenerations: new Set() };
    setPersonalNoteFrameFeedback(null);
    setRoughCutFrameFeedback(null);
    setFrameNavigationFeedback(null);
    chapterDirectoryRef.current = {
      scope: owner.scope,
      requestGeneration: owner.requestGeneration,
      chapters: null,
    };
    setChapters(null);
    setChaptersError(null);
    setChaptersLoading(true);
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
      refreshLockOnClose: false,
      lockNotice: null as string | null,
      lockLease: null as ChapterEditLockLeaseHandle | null,
      lockIdentity: null as FrameTextEditorContext["lockIdentity"],
    };
    frameEditOpeningController.current = controller;
    frameEditOpeningChapterIdRef.current = base.chapter.id;
    setFrameEditOpening(true);
    setChapterMutationNotice(null);
    let newlyAcquiredLock = false;
    let refreshLockOnClose = false;
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
          refreshLockOnClose = true;
          lockNotice = "当前账号已持有有效编辑锁。";
        } else {
          const acquisition = await acquireChapterEditLock(base.chapter.id, controller.signal);
          const acquired = acquisition.state;
          lockLease = acquisition.lease;
          if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
          if (!acquired.locked || typeof acquired.expires_at !== "string"
            || utcTimestampMilliseconds(acquired.expires_at) <= Date.now()) {
            throw new ApiError("http", acquired.message ?? "未能取得有效章节编辑锁。", 409);
          }
          newlyAcquiredLock = lockLease !== null;
          refreshLockOnClose = lockLease === null;
          const verified = lockLease === null
            ? await getChapterEditLock(base.chapter.id, controller.signal)
            : await getChapterEditLock(base.chapter.id, controller.signal, lockLease);
          if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
          if (!validOwnedChapterLock(verified, userId)) {
            throw new ApiError("http", "取得锁后无法核对当前账号、所有权和有效期限，已停止编辑。", 409);
          }
          lockIdentity = chapterEditLockIdentity(verified);
          lockNotice = lockLease === null
            ? `当前账号持有有效章节编辑锁；取得来源无法确认，关闭后保留至服务器过期（${verified.expires_at}）。`
            : "已取得章节编辑锁，有效期至 " + verified.expires_at;
        }
      }
      if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
      const context: FrameTextEditorContext = {
        ...base, newlyAcquiredLock, refreshLockOnClose, lockNotice, lockLease, lockIdentity,
      };
      frameTextEditorRef.current = context;
      setFrameTextEditor(context);
    } catch (cause) {
      if (isCurrent()) {
        const error = normalizeApiError(cause);
        if (error.status === 401) unauthorizedHandlerRef.current();
        else if (error.kind !== "aborted") setChapterMutationNotice(error.detail ?? error.message);
      }
      if (newlyAcquiredLock || refreshLockOnClose) {
        await releaseOrRefreshChapterEditLock({
          ...base, newlyAcquiredLock, refreshLockOnClose, lockNotice: null, lockLease, lockIdentity,
        }, { refreshLockSnapshot: refreshLockOnClose });
      }
    } finally {
      if (frameEditOpeningController.current === controller) {
        frameEditOpeningController.current = null;
        frameEditOpeningChapterIdRef.current = null;
        setFrameEditOpening(false);
      }
    }
  }

  async function openFrameStructureEditor(mode: FrameStructureMode): Promise<void> {
    const directoryAtOpen = chapterDirectoryRef.current;
    if (!canEditFrameStructure || chapterManagement !== null || chapterManagementBlocked
      || chapterOrderEditorRef.current !== null || frameTextEditorRef.current !== null
      || frameStructureEditorRef.current !== null || frameEditOpening || chaptersLoading
      || chapters === null || chaptersError !== null || selectedChapter === null
      || directoryAtOpen.chapters !== chapters
      || !sameChapterBrowserScope(directoryAtOpen.scope, currentScope)
      || directoryAtOpen.requestGeneration !== chapterRequestGeneration.current
      || !isChapterSelectableFromDirectory(directoryAtOpen.chapters, selectedChapter.id, series.id)) return;
    if (mode.kind === "delete" && selectedChapter.content !== null && selectedChapter.content.length <= 1) {
      setChapterMutationNotice("章节至少保留一个镜头，当前操作未打开。");
      return;
    }

    frameEditWorkflowEpoch.current += 1;
    const controller = new AbortController();
    const base = {
      scope: currentScope,
      chapter: selectedChapter,
      requestGeneration: directoryAtOpen.requestGeneration,
      lifecycleController: controller,
      newlyAcquiredLock: false,
      refreshLockOnClose: false,
      lockNotice: null as string | null,
      lockLease: null as ChapterEditLockLeaseHandle | null,
      lockIdentity: null as ChapterEditLockIdentity,
    };
    frameEditOpeningController.current = controller;
    frameEditOpeningChapterIdRef.current = base.chapter.id;
    setFrameEditOpening(true);
    setChapterMutationNotice(null);
    const isCurrent = () => frameEditOpeningController.current === controller
      && frameEditOpeningChapterIdRef.current === base.chapter.id
      && !controller.signal.aborted
      && sameChapterBrowserScope(base.scope, activeScopeRef.current)
      && base.requestGeneration === chapterRequestGeneration.current
      && selectedChapterIdRef.current === base.chapter.id
      && chapterDirectoryRef.current.chapters?.filter((chapter) => chapter.id === base.chapter.id).length === 1;
    let newlyAcquiredLock = false;
    let refreshLockOnClose = false;
    let lockLease: ChapterEditLockLeaseHandle | null = null;
    let lockIdentity: ChapterEditLockIdentity = null;
    try {
      let lockNotice: string | null = null;
      if (typeof series.team_id === "string" && series.team_id !== "") {
        const { getChapterEditLock, acquireChapterEditLock } = services;
        if (getChapterEditLock === undefined || acquireChapterEditLock === undefined
          || services.releaseChapterEditLock === undefined) {
          throw new ApiError("invalid-response", "团队章节锁能力未就绪，已停止修改分镜结构。", null);
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
          refreshLockOnClose = true;
          lockNotice = "当前账号已持有有效编辑锁。";
        } else {
          const acquisition = await acquireChapterEditLock(base.chapter.id, controller.signal);
          lockLease = acquisition.lease;
          if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
          if (!acquisition.state.locked || typeof acquisition.state.expires_at !== "string"
            || utcTimestampMilliseconds(acquisition.state.expires_at) <= Date.now()) {
            throw new ApiError("http", acquisition.state.message ?? "未能取得有效章节编辑锁。", 409);
          }
          newlyAcquiredLock = lockLease !== null;
          refreshLockOnClose = lockLease === null;
          const verified = lockLease === null
            ? await getChapterEditLock(base.chapter.id, controller.signal)
            : await getChapterEditLock(base.chapter.id, controller.signal, lockLease);
          if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
          if (!validOwnedChapterLock(verified, userId)) {
            throw new ApiError("http", "取得锁后无法核对当前账号、所有权和有效期限，已停止修改。", 409);
          }
          lockIdentity = chapterEditLockIdentity(verified);
          lockNotice = lockLease === null
            ? `当前账号持有有效章节编辑锁；取得来源无法确认，关闭后保留至服务器过期（${verified.expires_at}）。`
            : "已取得章节编辑锁，有效期至 " + verified.expires_at;
        }
      }

      const [latestDirectory, assets, characters, scenes, props] = await Promise.all([
        services.listChapters(base.scope.seriesId, controller.signal),
        services.listStoryboardAssets(base.scope.seriesId, base.chapter.id, controller.signal),
        services.listCharacters(base.scope.seriesId, controller.signal),
        services.listScenes(base.scope.seriesId, controller.signal),
        services.listProps(base.scope.seriesId, controller.signal),
      ]);
      if (!isCurrent()) throw new ApiError("aborted", "Request was cancelled.");
      const matching = latestDirectory.filter((chapter) => chapter.id === base.chapter.id);
      const latestChapter = matching[0];
      if (!sameChapterOrderSnapshot(directoryAtOpen.chapters, latestDirectory, base.scope.seriesId)
        || matching.length !== 1 || latestChapter === undefined
        || latestChapter.order !== base.chapter.order
        || !sameChapterContent(latestChapter.content, base.chapter.content)) {
        applyFreshChapterDirectory(latestDirectory, base.scope);
        throw new ApiError("http", "章节内容或镜头顺序已变化；未打开结构编辑，请核对最新章节后重试。", 409);
      }
      const references = inspectStoryboardReferences(latestChapter.content, assets, base.scope.seriesId, base.chapter.id);
      if (!references.ok) throw new ApiError("http", references.message, 409);
      if (!areReferencedFrameAssetPositionsValid(latestChapter, assets, base.scope.seriesId)) {
        throw new ApiError("http", "分镜素材位置与当前章节不一致；未打开结构编辑，请核对最新素材。", 409);
      }
      const deleteTarget = mode.kind === "delete"
        ? resolveDeletableFrameTarget(latestChapter, mode.position - 1, assets)
        : null;
      if (mode.kind === "delete" && deleteTarget === null) {
        throw new ApiError("http", "目标镜头的稳定素材 ID、章节归属或索引无法唯一核对，未打开删除操作。", 409);
      }
      const positionCount = latestChapter.content?.length ?? 0;
      if (mode.kind === "insert" && (!Number.isSafeInteger(mode.afterPosition)
        || mode.afterPosition < 0 || mode.afterPosition > positionCount)) {
        throw new ApiError("http", "插入位置已变化，请重新读取章节后再试。", 409);
      }
      const context: FrameStructureEditorContext = {
        ...base,
        chapter: latestChapter,
        newlyAcquiredLock,
        refreshLockOnClose,
        lockNotice,
        lockLease,
        lockIdentity,
        mode,
        characters: characters.filter((asset) => asset.series_id === base.scope.seriesId),
        scenes: scenes.filter((asset) => asset.series_id === base.scope.seriesId),
        props: props.filter((asset) => asset.series_id === base.scope.seriesId),
        baseDirectory: latestDirectory,
        baseContent: latestChapter.content,
        baseAssets: snapshotStoryboardAssetPositions(assets),
        deleteTarget,
        writeAttemptStarted: false,
        pendingExpectedContent: null,
        pendingRemovedAssetId: null,
      };
      frameStructureEditorRef.current = context;
      setFrameStructureEditor(context);
    } catch (cause) {
      if (isCurrent()) {
        const error = normalizeApiError(cause);
        if (error.status === 401) unauthorizedHandlerRef.current();
        else if (error.kind !== "aborted") setChapterMutationNotice(error.detail ?? error.message);
      }
      if (newlyAcquiredLock || refreshLockOnClose) {
        await releaseOrRefreshChapterEditLock({
          ...base, newlyAcquiredLock, refreshLockOnClose, lockNotice: null, lockLease, lockIdentity,
        }, { refreshLockSnapshot: refreshLockOnClose });
      }
    } finally {
      if (frameEditOpeningController.current === controller) {
        frameEditOpeningController.current = null;
        frameEditOpeningChapterIdRef.current = null;
        setFrameEditOpening(false);
      }
    }
  }

  async function copyFrameText(position: number): Promise<void> {
    if (selectedChapter === null) return;
    const text = readEditableFrameText(selectedChapter, position);
    if (text === null) {
      setChapterMutationNotice("当前镜头文字无法识别，未复制内容。");
      return;
    }
    try {
      if (typeof navigator.clipboard?.writeText !== "function") {
        throw new Error("clipboard unavailable");
      }
      await navigator.clipboard.writeText(text);
      setChapterMutationNotice(`镜头 ${position} 的文字已复制。`);
    } catch {
      setChapterMutationNotice("无法访问剪贴板；请检查浏览器权限后重试。");
    }
  }

  async function readFrameStructureSnapshot(
    owner: FrameStructureEditorContext,
    signal: AbortSignal,
  ): Promise<{
    directory: Chapter[];
    chapter: Chapter;
    assets: StoryboardAsset[];
    notes: PersonalProductionSnapshot;
    roughCut: PersonalRoughCutSnapshot;
  }> {
    const [initialDirectory, initialAssets, notes, roughCut] = await Promise.all([
      owner.scope.services.listChapters(owner.scope.seriesId, signal),
      owner.scope.services.listStoryboardAssets(owner.scope.seriesId, owner.chapter.id, signal),
      owner.scope.services.getPersonalProductionNotes(owner.chapter.id, signal),
      owner.scope.services.getPersonalRoughCut(owner.chapter.id, signal),
    ]);
    if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
    const [directory, assets] = await Promise.all([
      owner.scope.services.listChapters(owner.scope.seriesId, signal),
      owner.scope.services.listStoryboardAssets(owner.scope.seriesId, owner.chapter.id, signal),
    ]);
    if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
    const initialMatches = initialDirectory.filter((chapter) => chapter.id === owner.chapter.id);
    const matches = directory.filter((chapter) => chapter.id === owner.chapter.id);
    const chapter = matches[0];
    const initialChapter = initialMatches[0];
    if (initialMatches.length !== 1 || initialChapter === undefined
      || matches.length !== 1 || chapter === undefined || chapter.series_id !== owner.scope.seriesId
      || initialChapter.order !== chapter.order
      || !sameChapterContent(initialChapter.content, chapter.content)
      || !sameChapterOrderSnapshot(initialDirectory, directory, owner.scope.seriesId)
      || !sameStoryboardAssetPositions(initialAssets, assets)) {
      throw new ApiError("invalid-response", "读取期间章节或素材关系再次变化，结果无法确认。", null);
    }
    return { directory, chapter, assets, notes, roughCut };
  }

  function structureReadbackMatches(
    owner: FrameStructureEditorContext,
    snapshot: Awaited<ReturnType<typeof readFrameStructureSnapshot>>,
    expectedContent: StoryboardFrame[],
  ): boolean {
    return snapshot.chapter.order === owner.chapter.order
      && isFrameStructureReadbackValid(
        expectedContent,
        snapshot.chapter.content,
        snapshot.assets,
        owner.scope.seriesId,
        owner.chapter.id,
      )
      && areFrameStructureMappingsValid(snapshot.chapter, snapshot.notes, snapshot.roughCut);
  }

  function structureBaselineMatches(
    owner: FrameStructureEditorContext,
    snapshot: Awaited<ReturnType<typeof readFrameStructureSnapshot>>,
  ): boolean {
    return snapshot.chapter.order === owner.chapter.order
      && sameChapterOrderSnapshot(owner.baseDirectory, snapshot.directory, owner.scope.seriesId)
      && sameChapterContent(owner.baseContent, snapshot.chapter.content)
      && sameStoryboardAssetPositions(owner.baseAssets, snapshot.assets);
  }

  function setStructureWriteAttempt(
    owner: FrameStructureEditorContext,
    started: boolean,
    expectedContent: StoryboardFrame[] | null,
    removedAssetId: string | null,
  ): FrameStructureEditorContext {
    const next = {
      ...owner,
      writeAttemptStarted: started,
      pendingExpectedContent: expectedContent,
      pendingRemovedAssetId: removedAssetId,
    };
    if (frameStructureEditorRef.current === owner) {
      frameStructureEditorRef.current = next;
      setFrameStructureEditor(next);
    }
    return next;
  }

  async function submitFrameStructure(
    expectedOwner: FrameStructureEditorContext,
    draft: FrameStructureDraft | null,
    signal: AbortSignal,
  ): Promise<FrameStructureSubmitResult> {
    const activeOwner = frameStructureEditorRef.current;
    if (activeOwner === null || activeOwner !== expectedOwner
      || !isFrameStructureEditorCurrent(activeOwner, signal)) {
      throw new ApiError("aborted", "编辑内容已切换。", null);
    }
    let owner = activeOwner;
    if ((owner.mode.kind === "insert") !== (draft !== null)) {
      return { status: "conflict", message: "分镜草稿类型与当前操作不一致，未发送修改。" };
    }

    let directory: Chapter[];
    let assets: StoryboardAsset[];
    let characters: Character[];
    let scenes: Scene[];
    let props: Prop[];
    try {
      [directory, assets, characters, scenes, props] = await Promise.all([
        owner.scope.services.listChapters(owner.scope.seriesId, signal),
        owner.scope.services.listStoryboardAssets(owner.scope.seriesId, owner.chapter.id, signal),
        owner.scope.services.listCharacters(owner.scope.seriesId, signal),
        owner.scope.services.listScenes(owner.scope.seriesId, signal),
        owner.scope.services.listProps(owner.scope.seriesId, signal),
      ]);
    } catch (cause) {
      if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (normalizeApiError(cause).status === 401) unauthorizedHandlerRef.current();
      throw cause;
    }
    if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
    const chapterMatches = directory.filter((chapter) => chapter.id === owner.chapter.id);
    const latestChapter = chapterMatches[0];
    if (chapterMatches.length !== 1 || latestChapter === undefined || latestChapter.series_id !== owner.scope.seriesId) {
      return { status: "conflict", message: "最新目录无法唯一确认当前章节，草稿仍保留。" };
    }
    const directoryUnchanged = sameChapterOrderSnapshot(owner.baseDirectory, directory, owner.scope.seriesId);
    const chapterUnchanged = latestChapter.order === owner.chapter.order
      && sameChapterContent(latestChapter.content, owner.baseContent);
    if (!directoryUnchanged || !chapterUnchanged) {
      refreshFrameStructureBase(owner, directory, latestChapter, assets, false);
      return { status: "conflict", message: "章节内容或镜头顺序已变化；草稿保留，请核对最新章节后再编辑。" };
    }

    const references = inspectStoryboardReferences(latestChapter.content, assets, owner.scope.seriesId, owner.chapter.id);
    if (!references.ok) return { status: "conflict", message: references.message };
    if (!sameStoryboardAssetPositions(owner.baseAssets, assets)
      || !areReferencedFrameAssetPositionsValid(latestChapter, assets, owner.scope.seriesId)) {
      return { status: "conflict", message: "分镜素材位置在编辑期间发生变化；未发送修改，草稿仍保留，请核对最新素材。" };
    }
    let expectedContent: StoryboardFrame[] | null = null;
    let removedAssetId: string | null = null;
    let targetIndex: number | null = null;
    if (owner.mode.kind === "insert") {
      const selectedCharacterIds = draft?.characterAssetIds ?? [];
      const selectedSceneIds = draft?.sceneAssetIds ?? [];
      const selectedPropIds = draft?.propAssetIds ?? [];
      if (!hasUniqueOwnedAssetIds(characters, selectedCharacterIds, owner.scope.seriesId)
        || !hasUniqueOwnedAssetIds(scenes, selectedSceneIds, owner.scope.seriesId)
        || !hasUniqueOwnedAssetIds(props, selectedPropIds, owner.scope.seriesId)) {
        return { status: "conflict", message: "所选角色、场景或道具已变化，未发送修改；请重新选择当前剧集素材。" };
      }
      const afterIndex = owner.mode.afterPosition - 1;
      expectedContent = buildInsertedFrameContent(latestChapter.content, afterIndex, draft!);
      if (expectedContent === null) return { status: "conflict", message: "插入位置已变化，未发送修改。" };
      targetIndex = afterIndex + 1;
    } else {
      const currentTarget = resolveDeletableFrameTarget(latestChapter, owner.mode.position - 1, assets);
      if (currentTarget === null || owner.deleteTarget === null
        || currentTarget.frameIndex !== owner.deleteTarget.frameIndex
        || currentTarget.storyboardAssetId !== owner.deleteTarget.storyboardAssetId) {
        return { status: "conflict", message: "目标镜头的稳定素材身份或索引已变化，未发送删除。草稿仍保留。" };
      }
      if (latestChapter.content === null || latestChapter.content.length <= 1) {
        return { status: "conflict", message: "章节至少保留一个镜头；最新章节不满足删除条件。" };
      }
      targetIndex = currentTarget.frameIndex;
      removedAssetId = currentTarget.storyboardAssetId;
      expectedContent = latestChapter.content.filter((_frame, index) => index !== currentTarget.frameIndex);
    }
    if (expectedContent === null || targetIndex === null) {
      return { status: "conflict", message: "当前分镜操作无法构造完整章节内容，未发送修改。" };
    }

    if (typeof series.team_id === "string" && series.team_id !== "") {
      const getChapterEditLock = owner.scope.services.getChapterEditLock;
      if (getChapterEditLock === undefined) {
        return { status: "conflict", message: "团队章节锁能力不可用，草稿仍保留。" };
      }
      let lock: ChapterEditLockState;
      try {
        lock = owner.lockLease === null
          ? await getChapterEditLock(owner.chapter.id, signal)
          : await getChapterEditLock(owner.chapter.id, signal, owner.lockLease);
      } catch (cause) {
        if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
        if (normalizeApiError(cause).status === 401) unauthorizedHandlerRef.current();
        throw cause;
      }
      if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (!validOwnedChapterLock(lock, owner.scope.userId)
        || !sameChapterEditLockIdentity(owner.lockIdentity, lock)) {
        return { status: "conflict", message: "提交前无法确认当前账号仍持有有效章节锁；草稿仍保留。" };
      }
    }

    owner = setStructureWriteAttempt(owner, true, expectedContent, removedAssetId);
    invalidateFrameStructureProjections(owner);
    let writeError: ApiError | null = null;
    try {
      if (owner.mode.kind === "insert") {
        if (owner.scope.services.updateChapter === undefined) {
          throw new ApiError("invalid-response", "当前服务不支持插入分镜。", null);
        }
        await owner.scope.services.updateChapter(
          owner.scope.seriesId,
          owner.chapter.id,
          { content: expectedContent },
          signal,
        );
      } else {
        if (owner.scope.services.deleteFrame === undefined || targetIndex === null) {
          throw new ApiError("invalid-response", "当前服务不支持安全删除分镜。", null);
        }
        await owner.scope.services.deleteFrame(owner.scope.seriesId, owner.chapter.id, targetIndex, signal);
      }
    } catch (cause) {
      const error = normalizeApiError(cause);
      if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (error.status === 401) unauthorizedHandlerRef.current();
      if (error.kind === "aborted") throw cause;
      writeError = error;
    }
    if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");

    let snapshot: Awaited<ReturnType<typeof readFrameStructureSnapshot>>;
    try {
      snapshot = await readFrameStructureSnapshot(owner, signal);
    } catch (cause) {
      if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      const error = normalizeApiError(cause);
      if (error.status === 401) unauthorizedHandlerRef.current();
      if (error.kind === "aborted") throw cause;
      return { status: "unknown", message: "请求已发送，但章节、素材或制作映射无法重新读取；结果尚未确认。草稿已冻结，请只核对服务器状态。" };
    }

    if (structureReadbackMatches(owner, snapshot, expectedContent)) {
      refreshFrameStructureBase(owner, snapshot.directory, snapshot.chapter, snapshot.assets, true);
      setChapterMutationNotice(owner.mode.kind === "insert"
        ? "分镜已保存并重新核对。"
        : "分镜已删除并核对；聊天记录清理与后续索引由服务器处理。");
      return { status: "saved", message: owner.mode.kind === "insert" ? "分镜已保存并重新核对。" : "分镜已删除并重新核对。" };
    }

    if (structureBaselineMatches(owner, snapshot)) {
      if (writeError !== null && writeError.kind === "http" && writeError.status !== null && writeError.status < 500) {
        refreshFrameStructureBase(owner, snapshot.directory, snapshot.chapter, snapshot.assets, true);
        return { status: "conflict", message: writeError.detail ?? "服务器明确拒绝了本次修改，且最新章节仍为原内容。草稿保留。" };
      }
      refreshFrameStructureBase(owner, snapshot.directory, snapshot.chapter, snapshot.assets, false);
      return {
        status: "unknown",
        message: "最新章节仍是修改前内容，但无法证明请求不会迟到落库。当前操作继续冻结，请只核对服务器状态，或结束本次核对后重新读取再创建草稿。",
      };
    }

    return { status: "unknown", message: "最新章节或制作映射与预期不一致，无法判断请求是否完成。草稿已冻结，请只核对服务器状态。" };
  }

  async function verifyFrameStructureWrite(
    expectedOwner: FrameStructureEditorContext,
    signal: AbortSignal,
  ): Promise<FrameStructureVerifyResult> {
    let owner = frameStructureEditorRef.current;
    if (owner === null || owner.chapter.id !== expectedOwner.chapter.id
      || !sameChapterBrowserScope(owner.scope, expectedOwner.scope)
      || !isFrameStructureEditorCurrent(owner, signal)) {
      throw new ApiError("aborted", "编辑内容已切换。", null);
    }
    try {
      const snapshot = await readFrameStructureSnapshot(owner, signal);
      if (owner.pendingExpectedContent !== null
        && structureReadbackMatches(owner, snapshot, owner.pendingExpectedContent)) {
        refreshFrameStructureBase(owner, snapshot.directory, snapshot.chapter, snapshot.assets, true);
        setChapterMutationNotice("服务器状态已核对，分镜修改已完成。");
        return { status: "saved", message: "服务器状态已核对，分镜修改已完成。" };
      }
      if (structureBaselineMatches(owner, snapshot)) {
        refreshFrameStructureBase(owner, snapshot.directory, snapshot.chapter, snapshot.assets, false);
        return {
          status: "unknown",
          message: "服务器仍是修改前内容，但无法证明原请求不会迟到落库。当前操作继续冻结；请只核对状态，或结束本次核对后重新读取再创建草稿。",
        };
      }
      return { status: "unknown", message: "服务器当前内容与修改前和草稿结果都不一致，草稿继续冻结。" };
    } catch (cause) {
      if (!isFrameStructureEditorCurrent(owner, signal)) throw new ApiError("aborted", "Request was cancelled.");
      if (normalizeApiError(cause).status === 401) unauthorizedHandlerRef.current();
      throw cause;
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
      const lock = owner.lockLease === null
        ? await owner.scope.services.getChapterEditLock(owner.chapter.id, signal)
        : await owner.scope.services.getChapterEditLock(owner.chapter.id, signal, owner.lockLease);
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
    || chapterOrderEditor !== null || frameTextEditor !== null || frameStructureEditor !== null || frameEditOpening
    || storyboardGenerationDialog !== null || frameReviewDialog !== null;
  const canEditStoryboard = series.can_enter && services.updateChapter !== undefined;
  const canEditFrameStructure = series.can_enter
    && services.updateChapter !== undefined
    && services.deleteFrame !== undefined;
  const canReorderChapters = series.can_enter && services.reorderChapters !== undefined;
  const canManageChapters = series.can_enter
    && services.createChapter !== undefined
    && services.updateChapter !== undefined
    && services.deleteChapter !== undefined;

  function closeFrameReviewDialog(expected?: FrameReviewDialogOwner): void {
    const owner = frameReviewDialogRef.current;
    if (expected !== undefined && owner !== expected) return;
    frameReviewDialogRef.current = null;
    frameReviewVerifiedSourceRef.current = null;
    setFrameReviewDialog(null);
  }

  function isFrameReviewOwnerCurrent(owner: FrameReviewDialogOwner): boolean {
    const directory = chapterDirectoryRef.current;
    const chaptersForTarget = directory.chapters?.filter((chapter) => chapter.id === owner.chapter.id) ?? [];
    const chapter = chaptersForTarget[0];
    if (
      frameReviewDialogRef.current !== owner
      || !sameChapterBrowserScope(owner.scope, activeScopeRef.current)
      || owner.requestGeneration !== chapterRequestGeneration.current
      || selectedChapterIdRef.current !== owner.chapter.id
      || !sameChapterBrowserScope(directory.scope, owner.scope)
      || directory.requestGeneration !== owner.requestGeneration
      || chaptersForTarget.length !== 1
      || chapter?.series_id !== owner.scope.seriesId
    ) {
      return false;
    }
    if (frameReviewVerifiedSourceRef.current === owner) return true;
    const currentFrame = chapter.content?.[owner.frameIndex];
    return currentFrame !== undefined
      && storyboardFrameId(currentFrame) === owner.storyboardAssetId
      && frameSourceVersion(chapter, currentFrame) === owner.sourceVersion
      && readEditableFrameText(chapter, owner.position) === owner.sourceText;
  }

  async function openFrameReview(
    sourceFrame: StoryboardFrame,
    position: number,
    storyboardAssetId: string,
  ): Promise<void> {
    const directory = chapterDirectoryRef.current;
    const assets = currentAssetResult?.status === "ready" ? currentAssetResult.assets : null;
    const matchingAssets = assets?.filter((asset) => (
      asset.id === storyboardAssetId
      && asset.series_id === series.id
      && asset.chapter_id === selectedChapter?.id
      && asset.frame_index === position - 1
    )) ?? [];
    if (
      !canEditStoryboard
      || services.frameReview === undefined
      || chapterManagement !== null
      || chapterManagementBlocked
      || frameReviewDialogRef.current !== null
      || chaptersLoading
      || chaptersError !== null
      || chapters === null
      || selectedChapter === null
      || assets === null
      || matchingAssets.length !== 1
      || frames[position - 1] !== sourceFrame
      || !sameChapterBrowserScope(directory.scope, currentScope)
      || directory.requestGeneration !== chapterRequestGeneration.current
      || directory.chapters !== chapters
      || !isChapterSelectableFromDirectory(directory.chapters, selectedChapter.id, series.id)
    ) {
      return;
    }
    const sourceText = readEditableFrameText(selectedChapter, position);
    if (sourceText === null) return;
    const owner: FrameReviewDialogOwner = {
      scope: currentScope,
      chapter: selectedChapter,
      position,
      frameIndex: position - 1,
      storyboardAssetId,
      sourceText,
      sourceVersion: frameSourceVersion(selectedChapter, sourceFrame),
      requestGeneration: directory.requestGeneration,
    };
    frameReviewVerifiedSourceRef.current = null;
    frameReviewDialogRef.current = owner;
    setFrameReviewDialog(owner);
  }

  async function refreshFrameReviewSource(
    owner: FrameReviewDialogOwner,
    result: FrameReviewResult,
  ): Promise<FrameReviewSourceCheck> {
    if (!isFrameReviewOwnerCurrent(owner)) {
      return { status: "unconfirmed", message: "当前章节或登录范围已变化，未将迟到结果标记为完成。" };
    }
    const directoryAtRead = chapterDirectoryRef.current;
    const requestGeneration = chapterRequestGeneration.current;
    let nextChapters: Chapter[];
    let nextAssets: StoryboardAsset[];
    try {
      [nextChapters, nextAssets] = await Promise.all([
        owner.scope.services.listChapters(owner.scope.seriesId, new AbortController().signal),
        owner.scope.services.listStoryboardAssets(owner.scope.seriesId, owner.chapter.id, new AbortController().signal),
      ]);
    } catch (cause) {
      if (normalizeApiError(cause).status === 401 && isFrameReviewOwnerCurrent(owner)) {
        unauthorizedHandlerRef.current();
      }
      return { status: "unconfirmed", message: "任务已返回结果，但章节或素材真源读取失败；请重新读取后核对。" };
    }
    if (
      !isFrameReviewOwnerCurrent(owner)
      || chapterRequestGeneration.current !== requestGeneration
      || chapterDirectoryRef.current !== directoryAtRead
    ) {
      return { status: "unconfirmed", message: "章节读取期间页面范围发生变化，结果尚未核对。" };
    }
    const matchingChapters = nextChapters.filter((chapter) => chapter.id === owner.chapter.id);
    const latestChapter = matchingChapters[0];
    if (matchingChapters.length !== 1 || latestChapter?.series_id !== owner.scope.seriesId) {
      return { status: "unconfirmed", message: "章节源数据无法唯一确认，结果没有标记为已应用。" };
    }
    const latestFrame = latestChapter.content?.[owner.frameIndex];
    const latestFrameText = readEditableFrameText(latestChapter, owner.position);
    const matchingAssets = nextAssets.filter((asset) => (
      asset.id === owner.storyboardAssetId
      && asset.series_id === owner.scope.seriesId
      && asset.chapter_id === owner.chapter.id
      && asset.frame_index === owner.frameIndex
    ));
    const applied = latestFrame !== undefined
      && storyboardFrameId(latestFrame) === owner.storyboardAssetId
      && matchingAssets.length === 1
      && result.original_text === owner.sourceText
      && latestFrameText === result.optimized_text;

    frameReviewVerifiedSourceRef.current = owner;
    applyFreshChapterDirectory(nextChapters, owner.scope);
    refreshAssetsAfterFrameRead(owner.chapter.id, nextAssets);
    return applied
      ? { status: "applied" }
      : { status: "unconfirmed", message: "已重新读取章节与素材，但镜头文字或来源版本与任务结果不一致；请核对当前真源。" };
  }

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
                  {canEditFrameStructure && (
                    <button
                      className="text-button frame-structure-insert-opening"
                      disabled={chaptersLoading || chapterManagementBlocked || chapterManagement !== null || frameEditOpening}
                      onClick={() => void openFrameStructureEditor({ kind: "insert", afterPosition: 0 })}
                      title={chapterManagementBlocked ? "请先关闭当前制作记录或编辑窗口" : undefined}
                      type="button"
                    >在开头插入分镜</button>
                  )}
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
                      const storyboardAssetId = storyboardFrameId(sourceFrame);
                      const reviewAssets = storyboardAssetId !== null && currentAssetResult?.status === "ready"
                        ? currentAssetResult.assets.filter((asset) => (
                          asset.id === storyboardAssetId
                          && asset.series_id === series.id
                          && asset.chapter_id === selectedChapter?.id
                          && asset.frame_index === frame.position - 1
                        ))
                        : [];
                      const canReviewFrame = canEditStoryboard
                        && services.frameReview !== undefined
                        && sourceFrame !== undefined
                        && storyboardAssetId !== null
                        && reviewAssets.length === 1
                        && frame.text.kind !== "invalid";
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
                              {canReviewFrame && sourceFrame !== undefined && storyboardAssetId !== null && (
                                <button
                                  className="text-button frame-review-open-button"
                                  disabled={chapterManagementBlocked || chapterManagement !== null || frameEditOpening}
                                  onClick={() => void openFrameReview(sourceFrame, frame.position, storyboardAssetId)}
                                  title={chapterPanelsOpen ? "请先关闭当前制作记录或原图引用面板" : undefined}
                                  type="button"
                                >AI 复核</button>
                              )}
                              <button
                                className="text-button frame-copy-text-button"
                                disabled={selectedChapter === null || chapterManagementBlocked || chapterManagement !== null || frameEditOpening}
                                onClick={() => void copyFrameText(frame.position)}
                                type="button"
                              >复制文字</button>
                              {canEditFrameStructure && sourceFrame !== undefined && (
                                <>
                                  <button
                                    className="text-button frame-structure-insert-after"
                                    disabled={chapterManagementBlocked || chapterManagement !== null || frameEditOpening}
                                    onClick={() => void openFrameStructureEditor({ kind: "insert", afterPosition: frame.position })}
                                    title={chapterManagementBlocked ? "请先关闭当前制作记录或编辑窗口" : undefined}
                                    type="button"
                                  >在此镜头后插入</button>
                                  <button
                                    className="text-button chapter-delete-action frame-structure-delete"
                                    disabled={chapterManagementBlocked || chapterManagement !== null || frameEditOpening || frames.length <= 1}
                                    onClick={() => void openFrameStructureEditor({ kind: "delete", position: frame.position })}
                                    title={frames.length <= 1 ? "章节至少保留一个镜头" : undefined}
                                    type="button"
                                  >删除镜头</button>
                                </>
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
      {frameReviewDialog !== null
        && sameChapterBrowserScope(frameReviewDialog.scope, currentScope)
        && selectedChapterId === frameReviewDialog.chapter.id && (
          <FrameReviewDialog
            key={`${frameReviewDialog.scope.userId}:${frameReviewDialog.scope.seriesId}:${frameReviewDialog.chapter.id}:${frameReviewDialog.storyboardAssetId}:${frameReviewDialog.frameIndex}:${frameReviewDialog.requestGeneration}`}
            chapterId={frameReviewDialog.chapter.id}
            chapterTitle={frameReviewDialog.chapter.title}
            frameIndex={frameReviewDialog.frameIndex}
            isCurrent={() => isFrameReviewOwnerCurrent(frameReviewDialog)}
            onClose={() => closeFrameReviewDialog(frameReviewDialog)}
            onSourceApplied={(result) => refreshFrameReviewSource(frameReviewDialog, result)}
            onUnauthorized={() => {
              if (isFrameReviewOwnerCurrent(frameReviewDialog)) unauthorizedHandlerRef.current();
            }}
            services={frameReviewDialog.scope.services}
            sourceText={frameReviewDialog.sourceText}
            sourceVersion={frameReviewDialog.sourceVersion}
            storyboardAssetId={frameReviewDialog.storyboardAssetId}
            seriesId={frameReviewDialog.scope.seriesId}
            userId={frameReviewDialog.scope.userId}
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
      {frameStructureEditor !== null && sameChapterBrowserScope(frameStructureEditor.scope, currentScope) && (
        <FrameStructureEditor
          key={`${frameStructureEditor.scope.userId}:${frameStructureEditor.scope.seriesId}:${frameStructureEditor.chapter.id}:${frameStructureEditor.requestGeneration}:${frameStructureEditor.mode.kind}:${frameStructureEditor.mode.kind === "insert" ? frameStructureEditor.mode.afterPosition : frameStructureEditor.mode.position}`}
          chapter={frameStructureEditor.chapter}
          characters={frameStructureEditor.characters}
          lifecycleSignal={frameStructureEditor.lifecycleController.signal}
          lockNotice={frameStructureEditor.lockNotice}
          mode={frameStructureEditor.mode}
          onClose={() => closeFrameStructureEditor()}
          onDraftDirtyChange={(dirty) => {
            if (frameStructureEditorRef.current === frameStructureEditor) {
              frameStructureDraftDirtyRef.current = dirty;
            }
          }}
          onRegisterDiscardRequest={(request) => {
            if (frameStructureEditorRef.current === frameStructureEditor) {
              frameStructureDiscardRequestRef.current = request;
            }
          }}
          onSubmit={(draft, signal) => submitFrameStructure(frameStructureEditor, draft, signal)}
          onVerify={(signal) => verifyFrameStructureWrite(frameStructureEditor, signal)}
          props={frameStructureEditor.props}
          scenes={frameStructureEditor.scenes}
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
