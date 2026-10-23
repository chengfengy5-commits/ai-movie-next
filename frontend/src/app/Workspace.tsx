import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../shared/api/errors";
import type { Series } from "../shared/api/contracts";
import { useAuth } from "../features/auth/AuthProvider";
import { LoginPage } from "../features/auth/LoginPage";
import { ChapterBrowser, type ChapterNavigationIntent } from "../features/chapters/ChapterBrowser";
import { AssetLibrary, type AssetNavigationIntent } from "../features/assets/AssetLibrary";
import type {
  AssetFrameUsageChapterTarget,
  AssetFrameUsageFrameTarget,
} from "../features/assets/AssetFrameUsagePanel";
import type { FrameAssetReferenceAssetTarget } from "../features/chapters/FrameAssetReferencesPanel";
import { SeriesPage } from "../features/series/SeriesPage";
import { MyTasksPage } from "../features/tasks/MyTasksPage";
import { CreatorSettingsPage } from "../features/creator-settings/CreatorSettingsPage";
import { AccountPage } from "../features/account/AccountPage";
import { TeamsPage } from "../features/teams/TeamsPage";
import type { CreatorSettingsServicePort } from "../shared/api/creatorSettings";
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
  const [selectedView, setSelectedView] = useState<"series" | "chapters" | "assets" | "tasks" | "teams" | "creator-settings" | "account">("series");
  const [teamDirectoryRevision, setTeamDirectoryRevision] = useState(0);
  const [seriesRevision, setSeriesRevision] = useState(0);
  const [teamGuardVersion, setTeamGuardVersion] = useState(0);
  const [seriesShareGuardVersion, setSeriesShareGuardVersion] = useState(0);
  const [chapterNavigationIntent, setChapterNavigationIntent] = useState<ChapterNavigationIntent | null>(null);
  const [assetNavigationIntent, setAssetNavigationIntent] = useState<AssetNavigationIntent | null>(null);
  const navigationIntentRef = useRef<ChapterNavigationIntent | null>(null);
  const assetNavigationIntentRef = useRef<AssetNavigationIntent | null>(null);
  assetNavigationIntentRef.current = assetNavigationIntent;
  const navigationEpoch = useRef(0);
  const dashboardScope = useRef({
    userId: state.status === "authenticated" ? state.user.id : null,
    services,
  });
  const dashboardScopeEpoch = useRef(0);
  const dashboardMounted = useRef(false);
  const creatorSettingsDirtyRef = useRef(false);
  const accountDirtyRef = useRef(false);
  const teamGuardRef = useRef({ scopeKey: "", dirty: false, inFlight: false });
  const seriesShareGuardRef = useRef({ scopeKey: "", dirty: false, inFlight: false });
  const currentNavigationContext = useRef<{
    userId: string;
    services: typeof services;
    selectedSeries: Series | null;
    selectedView: "series" | "chapters" | "assets" | "tasks" | "teams" | "creator-settings" | "account";
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

  let scopeChanged = false;
  if (dashboardScope.current.userId !== state.user.id || dashboardScope.current.services !== services) {
    scopeChanged = true;
    dashboardScope.current = { userId: state.user.id, services };
    dashboardScopeEpoch.current += 1;
    seriesShareGuardRef.current = { scopeKey: "", dirty: false, inFlight: false };
    navigationEpoch.current += 1;
    navigationIntentRef.current = null;
    assetNavigationIntentRef.current = null;
    setChapterNavigationIntent(null);
    setAssetNavigationIntent(null);
    setSelectedSeries(null);
    setSelectedView("series");
  }

  const renderNavigationContext = {
    userId: state.user.id,
    services,
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
    logout();
  }

  function confirmLeaveCurrentView(): boolean {
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
      || current.selectedView !== "assets"
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
    setSelectedView("chapters");
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
      || current.selectedView !== "chapters"
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
      || current.selectedView !== "assets"
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
    setSelectedView("chapters");
  }

  const creatorSettingsService = (services as typeof services & {
    creatorSettings?: CreatorSettingsServicePort;
  }).creatorSettings;

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
                setSelectedView("chapters");
              }}
              onViewAssets={(series) => {
                clearChapterNavigationIntent();
                clearAssetNavigationIntent();
                setSelectedSeries(series);
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
          {selectedSeries !== null && selectedView === "chapters" && (
            <ChapterBrowser
              key={state.user.id + ":" + dashboardScopeEpoch.current + ":" + selectedSeries.id}
              onBack={() => {
                clearChapterNavigationIntent();
                clearAssetNavigationIntent();
                setSelectedSeries(null);
                setSelectedView("series");
              }}
              onUnauthorized={logoutFromWorkspace}
              sessionGeneration={sessionGeneration}
              navigationIntent={chapterNavigationIntent}
              onNavigationAbandoned={(epoch) => clearChapterNavigationIntent(epoch)}
              onNavigateToAsset={acceptFrameReferenceTarget}
              onNavigationConsumed={(epoch) => clearChapterNavigationIntent(epoch)}
              series={selectedSeries}
              services={services}
              userId={state.user.id}
            />
          )}
          {selectedSeries !== null && selectedView === "assets" && (
            <AssetLibrary
              key={state.user.id + ":" + dashboardScopeEpoch.current + ":" + selectedSeries.id}
              onBack={() => {
                clearChapterNavigationIntent();
                clearAssetNavigationIntent();
                setSelectedSeries(null);
                setSelectedView("series");
              }}
              onUnauthorized={logoutFromWorkspace}
              onNavigationAbandoned={(epoch) => clearAssetNavigationIntent(epoch)}
              onNavigationConsumed={(epoch) => clearAssetNavigationIntent(epoch)}
              navigationIntent={assetNavigationIntent}
              onViewChapter={acceptAssetChapterTarget}
              onViewFrame={acceptAssetFrameTarget}
              series={selectedSeries}
              services={services}
              userId={state.user.id}
            />
          )}
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
                setSelectedView(destination);
              }}
            />
          )}
          {selectedView === "creator-settings" && (
            <CreatorSettingsPage
              key={state.user.id + ":" + dashboardScopeEpoch.current}
              {...(creatorSettingsService === undefined ? {} : { service: creatorSettingsService })}
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
