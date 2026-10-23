import type { Chapter, Character, Prop, Scene, Series, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { CreatorPrompt } from "../../shared/api/creatorSettings";
import type { FrameOptimizeSubmitInput } from "../../shared/api/frameOptimize";

export interface FrameOptimizeAssetNames {
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
  prompts: CreatorPrompt[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function frameAssetIds(frame: StoryboardFrame, key: "character" | "scene" | "prop"): string[] {
  const value = frame[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && item.trim() !== "")
    : [];
}

function namedIds<T extends { id: string }>(
  ids: string[],
  entries: T[],
  getName: (entry: T) => string,
): string[] {
  const byId = new Map(entries.map((entry) => [entry.id, entry]));
  return ids.map((id) => {
    const entry = byId.get(id);
    return entry === undefined ? id : getName(entry) || "未命名";
  });
}

function stylePromptBody(series: Pick<Series, "style_prompt_id" | "style_prompt">, prompts: CreatorPrompt[]): string {
  const selectedPrompt = series.style_prompt_id === null
    ? undefined
    : prompts.find((prompt) => prompt.id === series.style_prompt_id);
  const promptBody = selectedPrompt?.user_prompt?.trim() || selectedPrompt?.system_prompt?.trim() || "";
  const selectedText = promptBody === "" ? null : extractStyleText(promptBody);
  const teamText = textValue(series.style_prompt).trim() === ""
    ? null
    : extractStyleText(textValue(series.style_prompt).trim());
  const value = selectedText ?? teamText;
  return value === null ? "" : "【风格提示词】\n" + value;
}

function extractStyleText(raw: string): string | null {
  if (raw === "") return null;
  const parsed = parseStyleValue(raw);
  if (parsed.kind === "text") return parsed.value;
  if (parsed.kind === "invalid-json") return null;

  const textEntries = Object.entries(parsed.value).filter(([key, value]) => (
    typeof value === "string" && value.trim() !== "" && !/_image$/i.test(key)
  ));
  const storyboard = parsed.value.storyboard;
  if (typeof storyboard === "string" && storyboard.trim() !== "") {
    return storyboard.trim();
  }
  const general = textEntries.find(([key]) => /^(general|default|all|base|common|text|style)$/i.test(key));
  if (general !== undefined) return String(general[1]).trim();
  return textEntries.length === 1
    ? String(textEntries[0]?.[1]).trim()
    : null;
}

type ParsedStyleValue =
  | { kind: "text"; value: string }
  | { kind: "object"; value: Record<string, unknown> }
  | { kind: "invalid-json" };

function parseStyleValue(raw: string): ParsedStyleValue {
  const trimmed = raw.trim();
  try {
    const value: unknown = JSON.parse(raw);
    return isRecord(value) ? { kind: "object", value } : { kind: "invalid-json" };
  } catch {
    if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return { kind: "text", value: raw };
    if (!trimmed.startsWith("{")) return { kind: "invalid-json" };
    try {
      const repaired = trimmed.replace(/"([^"]*)"/g, (_match, inner: string) => (
        "\"" + inner.replace(/\n/g, "\\n").replace(/\r/g, "") + "\""
      ));
      const value: unknown = JSON.parse(repaired);
      return isRecord(value) ? { kind: "object", value } : { kind: "invalid-json" };
    } catch {
      return { kind: "invalid-json" };
    }
  }
}

export function stripFrameOptimizeShotMarkers(value: string): string {
  if (!/(^|\n)\s*Shot\s*\d+/i.test(value) && !/(^|\n)\s*转场\s*[:：]/.test(value)) {
    return value;
  }
  const droppedLines = [
    /^\s*Shot\s*\d+/i,
    /^\s*转场\s*[:：]/,
    /^\s*景别\s*\/\s*机位\s*[:：]/,
    /^\s*运镜\s*[:：]/,
    /^\s*音效\s*[:：]/,
  ];
  return value.split("\n")
    .filter((line) => !droppedLines.some((pattern) => pattern.test(line)))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function frameOptimizeSourceText(frame: StoryboardFrame): string {
  const original = textValue(frame.original_text);
  return original || textValue(frame.text) || "";
}

export function buildFrameOptimizeAssetContext(
  frame: StoryboardFrame,
  series: Pick<Series, "style_prompt_id" | "style_prompt">,
  assets: FrameOptimizeAssetNames,
): string {
  const parts: string[] = [];
  const characters = namedIds(frameAssetIds(frame, "character"), assets.characters, (item) => item.name);
  const scenes = namedIds(frameAssetIds(frame, "scene"), assets.scenes, (item) => item.title);
  const props = namedIds(frameAssetIds(frame, "prop"), assets.props, (item) => item.name);
  if (characters.length > 0) parts.push("角色：" + characters.join("、"));
  if (scenes.length > 0) parts.push("场景：" + scenes.join("、"));
  if (props.length > 0) parts.push("道具：" + props.join("、"));
  const assetContext = parts.length > 0 ? "【当前分镜资产信息】\n" + parts.join("\n") : "";
  const style = stylePromptBody(series, assets.prompts);
  if (style !== "" && assetContext !== "") return style + "\n\n" + assetContext;
  return style || assetContext;
}

export function buildFrameOptimizeRequest(
  chapter: Chapter,
  selectedIndices: number[],
  series: Pick<Series, "style_prompt_id" | "style_prompt">,
  assets: FrameOptimizeAssetNames,
  options: { modelId: string; promptId: string; enableThinking: boolean; concurrency: number },
): FrameOptimizeSubmitInput | null {
  const content = chapter.content;
  if (!Array.isArray(content) || content.length === 0) return null;
  const indices = [...new Set(selectedIndices)].sort((left, right) => left - right);
  if (indices.length === 0 || indices.some((index) => !Number.isSafeInteger(index) || index < 0 || index >= content.length)) {
    return null;
  }
  const frames = content.map((frame) => isRecord(frame) ? frame as StoryboardFrame : null);
  if (frames.some((frame) => frame === null)) return null;
  const validFrames = frames as StoryboardFrame[];
  const chapterTexts = validFrames.map((frame) => {
    const assetSummary = buildFrameAssetSummary(frame, assets);
    const story = stripFrameOptimizeShotMarkers(frameOptimizeSourceText(frame)).trim();
    const body = [assetSummary, story].filter((part) => part !== "").join("\n");
    return body === "" ? "【剧情片段】" : "【剧情片段】\n" + body;
  });
  return {
    chapter_id: chapter.id,
    model_id: options.modelId,
    prompt_id: options.promptId,
    enable_thinking: options.enableThinking,
    concurrency: options.concurrency,
    chapter_texts: chapterTexts,
    frames: indices.map((index) => ({
      index,
      text: frameOptimizeSourceText(validFrames[index] as StoryboardFrame),
      asset_context: buildFrameOptimizeAssetContext(validFrames[index] as StoryboardFrame, series, assets),
    })),
  };
}

function buildFrameAssetSummary(frame: StoryboardFrame, assets: FrameOptimizeAssetNames): string {
  const parts: string[] = [];
  const characters = namedIds(frameAssetIds(frame, "character"), assets.characters, (item) => item.name);
  const scenes = namedIds(frameAssetIds(frame, "scene"), assets.scenes, (item) => item.title);
  const props = namedIds(frameAssetIds(frame, "prop"), assets.props, (item) => item.name);
  if (characters.length > 0) parts.push("角色：" + characters.join("、"));
  if (scenes.length > 0) parts.push("场景：" + scenes.join("、"));
  if (props.length > 0) parts.push("道具：" + props.join("、"));
  return parts.length === 0 ? "" : "（" + parts.join("；") + "）";
}

export function restoreFrameOptimizeOriginals(
  content: StoryboardFrame[] | null,
  selectedIndices: number[],
): { content: StoryboardFrame[]; restoredIndices: number[] } | null {
  if (!Array.isArray(content)) return null;
  const indices = [...new Set(selectedIndices)].sort((left, right) => left - right);
  if (indices.some((index) => !Number.isSafeInteger(index) || index < 0 || index >= content.length)) return null;
  let nextContent = content;
  const restoredIndices: number[] = [];
  for (const index of indices) {
    const frame = content[index];
    if (!isRecord(frame) || typeof frame.original_text !== "string" || frame.original_text === "") continue;
    if (nextContent === content) nextContent = content.slice();
    const restored: Record<string, unknown> = { ...frame, text: frame.original_text };
    delete restored.original_text;
    nextContent[index] = restored;
    restoredIndices.push(index);
  }
  return { content: nextContent, restoredIndices };
}

export function frameOptimizeChapterContentAfterWrite(content: StoryboardFrame[]): StoryboardFrame[] {
  return content.map((frame) => isRecord(frame) && !Object.prototype.hasOwnProperty.call(frame, "storyboard")
    ? { ...frame, storyboard: [] }
    : frame);
}

export function parseFrameOptimizePartialResult(
  result: Record<string, unknown> | string | null,
): { partial: Array<{ index: number; optimized_text: string }>; error: string | null; valid: boolean } {
  if (result === null) return { partial: [], error: null, valid: true };
  if (typeof result === "string") return { partial: [], error: null, valid: false };
  const rawPartial = result.partial;
  if (!Array.isArray(rawPartial)) return { partial: [], error: null, valid: false };
  const partial: Array<{ index: number; optimized_text: string }> = [];
  const seen = new Set<number>();
  for (const entry of rawPartial) {
    if (!isRecord(entry) || !Number.isSafeInteger(entry.index) || (entry.index as number) < 0
      || typeof entry.optimized_text !== "string" || seen.has(entry.index as number)) {
      return { partial: [], error: null, valid: false };
    }
    seen.add(entry.index as number);
    partial.push({ index: entry.index as number, optimized_text: entry.optimized_text });
  }
  const error = result.error;
  if (error !== undefined && typeof error !== "string") return { partial: [], error: null, valid: false };
  return { partial, error: typeof error === "string" ? error : null, valid: true };
}

export function applyFrameOptimizePartialToContent(
  content: StoryboardFrame[] | null,
  requestFrames: Array<{ index: number; text: string }>,
  partial: Array<{ index: number; optimized_text: string }>,
): StoryboardFrame[] | null {
  if (!Array.isArray(content)) return null;
  const requestedByIndex = new Map<number, string>();
  for (const frame of requestFrames) {
    if (!Number.isSafeInteger(frame.index) || frame.index < 0 || frame.index >= content.length
      || requestedByIndex.has(frame.index) || typeof frame.text !== "string") return null;
    requestedByIndex.set(frame.index, frame.text);
  }
  const resultByIndex = new Map<number, string>();
  for (const result of partial) {
    if (!requestedByIndex.has(result.index) || resultByIndex.has(result.index)
      || typeof result.optimized_text !== "string") return null;
    resultByIndex.set(result.index, result.optimized_text);
  }
  if (resultByIndex.size === 0) return content;
  const next = content.slice();
  for (const [index, optimizedText] of resultByIndex) {
    const source = content[index];
    if (!isRecord(source)) return null;
    const updated: Record<string, unknown> = { ...source };
    const requestText = requestedByIndex.get(index) ?? "";
    if (!textValue(source.original_text) && requestText !== "") updated.original_text = requestText;
    updated.text = optimizedText;
    next[index] = updated;
  }
  return next;
}

export function sameFrameOptimizeAssets(
  before: StoryboardAsset[],
  after: StoryboardAsset[],
  applied: Array<{ index: number; optimized_text: string }>,
): boolean {
  if (!sameFrameOptimizeAssetIdentity(before, after)) return false;
  const afterById = new Map(after.map((asset) => [asset.id, asset]));
  const appliedByIndex = new Map(applied.map((entry) => [entry.index, entry.optimized_text]));
  return before.every((asset) => {
    const next = afterById.get(asset.id);
    if (next === undefined) return false;
    const updatedText = appliedByIndex.get(asset.frame_index);
    return updatedText === undefined
      ? next.description === asset.description
      : next.description === updatedText;
  });
}

export function sameFrameOptimizeAssetIdentity(before: StoryboardAsset[], after: StoryboardAsset[]): boolean {
  if (before.length !== after.length) return false;
  const afterById = new Map(after.map((asset) => [asset.id, asset]));
  if (afterById.size !== after.length) return false;
  return before.every((asset) => {
    const next = afterById.get(asset.id);
    return next !== undefined && next.series_id === asset.series_id && next.chapter_id === asset.chapter_id
      && next.frame_index === asset.frame_index && next.name === asset.name && next.image_url === asset.image_url
      && next.created_at === asset.created_at;
  });
}

export function sameFrameOptimizeAssetsAfterChapterWrite(
  before: StoryboardAsset[],
  after: StoryboardAsset[],
  content: StoryboardFrame[],
): boolean {
  const expectedById = new Map<string, { frame: StoryboardFrame; index: number }>();
  for (const [index, frame] of content.entries()) {
    if (!isRecord(frame)) return false;
    const references = frame.storyboard;
    const assetId = Array.isArray(references) ? references[0] : undefined;
    if (assetId === undefined) continue;
    if (typeof assetId !== "string" || assetId.trim() === "" || expectedById.has(assetId)) return false;
    expectedById.set(assetId, { frame, index });
  }

  const beforeById = new Map(before.map((asset) => [asset.id, asset]));
  const afterById = new Map(after.map((asset) => [asset.id, asset]));
  if (beforeById.size !== before.length || afterById.size !== after.length
    || afterById.size !== expectedById.size) return false;

  return [...expectedById].every(([id, expected]) => {
    const previous = beforeById.get(id);
    const next = afterById.get(id);
    if (previous === undefined || next === undefined) return false;
    const text = expected.frame.text;
    const originalText = expected.frame.original_text;
    const description = text || originalText || "";
    if (typeof description !== "string") return false;
    const name = `分镜 ${expected.index + 1}`;
    const syncChanges = next.frame_index !== previous.frame_index
      || next.name !== previous.name || next.description !== previous.description;
    return next.series_id === previous.series_id
      && next.chapter_id === previous.chapter_id
      && next.frame_index === expected.index
      && next.name === name
      && next.description === description
      && next.image_url === previous.image_url
      && next.created_at === previous.created_at
      && typeof next.updated_at === "string" && next.updated_at.trim() !== ""
      && (syncChanges || next.updated_at === previous.updated_at);
  });
}
