import { InvalidResponseError } from "./contracts";

export interface TeamReportingDateRange {
  startTime?: string;
  endTime?: string;
}

export interface TeamMemberTask {
  id: string;
  type: string;
  message_id: string | null;
  status: string;
  result: string | null;
  request_data: string | null;
  credit_cost: number;
  progress: number;
  progress_message: string | null;
  created_at: string | null;
  updated_at: string | null;
  asset_type: string | null;
  asset_id: string | null;
  asset_name: string | null;
  chapter_title: string | null;
  chapter_id: string | null;
  frame_index: number | null;
  frame_count: number | null;
  frame_text: string | null;
  prompt_id?: string;
}

export interface TeamMemberTasksPage {
  total: number;
  tasks: TeamMemberTask[];
  page: number;
  page_size: number;
  credits: number;
}

export interface TeamReportingModelUsage {
  model_name: string;
  calls: number;
  credits: number;
  failed_calls: number;
}

export interface TeamReportingMemberUsage {
  user_id: string;
  username: string;
  avatar_url: string | null;
  calls: number;
  credits: number;
  failed_calls: number;
  chapter_count?: number;
}

export interface TeamReportingTotals {
  calls: number;
  credits: number;
  failed_calls: number;
  by_model: TeamReportingModelUsage[];
  by_member: TeamReportingMemberUsage[];
}

export interface TeamUsageItem {
  series_id: string;
  series_name: string;
  updated_at: string;
  total_calls: number;
  total_credits: number;
  failed_calls: number;
  by_model: TeamReportingModelUsage[];
  by_member: TeamReportingMemberUsage[];
}

export interface TeamUsageResponse {
  items: TeamUsageItem[];
  total_calls: number;
  total_credits: number;
  total_failed: number;
}

export interface TeamSeriesUsageChapter extends TeamReportingTotals {
  chapter_id: string;
  chapter_title: string;
}

export interface TeamSeriesUsageResponse {
  series_id: string;
  series_name: string;
  series_total: TeamReportingTotals;
  chapters: TeamSeriesUsageChapter[];
}

export interface TeamModelUsageSeries {
  series_id: string;
  series_name: string;
  calls: number;
  credits: number;
  failed_calls: number;
}

export interface TeamModelUsageMember {
  user_id: string;
  username: string;
  avatar_url: string | null;
  calls: number;
  credits: number;
  failed_calls: number;
}

export interface TeamModelUsageResponse {
  model_name: string;
  total_calls: number;
  total_credits: number;
  failed_calls: number;
  by_series: TeamModelUsageSeries[];
  by_member: TeamModelUsageMember[];
}

export interface TeamUsageExportRow {
  user_id: string;
  username: string;
  avatar_url: string | null;
  series_id: string;
  series_name: string;
  chapter_id: string;
  chapter_title: string;
  model_name: string;
  calls: number;
  credits: number;
  failed_calls: number;
}

export interface TeamUsageExportMember {
  user_id: string;
  username: string;
  avatar_url: string | null;
  chapter_count: number;
  models: string[];
  calls: number;
  credits: number;
  failed_calls: number;
}

export interface TeamUsageExport {
  rows: TeamUsageExportRow[];
  members: TeamUsageExportMember[];
  model_order: string[];
  total_calls: number;
  total_credits: number;
  total_failed: number;
}

export interface TeamReportingServicePort {
  listMemberTasks(
    teamId: string,
    userId: string,
    page: number,
    pageSize: number,
    signal: AbortSignal,
  ): Promise<TeamMemberTasksPage>;
  getTeamUsage(teamId: string, range: TeamReportingDateRange, signal: AbortSignal): Promise<TeamUsageResponse>;
  getSeriesUsage(
    teamId: string,
    seriesId: string,
    range: TeamReportingDateRange,
    signal: AbortSignal,
  ): Promise<TeamSeriesUsageResponse>;
  getModelUsage(
    teamId: string,
    modelName: string,
    range: TeamReportingDateRange,
    signal: AbortSignal,
  ): Promise<TeamModelUsageResponse>;
  exportTeamUsage(teamId: string, range: TeamReportingDateRange, signal: AbortSignal): Promise<TeamUsageExport>;
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function stringField(source: Record<string, unknown>, key: string, label: string, allowEmpty = false): string {
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

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new InvalidResponseError(`${label}格式无效。`);
  }
  return value;
}

function nullableIntegerField(source: Record<string, unknown>, key: string, label: string): number | null {
  const value = source[key];
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function arrayField<T>(value: unknown, label: string, parse: (item: unknown, index: number) => T): T[] {
  if (!Array.isArray(value)) throw new InvalidResponseError(`${label}格式无效。`);
  return value.map(parse);
}

function parseTask(value: unknown, index: number): TeamMemberTask {
  const label = `成员任务第 ${index + 1} 项`;
  const source = record(value, label);
  const task: TeamMemberTask = {
    id: stringField(source, "id", label),
    type: stringField(source, "type", label),
    message_id: nullableStringField(source, "message_id", label),
    status: stringField(source, "status", label),
    result: nullableStringField(source, "result", label),
    request_data: nullableStringField(source, "request_data", label),
    credit_cost: finiteNumber(source.credit_cost, `${label} credit_cost`),
    progress: finiteNumber(source.progress, `${label} progress`),
    progress_message: nullableStringField(source, "progress_message", label),
    created_at: nullableStringField(source, "created_at", label),
    updated_at: nullableStringField(source, "updated_at", label),
    asset_type: nullableStringField(source, "asset_type", label),
    asset_id: nullableStringField(source, "asset_id", label),
    asset_name: nullableStringField(source, "asset_name", label),
    chapter_title: nullableStringField(source, "chapter_title", label),
    chapter_id: nullableStringField(source, "chapter_id", label),
    frame_index: nullableIntegerField(source, "frame_index", label),
    frame_count: nullableIntegerField(source, "frame_count", label),
    frame_text: nullableStringField(source, "frame_text", label),
  };
  if (Object.hasOwn(source, "prompt_id")) task.prompt_id = stringField(source, "prompt_id", label, true);
  return task;
}

export function parseTeamMemberTasksPage(value: unknown): TeamMemberTasksPage {
  const source = record(value, "成员任务分页");
  const page = nonNegativeInteger(source.page, "成员任务页码");
  const pageSize = nonNegativeInteger(source.page_size, "成员任务每页条数");
  if (page < 1 || pageSize < 1) throw new InvalidResponseError("成员任务分页格式无效。");
  return {
    total: nonNegativeInteger(source.total, "成员任务总数"),
    tasks: arrayField(source.tasks, "成员任务列表", parseTask),
    page,
    page_size: pageSize,
    credits: finiteNumber(source.credits, "成员积分余额"),
  };
}

function parseModelUsage(value: unknown, index: number): TeamReportingModelUsage {
  const label = `模型用量第 ${index + 1} 项`;
  const source = record(value, label);
  return {
    model_name: stringField(source, "model_name", label, true),
    calls: nonNegativeInteger(source.calls, `${label}调用数`),
    credits: finiteNumber(source.credits, `${label}积分`),
    failed_calls: nonNegativeInteger(source.failed_calls, `${label}失败数`),
  };
}

function parseMemberUsage(value: unknown, index: number): TeamReportingMemberUsage {
  const label = `成员用量第 ${index + 1} 项`;
  const source = record(value, label);
  return {
    user_id: stringField(source, "user_id", label, true),
    username: stringField(source, "username", label, true),
    avatar_url: nullableStringField(source, "avatar_url", label),
    calls: nonNegativeInteger(source.calls, `${label}调用数`),
    credits: finiteNumber(source.credits, `${label}积分`),
    failed_calls: nonNegativeInteger(source.failed_calls, `${label}失败数`),
    ...(Object.hasOwn(source, "chapter_count")
      ? { chapter_count: nonNegativeInteger(source.chapter_count, `${label}章节数`) }
      : {}),
  };
}

function parseTotals(value: unknown, label: string): TeamReportingTotals {
  const source = record(value, label);
  return {
    calls: nonNegativeInteger(source.calls, `${label}调用数`),
    credits: finiteNumber(source.credits, `${label}积分`),
    failed_calls: nonNegativeInteger(source.failed_calls, `${label}失败数`),
    by_model: arrayField(source.by_model, `${label}模型统计`, parseModelUsage),
    by_member: arrayField(source.by_member, `${label}成员统计`, parseMemberUsage),
  };
}

export function parseTeamUsage(value: unknown): TeamUsageResponse {
  const source = record(value, "团队用量");
  const items = arrayField(source.items, "团队剧集用量", (item, index) => {
    const label = `团队剧集用量第 ${index + 1} 项`;
    const row = record(item, label);
    return {
      series_id: stringField(row, "series_id", label),
      series_name: stringField(row, "series_name", label, true),
      updated_at: stringField(row, "updated_at", label, true),
      total_calls: nonNegativeInteger(row.total_calls, `${label}调用数`),
      total_credits: finiteNumber(row.total_credits, `${label}积分`),
      failed_calls: nonNegativeInteger(row.failed_calls, `${label}失败数`),
      by_model: arrayField(row.by_model, `${label}模型统计`, parseModelUsage),
      by_member: arrayField(row.by_member, `${label}成员统计`, parseMemberUsage),
    };
  });
  return {
    items,
    total_calls: nonNegativeInteger(source.total_calls, "团队总调用数"),
    total_credits: finiteNumber(source.total_credits, "团队总积分"),
    total_failed: nonNegativeInteger(source.total_failed, "团队失败调用数"),
  };
}

export function parseTeamSeriesUsage(value: unknown): TeamSeriesUsageResponse {
  const source = record(value, "团队剧集用量");
  return {
    series_id: stringField(source, "series_id", "团队剧集用量"),
    series_name: stringField(source, "series_name", "团队剧集用量", true),
    series_total: parseTotals(source.series_total, "剧集汇总"),
    chapters: arrayField(source.chapters, "章节用量", (item, index) => {
      const label = `章节用量第 ${index + 1} 项`;
      const row = record(item, label);
      return {
        ...parseTotals(row, label),
        chapter_id: stringField(row, "chapter_id", label),
        chapter_title: stringField(row, "chapter_title", label, true),
      };
    }),
  };
}

export function parseTeamModelUsage(value: unknown): TeamModelUsageResponse {
  const source = record(value, "团队模型用量");
  return {
    model_name: stringField(source, "model_name", "团队模型用量", true),
    total_calls: nonNegativeInteger(source.total_calls, "模型总调用数"),
    total_credits: finiteNumber(source.total_credits, "模型总积分"),
    failed_calls: nonNegativeInteger(source.failed_calls, "模型失败调用数"),
    by_series: arrayField(source.by_series, "模型剧集统计", (item, index) => {
      const label = `模型剧集统计第 ${index + 1} 项`;
      const row = record(item, label);
      return {
        series_id: stringField(row, "series_id", label),
        series_name: stringField(row, "series_name", label, true),
        calls: nonNegativeInteger(row.calls, `${label}调用数`),
        credits: finiteNumber(row.credits, `${label}积分`),
        failed_calls: nonNegativeInteger(row.failed_calls, `${label}失败数`),
      };
    }),
    by_member: arrayField(source.by_member, "模型成员统计", (item, index) => {
      const label = `模型成员统计第 ${index + 1} 项`;
      const row = record(item, label);
      return {
        user_id: stringField(row, "user_id", label, true),
        username: stringField(row, "username", label, true),
        avatar_url: nullableStringField(row, "avatar_url", label),
        calls: nonNegativeInteger(row.calls, `${label}调用数`),
        credits: finiteNumber(row.credits, `${label}积分`),
        failed_calls: nonNegativeInteger(row.failed_calls, `${label}失败数`),
      };
    }),
  };
}

export function parseTeamUsageExport(value: unknown): TeamUsageExport {
  const source = record(value, "团队用量导出");
  const modelOrder = source.model_order === undefined || source.model_order === null
    ? []
    : arrayField(source.model_order, "导出模型顺序", (item) => {
      if (typeof item !== "string") throw new InvalidResponseError("导出模型顺序格式无效。");
      return item;
    });
  return {
    rows: arrayField(source.rows, "导出明细", (item, index) => {
      const label = `导出明细第 ${index + 1} 项`;
      const row = record(item, label);
      return {
        user_id: stringField(row, "user_id", label, true),
        username: stringField(row, "username", label, true),
        avatar_url: nullableStringField(row, "avatar_url", label),
        series_id: stringField(row, "series_id", label, true),
        series_name: stringField(row, "series_name", label, true),
        chapter_id: stringField(row, "chapter_id", label, true),
        chapter_title: stringField(row, "chapter_title", label, true),
        model_name: stringField(row, "model_name", label, true),
        calls: nonNegativeInteger(row.calls, `${label}调用数`),
        credits: finiteNumber(row.credits, `${label}积分`),
        failed_calls: nonNegativeInteger(row.failed_calls, `${label}失败数`),
      };
    }),
    members: arrayField(source.members, "导出成员汇总", (item, index) => {
      const label = `导出成员汇总第 ${index + 1} 项`;
      const row = record(item, label);
      return {
        user_id: stringField(row, "user_id", label, true),
        username: stringField(row, "username", label, true),
        avatar_url: nullableStringField(row, "avatar_url", label),
        chapter_count: nonNegativeInteger(row.chapter_count, `${label}章节数`),
        models: arrayField(row.models, `${label}模型列表`, (model) => {
          if (typeof model !== "string") throw new InvalidResponseError(`${label}模型列表格式无效。`);
          return model;
        }),
        calls: nonNegativeInteger(row.calls, `${label}调用数`),
        credits: finiteNumber(row.credits, `${label}积分`),
        failed_calls: nonNegativeInteger(row.failed_calls, `${label}失败数`),
      };
    }),
    model_order: modelOrder,
    total_calls: nonNegativeInteger(source.total_calls, "导出总调用数"),
    total_credits: finiteNumber(source.total_credits, "导出总积分"),
    total_failed: nonNegativeInteger(source.total_failed, "导出失败调用数"),
  };
}
