import { ApiError } from "./errors";
import type { StoryboardFrame } from "./contracts";

export type StoryboardAssetKind = "character" | "scene" | "prop";
export type StoryboardAssetAction = "extract" | "judge" | "save";

export interface StoryboardAssetCandidate {
  name: string;
  title: string;
  description: string;
  gender: string;
  age: string;
  role: string;
  appearance: string;
  voice_ref: string;
  aliases: string[];
  original_name: string;
  existing_id: string | null;
  is_existing: boolean;
  is_backfill: boolean;
  keep: boolean;
}

export type StoryboardAssetAppearance = string[] | string | null;

export interface StoryboardAssetPlanItem {
  name: string;
  status: "create" | "reuse" | "drop";
  reason: string;
  frames: number[];
  existing_id: string | null;
  existing_name: string | null;
  is_existing: boolean;
  is_backfill: boolean;
  description: string;
  gender: string;
  age: string;
  role: string;
  appearance: string;
  voice_ref: string;
  aliases: string[];
}

export interface StoryboardAssetSavePlan {
  asset_type: StoryboardAssetKind;
  items: StoryboardAssetPlanItem[];
  frame_map: Array<{ frame: number; candidates: string[] }>;
}

export interface StoryboardAssetStreamEvent {
  readonly type: "start" | "status" | "content" | "warning" | "merged" | "plan" | "complete" | "done";
  readonly data: Readonly<Record<string, unknown>>;
}

export interface StoryboardAssetExtractInput {
  chapter_id: string;
  model_id: string;
  prompt_id: string | null;
  enable_thinking: boolean;
  frame_indices: number[] | null;
}

export interface StoryboardAssetJudgeInput {
  chapter_id: string;
  model_id: string;
  prompt_id: string | null;
  enable_thinking: boolean;
  skip_merge: boolean;
  candidates: Array<Record<string, unknown>>;
  storyboard_texts: string[];
  frame_numbers: number[];
}

export interface StoryboardAssetSaveInput {
  chapter_id: string;
  expected_series_id?: string;
  expected_content?: StoryboardFrame[];
  target_frames: number[];
  appearances: StoryboardAssetAppearance[];
  dry_run: boolean;
  candidates: Record<string, unknown>[];
}

export interface StoryboardAssetGenerationServicePort {
  streamExtract(
    kind: StoryboardAssetKind,
    input: StoryboardAssetExtractInput,
    signal: AbortSignal,
  ): AsyncIterable<StoryboardAssetStreamEvent>;
  streamJudge(
    kind: StoryboardAssetKind,
    input: StoryboardAssetJudgeInput,
    signal: AbortSignal,
  ): AsyncIterable<StoryboardAssetStreamEvent>;
  streamSave(
    kind: StoryboardAssetKind,
    input: StoryboardAssetSaveInput,
    signal: AbortSignal,
  ): AsyncIterable<StoryboardAssetStreamEvent>;
}

export class InvalidStoryboardAssetResponseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidStoryboardAssetResponseError";
  }
}

export function parseStoryboardAssetCandidates(value: unknown, kind: StoryboardAssetKind): StoryboardAssetCandidate[] {
  if (!Array.isArray(value)) throw new InvalidStoryboardAssetResponseError("素材候选列表格式无效。");
  return value.map((entry, index) => parseCandidate(entry, kind, index));
}

export function parseStoryboardAssetAppearances(
  value: unknown,
  kind: StoryboardAssetKind,
  expectedLength: number,
): StoryboardAssetAppearance[] {
  if (!Array.isArray(value) || value.length !== expectedLength) {
    throw new InvalidStoryboardAssetResponseError("出场判定数量与本次选择的镜头不一致。");
  }
  return value.map((entry) => {
    if (entry === null) return null;
    if (kind === "scene") {
      if (typeof entry === "string") return entry;
      if (Array.isArray(entry) && entry.every((name) => typeof name === "string")) {
        if (entry.length > 1) throw new InvalidStoryboardAssetResponseError("场景出场判定每个镜头只能有一个名称。");
        return entry[0] ?? "";
      }
      throw new InvalidStoryboardAssetResponseError("场景出场判定格式无效。");
    }
    if (Array.isArray(entry) && entry.every((name) => typeof name === "string")) return [...entry];
    if (typeof entry === "string") return entry === "" ? [] : [entry];
    throw new InvalidStoryboardAssetResponseError("角色或道具出场判定格式无效。");
  });
}

export function parseStoryboardAssetSavePlan(value: unknown, kind: StoryboardAssetKind): StoryboardAssetSavePlan {
  const data = record(value, "保存计划");
  if (data.asset_type !== kind || !Array.isArray(data.items) || !Array.isArray(data.frame_map)) {
    throw new InvalidStoryboardAssetResponseError("保存计划格式无效。");
  }
  const items = data.items.map((value, index): StoryboardAssetPlanItem => {
    const item = record(value, `保存计划.items[${index}]`);
    const status = item.status;
    if (status !== "create" && status !== "reuse" && status !== "drop") {
      throw new InvalidStoryboardAssetResponseError("保存计划中的素材状态无效。");
    }
    const frames = item.frames;
    if (!Array.isArray(frames) || !frames.every((frame) => Number.isSafeInteger(frame) && (frame as number) > 0)) {
      throw new InvalidStoryboardAssetResponseError("保存计划中的镜头编号无效。");
    }
    return {
      name: requiredString(item, "name"),
      status,
      reason: optionalString(item, "reason"),
      frames: [...frames] as number[],
      existing_id: nullableString(item, "existing_id"),
      existing_name: nullableString(item, "existing_name"),
      is_existing: optionalBoolean(item, "is_existing"),
      is_backfill: optionalBoolean(item, "is_backfill"),
      description: optionalString(item, "description"),
      gender: optionalString(item, "gender"),
      age: optionalString(item, "age"),
      role: optionalString(item, "role"),
      appearance: optionalString(item, "appearance"),
      voice_ref: optionalString(item, "voice_ref"),
      aliases: stringList(item.aliases, "aliases"),
    };
  });
  const frameMap = data.frame_map.map((value, index) => {
    const row = record(value, `保存计划.frame_map[${index}]`);
    if (!Number.isSafeInteger(row.frame) || (row.frame as number) < 1) {
      throw new InvalidStoryboardAssetResponseError("保存计划中的镜头编号无效。");
    }
    return { frame: row.frame as number, candidates: stringList(row.candidates, "candidates") };
  });
  return { asset_type: kind, items, frame_map: frameMap };
}

export function candidateName(candidate: StoryboardAssetCandidate, kind: StoryboardAssetKind): string {
  return kind === "scene" ? candidate.title : candidate.name;
}

export function candidatePayload(candidate: StoryboardAssetCandidate, kind: StoryboardAssetKind): Record<string, unknown> {
  const name = candidateName(candidate, kind);
  return {
    name,
    title: kind === "scene" ? name : candidate.title,
    description: candidate.description,
    ...(kind === "character" ? {
      gender: candidate.gender,
      age: candidate.age,
      role: candidate.role,
      appearance: candidate.appearance,
      voice_ref: candidate.voice_ref,
    } : {}),
    aliases: [...candidate.aliases],
    original_name: candidate.original_name || name,
    ...(candidate.existing_id === null ? {} : { existing_id: candidate.existing_id }),
    is_existing: candidate.is_existing,
    is_backfill: candidate.is_backfill,
  };
}

export function buildStoryboardAssetSaveInput(
  kind: StoryboardAssetKind,
  chapterId: string,
  selectedFrames: number[],
  candidates: StoryboardAssetCandidate[],
  appearances: StoryboardAssetAppearance[],
  dryRun: boolean,
  expectedContent?: StoryboardFrame[],
  expectedSeriesId?: string,
): StoryboardAssetSaveInput {
  if (selectedFrames.length === 0 || appearances.length !== selectedFrames.length) {
    throw new InvalidStoryboardAssetResponseError("保存镜头与出场数据没有一一对应。");
  }
  if (new Set(selectedFrames).size !== selectedFrames.length
    || selectedFrames.some((frame) => !Number.isSafeInteger(frame) || frame < 0)) {
    throw new InvalidStoryboardAssetResponseError("保存镜头列表无效。");
  }
  const included = candidates.filter((candidate) => candidate.keep && candidateName(candidate, kind).trim() !== "");
  if (included.length === 0) throw new InvalidStoryboardAssetResponseError("至少保留一个素材候选。");
  const validNames = new Set(included.map((candidate) => candidateName(candidate, kind)));
  const safeAppearances = appearances.map((entry) => {
    if (entry === null) return null;
    if (kind === "scene") {
      if (typeof entry !== "string") throw new InvalidStoryboardAssetResponseError("场景每个镜头只能绑定一个场景。");
      if (entry !== "" && !validNames.has(entry)) {
        throw new InvalidStoryboardAssetResponseError(`场景“${entry}”已不在候选中，请先补入候选或重新绑定。`);
      }
      return entry;
    }
    if (!Array.isArray(entry)) throw new InvalidStoryboardAssetResponseError("角色或道具镜头绑定格式无效。");
    const unknown = entry.find((name) => !validNames.has(name));
    if (unknown !== undefined) {
      throw new InvalidStoryboardAssetResponseError(`素材“${unknown}”已不在候选中，请先补入候选或重新绑定。`);
    }
    return [...new Set(entry)];
  });
  return {
    chapter_id: chapterId,
    ...(expectedSeriesId === undefined ? {} : { expected_series_id: expectedSeriesId }),
    ...(expectedContent === undefined ? {} : { expected_content: expectedContent }),
    target_frames: [...selectedFrames],
    appearances: safeAppearances,
    dry_run: dryRun,
    candidates: included.map((candidate) => ({
      ...candidatePayload(candidate, kind),
      ...(candidateName(candidate, kind) === candidate.name ? {} : { name: candidateName(candidate, kind) }),
    })),
  };
}

function parseCandidate(value: unknown, kind: StoryboardAssetKind, index: number): StoryboardAssetCandidate {
  if (typeof value === "string") {
    const name = value.trim();
    if (name === "") throw new InvalidStoryboardAssetResponseError(`候选[${index}]缺少素材名称。`);
    return {
      ...emptyCandidate(),
      name: kind === "scene" ? "" : name,
      title: kind === "scene" ? name : "",
      original_name: name,
    };
  }
  const item = record(value, `候选[${index}]`);
  const rawName = kind === "scene" ? item.title ?? item.name : item.name;
  if (typeof rawName !== "string" || rawName.trim() === "") {
    throw new InvalidStoryboardAssetResponseError(`候选[${index}]缺少素材名称。`);
  }
  const name = kind === "scene" ? optionalString(item, "name") || rawName : rawName;
  const title = kind === "scene" ? rawName : optionalString(item, "title");
  return {
    ...emptyCandidate(),
    name,
    title,
    description: optionalString(item, "description") || optionalString(item, "visualPrompt"),
    gender: optionalString(item, "gender"),
    age: optionalString(item, "age"),
    role: optionalString(item, "role"),
    appearance: optionalString(item, "appearance"),
    voice_ref: optionalString(item, "voice_ref"),
    aliases: stringList(item.aliases, "aliases"),
    original_name: optionalString(item, "original_name") || rawName,
    existing_id: nullableString(item, "existing_id"),
    is_existing: optionalBoolean(item, "is_existing"),
    is_backfill: optionalBoolean(item, "is_backfill"),
    keep: item.keep === undefined ? true : optionalBoolean(item, "keep"),
  };
}

function emptyCandidate(): StoryboardAssetCandidate {
  return {
    name: "",
    title: "",
    description: "",
    gender: "",
    age: "",
    role: "",
    appearance: "",
    voice_ref: "",
    aliases: [],
    original_name: "",
    existing_id: null,
    is_existing: false,
    is_backfill: false,
    keep: true,
  };
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new InvalidStoryboardAssetResponseError(`${label}格式无效。`);
  }
  return value as Record<string, unknown>;
}

function requiredString(value: Record<string, unknown>, key: string): string {
  const item = value[key];
  if (typeof item !== "string" || item.trim() === "") {
    throw new InvalidStoryboardAssetResponseError(`保存计划.${key}无效。`);
  }
  return item;
}

function nullableString(value: Record<string, unknown>, key: string): string | null {
  const item = value[key];
  if (item === undefined || item === null) return null;
  if (typeof item !== "string") throw new InvalidStoryboardAssetResponseError(`${key}格式无效。`);
  return item;
}

function optionalString(value: Record<string, unknown>, key: string): string {
  const item = value[key];
  if (item === undefined || item === null) return "";
  if (typeof item !== "string") throw new InvalidStoryboardAssetResponseError(`${key}格式无效。`);
  return item;
}

function optionalBoolean(value: Record<string, unknown>, key: string): boolean {
  const item = value[key];
  if (item === undefined) return false;
  if (typeof item !== "boolean") throw new InvalidStoryboardAssetResponseError(`${key}格式无效。`);
  return item;
}

function stringList(value: unknown, key: string): string[] {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new InvalidStoryboardAssetResponseError(`${key}列表格式无效。`);
  }
  return [...value];
}

export function invalidAssetGenerationError(message: string): ApiError {
  return new ApiError("invalid-response", message);
}
