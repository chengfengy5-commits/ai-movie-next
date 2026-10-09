import { InvalidResponseError } from "./contracts";

export const MY_TASK_PAGE_SIZE = 10;

export interface MyTaskRecord {
  id: string;
  type: string;
  message_id: string;
  status: string;
  result: string | null;
  request_data: string | null;
  progress_message: string | null;
  credit_cost: number;
  progress: number;
  created_at: string | null;
  updated_at: string | null;
  asset_type: string | null;
  asset_id: string | null;
  asset_name: string | null;
  chapter_title: string | null;
  chapter_id: unknown;
  frame_index: number | null;
  frame_count: unknown;
  frame_text: string | null;
  prompt_id?: unknown;
}

export interface MyTaskPage {
  total: number;
  page: number;
  page_size: number;
  tasks: MyTaskRecord[];
}

export interface MyTaskPresentation {
  type: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  progress: string | null;
  progressMessage: string | null;
  creditCost: string;
  assetType: string | null;
  assetName: string | null;
  chapterTitle: string | null;
  framePosition: string | null;
  frameCount: string | null;
  result: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOwn(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function requiredValue(record: Record<string, unknown>, key: string, label: string): unknown {
  if (!hasOwn(record, key)) {
    throw new InvalidResponseError(`${label}缺少 ${key} 字段。`);
  }
  return record[key];
}

function requiredString(record: Record<string, unknown>, key: string, label: string): string {
  const value = requiredValue(record, key, label);
  if (typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function requiredIdentity(record: Record<string, unknown>, key: string, label: string): string {
  const value = requiredString(record, key, label);
  if (value.length === 0) {
    throw new InvalidResponseError(`${label}中的 ${key} 字段不能为空。`);
  }
  return value;
}

function requiredNullableString(record: Record<string, unknown>, key: string, label: string): string | null {
  const value = requiredValue(record, key, label);
  if (value !== null && typeof value !== "string") {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function requiredSafeInteger(record: Record<string, unknown>, key: string, label: string): number {
  const value = requiredValue(record, key, label);
  if (typeof value !== "number" || !Number.isSafeInteger(value)) {
    throw new InvalidResponseError(`${label}中的 ${key} 字段格式无效。`);
  }
  return value;
}

function parseNullableTimestamp(record: Record<string, unknown>, key: string, label: string): string | null {
  const value = requiredNullableString(record, key, label);
  if (value !== null && !isValidIsoTimestamp(value)) {
    throw new InvalidResponseError(`${label}中的 ${key} 日期格式无效。`);
  }
  return value;
}

function isValidIsoTimestamp(value: string): boolean {
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?(?:([zZ])|([+-])(\d{2}):?(\d{2}))?$/,
  );
  if (match === null) {
    return false;
  }

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = match[6] === undefined ? 0 : Number(match[6]);
  if (year < 1 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59) {
    return false;
  }

  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(0, 0, 0, 0);
  if (
    calendar.getUTCFullYear() !== year
    || calendar.getUTCMonth() !== month - 1
    || calendar.getUTCDate() !== day
  ) {
    return false;
  }

  if (match[10] !== undefined) {
    const offsetHour = Number(match[10]);
    const offsetMinute = Number(match[11]);
    if (offsetHour > 23 || offsetMinute > 59) {
      return false;
    }
  }
  return Number.isFinite(timestampMilliseconds(value));
}

function timestampMilliseconds(value: string): number {
  const match = value.match(
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2}))?(?:\.(\d{1,9}))?([zZ]|[+-]\d{2}:?\d{2})?$/,
  );
  if (match === null) {
    return Number.NaN;
  }
  const seconds = match[2] ?? "00";
  const milliseconds = (match[3] ?? "").padEnd(3, "0").slice(0, 3);
  const timezone = match[4] ?? "Z";
  const normalizedTimezone = timezone === "Z" || timezone === "z"
    ? "Z"
    : timezone.includes(":")
      ? timezone
      : `${timezone.slice(0, 3)}:${timezone.slice(3)}`;
  return Date.parse(`${match[1]}:${seconds}.${milliseconds}${normalizedTimezone}`);
}

export function parseMyTaskRecord(value: unknown): MyTaskRecord {
  if (!isRecord(value)) {
    throw new InvalidResponseError("任务列表中包含格式无效的项目。");
  }

  const label = "任务记录";
  const frameIndexValue = requiredValue(value, "frame_index", label);
  if (frameIndexValue !== null && (typeof frameIndexValue !== "number" || !Number.isSafeInteger(frameIndexValue))) {
    throw new InvalidResponseError(`${label}中的 frame_index 字段格式无效。`);
  }

  const task: MyTaskRecord = {
    id: requiredIdentity(value, "id", label),
    type: requiredString(value, "type", label),
    message_id: requiredString(value, "message_id", label),
    status: requiredString(value, "status", label),
    result: requiredNullableString(value, "result", label),
    request_data: requiredNullableString(value, "request_data", label),
    progress_message: requiredNullableString(value, "progress_message", label),
    credit_cost: requiredSafeInteger(value, "credit_cost", label),
    progress: requiredSafeInteger(value, "progress", label),
    created_at: parseNullableTimestamp(value, "created_at", label),
    updated_at: parseNullableTimestamp(value, "updated_at", label),
    asset_type: requiredNullableString(value, "asset_type", label),
    asset_id: requiredNullableString(value, "asset_id", label),
    asset_name: requiredNullableString(value, "asset_name", label),
    chapter_title: requiredNullableString(value, "chapter_title", label),
    chapter_id: requiredValue(value, "chapter_id", label),
    frame_index: frameIndexValue,
    frame_count: requiredValue(value, "frame_count", label),
    frame_text: requiredNullableString(value, "frame_text", label),
  };

  if (hasOwn(value, "prompt_id")) {
    task.prompt_id = value.prompt_id;
  }
  return task;
}

export function parseMyTaskPage(value: unknown, requestedPage: number): MyTaskPage {
  if (!Number.isSafeInteger(requestedPage) || requestedPage < 1) {
    throw new InvalidResponseError("任务页码无效。");
  }
  if (!isRecord(value)) {
    throw new InvalidResponseError("服务器返回的任务分页格式无效。");
  }

  const total = requiredValue(value, "total", "任务分页");
  const page = requiredValue(value, "page", "任务分页");
  const pageSize = requiredValue(value, "page_size", "任务分页");
  const tasks = requiredValue(value, "tasks", "任务分页");
  if (typeof total !== "number" || !Number.isSafeInteger(total) || total < 0) {
    throw new InvalidResponseError("任务分页中的 total 字段格式无效。");
  }
  if (page !== requestedPage) {
    throw new InvalidResponseError("服务器返回了与请求不一致的任务页码。");
  }
  if (pageSize !== MY_TASK_PAGE_SIZE) {
    throw new InvalidResponseError("服务器返回了与请求不一致的任务页大小。");
  }
  if (!Array.isArray(tasks) || tasks.length > MY_TASK_PAGE_SIZE) {
    throw new InvalidResponseError("服务器返回的任务页内容格式无效。");
  }

  const parsedTasks = tasks.map(parseMyTaskRecord);
  const ids = new Set<string>();
  for (const task of parsedTasks) {
    if (ids.has(task.id)) {
      throw new InvalidResponseError("任务页中包含重复的任务编号。");
    }
    ids.add(task.id);
  }

  return { total, page: requestedPage, page_size: MY_TASK_PAGE_SIZE, tasks: parsedTasks };
}

const taskTypeLabels = new Map<string, string>([
  ["chat", "聊天"],
  ["image", "图片"],
  ["image-single", "单图"],
  ["batch-image", "批量生图"],
  ["video", "视频"],
  ["video-single", "视频"],
  ["extract", "提取资产"],
  ["storyboard", "智能分镜"],
  ["optimize-frame", "分镜优化"],
  ["batch-optimize", "批量优化"],
  ["ai-review", "AI复核"],
  ["fused", "融合"],
]);

const taskStatusLabels = new Map<string, string>([
  ["queued", "排队中"],
  ["processing", "处理中"],
  ["cancelling", "取消中"],
  ["completed", "已完成"],
  ["failed", "失败"],
  ["cancelled", "已停止"],
]);

const taskAssetTypeLabels = new Map<string, string>([
  ["character", "角色"],
  ["scene", "场景"],
  ["prop", "道具"],
  ["storyboard", "故事板"],
]);

function displayTimestamp(value: string | null, timeZone?: string): string {
  if (value === null) {
    return "未提供";
  }
  const timestamp = timestampMilliseconds(value);
  if (!Number.isFinite(timestamp)) {
    return "未提供";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp));
}

export function projectMyTask(task: MyTaskRecord, timeZone?: string): MyTaskPresentation {
  const result = task.status === "failed" && task.result !== null && task.result.length > 0
    ? `失败说明：${task.result}`
    : task.result === null || task.result.length === 0
      ? "没有结果记录"
      : "已有结果记录";

  return {
    type: task.type.length === 0 ? "类型未识别" : taskTypeLabels.get(task.type) ?? task.type,
    status: taskStatusLabels.get(task.status) ?? "状态未识别",
    createdAt: displayTimestamp(task.created_at, timeZone),
    updatedAt: displayTimestamp(task.updated_at, timeZone),
    progress: task.progress >= 0 && task.progress <= 100 ? `${task.progress}%` : null,
    progressMessage: task.progress_message || null,
    creditCost: `记录积分：${task.credit_cost}`,
    assetType: task.asset_type
      ? taskAssetTypeLabels.get(task.asset_type) ?? task.asset_type
      : null,
    assetName: task.asset_name || null,
    chapterTitle: task.chapter_title || null,
    framePosition: task.frame_index !== null && task.frame_index > 0
      ? `分镜 ${task.frame_index}`
      : null,
    frameCount: typeof task.frame_count === "number"
      && Number.isSafeInteger(task.frame_count)
      && task.frame_count >= 0
      ? `共 ${task.frame_count} 帧`
      : null,
    result,
  };
}
