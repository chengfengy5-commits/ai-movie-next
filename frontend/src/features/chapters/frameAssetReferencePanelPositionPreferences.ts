export interface FrameAssetReferencePanelPosition {
  readonly left: number;
  readonly top: number;
}

export interface FrameAssetReferencePanelPositionScope {
  readonly userId: string;
  readonly apiBaseUrl: string;
  readonly panelKind: "frame-assets";
}

export interface FrameAssetReferencePanelSize {
  readonly width: number;
  readonly height: number;
}

export interface FrameAssetReferenceViewportSize {
  readonly width: number;
  readonly height: number;
}

export type FrameAssetReferencePanelPositionReadResult =
  | { readonly kind: "stored"; readonly position: FrameAssetReferencePanelPosition }
  | { readonly kind: "missing" | "unavailable" | "invalid-scope" };

export type FrameAssetReferencePanelPositionWriteResult =
  | "saved"
  | "unavailable"
  | "invalid-scope"
  | "invalid-position";

export type FrameAssetReferencePanelPositionStorage = Pick<Storage, "getItem" | "setItem">;

const STORAGE_KEY_PREFIX = "haoai.frame-asset-reference-panel-position.v1";
const PANEL_KIND = "frame-assets" as const;
const DEFAULT_RIGHT_OFFSET = 400;
const DEFAULT_TOP_OFFSET = 60;
const VIEWPORT_GUTTER = 20;

export function createFrameAssetReferencePanelPositionScope(
  userId: string | null | undefined,
  apiBaseUrl: string | null | undefined,
): FrameAssetReferencePanelPositionScope | null {
  if (typeof userId !== "string" || userId.trim() === ""
    || typeof apiBaseUrl !== "string" || apiBaseUrl.trim() === "") {
    return null;
  }
  return { userId, apiBaseUrl, panelKind: PANEL_KIND };
}

function storageKey(scope: FrameAssetReferencePanelPositionScope): string | null {
  if (scope.panelKind !== PANEL_KIND
    || typeof scope.userId !== "string" || scope.userId.trim() === ""
    || typeof scope.apiBaseUrl !== "string" || scope.apiBaseUrl.trim() === "") {
    return null;
  }
  return `${STORAGE_KEY_PREFIX}:${encodeURIComponent(JSON.stringify([
    scope.userId,
    scope.apiBaseUrl,
    scope.panelKind,
  ]))}`;
}

function browserStorage(): FrameAssetReferencePanelPositionStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function resolveStorage(
  storage: FrameAssetReferencePanelPositionStorage | null | undefined,
): FrameAssetReferencePanelPositionStorage | null {
  return storage === undefined ? browserStorage() : storage;
}

function isPosition(value: unknown): value is FrameAssetReferencePanelPosition {
  if (value === null || typeof value !== "object") return false;
  const position = value as Record<string, unknown>;
  return typeof position.left === "number"
    && Number.isFinite(position.left)
    && position.left >= 0
    && typeof position.top === "number"
    && Number.isFinite(position.top)
    && position.top >= 0;
}

export function readFrameAssetReferencePanelPosition(
  scope: FrameAssetReferencePanelPositionScope,
  storage?: FrameAssetReferencePanelPositionStorage | null,
): FrameAssetReferencePanelPositionReadResult {
  const key = storageKey(scope);
  if (key === null) return { kind: "invalid-scope" };
  const source = resolveStorage(storage);
  if (source === null) return { kind: "unavailable" };

  let raw: string | null;
  try {
    raw = source.getItem(key);
  } catch {
    return { kind: "unavailable" };
  }
  if (raw === null) return { kind: "missing" };

  try {
    const parsed: unknown = JSON.parse(raw);
    return isPosition(parsed)
      ? { kind: "stored", position: { left: parsed.left, top: parsed.top } }
      : { kind: "missing" };
  } catch {
    return { kind: "missing" };
  }
}

export function writeFrameAssetReferencePanelPosition(
  scope: FrameAssetReferencePanelPositionScope,
  position: FrameAssetReferencePanelPosition,
  storage?: FrameAssetReferencePanelPositionStorage | null,
): FrameAssetReferencePanelPositionWriteResult {
  const key = storageKey(scope);
  if (key === null) return "invalid-scope";
  if (!isPosition(position)) return "invalid-position";
  const source = resolveStorage(storage);
  if (source === null) return "unavailable";

  try {
    source.setItem(key, JSON.stringify({ left: position.left, top: position.top }));
    return "saved";
  } catch {
    return "unavailable";
  }
}

export function clampFrameAssetReferencePanelPosition(
  position: FrameAssetReferencePanelPosition,
  panel: FrameAssetReferencePanelSize,
  viewport: FrameAssetReferenceViewportSize,
): FrameAssetReferencePanelPosition {
  const maximumLeft = Math.max(VIEWPORT_GUTTER, viewport.width - panel.width - VIEWPORT_GUTTER);
  const maximumTop = Math.max(VIEWPORT_GUTTER, viewport.height - panel.height - VIEWPORT_GUTTER);
  return {
    left: Math.min(Math.max(position.left, VIEWPORT_GUTTER), maximumLeft),
    top: Math.min(Math.max(position.top, VIEWPORT_GUTTER), maximumTop),
  };
}

export function defaultFrameAssetReferencePanelPosition(
  panel: FrameAssetReferencePanelSize,
  viewport: FrameAssetReferenceViewportSize,
): FrameAssetReferencePanelPosition {
  return clampFrameAssetReferencePanelPosition(
    {
      left: viewport.width - DEFAULT_RIGHT_OFFSET - panel.width,
      top: DEFAULT_TOP_OFFSET,
    },
    panel,
    viewport,
  );
}
