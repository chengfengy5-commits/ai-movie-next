import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import type {
  TeamMemberTasksPage,
  TeamModelUsageResponse,
  TeamReportingDateRange,
  TeamReportingServicePort,
  TeamSeriesUsageResponse,
  TeamUsageResponse,
  TeamUsageExport,
} from "../../shared/api/teamReporting";
import type { TeamDetail } from "../../shared/api/teamManagement";
import { buildTeamReportingSpreadsheetBlob } from "./teamReportingExport";
import "./TeamReporting.css";

type ReportingView = "tasks" | "usage" | "series" | "model";
type UsageDetailView = "usage" | "series";

interface TeamReportingPanelProps {
  active: boolean;
  actorId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  team: TeamDetail;
  port: TeamReportingServicePort | undefined;
  onUnauthorized(): void;
  onRefreshTeam(): void;
}

interface RequestScope {
  actorId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  teamId: string;
  port: TeamReportingServicePort | undefined;
  active: boolean;
  canViewTasks: boolean;
  canViewUsage: boolean;
  membersKey: string;
  view: ReportingView;
  selectedUserId: string;
  taskPage: number;
  range: TeamReportingDateRange;
  seriesId: string | null;
  modelName: string | null;
  modelReturnView: UsageDetailView;
  reloadRevision: number;
}

interface RequestHandle {
  channel: "read" | "export";
  owner: RequestScope;
  sequence: number;
  controller: AbortController;
}

function hasPermission(team: TeamDetail, actorId: string, permission: "view_tasks" | "view_usage"): boolean {
  return team.owner_id === actorId || team.my_permissions.includes(permission);
}

function isCurrentScope(left: RequestScope, right: RequestScope): boolean {
  return left.actorId === right.actorId
    && left.sessionGeneration === right.sessionGeneration
    && left.services === right.services
    && left.teamId === right.teamId
    && left.port === right.port
    && left.active === right.active
    && left.canViewTasks === right.canViewTasks
    && left.canViewUsage === right.canViewUsage
    && left.membersKey === right.membersKey
    && left.view === right.view
    && left.selectedUserId === right.selectedUserId
    && left.taskPage === right.taskPage
    && left.range.startTime === right.range.startTime
    && left.range.endTime === right.range.endTime
    && left.seriesId === right.seriesId
    && left.modelName === right.modelName
    && left.modelReturnView === right.modelReturnView
    && left.reloadRevision === right.reloadRevision;
}

function parseCalendarDate(value: string): { year: number; month: number; day: number } | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(0);
  date.setFullYear(year, month - 1, day);
  date.setHours(0, 0, 0, 0);
  if (date.getFullYear() !== year || date.getMonth() !== month - 1 || date.getDate() !== day) return null;
  return { year, month, day };
}

function localDayRange(value: string, endOfDay: boolean): string | null {
  const date = parseCalendarDate(value);
  if (date === null) return null;
  const localDate = new Date(0);
  localDate.setFullYear(date.year, date.month - 1, date.day);
  localDate.setHours(
    endOfDay ? 23 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 59 : 0,
    endOfDay ? 999 : 0,
  );
  return localDate.toISOString();
}

function rangeFromDates(startDate: string, endDate: string): TeamReportingDateRange | null {
  if (startDate === "" && endDate === "") return {};
  const startTime = startDate === "" ? undefined : localDayRange(startDate, false);
  const endTime = endDate === "" ? undefined : localDayRange(endDate, true);
  if (startTime === null || endTime === null) return null;
  if (startTime !== undefined && endTime !== undefined && Date.parse(startTime) > Date.parse(endTime)) return null;
  return {
    ...(startTime === undefined ? {} : { startTime }),
    ...(endTime === undefined ? {} : { endTime }),
  };
}

function readErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.detail !== null) return error.detail;
    if (error.status === 403) return "团队成员关系或查看权限已变化。";
    if (error.status === 404) return "团队或报告对象已不存在，请刷新团队资料后重试。";
    if (error.status === 503) return "当前服务没有接入团队报告接口。";
    if (error.kind === "timeout" || error.kind === "network") return "读取团队报告失败，请检查连接后重试。";
  }
  return "读取团队报告失败，请重试。";
}

function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}

function formatCredits(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatDate(value: string | null): string {
  if (value === null) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function triggerDownload(blob: Blob, teamId: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = `team-${teamId}-usage.xls`;
  link.style.display = "none";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

function UsageMetrics({
  calls,
  credits,
  failed,
}: {
  calls: number;
  credits: number;
  failed: number;
}) {
  return (
    <dl className="team-reporting-metrics">
      <div><dt>成功调用</dt><dd>{calls}</dd></div>
      <div><dt>消耗积分</dt><dd>{formatCredits(credits)}</dd></div>
      <div><dt>失败调用</dt><dd>{failed}</dd></div>
    </dl>
  );
}

function EmptyState({ children }: { children: string }) {
  return <p className="team-empty" role="status">{children}</p>;
}

export function TeamReportingPanel({
  active,
  actorId,
  sessionGeneration,
  services,
  team,
  port,
  onUnauthorized,
  onRefreshTeam,
}: TeamReportingPanelProps) {
  const [view, setView] = useState<ReportingView>(() => hasPermission(team, actorId, "view_tasks") ? "tasks" : "usage");
  const [selectedUserId, setSelectedUserId] = useState(actorId);
  const [taskPage, setTaskPage] = useState(1);
  const [taskResult, setTaskResult] = useState<TeamMemberTasksPage | null>(null);
  const [usageResult, setUsageResult] = useState<TeamUsageResponse | null>(null);
  const [seriesResult, setSeriesResult] = useState<TeamSeriesUsageResponse | null>(null);
  const [modelResult, setModelResult] = useState<TeamModelUsageResponse | null>(null);
  const [seriesId, setSeriesId] = useState<string | null>(null);
  const [modelName, setModelName] = useState<string | null>(null);
  const [modelReturnView, setModelReturnView] = useState<UsageDetailView>("usage");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [appliedRange, setAppliedRange] = useState<TeamReportingDateRange>({});
  const [dateError, setDateError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blockedTasks, setBlockedTasks] = useState(false);
  const [blockedUsage, setBlockedUsage] = useState(false);
  const [reloadRevision, setReloadRevision] = useState(0);

  const readSequence = useRef(0);
  const exportSequence = useRef(0);
  const readController = useRef<AbortController | null>(null);
  const exportController = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const hasScopeMountedRef = useRef(false);
  const onUnauthorizedRef = useRef(onUnauthorized);
  const onRefreshTeamRef = useRef(onRefreshTeam);
  onUnauthorizedRef.current = onUnauthorized;
  onRefreshTeamRef.current = onRefreshTeam;

  const hasViewTasks = hasPermission(team, actorId, "view_tasks");
  const hasViewUsage = hasPermission(team, actorId, "view_usage");
  const canViewTasks = hasViewTasks && !blockedTasks;
  const canViewUsage = hasViewUsage && !blockedUsage;
  const effectiveUserId = team.members.some((member) => member.user_id === selectedUserId)
    ? selectedUserId
    : team.members[0]?.user_id ?? actorId;
  const membersKey = [
    `owner:${team.owner_id}:role:${team.my_role}`,
    ...team.members.map((member) => `${member.user_id}:${member.username}:${member.role}:${member.permissions.join(",")}`),
    `self:${[...team.my_permissions].sort().join(",")}`,
  ].join("|");
  const scopeRef = useRef<RequestScope>({
    actorId, sessionGeneration, services, teamId: team.id, port, active, canViewTasks, canViewUsage,
    membersKey, view, selectedUserId: effectiveUserId, taskPage, range: appliedRange,
    seriesId, modelName, modelReturnView, reloadRevision,
  });
  scopeRef.current = {
    actorId, sessionGeneration, services, teamId: team.id, port, active, canViewTasks, canViewUsage,
    membersKey, view, selectedUserId: effectiveUserId, taskPage, range: appliedRange,
    seriesId, modelName, modelReturnView, reloadRevision,
  };

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      readSequence.current += 1;
      readController.current?.abort();
      readController.current = null;
      exportSequence.current += 1;
      exportController.current?.abort();
      exportController.current = null;
    };
  }, []);

  useLayoutEffect(() => {
    exportSequence.current += 1;
    exportController.current?.abort();
    exportController.current = null;
    setExporting(false);
  }, [
    active,
    actorId,
    appliedRange,
    canViewTasks,
    canViewUsage,
    effectiveUserId,
    membersKey,
    modelName,
    modelReturnView,
    port,
    reloadRevision,
    selectedUserId,
    services,
    seriesId,
    sessionGeneration,
    taskPage,
    team.id,
    view,
  ]);

  const current = useCallback((handle: RequestHandle): boolean => mountedRef.current
    && !handle.controller.signal.aborted
    && (handle.channel === "read" ? readSequence.current : exportSequence.current) === handle.sequence
    && isCurrentScope(handle.owner, scopeRef.current), []);

  const beginRequest = useCallback((channel: RequestHandle["channel"] = "read"): RequestHandle => {
    const sequenceRef = channel === "read" ? readSequence : exportSequence;
    const controllerRef = channel === "read" ? readController : exportController;
    controllerRef.current?.abort();
    const controller = new AbortController();
    const handle: RequestHandle = { channel, owner: scopeRef.current, sequence: ++sequenceRef.current, controller };
    controllerRef.current = controller;
    if (channel === "read") setLoading(true);
    else setExporting(true);
    setError(null);
    return handle;
  }, []);

  const endRequest = useCallback((handle: RequestHandle): void => {
    if (!current(handle)) return;
    const controllerRef = handle.channel === "read" ? readController : exportController;
    if (controllerRef.current === handle.controller) controllerRef.current = null;
    if (handle.channel === "read") setLoading(false);
    else setExporting(false);
  }, [current]);

  const handleFailure = useCallback((handle: RequestHandle, cause: unknown): void => {
    if (!current(handle) || isAbort(cause)) return;
    if (cause instanceof ApiError && cause.status === 401) {
      onUnauthorizedRef.current();
      return;
    }
    if (cause instanceof ApiError && (cause.status === 403 || cause.status === 404)) {
      if (handle.owner.view === "tasks") {
        setTaskResult(null);
        if (cause.status === 403) setBlockedTasks(true);
      } else {
        if (handle.owner.view === "series") setSeriesResult(null);
        else if (handle.owner.view === "model") setModelResult(null);
        else setUsageResult(null);
        if (cause.status === 403) setBlockedUsage(true);
      }
      onRefreshTeamRef.current();
    }
    setError(readErrorMessage(cause));
  }, [current]);

  useEffect(() => {
    if (selectedUserId !== effectiveUserId) {
      setSelectedUserId(effectiveUserId);
      setTaskPage(1);
      setTaskResult(null);
    }
  }, [effectiveUserId, selectedUserId]);

  useEffect(() => {
    if (!canViewTasks && view === "tasks" && canViewUsage) setView("usage");
    else if (!canViewUsage && view !== "tasks" && canViewTasks) setView("tasks");
  }, [canViewTasks, canViewUsage, view]);

  useLayoutEffect(() => {
    if (!hasScopeMountedRef.current) {
      hasScopeMountedRef.current = true;
      return;
    }
    readSequence.current += 1;
    readController.current?.abort();
    readController.current = null;
    exportSequence.current += 1;
    exportController.current?.abort();
    exportController.current = null;
    setLoading(false);
    setExporting(false);
    setError(null);
    setTaskResult(null);
    setUsageResult(null);
    setSeriesResult(null);
    setModelResult(null);
    setBlockedTasks(false);
    setBlockedUsage(false);
    setView(hasPermission(team, actorId, "view_tasks") ? "tasks" : "usage");
    setSelectedUserId(actorId);
    setTaskPage(1);
    setSeriesId(null);
    setModelName(null);
    setModelReturnView("usage");
    setStartDate("");
    setEndDate("");
    setAppliedRange({});
  }, [actorId, sessionGeneration, services, port, team.id, membersKey, hasViewTasks, hasViewUsage]);

  useEffect(() => {
    if (!active || port === undefined) return undefined;
    const owner = scopeRef.current;
    if (owner.view === "tasks" && owner.canViewTasks) {
      const handle = beginRequest();
      const requestPageSize = 10;
      void owner.port!.listMemberTasks(owner.teamId, owner.selectedUserId, owner.taskPage, requestPageSize, handle.controller.signal)
        .then((result) => { if (current(handle)) setTaskResult(result); })
        .catch((cause: unknown) => handleFailure(handle, cause))
        .finally(() => endRequest(handle));
      return () => {
        if (readSequence.current === handle.sequence) {
          readSequence.current += 1;
          handle.controller.abort();
          if (readController.current === handle.controller) readController.current = null;
        }
      };
    }
    if (owner.view === "usage" && owner.canViewUsage) {
      const handle = beginRequest();
      void owner.port!.getTeamUsage(owner.teamId, owner.range, handle.controller.signal)
        .then((result) => { if (current(handle)) setUsageResult(result); })
        .catch((cause: unknown) => handleFailure(handle, cause))
        .finally(() => endRequest(handle));
      return () => {
        if (readSequence.current === handle.sequence) {
          readSequence.current += 1;
          handle.controller.abort();
          if (readController.current === handle.controller) readController.current = null;
        }
      };
    }
    if (owner.view === "series" && owner.canViewUsage && owner.seriesId !== null) {
      const handle = beginRequest();
      void owner.port!.getSeriesUsage(owner.teamId, owner.seriesId, owner.range, handle.controller.signal)
        .then((result) => { if (current(handle)) setSeriesResult(result); })
        .catch((cause: unknown) => handleFailure(handle, cause))
        .finally(() => endRequest(handle));
      return () => {
        if (readSequence.current === handle.sequence) {
          readSequence.current += 1;
          handle.controller.abort();
          if (readController.current === handle.controller) readController.current = null;
        }
      };
    }
    if (owner.view === "model" && owner.canViewUsage && owner.modelName !== null) {
      const handle = beginRequest();
      void owner.port!.getModelUsage(owner.teamId, owner.modelName, owner.range, handle.controller.signal)
        .then((result) => { if (current(handle)) setModelResult(result); })
        .catch((cause: unknown) => handleFailure(handle, cause))
        .finally(() => endRequest(handle));
      return () => {
        if (readSequence.current === handle.sequence) {
          readSequence.current += 1;
          handle.controller.abort();
          if (readController.current === handle.controller) readController.current = null;
        }
      };
    }
    return undefined;
  }, [active, actorId, appliedRange, beginRequest, canViewTasks, canViewUsage, current, effectiveUserId, endRequest, handleFailure, modelName, modelReturnView, port, reloadRevision, selectedUserId, seriesId, services, sessionGeneration, taskPage, team.id, view]);

  function applyDateRange(): void {
    const next = rangeFromDates(startDate, endDate);
    if (next === null) {
      setDateError("请输入有效日期，并确认开始日期不晚于结束日期。");
      return;
    }
    setDateError(null);
    setSeriesResult(null);
    setModelResult(null);
    setUsageResult(null);
    setSeriesId(null);
    setModelName(null);
    setView("usage");
    setAppliedRange(next);
  }

  function openSeries(nextSeriesId: string): void {
    setSeriesResult(null);
    setSeriesId(nextSeriesId);
    setView("series");
  }

  function openModel(nextModelName: string, returnView: UsageDetailView): void {
    setModelResult(null);
    setModelName(nextModelName);
    setModelReturnView(returnView);
    setView("model");
  }

  function returnFromModel(): void {
    setModelResult(null);
    setModelName(null);
    setView(modelReturnView);
  }

  function retry(): void {
    setError(null);
    setReloadRevision((value) => value + 1);
  }

  async function exportUsage(): Promise<void> {
    const owner = scopeRef.current;
    if (!owner.active || !owner.canViewUsage || owner.port === undefined) return;
    const handle = beginRequest("export");
    try {
      const result: TeamUsageExport = await owner.port.exportTeamUsage(owner.teamId, owner.range, handle.controller.signal);
      if (!current(handle)) return;
      const blob = buildTeamReportingSpreadsheetBlob(result);
      if (!current(handle)) return;
      triggerDownload(blob, owner.teamId);
    } catch (cause) {
      handleFailure(handle, cause);
    } finally {
      endRequest(handle);
    }
  }

  const dateRangeLabel = appliedRange.startTime || appliedRange.endTime
    ? `${appliedRange.startTime ? new Date(appliedRange.startTime).toLocaleDateString() : "不限"} 至 ${appliedRange.endTime ? new Date(appliedRange.endTime).toLocaleDateString() : "不限"}`
    : "全部日期";

  return (
    <section aria-label="团队用量与任务" className="team-panel team-reporting-panel" hidden={!active}>
      <div className="team-panel-heading">
        <div>
          <p className="team-eyebrow">团队报告</p>
          <h2>用量与任务</h2>
          <p className="team-muted">任务积分属于所选成员账户；用量按团队剧集关联的完成与失败任务统计。</p>
        </div>
        <button className="secondary-button" disabled={loading || exporting || port === undefined} onClick={retry} type="button">刷新</button>
      </div>

      {port === undefined ? (
        <EmptyState>当前服务没有接入团队报告接口。</EmptyState>
      ) : !canViewTasks && !canViewUsage ? (
        <EmptyState>当前账号没有查看团队任务或用量的权限。</EmptyState>
      ) : (
        <>
          {canViewTasks && canViewUsage && (
            <div aria-label="报告类型" className="team-reporting-switch" role="tablist">
              <button
                aria-selected={view === "tasks"}
                className={view === "tasks" ? "active" : ""}
                onClick={() => {
                  if (view === "tasks") return;
                  setTaskResult(null);
                  setView("tasks");
                }}
                role="tab"
                type="button"
              >
                成员任务
              </button>
              <button
                aria-selected={view !== "tasks"}
                className={view !== "tasks" ? "active" : ""}
                onClick={() => {
                  if (view === "usage") return;
                  setUsageResult(null);
                  setSeriesResult(null);
                  setModelResult(null);
                  setSeriesId(null);
                  setModelName(null);
                  setView("usage");
                }}
                role="tab"
                type="button"
              >
                团队用量
              </button>
            </div>
          )}

          {error !== null && <p className="team-error" role="alert">{error} <button className="team-reporting-inline-button" onClick={retry} type="button">重试</button></p>}
          {loading && <p className="team-muted" role="status">正在读取报告…</p>}

          {view === "tasks" && canViewTasks && (
            <div aria-label="成员任务" className="team-reporting-content" role="tabpanel">
              <div className="team-reporting-toolbar">
                <label htmlFor="team-reporting-member">成员</label>
                <select
                  id="team-reporting-member"
                  onChange={(event) => { setSelectedUserId(event.currentTarget.value); setTaskPage(1); setTaskResult(null); }}
                  value={effectiveUserId}
                >
                  {team.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.username || member.user_id}</option>)}
                </select>
                <span className="team-muted">成员全部账户任务，不限于团队剧集</span>
              </div>
              {taskResult !== null && (
                <>
                  <div className="team-reporting-task-summary">
                    <span>账户积分余额 <strong>{formatCredits(taskResult.credits)}</strong></span>
                    <span>共 {taskResult.total} 项任务</span>
                  </div>
                  {taskResult.tasks.length === 0 ? <EmptyState>该成员当前没有任务记录。</EmptyState> : (
                    <div className="team-reporting-task-list">
                      {taskResult.tasks.map((task) => (
                        <article className="team-reporting-task" key={task.id}>
                          <div className="team-reporting-task-heading">
                            <strong>{task.asset_name || task.chapter_title || task.type}</strong>
                            <span>{task.status}</span>
                          </div>
                          <p>{task.progress_message || task.result || task.frame_text || "暂无任务说明"}</p>
                          <small>{task.type} · {formatDate(task.created_at)} · 积分 {formatCredits(task.credit_cost)}</small>
                          {task.frame_index !== null && task.frame_count !== null && <small>分镜 {task.frame_index} / {task.frame_count}</small>}
                        </article>
                      ))}
                    </div>
                  )}
                  <div aria-label="成员任务分页" className="team-reporting-pagination">
                    <span>第 {taskResult.page} 页 · 每页 {taskResult.page_size} 项</span>
                    <button className="secondary-button" disabled={loading || taskPage <= 1} onClick={() => { setTaskResult(null); setTaskPage((value) => Math.max(1, value - 1)); }} type="button">上一页</button>
                    <button className="secondary-button" disabled={loading || taskPage * taskResult.page_size >= taskResult.total} onClick={() => { setTaskResult(null); setTaskPage((value) => value + 1); }} type="button">下一页</button>
                  </div>
                </>
              )}
              {taskResult === null && !loading && error === null && <EmptyState>正在等待成员任务。</EmptyState>}
            </div>
          )}

          {view !== "tasks" && canViewUsage && (
            <div aria-label="团队用量明细" className="team-reporting-content" role="tabpanel">
              <form className="team-reporting-date-filter" onSubmit={(event) => { event.preventDefault(); applyDateRange(); }}>
                <label htmlFor="team-reporting-start">开始日期</label>
                <input id="team-reporting-start" onChange={(event) => setStartDate(event.currentTarget.value)} type="date" value={startDate} />
                <label htmlFor="team-reporting-end">结束日期</label>
                <input id="team-reporting-end" onChange={(event) => setEndDate(event.currentTarget.value)} type="date" value={endDate} />
                <button className="secondary-button" type="submit">应用筛选</button>
                {view === "usage" && <button className="primary-button" disabled={exporting || loading} onClick={() => { void exportUsage(); }} type="button">{exporting ? "正在导出…" : "导出 Excel"}</button>}
              </form>
              {dateError !== null && <p className="team-error" role="alert">{dateError}</p>}
              <p className="team-muted">当前范围：{dateRangeLabel}。日期按任务创建时间筛选；失败调用单独统计。</p>

              {view === "usage" && usageResult !== null && (
                <>
                  <UsageMetrics calls={usageResult.total_calls} credits={usageResult.total_credits} failed={usageResult.total_failed} />
                  {usageResult.items.length === 0 ? <EmptyState>当前日期范围内没有团队剧集用量。</EmptyState> : (
                    <div className="team-reporting-table-wrap">
                      <table aria-label="团队用量明细" className="team-reporting-table">
                        <thead><tr><th>团队剧集</th><th>成功调用</th><th>消耗积分</th><th>失败调用</th><th>更新时间</th></tr></thead>
                        <tbody>
                          {usageResult.items.map((item) => (
                            <tr key={item.series_id}>
                              <th scope="row"><button className="team-reporting-link" onClick={() => openSeries(item.series_id)} type="button">{item.series_name || "未命名剧集"}</button></th>
                              <td>{item.total_calls}</td><td>{formatCredits(item.total_credits)}</td><td>{item.failed_calls}</td><td>{formatDate(item.updated_at)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {usageResult.items.flatMap((item) => item.by_model).map((item, index) => (
                    <button className="team-reporting-model-chip" key={`${item.model_name}-${index}`} onClick={() => openModel(item.model_name, "usage")} type="button">
                      {item.model_name || "未知模型"} · {item.calls} 次
                    </button>
                  ))}
                  {usageResult.items.some((item) => item.by_member.length > 0) && (
                    <>
                      <h4>成员用量</h4>
                      <ul className="team-reporting-member-list">
                        {usageResult.items.flatMap((item) => item.by_member.map((member) => (
                          <li key={`${item.series_id}-${member.user_id}`}>
                            {member.username || member.user_id} · {item.series_name || "未命名剧集"} · {member.calls} 次 · {formatCredits(member.credits)} 积分 · 失败 {member.failed_calls}
                          </li>
                        )))}
                      </ul>
                    </>
                  )}
                </>
              )}

              {view === "series" && (
                <>
                  <button className="team-reporting-back" onClick={() => setView("usage")} type="button">‹ 返回团队用量</button>
                  {seriesResult !== null && (
                    <>
                      <h3>{seriesResult.series_name || "未命名剧集"}</h3>
                      <UsageMetrics calls={seriesResult.series_total.calls} credits={seriesResult.series_total.credits} failed={seriesResult.series_total.failed_calls} />
                      {seriesResult.chapters.length === 0 ? <EmptyState>该剧集在当前日期范围内没有章节用量。</EmptyState> : (
                        <div className="team-reporting-table-wrap">
                          <table aria-label="章节用量" className="team-reporting-table">
                            <thead><tr><th>章节</th><th>成功调用</th><th>消耗积分</th><th>失败调用</th></tr></thead>
                            <tbody>{seriesResult.chapters.map((chapter) => <tr key={chapter.chapter_id}><th scope="row">{chapter.chapter_title || "未命名章节"}</th><td>{chapter.calls}</td><td>{formatCredits(chapter.credits)}</td><td>{chapter.failed_calls}</td></tr>)}</tbody>
                          </table>
                        </div>
                      )}
                      {seriesResult.series_total.by_member.length > 0 && (
                        <>
                          <h4>成员用量</h4>
                          <ul className="team-reporting-member-list">
                            {seriesResult.series_total.by_member.map((member) => (
                              <li key={member.user_id}>
                                {member.username || member.user_id} · {member.calls} 次 · {formatCredits(member.credits)} 积分 · 失败 {member.failed_calls}
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                      {seriesResult.series_total.by_model.map((item) => <button className="team-reporting-model-chip" key={item.model_name} onClick={() => openModel(item.model_name, "series")} type="button">{item.model_name || "未知模型"} · {item.calls} 次</button>)}
                    </>
                  )}
                </>
              )}

              {view === "model" && (
                <>
                  <button className="team-reporting-back" onClick={returnFromModel} type="button">‹ 返回{modelReturnView === "series" ? "剧集明细" : "团队用量"}</button>
                  {modelResult !== null && (
                    <>
                      <h3>{modelResult.model_name || "未知模型"}</h3>
                      <UsageMetrics calls={modelResult.total_calls} credits={modelResult.total_credits} failed={modelResult.failed_calls} />
                      {modelResult.by_series.length === 0 ? <EmptyState>当前日期范围内没有该模型的剧集用量。</EmptyState> : (
                        <div className="team-reporting-table-wrap">
                          <table aria-label="模型剧集用量" className="team-reporting-table">
                            <thead><tr><th>团队剧集</th><th>成功调用</th><th>消耗积分</th><th>失败调用</th></tr></thead>
                            <tbody>{modelResult.by_series.map((item) => <tr key={item.series_id}><th scope="row"><button className="team-reporting-link" onClick={() => openSeries(item.series_id)} type="button">{item.series_name || "未命名剧集"}</button></th><td>{item.calls}</td><td>{formatCredits(item.credits)}</td><td>{item.failed_calls}</td></tr>)}</tbody>
                          </table>
                        </div>
                      )}
                      <h4>成员用量</h4>
                      <ul className="team-reporting-member-list">{modelResult.by_member.map((member) => <li key={member.user_id}>{member.username || member.user_id} · {member.calls} 次 · {formatCredits(member.credits)} 积分 · 失败 {member.failed_calls}</li>)}</ul>
                    </>
                  )}
                </>
              )}
              {view !== "usage" && !loading && error === null && ((view === "series" && seriesResult === null) || (view === "model" && modelResult === null)) && <EmptyState>正在读取用量明细。</EmptyState>}
            </div>
          )}
        </>
      )}
    </section>
  );
}
