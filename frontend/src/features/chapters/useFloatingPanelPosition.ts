import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";
import {
  clampFramePanelPosition,
  createFramePanelPositionScope,
  defaultFramePanelPosition,
  readFramePanelPosition,
  writeFramePanelPosition,
  type FramePanelKind,
  type FramePanelPosition,
  type FramePanelPositionScope,
  type FrameAssetReferencePanelSize,
  type FrameAssetReferenceViewportSize,
} from "./frameAssetReferencePanelPositionPreferences";

export type FloatingPanelPresentation = "inline" | "floating";

export type FloatingPanelPositionNotice = "read-unavailable" | "write-unavailable" | "invalid-scope" | null;

export interface UseFloatingPanelPositionOptions {
  readonly panelKind: FramePanelKind;
  readonly userId: string | null | undefined;
  readonly apiBaseUrl: string | null | undefined;
  readonly ownerToken: unknown;
  readonly lifecycleSignal?: AbortSignal;
  readonly isOwnerCurrent: () => boolean;
  readonly initialPresentation: FloatingPanelPresentation;
  readonly defaultRightOffset: number;
  readonly defaultTop: number;
  readonly remeasureKey?: unknown;
  readonly breakpoint?: number;
}

interface ActivePanelDrag {
  readonly ownerToken: unknown;
  readonly scopeIdentity: string;
  readonly pointerId: number;
  readonly startX: number;
  readonly startY: number;
  readonly origin: FramePanelPosition;
  readonly captureTarget: HTMLButtonElement;
}

export interface FloatingPanelPositionController {
  readonly panelRef: RefObject<HTMLElement | null>;
  readonly dragHandleRef: RefObject<HTMLButtonElement | null>;
  readonly presentation: FloatingPanelPresentation;
  readonly floatingActive: boolean;
  readonly isSmallViewport: boolean;
  readonly desiredPosition: FramePanelPosition | null;
  readonly visiblePosition: FramePanelPosition | null;
  readonly positionNotice: FloatingPanelPositionNotice;
  requestFloating(): void;
  returnInline(): void;
  cancelDrag(): void;
  resetPosition(): void;
  clearPositionNotice(): void;
  beginDrag(event: PointerEvent<HTMLButtonElement>): void;
  moveDrag(event: PointerEvent<HTMLButtonElement>): void;
  finishDrag(event: PointerEvent<HTMLButtonElement>): void;
  cancelDragEvent(event: PointerEvent<HTMLButtonElement>): void;
  moveWithKeyboard(event: KeyboardEvent<HTMLButtonElement>): void;
}

const DEFAULT_BREAKPOINT = 768;
const DEFAULT_PANEL_SIZE: FrameAssetReferencePanelSize = { width: 360, height: 320 };

function scopeIdentity(
  panelKind: FramePanelKind,
  userId: string | null | undefined,
  apiBaseUrl: string | null | undefined,
): string {
  return JSON.stringify([panelKind, userId ?? null, apiBaseUrl ?? null]);
}

function samePosition(left: FramePanelPosition | null, right: FramePanelPosition): boolean {
  return left?.left === right.left && left.top === right.top;
}

export function useFloatingPanelPosition({
  panelKind,
  userId,
  apiBaseUrl,
  ownerToken,
  lifecycleSignal,
  isOwnerCurrent,
  initialPresentation,
  defaultRightOffset,
  defaultTop,
  remeasureKey,
  breakpoint = DEFAULT_BREAKPOINT,
}: UseFloatingPanelPositionOptions): FloatingPanelPositionController {
  const panelRef = useRef<HTMLElement | null>(null);
  const dragHandleRef = useRef<HTMLButtonElement | null>(null);
  const activeDragRef = useRef<ActivePanelDrag | null>(null);
  const currentOptions: UseFloatingPanelPositionOptions = {
    panelKind,
    userId,
    apiBaseUrl,
    ownerToken,
    isOwnerCurrent,
    initialPresentation,
    defaultRightOffset,
    defaultTop,
    breakpoint,
    ...(lifecycleSignal === undefined ? {} : { lifecycleSignal }),
    ...(remeasureKey === undefined ? {} : { remeasureKey }),
  };
  const optionsRef = useRef<UseFloatingPanelPositionOptions>(currentOptions);
  optionsRef.current = currentOptions;

  const currentScopeIdentity = scopeIdentity(panelKind, userId, apiBaseUrl);
  const [presentation, setPresentation] = useState<FloatingPanelPresentation>(initialPresentation);
  const [desiredPosition, setDesiredPosition] = useState<FramePanelPosition | null>(null);
  const [visiblePosition, setVisiblePosition] = useState<FramePanelPosition | null>(null);
  const desiredPositionRef = useRef<FramePanelPosition | null>(null);
  desiredPositionRef.current = desiredPosition;
  const [positionNotice, setPositionNotice] = useState<FloatingPanelPositionNotice>(null);
  const [viewportSize, setViewportSize] = useState<FrameAssetReferenceViewportSize>(() => ({
    width: typeof window === "undefined" ? 1024 : window.innerWidth,
    height: typeof window === "undefined" ? 768 : window.innerHeight,
  }));
  const viewportSizeRef = useRef(viewportSize);
  viewportSizeRef.current = viewportSize;
  const positionRestoredRef = useRef(false);
  const previousOwnerRef = useRef({ scopeIdentity: currentScopeIdentity, ownerToken });
  const isSmallViewport = viewportSize.width <= breakpoint;
  const floatingActive = presentation === "floating" && !isSmallViewport;

  function ownerIsCurrent(): boolean {
    const current = optionsRef.current;
    if (current.lifecycleSignal?.aborted) return false;
    try {
      return current.isOwnerCurrent();
    } catch {
      return false;
    }
  }

  function measuredPanelSize(): FrameAssetReferencePanelSize {
    const rect = panelRef.current?.getBoundingClientRect();
    return {
      width: rect !== undefined && rect.width > 0 ? rect.width : DEFAULT_PANEL_SIZE.width,
      height: rect !== undefined && rect.height > 0 ? rect.height : DEFAULT_PANEL_SIZE.height,
    };
  }

  function viewport(): FrameAssetReferenceViewportSize {
    return viewportSizeRef.current;
  }

  function clamp(position: FramePanelPosition): FramePanelPosition {
    return clampFramePanelPosition(position, measuredPanelSize(), viewport());
  }

  function defaultPosition(): FramePanelPosition {
    const current = optionsRef.current;
    return defaultFramePanelPosition(
      measuredPanelSize(),
      viewport(),
      current.defaultRightOffset,
      current.defaultTop,
    );
  }

  function setDesired(position: FramePanelPosition | null): void {
    desiredPositionRef.current = position;
    setDesiredPosition(position);
  }

  function releaseActiveDrag(): ActivePanelDrag | null {
    const active = activeDragRef.current;
    if (active === null) return null;
    activeDragRef.current = null;
    try {
      if (active.captureTarget.hasPointerCapture(active.pointerId)) {
        active.captureTarget.releasePointerCapture(active.pointerId);
      }
    } catch {
      // Pointer capture may already have been released by the browser.
    }
    return active;
  }

  function cancelDrag(restorePreview = true): void {
    const active = releaseActiveDrag();
    if (active === null || !restorePreview) return;
    const current = optionsRef.current;
    if (active.ownerToken !== current.ownerToken
      || active.scopeIdentity !== scopeIdentity(current.panelKind, current.userId, current.apiBaseUrl)
      || !ownerIsCurrent()) return;
    setVisiblePosition(clamp(active.origin));
  }

  function saveExplicitPosition(position: FramePanelPosition): void {
    if (!ownerIsCurrent()) return;
    const current = optionsRef.current;
    const scope: FramePanelPositionScope | null = createFramePanelPositionScope(
      current.userId,
      current.apiBaseUrl,
      current.panelKind,
    );
    if (scope === null) {
      setPositionNotice("invalid-scope");
      return;
    }
    const result = writeFramePanelPosition(scope, position);
    setPositionNotice(result === "saved" ? null : "write-unavailable");
  }

  function requestFloating(): void {
    if (isSmallViewport || !ownerIsCurrent()) return;
    cancelDrag();
    positionRestoredRef.current = false;
    setPositionNotice(null);
    setPresentation("floating");
  }

  function returnInline(): void {
    cancelDrag();
    setPresentation("inline");
    setPositionNotice(null);
  }

  function resetPosition(): void {
    cancelDrag(false);
    if (!floatingActive || !ownerIsCurrent()) return;
    const reset = defaultPosition();
    setDesired(reset);
    setVisiblePosition(reset);
    saveExplicitPosition(reset);
  }

  function beginDrag(event: PointerEvent<HTMLButtonElement>): void {
    if (!floatingActive || event.button !== 0 || !ownerIsCurrent()) return;
    event.preventDefault();
    cancelDrag(false);
    const rect = panelRef.current?.getBoundingClientRect();
    const origin = visiblePosition ?? clamp({
      left: rect?.left ?? defaultPosition().left,
      top: rect?.top ?? defaultPosition().top,
    });
    activeDragRef.current = {
      ownerToken: optionsRef.current.ownerToken,
      scopeIdentity: currentScopeIdentity,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      origin,
      captureTarget: event.currentTarget,
    };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Dragging still works when pointer capture is unavailable.
    }
  }

  function moveDrag(event: PointerEvent<HTMLButtonElement>): void {
    const active = activeDragRef.current;
    if (active === null || active.pointerId !== event.pointerId) return;
    const current = optionsRef.current;
    if (!floatingActive || !ownerIsCurrent()
      || active.ownerToken !== current.ownerToken
      || active.scopeIdentity !== scopeIdentity(current.panelKind, current.userId, current.apiBaseUrl)) {
      cancelDrag(false);
      return;
    }
    event.preventDefault();
    setVisiblePosition(clamp({
      left: active.origin.left + event.clientX - active.startX,
      top: active.origin.top + event.clientY - active.startY,
    }));
  }

  function finishDrag(event: PointerEvent<HTMLButtonElement>): void {
    const active = activeDragRef.current;
    if (active === null || active.pointerId !== event.pointerId) return;
    const current = optionsRef.current;
    if (!floatingActive || !ownerIsCurrent()
      || active.ownerToken !== current.ownerToken
      || active.scopeIdentity !== scopeIdentity(current.panelKind, current.userId, current.apiBaseUrl)) {
      cancelDrag(false);
      return;
    }
    event.preventDefault();
    const next = clamp({
      left: active.origin.left + event.clientX - active.startX,
      top: active.origin.top + event.clientY - active.startY,
    });
    releaseActiveDrag();
    setDesired(next);
    setVisiblePosition(next);
    saveExplicitPosition(next);
  }

  function cancelDragEvent(event: PointerEvent<HTMLButtonElement>): void {
    if (activeDragRef.current?.pointerId === event.pointerId) cancelDrag();
  }

  function moveWithKeyboard(event: KeyboardEvent<HTMLButtonElement>): void {
    if (!floatingActive || !ownerIsCurrent()) return;
    const step = event.shiftKey ? 1 : 16;
    const offsets: Record<string, { left: number; top: number }> = {
      ArrowLeft: { left: -step, top: 0 },
      ArrowRight: { left: step, top: 0 },
      ArrowUp: { left: 0, top: -step },
      ArrowDown: { left: 0, top: step },
    };
    const offset = offsets[event.key];
    if (offset === undefined) return;
    event.preventDefault();
    cancelDrag(false);
    const currentPosition = visiblePosition ?? defaultPosition();
    const next = clamp({ left: currentPosition.left + offset.left, top: currentPosition.top + offset.top });
    setDesired(next);
    setVisiblePosition(next);
    saveExplicitPosition(next);
  }

  useLayoutEffect(() => {
    const previous = previousOwnerRef.current;
    if (previous.scopeIdentity !== currentScopeIdentity || previous.ownerToken !== ownerToken) {
      cancelDrag(false);
      setDesired(null);
      setVisiblePosition(null);
      setPositionNotice(null);
      positionRestoredRef.current = false;
      previousOwnerRef.current = { scopeIdentity: currentScopeIdentity, ownerToken };
    }
  }, [currentScopeIdentity, ownerToken]);

  useLayoutEffect(() => {
    if (!floatingActive || !ownerIsCurrent()) {
      if (!ownerIsCurrent()) cancelDrag(false);
      return;
    }
    const panel = panelRef.current;
    if (panel === null) return;
    let desired = desiredPositionRef.current;
    if (!positionRestoredRef.current) {
      positionRestoredRef.current = true;
      const current = optionsRef.current;
      const scope = createFramePanelPositionScope(current.userId, current.apiBaseUrl, current.panelKind);
      if (scope === null) {
        setPositionNotice("invalid-scope");
      } else {
        const saved = readFramePanelPosition(scope);
        if (saved.kind === "stored") desired = saved.position;
        else if (saved.kind === "unavailable") setPositionNotice("read-unavailable");
      }
      desired ??= defaultPosition();
      setDesired(desired);
    }
    if (desired !== null) {
      const visible = clamp(desired);
      setVisiblePosition((current) => (samePosition(current, visible) ? current : visible));
    }
  }, [floatingActive, currentScopeIdentity, ownerToken, lifecycleSignal, desiredPosition, positionNotice, remeasureKey, viewportSize]);

  useEffect(() => {
    const onResize = (): void => {
      const next = { width: window.innerWidth, height: window.innerHeight };
      if (next.width <= (optionsRef.current.breakpoint ?? DEFAULT_BREAKPOINT)) cancelDrag();
      setViewportSize(next);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const currentSignal = lifecycleSignal;
    if (currentSignal === undefined) return undefined;
    const onAbort = (): void => cancelDrag(false);
    currentSignal.addEventListener("abort", onAbort);
    if (currentSignal.aborted) onAbort();
    return () => currentSignal.removeEventListener("abort", onAbort);
  }, [lifecycleSignal]);

  useEffect(() => () => cancelDrag(false), []);

  useEffect(() => {
    const panel = panelRef.current;
    if (!floatingActive || panel === null || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(() => {
      if (!ownerIsCurrent()) {
        cancelDrag(false);
        return;
      }
      const desired = desiredPositionRef.current;
      if (desired === null) return;
      const visible = clamp(desired);
      setVisiblePosition((current) => (samePosition(current, visible) ? current : visible));
    });
    observer.observe(panel);
    return () => observer.disconnect();
  }, [floatingActive, currentScopeIdentity, ownerToken, lifecycleSignal]);

  return {
    panelRef,
    dragHandleRef,
    presentation,
    floatingActive,
    isSmallViewport,
    desiredPosition,
    visiblePosition,
    positionNotice,
    requestFloating,
    returnInline,
    cancelDrag: () => cancelDrag(false),
    resetPosition,
    clearPositionNotice: () => setPositionNotice(null),
    beginDrag,
    moveDrag,
    finishDrag,
    cancelDragEvent,
    moveWithKeyboard,
  };
}
