import { ApiError, isAbortError } from "./errors";
import {
  InvalidResponseError,
  parseErrorDetail,
} from "./contracts";
import { parseTaskReceipt } from "./taskDetails";
import {
  API_TOKEN_KEY,
  API_USER_KEY,
  browserStorage,
  clearApiSession,
  type StorageLike,
} from "./storage";
import { resolveApiBaseUrl } from "./config";
import {
  type ChatAssistantMessage,
  type ChatAssistantMessageCreate,
  type ChatAssistantPort,
  type ChatAssistantRunContext,
  type ChatAssistantStreamEvent,
  type ChatAssistantStreamInput,
  type ChatAssistantTarget,
  type ChatAssistantTaskLookup,
} from "./chatAssistant";
import { readChatAssistantSse } from "./chatAssistantSse";

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;
const CHAT_STREAM_TIMEOUT_MS = 650_000;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;
const CHAT_ASSET_TYPES = new Set(["character", "scene", "prop", "storyboard"]);

export interface ChatAssistantHttpOptions {
  readonly storage?: StorageLike;
  readonly fetcher?: typeof fetch;
  readonly timeoutMs?: number;
  readonly pageUrl?: string;
}

export function createChatAssistantHttpPort(
  apiBaseUrl: string,
  options: ChatAssistantHttpOptions = {},
): ChatAssistantPort {
  const storage = options.storage ?? browserStorage();
  const fetcher = options.fetcher ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    throw new ApiError("invalid-response", "聊天请求超时配置无效。");
  }

  let baseUrl: string;
  try {
    baseUrl = resolveApiBaseUrl(
      apiBaseUrl,
      options.pageUrl ?? (typeof window === "undefined" ? "http://127.0.0.1/" : window.location.href),
    );
  } catch (error) {
    throw new ApiError("invalid-response", error instanceof Error ? error.message : "聊天 API 地址无效。");
  }

  return {
    beginRun(context: ChatAssistantRunContext) {
      if (!isIdentifier(context.actorId) || typeof context.isContextCurrent !== "function") {
        throw new ApiError("aborted", "当前会话无法确认，未开始聊天操作。");
      }
      const token = readSession(storage, context.actorId);
      const capturedContextCheck = context.isContextCurrent;
      const contextIsCurrent = capturedContextCheck.bind(context);
      const isCurrent = () => {
        try {
          return context.isContextCurrent === capturedContextCheck
            && contextIsCurrent()
            && storage.getItem(API_TOKEN_KEY) === token
            && storedActorId(storage) === context.actorId;
        } catch {
          return false;
        }
      };

      async function request(
        url: URL,
        signal: AbortSignal,
        init: RequestInit,
        expectedStatus: number,
      ): Promise<unknown> {
        if (!isCurrent() || signal.aborted) throw aborted();
        const timed = timeoutSignal(signal, timeoutMs);
        try {
          let response: Response;
          try {
            response = await fetcher(url, {
              ...init,
              signal: timed.signal,
              credentials: "omit",
              cache: "no-store",
              redirect: "error",
              referrerPolicy: "no-referrer",
            });
          } catch (error) {
            if (!isCurrent() || signal.aborted) throw aborted();
            if (timed.didTimeout()) throw new ApiError("timeout", "聊天请求超时，结果尚未确认。");
            if (isAbortError(error)) throw new ApiError("network", "聊天请求中断，结果尚未确认。");
            throw new ApiError("network", "无法连接聊天服务，结果尚未确认。");
          }
          if (!isCurrent() || signal.aborted) throw aborted();
          if (response.status === 401) {
            if (storage.getItem(API_TOKEN_KEY) === token && storedActorId(storage) === context.actorId) {
              clearApiSession(storage);
            }
            throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
          }
          if (response.status !== expectedStatus) {
            const detail = await responseDetail(response);
            if (!isCurrent() || signal.aborted) throw aborted();
            throw new ApiError("http", detail ?? `聊天服务返回 HTTP ${response.status}。`, response.status, detail);
          }
          if (expectedStatus === 204) return undefined;
          let value: unknown;
          try {
            value = await response.json();
          } catch {
            throw new ApiError("invalid-response", "聊天服务返回了无法读取的响应。");
          }
          if (!isCurrent() || signal.aborted) throw aborted();
          return value;
        } finally {
          timed.dispose();
        }
      }

      function requestUrl(path: string): URL {
        return new URL(`${baseUrl}${path}`);
      }

      function currentHeaders(json = true): Headers {
        return new Headers({
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          ...(json ? { "Content-Type": "application/json" } : {}),
        });
      }

      async function listMessages(target: ChatAssistantTarget, signal: AbortSignal): Promise<ChatAssistantMessage[]> {
        const url = historyUrl(baseUrl, target);
        const payload = await request(url, signal, { method: "GET", headers: currentHeaders(false) }, 200);
        if (!Array.isArray(payload)) throw new InvalidResponseError("聊天历史必须是数组。");
        const messages = payload.map(parseChatAssistantMessage);
        return messages.filter((message) => {
          if (message.chapterId !== target.chapterId || message.chatMode !== "chat") {
            throw new InvalidResponseError("聊天历史超出了当前章节或模式。");
          }
          if (target.kind === "chapter") {
            return message.frameIndex === null && message.assetType === null && message.assetId === null;
          }
          if (target.kind === "frame") {
            if (message.assetType !== null || message.assetId !== null) {
              throw new InvalidResponseError("分镜聊天历史包含素材消息。");
            }
            return message.frameIndex === target.frameIndex;
          }
          if (message.frameIndex !== null) throw new InvalidResponseError("素材聊天历史包含分镜消息。");
          return message.assetType === target.assetType && message.assetId === target.assetId;
        });
      }

      async function createMessage(
        input: ChatAssistantMessageCreate,
        signal: AbortSignal,
      ): Promise<ChatAssistantMessage> {
        const path = messageCollectionPath(input.target);
        const body: Record<string, unknown> = {
          chapter_id: input.target.chapterId,
          chat_mode: "chat",
          role: input.role,
          content: input.content,
          model_name: input.modelName,
        };
        if (input.target.kind === "frame") body.frame_index = input.target.frameIndex;
        if (input.target.kind === "asset") {
          body.asset_type = input.target.assetType;
          body.asset_id = input.target.assetId;
        }
        const payload = await request(requestUrl(path), signal, {
          method: "POST",
          headers: currentHeaders(),
          body: JSON.stringify(body),
        }, 201);
        const message = parseChatAssistantMessage(payload);
        if (message.id.trim() === ""
          || message.chapterId !== input.target.chapterId
          || message.chatMode !== "chat"
          || message.role !== input.role
          || message.content !== input.content
          || message.modelName !== input.modelName
          || !messageMatchesTarget(message, input.target)) {
          throw new InvalidResponseError("聊天消息回执与当前创建请求不一致。");
        }
        return message;
      }

      async function deleteMessage(
        target: ChatAssistantTarget,
        messageId: string,
        signal: AbortSignal,
      ): Promise<void> {
        if (!isIdentifier(messageId)) throw new ApiError("invalid-response", "聊天消息编号无效。");
        const route = target.kind === "asset" ? "asset-chat-messages" : "chat-messages";
        await request(requestUrl(`/chapters/${encodeURIComponent(target.chapterId)}/${route}/single/${encodeURIComponent(messageId)}`),
          signal, { method: "DELETE", headers: currentHeaders(false) }, 204);
      }

      async function getTaskByMessage(messageId: string, signal: AbortSignal): Promise<ChatAssistantTaskLookup> {
        if (!isIdentifier(messageId)) throw new ApiError("invalid-response", "聊天消息编号无效。");
        const url = requestUrl(`/chat/tasks?message_id=${encodeURIComponent(messageId)}`);
        const payload = await request(url, signal, { method: "GET", headers: currentHeaders(false) }, 200);
        if (!isRecord(payload)) throw new InvalidResponseError("聊天任务回执格式无效。");
        if (payload.status === null && payload.result === null
          && Object.keys(payload).length === 2) {
          return { kind: "missing" };
        }
        if (typeof payload.id !== "string" || payload.id.trim() === "") {
          throw new InvalidResponseError("聊天任务回执缺少服务器任务编号。");
        }
        const receipt = parseTaskReceipt(payload, payload.id);
        if (receipt.type !== "chat" || receipt.messageId !== messageId) {
          throw new InvalidResponseError("聊天任务回执未绑定当前服务器消息。");
        }
        return { kind: "task", receipt };
      }

      async function* stream(
        input: ChatAssistantStreamInput,
        signal: AbortSignal,
      ): AsyncGenerator<ChatAssistantStreamEvent> {
        validateStreamInput(input);
        if (!isCurrent() || signal.aborted) throw aborted();
        const timed = timeoutSignal(signal, CHAT_STREAM_TIMEOUT_MS);
        const url = requestUrl("/chat/stream");
        try {
          let response: Response;
          try {
            response = await fetcher(url, {
              method: "POST",
              headers: new Headers({
                Accept: "text/event-stream",
                "Content-Type": "application/json",
                Authorization: `Bearer ${token}`,
              }),
              body: JSON.stringify({
                model_id: input.modelId,
                messages: input.messages.map(({ role, content }) => ({ role, content })),
                message_id: input.messageId,
                ...(input.promptId === null ? {} : { prompt_id: input.promptId }),
                enable_thinking: input.enableThinking,
              }),
              signal: timed.signal,
              credentials: "omit",
              cache: "no-store",
              redirect: "error",
              referrerPolicy: "no-referrer",
            });
          } catch (error) {
            if (!isCurrent() || signal.aborted) throw aborted();
            if (timed.didTimeout()) throw new ApiError("timeout", "聊天流等待超时，任务结果尚未确认。");
            if (isAbortError(error)) throw new ApiError("network", "聊天流连接中断，任务结果尚未确认。");
            throw new ApiError("network", "聊天流连接中断，任务结果尚未确认。");
          }
          if (!isCurrent() || signal.aborted) throw aborted();
          if (response.status === 401) {
            if (storage.getItem(API_TOKEN_KEY) === token && storedActorId(storage) === context.actorId) {
              clearApiSession(storage);
            }
            throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
          }
          if (!response.ok) {
            const detail = await responseDetail(response);
            if (!isCurrent() || signal.aborted) throw aborted();
            throw new ApiError("http", detail ?? `聊天服务返回 HTTP ${response.status}。`, response.status, detail);
          }
          const contentType = response.headers.get("Content-Type")?.split(";", 1)[0]?.trim().toLowerCase();
          if (response.status !== 200 || contentType !== "text/event-stream" || response.body === null) {
            throw new ApiError("invalid-response", "聊天流响应不可读取，任务结果尚未确认。");
          }
          for await (const event of readChatAssistantSse(response.body, timed.signal)) {
            if (!isCurrent() || signal.aborted) throw aborted();
            yield event;
          }
          if (!isCurrent() || signal.aborted) throw aborted();
        } finally {
          timed.dispose();
        }
      }

      return { isCurrent, listMessages, createMessage, deleteMessage, getTaskByMessage, stream };
    },
  };
}

function historyUrl(baseUrl: string, target: ChatAssistantTarget): URL {
  const url = new URL(`${baseUrl}${target.kind === "asset"
    ? `/chapters/${encodeURIComponent(target.chapterId)}/asset-chat-messages`
    : `/chapters/${encodeURIComponent(target.chapterId)}/chat-messages`}`);
  url.searchParams.set("chat_mode", "chat");
  if (target.kind === "frame") url.searchParams.set("frame_index", String(target.frameIndex));
  if (target.kind === "asset") {
    url.searchParams.set("asset_type", target.assetType);
    url.searchParams.set("asset_id", target.assetId);
  }
  return url;
}

function messageCollectionPath(target: ChatAssistantTarget): string {
  return target.kind === "asset"
    ? `/chapters/${encodeURIComponent(target.chapterId)}/asset-chat-messages`
    : `/chapters/${encodeURIComponent(target.chapterId)}/chat-messages`;
}

function parseChatAssistantMessage(value: unknown): ChatAssistantMessage {
  if (!isRecord(value)) throw new InvalidResponseError("聊天消息格式无效。");
  const id = requiredString(value, "id");
  const chapterId = requiredString(value, "chapter_id");
  const chatMode = requiredString(value, "chat_mode");
  const role = requiredString(value, "role");
  const content = requiredString(value, "content", true);
  const modelName = nullableString(value, "model_name");
  const createdAt = requiredString(value, "created_at");
  const frameIndex = value.frame_index;
  if (frameIndex !== null && frameIndex !== undefined
    && (!Number.isSafeInteger(frameIndex) || (frameIndex as number) < 0)) {
    throw new InvalidResponseError("聊天消息的分镜索引无效。");
  }
  const assetType = value.asset_type;
  const assetId = value.asset_id;
  if ((assetType !== null && assetType !== undefined && (typeof assetType !== "string" || !CHAT_ASSET_TYPES.has(assetType)))
    || (assetId !== null && assetId !== undefined && typeof assetId !== "string")
    || ((assetType === null || assetType === undefined) !== (assetId === null || assetId === undefined))) {
    throw new InvalidResponseError("聊天消息的素材目标无效。");
  }
  if (chatMode !== "chat" || (role !== "user" && role !== "assistant")) {
    throw new InvalidResponseError("聊天消息角色或模式无效。");
  }
  if (frameIndex !== null && frameIndex !== undefined && assetType !== null && assetType !== undefined) {
    throw new InvalidResponseError("聊天消息不能同时绑定分镜与素材目标。");
  }
  if (Number.isNaN(Date.parse(createdAt))) throw new InvalidResponseError("聊天消息时间无效。");
  return {
    id,
    chapterId,
    frameIndex: frameIndex === null || frameIndex === undefined ? null : frameIndex as number,
    assetType: assetType === null || assetType === undefined ? null : assetType as ChatAssistantMessage["assetType"],
    assetId: assetId === null || assetId === undefined ? null : assetId as string,
    chatMode: "chat",
    role,
    content,
    modelName,
    createdAt,
  };
}

function messageMatchesTarget(message: ChatAssistantMessage, target: ChatAssistantTarget): boolean {
  if (target.kind === "chapter") {
    return message.frameIndex === null && message.assetType === null && message.assetId === null;
  }
  if (target.kind === "frame") {
    return message.frameIndex === target.frameIndex && message.assetType === null && message.assetId === null;
  }
  return message.frameIndex === null && message.assetType === target.assetType && message.assetId === target.assetId;
}

function validateStreamInput(input: ChatAssistantStreamInput): void {
  if (!isIdentifier(input.modelId) || !isIdentifier(input.messageId)
    || (input.promptId !== null && !isIdentifier(input.promptId))
    || typeof input.enableThinking !== "boolean" || input.messages.length === 0
    || input.messages.some((message) => !["system", "user", "assistant"].includes(message.role)
      || typeof message.content !== "string" || message.content.length > 1_000_000)) {
    throw new ApiError("invalid-response", "聊天请求内容无效，未发送生成请求。");
  }
}

function readSession(storage: StorageLike, actorId: string): string {
  try {
    const token = storage.getItem(API_TOKEN_KEY);
    if (token === null || token.trim() === "" || token.trim() !== token || CONTROL_CHARACTERS.test(token)
      || storedActorId(storage) !== actorId) {
      throw new ApiError("http", "登录状态已失效，请重新登录。", 401);
    }
    return token;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError("http", "登录状态无法核对，请重新登录。", 401);
  }
}

function storedActorId(storage: StorageLike): string | null {
  try {
    const raw = storage.getItem(API_USER_KEY);
    if (raw === null) return null;
    const user: unknown = JSON.parse(raw);
    if (!isRecord(user) || typeof user.id !== "string" || !isIdentifier(user.id)) return null;
    return user.id;
  } catch {
    return null;
  }
}

function isIdentifier(value: unknown): value is string {
  return typeof value === "string" && value.trim() === value && value.length > 0 && value.length <= 512
    && value !== "." && value !== ".." && !CONTROL_CHARACTERS.test(value);
}

function requiredString(record: Record<string, unknown>, key: string, allowEmpty = false): string {
  const value = record[key];
  if (typeof value !== "string" || (!allowEmpty && value.trim() === "")) {
    throw new InvalidResponseError(`聊天消息中的 ${key} 字段无效。`);
  }
  return value;
}

function nullableString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  if (value !== null && value !== undefined && typeof value !== "string") {
    throw new InvalidResponseError(`聊天消息中的 ${key} 字段无效。`);
  }
  return value === null || value === undefined ? null : value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function responseDetail(response: Response): Promise<string | null> {
  try {
    return parseErrorDetail(await response.json());
  } catch {
    return null;
  }
}

function aborted(): ApiError {
  return new ApiError("aborted", "当前聊天操作已失效。");
}

function timeoutSignal(parent: AbortSignal, timeoutMs: number): {
  signal: AbortSignal;
  didTimeout(): boolean;
  dispose(): void;
} {
  const controller = new AbortController();
  let timedOut = false;
  const timer = globalThis.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromParent = () => controller.abort();
  if (parent.aborted) controller.abort();
  else parent.addEventListener("abort", abortFromParent, { once: true });
  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    dispose() {
      globalThis.clearTimeout(timer);
      parent.removeEventListener("abort", abortFromParent);
    },
  };
}
