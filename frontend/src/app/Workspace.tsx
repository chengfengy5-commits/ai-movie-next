import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../shared/api/errors";
import type { Series } from "../shared/api/contracts";
import { useAuth } from "../features/auth/AuthProvider";
import { LoginPage } from "../features/auth/LoginPage";
import { AppearanceControl } from "../features/appearance/AppearanceControl";
import { PublicSiteTutorialLink } from "../features/site-config/PublicSiteConfigProvider";
import { ChapterBrowser, type ChapterNavigationIntent } from "../features/chapters/ChapterBrowser";
import { AssetLibrary, type AssetNavigationIntent } from "../features/assets/AssetLibrary";
import { FusedAssetsPage } from "../features/fused-assets/FusedAssetsPage";
import type { FusedGenerationOpenIntent } from "../features/fused-assets/fusedGenerationEntryIntent";
import type {
  AssetFrameUsageChapterTarget,
  AssetFrameUsageFrameTarget,
  AssetFrameUsageWorkbenchTarget,
} from "../features/assets/AssetFrameUsagePanel";
import type { FrameAssetReferenceAssetTarget } from "../features/chapters/FrameAssetReferencesPanel";
import { SeriesPage } from "../features/series/SeriesPage";
import { MyTasksPage } from "../features/tasks/MyTasksPage";
import { CreatorSettingsPage } from "../features/creator-settings/CreatorSettingsPage";
import { MediaGenerationPage } from "../features/media-generation/MediaGenerationPage";
import { ChatAssistantPage, type ChatAssistantSourceIntent } from "../features/chat/ChatAssistantPage";
import { AccountPage } from "../features/account/AccountPage";
import { TeamsPage } from "../features/teams/TeamsPage";
import { SeriesWorkbenchSurface, type SeriesWorkbenchSurfaceMode } from "../features/series-workbench/SeriesWorkbenchSurface";
import {
  defaultSeriesWorkbenchPreferences,
  readSeriesWorkbenchPreferences,
  writeSeriesWorkbenchPreferences,
  type SeriesWorkbenchLeftView,
  type SeriesWorkbenchPreferences,
  type SeriesWorkbenchRoute,
} from "../features/series-workbench/preferences";
import type {
  WorkbenchSourceIntent,
  WorkbenchSourceSelection,
} from "../features/series-workbench/sourceIntent";
import type { FusedSourceEntrySelection } from "../features/series-workbench/fusedSourceEntry";
import "../features/series-workbench/seriesWorkbench.css";
import type { CreatorSettingsServicePort } from "../shared/api/creatorSettings";
import type { ProviderQuotaServicePort } from "../shared/api/providerQuota";
import type { ZhangyugePatServicePort } from "../shared/api/zhangyugePat";
import {
  createTeamCollaborationWriteScope,
  sameTeamCollaborationWriteScope,
  TeamCollaborationWritePermit,
} from "./teamCollaborationWritePermit";

function restoreErrorMessage(error: ApiError): string {
  if (error.status === 401) {
    return "当前登录状态已失效，请重新登录。";
  }
  if (error.kind === "timeout") {
    return "验证请求超时，请检查本地服务后重试。";
  }
  if (error.kind === "network") {
    return "无法连接本地服务，请确认服务正在运行。";
  }
  return error.detail ?? "暂时无法验证当前登录状态。";
}

type WorkspaceView = "series" | "chapters" | "assets" | "fused-assets" | "tasks" | "teams" | "creator-settings"
  | "media-generation" | "chat-assistant" | "series-workbench" | "account";

interface FusedPageHost {
  readonly series: Series;
  readonly openIntent: FusedGenerationOpenIntent | null;
}

interface ContinuousOriginalSelection {
  readonly position: number;
  readonly text: string | null;
  readonly status: "text" | "missing" | "unrecognized";
}

interface ContinuousOriginalOwner {
  readonly userId: string;
  readonly apiBaseUrl: string | null;
  readonly services: object;
  readonly sessionGeneration: number;
  readonly scopeEpoch: number;
  readonly seriesId: string;
}

interface OwnedContinuousOriginal {
  readonly owner: ContinuousOriginalOwner;
  readonly callbackEpoch: number;
  readonly selection: ContinuousOriginalSelection;
}

function sameContinuousOriginalOwner(
  left: ContinuousOriginalOwner | null,
  right: ContinuousOriginalOwner | null,
): boolean {
  return left === right || (left !== null && right !== null
    && left.userId === right.userId
    && left.apiBaseUrl === right.apiBaseUrl
    && left.services === right.services
    && left.sessionGeneration === right.sessionGeneration
    && left.scopeEpoch === right.scopeEpoch
    && left.seriesId === right.seriesId);
}

function Dashboard() {
  const { services, state, logout, refreshCurrentUser, sessionGeneration } = useAuth();
  const collaborationActorId = state.status === "authenticated" ? state.user.id : null;
  const collaborationWriteScope = useMemo(
    () => createTeamCollaborationWriteScope(collaborationActorId, sessionGeneration, services),
    [collaborationActorId, services, services.teamManagement, services.teamSeries, sessionGeneration],
  );
  const collaborationWritePermitRef = useRef<TeamCollaborationWritePermit | null>(null);
  if (collaborationWritePermitRef.current === null) {
    collaborationWritePermitRef.current = new TeamCollaborationWritePermit();
  }
  const collaborationWritePermit = collaborationWritePermitRef.current;
  // Keep host guard ownership distinct when a port changes without replacing services.
  const seriesShareGuardScopeRef = useRef(collaborationWriteScope);
  const seriesShareGuardScopeRevisionRef = useRef(0);
  if (!sameTeamCollaborationWriteScope(seriesShareGuardScopeRef.current, collaborationWriteScope)) {
    seriesShareGuardScopeRef.current = collaborationWriteScope;
    seriesShareGuardScopeRevisionRef.current += 1;
  }
  const [selectedSeries, setSelectedSeries] = useState<Series | null>(null);
  const [workbenchSeries, setWorkbenchSeries] = useState<Series | null>(null);
  const [selectedView, setSelectedView] = useState<WorkspaceView>("series");
  const [workbenchRoute, setWorkbenchRoute] = useState<SeriesWorkbenchRoute>(defaultSeriesWorkbenchPreferences.route);
  const [workbenchLeftView, setWorkbenchLeftView] = useState<SeriesWorkbenchLeftView>(defaultSeriesWorkbenchPreferences.leftView);
  const [workbenchLeftWidths, setWorkbenchLeftWidths] = useState<SeriesWorkbenchPreferences["leftWidths"]>(defaultSeriesWorkbenchPreferences.leftWidths);
  const workbenchCommittedLeftWidthsRef = useRef<SeriesWorkbenchPreferences["leftWidths"]>(defaultSeriesWorkbenchPreferences.leftWidths);
  const [workbenchPreferenceNotice, setWorkbenchPreferenceNotice] = useState<string | null>(null);
  const [continuousOriginalFrame, setContinuousOriginalFrame] = useState<OwnedContinuousOriginal | null>(null);
  const continuousOriginalOwnerRef = useRef<ContinuousOriginalOwner | null>(null);
  const continuousOriginalCallbackEpochRef = useRef(0);
  const [chatWorkbenchIntent, setChatWorkbenchIntent] = useState<WorkbenchSourceIntent | null>(null);
  const [mediaWorkbenchIntent, setMediaWorkbenchIntent] = useState<WorkbenchSourceIntent | null>(null);
  const [mediaGenerationVisited, setMediaGenerationVisited] = useState(false);
  const [chatAssistantVisited, setChatAssistantVisited] = useState(false);
  const [chatAssistantSeries, setChatAssistantSeries] = useState<Series | null>(null);
  const [teamDirectoryRevision, setTeamDirectoryRevision] = useState(0);
  const [seriesRevision, setSeriesRevision] = useState(0);
  const [teamGuardVersion, setTeamGuardVersion] = useState(0);
  const [seriesShareGuardVersion, setSeriesShareGuardVersion] = useState(0);
  const [chapterNavigationIntent, setChapterNavigationIntent] = useState<ChapterNavigationIntent | null>(null);
  const [assetNavigationIntent, setAssetNavigationIntent] = useState<AssetNavigationIntent | null>(null);
  const [fusedPageHosts, setFusedPageHosts] = useState<Map<string, FusedPageHost>>(() => new Map());
  const navigationIntentRef = useRef<ChapterNavigationIntent | null>(null);
  const assetNavigationIntentRef = useRef<AssetNavigationIntent | null>(null);
  assetNavigationIntentRef.current = assetNavigationIntent;
  const navigationEpoch = useRef(0);
  const workbenchNavigationEpoch = useRef(0);
  const workbenchSourceIntentRef = useRef<WorkbenchSourceIntent | null>(null);
  const dashboardScope = useRef({
    userId: state.status === "authenticated" ? state.user.id : null,
    services,
  });
  const dashboardScopeEpoch = useRef(0);
  const dashboardMounted = useRef(false);
  const creatorSettingsDirtyRef = useRef(false);
  const accountDirtyRef = useRef(false);
  const fusedAssetWriteInFlightRef = useRef(new Map<string, boolean>());
  const fusedOpenIntentEpochRef = useRef(0);
  const dashboardSessionGenerationRef = useRef(sessionGeneration);
  const teamGuardRef = useRef({ scopeKey: "", dirty: false, inFlight: false });
  const seriesShareGuardRef = useRef({ scopeKey: "", dirty: false, inFlight: false });
  const currentNavigationContext = useRef<{
    userId: string;
    services: typeof services;
    sessionGeneration: number;
    selectedSeries: Series | null;
    selectedView: WorkspaceView;
    scopeEpoch: number;
  } | null>(null);

  useLayoutEffect(() => {
    dashboardMounted.current = true;
    return () => {
      dashboardMounted.current = false;
    };
  }, []);

  useLayoutEffect(() => {
    collaborationWritePermit.synchronizeScope(collaborationWriteScope);
  }, [collaborationWritePermit, collaborationWriteScope]);

  useEffect(() => {
    const preventUnloadDuringWrite = (event: BeforeUnloadEvent) => {
      if (!collaborationWritePermit.hasUnresolvedWrite()) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnloadDuringWrite);
    return () => window.removeEventListener("beforeunload", preventUnloadDuringWrite);
  }, [collaborationWritePermit]);

  useEffect(() => {
    if (selectedView !== "teams" || state.status !== "authenticated") return;
    const scopeKey = `${state.user.id}:${sessionGeneration}:${dashboardScopeEpoch.current}`;
    if (teamGuardRef.current.scopeKey !== scopeKey || !teamGuardRef.current.dirty) return;
    const preventUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [selectedView, sessionGeneration, state, teamGuardVersion]);

  useLayoutEffect(() => {
    if (state.status !== "authenticated") return;
    const scopeKey = `${state.user.id}:${sessionGeneration}:${dashboardScopeEpoch.current}:series-share:${seriesShareGuardScopeRevisionRef.current}`;
    if (seriesShareGuardRef.current.scopeKey !== scopeKey || !seriesShareGuardRef.current.dirty) return;
    const preventUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventUnload);
    return () => window.removeEventListener("beforeunload", preventUnload);
  }, [services, services.teamManagement, services.teamSeries, sessionGeneration, state, seriesShareGuardVersion]);

  if (state.status !== "authenticated") {
    return null;
  }
  const currentUserId = state.user.id;

  let scopeChanged = false;
  if (dashboardScope.current.userId !== state.user.id || dashboardScope.current.services !== services
    || dashboardSessionGenerationRef.current !== sessionGeneration) {
    scopeChanged = true;
    dashboardScope.current = { userId: state.user.id, services };
    dashboardSessionGenerationRef.current = sessionGeneration;
    dashboardScopeEpoch.current += 1;
    seriesShareGuardRef.current = { scopeKey: "", dirty: false, inFlight: false };
    navigationEpoch.current += 1;
    navigationIntentRef.current = null;
    assetNavigationIntentRef.current = null;
    setChapterNavigationIntent(null);
    setAssetNavigationIntent(null);
    setSelectedSeries(null);
    setWorkbenchSeries(null);
    setSelectedView("series");
    setWorkbenchRoute(defaultSeriesWorkbenchPreferences.route);
    setWorkbenchLeftView(defaultSeriesWorkbenchPreferences.leftView);
    workbenchCommittedLeftWidthsRef.current = defaultSeriesWorkbenchPreferences.leftWidths;
    setWorkbenchLeftWidths(defaultSeriesWorkbenchPreferences.leftWidths);
    setWorkbenchPreferenceNotice(null);
    setContinuousOriginalFrame(null);
    workbenchNavigationEpoch.current += 1;
    workbenchSourceIntentRef.current = null;
    setChatWorkbenchIntent(null);
    setMediaWorkbenchIntent(null);
    setMediaGenerationVisited(false);
    setChatAssistantVisited(false);
    setChatAssistantSeries(null);
    setFusedPageHosts(new Map());
    fusedAssetWriteInFlightRef.current.clear();
    fusedOpenIntentEpochRef.current += 1;
  }

  const renderNavigationContext = {
    userId: state.user.id,
    services,
    sessionGeneration,
    selectedSeries: scopeChanged ? null : selectedSeries,
    selectedView: scopeChanged ? "series" : selectedView,
    scopeEpoch: dashboardScopeEpoch.current,
  };
  const teamGuardScopeKey = `${state.user.id}:${sessionGeneration}:${dashboardScopeEpoch.current}`;
  if (teamGuardRef.current.scopeKey !== teamGuardScopeKey) {
    teamGuardRef.current = { scopeKey: teamGuardScopeKey, dirty: false, inFlight: false };
  }
  const seriesShareGuardScopeKey = `${teamGuardScopeKey}:series-share:${seriesShareGuardScopeRevisionRef.current}`;
  if (seriesShareGuardRef.current.scopeKey !== seriesShareGuardScopeKey) {
    seriesShareGuardRef.current = { scopeKey: seriesShareGuardScopeKey, dirty: false, inFlight: false };
  }
  currentNavigationContext.current = renderNavigationContext;

  const continuousOriginalOwner: ContinuousOriginalOwner | null = scopeChanged || workbenchSeries === null
    ? null
    : {
      userId: currentUserId,
      apiBaseUrl: services.apiBaseUrl,
      services,
      sessionGeneration,
      scopeEpoch: dashboardScopeEpoch.current,
      seriesId: workbenchSeries.id,
    };
  if (!sameContinuousOriginalOwner(continuousOriginalOwnerRef.current, continuousOriginalOwner)) {
    continuousOriginalOwnerRef.current = continuousOriginalOwner;
    continuousOriginalCallbackEpochRef.current += 1;
  }
  const continuousOriginalCallbackEpoch = continuousOriginalCallbackEpochRef.current;
  const displayedContinuousOriginal = continuousOriginalFrame !== null
    && continuousOriginalFrame.callbackEpoch === continuousOriginalCallbackEpoch
    && sameContinuousOriginalOwner(continuousOriginalFrame.owner, continuousOriginalOwner)
    ? continuousOriginalFrame.selection
    : null;

  function updateContinuousOriginal(
    owner: ContinuousOriginalOwner | null,
    callbackEpoch: number,
    selection: ContinuousOriginalSelection | null,
  ) {
    if (owner === null) return;
    const isCurrentOwner = () => callbackEpoch === continuousOriginalCallbackEpochRef.current
      && sameContinuousOriginalOwner(owner, continuousOriginalOwnerRef.current);
    if (!isCurrentOwner()) return;

    setContinuousOriginalFrame((current) => {
      if (!isCurrentOwner()) return current;
      if (selection === null) {
        return current !== null && sameContinuousOriginalOwner(current.owner, owner) ? null : current;
      }
      return { owner, callbackEpoch, selection };
    });
  }

  function clearChapterNavigationIntent(expectedEpoch?: number) {
    const current = navigationIntentRef.current;
    if (current === null || (expectedEpoch !== undefined && current.navigationEpoch !== expectedEpoch)) {
      return;
    }
    navigationIntentRef.current = null;
    navigationEpoch.current += 1;
    setChapterNavigationIntent(null);
  }

  function clearAssetNavigationIntent(expectedEpoch?: number) {
    const current = assetNavigationIntentRef.current;
    if (current === null || (expectedEpoch !== undefined && current.navigationEpoch !== expectedEpoch)) {
      return;
    }
    assetNavigationIntentRef.current = null;
    navigationEpoch.current += 1;
    setAssetNavigationIntent(null);
  }

  function logoutFromWorkspace() {
    teamGuardRef.current = { scopeKey: teamGuardScopeKey, dirty: false, inFlight: false };
    seriesShareGuardRef.current = { scopeKey: seriesShareGuardScopeKey, dirty: false, inFlight: false };
    setTeamGuardVersion((version) => version + 1);
    setSeriesShareGuardVersion((version) => version + 1);
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setSelectedSeries(null);
    setSelectedView("series");
    setMediaGenerationVisited(false);
    setFusedPageHosts(new Map());
    fusedAssetWriteInFlightRef.current.clear();
    fusedOpenIntentEpochRef.current += 1;
    logout();
  }

  function confirmLeaveCurrentView(): boolean {
    const activeFusedHostKey = selectedSeries === null ? null : fusedPageHostKey(selectedSeries.id);
    if (selectedView === "fused-assets" && activeFusedHostKey !== null
      && fusedAssetWriteInFlightRef.current.get(activeFusedHostKey) === true) {
      if (!window.confirm("融合写入或目录核对仍在进行。离开后原请求和本地任务记录会保留，服务器结果仍可能需要核对。仍要离开吗？")) {
        return false;
      }
    }
    if (selectedView === "series"
      && seriesShareGuardRef.current.scopeKey === seriesShareGuardScopeKey
      && seriesShareGuardRef.current.dirty) {
      const message = seriesShareGuardRef.current.inFlight
        ? "团队共享写操作仍在请求中，离开不会撤销已发出的操作。页面会隐藏，回到我的剧集后仍可查看结果；仍要继续吗？"
        : "当前有未提交的团队共享选择或待核对操作。页面会隐藏并保留当前状态；仍要继续吗？";
      if (!window.confirm(message)) return false;
    }
    if (selectedView === "teams"
      && teamGuardRef.current.scopeKey === teamGuardScopeKey
      && teamGuardRef.current.dirty) {
      const message = teamGuardRef.current.inFlight
        ? "团队写操作仍在请求中，离开不能撤销已发出的操作。离开会丢弃本地草稿并停止当前页面核对，仍要继续吗？"
        : "当前有未保存的团队草稿、待核对操作或尚未关闭的邀请码回执；离开会丢弃页面状态，但不会撤销服务器可能已完成的操作。继续吗？";
      if (!window.confirm(message)) return false;
      teamGuardRef.current = { scopeKey: teamGuardScopeKey, dirty: false, inFlight: false };
      setTeamGuardVersion((version) => version + 1);
    }
    if (selectedView === "creator-settings" && creatorSettingsDirtyRef.current) {
      return window.confirm("当前有未保存的创作者设置草稿或待核对操作，离开将丢弃页面状态。继续吗？");
    }
    if (selectedView === "account" && accountDirtyRef.current) {
      if (!window.confirm("当前有未保存的账户简介或安全表单，或待核对操作；离开将丢弃页面状态。继续吗？")) {
        return false;
      }
    }
    if (selectedView === "account") accountDirtyRef.current = false;
    return true;
  }

  function updateTeamGuard(scopeKey: string, dirty: boolean, inFlight: boolean) {
    if (scopeKey !== teamGuardScopeKey || selectedView !== "teams") return;
    const current = teamGuardRef.current;
    if (current.dirty === dirty && current.inFlight === inFlight && current.scopeKey === scopeKey) return;
    teamGuardRef.current = { scopeKey, dirty, inFlight };
    setTeamGuardVersion((version) => version + 1);
  }

  function updateSeriesShareGuard(scopeKey: string, dirty: boolean, inFlight: boolean) {
    if (scopeKey !== seriesShareGuardScopeKey || seriesShareGuardRef.current.scopeKey !== scopeKey) return;
    const current = seriesShareGuardRef.current;
    if (current.dirty === dirty && current.inFlight === inFlight && current.scopeKey === scopeKey) return;
    seriesShareGuardRef.current = { scopeKey, dirty, inFlight };
    setSeriesShareGuardVersion((version) => version + 1);
  }

  function navigateToSeries() {
    if ((selectedView === "series" && selectedSeries === null) || !confirmLeaveCurrentView()) return;
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setSelectedSeries(null);
    setSelectedView("series");
  }

  function navigateToTeams() {
    if (selectedView === "teams" || !confirmLeaveCurrentView()) return;
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setSelectedSeries(null);
    setSelectedView("teams");
  }

  function navigateToCreatorSettings() {
    if (selectedView === "creator-settings" || !confirmLeaveCurrentView()) return;
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setSelectedSeries(null);
    setSelectedView("creator-settings");
  }

  function navigateToMediaGeneration() {
    if (selectedView === "media-generation" || !confirmLeaveCurrentView()) return;
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setSelectedSeries(null);
    setMediaGenerationVisited(true);
    setSelectedView("media-generation");
  }

  function navigateToChatAssistant() {
    if (selectedView === "chat-assistant" || selectedSeries === null || selectedSeries.can_enter !== true
      || !confirmLeaveCurrentView()) return;
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setChatAssistantSeries(selectedSeries);
    setChatAssistantVisited(true);
    setSelectedView("chat-assistant");
  }

  function persistWorkbenchPreferences(
    route: SeriesWorkbenchRoute,
    leftView: SeriesWorkbenchLeftView,
    leftWidths: SeriesWorkbenchPreferences["leftWidths"],
  ) {
    if (selectedSeries === null || services.apiBaseUrl === null) {
      setWorkbenchPreferenceNotice("当前账号、接口或剧集身份不完整；工作台仍可使用，但布局和工具选择不会记住。");
      return;
    }
    const saved = writeSeriesWorkbenchPreferences({
      userId: currentUserId,
      apiBaseUrl: services.apiBaseUrl,
      seriesId: selectedSeries.id,
    }, { route, leftView, leftWidths });
    setWorkbenchPreferenceNotice(saved
      ? null
      : "浏览器未能保存工作台布局；当前页面仍可继续使用。");
  }

  function workbenchLayoutOwnerKey(): string {
    return JSON.stringify([
      currentUserId,
      services.apiBaseUrl,
      sessionGeneration,
      dashboardScopeEpoch.current,
      workbenchSeries?.id ?? null,
    ]);
  }

  function fusedPageHostKey(seriesId: string): string {
    return JSON.stringify([
      currentUserId,
      services.apiBaseUrl,
      sessionGeneration,
      dashboardScopeEpoch.current,
      seriesId,
    ]);
  }

  function rememberFusedPageHost(series: Series, openIntent?: FusedGenerationOpenIntent): string {
    const key = fusedPageHostKey(series.id);
    setFusedPageHosts((current) => {
      const existing = current.get(key);
      const next = new Map(current);
      next.set(key, {
        series,
        openIntent: openIntent ?? existing?.openIntent ?? null,
      });
      return next;
    });
    return key;
  }

  function enterSeriesWorkbench(series: Series | null = selectedSeries) {
    if (series === null || series.can_enter !== true) return;
    const restored = services.apiBaseUrl === null
      ? { preferences: defaultSeriesWorkbenchPreferences, available: false }
      : readSeriesWorkbenchPreferences({
      userId: currentUserId,
        apiBaseUrl: services.apiBaseUrl,
        seriesId: series.id,
      });
    setWorkbenchRoute(restored.preferences.route);
    setWorkbenchLeftView(restored.preferences.leftView);
    workbenchCommittedLeftWidthsRef.current = restored.preferences.leftWidths;
    setWorkbenchLeftWidths(restored.preferences.leftWidths);
    setWorkbenchPreferenceNotice(restored.available
      ? null
      : "当前浏览器或接口身份无法读取工作台偏好；仍可手动切换工具和调整布局。");
    setSelectedSeries(series);
    setChatAssistantSeries(series);
    setWorkbenchSeries(series);
    setChatAssistantVisited(true);
    setMediaGenerationVisited(true);
    workbenchSourceIntentRef.current = null;
    setChatWorkbenchIntent(null);
    setMediaWorkbenchIntent(null);
    setSelectedView("series-workbench");
  }

  function changeWorkbenchRoute(route: SeriesWorkbenchRoute) {
    setWorkbenchRoute(route);
    persistWorkbenchPreferences(route, workbenchLeftView, workbenchCommittedLeftWidthsRef.current);
  }

  function changeWorkbenchLeftView(view: SeriesWorkbenchLeftView) {
    setWorkbenchLeftView(view);
    persistWorkbenchPreferences(workbenchRoute, view, workbenchCommittedLeftWidthsRef.current);
  }

  function changeWorkbenchWidth(
    leftView: SeriesWorkbenchLeftView,
    width: number,
    commit: boolean,
    ownerKey: string,
  ) {
    if (ownerKey !== workbenchLayoutOwnerKey()) return;
    if (commit && (selectedView !== "series-workbench" || workbenchLeftView !== leftView)) return;
    if (!commit) {
      setWorkbenchLeftWidths((current) => ({ ...current, [leftView]: width }));
      return;
    }
    const committedWidths = { ...workbenchCommittedLeftWidthsRef.current, [leftView]: width };
    workbenchCommittedLeftWidthsRef.current = committedWidths;
    setWorkbenchLeftWidths(committedWidths);
    persistWorkbenchPreferences(workbenchRoute, leftView, committedWidths);
  }

  function acceptWorkbenchSource(selection: WorkbenchSourceSelection) {
    const current = currentNavigationContext.current;
    if (
      !dashboardMounted.current
      || current === null
      || current !== renderNavigationContext
      || current.userId !== currentUserId
      || current.services !== services
      || current.scopeEpoch !== dashboardScopeEpoch.current
      || current.selectedView !== "series-workbench"
      || current.selectedSeries === null
      || current.selectedSeries.can_enter !== true
      || selection.seriesId !== current.selectedSeries.id
      || selection.chapterId.trim() === ""
      || selection.target.chapterId !== selection.chapterId
      || (selection.target.kind === "frame" && selection.target.storyboardAssetId.trim() === "")
      || (selection.target.kind === "asset" && selection.target.assetId.trim() === "")
      || !selection.isCurrent()
    ) return;

    if (selection.target.kind === "asset" && selection.target.category === "fused") {
      setSelectedView("fused-assets");
      return;
    }

    const navigationEpoch = ++workbenchNavigationEpoch.current;
    let intent: WorkbenchSourceIntent;
    intent = {
      ...selection,
      actorId: currentUserId,
      apiBaseUrl: services.apiBaseUrl,
      services,
      sessionGeneration,
      navigationEpoch,
      isCurrent: () => {
        const active = currentNavigationContext.current;
        return workbenchSourceIntentRef.current === intent
        && dashboardMounted.current
        && dashboardScope.current.userId === current.userId
        && dashboardScope.current.services === current.services
        && dashboardScopeEpoch.current === current.scopeEpoch
        && active?.userId === current.userId
        && active.services === current.services
        && active.scopeEpoch === current.scopeEpoch
        && active.sessionGeneration === intent.sessionGeneration
        && active.services.apiBaseUrl === intent.apiBaseUrl
        && intent.navigationEpoch === workbenchNavigationEpoch.current
        && selection.isCurrent();
      },
    };
    workbenchSourceIntentRef.current = intent;
    if (workbenchRoute === "chat") setChatWorkbenchIntent(intent);
    else setMediaWorkbenchIntent(intent);
  }

  function acceptFusedSourceEntry(selection: FusedSourceEntrySelection) {
    const current = currentNavigationContext.current;
    const sourceSeries = current?.selectedView === "series-workbench" ? workbenchSeries : current?.selectedSeries ?? null;
    if (
      !dashboardMounted.current
      || current === null
      || current !== renderNavigationContext
      || (current.selectedView !== "series-workbench" && current.selectedView !== "assets")
      || sourceSeries === null
      || sourceSeries.can_enter !== true
      || selection.actorId !== currentUserId
      || selection.services !== services
      || selection.apiBaseUrl !== services.apiBaseUrl
      || selection.sessionGeneration !== sessionGeneration
      || selection.seriesId !== sourceSeries.id
      || !Number.isSafeInteger(selection.sourceEpoch)
      || selection.sourceEpoch < 0
      || (selection.target === null && selection.memberAssetIds.length === 0)
      || selection.memberAssetIds.some((id) => id.trim() === "" || id !== id.trim())
      || new Set(selection.memberAssetIds).size !== selection.memberAssetIds.length
      || (selection.target !== null && (selection.target.chapterId.trim() === ""
        || selection.target.frameIndex < 0 || !Number.isSafeInteger(selection.target.frameIndex)))
      || !selection.isCurrent()
    ) return;

    const epoch = ++fusedOpenIntentEpochRef.current;
    const openIntent: FusedGenerationOpenIntent = {
      id: `workspace-fused-entry:${dashboardScopeEpoch.current}:${epoch}`,
      epoch,
      userId: currentUserId,
      apiBaseUrl: services.apiBaseUrl,
      seriesId: sourceSeries.id,
      sessionGeneration,
      memberType: selection.memberType,
      memberAssetIds: Object.freeze([...selection.memberAssetIds]),
      target: selection.target,
    };
    rememberFusedPageHost(sourceSeries, openIntent);
    setSelectedSeries(sourceSeries);
    setSelectedView("fused-assets");
  }

  function acceptAssetLibraryChapter(target: AssetFrameUsageChapterTarget) {
    acceptAssetChapterTarget(target);
  }

  function acceptAssetLibraryFrame(target: AssetFrameUsageFrameTarget) {
    acceptAssetFrameTarget(target);
  }

  function acceptAssetLibraryWorkbenchTarget(target: AssetFrameUsageWorkbenchTarget) {
    acceptWorkbenchSource({
      seriesId: target.seriesId,
      chapterId: target.chapterId,
      target: {
        kind: "asset",
        chapterId: target.chapterId,
        category: target.category,
        assetId: target.assetId,
      },
      isCurrent: target.isCurrent,
    });
  }

  function navigateToFusedAssets() {
    if (selectedView === "fused-assets" || selectedSeries === null || selectedSeries.can_enter !== true
      || !confirmLeaveCurrentView()) return;
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    rememberFusedPageHost(selectedSeries);
    setSelectedView("fused-assets");
  }

  function navigateToAccount() {
    if (selectedView === "account" || !confirmLeaveCurrentView()) return;
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setSelectedSeries(null);
    setSelectedView("account");
  }

  function leaveWorkspace() {
    if (!confirmLeaveCurrentView()) return;
    if (selectedView !== "series"
      && seriesShareGuardRef.current.scopeKey === seriesShareGuardScopeKey
      && seriesShareGuardRef.current.dirty) {
      const message = seriesShareGuardRef.current.inFlight
        ? "团队共享写操作仍在请求中。退出后无法撤销该请求；服务器结果仍可能需要核对。继续退出吗？"
        : "仍有团队共享选择或待核对状态未清理。退出会结束本地核对，服务器可能已完成的操作不会撤销。继续退出吗？";
      if (!window.confirm(message)) return;
    }
    logoutFromWorkspace();
  }

  function acceptAssetChapterTarget(target: AssetFrameUsageChapterTarget) {
    const current = currentNavigationContext.current;
    if (
      !dashboardMounted.current
      || current === null
      || current !== renderNavigationContext
      || current.userId !== renderNavigationContext.userId
      || current.services !== renderNavigationContext.services
      || current.selectedSeries !== renderNavigationContext.selectedSeries
      || current.selectedView !== renderNavigationContext.selectedView
      || current.scopeEpoch !== renderNavigationContext.scopeEpoch
      || dashboardScope.current.userId !== current.userId
      || dashboardScope.current.services !== current.services
      || dashboardScopeEpoch.current !== current.scopeEpoch
      || current.selectedSeries === null
      || (current.selectedView !== "assets" && current.selectedView !== "series-workbench")
      || current.selectedSeries.can_enter !== true
      || target.seriesId !== current.selectedSeries.id
      || target.chapterId.trim() === ""
      || !target.isCurrent()
      || navigationIntentRef.current !== null
    ) {
      return;
    }
    clearAssetNavigationIntent();
    const intent: ChapterNavigationIntent = {
      userId: current.userId,
      services: current.services,
      seriesId: current.selectedSeries.id,
      chapterId: target.chapterId,
      navigationEpoch: ++navigationEpoch.current,
    };
    navigationIntentRef.current = intent;
    setChapterNavigationIntent(intent);
    if (current.selectedView === "series-workbench") {
      changeWorkbenchLeftView("storyboard");
    } else {
      setSelectedView("chapters");
    }
  }

  function acceptFrameReferenceTarget(target: FrameAssetReferenceAssetTarget) {
    const current = currentNavigationContext.current;
    if (
      !dashboardMounted.current
      || current === null
      || current !== renderNavigationContext
      || current.userId !== renderNavigationContext.userId
      || current.services !== renderNavigationContext.services
      || current.selectedSeries !== renderNavigationContext.selectedSeries
      || current.selectedView !== renderNavigationContext.selectedView
      || current.scopeEpoch !== renderNavigationContext.scopeEpoch
      || dashboardScope.current.userId !== current.userId
      || dashboardScope.current.services !== current.services
      || dashboardScopeEpoch.current !== current.scopeEpoch
      || current.selectedSeries === null
      || (current.selectedView !== "chapters" && current.selectedView !== "series-workbench")
      || current.selectedSeries.can_enter !== true
      || target.seriesId !== current.selectedSeries.id
      || target.assetId.trim() === ""
      || !target.isCurrent()
      || assetNavigationIntentRef.current !== null
    ) {
      return;
    }

    clearChapterNavigationIntent();
    const intent: AssetNavigationIntent = {
      userId: current.userId,
      services: current.services,
      seriesId: current.selectedSeries.id,
      category: target.category,
      assetId: target.assetId,
      navigationEpoch: ++navigationEpoch.current,
    };
    assetNavigationIntentRef.current = intent;
    setAssetNavigationIntent(intent);
    setSelectedView("assets");
  }

  function acceptAssetFrameTarget(target: AssetFrameUsageFrameTarget) {
    const current = currentNavigationContext.current;
    if (
      !dashboardMounted.current
      || current === null
      || current !== renderNavigationContext
      || current.userId !== renderNavigationContext.userId
      || current.services !== renderNavigationContext.services
      || current.selectedSeries !== renderNavigationContext.selectedSeries
      || current.selectedView !== renderNavigationContext.selectedView
      || current.scopeEpoch !== renderNavigationContext.scopeEpoch
      || dashboardScope.current.userId !== current.userId
      || dashboardScope.current.services !== current.services
      || dashboardScopeEpoch.current !== current.scopeEpoch
      || current.selectedSeries === null
      || (current.selectedView !== "assets" && current.selectedView !== "series-workbench")
      || current.selectedSeries.can_enter !== true
      || target.seriesId !== current.selectedSeries.id
      || target.chapterId.trim() === ""
      || target.storyboardAssetId.trim() === ""
      || target.assetId.trim() === ""
      || !target.isCurrent()
      || navigationIntentRef.current !== null
    ) {
      return;
    }
    clearAssetNavigationIntent();
    const intent: ChapterNavigationIntent = {
      userId: current.userId,
      services: current.services,
      seriesId: current.selectedSeries.id,
      chapterId: target.chapterId,
      navigationEpoch: ++navigationEpoch.current,
      frameTarget: {
        storyboardAssetId: target.storyboardAssetId,
        category: target.category,
        assetId: target.assetId,
      },
    };
    navigationIntentRef.current = intent;
    setChapterNavigationIntent(intent);
    if (current.selectedView === "series-workbench") {
      changeWorkbenchLeftView("storyboard");
    } else {
      setSelectedView("chapters");
    }
  }

  function acceptChatAssistantSource(target: ChatAssistantSourceIntent) {
    const current = currentNavigationContext.current;
    const chatSurfaceActive = current?.selectedView === "chat-assistant"
      || (current?.selectedView === "series-workbench" && workbenchRoute === "chat");
    if (
      !dashboardMounted.current
      || current === null
      || current !== renderNavigationContext
      || current.userId !== renderNavigationContext.userId
      || current.services !== renderNavigationContext.services
      || current.selectedSeries !== renderNavigationContext.selectedSeries
      || !chatSurfaceActive
      || current.selectedView !== renderNavigationContext.selectedView
      || current.scopeEpoch !== renderNavigationContext.scopeEpoch
      || dashboardScope.current.userId !== current.userId
      || dashboardScope.current.services !== current.services
      || dashboardScopeEpoch.current !== current.scopeEpoch
      || current.selectedSeries === null
      || current.selectedSeries.can_enter !== true
      || target.seriesId !== current.selectedSeries.id
      || !target.isCurrent()
    ) return;

    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    if (target.kind === "chapter") {
      const intent: ChapterNavigationIntent = {
        userId: current.userId,
        services: current.services,
        seriesId: current.selectedSeries.id,
        chapterId: target.chapterId,
        navigationEpoch: ++navigationEpoch.current,
      };
      navigationIntentRef.current = intent;
      setChapterNavigationIntent(intent);
      setSelectedView("chapters");
      return;
    }

    const intent: AssetNavigationIntent = {
      userId: current.userId,
      services: current.services,
      seriesId: current.selectedSeries.id,
      category: target.category,
      assetId: target.assetId,
      navigationEpoch: ++navigationEpoch.current,
    };
    assetNavigationIntentRef.current = intent;
    setAssetNavigationIntent(intent);
    setSelectedView("assets");
  }

  const creatorSettingsService = (services as typeof services & {
    creatorSettings?: CreatorSettingsServicePort;
  }).creatorSettings;
  const providerQuotaService = (services as typeof services & {
    providerQuota?: ProviderQuotaServicePort;
  }).providerQuota;
  const zhangyugePatService = (services as typeof services & {
    zhangyugePat?: ZhangyugePatServicePort;
  }).zhangyugePat;
  const workbenchSurfaceMode: SeriesWorkbenchSurfaceMode = selectedView === "series-workbench"
    ? "workbench"
    : selectedView === "chapters"
      ? "chapters"
      : selectedView === "assets"
        ? "assets"
      : selectedView === "chat-assistant"
        ? "chat"
        : selectedView === "media-generation"
          ? "media"
          : "hidden";

  return (
    <div className="workspace-shell">
      <aside className="workspace-sidebar">
        <div className="workspace-brand" aria-label="Hao AI 工作台">
          <span className="brand-mark" aria-hidden="true">H</span>
          <span><strong>Hao AI</strong><small>创作工作台</small></span>
        </div>
        <nav aria-label="主要导航">
          <p className="nav-section-label">工作空间</p>
          <button
            className={selectedView === "series" ? "nav-item active" : "nav-item"}
            aria-current={selectedView === "series" ? "page" : undefined}
            onClick={navigateToSeries}
            type="button"
          >
            <span className="nav-icon" aria-hidden="true">▤</span>
            <span>我的剧集</span>
          </button>
          <button
            aria-current={selectedView === "teams" ? "page" : undefined}
            className={selectedView === "teams" ? "nav-item active" : "nav-item"}
            onClick={navigateToTeams}
            type="button"
          >
            <span className="nav-icon" aria-hidden="true">♧</span>
            <span>我的团队</span>
          </button>
          <button
            aria-current={selectedView === "tasks" ? "page" : undefined}
            className={selectedView === "tasks" ? "nav-item active" : "nav-item"}
            onClick={() => {
              if (selectedView !== "tasks") {
                if (!confirmLeaveCurrentView()) return;
                clearChapterNavigationIntent();
                clearAssetNavigationIntent();
                setSelectedSeries(null);
                setSelectedView("tasks");
              }
            }}
            type="button"
          >
            <span className="nav-icon" aria-hidden="true">☷</span>
            <span>我的任务</span>
          </button>
          <button
            aria-current={selectedView === "media-generation" ? "page" : undefined}
            className={selectedView === "media-generation" ? "nav-item active" : "nav-item"}
            onClick={navigateToMediaGeneration}
            type="button"
          >
            <span className="nav-icon" aria-hidden="true">✦</span>
            <span>AI 生成</span>
          </button>
          {selectedSeries !== null && (
            <button
              aria-current={selectedView === "chat-assistant" ? "page" : undefined}
              className={selectedView === "chat-assistant" ? "nav-item active" : "nav-item"}
              disabled={selectedSeries.can_enter !== true}
              onClick={navigateToChatAssistant}
              type="button"
            >
              <span className="nav-icon" aria-hidden="true">✎</span>
              <span>AI 助手</span>
            </button>
          )}
          {selectedSeries !== null && (
            <button
              aria-current={selectedView === "fused-assets" ? "page" : undefined}
              className={selectedView === "fused-assets" ? "nav-item active" : "nav-item"}
              disabled={selectedSeries.can_enter !== true}
              onClick={navigateToFusedAssets}
              type="button"
            >
              <span className="nav-icon" aria-hidden="true">✧</span>
              <span>融合素材</span>
            </button>
          )}
          <button
            aria-current={selectedView === "creator-settings" ? "page" : undefined}
            className={selectedView === "creator-settings" ? "nav-item active" : "nav-item"}
            onClick={navigateToCreatorSettings}
            type="button"
          >
            <span className="nav-icon" aria-hidden="true">⚙</span>
            <span>创作者设置</span>
          </button>
          <button
            aria-current={selectedView === "account" ? "page" : undefined}
            className={selectedView === "account" ? "nav-item active" : "nav-item"}
            onClick={navigateToAccount}
            type="button"
          >
            <span className="nav-icon" aria-hidden="true">◎</span>
            <span>我的账户</span>
          </button>
          <div className="nav-item future" aria-label="章节制作，后续开放">
            <span className="nav-icon" aria-hidden="true">▧</span>
            <span>章节制作</span>
            <span className="nav-status">后续开放</span>
          </div>
          <div className="nav-item future" aria-label="素材库，后续开放">
            <span className="nav-icon" aria-hidden="true">◇</span>
            <span>素材库</span>
            <span className="nav-status">后续开放</span>
          </div>
          <p className="sidebar-note">在创作者设置中管理个人模型与提示词。</p>
        </nav>
        <div className="workspace-appearance">
          <AppearanceControl />
          <PublicSiteTutorialLink className="sidebar-tutorial-link" />
        </div>
        <div className="sidebar-bottom">
          <span className="sidebar-avatar" aria-hidden="true">
            {state.user.username.slice(0, 1)}
          </span>
          <span className="sidebar-user">
            <strong>{state.user.username}</strong>
            <small>{services.mode === "demo" ? "本机演示账号" : "本地服务账号"}</small>
          </span>
          <button
            aria-label="退出登录"
            className="icon-button logout-icon"
            onClick={leaveWorkspace}
            title="退出登录"
            type="button"
          >
            <span aria-hidden="true">↪</span>
          </button>
        </div>
      </aside>

      <div className="workspace-main">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作空间</span>
            <span aria-hidden="true">/</span>
            <strong>{selectedView === "creator-settings"
              ? "创作者设置"
              : selectedView === "media-generation"
                ? "AI 生成"
              : selectedView === "chat-assistant"
                ? "AI 助手"
              : selectedView === "series-workbench"
                ? "剧集工作台"
              : selectedView === "fused-assets"
                ? "融合素材"
              : selectedView === "account"
                ? "我的账户"
                : selectedView === "tasks"
                  ? "我的任务"
                  : selectedView === "teams"
                    ? "团队管理"
                  : selectedSeries === null
                    ? "我的剧集"
                    : selectedSeries.name}</strong>
          </div>
          <div className="topbar-actions">
            {selectedView !== "tasks" && (
              <button
                className="mobile-task-entry"
                onClick={() => {
                  if (!confirmLeaveCurrentView()) return;
                  clearChapterNavigationIntent();
                  clearAssetNavigationIntent();
                  setSelectedSeries(null);
                  setSelectedView("tasks");
                }}
                type="button"
              >
                我的任务
              </button>
            )}
            {selectedView !== "media-generation" && (
              <button className="mobile-task-entry" onClick={navigateToMediaGeneration} type="button">
                AI 生成
              </button>
            )}
            {workbenchSeries !== null && workbenchSeries.can_enter === true
              && ["chapters", "assets", "chat-assistant", "media-generation", "fused-assets"].includes(selectedView) && (
              <button className="workbench-entry-button" onClick={() => {
                if (!confirmLeaveCurrentView()) return;
                enterSeriesWorkbench(workbenchSeries);
              }} type="button">
                {selectedView === "chapters" || selectedView === "assets" ? "剧集工作台" : "返回剧集工作台"}
              </button>
            )}
            {selectedSeries !== null && selectedView !== "chat-assistant" && (
              <button className="mobile-task-entry" disabled={selectedSeries.can_enter !== true}
                onClick={navigateToChatAssistant} type="button">AI 助手</button>
            )}
            {selectedSeries !== null && selectedSeries.can_enter === true && selectedView !== "fused-assets" && (
              <button className="mobile-task-entry" onClick={navigateToFusedAssets} type="button">
                融合素材
              </button>
            )}
            {selectedView !== "teams" && (
              <button className="mobile-task-entry teams-mobile-entry" onClick={navigateToTeams} type="button">
                我的团队
              </button>
            )}
            {selectedView !== "creator-settings" && (
              <button
                aria-label="创作者设置"
                className="mobile-task-entry creator-settings-mobile-entry"
                onClick={navigateToCreatorSettings}
                type="button"
              >
                创作者设置
              </button>
            )}
            {selectedView !== "account" && (
              <button
                aria-label="我的账户"
                className="mobile-task-entry account-mobile-entry"
                onClick={navigateToAccount}
                type="button"
              >
                我的账户
              </button>
            )}
            <span className={services.mode === "demo" ? "mode-chip demo-chip" : "mode-chip api-chip"}>
              <span className="status-dot" aria-hidden="true" />
              {services.mode === "demo" ? "演示模式" : "隔离 API"}
            </span>
            <button className="text-button" onClick={leaveWorkspace} type="button">退出</button>
          </div>
        </header>
        <main className="workspace-content">
          <div
            className="series-list-view"
            hidden={selectedSeries !== null || selectedView !== "series"}
            inert={selectedSeries !== null || selectedView !== "series"}
            aria-hidden={selectedSeries !== null || selectedView !== "series"}
          >
            <SeriesPage
              onUnauthorized={logoutFromWorkspace}
              sessionGeneration={sessionGeneration}
              onViewChapters={(series) => {
                clearChapterNavigationIntent();
                clearAssetNavigationIntent();
                setSelectedSeries(series);
                setWorkbenchSeries(series);
                setSelectedView("chapters");
              }}
              onViewAssets={(series) => {
                clearChapterNavigationIntent();
                clearAssetNavigationIntent();
                setSelectedSeries(series);
                setWorkbenchSeries(series);
                setSelectedView("assets");
              }}
              active={selectedSeries === null && selectedView === "series"}
              collaborationWritePermit={collaborationWritePermit}
              collaborationWriteScope={collaborationWriteScope}
              teamDirectoryRevision={teamDirectoryRevision}
              seriesRevision={seriesRevision}
              shareGuardScopeKey={seriesShareGuardScopeKey}
              onShareGuardChange={updateSeriesShareGuard}
              onSeriesChanged={() => setSeriesRevision((revision) => revision + 1)}
              services={services}
              userId={state.user.id}
              isSuperuser={state.user.is_superuser}
            />
          </div>
          <SeriesWorkbenchSurface
            ownerKey={workbenchLayoutOwnerKey()}
            chat={chatAssistantVisited && chatAssistantSeries !== null ? (
              <ChatAssistantPage
                key={`${state.user.id}:${dashboardScopeEpoch.current}:${sessionGeneration}:${chatAssistantSeries.id}`}
                services={services}
                series={chatAssistantSeries}
                userId={state.user.id}
                sessionGeneration={sessionGeneration}
                onUnauthorized={logoutFromWorkspace}
                onBack={() => setSelectedView("chapters")}
                onNavigateSource={acceptChatAssistantSource}
                workbenchSourceIntent={chatWorkbenchIntent}
              />
            ) : null}
            storyboard={workbenchSeries !== null ? (
              <ChapterBrowser
                key={state.user.id + ":" + dashboardScopeEpoch.current + ":" + sessionGeneration + ":" + workbenchSeries.id}
                onBack={() => {
                  clearChapterNavigationIntent();
                  clearAssetNavigationIntent();
                  setSelectedSeries(null);
                  setWorkbenchSeries(null);
                  setSelectedView("series");
                }}
                onUnauthorized={logoutFromWorkspace}
                sessionGeneration={sessionGeneration}
                navigationIntent={chapterNavigationIntent}
                onNavigationAbandoned={(epoch) => clearChapterNavigationIntent(epoch)}
                onNavigateToAsset={acceptFrameReferenceTarget}
                onNavigationConsumed={(epoch) => clearChapterNavigationIntent(epoch)}
                onWorkbenchSource={acceptWorkbenchSource}
                onFusedSource={acceptFusedSourceEntry}
                workbenchActive={selectedView === "series-workbench"
                  && (workbenchLeftView === "storyboard" || workbenchLeftView === "continuous-preview")}
                continuousPreviewActive={selectedView === "series-workbench" && workbenchLeftView === "continuous-preview"}
                onContinuousOriginalChange={(selection) => updateContinuousOriginal(
                  continuousOriginalOwner,
                  continuousOriginalCallbackEpoch,
                  selection,
                )}
                series={workbenchSeries}
                services={services}
                userId={state.user.id}
              />
            ) : null}
            continuousOriginal={displayedContinuousOriginal === null ? (
              <p role="status">选择镜头后显示其打开时的原文字段；不会以草稿替代原文。</p>
            ) : displayedContinuousOriginal.status === "text" ? (
              <div className="series-workbench-original-text">
                <span>镜头 {displayedContinuousOriginal.position}</span>
                <p>{displayedContinuousOriginal.text}</p>
              </div>
            ) : (
              <p role="status">
                {displayedContinuousOriginal.status === "missing" ? "该镜头没有原文字段。" : "原文字段无法识别。"}
              </p>
            )}
            assets={workbenchSeries !== null ? (
              <AssetLibrary
                key={state.user.id + ":" + dashboardScopeEpoch.current + ":" + sessionGeneration + ":" + workbenchSeries.id}
                onBack={() => {
                  if (selectedView === "series-workbench") {
                    setWorkbenchLeftView("storyboard");
                    return;
                  }
                  clearChapterNavigationIntent();
                  clearAssetNavigationIntent();
                  setSelectedSeries(null);
                  setWorkbenchSeries(null);
                  setSelectedView("series");
                }}
                onUnauthorized={logoutFromWorkspace}
                onNavigationAbandoned={(epoch) => clearAssetNavigationIntent(epoch)}
                onNavigationConsumed={(epoch) => clearAssetNavigationIntent(epoch)}
                navigationIntent={assetNavigationIntent}
                onViewChapter={acceptAssetLibraryChapter}
                onViewFrame={acceptAssetLibraryFrame}
                {...(selectedView === "series-workbench"
                  ? { onUseAsWorkbenchTarget: acceptAssetLibraryWorkbenchTarget }
                  : {})}
                onFusedSource={acceptFusedSourceEntry}
                sessionGeneration={sessionGeneration}
                active={selectedView === "assets"
                  || (selectedView === "series-workbench" && workbenchLeftView === "assets")}
                generationHistoryPort={services.assetGenerationHistory}
                series={workbenchSeries}
                services={services}
                userId={state.user.id}
              />
            ) : null}
            leftView={workbenchLeftView}
            leftWidth={workbenchLeftWidths[workbenchLeftView]}
            media={mediaGenerationVisited ? (
              <MediaGenerationPage
                key={`${state.user.id}:${dashboardScopeEpoch.current}:${sessionGeneration}`}
                services={services}
                userId={state.user.id}
                sessionGeneration={sessionGeneration}
                onUnauthorized={logoutFromWorkspace}
                onOpenTasks={() => setSelectedView("tasks")}
                workbenchSourceIntent={mediaWorkbenchIntent}
              />
            ) : null}
            mode={workbenchSurfaceMode}
            onLeftViewChange={changeWorkbenchLeftView}
            onRouteChange={changeWorkbenchRoute}
            onWidthChange={(leftView, width, ownerKey) => changeWorkbenchWidth(leftView, width, false, ownerKey)}
            onWidthCommit={(leftView, width, ownerKey) => changeWorkbenchWidth(leftView, width, true, ownerKey)}
            preferenceNotice={workbenchPreferenceNotice}
            route={workbenchRoute}
          />
          {!scopeChanged && Array.from(fusedPageHosts.entries()).map(([hostKey, host]) => {
            const active = selectedView === "fused-assets"
              && selectedSeries?.id === host.series.id
              && selectedSeries.can_enter === true;
            return (
              <div
                aria-hidden={!active}
                className="fused-assets-workspace-host"
                hidden={!active}
                inert={!active}
                key={hostKey}
              >
                <FusedAssetsPage
                  key={hostKey}
                  openIntent={active ? host.openIntent : null}
                  onIntentConsumed={(intentId) => {
                    setFusedPageHosts((current) => {
                      const existing = current.get(hostKey);
                      if (existing === undefined || existing.openIntent?.id !== intentId) return current;
                      const next = new Map(current);
                      next.set(hostKey, { ...existing, openIntent: null });
                      return next;
                    });
                  }}
                  services={services}
                  series={host.series}
                  userId={state.user.id}
                  sessionGeneration={sessionGeneration}
                  onBack={navigateToSeries}
                  onUnauthorized={logoutFromWorkspace}
                  onActivityChange={(isActive) => { fusedAssetWriteInFlightRef.current.set(hostKey, isActive); }}
                />
              </div>
            );
          })}
          {selectedView === "tasks" && (
            <MyTasksPage
              key={state.user.id}
              onBack={() => setSelectedView("series")}
              onUnauthorized={logoutFromWorkspace}
              services={services}
              userId={state.user.id}
            />
          )}
          {selectedView === "teams" && (
            <TeamsPage
              key={`${state.user.id}:${dashboardScopeEpoch.current}:${sessionGeneration}`}
              services={services}
              collaborationWritePermit={collaborationWritePermit}
              collaborationWriteScope={collaborationWriteScope}
              seriesRevision={seriesRevision}
              userId={state.user.id}
              sessionGeneration={sessionGeneration}
              guardScopeKey={teamGuardScopeKey}
              onUnauthorized={logoutFromWorkspace}
              onBack={navigateToSeries}
              onGuardChange={updateTeamGuard}
              onDirectoryChanged={() => setTeamDirectoryRevision((revision) => revision + 1)}
              onSeriesChanged={() => setSeriesRevision((revision) => revision + 1)}
              onOpenSeries={(series, destination) => {
                if (selectedView !== "teams" || !confirmLeaveCurrentView()) return;
                clearChapterNavigationIntent();
                clearAssetNavigationIntent();
                setSelectedSeries(series);
                setWorkbenchSeries(series);
                setChatAssistantSeries(series);
                workbenchSourceIntentRef.current = null;
                setChatWorkbenchIntent(null);
                setMediaWorkbenchIntent(null);
                setSelectedView(destination);
              }}
            />
          )}
          {selectedView === "creator-settings" && (
            <CreatorSettingsPage
              key={`${state.user.id}:${dashboardScopeEpoch.current}:${sessionGeneration}`}
              {...(creatorSettingsService === undefined ? {} : { service: creatorSettingsService })}
              {...(providerQuotaService === undefined ? {} : { providerQuotaService })}
              {...(zhangyugePatService === undefined ? {} : { zhangyugePatService })}
              isSuperuser={state.user.is_superuser}
              userId={state.user.id}
              onUnauthorized={logoutFromWorkspace}
              onBack={() => {
                setSelectedSeries(null);
                setSelectedView("series");
              }}
              onDirtyChange={(dirty) => { creatorSettingsDirtyRef.current = dirty; }}
            />
          )}
          {selectedView === "account" && (
            <AccountPage
              key={`${state.user.id}:${dashboardScopeEpoch.current}:${sessionGeneration}`}
              user={state.user}
              services={services}
              profile={services.accountProfile ?? null}
              refreshCurrentUser={refreshCurrentUser}
              sessionGeneration={sessionGeneration}
              onUnauthorized={logoutFromWorkspace}
              onLogout={leaveWorkspace}
              onBack={() => {
                if (!confirmLeaveCurrentView()) return;
                setSelectedSeries(null);
                setSelectedView("series");
              }}
              onDirtyChange={(dirty) => { accountDirtyRef.current = dirty; }}
            />
          )}
        </main>
        <footer className="workspace-footer">
          <span>Hao AI · 新版工作台</span>
          <span>
            {selectedView === "creator-settings"
              ? "个人模型与提示词"
              : selectedView === "media-generation"
                ? "视频与批量图片任务"
              : selectedView === "chat-assistant"
                ? "剧集文字会话"
              : selectedView === "fused-assets"
                ? "融合素材管理"
              : selectedView === "account"
                ? "本人资料、登录设备与密码"
              : selectedView === "tasks"
              ? "任务记录只读预览"
              : selectedView === "teams"
                ? "团队、成员与邀请码管理"
              : selectedSeries === null
                ? "剧集管理"
                : selectedView === "assets"
                  ? "素材只读预览"
                  : "章节与分镜只读预览"}
          </span>
        </footer>
      </div>
    </div>
  );
}

export function Workspace() {
  const { services, state, login, logout, retryRestore } = useAuth();

  if (state.status === "restoring") {
    return (
      <main className="auth-loading-screen" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span>正在验证会话…</span>
      </main>
    );
  }

  if (state.status === "restore-error") {
    return (
      <main className="restore-error-screen">
        <section className="restore-error-card" role="alert">
          <p className="eyebrow">会话验证</p>
          <h1>暂时无法确认登录状态</h1>
          <p>{restoreErrorMessage(state.error)}</p>
          <div className="button-row">
            <button className="primary-button" onClick={retryRestore} type="button">
              重试验证
            </button>
            <button className="secondary-button" onClick={logout} type="button">
              退出此设备
            </button>
          </div>
        </section>
      </main>
    );
  }

  if (state.status === "anonymous") {
    return (
      <LoginPage
        accountAccess={services.accountAccess}
        error={state.error}
        mode={services.mode}
        onSubmit={login}
      />
    );
  }

  return <Dashboard key={state.user.id} />;
}
