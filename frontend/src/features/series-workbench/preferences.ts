export type SeriesWorkbenchRoute = "chat" | "media";
export type SeriesWorkbenchLeftView = "storyboard" | "assets" | "continuous-preview";

export interface SeriesWorkbenchPreferenceScope {
  readonly userId: string;
  readonly apiBaseUrl: string;
  readonly seriesId: string;
}

export interface SeriesWorkbenchPreferences {
  readonly route: SeriesWorkbenchRoute;
  readonly leftView: Exclude<SeriesWorkbenchLeftView, "continuous-preview">;
  readonly leftWidths: Readonly<Record<SeriesWorkbenchLeftView, number>>;
}

export interface SeriesWorkbenchStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface SeriesWorkbenchPreferenceRead {
  readonly preferences: SeriesWorkbenchPreferences;
  readonly available: boolean;
}

export const defaultSeriesWorkbenchPreferences: SeriesWorkbenchPreferences = {
  route: "chat",
  leftView: "storyboard",
  leftWidths: { storyboard: 70, assets: 70, "continuous-preview": 70 },
};

const preferencePrefix = "haoai.series-workbench.v1";
const minimumLeftWidth = 30;
const maximumLeftWidth = 80;

function browserStorage(): SeriesWorkbenchStorage {
  return window.localStorage;
}

function preferenceKey(scope: SeriesWorkbenchPreferenceScope): string {
  return [preferencePrefix, scope.userId, scope.apiBaseUrl, scope.seriesId]
    .map(encodeURIComponent)
    .join(":");
}

function clampLeftWidth(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return 70;
  return Math.min(maximumLeftWidth, Math.max(minimumLeftWidth, Math.round(value)));
}

function parsePreferences(value: string | null): SeriesWorkbenchPreferences {
  if (value === null) return defaultSeriesWorkbenchPreferences;
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return defaultSeriesWorkbenchPreferences;
    }
    const record = parsed as Record<string, unknown>;
    const oldWidth = clampLeftWidth(record.leftWidth);
    const widths = typeof record.leftWidths === "object" && record.leftWidths !== null && !Array.isArray(record.leftWidths)
      ? record.leftWidths as Record<string, unknown>
      : {};
    return {
      route: record.route === "media" ? "media" : "chat",
      leftView: record.leftView === "assets" ? "assets" : "storyboard",
      leftWidths: {
        storyboard: clampLeftWidth(widths.storyboard ?? widths.chat ?? oldWidth),
        assets: clampLeftWidth(widths.assets ?? oldWidth),
        "continuous-preview": clampLeftWidth(widths["continuous-preview"] ?? oldWidth),
      },
    };
  } catch {
    return defaultSeriesWorkbenchPreferences;
  }
}

export function readSeriesWorkbenchPreferences(
  scope: SeriesWorkbenchPreferenceScope,
  getStorage: () => SeriesWorkbenchStorage = browserStorage,
): SeriesWorkbenchPreferenceRead {
  if (scope.userId.trim() === "" || scope.apiBaseUrl.trim() === "" || scope.seriesId.trim() === "") {
    return { preferences: defaultSeriesWorkbenchPreferences, available: false };
  }
  try {
    return { preferences: parsePreferences(getStorage().getItem(preferenceKey(scope))), available: true };
  } catch {
    return { preferences: defaultSeriesWorkbenchPreferences, available: false };
  }
}

export function writeSeriesWorkbenchPreferences(
  scope: SeriesWorkbenchPreferenceScope,
  preferences: SeriesWorkbenchPreferences,
  getStorage: () => SeriesWorkbenchStorage = browserStorage,
): boolean {
  if (scope.userId.trim() === "" || scope.apiBaseUrl.trim() === "" || scope.seriesId.trim() === "") return false;
  try {
    getStorage().setItem(preferenceKey(scope), JSON.stringify({
      route: preferences.route,
      leftView: preferences.leftView,
      leftWidths: {
        storyboard: clampLeftWidth(preferences.leftWidths.storyboard),
        assets: clampLeftWidth(preferences.leftWidths.assets),
        "continuous-preview": clampLeftWidth(preferences.leftWidths["continuous-preview"]),
      },
    }));
    return true;
  } catch {
    return false;
  }
}

export function resizeSeriesWorkbenchLeftWidth(current: number, deltaPixels: number, totalPixels: number): number {
  if (!Number.isFinite(deltaPixels) || !Number.isFinite(totalPixels) || totalPixels <= 0) return clampLeftWidth(current);
  return clampLeftWidth(current + deltaPixels / totalPixels * 100);
}
