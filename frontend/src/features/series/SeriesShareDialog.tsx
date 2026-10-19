import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { Series } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import type { MyTeam } from "../../shared/api/teams";
import type { TeamSeriesServicePort } from "../../shared/api/teamSeries";
import {
  createTeamCollaborationWriteScope,
  sameTeamCollaborationWriteScope,
  TeamCollaborationWritePermit,
  type TeamCollaborationWriteLease,
  type TeamCollaborationWriteScope,
} from "../../app/teamCollaborationWritePermit";

interface SeriesShareDialogProps {
  active: boolean;
  actorId: string;
  sessionGeneration: number;
  scopeKey: string;
  series: Series;
  services: WorkspaceServices;
  collaborationWritePermit?: TeamCollaborationWritePermit;
  collaborationWriteScope?: TeamCollaborationWriteScope;
  onGuardChange(scopeKey: string, dirty: boolean, inFlight: boolean): void;
  onClose(): void;
  onUnauthorized(): void;
  onSeriesChanged(teamId: string, seriesId: string): void;
  onConfirmed(message: string): void;
}

interface UnknownShareAction {
  kind: "share" | "unshare";
  label: string;
  seriesId: string;
  teamId: string | null;
  submitted: unknown;
  attempt: number;
  leaseId: number;
}

interface ShareScope {
  active: boolean;
  actorId: string;
  sessionGeneration: number;
  seriesId: string;
  services: WorkspaceServices;
  port: TeamSeriesServicePort | undefined;
  collaborationWriteScope: TeamCollaborationWriteScope;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail ?? error.message;
  return "团队共享操作未完成。";
}

function isUnknown(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response"
    || error.kind === "aborted" || (error.status !== null && error.status >= 500);
}

function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}

export function SeriesShareDialog({
  active,
  actorId,
  sessionGeneration,
  scopeKey,
  series,
  services,
  collaborationWritePermit: providedWritePermit,
  collaborationWriteScope: providedWriteScope,
  onGuardChange,
  onClose,
  onUnauthorized,
  onSeriesChanged,
  onConfirmed,
}: SeriesShareDialogProps) {
  const localWritePermitRef = useRef<TeamCollaborationWritePermit | null>(null);
  if (localWritePermitRef.current === null) localWritePermitRef.current = new TeamCollaborationWritePermit();
  const collaborationWritePermit = providedWritePermit ?? localWritePermitRef.current;
  const localWriteScope = useMemo(
    () => createTeamCollaborationWriteScope(actorId, sessionGeneration, services),
    [actorId, services, services.teamManagement, services.teamSeries, sessionGeneration],
  );
  const collaborationWriteScope = providedWriteScope ?? localWriteScope;
  const permitSnapshot = useSyncExternalStore(
    collaborationWritePermit.subscribe,
    collaborationWritePermit.getSnapshot,
    collaborationWritePermit.getSnapshot,
  );
  const [teams, setTeams] = useState<MyTeam[]>([]);
  const [teamsLoading, setTeamsLoading] = useState(false);
  const [teamsError, setTeamsError] = useState<string | null>(null);
  const [selectedTeamId, setSelectedTeamId] = useState("");
  const [claim, setClaim] = useState(false);
  const [pendingLabel, setPendingLabel] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unknown, setUnknown] = useState<UnknownShareAction | null>(null);
  const [snapshot, setSnapshot] = useState<{ exists: boolean; teamId: string | null; teamName: string | null } | null>(null);
  const [checking, setChecking] = useState(false);
  const [teamReadKey, setTeamReadKey] = useState(0);

  const mountedRef = useRef(false);
  const identityRef = useRef({ actorId, sessionGeneration, seriesId: series.id, services, scopeKey, collaborationWriteScope });
  const attemptRef = useRef(0);
  const readSequenceRef = useRef(0);
  const readControllerRef = useRef<AbortController | null>(null);
  const writeRef = useRef<{ attempt: number; controller: AbortController; lease: TeamCollaborationWriteLease } | null>(null);
  const unknownRef = useRef<UnknownShareAction | null>(null);
  const selectedTeamIdRef = useRef(selectedTeamId);
  const claimRef = useRef(claim);
  const scopeRef = useRef<ShareScope>({ active, actorId, sessionGeneration, seriesId: series.id, services, port: services.teamSeries, collaborationWriteScope });
  const onGuardChangeRef = useRef(onGuardChange);
  const onUnauthorizedRef = useRef(onUnauthorized);
  const onCloseRef = useRef(onClose);
  const onSeriesChangedRef = useRef(onSeriesChanged);
  const onConfirmedRef = useRef(onConfirmed);
  scopeRef.current = { active, actorId, sessionGeneration, seriesId: series.id, services, port: services.teamSeries, collaborationWriteScope };
  onGuardChangeRef.current = onGuardChange;
  onUnauthorizedRef.current = onUnauthorized;
  onCloseRef.current = onClose;
  onSeriesChangedRef.current = onSeriesChanged;
  onConfirmedRef.current = onConfirmed;
  selectedTeamIdRef.current = selectedTeamId;
  claimRef.current = claim;

  function publishGuard(inFlight = writeRef.current !== null): void {
    const dirty = selectedTeamIdRef.current !== "" || claimRef.current || unknownRef.current !== null || inFlight;
    onGuardChangeRef.current(scopeKey, dirty, inFlight);
  }

  useLayoutEffect(() => {
    collaborationWritePermit.synchronizeScope(collaborationWriteScope);
  }, [collaborationWritePermit, collaborationWriteScope]);

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      readSequenceRef.current += 1;
      readControllerRef.current?.abort();
      writeRef.current?.controller.abort();
      onGuardChangeRef.current(identityRef.current.scopeKey, false, false);
    };
  }, []);

  useLayoutEffect(() => {
    const previous = identityRef.current;
    const changed = previous.actorId !== actorId
      || previous.sessionGeneration !== sessionGeneration
      || previous.seriesId !== series.id
      || previous.services !== services
      || !sameTeamCollaborationWriteScope(previous.collaborationWriteScope, collaborationWriteScope)
      || previous.scopeKey !== scopeKey;
    if (!changed) return;
    identityRef.current = { actorId, sessionGeneration, seriesId: series.id, services, scopeKey, collaborationWriteScope };
    readSequenceRef.current += 1;
    readControllerRef.current?.abort();
    writeRef.current?.controller.abort();
    writeRef.current = null;
    unknownRef.current = null;
    selectedTeamIdRef.current = "";
    claimRef.current = false;
    setTeams([]);
    setTeamsLoading(false);
    setTeamsError(null);
    setSelectedTeamId("");
    setClaim(false);
    setPendingLabel(null);
    setUnknown(null);
    setSnapshot(null);
    setError(null);
    onGuardChangeRef.current(previous.scopeKey, false, false);
    onGuardChangeRef.current(scopeKey, false, false);
  }, [actorId, collaborationWriteScope, scopeKey, series.id, services, sessionGeneration]);

  useEffect(() => {
    const frozen = unknownRef.current;
    if (frozen === null || permitSnapshot.lastAbandonedLeaseId !== frozen.leaseId) return;
    unknownRef.current = null;
    selectedTeamIdRef.current = "";
    claimRef.current = false;
    setUnknown(null);
    setSnapshot(null);
    setSelectedTeamId("");
    setClaim(false);
    setError(null);
    publishGuard(false);
  }, [permitSnapshot.revision]);

  useLayoutEffect(() => {
    if (active) publishGuard();
  }, [active, permitSnapshot.revision, scopeKey]);

  const canShare = series.user_id === actorId && series.team_id === null;
  const canUnshare = series.user_id === actorId && series.team_id !== null;
  const collaborationLocked = permitSnapshot.phase !== "idle";

  useEffect(() => {
    if (!active || !canShare) return undefined;
    const listTeams = services.listMyTeams;
    const owner = { actorId, sessionGeneration, seriesId: series.id, services, collaborationWriteScope };
    const sequence = ++readSequenceRef.current;
    const controller = new AbortController();
    readControllerRef.current?.abort();
    readControllerRef.current = controller;
    setTeamsLoading(true);
    setTeamsError(null);
    const isCurrent = () => mountedRef.current
      && !controller.signal.aborted
      && readSequenceRef.current === sequence
      && scopeRef.current.active
      && scopeRef.current.actorId === owner.actorId
      && scopeRef.current.sessionGeneration === owner.sessionGeneration
      && scopeRef.current.seriesId === owner.seriesId
      && scopeRef.current.services === owner.services
      && sameTeamCollaborationWriteScope(scopeRef.current.collaborationWriteScope, owner.collaborationWriteScope);
    void listTeams(controller.signal).then((items) => {
      if (!isCurrent()) return;
      setTeams(items);
    }).catch((cause: unknown) => {
      if (!isCurrent() || isAbort(cause)) return;
      if (cause instanceof ApiError && cause.status === 401) onUnauthorizedRef.current();
      else setTeamsError(errorMessage(cause));
    }).finally(() => {
      if (isCurrent()) {
        readControllerRef.current = null;
        setTeamsLoading(false);
      }
    });
    return () => {
      readSequenceRef.current += 1;
      controller.abort();
      if (readControllerRef.current === controller) readControllerRef.current = null;
    };
  }, [active, actorId, canShare, collaborationWriteScope, services, sessionGeneration, series.id, teamReadKey]);

  function setSelection(teamId: string): void {
    selectedTeamIdRef.current = teamId;
    setSelectedTeamId(teamId);
    setError(null);
    publishGuard();
  }

  function setClaimValue(value: boolean): void {
    claimRef.current = value;
    setClaim(value);
    publishGuard();
  }

  function close(): void {
    if (writeRef.current !== null) {
      setError("写操作仍在请求中，请等待结果返回；离开工作台仍可通过全局确认处理。");
      return;
    }
    const hasLocalState = selectedTeamIdRef.current !== "" || claimRef.current || unknownRef.current !== null;
    if (hasLocalState && !window.confirm("关闭会丢弃当前共享选择或待核对状态；这不会撤销服务器可能已完成的操作。继续吗？")) return;
    const frozenUnknown = unknownRef.current;
    if (frozenUnknown !== null) {
      collaborationWritePermit.abandonUnknown(collaborationWriteScope, frozenUnknown.leaseId);
    }
    selectedTeamIdRef.current = "";
    claimRef.current = false;
    unknownRef.current = null;
    setSelectedTeamId("");
    setClaim(false);
    setUnknown(null);
    setSnapshot(null);
    publishGuard(false);
    onCloseRef.current();
  }

  function runWrite<T>(
    kind: "share" | "unshare",
    label: string,
    teamId: string | null,
    submitted: unknown,
    request: (port: TeamSeriesServicePort, signal: AbortSignal) => Promise<T>,
    applyAck: (ack: T) => { message: string; affectedTeamId: string },
  ): void {
    const owner = scopeRef.current;
    const ownerPort = owner.services.teamSeries;
    if (!owner.active || ownerPort === undefined || writeRef.current !== null || unknownRef.current !== null) return;
    const lease = collaborationWritePermit.acquire(owner.collaborationWriteScope, {
      kind: "series-share",
      label,
    });
    if (lease === null) {
      setError(permitSnapshot.phase === "unknown"
        ? "另一项团队协作写操作仍待明确放弃；当前操作没有发送。"
        : "另一项团队协作写操作正在进行；当前操作没有发送。");
      return;
    }
    const attempt = ++attemptRef.current;
    const controller = new AbortController();
    writeRef.current = { attempt, controller, lease };
    setPendingLabel(label);
    setError(null);
    publishGuard(true);
    const isCurrent = () => mountedRef.current
      && scopeRef.current.actorId === owner.actorId
      && scopeRef.current.sessionGeneration === owner.sessionGeneration
      && scopeRef.current.services === owner.services
      && scopeRef.current.seriesId === owner.seriesId
      && scopeRef.current.port === owner.port
      && sameTeamCollaborationWriteScope(scopeRef.current.collaborationWriteScope, owner.collaborationWriteScope)
      && writeRef.current?.attempt === attempt
      && writeRef.current.lease.id === lease.id
      && collaborationWritePermit.isLeaseCurrent(lease);

    let pending: Promise<T>;
    try {
      pending = request(ownerPort, controller.signal);
    } catch (cause) {
      pending = Promise.reject(cause);
    }
    void pending.then((ack) => {
      if (!isCurrent()) return;
      const confirmation = applyAck(ack);
      collaborationWritePermit.complete(lease);
      writeRef.current = null;
      setPendingLabel(null);
      selectedTeamIdRef.current = "";
      claimRef.current = false;
      unknownRef.current = null;
      setSelectedTeamId("");
      setClaim(false);
      setUnknown(null);
      setSnapshot(null);
      publishGuard(false);
      onSeriesChangedRef.current(confirmation.affectedTeamId, owner.seriesId);
      onConfirmedRef.current(confirmation.message);
    }).catch((cause: unknown) => {
      if (!isCurrent()) return;
      writeRef.current = null;
      setPendingLabel(null);
      if (cause instanceof ApiError && cause.status === 401) {
        collaborationWritePermit.complete(lease);
        publishGuard();
        onUnauthorizedRef.current();
        return;
      }
      if (isUnknown(cause)) {
        collaborationWritePermit.markUnknown(lease);
        const frozen: UnknownShareAction = { kind, label, seriesId: owner.seriesId, teamId, submitted, attempt, leaseId: lease.id };
        unknownRef.current = frozen;
        setUnknown(frozen);
        setError("操作结果无法确认。系统不会自动重试；当前事实读取不能确认原操作归属。请明确放弃本地状态后再发起新的独立操作。");
      } else {
        collaborationWritePermit.complete(lease);
        setError(errorMessage(cause));
      }
      publishGuard();
    }).finally(() => {
      if (isCurrent()) {
        writeRef.current = null;
        setPendingLabel(null);
        publishGuard();
      }
    });
  }

  function share(): void {
    const selected = teams.find((item) => item.id === selectedTeamIdRef.current);
    const teamId = selectedTeamIdRef.current;
    const ownerPort = scopeRef.current.services.teamSeries;
    if (!canShare || ownerPort === undefined || teamId === "" || selected === undefined || teamsLoading || unknownRef.current !== null) return;
    const teamName = selected.name || "未命名团队";
    const consequence = claimRef.current
      ? "分享会清除这部剧集所有章节编辑锁，并将制作负责人设为你。"
      : "分享会清除这部剧集所有章节编辑锁，并保留服务器当前的认领状态。";
    if (!window.confirm(`确认将《${series.name}》分享到${teamName}？${consequence}`)) return;
    const claimValue = claimRef.current;
    runWrite(
      "share",
      "分享剧集",
      teamId,
      { seriesId: series.id, teamId, claim: claimValue },
      (port, signal) => port.shareSeries(series.id, teamId, claimValue, signal),
      (ack) => ({
        message: `《${series.name}》已分享到团队「${ack.team_name}」。`,
        affectedTeamId: ack.team_id,
      }),
    );
  }

  function unshare(): void {
    const teamId = series.team_id;
    if (!canUnshare || teamId === null || unknownRef.current !== null) return;
    const teamName = series.team_name || "当前团队";
    if (!window.confirm(`确认将《${series.name}》从${teamName}撤回并恢复为个人剧集？这会清除此剧集所有章节编辑锁；即使你已离开团队仍可撤回。`)) return;
    runWrite(
      "unshare",
      "撤回剧集分享",
      teamId,
      { seriesId: series.id, teamId },
      (port, signal) => port.unshareSeries(series.id, signal),
      () => ({ message: `《${series.name}》已撤回分享。`, affectedTeamId: teamId }),
    );
  }

  async function readCurrentFact(): Promise<void> {
    const frozen = unknownRef.current;
    if (frozen === null || checking) return;
    const sequence = ++readSequenceRef.current;
    const controller = new AbortController();
    readControllerRef.current?.abort();
    readControllerRef.current = controller;
    setChecking(true);
    setError(null);
    const owner = scopeRef.current;
    const isCurrent = () => mountedRef.current
      && !controller.signal.aborted
      && readSequenceRef.current === sequence
      && unknownRef.current === frozen
      && scopeRef.current.active
      && scopeRef.current.actorId === owner.actorId
      && scopeRef.current.sessionGeneration === owner.sessionGeneration
      && scopeRef.current.services === owner.services
      && sameTeamCollaborationWriteScope(scopeRef.current.collaborationWriteScope, owner.collaborationWriteScope)
      && scopeRef.current.seriesId === frozen.seriesId;
    try {
      const currentSeries = await owner.services.listSeries(controller.signal);
      if (!isCurrent()) return;
      const current = currentSeries.find((item) => item.id === series.id);
      setSnapshot(current === undefined
        ? { exists: false, teamId: null, teamName: null }
        : { exists: true, teamId: current.team_id, teamName: current.team_name });
    } catch (cause) {
      if (!isCurrent() || isAbort(cause)) return;
      if (cause instanceof ApiError && cause.status === 401) onUnauthorizedRef.current();
      else setError(`只读核对未完成；原操作仍待确认。${errorMessage(cause)}`);
    } finally {
      if (isCurrent()) {
        readControllerRef.current = null;
        setChecking(false);
      }
    }
  }

  function abandonUnknown(): void {
    if (unknownRef.current === null || writeRef.current !== null) return;
    if (!window.confirm("放弃这次本地核对状态并清除当前选择？这不会撤销服务器可能已完成的操作。")) return;
    if (!collaborationWritePermit.abandonUnknown(collaborationWriteScope, unknownRef.current.leaseId)) return;
    unknownRef.current = null;
    selectedTeamIdRef.current = "";
    claimRef.current = false;
    setUnknown(null);
    setSnapshot(null);
    setSelectedTeamId("");
    setClaim(false);
    setError(null);
    publishGuard(false);
  }

  if (!active) return null;

  return (
    <div className="series-dialog-backdrop" role="presentation">
      <section aria-labelledby="series-share-title" aria-modal="true" className="series-dialog series-share-dialog" role="dialog">
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">团队协作</p>
            <h2 id="series-share-title">{canUnshare ? "管理团队共享" : "分享到团队"}</h2>
          </div>
          <button aria-label="关闭团队共享" className="icon-button" disabled={pendingLabel !== null} onClick={close} type="button">×</button>
        </div>
        <p className="team-series-share-name">《{series.name}》</p>
        {error !== null && <p className="series-dialog-error" role="alert">{error}</p>}
        {pendingLabel !== null && <p className="series-dialog-status" role="status">正在执行：{pendingLabel}</p>}
        {collaborationLocked && pendingLabel === null && unknown === null && (
          <p className="series-dialog-status" role="status">
            {permitSnapshot.phase === "unknown"
              ? "另一项团队协作操作结果待核对；请先在对应页面明确放弃本地状态。"
              : `另一项团队协作操作正在进行：${permitSnapshot.owner?.label ?? "请稍候"}。`}
          </p>
        )}

        {canShare && (
          <div className="series-share-form">
            {teamsLoading && <p className="series-dialog-status" role="status">正在读取所属团队…</p>}
            {teamsError !== null && (
              <div className="series-dialog-error" role="alert">
                <span>{teamsError}</span>
                <button className="secondary-button" disabled={pendingLabel !== null} onClick={() => {
                  setTeamReadKey((value) => value + 1);
                }} type="button">重试读取团队</button>
              </div>
            )}
            <label htmlFor="series-share-team">分享给团队</label>
            <select
              disabled={teamsLoading || teamsError !== null || pendingLabel !== null || unknown !== null || collaborationLocked}
              id="series-share-team"
              onChange={(event) => setSelection(event.currentTarget.value)}
              value={selectedTeamId}
            >
              <option value="">选择所属团队</option>
              {teams.map((item) => <option key={item.id} value={item.id}>{item.name || "未命名团队"}</option>)}
            </select>
            <label className="series-share-claim-option">
              <input
                checked={claim}
                disabled={pendingLabel !== null || unknown !== null || collaborationLocked}
                onChange={(event) => setClaimValue(event.currentTarget.checked)}
                type="checkbox"
              />
              同时由我认领
            </label>
            {teams.length === 0 && !teamsLoading && teamsError === null && (
              <p className="series-dialog-status" role="status">当前账号没有可分享的所属团队。</p>
            )}
            <p className="series-dialog-status">
              分享后，目标剧集的章节编辑锁会被清除。认领状态按上方选项提交。
            </p>
          </div>
        )}

        {canUnshare && (
          <div className="series-share-form">
            <p className="series-dialog-status">当前归属：{series.team_name || "团队剧集"}。作者即使离队，仍可撤回自己的分享。</p>
            <button className="danger-button" disabled={pendingLabel !== null || unknown !== null || collaborationLocked} onClick={unshare} type="button">撤回分享</button>
          </div>
        )}

        {unknown !== null && (
          <section aria-label="待核对的团队共享操作" className="series-share-unknown" role="status">
            <strong>“{unknown.label}”结果待核对</strong>
            <p>不会自动重发。下方只读核对显示当前剧集事实，不能确认原操作是否由本次请求完成。</p>
            {snapshot !== null && (
              <p>
                {snapshot.exists
                  ? snapshot.teamId === null
                    ? "当前剧集列表显示：个人剧集。"
                    : `当前剧集列表显示：团队共享至${snapshot.teamName || "未命名团队"}。`
                  : "当前剧集列表没有返回这部剧集；这不能证明写请求失败。"}
              </p>
            )}
            <div className="series-dialog-actions">
              <button className="secondary-button" disabled={checking} onClick={() => { void readCurrentFact(); }} type="button">
                {checking ? "正在只读核对…" : "读取当前剧集事实"}
              </button>
              <button className="danger-button" onClick={abandonUnknown} type="button">明确放弃本地状态</button>
            </div>
          </section>
        )}

        <div className="series-dialog-actions">
          {canShare && (
            <button
              className="primary-button"
              disabled={pendingLabel !== null || teamsLoading || teamsError !== null || selectedTeamId === "" || unknown !== null || collaborationLocked}
              onClick={share}
              type="button"
            >
              分享剧集
            </button>
          )}
          <button className="secondary-button" disabled={pendingLabel !== null} onClick={close} type="button">关闭</button>
        </div>
      </section>
    </div>
  );
}
