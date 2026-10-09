import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ApiError } from "../../shared/api/errors";
import type { Chapter, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
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

interface ChapterDirectorySnapshot {
  scope: ChapterBrowserScope;
  requestGeneration: number;
  chapters: Chapter[] | null;
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
  const [navigationIssue, setNavigationIssue] = useState<{ epoch: number; issue: NavigationIssue } | null>(null);
  const [pendingFrameNavigation, setPendingFrameNavigation] = useState<PendingFrameNavigation | null>(null);
  const pendingFrameNavigationRef = useRef<PendingFrameNavigation | null>(null);
  pendingFrameNavigationRef.current = pendingFrameNavigation;
  const [frameNavigationFeedback, setFrameNavigationFeedback] = useState<FrameNavigationFeedback | null>(null);
  const [chapterReloadKey, setChapterReloadKey] = useState(0);
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
  const chapterRequestGeneration = useRef(0);
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
    const controller = new AbortController();
    let current = true;
    assetCache.current.clear();
    setChapters(null);
    setSelectedChapterId(null);
    setAssetResult(null);
    setChaptersError(null);
    setNavigationIssue(null);
    setPendingFrameNavigation(null);
    setFrameNavigationFeedback(null);
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
        setSelectedChapterId(result[0]?.id ?? null);
        selectedChapterIdRef.current = result[0]?.id ?? null;
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
    setChapterReloadKey((key) => key + 1);
  }

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
    pendingFrameNavigationRef.current = null;
    setPendingFrameNavigation(null);
    setFrameNavigationFeedback(null);
    closePersonalNotes();
    closeFrameAssetReferences();
    closePersonalRoughCut();
    onBack();
  }

  const selectedIndex = chapters?.findIndex((chapter) => chapter.id === selectedChapterId) ?? -1;
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
          <p className="eyebrow">只读浏览</p>
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
            {!chaptersLoading && chaptersError === null && chapters !== null && (
              <span className="chapter-count">{chapters.length} 章</span>
            )}
          </div>

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
                  <span className="readonly-pill">只读</span>
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
                <span>此页面提供只读展示，不包含编辑、锁定或提交操作。</span>
              </div>

              {lockMessage !== null && (
                <div className="chapter-lock-note" role="note">
                  <span className="lock-glyph" aria-hidden="true">▣</span>
                  <span>{lockMessage} · 这是读取时的状态快照，当前页面始终只读。</span>
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
                            <span className="frame-preview-state">
                              {frame.hasPreview ? "已有预览" : "暂无预览"}
                            </span>
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
    </section>
  );
}
