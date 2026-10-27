import { describe, expect, it } from "vitest";
import type { CreatorPrompt } from "../../shared/api/creatorSettings";
import {
  fusedGenerationPreferenceKey,
  readFusedGenerationPreference,
  readFusedGenerationPreferenceResult,
  resolveFusedPrompt,
  safeFusedImageUrl,
  writeFusedGenerationPreference,
} from "./fusedGenerationPreferences";

class MemoryStorage {
  readonly values = new Map<string, string>();
  getItem(key: string): string | null { return this.values.get(key) ?? null; }
  setItem(key: string, value: string): void { this.values.set(key, value); }
}

describe("fused generation preferences", () => {
  it("scopes only model, prompt id, and size by actor, API, and member type", () => {
    const storage = new MemoryStorage();
    const characterKey = fusedGenerationPreferenceKey("actor-1", "https://api.example.test/api", "character");
    const propKey = fusedGenerationPreferenceKey("actor-1", "https://api.example.test/api", "prop");
    expect(characterKey).not.toBe(propKey);
    expect(fusedGenerationPreferenceKey("actor-2", "https://api.example.test/api", "character")).not.toBe(characterKey);
    expect(fusedGenerationPreferenceKey("actor-1", "https://other.example.test/api", "character")).not.toBe(characterKey);
    expect(writeFusedGenerationPreference(storage, characterKey, {
      modelId: "model-1",
      promptId: "",
      size: "1024x1024",
    })).toBe(true);
    expect(readFusedGenerationPreference(storage, characterKey)).toEqual({
      modelId: "model-1",
      promptId: "",
      size: "1024x1024",
    });
    expect(storage.values.get(characterKey!)).not.toContain("成员");
    expect(storage.values.get(characterKey!)).not.toContain("提示词正文");
    expect(readFusedGenerationPreferenceResult({ getItem: () => { throw new Error("storage denied"); } }, characterKey))
      .toEqual({ value: null, available: false });
  });

  it("resolves only the selected type from valid fuse JSON and does not inject malformed JSON", () => {
    const prompt: CreatorPrompt = {
      id: "prompt-1",
      category: "fuse",
      name: "双类型模板",
      system_prompt: "系统原文",
      user_prompt: JSON.stringify({ character: "角色正文", prop: "道具正文" }),
      sort_order: 0,
      user_id: "actor-1",
    };
    expect(resolveFusedPrompt(prompt, "character")).toEqual({ system: "系统原文", user: "角色正文" });
    expect(resolveFusedPrompt(prompt, "prop")).toEqual({ system: "系统原文", user: "道具正文" });
    expect(resolveFusedPrompt({ ...prompt, user_prompt: "{broken json" }, "character"))
      .toEqual({ system: "系统原文", user: null });
    expect(resolveFusedPrompt({ ...prompt, user_prompt: "角色旧版原文" }, "character"))
      .toEqual({ system: "系统原文", user: "角色旧版原文" });
    expect(safeFusedImageUrl("/uploads/reference.png", "http://127.0.0.1:4175/api"))
      .toBe("http://127.0.0.1:4175/uploads/reference.png");
    expect(safeFusedImageUrl("https://user:secret@images.example.test/image.png", "https://api.example.test/api"))
      .toBeNull();
  });
});
