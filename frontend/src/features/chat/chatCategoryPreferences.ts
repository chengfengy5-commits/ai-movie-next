import type { ChatAssistantAssetType, ChatAssistantTarget } from "../../shared/api/chatAssistant";

export type ChatPreferenceCategory = "general" | "frame" | ChatAssistantAssetType;

export interface ChatCategoryPreferenceScope {
  readonly apiBaseUrl: string | null;
  readonly userId: string;
}

export interface ChatCategoryPreference {
  readonly modelId: string;
  readonly promptId: string;
}

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

const storageKeyPrefix = "haoai:chat-assistant-selection:v1";

export function chatPreferenceCategory(target: ChatAssistantTarget | null): ChatPreferenceCategory {
  if (target === null || target.kind === "chapter") return "general";
  if (target.kind === "frame") return "frame";
  return target.assetType;
}

export function chatCategoryPreferenceKey(
  scope: ChatCategoryPreferenceScope,
  category: ChatPreferenceCategory,
): string {
  return `${storageKeyPrefix}:${encodeURIComponent(scope.apiBaseUrl ?? "")}:${encodeURIComponent(scope.userId)}:${category}`;
}

export function readChatCategoryPreference(
  scope: ChatCategoryPreferenceScope,
  category: ChatPreferenceCategory,
  storage?: PreferenceStorage,
): ChatCategoryPreference | null {
  try {
    const source = storage ?? globalThis.localStorage;
    const raw = source.getItem(chatCategoryPreferenceKey(scope, category));
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
    const record = value as Record<string, unknown>;
    if (typeof record.modelId !== "string" || typeof record.promptId !== "string") return null;
    return { modelId: record.modelId, promptId: record.promptId };
  } catch {
    return null;
  }
}

export function writeChatCategoryPreference(
  scope: ChatCategoryPreferenceScope,
  category: ChatPreferenceCategory,
  preference: ChatCategoryPreference,
  storage?: PreferenceStorage,
): boolean {
  if (typeof preference.modelId !== "string" || typeof preference.promptId !== "string") return false;
  try {
    const destination = storage ?? globalThis.localStorage;
    destination.setItem(chatCategoryPreferenceKey(scope, category), JSON.stringify(preference));
    return true;
  } catch {
    return false;
  }
}
