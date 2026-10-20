import { ApiError } from "./errors";
import type {
  FrameReviewCancelReceipt,
  FrameReviewCapabilities,
  FrameReviewProvider,
  FrameReviewReceipt,
  FrameReviewResult,
  FrameReviewTask,
} from "./frameReview";

const providers = new Set<FrameReviewProvider>(["openai", "deepseek", "custom"]);

function objectValue(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError("invalid-response", `${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: Record<string, unknown>, key: string, label: string): string {
  const item = value[key];
  if (typeof item !== "string" || item.trim() === "") {
    throw new ApiError("invalid-response", `${label}.${key}格式无效。`);
  }
  return item;
}

function nullableString(value: Record<string, unknown>, key: string, label: string): string | null {
  const item = value[key];
  if (item === null || item === undefined) return null;
  if (typeof item !== "string") throw new ApiError("invalid-response", `${label}.${key}格式无效。`);
  return item;
}

function finiteNumber(value: Record<string, unknown>, key: string, label: string): number {
  const item = value[key];
  if (typeof item !== "number" || !Number.isFinite(item)) {
    throw new ApiError("invalid-response", `${label}.${key}格式无效。`);
  }
  return item;
}

export function parseFrameReviewCapabilities(value: unknown): FrameReviewCapabilities {
  const payload = objectValue(value, "分镜复核能力");
  const reason = nullableString(payload, "reason", "分镜复核能力");
  if (reason !== null && !["not_configured", "not_ready", "worker_unavailable"].includes(reason)) {
    throw new ApiError("invalid-response", "分镜复核能力.reason格式无效。");
  }
  const supported = payload.supported_providers;
  if (!Array.isArray(supported) || supported.some((item) => typeof item !== "string" || !providers.has(item as FrameReviewProvider))) {
    throw new ApiError("invalid-response", "分镜复核支持的服务商格式无效。");
  }
  if (payload.operation !== "frame.review" || typeof payload.enabled !== "boolean") {
    throw new ApiError("invalid-response", "分镜复核能力与当前操作不匹配。");
  }
  if (payload.enabled && reason !== null) {
    throw new ApiError("invalid-response", "已启用的分镜复核不能包含关闭原因。");
  }
  return {
    operation: "frame.review",
    enabled: payload.enabled,
    reason: reason as FrameReviewCapabilities["reason"],
    supported_providers: supported as FrameReviewProvider[],
  };
}

export function parseFrameReviewReceipt(value: unknown): FrameReviewReceipt {
  const payload = objectValue(value, "分镜复核提交回执");
  if (typeof payload.is_system !== "boolean") {
    throw new ApiError("invalid-response", "分镜复核提交回执.is_system格式无效。");
  }
  const creditCost = finiteNumber(payload, "credit_cost", "分镜复核提交回执");
  if (creditCost < 0) throw new ApiError("invalid-response", "分镜复核报价无效。");
  return {
    task_id: requiredString(payload, "task_id", "分镜复核提交回执"),
    model_name: requiredString(payload, "model_name", "分镜复核提交回执"),
    is_system: payload.is_system,
    credit_cost: creditCost,
  };
}

export function parseFrameReviewTask(value: unknown, expectedTaskId: string): FrameReviewTask {
  const payload = objectValue(value, "分镜复核任务");
  const id = requiredString(payload, "id", "分镜复核任务");
  if (id !== expectedTaskId || payload.type !== "ai-review") {
    throw new ApiError("invalid-response", "服务器返回了其他任务或任务类型。");
  }
  const progressValue = payload.progress;
  const progress = progressValue === null || progressValue === undefined
    ? null
    : finiteNumber(payload, "progress", "分镜复核任务");
  if (progress !== null && (progress < 0 || progress > 100)) {
    throw new ApiError("invalid-response", "分镜复核进度超出范围。");
  }
  const progressMessage = nullableString(payload, "progress_message", "分镜复核任务");
  const result = nullableString(payload, "result", "分镜复核任务");
  const creditCost = finiteNumber(payload, "credit_cost", "分镜复核任务");
  if (creditCost < 0) throw new ApiError("invalid-response", "分镜复核任务报价无效。");
  return {
    id,
    type: "ai-review",
    status: requiredString(payload, "status", "分镜复核任务"),
    progress,
    progress_message: progressMessage,
    result,
    credit_cost: creditCost,
    model_name: nullableString(payload, "model_name", "分镜复核任务"),
  };
}

export function parseFrameReviewResult(
  task: FrameReviewTask,
  expected: { chapterId: string; frameIndex: number; promptId: string },
): FrameReviewResult {
  if (task.type !== "ai-review" || task.status !== "completed" || task.result === null) {
    throw new ApiError("invalid-response", "分镜复核任务尚无可核对的完成结果。");
  }
  let value: unknown;
  try {
    value = JSON.parse(task.result);
  } catch {
    throw new ApiError("invalid-response", "服务器返回的分镜复核结果无法解析。");
  }
  const payload = objectValue(value, "分镜复核结果");
  const chapterId = requiredString(payload, "chapter_id", "分镜复核结果");
  const frameIndex = finiteNumber(payload, "frame_index", "分镜复核结果");
  const promptId = requiredString(payload, "prompt_id", "分镜复核结果");
  const optimizedText = payload.optimized_text;
  const originalText = payload.original_text;
  if (chapterId !== expected.chapterId || frameIndex !== expected.frameIndex || promptId !== expected.promptId
    || typeof optimizedText !== "string" || typeof originalText !== "string") {
    throw new ApiError("invalid-response", "复核结果目标与当前镜头不匹配。");
  }
  return {
    chapter_id: chapterId,
    frame_index: frameIndex,
    prompt_id: promptId,
    optimized_text: optimizedText,
    original_text: originalText,
  };
}

export function parseFrameReviewCancelReceipt(
  value: unknown,
  expectedTaskId: string,
): FrameReviewCancelReceipt {
  const payload = objectValue(value, "分镜复核取消回执");
  const taskId = requiredString(payload, "task_id", "分镜复核取消回执");
  if (taskId !== expectedTaskId || payload.status !== "cancelling") {
    throw new ApiError("invalid-response", "服务器返回了其他任务的取消回执。");
  }
  return { task_id: taskId, status: "cancelling" };
}
