import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../../shared/api/errors";
import type { WorkspaceServices } from "../../shared/api/services";
import {
  type TeamDetail,
  type TeamDirectoryEntry,
  type TeamInvite,
  type TeamInviteReceipt,
  type TeamJoinReceipt,
  type TeamMember,
  type TeamPermission,
  type TeamRole,
  type TeamRoleReceipt,
} from "../../shared/api/teamManagement";
import { TeamDetailPanel, type TeamDetailTab } from "./TeamDetailPanel";
import { TeamDirectoryPanel } from "./TeamDirectoryPanel";
import "./TeamsPage.css";

interface TeamScope {
  userId: string;
  sessionGeneration: number;
  services: WorkspaceServices;
  epoch: number;
}

interface TeamSnapshotSummary {
  kind: "directory" | "team" | "invites";
  teamName?: string;
  teamCount?: number;
  memberCount?: number;
  myRole?: string;
  inviteCount?: number;
  inviteStatuses?: string[];
}

interface TeamsPageProps {
  services: WorkspaceServices;
  userId: string;
  sessionGeneration: number;
  guardScopeKey: string;
  onUnauthorized(): void;
  onBack(): void;
  onGuardChange(scopeKey: string, dirty: boolean, inFlight: boolean): void;
  onDirectoryChanged(): void;
}

interface Drafts {
  createName: string;
  joinCode: string;
  teamName: string;
  inviteCount: string;
  roleValues: Record<string, string>;
  permissionValues: Record<string, TeamPermission[]>;
}

interface WriteOperation<T> {
  label: string;
  teamId: string | null;
  targetId: string | null;
  submitted: unknown;
  request(port: NonNullable<WorkspaceServices["teamManagement"]>, signal: AbortSignal): Promise<T>;
  applyAck?(ack: T): void;
  refresh?(): void;
}

function initialDrafts(): Drafts {
  return {
    createName: "",
    joinCode: "",
    teamName: "",
    inviteCount: "1",
    roleValues: {},
    permissionValues: {},
  };
}

function samePermissions(left: TeamPermission[], right: TeamPermission[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function hasDrafts(drafts: Drafts, detail: TeamDetail | null): boolean {
  if (drafts.createName.length > 0 || drafts.joinCode.length > 0 || drafts.inviteCount !== "1") return true;
  if (detail === null) return false;
  if (drafts.teamName !== detail.name) return true;
  for (const member of detail.members) {
    if (drafts.roleValues[member.user_id] !== undefined && drafts.roleValues[member.user_id] !== member.role) return true;
    const permissions = drafts.permissionValues[member.user_id];
    if (permissions !== undefined && !samePermissions(permissions, member.permissions)) return true;
  }
  return false;
}

function apiFailureMessage(error: unknown, writing: boolean): string {
  if (!(error instanceof ApiError)) return writing ? "操作结果暂时无法确认，请只读核对。" : "读取团队信息失败，请重试。";
  if (error.status === 401) return "当前登录状态已失效，正在返回登录页。";
  if (error.status === 403) return "当前账号无权执行此操作；权限可能已变化。";
  if (error.status === 404) return "目标团队、成员或邀请码已不存在，请刷新后确认。";
  if (error.status === 422) return "提交内容未通过服务端校验，请检查表单。";
  if (error.status === 429) return "加入请求过于频繁，请稍后再试。";
  if (error.status === 503) return "加入服务当前不可用，系统未提供可用的加入配额。";
  if (error.kind === "timeout" || error.kind === "network" || error.kind === "invalid-response") {
    return writing ? "操作结果暂时无法确认，请只读核对。" : "服务器返回内容异常，请重新读取。";
  }
  if (error.status === 400) return "服务端拒绝了此操作，请检查团队状态后重试。";
  return writing ? "操作结果暂时无法确认，请只读核对。" : "读取团队信息失败，请重试。";
}

function isUnknownWrite(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response" || error.kind === "aborted") {
    return true;
  }
  return error.status !== null && error.status >= 500;
}

export function TeamsPage({
  services,
  userId,
  sessionGeneration,
  guardScopeKey,
  onUnauthorized,
  onBack,
  onGuardChange,
  onDirectoryChanged,
}: TeamsPageProps) {
  const port = services.teamManagement;
  const [teams, setTeams] = useState<TeamDirectoryEntry[]>([]);
  const [teamsLoading, setTeamsLoading] = useState(false);
  const [selectedTeamId, setSelectedTeamId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TeamDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [tab, setTab] = useState<TeamDetailTab>("members");
  const [invites, setInvites] = useState<TeamInvite[]>([]);
  const [inviteLoading, setInviteLoading] = useState(false);
  const [inviteReceipt, setInviteReceipt] = useState<TeamInviteReceipt | null>(null);
  const inviteReceiptRef = useRef<TeamInviteReceipt | null>(null);
  const [drafts, setDrafts] = useState<Drafts>(initialDrafts);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [copyNotice, setCopyNotice] = useState<string | null>(null);
  const [pendingLabel, setPendingLabel] = useState<string | null>(null);
  const [unknownLabel, setUnknownLabel] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<TeamSnapshotSummary | null>(null);

  const mountedRef = useRef(false);
  const scopeEpochRef = useRef(0);
  const scopeRef = useRef<TeamScope>({ userId, sessionGeneration, services, epoch: 0 });
  const draftsRef = useRef(drafts);
  const detailRef = useRef(detail);
  const dirtyRef = useRef(false);
  const publishedGuardRef = useRef({ scopeKey: guardScopeKey, inFlight: false });
  const writeRef = useRef<{
    attempt: number;
    epoch: number;
    controller: AbortController;
    label: string;
    teamId: string | null;
    targetId: string | null;
    submitted: unknown;
  } | null>(null);
  const unknownRef = useRef<{
    attempt: number;
    epoch: number;
    label: string;
    teamId: string | null;
    targetId: string | null;
    submitted: unknown;
    draft: Drafts;
  } | null>(null);
  const snapshotAttemptRef = useRef(0);
  const snapshotControllerRef = useRef<AbortController | null>(null);
  const copyAttemptRef = useRef(0);
  const attemptRef = useRef(0);
  const readSequenceRef = useRef(0);
  const inviteViewGenerationRef = useRef(0);
  const readControllersRef = useRef(new Map<string, { sequence: number; controller: AbortController }>());
  const onUnauthorizedRef = useRef(onUnauthorized);
  const onBackRef = useRef(onBack);
  const onGuardChangeRef = useRef(onGuardChange);
  const onDirectoryChangedRef = useRef(onDirectoryChanged);
  onUnauthorizedRef.current = onUnauthorized;
  onBackRef.current = onBack;
  onGuardChangeRef.current = onGuardChange;
  onDirectoryChangedRef.current = onDirectoryChanged;
  draftsRef.current = drafts;
  detailRef.current = detail;

  const priorScope = scopeRef.current;
  const identityChanged = priorScope.userId !== userId
    || priorScope.sessionGeneration !== sessionGeneration
    || priorScope.services !== services;
  if (identityChanged) {
    const nextEpoch = ++scopeEpochRef.current;
    scopeRef.current = { userId, sessionGeneration, services, epoch: nextEpoch };
  }

  const currentScope = scopeRef.current;
  const isCurrent = useCallback((epoch: number) => mountedRef.current && scopeRef.current.epoch === epoch,
    []);

  function publishGuard(
    nextDirty: boolean,
    epoch = scopeRef.current.epoch,
    inFlight = writeRef.current !== null,
  ): void {
    if (epoch !== scopeRef.current.epoch) return;
    const sameScope = publishedGuardRef.current.scopeKey === guardScopeKey;
    if (sameScope && dirtyRef.current === nextDirty && publishedGuardRef.current.inFlight === inFlight) return;
    dirtyRef.current = nextDirty;
    publishedGuardRef.current = { scopeKey: guardScopeKey, inFlight };
    onGuardChangeRef.current(guardScopeKey, nextDirty, inFlight);
  }

  function hasPageGuard(): boolean {
    return hasDrafts(draftsRef.current, detailRef.current)
      || writeRef.current !== null
      || unknownRef.current !== null
      || inviteReceiptRef.current !== null;
  }

  function syncGuard(): void {
    publishGuard(hasPageGuard());
  }

  function changeInviteReceipt(receipt: TeamInviteReceipt | null): void {
    copyAttemptRef.current += 1;
    inviteReceiptRef.current = receipt;
    setInviteReceipt(receipt);
    publishGuard(hasPageGuard());
  }

  function updateDrafts(update: (current: Drafts) => Drafts): void {
    const next = update(draftsRef.current);
    draftsRef.current = next;
    setDrafts(next);
    publishGuard(hasPageGuard());
  }

  function abortReads(): void {
    for (const request of readControllersRef.current.values()) request.controller.abort();
    readControllersRef.current.clear();
  }

  function retireInviteRead(teamId: string | null): void {
    if (teamId !== null) {
      const channel = `invites:${teamId}`;
      const request = readControllersRef.current.get(channel);
      if (request !== undefined) {
        readControllersRef.current.delete(channel);
        request.controller.abort();
      }
    }
    setInviteLoading(false);
  }

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortReads();
      snapshotControllerRef.current?.abort();
      writeRef.current?.controller.abort();
    };
  }, []);

  useLayoutEffect(() => {
    if (!identityChanged) return;
    abortReads();
    copyAttemptRef.current += 1;
    snapshotControllerRef.current?.abort();
    snapshotControllerRef.current = null;
    writeRef.current?.controller.abort();
    writeRef.current = null;
    unknownRef.current = null;
    dirtyRef.current = false;
    setTeams([]);
    setTeamsLoading(false);
    setSelectedTeamId(null);
    detailRef.current = null;
    setDetail(null);
    setDetailLoading(false);
    setTab("members");
    setInvites([]);
    setInviteLoading(false);
    inviteReceiptRef.current = null;
    setInviteReceipt(null);
    const cleanDrafts = initialDrafts();
    draftsRef.current = cleanDrafts;
    setDrafts(cleanDrafts);
    setDirectoryError(null);
    setDetailError(null);
    setRefreshWarning(null);
    setNotice(null);
    setCopyNotice(null);
    setPendingLabel(null);
    setUnknownLabel(null);
    setSnapshot(null);
    publishedGuardRef.current = { scopeKey: guardScopeKey, inFlight: false };
    onGuardChangeRef.current(guardScopeKey, false, false);
  }, [guardScopeKey, identityChanged, services, sessionGeneration, userId]);

  const loadTeams = useCallback(async (afterWrite = false): Promise<void> => {
    const owner = scopeRef.current;
    const ownerPort = owner.services.teamManagement;
    if (ownerPort === undefined) {
      setTeams([]);
      setTeamsLoading(false);
      return;
    }
    const channel = "directory";
    const previous = readControllersRef.current.get(channel);
    previous?.controller.abort();
    const sequence = ++readSequenceRef.current;
    const controller = new AbortController();
    readControllersRef.current.set(channel, { sequence, controller });
    setTeamsLoading(true);
    if (!afterWrite) setDirectoryError(null);
    try {
      const result = await ownerPort.listTeams(controller.signal);
      if (!isCurrent(owner.epoch) || readControllersRef.current.get(channel)?.sequence !== sequence) return;
      setTeams(result);
      setDirectoryError(null);
      if (afterWrite) setRefreshWarning(null);
    } catch (error) {
      if (!isCurrent(owner.epoch) || readControllersRef.current.get(channel)?.sequence !== sequence) return;
      if (error instanceof ApiError && error.kind === "aborted") return;
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      const message = apiFailureMessage(error, false);
      if (afterWrite) setRefreshWarning(`操作已确认，但团队列表暂时无法刷新。${message}`);
      else setDirectoryError(message);
    } finally {
      if (isCurrent(owner.epoch) && readControllersRef.current.get(channel)?.sequence === sequence) {
        readControllersRef.current.delete(channel);
        setTeamsLoading(false);
      }
    }
  }, [isCurrent]);

  const loadDetail = useCallback(async (teamId: string, afterWrite = false): Promise<void> => {
    const owner = scopeRef.current;
    const ownerPort = owner.services.teamManagement;
    if (ownerPort === undefined) return;
    const channel = `detail:${teamId}`;
    const previous = readControllersRef.current.get(channel);
    previous?.controller.abort();
    const sequence = ++readSequenceRef.current;
    const controller = new AbortController();
    readControllersRef.current.set(channel, { sequence, controller });
    setDetailLoading(true);
    if (!afterWrite) setDetailError(null);
    try {
      const result = await ownerPort.getTeam(teamId, controller.signal);
      if (!isCurrent(owner.epoch) || readControllersRef.current.get(channel)?.sequence !== sequence) return;
      const firstDetailForSelection = detailRef.current?.id !== teamId;
      detailRef.current = result;
      setDetail(result);
      if (firstDetailForSelection) {
        updateDrafts((current) => ({ ...current, teamName: result.name }));
      }
      setDetailError(null);
      if (afterWrite) setRefreshWarning(null);
    } catch (error) {
      if (!isCurrent(owner.epoch) || readControllersRef.current.get(channel)?.sequence !== sequence) return;
      if (error instanceof ApiError && error.kind === "aborted") return;
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      const message = apiFailureMessage(error, false);
      if (afterWrite) setRefreshWarning(`操作已确认，但团队详情暂时无法刷新。${message}`);
      else setDetailError(message);
    } finally {
      if (isCurrent(owner.epoch) && readControllersRef.current.get(channel)?.sequence === sequence) {
        readControllersRef.current.delete(channel);
        setDetailLoading(false);
      }
    }
  }, [isCurrent]);

  const loadInvites = useCallback(async (teamId: string, snapshotOnly = false): Promise<void> => {
    const owner = scopeRef.current;
    const ownerPort = owner.services.teamManagement;
    if (ownerPort === undefined) return;
    const viewGeneration = inviteViewGenerationRef.current;
    const channel = `invites:${teamId}`;
    const previous = readControllersRef.current.get(channel);
    previous?.controller.abort();
    const sequence = ++readSequenceRef.current;
    const controller = new AbortController();
    readControllersRef.current.set(channel, { sequence, controller });
    setInviteLoading(true);
    const isCurrentInviteRead = () => isCurrent(owner.epoch)
      && inviteViewGenerationRef.current === viewGeneration
      && readControllersRef.current.get(channel)?.sequence === sequence;
    try {
      const result = await ownerPort.listInvites(teamId, controller.signal);
      if (!isCurrentInviteRead()) return;
      if (snapshotOnly) {
        setSnapshot({ kind: "invites", inviteCount: result.length, inviteStatuses: result.map(({ status }) => status) });
      } else {
        setInvites(result);
      }
      setDetailError(null);
    } catch (error) {
      if (!isCurrentInviteRead()) return;
      if (error instanceof ApiError && error.kind === "aborted") return;
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorizedRef.current();
        return;
      }
      setDetailError(apiFailureMessage(error, false));
    } finally {
      if (isCurrentInviteRead()) {
        readControllersRef.current.delete(channel);
        setInviteLoading(false);
      }
    }
  }, [isCurrent]);

  useEffect(() => {
    void loadTeams();
    return () => abortReads();
  }, [currentScope.epoch, loadTeams]);

  useLayoutEffect(() => {
    syncGuard();
  }, [detail, drafts, guardScopeKey, inviteReceipt, sessionGeneration, unknownLabel]);

  async function runWrite<T>(operation: WriteOperation<T>): Promise<void> {
    const owner = scopeRef.current;
    const ownerPort = owner.services.teamManagement;
    if (ownerPort === undefined || writeRef.current !== null || unknownRef.current !== null) return;
    const attempt = ++attemptRef.current;
    const controller = new AbortController();
    writeRef.current = {
      attempt,
      epoch: owner.epoch,
      controller,
      label: operation.label,
      teamId: operation.teamId,
      targetId: operation.targetId,
      submitted: operation.submitted,
    };
    setPendingLabel(operation.label);
    setDetailError(null);
    setDirectoryError(null);
    setRefreshWarning(null);
    publishGuard(true, owner.epoch, true);
    try {
      const request = operation.request(ownerPort, controller.signal);
      const ack = await request;
      if (!isCurrent(owner.epoch) || writeRef.current?.attempt !== attempt) return;
      writeRef.current = null;
      setPendingLabel(null);
      setUnknownLabel(null);
      unknownRef.current = null;
      setNotice(`${operation.label}已确认。`);
      operation.applyAck?.(ack);
      operation.refresh?.();
      publishGuard(hasPageGuard(), owner.epoch);
    } catch (error) {
      if (!isCurrent(owner.epoch) || writeRef.current?.attempt !== attempt) return;
      if (error instanceof ApiError && error.status === 401) {
        writeRef.current = null;
        setPendingLabel(null);
        onUnauthorizedRef.current();
        return;
      }
      if (isUnknownWrite(error)) {
        unknownRef.current = {
          attempt,
          epoch: owner.epoch,
          label: operation.label,
          teamId: operation.teamId,
          targetId: operation.targetId,
          submitted: operation.submitted,
          draft: {
            ...draftsRef.current,
            roleValues: { ...draftsRef.current.roleValues },
            permissionValues: Object.fromEntries(Object.entries(draftsRef.current.permissionValues)
              .map(([memberId, permissions]) => [memberId, [...permissions]])),
          },
        };
        setUnknownLabel(operation.label);
        setDetailError("这次写操作的结果无法确认。请先进行只读核对；系统不会自动重试。放弃本地状态也不会撤销服务器可能已完成的操作。");
      } else {
        setDetailError(apiFailureMessage(error, true));
      }
    } finally {
      if (isCurrent(owner.epoch) && writeRef.current?.attempt === attempt) {
        writeRef.current = null;
        setPendingLabel(null);
        publishGuard(
          hasPageGuard(),
          owner.epoch,
        );
      }
    }
  }

  function resetDrafts(): void {
    const clean = { ...initialDrafts(), teamName: detailRef.current?.name ?? "" };
    draftsRef.current = clean;
    setDrafts(clean);
  }

  function changeTab(nextTab: TeamDetailTab): void {
    if (nextTab === tab || !confirmDiscard()) return;
    inviteViewGenerationRef.current += 1;
    if (tab === "invites") retireInviteRead(selectedTeamId);
    setTab(nextTab);
  }

  function confirmDiscard(): boolean {
    if (!dirtyRef.current) return true;
    if (writeRef.current !== null) {
      setDetailError("写操作仍在请求中。请等待服务器返回后再切换团队或管理面板；离开工作台仍可通过全局退出确认处理。 ");
      return false;
    }
    const message = unknownRef.current !== null
      ? "当前有尚未确认的团队操作或草稿。放弃本地核对状态不会撤销服务器可能已完成的操作，继续离开吗？"
      : inviteReceiptRef.current !== null
        ? "当前有尚未关闭的邀请码回执或未保存的团队草稿。离开会丢弃页面状态，但已生成的邀请码不会撤销。继续吗？"
        : "当前有未保存的团队草稿，离开会丢弃这些页面状态。继续吗？";
    if (!window.confirm(message)) return false;
    unknownRef.current = null;
    setUnknownLabel(null);
    resetDrafts();
    setSnapshot(null);
    changeInviteReceipt(null);
    publishGuard(false);
    return true;
  }

  function selectTeam(teamId: string): void {
    if (teamId === selectedTeamId) return;
    if (!confirmDiscard()) return;
    abortReads();
    copyAttemptRef.current += 1;
    resetDrafts();
    setSelectedTeamId(teamId);
    detailRef.current = null;
    setDetail(null);
    setInvites([]);
    inviteReceiptRef.current = null;
    setInviteReceipt(null);
    setTab("members");
    setDirectoryError(null);
    setDetailError(null);
    setNotice(null);
    setCopyNotice(null);
    setSnapshot(null);
    const picked = teams.find((item) => item.id === teamId);
    updateDrafts((current) => ({ ...current, teamName: picked?.name ?? "" }));
    void loadDetail(teamId);
  }

  function goBackToDirectory(): void {
    if (!confirmDiscard()) return;
    abortReads();
    copyAttemptRef.current += 1;
    setSelectedTeamId(null);
    detailRef.current = null;
    setDetail(null);
    setInvites([]);
    inviteReceiptRef.current = null;
    setInviteReceipt(null);
    setTab("members");
    setDetailError(null);
    setNotice(null);
    setCopyNotice(null);
    setSnapshot(null);
    resetDrafts();
    publishGuard(false);
  }

  function createTeam(): void {
    const name = draftsRef.current.createName;
    void runWrite({
      label: "创建团队",
      teamId: null,
      targetId: null,
      submitted: { name },
      request: (api, signal) => api.createTeam(name, userId, signal),
      applyAck: (created) => {
        updateDrafts((current) => ({ ...current, createName: "", teamName: created.name }));
        setSelectedTeamId(created.id);
        detailRef.current = null;
        setDetail(null);
        setTab("members");
        onDirectoryChangedRef.current();
        void loadTeams(true);
        void loadDetail(created.id, true);
      },
    });
  }

  function joinTeam(): void {
    const code = draftsRef.current.joinCode;
    void runWrite({
      label: "加入团队",
      teamId: null,
      targetId: null,
      submitted: { code },
      request: (api, signal) => api.joinTeam(code, signal),
      applyAck: (joined: TeamJoinReceipt) => {
        updateDrafts((current) => ({ ...current, joinCode: "", teamName: joined.team_name }));
        setSelectedTeamId(joined.team_id);
        detailRef.current = null;
        setDetail(null);
        setTab("members");
        onDirectoryChangedRef.current();
        void loadTeams(true);
        void loadDetail(joined.team_id, true);
      },
    });
  }

  function saveTeamName(): void {
    if (selectedTeamId === null || detail === null) return;
    const teamId = selectedTeamId;
    const name = draftsRef.current.teamName;
    void runWrite({
      label: "修改团队名称",
      teamId,
      targetId: null,
      submitted: { name, expectedName: detail.name },
      request: (api, signal) => api.updateTeam(teamId, { name }, detail.name, signal),
      applyAck: (ack) => {
        setDetail((current) => current?.id === teamId ? { ...current, name: ack.name } : current);
        updateDrafts((current) => ({ ...current, teamName: ack.name }));
        onDirectoryChangedRef.current();
      },
      refresh: () => { void loadTeams(true); void loadDetail(teamId, true); },
    });
  }

  function setRoleDraft(memberId: string, value: string): void {
    updateDrafts((current) => ({ ...current, roleValues: { ...current.roleValues, [memberId]: value } }));
  }

  function setPermissionDraft(memberId: string, permission: TeamPermission, checked: boolean): void {
    updateDrafts((current) => {
      const member = detailRef.current?.members.find((item) => item.user_id === memberId);
      const existing = current.permissionValues[memberId] ?? member?.permissions ?? [];
      const permissions = checked
        ? existing.includes(permission) ? existing : [...existing, permission]
        : existing.filter((item) => item !== permission);
      return { ...current, permissionValues: { ...current.permissionValues, [memberId]: permissions } };
    });
  }

  function saveMemberRole(member: TeamMember): void {
    if (selectedTeamId === null) return;
    const requested = draftsRef.current.roleValues[member.user_id] ?? member.role;
    if (requested !== "admin" && requested !== "member") return;
    const teamId = selectedTeamId;
    void runWrite({
      label: "更新成员角色",
      teamId,
      targetId: member.user_id,
      submitted: { role: requested, currentRole: member.role, currentPermissions: [...member.permissions] },
      request: (api, signal) => api.setMemberRole(
        teamId, member.user_id, requested as TeamRole, member.role, member.permissions, signal,
      ),
      applyAck: (ack: TeamRoleReceipt) => {
        setDetail((current) => current?.id === teamId ? {
          ...current,
          members: current.members.map((item) => item.user_id === member.user_id
            ? { ...item, role: ack.role, permissions: ack.permissions }
            : item),
        } : current);
        updateDrafts((current) => ({
          ...current,
          roleValues: { ...current.roleValues, [member.user_id]: ack.role },
          permissionValues: { ...current.permissionValues, [member.user_id]: ack.permissions },
        }));
        onDirectoryChangedRef.current();
      },
      refresh: () => { void loadTeams(true); void loadDetail(teamId, true); },
    });
  }

  function saveMemberPermissions(member: TeamMember): void {
    if (selectedTeamId === null) return;
    const permissions = draftsRef.current.permissionValues[member.user_id] ?? member.permissions;
    const teamId = selectedTeamId;
    void runWrite({
      label: "更新成员权限",
      teamId,
      targetId: member.user_id,
      submitted: { permissions: [...permissions] },
      request: (api, signal) => api.setMemberPermissions(teamId, member.user_id, permissions, signal),
      applyAck: (ack) => {
        setDetail((current) => current?.id === teamId ? {
          ...current,
          members: current.members.map((item) => item.user_id === member.user_id
            ? { ...item, permissions: ack.permissions }
            : item),
        } : current);
        updateDrafts((current) => ({
          ...current,
          permissionValues: { ...current.permissionValues, [member.user_id]: ack.permissions },
        }));
        onDirectoryChangedRef.current();
      },
      refresh: () => { void loadTeams(true); void loadDetail(teamId, true); },
    });
  }

  function removeMember(memberId: string): void {
    if (selectedTeamId === null || !confirmDiscard()) return;
    if (!window.confirm("移除这位成员？此操作会立即修改团队成员关系。")) return;
    const teamId = selectedTeamId;
    void runWrite({
      label: "移除成员",
      teamId,
      targetId: memberId,
      submitted: {},
      request: (api, signal) => api.removeMember(teamId, memberId, signal),
      applyAck: () => {
        onDirectoryChangedRef.current();
        void loadTeams(true);
        void loadDetail(teamId, true);
      },
    });
  }

  function leaveTeam(): void {
    if (selectedTeamId === null || !confirmDiscard()) return;
    if (!window.confirm("退出此团队？退出后将失去该团队的成员权限。")) return;
    const teamId = selectedTeamId;
    void runWrite({
      label: "退出团队",
      teamId,
      targetId: null,
      submitted: {},
      request: (api, signal) => api.leaveTeam(teamId, signal),
      applyAck: () => {
        setSelectedTeamId(null);
        detailRef.current = null;
        setDetail(null);
        setInvites([]);
        resetDrafts();
        onDirectoryChangedRef.current();
        void loadTeams(true);
      },
    });
  }

  function deleteTeam(): void {
    if (selectedTeamId === null || !confirmDiscard()) return;
    if (!window.confirm("解散团队？此操作不可撤回。")) return;
    const teamId = selectedTeamId;
    void runWrite({
      label: "解散团队",
      teamId,
      targetId: null,
      submitted: {},
      request: (api, signal) => api.deleteTeam(teamId, signal),
      applyAck: () => {
        setSelectedTeamId(null);
        detailRef.current = null;
        setDetail(null);
        setInvites([]);
        resetDrafts();
        onDirectoryChangedRef.current();
        void loadTeams(true);
      },
    });
  }

  function createInvites(): void {
    if (selectedTeamId === null) return;
    const count = Number(draftsRef.current.inviteCount);
    const teamId = selectedTeamId;
    void runWrite({
      label: "生成邀请码",
      teamId,
      targetId: null,
      submitted: { count },
      request: (api, signal) => api.createInvites(teamId, count, signal),
      applyAck: (receipt) => {
        changeInviteReceipt(receipt);
        updateDrafts((current) => ({ ...current, inviteCount: "1" }));
        void loadInvites(teamId);
      },
    });
  }

  function revokeInvite(inviteId: string): void {
    if (selectedTeamId === null || !window.confirm("作废此邀请码？已使用的邀请码无法通过此操作撤销成员权益。")) return;
    const teamId = selectedTeamId;
    void runWrite({
      label: "作废邀请码",
      teamId,
      targetId: inviteId,
      submitted: {},
      request: (api, signal) => api.revokeInvite(teamId, inviteId, signal),
      applyAck: () => { void loadInvites(teamId); },
    });
  }

  async function copyInviteCode(code: string): Promise<void> {
    const owner = scopeRef.current;
    const attempt = ++copyAttemptRef.current;
    const currentCopy = () => isCurrent(owner.epoch) && copyAttemptRef.current === attempt;
    const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
    if (typeof clipboard?.writeText !== "function") {
      if (currentCopy()) setCopyNotice("浏览器无法使用剪贴板，请手动选择邀请码复制。");
      return;
    }
    try {
      await clipboard.writeText(code);
      if (currentCopy()) setCopyNotice("邀请码已复制。");
    } catch {
      if (currentCopy()) setCopyNotice("无法访问剪贴板，请手动选择邀请码复制。");
    }
  }

  async function readOnlyCheck(): Promise<void> {
    const owner = scopeRef.current;
    const ownerPort = owner.services.teamManagement;
    if (ownerPort === undefined || unknownRef.current === null) return;
    const frozenAttempt = unknownRef.current;
    const attempt = ++snapshotAttemptRef.current;
    snapshotControllerRef.current?.abort();
    const controller = new AbortController();
    snapshotControllerRef.current = controller;
    const teamId = frozenAttempt.teamId;
    const currentTab = tab;
    try {
      if (teamId !== null && currentTab === "invites") {
        const list = await ownerPort.listInvites(teamId, controller.signal);
        if (!isCurrent(owner.epoch) || unknownRef.current !== frozenAttempt || snapshotAttemptRef.current !== attempt) return;
        setSnapshot({ kind: "invites", inviteCount: list.length, inviteStatuses: list.map(({ status }) => status) });
      } else if (teamId !== null) {
        const current = await ownerPort.getTeam(teamId, controller.signal);
        if (!isCurrent(owner.epoch) || unknownRef.current !== frozenAttempt || snapshotAttemptRef.current !== attempt) return;
        setSnapshot({ kind: "team", teamName: current.name, memberCount: current.members.length, myRole: current.my_role });
      } else {
        const current = await ownerPort.listTeams(controller.signal);
        if (!isCurrent(owner.epoch) || unknownRef.current !== frozenAttempt || snapshotAttemptRef.current !== attempt) return;
        setSnapshot({ kind: "directory", teamCount: current.length });
      }
    } catch (error) {
      if (!isCurrent(owner.epoch) || unknownRef.current !== frozenAttempt || snapshotAttemptRef.current !== attempt) return;
      if (error instanceof ApiError && error.status === 401) onUnauthorizedRef.current();
      else setRefreshWarning("只读核对未完成；原操作仍保持未确认状态。可稍后再次读取。");
    } finally {
      if (snapshotAttemptRef.current === attempt) snapshotControllerRef.current = null;
    }
  }

  function abandonUnknown(): void {
    if (unknownRef.current === null || writeRef.current !== null) return;
    if (!window.confirm("放弃本地核对状态并允许新的独立操作？这不会撤销服务器可能已完成的操作。")) return;
    unknownRef.current = null;
    setUnknownLabel(null);
    setSnapshot(null);
    resetDrafts();
    publishGuard(hasPageGuard());
  }

  const locked = pendingLabel !== null || unknownLabel !== null;
  const selectedTeam = detail !== null && detail.id === selectedTeamId ? detail : null;
  const selectedSummary = teams.find((item) => item.id === selectedTeamId);

  return (
    <div className="teams-page">
      <div className="teams-page-heading">
        <div>
          <p className="team-eyebrow">工作空间 / 协作</p>
          <h1>团队管理</h1>
        </div>
        <button className="secondary-button" onClick={onBack} type="button">返回工作空间</button>
      </div>

      {port === undefined ? (
        <section className="team-panel team-unavailable" role="status">
          <h2>团队管理未接入此服务</h2>
          <p>当前演示服务没有团队管理接口。切换到已配置的本地 API 服务后可管理团队。</p>
        </section>
      ) : (
        <>
          {unknownLabel !== null && (
            <section className="team-unknown-banner" aria-label="待核对的团队操作">
              <div>
                <strong>“{unknownLabel}”结果待核对</strong>
                <p>
                  系统不会自动重发。核对读取不会确认操作归属，也不会替换原草稿。
                  {selectedTeamId !== null && tab === "invites" && " 读取邀请码列表还会按服务端规则将已过期邀请码标记为过期。"}
                </p>
              </div>
              <div className="team-unknown-actions">
                <button className="secondary-button" disabled={pendingLabel !== null} onClick={() => { void readOnlyCheck(); }} type="button">只读核对</button>
                <button className="danger-button" disabled={pendingLabel !== null} onClick={abandonUnknown} type="button">明确放弃本地状态</button>
              </div>
            </section>
          )}

          {snapshot !== null && unknownLabel !== null && (
            <section className="team-snapshot" aria-label="只读核对快照" role="status">
              <strong>只读快照 · 未确认原操作</strong>
              {snapshot.kind === "directory" && <span>当前团队数量：{snapshot.teamCount}</span>}
              {snapshot.kind === "team" && <span>{snapshot.teamName || "未命名团队"} · {snapshot.memberCount} 位成员 · 当前角色 {snapshot.myRole}</span>}
              {snapshot.kind === "invites" && <span>{snapshot.inviteCount} 个邀请码：{snapshot.inviteStatuses?.join("、") || "无"}</span>}
            </section>
          )}

          {selectedTeam === null ? (
            <TeamDirectoryPanel
              teams={teams}
              selectedTeamId={selectedTeamId}
              loading={teamsLoading}
              disabled={locked}
              error={directoryError}
              createName={drafts.createName}
              joinCode={drafts.joinCode}
              onCreateNameChange={(value) => updateDrafts((current) => ({ ...current, createName: value }))}
              onJoinCodeChange={(value) => updateDrafts((current) => ({ ...current, joinCode: value }))}
              onRefresh={() => { void loadTeams(); }}
              onCreate={createTeam}
              onJoin={joinTeam}
              onSelect={selectTeam}
            />
          ) : detailLoading ? (
            <section aria-label="团队详情加载中" className="team-panel"><p className="team-empty">正在读取团队详情…</p></section>
          ) : (
            <TeamDetailPanel
              team={selectedTeam}
              actorId={userId}
              tab={tab}
              nameDraft={drafts.teamName}
              inviteCountDraft={drafts.inviteCount}
              roleDrafts={drafts.roleValues}
              permissionDrafts={drafts.permissionValues}
              invites={invites}
              inviteReceipt={inviteReceipt}
              inviteLoading={inviteLoading}
              locked={locked}
              pendingLabel={pendingLabel}
              notice={notice}
              copyNotice={copyNotice}
              error={detailError}
              refreshWarning={refreshWarning}
              onBack={goBackToDirectory}
              onTabChange={changeTab}
              onNameChange={(value) => updateDrafts((current) => ({ ...current, teamName: value }))}
              onSaveName={saveTeamName}
              onInviteCountChange={(value) => updateDrafts((current) => ({ ...current, inviteCount: value }))}
              onRefreshInvites={() => { if (selectedTeamId !== null) void loadInvites(selectedTeamId); }}
              onCreateInvites={createInvites}
              onRevokeInvite={revokeInvite}
              onRoleChange={setRoleDraft}
              onSaveRole={(memberId) => {
                const member = selectedTeam.members.find((row) => row.user_id === memberId);
                if (member !== undefined) saveMemberRole(member);
              }}
              onPermissionToggle={setPermissionDraft}
              onSavePermissions={(memberId) => {
                const member = selectedTeam.members.find((row) => row.user_id === memberId);
                if (member !== undefined) saveMemberPermissions(member);
              }}
              onRemoveMember={removeMember}
              onLeave={leaveTeam}
              onDelete={deleteTeam}
              onDismissInviteReceipt={() => changeInviteReceipt(null)}
              onCopyInviteCode={(code) => { void copyInviteCode(code); }}
            />
          )}
          {detailError !== null && selectedTeam === null && selectedTeamId !== null && (
            <section className="team-panel team-error-panel" role="alert">
              {notice !== null && <p className="team-success" role="status">{notice}</p>}
              <p>{detailError}</p>
              <button className="secondary-button" disabled={locked || detailLoading} onClick={() => { void loadDetail(selectedTeamId); }} type="button">重试读取详情</button>
              <button className="secondary-button" disabled={locked} onClick={goBackToDirectory} type="button">返回团队列表</button>
            </section>
          )}
          {selectedTeam === null && selectedTeamId !== null && selectedSummary !== undefined && !detailLoading && detailError === null && (
            <section className="team-panel team-error-panel" role="alert">
              {notice !== null && <p className="team-success" role="status">{notice}</p>}
              <p>服务器没有返回所选团队的详情。</p>
              <button className="secondary-button" disabled={locked} onClick={goBackToDirectory} type="button">返回团队列表</button>
            </section>
          )}
        </>
      )}
    </div>
  );
}
