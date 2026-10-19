import { InvalidResponseError } from "./contracts";

export interface TeamSeriesAssetCounts {
  characters: number;
  scenes: number;
  props: number;
  storyboard: number;
}

export interface TeamSeriesRecord {
  id: string;
  name: string;
  description: string | null;
  image_url: string | null;
  user_id: string;
  owner_name: string;
  owner_avatar_url: string | null;
  claimed_by: string | null;
  claimed_by_username: string;
  claimed_by_avatar_url: string | null;
  created_at: string;
  updated_at: string;
  chapter_count: number;
  asset_counts: TeamSeriesAssetCounts;
}

export interface TeamSeriesPage {
  items: TeamSeriesRecord[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface CreateTeamSeriesInput {
  name: string;
  description?: string | null;
  image_url?: string | null;
  style_prompt_id?: string | null;
  claim?: boolean;
}

export interface CreatedTeamSeries {
  id: string;
  name: string;
  team_id: string;
  user_id: string;
  style_prompt_id: string | null;
}

export interface TeamSeriesMessageReceipt {
  message: string;
}

export interface TeamSeriesShareReceipt extends TeamSeriesMessageReceipt {
  team_id: string;
  team_name: string;
}

export interface TeamSeriesClaimReceipt extends TeamSeriesMessageReceipt {
  claimed_by: string;
  claimed_at: string;
}

export interface TeamSeriesTransferReceipt extends TeamSeriesMessageReceipt {
  claimed_by: string;
}

export interface TeamSeriesCreateDraft {
  name: string;
  description?: string;
  image_url?: string;
  style_prompt_id?: string | null;
  claim: boolean;
}

export interface TeamSeriesWriteIntent<T> {
  label: string;
  teamId: string;
  targetId: string | null;
  submitted: unknown;
  request(port: TeamSeriesServicePort, signal: AbortSignal): Promise<T>;
  applyAck?(ack: T): void;
}

export interface TeamSeriesServicePort {
  listTeamSeries(teamId: string, page: number, pageSize: number, signal: AbortSignal): Promise<TeamSeriesPage>;
  createTeamSeries(
    teamId: string,
    input: CreateTeamSeriesInput,
    actorId: string,
    signal: AbortSignal,
  ): Promise<CreatedTeamSeries>;
  shareSeries(seriesId: string, teamId: string, claim: boolean, signal: AbortSignal): Promise<TeamSeriesShareReceipt>;
  unshareSeries(seriesId: string, signal: AbortSignal): Promise<TeamSeriesMessageReceipt>;
  claimTeamSeries(teamId: string, seriesId: string, actorId: string, signal: AbortSignal): Promise<TeamSeriesClaimReceipt>;
  unclaimTeamSeries(teamId: string, seriesId: string, signal: AbortSignal): Promise<TeamSeriesMessageReceipt>;
  transferTeamSeries(
    teamId: string,
    seriesId: string,
    userId: string,
    signal: AbortSignal,
  ): Promise<TeamSeriesTransferReceipt>;
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

function nonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }
  return value;
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

function parseTeamSeriesRecord(value: unknown, index: number): TeamSeriesRecord {
  const label = `团队剧集第 ${index + 1} 项`;
  const source = record(value, label);
  const counts = record(source.asset_counts, `${label}中的 asset_counts`);
  const claimedBy = source.claimed_by;
  if (claimedBy !== null && (typeof claimedBy !== "string" || claimedBy.length === 0)) {
    throw new InvalidResponseError(`${label}中的 claimed_by 字段格式无效。`);
  }
  return {
    id: stringField(source, "id", label),
    name: stringField(source, "name", label, true),
    description: nullableStringField(source, "description", label),
    image_url: nullableStringField(source, "image_url", label),
    user_id: stringField(source, "user_id", label),
    owner_name: stringField(source, "owner_name", label, true),
    owner_avatar_url: nullableStringField(source, "owner_avatar_url", label),
    claimed_by: claimedBy as string | null,
    claimed_by_username: stringField(source, "claimed_by_username", label, true),
    claimed_by_avatar_url: nullableStringField(source, "claimed_by_avatar_url", label),
    created_at: timestampField(source, "created_at", label),
    updated_at: timestampField(source, "updated_at", label),
    chapter_count: nonNegativeInteger(source.chapter_count, `${label}中的 chapter_count`),
    asset_counts: {
      characters: nonNegativeInteger(counts.characters, `${label}中的 characters`),
      scenes: nonNegativeInteger(counts.scenes, `${label}中的 scenes`),
      props: nonNegativeInteger(counts.props, `${label}中的 props`),
      storyboard: nonNegativeInteger(counts.storyboard, `${label}中的 storyboard`),
    },
  };
}

function normalizedPage(value: number): number {
  if (!Number.isSafeInteger(value)) throw new InvalidResponseError("请求页码格式无效。");
  return Math.max(1, value);
}

function normalizedPageSize(value: number): number {
  if (!Number.isSafeInteger(value)) throw new InvalidResponseError("请求每页数量格式无效。");
  return Math.max(1, Math.min(50, value));
}

export function parseTeamSeriesPage(value: unknown, requestedPage: number, requestedPageSize: number): TeamSeriesPage {
  const source = record(value, "团队剧集分页");
  if (!Array.isArray(source.items)) throw new InvalidResponseError("团队剧集分页中的 items 字段格式无效。");
  const expectedPage = normalizedPage(requestedPage);
  const expectedPageSize = normalizedPageSize(requestedPageSize);
  const total = nonNegativeInteger(source.total, "团队剧集分页中的 total");
  const page = nonNegativeInteger(source.page, "团队剧集分页中的 page");
  const pageSize = nonNegativeInteger(source.page_size, "团队剧集分页中的 page_size");
  const totalPages = nonNegativeInteger(source.total_pages, "团队剧集分页中的 total_pages");
  const expectedTotalPages = Math.max(1, Math.ceil(total / expectedPageSize));
  if (page !== expectedPage || pageSize !== expectedPageSize || totalPages !== expectedTotalPages) {
    throw new InvalidResponseError("团队剧集分页与本次请求不匹配。");
  }
  const items = source.items.map(parseTeamSeriesRecord);
  if (items.length > pageSize || new Set(items.map(({ id }) => id)).size !== items.length) {
    throw new InvalidResponseError("团队剧集分页包含重复编号或超出页容量。");
  }
  return { items, total, page, page_size: pageSize, total_pages: totalPages };
}

export function stripPythonWhitespace(value: string): string {
  return value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, "");
}

export function parseCreatedTeamSeries(
  value: unknown,
  teamId: string,
  actorId: string,
  input: CreateTeamSeriesInput,
): CreatedTeamSeries {
  const source = record(value, "团队剧集创建回执");
  const result: CreatedTeamSeries = {
    id: stringField(source, "id", "团队剧集创建回执"),
    name: stringField(source, "name", "团队剧集创建回执", true),
    team_id: stringField(source, "team_id", "团队剧集创建回执"),
    user_id: stringField(source, "user_id", "团队剧集创建回执"),
    style_prompt_id: nullableStringField(source, "style_prompt_id", "团队剧集创建回执"),
  };
  if (result.team_id !== teamId || result.user_id !== actorId
    || result.name !== stripPythonWhitespace(input.name)
    || result.style_prompt_id !== (input.style_prompt_id ?? null)) {
    throw new InvalidResponseError("团队剧集创建回执与本次请求不匹配。");
  }
  return result;
}

export function parseTeamSeriesShareReceipt(value: unknown, teamId: string): TeamSeriesShareReceipt {
  const source = record(value, "分享剧集回执");
  const result = {
    message: stringField(source, "message", "分享剧集回执", true),
    team_id: stringField(source, "team_id", "分享剧集回执"),
    team_name: stringField(source, "team_name", "分享剧集回执", true),
  };
  if (result.team_id !== teamId || result.message !== `已分享到团队「${result.team_name}」`) {
    throw new InvalidResponseError("分享剧集回执与本次请求不匹配。");
  }
  return result;
}

function exactMessage(value: unknown, expected: string, label: string): TeamSeriesMessageReceipt {
  const source = record(value, label);
  const message = stringField(source, "message", label, true);
  if (message !== expected) throw new InvalidResponseError(`${label}与本次请求不匹配。`);
  return { message };
}

export function parseTeamSeriesUnshareReceipt(value: unknown): TeamSeriesMessageReceipt {
  return exactMessage(value, "已移出团队，恢复为个人剧集", "撤回剧集分享回执");
}

export function parseTeamSeriesClaimReceipt(value: unknown, actorId: string): TeamSeriesClaimReceipt {
  const source = record(value, "认领剧集回执");
  const result = {
    message: stringField(source, "message", "认领剧集回执", true),
    claimed_by: stringField(source, "claimed_by", "认领剧集回执"),
    claimed_at: timestampField(source, "claimed_at", "认领剧集回执"),
  };
  if (result.message !== "认领成功" || result.claimed_by !== actorId) {
    throw new InvalidResponseError("认领剧集回执与本次请求不匹配。");
  }
  return result;
}

export function parseTeamSeriesUnclaimReceipt(value: unknown): TeamSeriesMessageReceipt {
  const source = record(value, "取消认领回执");
  const message = stringField(source, "message", "取消认领回执", true);
  if (message !== "已取消认领" && message !== "该剧集尚未被认领") {
    throw new InvalidResponseError("取消认领回执与本次请求不匹配。");
  }
  return { message };
}

export function parseTeamSeriesTransferReceipt(value: unknown, userId: string): TeamSeriesTransferReceipt {
  const source = record(value, "转交认领回执");
  const result = {
    message: stringField(source, "message", "转交认领回执", true),
    claimed_by: stringField(source, "claimed_by", "转交认领回执"),
  };
  if (result.message !== "已转交认领" || result.claimed_by !== userId) {
    throw new InvalidResponseError("转交认领回执与本次请求不匹配。");
  }
  return result;
}
