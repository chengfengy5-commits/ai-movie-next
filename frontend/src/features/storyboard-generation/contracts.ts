import { InvalidResponseError, type StoryboardFrame } from "../../shared/api/contracts";

export interface StoryboardModelChoice {
  id: string;
  name: string;
  category: "text" | "reasoning";
  provider: string;
  is_system: boolean;
  cost_per_call: number;
}

export interface StoryboardPromptChoice {
  id: string;
  name: string;
  category: "scene-segmentation" | "storyboard";
  is_system: boolean;
}

export interface TwoPassQuoteInput {
  series_id: string;
  raw_content: string;
  model_id: string;
  scene_prompt_id: string;
  frame_prompt_id?: string;
  temperature: number;
  enable_thinking: boolean;
  skip_frame_generation: boolean;
}

export interface TwoPassQuote {
  quote_id: string;
  operation: "storyboard.two_pass";
  model_id: string;
  model_name: string;
  model_source: string;
  unit_price: number;
  multiplier: number;
  quoted_amount: number;
  expires_at: string;
}

export interface TwoPassReceipt {
  submission_id: string;
  operation: "storyboard.two_pass";
  task_id: string;
  status: string;
  quoted_amount: number;
  billing_status: string;
}

export interface TwoPassEvent {
  type: "receipt" | "start" | "status" | "content" | "segments" | "segment_complete"
    | "step2_output" | "frame_progress" | "frame" | "pending" | "error" | "complete";
  data: Record<string, unknown>;
}

export interface TwoPassSubmission {
  submission_id: string;
  operation: "storyboard.two_pass";
  task_id: string;
  status: string;
  quoted_amount: number;
  billing_status: string;
}

export interface TwoPassTask {
  id: string;
  status: string;
  billing_status: string;
  quoted_amount: number;
  result: unknown;
}

export interface StoryboardGenerationServicePort {
  listModels(signal: AbortSignal): Promise<StoryboardModelChoice[]>;
  listPrompts(signal: AbortSignal): Promise<StoryboardPromptChoice[]>;
  quote(input: TwoPassQuoteInput, signal: AbortSignal): Promise<TwoPassQuote>;
  stream(quoteId: string, idempotencyKey: string, signal: AbortSignal): AsyncIterable<TwoPassEvent>;
  getSubmission(idempotencyKey: string, signal: AbortSignal): Promise<TwoPassSubmission>;
  getTask(taskId: string, signal: AbortSignal): Promise<TwoPassTask>;
}

export interface SeriesImportInput {
  name?: string;
  description?: string;
  data: unknown;
  team_id?: string;
  claim?: false;
}

export interface SeriesImportResult {
  id: string;
  name: string;
  message: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  if (typeof value !== "string" || value.trim() === "") {
    throw new InvalidResponseError("AI 分镜服务返回了无效数据。");
  }
  return value;
}

function requiredAmount(record: Record<string, unknown>, key: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) {
    throw new InvalidResponseError("AI 分镜服务返回了无效价格。");
  }
  return value;
}

export function parseStoryboardModels(value: unknown): StoryboardModelChoice[] {
  if (!Array.isArray(value)) throw new InvalidResponseError("模型目录格式无效。");
  return value.flatMap((item): StoryboardModelChoice[] => {
    if (!isRecord(item) || item.is_active === false) return [];
    if (item.category !== "text" && item.category !== "reasoning") return [];
    if (typeof item.is_system !== "boolean"
      || typeof item.provider !== "string") {
      throw new InvalidResponseError("模型目录中包含格式无效的项目。");
    }
    return [{
      id: requiredString(item, "id"),
      name: requiredString(item, "name"),
      category: item.category,
      provider: item.provider,
      is_system: item.is_system,
      cost_per_call: requiredAmount(item, "cost_per_call"),
    }];
  });
}

export function parseStoryboardPrompts(value: unknown): StoryboardPromptChoice[] {
  if (!Array.isArray(value)) throw new InvalidResponseError("提示词目录格式无效。");
  return value.flatMap((item): StoryboardPromptChoice[] => {
    if (!isRecord(item) || item.is_active === false) return [];
    if ((item.category !== "scene-segmentation" && item.category !== "storyboard")
      || typeof item.is_system !== "boolean") {
      return [];
    }
    return [{
      id: requiredString(item, "id"),
      name: requiredString(item, "name"),
      category: item.category,
      is_system: item.is_system,
    }];
  });
}

export function parseTwoPassQuote(value: unknown): TwoPassQuote {
  if (!isRecord(value) || value.operation !== "storyboard.two_pass") {
    throw new InvalidResponseError("AI 分镜报价格式无效。");
  }
  return {
    quote_id: requiredString(value, "quote_id"),
    operation: "storyboard.two_pass",
    model_id: requiredString(value, "model_id"),
    model_name: requiredString(value, "model_name"),
    model_source: requiredString(value, "model_source"),
    unit_price: requiredAmount(value, "unit_price"),
    multiplier: requiredAmount(value, "multiplier"),
    quoted_amount: requiredAmount(value, "quoted_amount"),
    expires_at: requiredString(value, "expires_at"),
  };
}

export function parseTwoPassSubmission(value: unknown): TwoPassSubmission {
  if (!isRecord(value) || value.operation !== "storyboard.two_pass") {
    throw new InvalidResponseError("分镜生成记录格式无效。");
  }
  return {
    submission_id: requiredString(value, "submission_id"),
    operation: "storyboard.two_pass",
    task_id: requiredString(value, "task_id"),
    status: requiredString(value, "status"),
    quoted_amount: requiredAmount(value, "quoted_amount"),
    billing_status: requiredString(value, "billing_status"),
  };
}

export function parseTwoPassTask(value: unknown): TwoPassTask {
  if (!isRecord(value)) throw new InvalidResponseError("分镜任务格式无效。");
  return {
    id: requiredString(value, "id"),
    status: requiredString(value, "status"),
    billing_status: requiredString(value, "billing_status"),
    quoted_amount: requiredAmount(value, "quoted_amount"),
    result: value.result,
  };
}

export function parseTwoPassReceipt(value: unknown): TwoPassReceipt {
  if (!isRecord(value) || value.operation !== "storyboard.two_pass") {
    throw new InvalidResponseError("分镜生成回执格式无效。");
  }
  return {
    submission_id: requiredString(value, "submission_id"),
    operation: "storyboard.two_pass",
    task_id: requiredString(value, "task_id"),
    status: requiredString(value, "status"),
    quoted_amount: requiredAmount(value, "quoted_amount"),
    billing_status: requiredString(value, "billing_status"),
  };
}

export function parseCompletedFrames(value: unknown): StoryboardFrame[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new InvalidResponseError("生成结果没有可编辑的分镜。");
  }
  return value.map((frame) => {
    if (!isRecord(frame) || typeof frame.text !== "string") {
      throw new InvalidResponseError("生成结果中包含格式无效的分镜。");
    }
    return { ...frame };
  });
}

export function parseRecoveredFrames(value: unknown): StoryboardFrame[] | null {
  let result = value;
  if (typeof result === "string") {
    try {
      result = JSON.parse(result) as unknown;
    } catch {
      return null;
    }
  }
  if (!isRecord(result)) return null;
  const frames = result.frames;
  try {
    return parseCompletedFrames(frames);
  } catch {
    return null;
  }
}
