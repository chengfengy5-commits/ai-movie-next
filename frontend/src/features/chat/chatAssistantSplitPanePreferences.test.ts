import { describe, expect, it } from "vitest";
import type { StorageLike } from "../../shared/api/storage";
import {
  chatAssistantSplitPanePreferenceKey,
  readChatAssistantSplitPanePreference,
  writeChatAssistantSplitPanePreference,
} from "./chatAssistantSplitPanePreferences";

class MemoryStorage implements StorageLike {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
  removeItem(key: string): void { this.values.delete(key); }
}

const scope = { userId: "actor-split-1070", apiBaseUrl: "https://api.example.test/api" };

describe("chat assistant split pane preferences", () => {
  it("isolates the saved ratio by user and API without using the old global keys", () => {
    const storage = new MemoryStorage();
    const key = chatAssistantSplitPanePreferenceKey(scope);
    expect(key).not.toBeNull();
    expect(writeChatAssistantSplitPanePreference(scope, 73, storage)).toEqual({ status: "saved" });
    expect(readChatAssistantSplitPanePreference(scope, storage)).toEqual({
      status: "ready",
      messageShare: 73,
      malformed: false,
    });
    expect(readChatAssistantSplitPanePreference({ ...scope, userId: "another-actor" }, storage)).toMatchObject({
      status: "ready",
      messageShare: null,
    });
    expect(readChatAssistantSplitPanePreference({ ...scope, apiBaseUrl: "https://other.example.test/api" }, storage))
      .toMatchObject({ status: "ready", messageShare: null });
    expect(key).not.toContain("episode_chatMessagesFlex");
    expect(key).not.toContain("episode_chatInputFlex");
  });

  it("rejects malformed or out-of-range values and invalid writes", () => {
    const storage = new MemoryStorage();
    const key = chatAssistantSplitPanePreferenceKey(scope)!;
    storage.setItem(key, "91");

    expect(readChatAssistantSplitPanePreference(scope, storage)).toEqual({
      status: "ready",
      messageShare: null,
      malformed: true,
    });
    expect(writeChatAssistantSplitPanePreference(scope, 9, storage)).toEqual({ status: "invalid" });
    expect(writeChatAssistantSplitPanePreference(scope, 90.5, storage)).toEqual({ status: "invalid" });
    expect(storage.getItem(key)).toBe("91");
  });

  it("reports a missing identity and storage failures without throwing", () => {
    const unavailable = {
      getItem: () => { throw new Error("read blocked"); },
      setItem: () => { throw new Error("write blocked"); },
      removeItem: () => undefined,
    };
    expect(readChatAssistantSplitPanePreference({ ...scope, userId: " " }, unavailable))
      .toEqual({ status: "unavailable", reason: "scope" });
    expect(readChatAssistantSplitPanePreference(scope, unavailable))
      .toEqual({ status: "unavailable", reason: "storage" });
    expect(writeChatAssistantSplitPanePreference(scope, 55, unavailable))
      .toEqual({ status: "unavailable", reason: "storage" });
  });

  it("catches a throwing browser storage getter", () => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() { throw new Error("storage getter blocked"); },
    });
    try {
      expect(readChatAssistantSplitPanePreference(scope)).toEqual({ status: "unavailable", reason: "storage" });
      expect(writeChatAssistantSplitPanePreference(scope, 55)).toEqual({ status: "unavailable", reason: "storage" });
    } finally {
      if (descriptor !== undefined) Object.defineProperty(window, "localStorage", descriptor);
    }
  });
});
