import type { TaskReceipt } from "./taskDetails";

export type ChatAssistantAssetType = "character" | "scene" | "prop" | "storyboard";

export type ChatAssistantTarget =
  | { readonly kind: "chapter"; readonly chapterId: string }
  | { readonly kind: "frame"; readonly chapterId: string; readonly frameIndex: number }
  | {
      readonly kind: "asset";
      readonly chapterId: string;
      readonly assetType: ChatAssistantAssetType;
      readonly assetId: string;
    };

export interface ChatAssistantMessage {
  readonly id: string;
  readonly chapterId: string;
  readonly frameIndex: number | null;
  readonly assetType: ChatAssistantAssetType | null;
  readonly assetId: string | null;
  readonly chatMode: "chat";
  readonly role: "user" | "assistant";
  readonly content: string;
  readonly modelName: string | null;
  readonly createdAt: string;
}

export interface ChatAssistantMessageCreate {
  readonly target: ChatAssistantTarget;
  readonly role: "user" | "assistant";
  readonly content: string;
  readonly modelName: string | null;
}

export interface ChatAssistantStreamMessage {
  readonly role: "system" | "user" | "assistant";
  readonly content: string;
}

export interface ChatAssistantStreamInput {
  readonly modelId: string;
  readonly messages: readonly ChatAssistantStreamMessage[];
  readonly messageId: string;
  readonly promptId: string | null;
  readonly enableThinking: boolean;
}

export type ChatAssistantTaskLookup =
  | { readonly kind: "missing" }
  | { readonly kind: "task"; readonly receipt: TaskReceipt };

export type ChatAssistantStreamEvent =
  | { readonly type: "start" }
  | { readonly type: "content"; readonly text: string; readonly fullText: string }
  | { readonly type: "status" | "warning"; readonly message: string }
  | { readonly type: "error"; readonly message: string }
  | { readonly type: "complete"; readonly rawResponse: string }
  | { readonly type: "done" };

export interface ChatAssistantRunContext {
  readonly actorId: string;
  isContextCurrent(): boolean;
}

export interface ChatAssistantRun {
  isCurrent(): boolean;
  listMessages(target: ChatAssistantTarget, signal: AbortSignal): Promise<ChatAssistantMessage[]>;
  createMessage(input: ChatAssistantMessageCreate, signal: AbortSignal): Promise<ChatAssistantMessage>;
  deleteMessage(target: ChatAssistantTarget, messageId: string, signal: AbortSignal): Promise<void>;
  getTaskByMessage(messageId: string, signal: AbortSignal): Promise<ChatAssistantTaskLookup>;
  stream(input: ChatAssistantStreamInput, signal: AbortSignal): AsyncIterable<ChatAssistantStreamEvent>;
}

export interface ChatAssistantPort {
  beginRun(context: ChatAssistantRunContext): ChatAssistantRun;
}

export interface ChatContextMetadata {
  readonly modelId: string;
  readonly modelName: string;
  readonly promptId: string | null;
}

export interface ParsedChatContent {
  readonly metadata: ChatContextMetadata | null;
  readonly text: string;
}

const CHAT_CONTEXT_PREFIX = "__CTX__:";
const MAX_CONTEXT_FIELD_LENGTH = 512;

export function encodeChatContent(metadata: ChatContextMetadata, text: string): string {
  return `${CHAT_CONTEXT_PREFIX}${JSON.stringify(metadata)}\n${text}`;
}

export function parseChatContent(content: string): ParsedChatContent {
  if (!content.startsWith(CHAT_CONTEXT_PREFIX)) return { metadata: null, text: content };
  const newline = content.indexOf("\n");
  if (newline < 0) return { metadata: null, text: content };

  try {
    const value: unknown = JSON.parse(content.slice(CHAT_CONTEXT_PREFIX.length, newline));
    if (!isRecord(value)
      || Object.keys(value).length !== 3
      || !Object.prototype.hasOwnProperty.call(value, "modelId")
      || !Object.prototype.hasOwnProperty.call(value, "modelName")
      || !Object.prototype.hasOwnProperty.call(value, "promptId")
      || !isContextString(value.modelId)
      || !isContextString(value.modelName)
      || (value.promptId !== null && !isContextString(value.promptId))) {
      return { metadata: null, text: content };
    }
    return {
      metadata: {
        modelId: value.modelId,
        modelName: value.modelName,
        promptId: value.promptId,
      },
      text: content.slice(newline + 1),
    };
  } catch {
    return { metadata: null, text: content };
  }
}

function isContextString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= MAX_CONTEXT_FIELD_LENGTH;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
