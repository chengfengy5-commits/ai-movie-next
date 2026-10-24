import { ApiError } from "./errors";
import {
  parseStoryboardAssetCandidates,
  parseStoryboardAssetSavePlan,
  type StoryboardAssetKind,
  type StoryboardAssetStreamEvent,
} from "./storyboardAssetGeneration";

const eventTypes = new Set([
  "start", "status", "content", "warning", "merged", "plan", "complete", "done", "error",
]);

export async function* parseStoryboardAssetSse(
  reader: Pick<ReadableStreamDefaultReader<Uint8Array>, "read">,
  signal: AbortSignal,
  action: "extract" | "judge" | "save",
  kind: StoryboardAssetKind,
): AsyncGenerator<StoryboardAssetStreamEvent> {
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];
  let started = false;
  let completeSeen = false;
  let planSeen = false;
  let doneSeen = false;

  const dispatch = (): StoryboardAssetStreamEvent | null => {
    const name = eventName;
    const source = dataLines.join("\n");
    eventName = "";
    dataLines = [];
    if (source === "") return null;

    let value: unknown;
    try {
      value = JSON.parse(source) as unknown;
    } catch {
      throw invalid("素材处理流包含无法识别的数据。");
    }
    if (!isRecord(value) || typeof value.type !== "string" || !eventTypes.has(value.type)) {
      throw invalid("素材处理流包含格式无效的事件。");
    }
    const type = value.type;
    if (name !== "" && name !== "message" && name !== type) {
      throw invalid("素材处理流事件名称与数据不一致。");
    }
    if (doneSeen) throw invalid("素材处理流在结束后仍返回了事件。");
    if (!started && type !== "start") throw invalid("素材处理流缺少开始事件。");
    if (type === "start") {
      if (started || completeSeen) throw invalid("素材处理流重复返回开始事件。");
      requiredMessage(value, "开始事件");
      started = true;
    }
    if (type === "error") {
      if (!started || completeSeen) throw invalid("素材处理流错误事件位置无效。");
      throw new ApiError("http", requiredMessage(value, "错误事件"), null);
    }
    if (type === "status" || type === "warning") requiredMessage(value, "状态事件");
    if (type === "content") {
      if (typeof value.text !== "string" || typeof value.full_text !== "string") {
        throw invalid("素材处理流的文本内容无效。");
      }
    }
    if (type === "merged") {
      if (action !== "judge" || completeSeen) throw invalid("素材合并事件位置无效。");
      const key = mergedCandidateKey(kind);
      parseStoryboardAssetCandidates(value[key], kind);
    }
    if (type === "plan") {
      if (action !== "save" || planSeen || completeSeen) throw invalid("素材保存计划事件位置无效。");
      parseStoryboardAssetSavePlan(value, kind);
      planSeen = true;
    }
    if (type === "complete") {
      if (completeSeen) throw invalid("素材处理流重复返回完成事件。");
      validateComplete(value, action, kind, planSeen);
      completeSeen = true;
    }
    if (type === "done") {
      if (!completeSeen) throw invalid("素材处理流没有完成结果。");
      doneSeen = true;
    } else if (completeSeen && type !== "complete") {
      throw invalid("素材处理流在完成后仍返回了事件。");
    }
    return { type: type as StoryboardAssetStreamEvent["type"], data: value };
  };

  const parseLine = (line: string): StoryboardAssetStreamEvent | null => {
    if (line === "") return dispatch();
    if (line.startsWith(":")) return null;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let fieldValue = separator < 0 ? "" : line.slice(separator + 1);
    if (fieldValue.startsWith(" ")) fieldValue = fieldValue.slice(1);
    if (field === "event") eventName = fieldValue;
    if (field === "data") dataLines.push(fieldValue);
    return null;
  };

  const takeEvents = (final: boolean): StoryboardAssetStreamEvent[] => {
    const events: StoryboardAssetStreamEvent[] = [];
    let start = 0;
    for (let index = 0; index < buffer.length; index += 1) {
      const char = buffer[index];
      if (char !== "\n" && char !== "\r") continue;
      if (char === "\r" && index === buffer.length - 1 && !final) break;
      const event = parseLine(buffer.slice(start, index));
      if (event !== null) events.push(event);
      if (char === "\r" && buffer[index + 1] === "\n") index += 1;
      start = index + 1;
    }
    buffer = buffer.slice(start);
    if (final && buffer !== "") {
      const event = parseLine(buffer);
      if (event !== null) events.push(event);
      buffer = "";
    }
    if (final) {
      const event = dispatch();
      if (event !== null) events.push(event);
    }
    return events;
  };

  while (true) {
    if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
    let result: ReadableStreamReadResult<Uint8Array>;
    try {
      result = await reader.read();
      if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
      buffer += decoder.decode(result.value, { stream: !result.done });
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (signal.aborted) throw new ApiError("aborted", "Request was cancelled.");
      throw invalid("素材处理流的文本编码无效。");
    }
    for (const event of takeEvents(result.done)) yield event;
    if (result.done) break;
  }

  if (!started || !completeSeen || !doneSeen) {
    throw new ApiError("network", "素材处理流提前结束，尚不能确认结果。");
  }
}

function validateComplete(
  value: Record<string, unknown>,
  action: "extract" | "judge" | "save",
  kind: StoryboardAssetKind,
  planSeen: boolean,
): void {
  if (action === "save") {
    if (value.success !== true || typeof value.message !== "string") {
      throw invalid("素材保存结果缺少完成状态。");
    }
    if (value.dry_run === true) {
      const plan = parseStoryboardAssetSavePlan(value.plan, kind);
      if (!planSeen || !isRecord(value.plan)) throw invalid("素材 dry-run 结果缺少计划事件。");
      if (value.plan.asset_type !== plan.asset_type) throw invalid("素材计划与完成结果不一致。");
    } else if (value.dry_run !== undefined && value.dry_run !== false || planSeen) {
      throw invalid("实际保存流不应包含 dry-run 计划事件。");
    }
    return;
  }

  if (typeof value.raw_response !== "string") throw invalid("素材处理完成事件缺少原始文本字段。");
  const candidateKey = action === "extract"
    ? kind === "character" ? "characters" : kind === "scene" ? "scenes" : "props"
    : kind === "character" ? "merged_characters" : kind === "scene" ? "scenes" : "props";
  parseStoryboardAssetCandidates(value[candidateKey], kind);
  if (action === "extract") return;
  if (!Array.isArray(value.appearances)) throw invalid("出场判断完成事件缺少逐镜结果。");
  if (!Array.isArray(value.missing_frames)
    || value.missing_frames.some((frame) => !Number.isSafeInteger(frame) || (frame as number) < 1)) {
    throw invalid("出场判断完成事件中的缺失镜头列表无效。");
  }
}

function mergedCandidateKey(kind: StoryboardAssetKind): string {
  return kind === "character" ? "merged_characters" : kind === "scene" ? "merged_scenes" : "merged_props";
}

function requiredMessage(value: Record<string, unknown>, label: string): string {
  if (typeof value.message !== "string" || value.message.trim() === "") {
    throw invalid(`${label}说明无效。`);
  }
  return value.message;
}

function invalid(message: string): ApiError {
  return new ApiError("invalid-response", message);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
