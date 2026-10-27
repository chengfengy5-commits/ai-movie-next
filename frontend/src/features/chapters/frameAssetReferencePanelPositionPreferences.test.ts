import { describe, expect, it, vi } from "vitest";
import {
  clampFrameAssetReferencePanelPosition,
  createFrameAssetReferencePanelPositionScope,
  defaultFrameAssetReferencePanelPosition,
  readFrameAssetReferencePanelPosition,
  writeFrameAssetReferencePanelPosition,
  type FrameAssetReferencePanelPositionScope,
  type FrameAssetReferencePanelPositionStorage,
} from "./frameAssetReferencePanelPositionPreferences";

function scope(overrides: Partial<FrameAssetReferencePanelPositionScope> = {}): FrameAssetReferencePanelPositionScope {
  return {
    userId: "position-user",
    apiBaseUrl: "http://127.0.0.1:4175/api",
    panelKind: "frame-assets",
    ...overrides,
  };
}

function memoryStorage(): FrameAssetReferencePanelPositionStorage & { records: Map<string, string> } {
  const records = new Map<string, string>();
  return {
    records,
    getItem: (key) => records.get(key) ?? null,
    setItem: (key, value) => { records.set(key, value); },
  };
}

describe("frame asset reference panel position preferences", () => {
  it("isolates coordinates by account and API and ignores unscoped legacy values", () => {
    const storage = memoryStorage();
    storage.records.set("legacy-panel-position", JSON.stringify({ left: 700, top: 80 }));
    const current = scope();

    expect(readFrameAssetReferencePanelPosition(current, storage)).toEqual({ kind: "missing" });
    expect(storage.records.size).toBe(1);
    expect(writeFrameAssetReferencePanelPosition(current, { left: 240, top: 60 }, storage)).toBe("saved");

    expect(readFrameAssetReferencePanelPosition(scope({ userId: "another-user" }), storage))
      .toEqual({ kind: "missing" });
    expect(readFrameAssetReferencePanelPosition(scope({ apiBaseUrl: "http://127.0.0.1:4176/api" }), storage))
      .toEqual({ kind: "missing" });
    expect(readFrameAssetReferencePanelPosition(current, storage)).toEqual({
      kind: "stored",
      position: { left: 240, top: 60 },
    });
  });

  it("rejects missing scopes and malformed, negative, or non-finite coordinates", () => {
    const storage = memoryStorage();
    const current = scope();
    expect(createFrameAssetReferencePanelPositionScope(" ", current.apiBaseUrl)).toBeNull();
    expect(createFrameAssetReferencePanelPositionScope(current.userId, null)).toBeNull();
    expect(createFrameAssetReferencePanelPositionScope(current.userId, "  ")).toBeNull();
    expect(writeFrameAssetReferencePanelPosition(current, { left: -1, top: 0 }, storage)).toBe("invalid-position");
    expect(writeFrameAssetReferencePanelPosition(current, { left: Number.NaN, top: 0 }, storage)).toBe("invalid-position");
    expect(writeFrameAssetReferencePanelPosition(current, { left: 0, top: Number.POSITIVE_INFINITY }, storage))
      .toBe("invalid-position");
    expect(writeFrameAssetReferencePanelPosition(current, { left: 1, top: 2 }, storage)).toBe("saved");

    const [key] = storage.records.keys();
    expect(key).toBeDefined();
    for (const raw of ["broken", "null", "[]", "{\"left\":-1,\"top\":3}", "{\"left\":2,\"top\":null}"]) {
      storage.records.set(key!, raw);
      expect(readFrameAssetReferencePanelPosition(current, storage)).toEqual({ kind: "missing" });
    }
  });

  it("reports unavailable storage without preventing manual interaction", () => {
    const deniedStorage: FrameAssetReferencePanelPositionStorage = {
      getItem: vi.fn(() => { throw new Error("Access denied."); }),
      setItem: vi.fn(() => { throw new Error("Access denied."); }),
    };
    const current = scope();

    expect(readFrameAssetReferencePanelPosition(current, deniedStorage)).toEqual({ kind: "unavailable" });
    expect(writeFrameAssetReferencePanelPosition(current, { left: 300, top: 75 }, deniedStorage)).toBe("unavailable");
    expect(readFrameAssetReferencePanelPosition(current, null)).toEqual({ kind: "unavailable" });
    expect(writeFrameAssetReferencePanelPosition(current, { left: 300, top: 75 }, null)).toBe("unavailable");
    expect(deniedStorage.getItem).toHaveBeenCalledOnce();
    expect(deniedStorage.setItem).toHaveBeenCalledOnce();
  });

  it("clamps saved and default positions using the measured panel and viewport size", () => {
    expect(defaultFrameAssetReferencePanelPosition(
      { width: 360, height: 300 },
      { width: 1400, height: 900 },
    )).toEqual({ left: 640, top: 60 });
    expect(clampFrameAssetReferencePanelPosition(
      { left: 2000, top: 1000 },
      { width: 360, height: 600 },
      { width: 1280, height: 720 },
    )).toEqual({ left: 900, top: 100 });
    expect(clampFrameAssetReferencePanelPosition(
      { left: 0, top: 0 },
      { width: 360, height: 300 },
      { width: 1280, height: 720 },
    )).toEqual({ left: 20, top: 20 });
  });
});
