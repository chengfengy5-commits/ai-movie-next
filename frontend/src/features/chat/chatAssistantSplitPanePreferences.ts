import type { StorageLike } from "../../shared/api/storage";

const STORAGE_KEY_PREFIX = "haoai:chat-assistant-split:v1";

export const DEFAULT_CHAT_MESSAGES_SHARE = 62;
export const MIN_CHAT_MESSAGES_SHARE = 10;
export const MAX_CHAT_MESSAGES_SHARE = 90;

export interface ChatAssistantSplitPanePreferenceScope {
  readonly userId: string;
  readonly apiBaseUrl: string | null | undefined;
}

export type ChatAssistantSplitPanePreferenceRead =
  | { readonly status: "ready"; readonly messageShare: number | null; readonly malformed: boolean }
  | { readonly status: "unavailable"; readonly reason: "scope" | "storage" };

export type ChatAssistantSplitPanePreferenceWrite =
  | { readonly status: "saved" }
  | { readonly status: "unavailable"; readonly reason: "scope" | "storage" }
  | { readonly status: "invalid" };

export function chatAssistantSplitPanePreferenceKey(
  scope: ChatAssistantSplitPanePreferenceScope,
): string | null {
  if (!hasUsableScope(scope)) return null;
  return `${STORAGE_KEY_PREFIX}:${encodeURIComponent(scope.apiBaseUrl!.trim())}:${encodeURIComponent(scope.userId.trim())}`;
}

export function readChatAssistantSplitPanePreference(
  scope: ChatAssistantSplitPanePreferenceScope,
  storage?: StorageLike,
): ChatAssistantSplitPanePreferenceRead {
  const key = chatAssistantSplitPanePreferenceKey(scope);
  if (key === null) return { status: "unavailable", reason: "scope" };
  try {
    const serialized = getStorage(storage).getItem(key);
    if (serialized === null) return { status: "ready", messageShare: null, malformed: false };
    if (!/^\d{1,2}$/u.test(serialized)) return { status: "ready", messageShare: null, malformed: true };
    const messageShare = Number(serialized);
    if (!isValidMessageShare(messageShare) || String(messageShare) !== serialized) {
      return { status: "ready", messageShare: null, malformed: true };
    }
    return { status: "ready", messageShare, malformed: false };
  } catch {
    return { status: "unavailable", reason: "storage" };
  }
}

export function writeChatAssistantSplitPanePreference(
  scope: ChatAssistantSplitPanePreferenceScope,
  messageShare: number,
  storage?: StorageLike,
): ChatAssistantSplitPanePreferenceWrite {
  const key = chatAssistantSplitPanePreferenceKey(scope);
  if (key === null) return { status: "unavailable", reason: "scope" };
  if (!isValidMessageShare(messageShare)) return { status: "invalid" };
  try {
    getStorage(storage).setItem(key, String(messageShare));
    return { status: "saved" };
  } catch {
    return { status: "unavailable", reason: "storage" };
  }
}

function hasUsableScope(scope: ChatAssistantSplitPanePreferenceScope): boolean {
  return scope.apiBaseUrl !== null && scope.apiBaseUrl !== undefined && scope.apiBaseUrl.trim() !== ""
    && scope.userId.trim() !== "";
}

function isValidMessageShare(value: number): boolean {
  return Number.isInteger(value) && value >= MIN_CHAT_MESSAGES_SHARE && value <= MAX_CHAT_MESSAGES_SHARE;
}

function getStorage(storage?: StorageLike): StorageLike {
  if (storage !== undefined) return storage;
  if (typeof window === "undefined") throw new Error("Browser storage is unavailable.");
  return window.localStorage;
}
