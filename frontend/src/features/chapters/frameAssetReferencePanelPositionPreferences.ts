export interface FramePanelPosition {
  readonly left: number;
  readonly top: number;
}

export type FramePanelKind = "frame-assets" | "frame-original" | "extract-script";

export interface FramePanelPositionScope {
  readonly userId: string;
  readonly apiBaseUrl: string;
  readonly panelKind: FramePanelKind;
}

export type FrameAssetReferencePanelPosition = FramePanelPosition;

export interface FrameAssetReferencePanelPositionScope extends FramePanelPositionScope {
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

export type FramePanelPositionReadResult =
  | { readonly kind: "stored"; readonly position: FramePanelPosition }
  | { readonly kind: "missing" | "unavailable" | "invalid-scope" };

export type FramePanelPositionWriteResult =
  | "saved"
  | "unavailable"
  | "invalid-scope"
  | "invalid-position";

export type FramePanelPositionStorage = Pick<Storage, "getItem" | "setItem">;

export type FrameAssetReferencePanelPositionReadResult = FramePanelPositionReadResult;
export type FrameAssetReferencePanelPositionWriteResult = FramePanelPositionWriteResult;
export type FrameAssetReferencePanelPositionStorage = FramePanelPositionStorage;

const STORAGE_KEY_PREFIX = "haoai.frame-asset-reference-panel-position.v1";
const ORIGINAL_TEXT_STORAGE_KEY_PREFIX = "haoai.frame-original-reference-panel-position.v1";
const EXTRACT_SCRIPT_STORAGE_KEY_PREFIX = "haoai.frame-extract-script-panel-position.v1";
const VIEWPORT_GUTTER = 20;

export function createFramePanelPositionScope(
  userId: string | null | undefined,
  apiBaseUrl: string | null | undefined,
  panelKind: FramePanelKind,
): FramePanelPositionScope | null {
  if (typeof userId !== "string" || userId.trim() === ""
    || typeof apiBaseUrl !== "string" || apiBaseUrl.trim() === "") {
    return null;
  }
  return { userId, apiBaseUrl, panelKind };
}

export function createFrameAssetReferencePanelPositionScope(
  userId: string | null | undefined,
  apiBaseUrl: string | null | undefined,
): FrameAssetReferencePanelPositionScope | null {
  const scope = createFramePanelPositionScope(userId, apiBaseUrl, "frame-assets");
  return scope === null ? null : { ...scope, panelKind: "frame-assets" };
}

function storageKey(scope: FramePanelPositionScope): string | null {
  const prefix = scope.panelKind === "frame-assets"
    ? STORAGE_KEY_PREFIX
    : scope.panelKind === "frame-original"
      ? ORIGINAL_TEXT_STORAGE_KEY_PREFIX
      : scope.panelKind === "extract-script"
        ? EXTRACT_SCRIPT_STORAGE_KEY_PREFIX
        : null;
  if (prefix === null
    || typeof scope.userId !== "string" || scope.userId.trim() === ""
    || typeof scope.apiBaseUrl !== "string" || scope.apiBaseUrl.trim() === "") {
    return null;
  }
  return `${prefix}:${encodeURIComponent(JSON.stringify([
    scope.userId,
    scope.apiBaseUrl,
    scope.panelKind,
  ]))}`;
}

function browserStorage(): FramePanelPositionStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

function resolveStorage(
  storage: FramePanelPositionStorage | null | undefined,
): FramePanelPositionStorage | null {
  return storage === undefined ? browserStorage() : storage;
}

function isPosition(value: unknown): value is FramePanelPosition {
  if (value === null || typeof value !== "object") return false;
  const position = value as Record<string, unknown>;
  return typeof position.left === "number"
    && Number.isFinite(position.left)
    && position.left >= 0
    && typeof position.top === "number"
    && Number.isFinite(position.top)
    && position.top >= 0;
}

export function readFramePanelPosition(
  scope: FramePanelPositionScope,
  storage?: FramePanelPositionStorage | null,
): FramePanelPositionReadResult {
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

export function writeFramePanelPosition(
  scope: FramePanelPositionScope,
  position: FramePanelPosition,
  storage?: FramePanelPositionStorage | null,
): FramePanelPositionWriteResult {
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

export function clampFramePanelPosition(
  position: FramePanelPosition,
  panel: FrameAssetReferencePanelSize,
  viewport: FrameAssetReferenceViewportSize,
): FramePanelPosition {
  const maximumLeft = Math.max(VIEWPORT_GUTTER, viewport.width - panel.width - VIEWPORT_GUTTER);
  const maximumTop = Math.max(VIEWPORT_GUTTER, viewport.height - panel.height - VIEWPORT_GUTTER);
  return {
    left: Math.min(Math.max(position.left, VIEWPORT_GUTTER), maximumLeft),
    top: Math.min(Math.max(position.top, VIEWPORT_GUTTER), maximumTop),
  };
}

export function defaultFramePanelPosition(
  panel: FrameAssetReferencePanelSize,
  viewport: FrameAssetReferenceViewportSize,
  rightOffset: number,
  top: number,
): FramePanelPosition {
  return clampFramePanelPosition(
    {
      left: viewport.width - rightOffset - panel.width,
      top,
    },
    panel,
    viewport,
  );
}

export function readFrameAssetReferencePanelPosition(
  scope: FrameAssetReferencePanelPositionScope,
  storage?: FrameAssetReferencePanelPositionStorage | null,
): FrameAssetReferencePanelPositionReadResult {
  return readFramePanelPosition(scope, storage);
}

export function writeFrameAssetReferencePanelPosition(
  scope: FrameAssetReferencePanelPositionScope,
  position: FrameAssetReferencePanelPosition,
  storage?: FrameAssetReferencePanelPositionStorage | null,
): FrameAssetReferencePanelPositionWriteResult {
  return writeFramePanelPosition(scope, position, storage);
}

export function clampFrameAssetReferencePanelPosition(
  position: FrameAssetReferencePanelPosition,
  panel: FrameAssetReferencePanelSize,
  viewport: FrameAssetReferenceViewportSize,
): FrameAssetReferencePanelPosition {
  return clampFramePanelPosition(position, panel, viewport);
}

export function defaultFrameAssetReferencePanelPosition(
  panel: FrameAssetReferencePanelSize,
  viewport: FrameAssetReferenceViewportSize,
): FrameAssetReferencePanelPosition {
  return defaultFramePanelPosition(panel, viewport, 400, 60);
}
