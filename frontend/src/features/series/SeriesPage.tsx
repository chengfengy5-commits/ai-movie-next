import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { Series, SeriesFilter } from "../../shared/api/contracts";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { MyTeam } from "../../shared/api/teams";
import { filterSeries, SERIES_PAGE_SIZE, seriesFilters } from "./seriesFilters";
import { SeriesCard } from "./SeriesCard";

interface SeriesPageProps {
  active: boolean;
  services: WorkspaceServices;
  userId: string;
  onUnauthorized(): void;
  onViewChapters(series: Series): void;
  onViewAssets(series: Series): void;
}

type TeamDirectoryStatus = "idle" | "loading" | "ready" | "error";

interface TeamDirectoryState {
  userId: string;
  services: WorkspaceServices;
  status: TeamDirectoryStatus;
  teams: MyTeam[] | null;
  error: ApiError | null;
}

interface TeamRequestContext {
  userId: string;
  services: WorkspaceServices;
  active: boolean;
  filter: SeriesFilter;
}

function emptyTeamDirectory(userId: string, services: WorkspaceServices): TeamDirectoryState {
  return { userId, services, status: "idle", teams: null, error: null };
}

function seriesErrorPresentation(error: ApiError): { title: string; message: string; kind: string } {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const isMembership = ["会员", "membership", "premium", "subscription"].some((word) => detail.includes(word));
    return isMembership
      ? {
          title: "当前账号暂不可查看剧集",
          message: error.detail ?? "会员资格暂不可用，请稍后再试。",
          kind: "membership",
        }
      : {
          title: "没有访问权限",
          message: error.detail ?? "当前账号无权读取此剧集列表。",
          kind: "forbidden",
        };
  }
  if (error.status === 422) {
    return { title: "列表请求未通过校验", message: error.detail ?? "请检查本地服务的接口数据。", kind: "request" };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "服务暂时不可用", message: "本地服务暂时无法读取剧集，请重试。", kind: "request" };
  }
  if (error.kind === "timeout") {
    return { title: "请求超时", message: "本地服务响应较慢，请确认服务状态后重试。", kind: "request" };
  }
  if (error.kind === "network") {
    return { title: "无法连接本地服务", message: "请确认本地服务正在运行，然后重试。", kind: "request" };
  }
  if (error.kind === "invalid-response") {
    return { title: "服务返回的数据无法识别", message: error.message, kind: "request" };
  }
  return { title: "暂时无法读取剧集", message: error.detail ?? error.message, kind: "request" };
}

function teamErrorPresentation(error: ApiError): { title: string; message: string; kind: string } {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const isMembership = ["会员", "membership", "premium", "subscription"].some((word) => detail.includes(word));
    return isMembership
      ? {
          title: "当前账号暂不可读取团队",
          message: error.detail ?? "会员资格暂不可用，请稍后再试。",
          kind: "membership",
        }
      : {
          title: "没有访问团队目录的权限",
          message: error.detail ?? "当前账号无权读取团队目录。",
          kind: "forbidden",
        };
  }
  if (error.status === 404) {
    return { title: "团队目录暂不可用", message: error.detail ?? "本地服务未提供团队目录。", kind: "request" };
  }
  if (error.status === 422) {
    return { title: "团队目录请求未通过校验", message: error.detail ?? "请检查本地服务的接口数据。", kind: "request" };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "团队目录暂时不可用", message: "本地服务暂时无法读取团队，请重试。", kind: "request" };
  }
  if (error.kind === "timeout") {
    return { title: "读取团队超时", message: "本地服务响应较慢，请稍后重试。", kind: "request" };
  }
  if (error.kind === "network") {
    return { title: "无法连接本地服务", message: "请确认本地服务正在运行，然后重试。", kind: "request" };
  }
  if (error.kind === "invalid-response") {
    return { title: "团队目录数据无法识别", message: error.message, kind: "request" };
  }
  return { title: "暂时无法读取团队目录", message: error.detail ?? error.message, kind: "request" };
}

export function SeriesPage({
  active,
  services,
  userId,
  onUnauthorized,
  onViewChapters,
  onViewAssets,
}: SeriesPageProps) {
  const [series, setSeries] = useState<Awaited<ReturnType<WorkspaceServices["listSeries"]>>>([]);
  const [seriesScope, setSeriesScope] = useState({ userId, services });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [filter, setFilter] = useState<SeriesFilter>("all");
  const [visibleCount, setVisibleCount] = useState(SERIES_PAGE_SIZE);
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [teamListChanged, setTeamListChanged] = useState(false);
  const [teamDirectory, setTeamDirectory] = useState<TeamDirectoryState>(() =>
    emptyTeamDirectory(userId, services),
  );

  const requestGeneration = useRef(0);
  const teamRequestController = useRef<AbortController | null>(null);
  const contextRef = useRef<TeamRequestContext>({ userId, services, active, filter });
  contextRef.current = { userId, services, active, filter };

  const seriesMatchesScope = seriesScope.userId === userId && seriesScope.services === services;
  const visibleSeries = seriesMatchesScope ? series : [];
  const visibleLoading = !seriesMatchesScope || loading;
  const visibleError = seriesMatchesScope ? error : null;
  const visibleFilter = seriesMatchesScope ? filter : "all";
  const scopedVisibleCount = seriesMatchesScope ? visibleCount : SERIES_PAGE_SIZE;

  const directoryMatchesScope = teamDirectory.userId === userId && teamDirectory.services === services;
  const visibleDirectory = directoryMatchesScope
    ? teamDirectory
    : emptyTeamDirectory(userId, services);
  const visibleSelectedTeamId = directoryMatchesScope ? selectedTeamId : null;
  const selectedTeamIdRef = useRef<string | null>(visibleSelectedTeamId);
  selectedTeamIdRef.current = visibleSelectedTeamId;

  const cancelTeamRequest = useCallback(() => {
    requestGeneration.current += 1;
    teamRequestController.current?.abort();
    teamRequestController.current = null;
  }, []);

  const startTeamRequest = useCallback(() => {
    const context = contextRef.current;
    if (!context.active || context.filter !== "team") {
      return;
    }

    cancelTeamRequest();
    const generation = requestGeneration.current;
    const controller = new AbortController();
    teamRequestController.current = controller;

    setTeamDirectory((previous) => {
      const scopedPrevious = previous.userId === context.userId && previous.services === context.services
        ? previous
        : emptyTeamDirectory(context.userId, context.services);
      return { ...scopedPrevious, status: "loading", error: null };
    });

    const isCurrentRequest = () => {
      const current = contextRef.current;
      return requestGeneration.current === generation
        && !controller.signal.aborted
        && current.userId === context.userId
        && current.services === context.services
        && current.active
        && current.filter === "team";
    };

    let request: Promise<MyTeam[]>;
    try {
      request = context.services.listMyTeams(controller.signal);
    } catch (cause) {
      request = Promise.reject(cause);
    }

    void request.then((teams) => {
      if (!isCurrentRequest()) {
        return;
      }

      const selectedAtApply = selectedTeamIdRef.current;
      const selectedStillExists = selectedAtApply === null
        || teams.some((team) => team.id === selectedAtApply);

      setTeamDirectory({
        userId: context.userId,
        services: context.services,
        status: "ready",
        teams,
        error: null,
      });
      setTeamListChanged(selectedAtApply !== null && !selectedStillExists);
      if (!selectedStillExists) {
        selectedTeamIdRef.current = null;
        setSelectedTeamId(null);
        setVisibleCount(SERIES_PAGE_SIZE);
      }
    }).catch((cause: unknown) => {
      if (!isCurrentRequest()) {
        return;
      }
      const nextError = normalizeApiError(cause);
      if (nextError.kind === "aborted") {
        return;
      }
      if (nextError.status === 401) {
        onUnauthorized();
        return;
      }

      setTeamDirectory((previous) => {
        const scopedPrevious = previous.userId === context.userId && previous.services === context.services
          ? previous
          : emptyTeamDirectory(context.userId, context.services);
        return { ...scopedPrevious, status: "error", error: nextError };
      });
    }).finally(() => {
      if (requestGeneration.current === generation) {
        teamRequestController.current = null;
      }
    });
  }, [cancelTeamRequest, onUnauthorized]);

  useLayoutEffect(() => {
    if (!directoryMatchesScope) {
      cancelTeamRequest();
      setTeamDirectory(emptyTeamDirectory(userId, services));
      setFilter("all");
      setSelectedTeamId(null);
      selectedTeamIdRef.current = null;
      setVisibleCount(SERIES_PAGE_SIZE);
      setTeamListChanged(false);
      return;
    }

    if ((!active || filter !== "team") && teamRequestController.current !== null) {
      cancelTeamRequest();
      setTeamDirectory((previous) => {
        if (previous.userId !== userId || previous.services !== services || previous.status !== "loading") {
          return previous;
        }
        return {
          ...previous,
          status: previous.teams === null ? "idle" : "ready",
          error: null,
        };
      });
    }
  }, [
    active,
    cancelTeamRequest,
    directoryMatchesScope,
    filter,
    services,
    teamDirectory.status,
    userId,
  ]);

  useEffect(() => {
    if (!active || filter !== "team" || !directoryMatchesScope || visibleDirectory.status !== "idle") {
      return;
    }
    startTeamRequest();
  }, [active, directoryMatchesScope, filter, services, startTeamRequest, userId, visibleDirectory.status]);

  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setLoading(true);
    setError(null);

    void services.listSeries(controller.signal).then((result) => {
      if (current && !controller.signal.aborted) {
        setSeries(result);
        setSeriesScope({ userId, services });
        setLoading(false);
      }
    }).catch((cause: unknown) => {
      if (!current || controller.signal.aborted) {
        return;
      }
      const nextError = normalizeApiError(cause);
      if (nextError.status === 401) {
        onUnauthorized();
        return;
      }
      if (!seriesMatchesScope) {
        setSeries([]);
      }
      setSeriesScope({ userId, services });
      setError(nextError);
      setLoading(false);
    });

    return () => {
      current = false;
      controller.abort();
    };
  }, [onUnauthorized, reloadKey, services, userId]);

  useLayoutEffect(() => () => cancelTeamRequest(), [cancelTeamRequest]);

  const teamSharedSeries = useMemo(
    () => filterSeries(visibleSeries, "team", userId),
    [userId, visibleSeries],
  );
  const filteredSeries = useMemo(() => {
    if (visibleFilter !== "team") {
      return filterSeries(visibleSeries, visibleFilter, userId);
    }
    if (visibleSelectedTeamId === null) {
      return teamSharedSeries;
    }
    return teamSharedSeries.filter((item) => item.team_id === visibleSelectedTeamId);
  }, [teamSharedSeries, userId, visibleFilter, visibleSeries, visibleSelectedTeamId]);

  const displayedSeries = filteredSeries.slice(0, scopedVisibleCount);
  const remainingCount = Math.max(0, filteredSeries.length - displayedSeries.length);

  function changeFilter(nextFilter: SeriesFilter) {
    if (nextFilter === filter) {
      return;
    }
    contextRef.current = { ...contextRef.current, filter: nextFilter };
    if (filter === "team" && nextFilter !== "team") {
      setSelectedTeamId(null);
      selectedTeamIdRef.current = null;
      setTeamListChanged(false);
    }
    setFilter(nextFilter);
    setVisibleCount(SERIES_PAGE_SIZE);
  }

  function changeTeam(nextTeamId: string | null) {
    if (nextTeamId === visibleSelectedTeamId) {
      return;
    }
    selectedTeamIdRef.current = nextTeamId;
    setSelectedTeamId(nextTeamId);
    setVisibleCount(SERIES_PAGE_SIZE);
    setTeamListChanged(false);
  }

  const seriesPresentation = visibleError === null ? null : seriesErrorPresentation(visibleError);
  const teamPresentation = visibleDirectory.error === null
    ? null
    : teamErrorPresentation(visibleDirectory.error);

  return (
    <section className="series-page" aria-labelledby="series-heading">
      <div className="page-heading">
        <div>
          <p className="eyebrow">创作项目</p>
          <h1 id="series-heading">我的剧集</h1>
          <p>查看个人持有、团队共享与认领的剧集。</p>
        </div>
        <div className="readonly-pill"><span aria-hidden="true">◉</span> 只读阶段</div>
      </div>

      <div className="series-toolbar">
        <div>
          <div className="filter-tabs" role="group" aria-label="剧集筛选">
            {seriesFilters.map((item) => (
              <button
                aria-pressed={visibleFilter === item.id}
                className={visibleFilter === item.id ? "filter-tab active" : "filter-tab"}
                key={item.id}
                onClick={() => changeFilter(item.id)}
                type="button"
              >
                {item.label}
                {item.id === "all" && !visibleLoading && visibleError === null && (
                  <span className="filter-count">{visibleSeries.length}</span>
                )}
              </button>
            ))}
          </div>
          <p className="list-order-note">保持列表原始顺序</p>
        </div>
        {visibleFilter === "team" && (
          <div className="team-filter-controls">
            <label htmlFor="team-filter">筛选团队</label>
            <select
              id="team-filter"
              onChange={(event) => changeTeam(event.currentTarget.value === "" ? null : event.currentTarget.value)}
              value={visibleSelectedTeamId ?? ""}
            >
              <option value="">全部团队</option>
              {visibleDirectory.teams?.map((team) => (
                <option key={team.id} value={team.id}>{team.name || "团队"}</option>
              ))}
            </select>
            <button
              className="secondary-button team-reload-button"
              onClick={startTeamRequest}
              type="button"
            >
              重新读取团队
            </button>
            {visibleDirectory.status === "loading" && (
              <p className="team-directory-message" role="status" aria-live="polite">
                正在读取团队名单…
              </p>
            )}
            {visibleDirectory.status === "ready" && visibleDirectory.teams?.length === 0 && (
              <p className="team-directory-message" role="status">暂无所属团队。</p>
            )}
            {teamPresentation !== null && (
              <div className={`team-directory-error ${teamPresentation.kind}`} role="alert">
                <strong>{teamPresentation.title}</strong>
                <span>{teamPresentation.message}</span>
                {visibleDirectory.teams !== null && (
                  <span>当前为上次成功的团队名单。</span>
                )}
              </div>
            )}
            {teamListChanged && (
              <p className="team-directory-message" role="status">
                团队名单已变化，已显示全部团队。
              </p>
            )}
          </div>
        )}
      </div>

      {visibleLoading && (
        <div className="state-panel loading-panel" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>正在读取剧集…</span>
        </div>
      )}

      {!visibleLoading && seriesPresentation !== null && (
        <div className={`state-panel error-panel ${seriesPresentation.kind}`} role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h2>{seriesPresentation.title}</h2>
            <p>{seriesPresentation.message}</p>
          </div>
          <button
            className="secondary-button"
            onClick={() => setReloadKey((key) => key + 1)}
            type="button"
          >
            重试读取
          </button>
        </div>
      )}

      {!visibleLoading && visibleError === null && visibleSeries.length === 0 && !(visibleFilter === "team" && visibleSelectedTeamId !== null) && (
        <div className="state-panel empty-panel" role="status">
          <div className="empty-icon" aria-hidden="true">◌</div>
          <h2>还没有剧集</h2>
          <p>创建与导入功能暂未开放。</p>
        </div>
      )}

      {!visibleLoading && visibleError === null && visibleFilter === "team" && visibleSelectedTeamId !== null && filteredSeries.length === 0 && (
        <div className="state-panel empty-panel" role="status">
          <div className="empty-icon" aria-hidden="true">⌕</div>
          <h2>该团队暂无剧集</h2>
          <p>此团队仍保留在所属团队名单中。</p>
        </div>
      )}

      {!visibleLoading && visibleError === null && visibleSeries.length > 0 && filteredSeries.length === 0
        && !(visibleFilter === "team" && visibleSelectedTeamId !== null) && (
        <div className="state-panel empty-panel" role="status">
          <div className="empty-icon" aria-hidden="true">⌕</div>
          <h2>这个分类暂时没有剧集</h2>
          <p>试试其他筛选，列表保持原始顺序。</p>
        </div>
      )}

      {!visibleLoading && visibleError === null && displayedSeries.length > 0 && (
        <>
          <div className="series-grid" aria-live="polite">
            {displayedSeries.map((item) => (
              <SeriesCard
                apiBaseUrl={services.apiBaseUrl}
                currentUserId={userId}
                key={item.id}
                onViewAssets={onViewAssets}
                onViewChapters={onViewChapters}
                series={item}
              />
            ))}
          </div>
          <div className="pagination-row">
            <span>
              显示 {displayedSeries.length} / {filteredSeries.length} 部
            </span>
            {remainingCount > 0 && (
              <button
                className="secondary-button load-more-button"
                onClick={() => setVisibleCount((count) => count + SERIES_PAGE_SIZE)}
                type="button"
              >
                加载更多 <span>还有 {remainingCount} 部</span>
              </button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
