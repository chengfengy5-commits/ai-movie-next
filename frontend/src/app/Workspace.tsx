import { useLayoutEffect, useRef, useState } from "react";
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
  const { services, state, logout } = useAuth();
  const [selectedSeries, setSelectedSeries] = useState<Series | null>(null);
  const [selectedView, setSelectedView] = useState<"series" | "chapters" | "assets" | "tasks">("series");
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
  const currentNavigationContext = useRef<{
    userId: string;
    services: typeof services;
    selectedSeries: Series | null;
    selectedView: "series" | "chapters" | "assets" | "tasks";
    scopeEpoch: number;
  } | null>(null);

  useLayoutEffect(() => {
    dashboardMounted.current = true;
    return () => {
      dashboardMounted.current = false;
    };
  }, []);

  if (state.status !== "authenticated") {
    return null;
  }

  let scopeChanged = false;
  if (dashboardScope.current.userId !== state.user.id || dashboardScope.current.services !== services) {
    scopeChanged = true;
    dashboardScope.current = { userId: state.user.id, services };
    dashboardScopeEpoch.current += 1;
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
    clearChapterNavigationIntent();
    clearAssetNavigationIntent();
    setSelectedSeries(null);
    setSelectedView("series");
    logout();
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

  return (
    <div className="workspace-shell">
      <aside className="workspace-sidebar">
        <div className="workspace-brand" aria-label="Hao AI 工作台">
          <span className="brand-mark" aria-hidden="true">H</span>
          <span><strong>Hao AI</strong><small>创作工作台</small></span>
        </div>
        <nav aria-label="主要导航">
          <p className="nav-section-label">工作空间</p>
          <div className={selectedView === "series" ? "nav-item active" : "nav-item"} aria-current={selectedView === "series" ? "page" : undefined}>
            <span className="nav-icon" aria-hidden="true">▤</span>
            <span>我的剧集</span>
          </div>
          <button
            aria-current={selectedView === "tasks" ? "page" : undefined}
            className={selectedView === "tasks" ? "nav-item active" : "nav-item"}
            onClick={() => {
              if (selectedView !== "tasks") {
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
          <p className="sidebar-note">剧集、章节、素材与任务记录仅供只读查看。</p>
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
            onClick={logoutFromWorkspace}
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
            <strong>{selectedView === "tasks" ? "我的任务" : selectedSeries === null ? "我的剧集" : selectedSeries.name}</strong>
          </div>
          <div className="topbar-actions">
            {selectedView !== "tasks" && (
              <button
                className="mobile-task-entry"
                onClick={() => {
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
            <span className={services.mode === "demo" ? "mode-chip demo-chip" : "mode-chip api-chip"}>
              <span className="status-dot" aria-hidden="true" />
              {services.mode === "demo" ? "演示模式" : "隔离 API"}
            </span>
            <button className="text-button" onClick={logoutFromWorkspace} type="button">退出</button>
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
              services={services}
              userId={state.user.id}
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
        </main>
        <footer className="workspace-footer">
          <span>Hao AI · 新版工作台</span>
          <span>
            {selectedView === "tasks"
              ? "任务记录只读预览"
              : selectedSeries === null
                ? "剧集只读预览"
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
        error={state.error}
        mode={services.mode}
        onSubmit={login}
      />
    );
  }

  return <Dashboard key={state.user.id} />;
}
