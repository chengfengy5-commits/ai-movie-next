import { ApiError } from "./errors";
import type {
  FrameOptimizeCancelReceipt,
  FrameOptimizeCapabilities,
  FrameOptimizeProvider,
  FrameOptimizeRunningTask,
  FrameOptimizeSubmitInput,
  FrameOptimizeSubmitReceipt,
  FrameOptimizeTaskStatus,
} from "./frameOptimize";

const supportedProviders = new Set<FrameOptimizeProvider>(["openai", "deepseek", "custom"]);

function record(value: unknown, label: string): Record<string, unknown> {
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
  if (item === undefined) throw new ApiError("invalid-response", `${label}.${key}缺失。`);
  if (item === null) return null;
  if (typeof item !== "string") throw new ApiError("invalid-response", `${label}.${key}格式无效。`);
  return item;
}

function requiredText(value: Record<string, unknown>, key: string, label: string): string {
  const item = value[key];
  if (typeof item !== "string") throw new ApiError("invalid-response", `${label}.${key}格式无效。`);
  return item;
}

function progressValue(value: Record<string, unknown>, label: string): number {
  const progress = value.progress;
  if (typeof progress !== "number" || !Number.isFinite(progress) || progress < 0 || progress > 100) {
    throw new ApiError("invalid-response", `${label}.progress格式无效。`);
  }
  return progress;
}

export function parseFrameOptimizeCapabilities(value: unknown): FrameOptimizeCapabilities {
  const payload = record(value, "分镜优化能力");
  const reason = nullableString(payload, "reason", "分镜优化能力");
  if (reason !== null && !["not_configured", "not_ready", "worker_unavailable"].includes(reason)) {
    throw new ApiError("invalid-response", "分镜优化能力.reason格式无效。");
  }
  const providers = payload.supported_providers;
  if (!Array.isArray(providers)
    || providers.some((provider) => typeof provider !== "string" || !supportedProviders.has(provider as FrameOptimizeProvider))) {
    throw new ApiError("invalid-response", "分镜优化支持的服务商格式无效。");
  }
  if (payload.operation !== "frame.optimize" || typeof payload.enabled !== "boolean") {
    throw new ApiError("invalid-response", "分镜优化能力与当前操作不匹配。");
  }
  if (payload.enabled && reason !== null) {
    throw new ApiError("invalid-response", "已启用的分镜优化不能包含关闭原因。");
  }
  if (!payload.enabled && reason === null) {
    throw new ApiError("invalid-response", "已关闭的分镜优化必须说明原因。");
  }
  return {
    operation: "frame.optimize",
    enabled: payload.enabled,
    reason: reason as FrameOptimizeCapabilities["reason"],
    supported_providers: providers as FrameOptimizeProvider[],
  };
}

export function validateFrameOptimizeSubmitInput(input: FrameOptimizeSubmitInput): void {
  if (input.chapter_id.trim() === "" || input.model_id.trim() === "") {
    throw new ApiError("invalid-response", "分镜优化章节和模型不能为空。", null);
  }
  if (typeof input.prompt_id !== "string") {
    throw new ApiError("invalid-response", "分镜优化提示词标识格式无效。", null);
  }
  if (typeof input.enable_thinking !== "boolean") {
    throw new ApiError("invalid-response", "分镜优化思考设置格式无效。", null);
  }
  if (!Number.isSafeInteger(input.concurrency) || input.concurrency < 3 || input.concurrency > 10) {
    throw new ApiError("invalid-response", "分镜优化并发设置必须在3到10之间。", null);
  }
  if (!Array.isArray(input.chapter_texts) || input.chapter_texts.length === 0
    || !Array.isArray(input.frames) || input.frames.length === 0) {
    throw new ApiError("invalid-response", "分镜优化请求缺少章节背景或镜头。", null);
  }
  if (input.chapter_texts.some((text) => typeof text !== "string")) {
    throw new ApiError("invalid-response", "分镜优化章节背景格式无效。", null);
  }
  const indices = new Set<number>();
  let previousIndex = -1;
  for (const frame of input.frames) {
    if (!Number.isSafeInteger(frame.index) || frame.index < 0 || indices.has(frame.index)
      || frame.index <= previousIndex || frame.index >= input.chapter_texts.length
      || typeof frame.text !== "string" || typeof frame.asset_context !== "string") {
      throw new ApiError("invalid-response", "分镜优化镜头格式无效。", null);
    }
    indices.add(frame.index);
    previousIndex = frame.index;
  }
}

export function parseFrameOptimizeSubmitReceipt(
  value: unknown,
  expected: { chapterId: string; frameCount: number },
): FrameOptimizeSubmitReceipt {
  const payload = record(value, "分镜优化提交回执");
  const chapterId = requiredString(payload, "chapter_id", "分镜优化提交回执");
  const frameCount = payload.frame_count;
  if (chapterId !== expected.chapterId || !Number.isSafeInteger(frameCount) || frameCount !== expected.frameCount) {
    throw new ApiError("invalid-response", "分镜优化回执与当前请求不匹配。");
  }
  return {
    task_id: requiredString(payload, "task_id", "分镜优化提交回执"),
    chapter_id: chapterId,
    frame_count: frameCount as number,
  };
}

function parseRunningTask(value: unknown): FrameOptimizeRunningTask {
  const payload = record(value, "运行中的分镜优化任务");
  const createdAt = nullableString(payload, "created_at", "运行中的分镜优化任务");
  return {
    task_id: requiredString(payload, "task_id", "运行中的分镜优化任务"),
    status: requiredString(payload, "status", "运行中的分镜优化任务"),
    progress: progressValue(payload, "运行中的分镜优化任务"),
    progress_message: requiredText(payload, "progress_message", "运行中的分镜优化任务"),
    created_at: createdAt,
  };
}

export function parseFrameOptimizeRunning(value: unknown): FrameOptimizeRunningTask | null {
  const payload = record(value, "运行中的分镜优化查询");
  if (!Object.prototype.hasOwnProperty.call(payload, "task")) {
    throw new ApiError("invalid-response", "运行中的分镜优化查询缺少task字段。");
  }
  return payload.task === null ? null : parseRunningTask(payload.task);
}

export function parseFrameOptimizeTaskStatus(value: unknown, expectedTaskId: string): FrameOptimizeTaskStatus {
  const payload = record(value, "分镜优化任务状态");
  const taskId = requiredString(payload, "task_id", "分镜优化任务状态");
  if (taskId !== expectedTaskId) throw new ApiError("invalid-response", "服务器返回了其他分镜优化任务。");
  if (!Object.prototype.hasOwnProperty.call(payload, "result")) {
    throw new ApiError("invalid-response", "分镜优化任务状态缺少result字段。");
  }
  const rawResult = payload.result;
  if (rawResult !== null && typeof rawResult !== "string"
    && (typeof rawResult !== "object" || Array.isArray(rawResult))) {
    throw new ApiError("invalid-response", "分镜优化任务result格式无效。");
  }
  return {
    task_id: taskId,
    status: requiredString(payload, "status", "分镜优化任务状态"),
    progress: progressValue(payload, "分镜优化任务状态"),
    progress_message: requiredText(payload, "progress_message", "分镜优化任务状态"),
    result: rawResult as FrameOptimizeTaskStatus["result"],
  };
}

export function parseFrameOptimizeCancelReceipt(
  value: unknown,
  expectedTaskId: string,
): FrameOptimizeCancelReceipt {
  const payload = record(value, "分镜优化取消回执");
  const taskId = requiredString(payload, "task_id", "分镜优化取消回执");
  if (taskId !== expectedTaskId || payload.status !== "cancelling") {
    throw new ApiError("invalid-response", "服务器返回了其他任务的取消回执。");
  }
  return { task_id: taskId, status: "cancelling" };
}
