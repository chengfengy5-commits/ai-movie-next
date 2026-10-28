import { describe, expect, it } from "vitest";
import {
  defaultSeriesWorkbenchPreferences,
  readSeriesWorkbenchPreferences,
  resizeSeriesWorkbenchLeftWidth,
  writeSeriesWorkbenchPreferences,
  type SeriesWorkbenchStorage,
} from "./preferences";

function memoryStorage(): SeriesWorkbenchStorage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
  };
}

const scope = { userId: "actor-a", apiBaseUrl: "https://api.example.invalid", seriesId: "series-a" };

describe("series workbench preferences", () => {
  it("scopes route and width to actor, API and series", () => {
    const storage = memoryStorage();
    const getStorage = () => storage;
    const saved = {
      route: "media" as const,
      leftView: "assets" as const,
      leftWidths: { storyboard: 64, assets: 58, "continuous-preview": 72 },
    };
    expect(writeSeriesWorkbenchPreferences(scope, saved, getStorage)).toBe(true);
    expect(readSeriesWorkbenchPreferences(scope, getStorage).preferences).toEqual(saved);
    expect(readSeriesWorkbenchPreferences({ ...scope, userId: "actor-b" }, getStorage).preferences)
      .toEqual(defaultSeriesWorkbenchPreferences);
    expect(readSeriesWorkbenchPreferences({ ...scope, apiBaseUrl: "https://other.example.invalid" }, getStorage).preferences)
      .toEqual(defaultSeriesWorkbenchPreferences);
    expect(readSeriesWorkbenchPreferences({ ...scope, seriesId: "series-b" }, getStorage).preferences)
      .toEqual(defaultSeriesWorkbenchPreferences);
  });

  it("clamps saved width and pointer-resize values to the supported range", () => {
    const storage = memoryStorage();
    expect(writeSeriesWorkbenchPreferences(scope, {
      route: "chat",
      leftView: "storyboard",
      leftWidths: { storyboard: 100, assets: 55, "continuous-preview": 70 },
    }, () => storage)).toBe(true);
    expect(readSeriesWorkbenchPreferences(scope, () => storage).preferences.leftWidths)
      .toEqual({ storyboard: 80, assets: 55, "continuous-preview": 70 });
    expect(resizeSeriesWorkbenchLeftWidth(70, -1_000, 1_000)).toBe(30);
    expect(resizeSeriesWorkbenchLeftWidth(70, 1_000, 1_000)).toBe(80);
  });

  it("restores the continuous editor as its own left view and width", () => {
    const storage = memoryStorage();
    const preferences = {
      route: "media" as const,
      leftView: "continuous-preview" as const,
      leftWidths: { storyboard: 64, assets: 58, "continuous-preview": 72 },
    };

    expect(writeSeriesWorkbenchPreferences(scope, preferences, () => storage)).toBe(true);
    expect(readSeriesWorkbenchPreferences(scope, () => storage).preferences).toEqual(preferences);
  });

  it("keeps default behavior available when browser storage fails", () => {
    expect(readSeriesWorkbenchPreferences(scope, () => { throw new Error("blocked"); }))
      .toEqual({ preferences: defaultSeriesWorkbenchPreferences, available: false });
    expect(writeSeriesWorkbenchPreferences(scope, {
      route: "media",
      leftView: "assets",
      leftWidths: { storyboard: 70, assets: 70, "continuous-preview": 55 },
    }, () => {
      throw new Error("blocked");
    })).toBe(false);
  });
});
