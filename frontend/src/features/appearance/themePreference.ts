export type ThemePreference = "light" | "dark" | "auto";

export const THEME_PREFERENCE_STORAGE_KEY = "hao-theme";

export interface ThemePreferenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface ThemePreferenceReadResult {
  preference: ThemePreference;
  storageAvailable: boolean;
}

const THEME_PREFERENCES = new Set<ThemePreference>(["light", "dark", "auto"]);

export function parseThemePreference(value: unknown): ThemePreference {
  return typeof value === "string" && THEME_PREFERENCES.has(value as ThemePreference)
    ? value as ThemePreference
    : "light";
}

function resolveStorage(storage?: ThemePreferenceStorage | null): ThemePreferenceStorage {
  if (storage !== undefined) {
    if (storage === null) {
      throw new Error("Appearance storage is unavailable.");
    }
    return storage;
  }
  if (typeof window === "undefined") {
    throw new Error("Appearance storage is unavailable.");
  }
  return window.localStorage;
}

export function readThemePreference(
  storage?: ThemePreferenceStorage | null,
): ThemePreferenceReadResult {
  try {
    const value = resolveStorage(storage).getItem(THEME_PREFERENCE_STORAGE_KEY);
    return {
      preference: parseThemePreference(value),
      storageAvailable: true,
    };
  } catch {
    return { preference: "light", storageAvailable: false };
  }
}

export function writeThemePreference(
  preference: ThemePreference,
  storage?: ThemePreferenceStorage | null,
): boolean {
  if (!THEME_PREFERENCES.has(preference)) {
    return false;
  }
  try {
    resolveStorage(storage).setItem(THEME_PREFERENCE_STORAGE_KEY, preference);
    return true;
  } catch {
    return false;
  }
}
