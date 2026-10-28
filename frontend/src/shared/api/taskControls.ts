import { ApiError } from "./errors";

export type TaskCancelReason =
  | "available"
  | "already_requested"
  | "terminal"
  | "unsupported_operation"
  | "runtime_unavailable"
  | "unsafe_task_graph";

export interface TaskControls {
  taskId: string;
  status: string;
  cancel: {
    allowed: boolean;
    reason: TaskCancelReason;
  };
}

export interface TaskCancelAcknowledgement {
  taskId: string;
  /** Compatibility receipt only; the independent task GET remains authoritative. */
  status: string;
}

const taskCancelReasons = new Set<TaskCancelReason>([
  "available",
  "already_requested",
  "terminal",
  "unsupported_operation",
  "runtime_unavailable",
  "unsafe_task_graph",
]);

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new ApiError("invalid-response", `${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function nonEmptyString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new ApiError("invalid-response", `${label}格式无效。`);
  }
  return value;
}

export function parseTaskControls(value: unknown, expectedTaskId: string): TaskControls {
  const payload = record(value, "任务操作能力");
  const taskId = nonEmptyString(payload.task_id, "任务操作能力.task_id");
  const status = nonEmptyString(payload.status, "任务操作能力.status");
  const cancel = record(payload.cancel, "任务操作能力.cancel");
  const allowed = cancel.allowed;
  const reason = cancel.reason;

  if (taskId !== expectedTaskId) {
    throw new ApiError("invalid-response", "服务器返回了其他任务的操作能力。");
  }
  if (typeof allowed !== "boolean" || typeof reason !== "string" || !taskCancelReasons.has(reason as TaskCancelReason)) {
    throw new ApiError("invalid-response", "任务取消能力字段格式无效。");
  }
  if (allowed !== (reason === "available")) {
    throw new ApiError("invalid-response", "任务取消能力与原因不一致。");
  }

  return {
    taskId,
    status,
    cancel: { allowed, reason: reason as TaskCancelReason },
  };
}

export function parseTaskCancelAcknowledgement(
  value: unknown,
  expectedTaskId: string,
): TaskCancelAcknowledgement {
  const payload = record(value, "取消请求回执");
  const taskId = nonEmptyString(payload.task_id, "取消请求回执.task_id");
  const status = nonEmptyString(payload.status, "取消请求回执.status");
  if (taskId !== expectedTaskId) {
    throw new ApiError("invalid-response", "服务器返回了其他任务的取消回执。");
  }
  return { taskId, status };
}
