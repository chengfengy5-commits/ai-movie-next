/**
 * Wire contracts for user-owned models and prompts in the creator settings UI.
 * Parsers deliberately project only fields needed by the UI; in particular,
 * model api_key values are discarded even when the server returns them.
 */

export type CreatorModelCategory = "text" | "image" | "video" | "reasoning" | (string & {});

export type CreatorPromptKnownCategory =
  | "storyboard"
  | "chapter-division"
  | "character-extraction"
  | "scene-extraction"
  | "prop-extraction"
  | "character-design"
  | "scene-design"
  | "prop-design"
  | "video-design"
  | "storyboard-design"
  | "chat-assistant"
  | "frame-optimize"
  | "ai-review"
  | "scene-segmentation"
  | "style"
  | "fuse";

export type CreatorPromptCategory = CreatorPromptKnownCategory | (string & {});

export interface CreatorModel {
  id: string;
  category: CreatorModelCategory;
  name: string;
  provider: string;
  model?: string | null;
  base_url?: string | null;
  params: string | null;
  sort_order: number;
  is_system: boolean;
  user_id: string;
}

export interface CreatorPrompt {
  id: string;
  category: CreatorPromptCategory;
  name: string;
  system_prompt?: string | null;
  user_prompt?: string | null;
  sort_order: number;
  /** Some prompt mutation responses omit this field; list readback supplies it. */
  is_system?: boolean;
  user_id: string;
}

export interface CreatorModelCreateInput {
  category: CreatorModelCategory;
  name: string;
  provider: string;
  api_key: string;
  model?: string | null;
  base_url?: string | null;
  params?: string | null;
  is_system: false;
}

export interface CreatorModelUpdateInput {
  name?: string;
  provider?: string;
  /** Omit to preserve the saved secret. Empty values are not valid updates. */
  api_key?: string;
  model?: string | null;
  base_url?: string | null;
  params?: string | null;
}

export interface CreatorPromptCreateInput {
  category: CreatorPromptCategory;
  name: string;
  system_prompt?: string | null;
  user_prompt?: string | null;
  is_system: false;
}

export interface CreatorPromptUpdateInput {
  name?: string;
  system_prompt?: string | null;
  user_prompt?: string | null;
}

export interface CreatorModelReorderInput {
  category: CreatorModelCategory;
  model_ids: string[];
}

export interface CreatorModelConnectionTestInput {
  provider: string;
  api_key: string;
  base_url: string;
  model: string;
  category: CreatorModelCategory;
}

export interface CreatorModelConnectionTestResult {
  success: boolean;
  message: string;
  status_code?: number;
}

export interface CreatorPromptReorderInput {
  category: CreatorPromptCategory;
  prompt_ids: string[];
}

export interface CreatorSettingsMessage {
  message: string;
}

/** Narrow API surface consumed by the creator settings feature. */
export interface CreatorSettingsServicePort {
  listModels(category: CreatorModelCategory | undefined, signal: AbortSignal): Promise<CreatorModel[]>;
  getModel(id: string, signal: AbortSignal): Promise<CreatorModel>;
  createModel(input: CreatorModelCreateInput, signal: AbortSignal): Promise<CreatorModel>;
  updateModel(id: string, update: CreatorModelUpdateInput, signal: AbortSignal): Promise<CreatorModel>;
  promoteModelToSystem?(id: string, signal: AbortSignal): Promise<CreatorModel>;
  demoteModelToPersonal?(id: string, signal: AbortSignal): Promise<CreatorModel>;
  testModelConnection?(
    input: CreatorModelConnectionTestInput,
    signal: AbortSignal,
  ): Promise<CreatorModelConnectionTestResult>;
  deleteModel(id: string, signal: AbortSignal): Promise<CreatorSettingsMessage>;
  reorderModels(input: CreatorModelReorderInput, signal: AbortSignal): Promise<CreatorSettingsMessage>;
  listPrompts(category: CreatorPromptCategory | undefined, signal: AbortSignal): Promise<CreatorPrompt[]>;
  getPrompt(id: string, signal: AbortSignal): Promise<CreatorPrompt>;
  createPrompt(input: CreatorPromptCreateInput, signal: AbortSignal): Promise<CreatorPrompt>;
  updatePrompt(id: string, update: CreatorPromptUpdateInput, signal: AbortSignal): Promise<CreatorPrompt>;
  deletePrompt(id: string, signal: AbortSignal): Promise<CreatorSettingsMessage>;
  reorderPrompts(input: CreatorPromptReorderInput, signal: AbortSignal): Promise<CreatorSettingsMessage>;
}

export class InvalidCreatorSettingsResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidCreatorSettingsResponseError";
  }
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new InvalidCreatorSettingsResponseError(`${label}必须是对象。`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: Record<string, unknown>, key: string, label: string): string {
  const item = value[key];
  if (typeof item !== "string" || item.trim() === "") {
    throw new InvalidCreatorSettingsResponseError(`${label}.${key}无效。`);
  }
  return item;
}

function optionalNullableString(
  value: Record<string, unknown>,
  key: string,
  label: string,
): string | null | undefined {
  const item = value[key];
  if (item === undefined) {
    return undefined;
  }
  if (item !== null && typeof item !== "string") {
    throw new InvalidCreatorSettingsResponseError(`${label}.${key}无效。`);
  }
  return item;
}

function requiredNumber(value: Record<string, unknown>, key: string, label: string): number {
  const item = value[key];
  if (typeof item !== "number" || !Number.isFinite(item)) {
    throw new InvalidCreatorSettingsResponseError(`${label}.${key}无效。`);
  }
  return item;
}

function requiredBoolean(value: Record<string, unknown>, key: string, label: string): boolean {
  const item = value[key];
  if (typeof item !== "boolean") {
    throw new InvalidCreatorSettingsResponseError(`${label}.${key}无效。`);
  }
  return item;
}

export function parseCreatorModel(value: unknown): CreatorModel {
  const model = record(value, "模型");
  const params = optionalNullableString(model, "params", "模型");
  const modelName = optionalNullableString(model, "model", "模型");
  const baseUrl = optionalNullableString(model, "base_url", "模型");
  return {
    id: requiredString(model, "id", "模型"),
    category: requiredString(model, "category", "模型"),
    name: requiredString(model, "name", "模型"),
    provider: requiredString(model, "provider", "模型"),
    ...(modelName === undefined ? {} : { model: modelName }),
    ...(baseUrl === undefined ? {} : { base_url: baseUrl }),
    params: params ?? null,
    sort_order: requiredNumber(model, "sort_order", "模型"),
    is_system: requiredBoolean(model, "is_system", "模型"),
    user_id: requiredString(model, "user_id", "模型"),
  };
}

export function parseCreatorModelList(value: unknown): CreatorModel[] {
  if (!Array.isArray(value)) {
    throw new InvalidCreatorSettingsResponseError("模型列表无效。");
  }
  return value.map(parseCreatorModel);
}

export function parseCreatorPrompt(value: unknown): CreatorPrompt {
  const prompt = record(value, "提示词");
  const isSystem = prompt.is_system;
  if (isSystem !== undefined && typeof isSystem !== "boolean") {
    throw new InvalidCreatorSettingsResponseError("提示词.is_system无效。");
  }
  const systemPrompt = optionalNullableString(prompt, "system_prompt", "提示词");
  const userPrompt = optionalNullableString(prompt, "user_prompt", "提示词");
  return {
    id: requiredString(prompt, "id", "提示词"),
    category: requiredString(prompt, "category", "提示词"),
    name: requiredString(prompt, "name", "提示词"),
    ...(systemPrompt === undefined ? {} : { system_prompt: systemPrompt }),
    ...(userPrompt === undefined ? {} : { user_prompt: userPrompt }),
    sort_order: requiredNumber(prompt, "sort_order", "提示词"),
    ...(isSystem === undefined ? {} : { is_system: isSystem }),
    user_id: requiredString(prompt, "user_id", "提示词"),
  };
}

export function parseCreatorPromptList(value: unknown): CreatorPrompt[] {
  if (!Array.isArray(value)) {
    throw new InvalidCreatorSettingsResponseError("提示词列表无效。");
  }
  return value.map(parseCreatorPrompt);
}

export function parseCreatorSettingsMessage(value: unknown): CreatorSettingsMessage {
  const response = record(value, "保存结果");
  return { message: requiredString(response, "message", "保存结果") };
}

export function parseCreatorModelConnectionTestResult(value: unknown): CreatorModelConnectionTestResult {
  const response = record(value, "连接测试结果");
  if (typeof response.success !== "boolean") {
    throw new InvalidCreatorSettingsResponseError("连接测试结果.success无效。");
  }
  if (typeof response.message !== "string") {
    throw new InvalidCreatorSettingsResponseError("连接测试结果.message无效。");
  }
  if (response.status_code !== undefined
    && (typeof response.status_code !== "number" || !Number.isInteger(response.status_code))) {
    throw new InvalidCreatorSettingsResponseError("连接测试结果.status_code无效。");
  }
  return {
    success: response.success,
    message: response.message,
    ...(response.status_code === undefined ? {} : { status_code: response.status_code }),
  };
}
