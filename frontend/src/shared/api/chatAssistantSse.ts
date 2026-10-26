import { ApiError } from "./errors";
import type { ChatAssistantStreamEvent } from "./chatAssistant";

const MAX_TOTAL_STREAM_BYTES = 64 * 1024 * 1024;
const MAX_EVENT_CHARACTERS = 2 * 1024 * 1024;
const MAX_RENDERED_TEXT_CHARACTERS = 1_000_000;

export async function* readChatAssistantSse(
  body: ReadableStream<Uint8Array> | null,
  signal: AbortSignal,
): AsyncGenerator<ChatAssistantStreamEvent> {
  if (body === null) throw invalid("聊天流响应没有正文。");

  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  let totalBytes = 0;
  let fullText = "";
  let started = false;
  let completed = false;
  let sawError = false;
  let finished = false;

  try {
    while (true) {
      if (signal.aborted) throw new ApiError("aborted", "已停止接收聊天流；服务器任务仍可能继续。 ");
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await reader.read();
      } catch (error) {
        if (signal.aborted) throw new ApiError("aborted", "已停止接收聊天流；服务器任务仍可能继续。 ");
        throw new ApiError("network", "聊天流中断，任务结果尚未确认。");
      }
      if (signal.aborted) throw new ApiError("aborted", "已停止接收聊天流；服务器任务仍可能继续。 ");
      if (result.value !== undefined) {
        totalBytes += result.value.byteLength;
        if (totalBytes > MAX_TOTAL_STREAM_BYTES) throw invalid("聊天流超过接收上限，任务结果尚未确认。");
      }
      try {
        buffer += decoder.decode(result.value, { stream: !result.done });
      } catch {
        throw invalid("聊天流文本编码无效，任务结果尚未确认。");
      }
      let boundary = findBoundary(buffer);
      while (boundary !== null) {
        const raw = buffer.slice(0, boundary.start);
        buffer = buffer.slice(boundary.end);
        boundary = findBoundary(buffer);
        const data = eventData(raw);
        if (data === null) continue;
        const event = parseEvent(data);

        if (!started) {
          if (event.type !== "start") throw invalid("聊天流未以 start 事件开始。");
          started = true;
        } else if (event.type === "start") {
          throw invalid("聊天流重复发送 start 事件。");
        }

        if (event.type === "content") {
          if (completed || sawError || event.fullText !== fullText + event.text) {
            throw invalid("聊天流正文顺序不一致，任务结果尚未确认。");
          }
          if (event.fullText.length > MAX_RENDERED_TEXT_CHARACTERS) {
            throw invalid("聊天正文超过显示上限，任务结果尚未确认。");
          }
          fullText = event.fullText;
        } else if (event.type === "error") {
          if (completed) throw invalid("聊天流在 complete 后发送了 error。");
          sawError = true;
        } else if (event.type === "complete") {
          if (completed || sawError || event.rawResponse.length > MAX_RENDERED_TEXT_CHARACTERS
            || event.rawResponse !== fullText || fullText === "") {
            throw invalid("聊天流 complete 与正文不一致，任务结果尚未确认。");
          }
          completed = true;
        } else if (event.type === "done") {
          finished = true;
        }

        yield event;
        if (finished) {
          if (buffer.trim() !== "") throw invalid("聊天流在 done 后仍有未读取内容。");
          return;
        }
      }

      if (buffer.length > MAX_EVENT_CHARACTERS) throw invalid("聊天流事件超过接收上限，任务结果尚未确认。");

      if (result.done) break;
    }

    if (buffer !== "" || !started || !finished) {
      throw new ApiError("network", "聊天流提前结束；任务结果尚未确认。 ");
    }
  } finally {
    if (!finished) {
      try {
        await reader.cancel();
      } catch {
        // The consumer may have already closed the stream.
      }
    }
    try {
      reader.releaseLock();
    } catch {
      // A closed stream may already have released its reader.
    }
  }
}

function findBoundary(value: string): { start: number; end: number } | null {
  const newline = value.search(/\r?\n\r?\n/u);
  if (newline < 0) return null;
  const match = value.slice(newline).match(/^\r?\n\r?\n/u);
  if (match === null) return null;
  return { start: newline, end: newline + match[0].length };
}

function eventData(raw: string): string | null {
  const lines = raw.split(/\r?\n/u);
  const data: string[] = [];
  for (const line of lines) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    if (field !== "data") continue;
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    data.push(value);
  }
  return data.length === 0 ? null : data.join("\n");
}

function parseEvent(raw: string): ChatAssistantStreamEvent {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw invalid("聊天流事件格式无效，任务结果尚未确认。");
  }
  if (!isRecord(value) || typeof value.type !== "string") {
    throw invalid("聊天流事件缺少类型，任务结果尚未确认。");
  }
  if (value.type === "start" || value.type === "done") return { type: value.type };
  if (value.type === "content") {
    if (typeof value.text !== "string" || typeof value.full_text !== "string") {
      throw invalid("聊天流正文事件格式无效，任务结果尚未确认。");
    }
    return { type: "content", text: value.text, fullText: value.full_text };
  }
  if (value.type === "status" || value.type === "warning" || value.type === "error") {
    if (typeof value.message !== "string" || value.message.length > 4_000) {
      throw invalid("聊天流状态事件格式无效，任务结果尚未确认。");
    }
    return { type: value.type, message: value.message };
  }
  if (value.type === "complete") {
    if (typeof value.raw_response !== "string") {
      throw invalid("聊天流 complete 事件格式无效，任务结果尚未确认。");
    }
    return { type: "complete", rawResponse: value.raw_response };
  }
  throw invalid("聊天流包含不支持的事件，任务结果尚未确认。");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function invalid(message: string): ApiError {
  return new ApiError("invalid-response", message);
}
