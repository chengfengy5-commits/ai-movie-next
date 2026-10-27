import { useLayoutEffect, useRef, type CSSProperties, type PointerEvent, type ReactNode, type KeyboardEvent } from "react";
import { resizeSeriesWorkbenchLeftWidth, type SeriesWorkbenchLeftView, type SeriesWorkbenchRoute } from "./preferences";

export type SeriesWorkbenchSurfaceMode = "workbench" | "chapters" | "assets" | "chat" | "media" | "hidden";

interface SeriesWorkbenchSurfaceProps {
  readonly ownerKey: string;
  readonly mode: SeriesWorkbenchSurfaceMode;
  readonly route: SeriesWorkbenchRoute;
  readonly leftView: Exclude<SeriesWorkbenchLeftView, "continuous-preview">;
  readonly leftWidth: number;
  readonly preferenceNotice: string | null;
  readonly storyboard: ReactNode;
  readonly assets: ReactNode;
  readonly chat: ReactNode;
  readonly media: ReactNode;
  onRouteChange(route: SeriesWorkbenchRoute): void;
  onLeftViewChange(view: Exclude<SeriesWorkbenchLeftView, "continuous-preview">): void;
  onWidthChange(leftView: Exclude<SeriesWorkbenchLeftView, "continuous-preview">, width: number, ownerKey: string): void;
  onWidthCommit(leftView: Exclude<SeriesWorkbenchLeftView, "continuous-preview">, width: number, ownerKey: string): void;
}

interface ActiveDrag {
  readonly pointerId: number;
  readonly captureTarget: HTMLButtonElement;
  readonly ownerKey: string;
  readonly leftView: Exclude<SeriesWorkbenchLeftView, "continuous-preview">;
  readonly interactionEpoch: number;
  readonly originX: number;
  readonly originWidth: number;
  currentWidth: number;
}

function clampWidth(width: number): number {
  return Math.min(80, Math.max(30, Math.round(width)));
}

export function SeriesWorkbenchSurface({
  ownerKey,
  mode,
  route,
  leftView,
  leftWidth,
  preferenceNotice,
  storyboard,
  assets,
  chat,
  media,
  onRouteChange,
  onLeftViewChange,
  onWidthChange,
  onWidthCommit,
}: SeriesWorkbenchSurfaceProps) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const separatorRef = useRef<HTMLButtonElement | null>(null);
  const dragRef = useRef<ActiveDrag | null>(null);
  const interactionEpochRef = useRef(0);
  const layoutStyle = { "--workbench-left-width": `${clampWidth(leftWidth)}%` } as CSSProperties;

  function isCurrentDrag(drag: ActiveDrag): boolean {
    return drag.ownerKey === ownerKey
      && drag.leftView === leftView
      && drag.interactionEpoch === interactionEpochRef.current
      && mode === "workbench";
  }

  function releasePointerCapture(target: HTMLButtonElement, pointerId: number) {
    if (target.hasPointerCapture(pointerId)) {
      target.releasePointerCapture(pointerId);
    }
  }

  function cancelActiveDrag(restoreOrigin: boolean) {
    const drag = dragRef.current;
    if (drag === null) return;
    dragRef.current = null;
    interactionEpochRef.current += 1;
    releasePointerCapture(drag.captureTarget, drag.pointerId);
    if (restoreOrigin && drag.ownerKey === ownerKey) {
      onWidthChange(drag.leftView, drag.originWidth, drag.ownerKey);
    }
  }

  useLayoutEffect(() => {
    const drag = dragRef.current;
    if (drag !== null && !isCurrentDrag(drag)) {
      cancelActiveDrag(drag.ownerKey === ownerKey);
    }
  }, [leftView, mode, ownerKey]);

  function onPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (mode !== "workbench" || event.button !== 0 || !event.isPrimary) return;
    cancelActiveDrag(true);
    interactionEpochRef.current += 1;
    dragRef.current = {
      pointerId: event.pointerId,
      captureTarget: event.currentTarget,
      ownerKey,
      leftView,
      interactionEpoch: interactionEpochRef.current,
      originX: event.clientX,
      originWidth: leftWidth,
      currentWidth: leftWidth,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function onPointerMove(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    const root = rootRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    if (!isCurrentDrag(drag)) {
      cancelActiveDrag(drag.ownerKey === ownerKey);
      return;
    }
    if (root === null) return;
    const width = root.getBoundingClientRect().width;
    if (width <= 0) return;
    drag.currentWidth = resizeSeriesWorkbenchLeftWidth(
      drag.originWidth,
      event.clientX - drag.originX,
      width,
    );
    onWidthChange(drag.leftView, drag.currentWidth, drag.ownerKey);
  }

  function onPointerUp(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    if (!isCurrentDrag(drag)) {
      cancelActiveDrag(drag.ownerKey === ownerKey);
      return;
    }
    dragRef.current = null;
    interactionEpochRef.current += 1;
    releasePointerCapture(drag.captureTarget, event.pointerId);
    onWidthCommit(drag.leftView, drag.currentWidth, drag.ownerKey);
  }

  function onPointerCancel(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    cancelActiveDrag(drag.ownerKey === ownerKey);
  }

  function onLostPointerCapture(event: PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (drag === null || drag.pointerId !== event.pointerId) return;
    cancelActiveDrag(drag.ownerKey === ownerKey);
  }

  function onSeparatorKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (mode !== "workbench") return;
    const drag = dragRef.current;
    const baseWidth = drag !== null && drag.ownerKey === ownerKey && drag.leftView === leftView
      ? drag.originWidth
      : leftWidth;
    cancelActiveDrag(drag !== null && drag.ownerKey === ownerKey);
    interactionEpochRef.current += 1;
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = clampWidth(baseWidth - 2);
    if (event.key === "ArrowRight") next = clampWidth(baseWidth + 2);
    if (event.key === "Home") next = 70;
    if (next === null) return;
    event.preventDefault();
    onWidthChange(leftView, next, ownerKey);
    onWidthCommit(leftView, next, ownerKey);
  }

  return (
    <div
      className={`series-workbench-surface mode-${mode}`}
      ref={rootRef}
      style={layoutStyle}
      hidden={mode === "hidden"}
    >
      <div className="series-workbench-left" hidden={mode !== "workbench" && mode !== "chapters" && mode !== "assets"}>
        {mode === "workbench" && (
          <nav className="series-workbench-left-tabs" aria-label="剧集内容视图">
            <button aria-pressed={leftView === "storyboard"} onClick={() => {
              cancelActiveDrag(true);
              onLeftViewChange("storyboard");
            }} type="button">分镜</button>
            <button aria-pressed={leftView === "assets"} onClick={() => {
              cancelActiveDrag(true);
              onLeftViewChange("assets");
            }} type="button">素材库</button>
          </nav>
        )}
        <div className="series-workbench-left-pane storyboard-pane" hidden={mode !== "chapters" && !(mode === "workbench" && leftView === "storyboard")}>
          {storyboard}
        </div>
        <div className="series-workbench-left-pane assets-pane" hidden={mode !== "assets" && !(mode === "workbench" && leftView === "assets")}>
          {assets}
        </div>
      </div>
      {mode === "workbench" && (
        <button
          aria-label="调整剧集内容与创作区宽度"
          aria-orientation="vertical"
          aria-valuemax={80}
          aria-valuemin={30}
          aria-valuenow={clampWidth(leftWidth)}
          className="series-workbench-separator"
          onKeyDown={onSeparatorKeyDown}
          onPointerCancel={onPointerCancel}
          onPointerDown={onPointerDown}
          onLostPointerCapture={onLostPointerCapture}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          ref={separatorRef}
          role="separator"
          tabIndex={0}
          type="button"
        />
      )}
      <div className="series-workbench-right" hidden={mode === "hidden" || mode === "chapters" || mode === "assets"}>
        {mode === "workbench" && (
          <header className="series-workbench-header">
            <div>
              <p className="eyebrow">剧集工作台</p>
              <h1>创作区</h1>
            </div>
            <div className="series-workbench-route-tabs" aria-label="创作工具">
              <button aria-pressed={route === "chat"} onClick={() => {
                cancelActiveDrag(true);
                onRouteChange("chat");
              }} type="button">AI 助手</button>
              <button aria-pressed={route === "media"} onClick={() => {
                cancelActiveDrag(true);
                onRouteChange("media");
              }} type="button">图片与视频</button>
            </div>
          </header>
        )}
        {preferenceNotice !== null && mode === "workbench" && <p className="series-workbench-notice" role="status">{preferenceNotice}</p>}
        <div className="series-workbench-consumer" hidden={mode !== "chat" && !(mode === "workbench" && route === "chat")}>
          {chat}
        </div>
        <div className="series-workbench-consumer" hidden={mode !== "media" && !(mode === "workbench" && route === "media")}>
          {media}
        </div>
      </div>
    </div>
  );
}
