import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Series } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import type { TeamDetail } from "../../shared/api/teamManagement";
import type { StylePromptChoice } from "../../shared/api/seriesManagement";
import {
  type CreateTeamSeriesInput,
  type TeamSeriesCreateDraft,
  type TeamSeriesPage,
  type TeamSeriesRecord,
  type TeamSeriesServicePort,
  type TeamSeriesWriteIntent,
} from "../../shared/api/teamSeries";
import { TeamSeriesClaimControls } from "./TeamSeriesClaimControls";
import { TeamSeriesCreateForm } from "./TeamSeriesCreateForm";
import { TeamSeriesShareControls } from "./TeamSeriesShareControls";

interface TeamSeriesPanelProps {
  active: boolean;
  actorId: string;
  sessionGeneration: number;
  seriesRevision: number;
  services: WorkspaceServices;
  teamId: string;
  team: TeamDetail | null;
  port: TeamSeriesServicePort | undefined;
  locked: boolean;
  pendingLabel: string | null;
  unknownLabel: string | null;
  createDraft: TeamSeriesCreateDraft;
  transferTargets: Record<string, string>;
  onCreateDraftChange(draft: TeamSeriesCreateDraft): void;
  onTransferTargetChange(seriesId: string, userId: string): void;
  onWrite<T>(intent: TeamSeriesWriteIntent<T>): void;
  onSeriesChanged(teamId: string, seriesId: string): void;
  onUnauthorized(): void;
  onOpenSeries(series: Series, destination: "chapters" | "assets"): void;
}

const TEAM_SERIES_PAGE_SIZE = 10;

interface RequestScope {
  actorId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  teamId: string;
  port: TeamSeriesServicePort | undefined;
  seriesRevision: number;
  active: boolean;
}

interface StylePromptState {
  actorId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  status: "idle" | "loading" | "ready" | "error";
  items: StylePromptChoice[];
  error: string | null;
}

function readErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  return "读取团队剧集失败，请重试。";
}

function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}

export function TeamSeriesPanel({
  active,
  actorId,
  sessionGeneration,
  seriesRevision,
  services,
  teamId,
  team,
  port,
  locked,
  pendingLabel,
  unknownLabel,
  createDraft,
  transferTargets,
  onCreateDraftChange,
  onTransferTargetChange,
  onWrite,
  onSeriesChanged,
  onUnauthorized,
  onOpenSeries,
}: TeamSeriesPanelProps) {
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<TeamSeriesPage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [openError, setOpenError] = useState<string | null>(null);
  const [openingSeriesId, setOpeningSeriesId] = useState<string | null>(null);
  const [stylePromptState, setStylePromptState] = useState<StylePromptState>({
    actorId,
    sessionGeneration,
    services,
    status: "idle",
    items: [],
    error: null,
  });

  const requestSequence = useRef(0);
  const requestController = useRef<AbortController | null>(null);
  const styleRequestRef = useRef<AbortController | null>(null);
  const openSequence = useRef(0);
  const openController = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const scopeRef = useRef<RequestScope>({ actorId, sessionGeneration, services, teamId, port, seriesRevision, active });
  scopeRef.current = { actorId, sessionGeneration, services, teamId, port, seriesRevision, active };
  const onUnauthorizedRef = useRef(onUnauthorized);
  const onOpenSeriesRef = useRef(onOpenSeries);
  onUnauthorizedRef.current = onUnauthorized;
  onOpenSeriesRef.current = onOpenSeries;

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestSequence.current += 1;
      requestController.current?.abort();
      openSequence.current += 1;
      openController.current?.abort();
      styleRequestRef.current?.abort();
    };
  }, []);

  const loadPage = useCallback(async (afterWrite = false): Promise<void> => {
    const owner = scopeRef.current;
    if (!owner.active || owner.port === undefined) return;
    requestController.current?.abort();
    const sequence = ++requestSequence.current;
    const controller = new AbortController();
    requestController.current = controller;
    setLoading(true);
    if (!afterWrite) setError(null);

    const isCurrent = () => mountedRef.current
      && !controller.signal.aborted
      && requestSequence.current === sequence
      && scopeRef.current.active
      && scopeRef.current.actorId === owner.actorId
      && scopeRef.current.sessionGeneration === owner.sessionGeneration
      && scopeRef.current.services === owner.services
      && scopeRef.current.port === owner.port
      && scopeRef.current.seriesRevision === owner.seriesRevision
      && scopeRef.current.teamId === owner.teamId;

    try {
      const next = await owner.port.listTeamSeries(owner.teamId, page, TEAM_SERIES_PAGE_SIZE, controller.signal);
      if (!isCurrent()) return;
      setResult(next);
      setError(null);
      if (afterWrite) setRefreshWarning(null);
    } catch (cause) {
      if (!isCurrent() || isAbort(cause)) return;
      if (cause instanceof ApiError && cause.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      const message = readErrorMessage(cause);
      if (afterWrite) setRefreshWarning(`操作已确认，但团队剧集列表暂时无法刷新。${message}`);
      else setError(message);
    } finally {
      if (isCurrent()) {
        requestController.current = null;
        setLoading(false);
      }
    }
  }, [page, seriesRevision]);

  useEffect(() => {
    if (!active || team === null || port === undefined) return undefined;
    void loadPage();
    return () => {
      requestSequence.current += 1;
      requestController.current?.abort();
      requestController.current = null;
    };
  }, [active, loadPage, port, seriesRevision, team, teamId]);

  const stylePromptsMatch = stylePromptState.actorId === actorId
    && stylePromptState.sessionGeneration === sessionGeneration
    && stylePromptState.services === services;
  const visibleStylePromptState = stylePromptsMatch
    ? stylePromptState
    : { actorId, sessionGeneration, services, status: "idle" as const, items: [], error: null };

  const stylePromptStateRef = useRef(stylePromptState);
  stylePromptStateRef.current = stylePromptState;

  useEffect(() => {
    const listStylePrompts = services.listSeriesStylePrompts;
    if (!active || listStylePrompts === undefined) return undefined;
    const previous = stylePromptStateRef.current;
    const stateMatchesScope = previous.actorId === actorId
      && previous.sessionGeneration === sessionGeneration
      && previous.services === services;
    if (stateMatchesScope && previous.status !== "idle") return undefined;

    const owner = { actorId, sessionGeneration, services, listStylePrompts };
    const controller = new AbortController();
    let current = true;
    styleRequestRef.current = controller;
    const previousItems = stateMatchesScope ? previous.items : [];
    const loadingState: StylePromptState = { ...owner, status: "loading", items: previousItems, error: null };
    stylePromptStateRef.current = loadingState;
    setStylePromptState(loadingState);
    const isCurrent = () => current
      && !controller.signal.aborted
      && styleRequestRef.current === controller
      && scopeRef.current.active
      && scopeRef.current.actorId === owner.actorId
      && scopeRef.current.sessionGeneration === owner.sessionGeneration
      && scopeRef.current.services === owner.services
      && scopeRef.current.services.listSeriesStylePrompts === owner.listStylePrompts;

    void listStylePrompts(controller.signal).then((items) => {
      if (!isCurrent()) return;
      const readyState: StylePromptState = { actorId, sessionGeneration, services, status: "ready", items, error: null };
      stylePromptStateRef.current = readyState;
      setStylePromptState(readyState);
    }).catch((cause: unknown) => {
      if (!isCurrent() || isAbort(cause)) return;
      if (cause instanceof ApiError && cause.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      const errorState: StylePromptState = {
        actorId,
        sessionGeneration,
        services,
        status: "error",
        items: previousItems,
        error: readErrorMessage(cause),
      };
      stylePromptStateRef.current = errorState;
      setStylePromptState(errorState);
    });

    return () => {
      current = false;
      controller.abort();
      if (styleRequestRef.current !== controller) return;
      styleRequestRef.current = null;
      const latest = stylePromptStateRef.current;
      if (latest.status !== "loading"
        || latest.actorId !== actorId
        || latest.sessionGeneration !== sessionGeneration
        || latest.services !== services) return;
      const idleState: StylePromptState = { ...latest, status: "idle", error: null };
      stylePromptStateRef.current = idleState;
      setStylePromptState(idleState);
    };
  }, [active, actorId, services, services.listSeriesStylePrompts, sessionGeneration]);

  function beginWrite<T>(intent: TeamSeriesWriteIntent<T>): void {
    const onConfirmed = intent.applyAck;
    onWrite({
      ...intent,
      applyAck: (ack) => {
        onConfirmed?.(ack);
        setNotice(`${intent.label}已确认，正在刷新团队剧集列表。`);
        setRefreshWarning(null);
        onSeriesChanged(intent.teamId, intent.targetId ?? "");
        void loadPage(true);
      },
    });
  }

  function createSeries(): void {
    if (port === undefined || team === null || locked) return;
    const input: CreateTeamSeriesInput = { name: createDraft.name, claim: createDraft.claim };
    if (createDraft.description !== undefined) input.description = createDraft.description;
    if (createDraft.image_url !== undefined) input.image_url = createDraft.image_url;
    if (createDraft.style_prompt_id !== undefined) input.style_prompt_id = createDraft.style_prompt_id;
    beginWrite({
      label: "创建团队剧集",
      teamId,
      targetId: null,
      submitted: { ...input },
      request: (api, signal) => api.createTeamSeries(teamId, input, actorId, signal),
      applyAck: (ack) => {
        onCreateDraftChange({ name: "", claim: false });
        setNotice(`《${ack.name || "未命名剧集"}》创建已确认，正在读取服务器当前状态。`);
      },
    });
  }

  async function openSeries(row: TeamSeriesRecord, destination: "chapters" | "assets"): Promise<void> {
    const owner = scopeRef.current;
    if (!owner.active || owner.services.getSeries === undefined) return;
    openController.current?.abort();
    const sequence = ++openSequence.current;
    const controller = new AbortController();
    openController.current = controller;
    setOpeningSeriesId(row.id);
    setOpenError(null);
    const isCurrent = () => mountedRef.current
      && !controller.signal.aborted
      && openSequence.current === sequence
      && scopeRef.current.active
      && scopeRef.current.actorId === owner.actorId
      && scopeRef.current.sessionGeneration === owner.sessionGeneration
      && scopeRef.current.services === owner.services
      && scopeRef.current.teamId === owner.teamId;
    try {
      const series = await owner.services.getSeries(row.id, controller.signal);
      if (!isCurrent()) return;
      if (series.id !== row.id || series.team_id !== owner.teamId) {
        setOpenError("剧集归属已变化，请刷新团队剧集列表后再打开。");
        return;
      }
      if (series.can_enter !== true) {
        setOpenError("当前账号暂时不能进入这部剧集，请确认认领状态或团队权限后重试。");
        return;
      }
      onOpenSeriesRef.current(series, destination);
    } catch (cause) {
      if (!isCurrent() || isAbort(cause)) return;
      if (cause instanceof ApiError && cause.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      setOpenError(readErrorMessage(cause));
    } finally {
      if (isCurrent()) {
        openController.current = null;
        setOpeningSeriesId(null);
      }
    }
  }

  const rows = result?.items ?? [];
  const teamIsReady = team !== null && team.id === teamId;

  return (
    <section
      aria-label="团队剧集协作"
      className="team-panel team-series-panel"
      hidden={!active}
    >
      <div className="team-panel-heading">
        <div>
          <p className="team-eyebrow">团队剧集</p>
          <h2>团队共享与制作负责人</h2>
          <p className="team-muted">成员可以创建或认领；取消与转交认领由当前认领者、剧集作者或团队拥有者操作。</p>
        </div>
        <button className="secondary-button" disabled={loading || port === undefined} onClick={() => { void loadPage(); }} type="button">
          {loading ? "正在读取…" : "刷新列表"}
        </button>
      </div>

      {unknownLabel !== null && (
        <p className="team-warning" role="status">
          “{unknownLabel}”仍待核对；当前列表只显示服务器事实，不会确认原操作或清除草稿。
        </p>
      )}
      {pendingLabel !== null && <p className="team-muted" role="status">正在执行：{pendingLabel}</p>}
      {notice !== null && <p className="team-success" role="status">{notice}</p>}
      {refreshWarning !== null && <p className="team-warning" role="status">{refreshWarning}</p>}
      {error !== null && <p className="team-error" role="alert">{error}</p>}
      {openError !== null && <p className="team-error" role="alert">{openError}</p>}

      {port === undefined ? (
        <p className="team-empty" role="status">当前服务没有接入团队剧集协作接口。</p>
      ) : (
        <>
          <TeamSeriesCreateForm
            disabled={locked || !teamIsReady}
            draft={createDraft}
            onDraftChange={onCreateDraftChange}
            onSubmit={createSeries}
            stylePrompts={visibleStylePromptState.items}
            stylePromptsError={visibleStylePromptState.error}
            stylePromptsLoading={visibleStylePromptState.status === "loading"}
          />

          {result === null && loading && <p className="team-empty" role="status">正在读取团队剧集…</p>}
          {result !== null && rows.length === 0 && (
            <p className="team-empty" role="status">第 {result.page} 页没有团队剧集。</p>
          )}
          {rows.length > 0 && (
            <div className="team-series-list">
              {rows.map((row) => (
                <article className="team-series-card" key={row.id}>
                  <div className="team-series-card-heading">
                    <div>
                      <h3>{row.name || "未命名剧集"}</h3>
                      <p>{row.description || "暂无简介"}</p>
                    </div>
                    <dl>
                      <div><dt>作者</dt><dd>{row.owner_name || "未命名作者"}</dd></div>
                      <div><dt>章节</dt><dd>{row.chapter_count}</dd></div>
                      <div><dt>角色 / 场景 / 道具 / 分镜素材</dt><dd>{row.asset_counts.characters} / {row.asset_counts.scenes} / {row.asset_counts.props} / {row.asset_counts.storyboard}</dd></div>
                    </dl>
                  </div>
                  <TeamSeriesClaimControls
                    actorId={actorId}
                    disabled={locked || !teamIsReady}
                    members={team?.members ?? []}
                    onRecipientChange={(value) => onTransferTargetChange(row.id, value)}
                    onWrite={beginWrite}
                    row={row}
                    selectedRecipientId={transferTargets[row.id] ?? ""}
                    team={team}
                    teamId={teamId}
                  />
                  <TeamSeriesShareControls
                    actorId={actorId}
                    disabled={locked || !teamIsReady}
                    onWrite={beginWrite}
                    row={row}
                    teamId={teamId}
                  />
                  {services.getSeries !== undefined && (
                    <div className="team-series-open-actions">
                      <button
                        className="secondary-button"
                        disabled={openingSeriesId !== null || locked || !teamIsReady}
                        onClick={() => { void openSeries(row, "chapters"); }}
                        type="button"
                      >
                        只读查看章节
                      </button>
                      <button
                        className="secondary-button"
                        disabled={openingSeriesId !== null || locked || !teamIsReady}
                        onClick={() => { void openSeries(row, "assets"); }}
                        type="button"
                      >
                        只读查看素材库
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          )}

          {result !== null && (
            <div aria-label="团队剧集分页" className="team-series-pagination">
              <span>第 {result.page} / {result.total_pages} 页 · 共 {result.total} 部</span>
              <div>
                <button className="secondary-button" disabled={loading || result.page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">上一页</button>
                <button className="secondary-button" disabled={loading || result.page >= result.total_pages} onClick={() => setPage((value) => value + 1)} type="button">下一页</button>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
