import { ApiError } from "../../shared/api/errors";
import {
  parseCompletedFrames,
  parseTwoPassReceipt,
  type TwoPassEvent,
} from "./contracts";

const EVENT_NAMES = new Set<TwoPassEvent["type"]>([
  "receipt",
  "start",
  "status",
  "content",
  "segments",
  "segment_complete",
  "step2_output",
  "frame_progress",
  "frame",
  "pending",
  "error",
  "complete",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseEventData(eventName: string, source: string): TwoPassEvent | null {
  if (eventName === "payload") return null;
  let value: unknown;
  try {
    value = JSON.parse(source) as unknown;
  } catch {
    throw new ApiError("invalid-response", "分镜生成流包含无法识别的数据。");
  }
  if (!isRecord(value)) {
    throw new ApiError("invalid-response", "分镜生成流包含格式无效的数据。");
  }
  const dataType = typeof value.type === "string" ? value.type : null;
  const resolvedName = eventName === "message" ? dataType ?? eventName : eventName;
  if (eventName !== "message" && dataType !== null && dataType !== eventName) {
    throw new ApiError("invalid-response", "分镜生成流事件名称与数据不一致。");
  }
  if (resolvedName === "payload" || !EVENT_NAMES.has(resolvedName as TwoPassEvent["type"])) return null;

  if (resolvedName === "receipt") {
    return { type: "receipt", data: parseTwoPassReceipt(value) as unknown as Record<string, unknown> };
  }
  if (resolvedName === "complete") {
    return {
      type: "complete",
      data: { ...value, frames: parseCompletedFrames(value.frames) },
    };
  }
  const optionalTextFields: Partial<Record<TwoPassEvent["type"], string[]>> = {
    start: ["message"],
    status: ["message"],
    content: ["text"],
    step2_output: ["text"],
  };
  for (const key of optionalTextFields[resolvedName as TwoPassEvent["type"]] ?? []) {
    if (value[key] !== undefined && typeof value[key] !== "string") {
      throw new ApiError("invalid-response", "分镜生成流包含格式无效的事件。");
    }
  }
  if (resolvedName === "error" && typeof value.message !== "string") {
    throw new ApiError("invalid-response", "分镜生成流的错误信息格式无效。");
  }
  if (resolvedName === "frame" && (!Number.isInteger(value.index) || !isRecord(value.frame))) {
    throw new ApiError("invalid-response", "分镜生成流的镜头事件格式无效。");
  }
  if (resolvedName === "frame_progress"
    && (typeof value.current !== "number" || typeof value.total !== "number")) {
    throw new ApiError("invalid-response", "分镜生成流的进度事件格式无效。");
  }
  if (resolvedName === "segments" && !Array.isArray(value.segments)) {
    throw new ApiError("invalid-response", "分镜生成流的分段事件格式无效。");
  }
  if (resolvedName === "segment_complete" && typeof value.count !== "number") {
    throw new ApiError("invalid-response", "分镜生成流的分段事件格式无效。");
  }
  return { type: resolvedName as TwoPassEvent["type"], data: value };
}

/** Reads one SSE response without assuming network chunks align with text or events. */
export async function* parseTwoPassSse(
  reader: Pick<ReadableStreamDefaultReader<Uint8Array>, "read">,
  signal: AbortSignal,
): AsyncGenerator<TwoPassEvent> {
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let eventName = "";
  let dataLines: string[] = [];
  let receiptSeen = false;
  let terminalSeen = false;

  const dispatch = (): TwoPassEvent | null => {
    const name = eventName || "message";
    const data = dataLines.join("\n");
    eventName = "";
    dataLines = [];
    if (data === "") return null;
    const event = parseEventData(name, data);
    if (event === null) return null;
    if (terminalSeen) {
      throw new ApiError("invalid-response", "分镜生成流在结束后仍返回了事件。");
    }
    if (!receiptSeen) {
      if (event.type !== "receipt") {
        throw new ApiError("invalid-response", "分镜生成流缺少首个提交回执。");
      }
      receiptSeen = true;
    } else if (event.type === "receipt") {
      throw new ApiError("invalid-response", "分镜生成流重复返回了提交回执。");
    }
    if (event.type === "pending" || event.type === "error" || event.type === "complete") {
      terminalSeen = true;
    }
    return event;
  };

  const parseLine = (line: string): TwoPassEvent | null => {
    if (line === "") return dispatch();
    if (line.startsWith(":")) return null;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    if (field === "data") dataLines.push(value);
    return null;
  };

  const takeLines = (final: boolean): TwoPassEvent[] => {
    const events: TwoPassEvent[] = [];
    let start = 0;
    for (let index = 0; index < buffer.length; index += 1) {
      const current = buffer[index];
      if (current !== "\n" && current !== "\r") continue;
      if (current === "\r" && index === buffer.length - 1 && !final) break;
      const line = buffer.slice(start, index);
      const event = parseLine(line);
      if (event !== null) events.push(event);
      if (current === "\r" && buffer[index + 1] === "\n") index += 1;
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
      throw new ApiError("invalid-response", "分镜生成流的文本编码无效。");
    }
    for (const event of takeLines(result.done)) yield event;
    if (result.done) break;
  }

  if (!receiptSeen) throw new ApiError("invalid-response", "分镜生成流没有返回提交回执。");
  if (!terminalSeen) throw new ApiError("network", "分镜生成流提前结束，可查询任务状态后恢复。");
}
