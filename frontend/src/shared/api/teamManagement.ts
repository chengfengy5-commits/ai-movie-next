import { InvalidResponseError } from "./contracts";

export const TEAM_PERMISSION_KEYS = [
  "view_tasks",
  "view_usage",
  "manage_invites",
  "remove_members",
  "delete_series",
  "enter_claimed_series",
] as const;

export type TeamPermission = typeof TEAM_PERMISSION_KEYS[number];
export type TeamRole = "admin" | "member";

export interface TeamRecord {
  id: string;
  name: string;
  owner_id: string;
  created_at: string;
}

export interface TeamDirectoryEntry extends TeamRecord {
  member_count: number;
  my_role: string;
}

export interface TeamMember {
  user_id: string;
  username: string;
  avatar_url: string | null;
  role: string;
  permissions: TeamPermission[];
  joined_at: string;
}

export interface TeamDetail extends TeamRecord {
  members: TeamMember[];
  my_role: string;
  my_permissions: TeamPermission[];
}

export interface TeamInvite {
  id: string;
  code: string;
  status: string;
  created_at: string;
  expires_at: string;
  used_by: string | null;
  used_at: string | null;
}

export interface CreatedTeamInvite {
  id: string;
  code: string;
  expires_at: string;
}

export interface TeamInviteReceipt {
  invites: CreatedTeamInvite[];
  ttl_hours: number;
}

export interface TeamMessageReceipt {
  message: string;
}

export interface TeamJoinReceipt extends TeamMessageReceipt {
  team_id: string;
  team_name: string;
}

export interface TeamRoleReceipt extends TeamMessageReceipt {
  role: TeamRole;
  permissions: TeamPermission[];
}

export interface TeamPermissionsReceipt extends TeamMessageReceipt {
  permissions: TeamPermission[];
}

export interface TeamManagementServicePort {
  listTeams(signal: AbortSignal): Promise<TeamDirectoryEntry[]>;
  createTeam(name: string, actorId: string, signal: AbortSignal): Promise<TeamRecord>;
  getTeam(teamId: string, signal: AbortSignal): Promise<TeamDetail>;
  updateTeam(
    teamId: string,
    update: { name?: string | null },
    expectedName: string,
    signal: AbortSignal,
  ): Promise<{ id: string; name: string }>;
  deleteTeam(teamId: string, signal: AbortSignal): Promise<TeamMessageReceipt>;
  leaveTeam(teamId: string, signal: AbortSignal): Promise<TeamMessageReceipt>;
  removeMember(teamId: string, userId: string, signal: AbortSignal): Promise<TeamMessageReceipt>;
  setMemberRole(
    teamId: string,
    userId: string,
    role: TeamRole,
    currentRole: string,
    currentPermissions: TeamPermission[],
    signal: AbortSignal,
  ): Promise<TeamRoleReceipt>;
  setMemberPermissions(
    teamId: string,
    userId: string,
    permissions: TeamPermission[],
    signal: AbortSignal,
  ): Promise<TeamPermissionsReceipt>;
  createInvites(teamId: string, count: number, signal: AbortSignal): Promise<TeamInviteReceipt>;
  listInvites(teamId: string, signal: AbortSignal): Promise<TeamInvite[]>;
  revokeInvite(teamId: string, inviteId: string, signal: AbortSignal): Promise<TeamMessageReceipt>;
  joinTeam(code: string, signal: AbortSignal): Promise<TeamJoinReceipt>;
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function stringField(
  source: Record<string, unknown>,
  key: string,
  label: string,
  allowEmpty = false,
): string {
  const value = source[key];
  if (typeof value !== "string" || (!allowEmpty && value.length === 0)) {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function nullableStringField(source: Record<string, unknown>, key: string, label: string): string | null {
  const value = source[key];
  if (value !== null && typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value as string | null;
}

function isValidTimestamp(value: string): boolean {
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?(?:([zZ])|([+-])(\d{2}):?(\d{2}))?$/,
  );
  if (match === null) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(0, 0, 0, 0);
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) {
    return false;
  }
  if (Number(match[4]) > 23 || Number(match[5]) > 59 || (match[6] !== undefined && Number(match[6]) > 59)) {
    return false;
  }
  if (match[9] !== undefined && (Number(match[10]) > 23 || Number(match[11]) > 59)) return false;
  const normalized = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
  return Number.isFinite(Date.parse(normalized));
}

function timestampField(source: Record<string, unknown>, key: string, label: string): string {
  const value = stringField(source, key, label);
  if (!isValidTimestamp(value)) {
    throw new InvalidResponseError(`${label}中的 ${key} 日期格式无效。`);
  }
  return value;
}

function permissionList(value: unknown, label: string): TeamPermission[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !TEAM_PERMISSION_KEYS.includes(item as TeamPermission))) {
    throw new InvalidResponseError(`${label}中的权限列表格式无效。`);
  }
  return [...value] as TeamPermission[];
}

function teamRecord(value: unknown, label: string): TeamRecord {
  const source = record(value, label);
  return {
    id: stringField(source, "id", label),
    name: stringField(source, "name", label, true),
    owner_id: stringField(source, "owner_id", label),
    created_at: timestampField(source, "created_at", label),
  };
}

export function parseTeamDirectory(value: unknown): TeamDirectoryEntry[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError("团队目录格式无效。");
  }
  const seen = new Set<string>();
  return value.map((entry, index) => {
    const label = `团队目录第 ${index + 1} 项`;
    const source = record(entry, label);
    const team = teamRecord(source, label);
    const memberCount = source.member_count;
    if (typeof memberCount !== "number" || !Number.isSafeInteger(memberCount) || memberCount < 0) {
      throw new InvalidResponseError(`${label}中的 member_count 字段格式无效。`);
    }
    if (seen.has(team.id)) {
      throw new InvalidResponseError("团队目录包含重复编号。");
    }
    seen.add(team.id);
    return {
      ...team,
      member_count: memberCount,
      my_role: stringField(source, "my_role", label),
    };
  });
}

export function parseCreatedTeam(value: unknown, requestedName: string, actorId: string): TeamRecord {
  const team = teamRecord(value, "新建团队回执");
  if (team.owner_id !== actorId || team.name !== requestedName.trim()) {
    throw new InvalidResponseError("新建团队回执与本次请求不匹配。");
  }
  return team;
}

export function parseTeamDetail(value: unknown, requestedId: string): TeamDetail {
  const source = record(value, "团队详情");
  const team = teamRecord(source, "团队详情");
  if (team.id !== requestedId || !Array.isArray(source.members)) {
    throw new InvalidResponseError("团队详情与当前目标不匹配。");
  }
  const members = source.members.map((member, index): TeamMember => {
    const row = record(member, `成员第 ${index + 1} 项`);
    return {
      user_id: stringField(row, "user_id", "成员"),
      username: stringField(row, "username", "成员", true),
      avatar_url: nullableStringField(row, "avatar_url", "成员"),
      role: stringField(row, "role", "成员"),
      permissions: permissionList(row.permissions, "成员"),
      joined_at: timestampField(row, "joined_at", "成员"),
    };
  });
  const memberIds = new Set<string>();
  for (const member of members) {
    if (memberIds.has(member.user_id)) {
      throw new InvalidResponseError("团队详情包含重复成员。");
    }
    memberIds.add(member.user_id);
  }
  return {
    ...team,
    members,
    my_role: stringField(source, "my_role", "团队详情"),
    my_permissions: permissionList(source.my_permissions, "团队详情"),
  };
}

export function parseUpdatedTeam(
  value: unknown,
  requestedId: string,
  expectedName: string,
): { id: string; name: string } {
  const source = record(value, "团队修改回执");
  const id = stringField(source, "id", "团队修改回执");
  const name = stringField(source, "name", "团队修改回执", true);
  if (id !== requestedId || name !== expectedName) {
    throw new InvalidResponseError("团队修改回执与本次请求不匹配。");
  }
  return { id, name };
}

export function parseTeamMessage(value: unknown, expected: string, label = "团队操作回执"): TeamMessageReceipt {
  const source = record(value, label);
  const message = stringField(source, "message", label);
  if (message !== expected) {
    throw new InvalidResponseError(`${label}与本次操作不匹配。`);
  }
  return { message };
}

export function parseTeamRoleReceipt(
  value: unknown,
  requestedRole: TeamRole,
  currentRole: string,
  currentPermissions: TeamPermission[],
): TeamRoleReceipt {
  const source = record(value, "成员角色回执");
  const message = stringField(source, "message", "成员角色回执");
  const role = stringField(source, "role", "成员角色回执");
  const permissions = permissionList(source.permissions, "成员角色回执");
  const normalizedCurrent = currentRole.trim().toLowerCase();
  const unchanged = normalizedCurrent === requestedRole;
  const expectedPermissions = unchanged
    ? currentPermissions
    : requestedRole === "admin" ? ["view_tasks", "manage_invites"] : [];
  if (role !== requestedRole
    || message !== (unchanged ? "角色未变化" : "角色已更新")
    || permissions.length !== expectedPermissions.length
    || permissions.some((permission, index) => permission !== expectedPermissions[index])) {
    throw new InvalidResponseError("成员角色回执与本次请求不匹配。");
  }
  return { message, role: requestedRole, permissions };
}

export function parseTeamPermissionsReceipt(
  value: unknown,
  requested: TeamPermission[],
): TeamPermissionsReceipt {
  const source = record(value, "成员权限回执");
  const permissions = permissionList(source.permissions, "成员权限回执");
  const expected = [...new Set(requested)];
  if (stringField(source, "message", "成员权限回执") !== "权限已更新"
    || permissions.length !== expected.length
    || permissions.some((permission, index) => permission !== expected[index])) {
    throw new InvalidResponseError("成员权限回执与本次请求不匹配。");
  }
  return { message: "权限已更新", permissions };
}

function inviteRecord(value: unknown, label: string): TeamInvite {
  const source = record(value, label);
  return {
    id: stringField(source, "id", label),
    code: stringField(source, "code", label),
    status: stringField(source, "status", label),
    created_at: timestampField(source, "created_at", label),
    expires_at: timestampField(source, "expires_at", label),
    used_by: nullableStringField(source, "used_by", label),
    used_at: source.used_at === null ? null : timestampField(source, "used_at", label),
  };
}

export function parseTeamInviteList(value: unknown): TeamInvite[] {
  if (!Array.isArray(value)) {
    throw new InvalidResponseError("邀请码列表格式无效。");
  }
  const ids = new Set<string>();
  return value.map((invite, index) => {
    const parsed = inviteRecord(invite, `邀请码第 ${index + 1} 项`);
    if (ids.has(parsed.id)) {
      throw new InvalidResponseError("邀请码列表包含重复编号。");
    }
    ids.add(parsed.id);
    return parsed;
  });
}

export function parseCreatedTeamInvites(value: unknown, expectedCount: number): TeamInviteReceipt {
  const source = record(value, "邀请码生成回执");
  if (!Array.isArray(source.invites) || source.invites.length !== expectedCount
    || !Number.isSafeInteger(source.ttl_hours)) {
    throw new InvalidResponseError("邀请码生成回执数量或有效期格式无效。");
  }
  const invites = source.invites.map((invite, index) => {
    const row = record(invite, `新邀请码第 ${index + 1} 项`);
    return {
      id: stringField(row, "id", "新邀请码"),
      code: stringField(row, "code", "新邀请码"),
      expires_at: timestampField(row, "expires_at", "新邀请码"),
    };
  });
  const ids = new Set(invites.map(({ id }) => id));
  const codes = new Set(invites.map(({ code }) => code));
  if (ids.size !== expectedCount || codes.size !== expectedCount) {
    throw new InvalidResponseError("邀请码生成回执包含重复值。");
  }
  return { invites, ttl_hours: source.ttl_hours as number };
}

export function parseTeamJoinReceipt(value: unknown): TeamJoinReceipt {
  const source = record(value, "加入团队回执");
  return {
    message: stringField(source, "message", "加入团队回执"),
    team_id: stringField(source, "team_id", "加入团队回执"),
    team_name: stringField(source, "team_name", "加入团队回执", true),
  };
}
