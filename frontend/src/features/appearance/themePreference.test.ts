// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import {
  parseThemePreference,
  readThemePreference,
  THEME_PREFERENCE_STORAGE_KEY,
  writeThemePreference,
  type ThemePreferenceStorage,
} from "./themePreference";

function createStorage(initial: Record<string, string> = {}): ThemePreferenceStorage {
  const values = new Map(Object.entries(initial));
  return {
    getItem: vi.fn((key) => values.get(key) ?? null),
    setItem: vi.fn((key, value) => { values.set(key, value); }),
  };
}

describe("theme preference storage", () => {
  it.each([
    ["light", "light"],
    ["dark", "dark"],
    ["auto", "auto"],
    ["sepia", "light"],
    [null, "light"],
    [7, "light"],
  ])("parses %s safely", (value, expected) => {
    expect(parseThemePreference(value)).toBe(expected);
  });

  it("restores the legacy hao-theme preference and persists new values under that key", () => {
    const storage = createStorage({ [THEME_PREFERENCE_STORAGE_KEY]: "auto" });

    expect(readThemePreference(storage)).toEqual({ preference: "auto", storageAvailable: true });
    expect(writeThemePreference("dark", storage)).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith(THEME_PREFERENCE_STORAGE_KEY, "dark");
    expect(readThemePreference(storage)).toEqual({ preference: "dark", storageAvailable: true });
  });

  it("uses light for an illegal stored value and reports unavailable storage accurately", () => {
    const invalidStorage = createStorage({ [THEME_PREFERENCE_STORAGE_KEY]: "cyber-purple" });
    const unavailableStorage: ThemePreferenceStorage = {
      getItem: () => { throw new Error("storage blocked"); },
      setItem: () => { throw new Error("storage blocked"); },
    };

    expect(readThemePreference(invalidStorage)).toEqual({ preference: "light", storageAvailable: true });
    expect(readThemePreference(unavailableStorage)).toEqual({ preference: "light", storageAvailable: false });
    expect(writeThemePreference("dark", unavailableStorage)).toBe(false);
  });
});
