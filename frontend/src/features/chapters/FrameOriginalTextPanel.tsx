import "./frameOriginalTextPanel.css";
import { useFloatingPanelPosition } from "./useFloatingPanelPosition";
import type { FrameOriginalTextSnapshot } from "./frameOriginalTextSnapshot";

interface FrameOriginalTextPanelProps {
  readonly snapshot: FrameOriginalTextSnapshot;
  readonly userId: string | null | undefined;
  readonly apiBaseUrl: string | null | undefined;
  readonly lifecycleSignal: AbortSignal;
  readonly isOwnerCurrent: () => boolean;
  readonly onHide: () => void;
}

function snapshotMessage(snapshot: FrameOriginalTextSnapshot): string | null {
  if (snapshot.kind === "missing") return "打开编辑器时，本镜头没有原文记录。";
  if (snapshot.kind === "unrecognized") return "打开编辑器时的原文内容无法识别。";
  return snapshot.value === "" ? "原文为空字符串。" : null;
}

export function FrameOriginalTextPanel({
  snapshot,
  userId,
  apiBaseUrl,
  lifecycleSignal,
  isOwnerCurrent,
  onHide,
}: FrameOriginalTextPanelProps) {
  const position = useFloatingPanelPosition({
    panelKind: "frame-original",
    userId,
    apiBaseUrl,
    ownerToken: lifecycleSignal,
    lifecycleSignal,
    isOwnerCurrent,
    initialPresentation: "floating",
    defaultRightOffset: 20,
    defaultTop: 60,
    remeasureKey: snapshot,
  });
  const notice = position.positionNotice === "read-unavailable"
    ? "浏览器未能读取原文对照位置，已使用默认位置。"
    : position.positionNotice === "write-unavailable"
      ? "浏览器未能记住原文对照位置；仍可继续使用和移动。"
      : position.positionNotice === "invalid-scope"
        ? "当前账号或接口身份不完整，原文对照仍可使用，但位置不会记住。"
        : null;
  const message = snapshotMessage(snapshot);

  return (
    <section
      aria-labelledby="frame-original-text-panel-title"
      className={position.floatingActive
        ? "frame-original-text-panel is-floating"
        : "frame-original-text-panel"}
      ref={position.panelRef}
      style={position.floatingActive && position.visiblePosition !== null
        ? { left: position.visiblePosition.left, top: position.visiblePosition.top, right: "auto" }
        : undefined}
    >
      <header className="frame-original-text-panel-heading">
        <div>
          <p className="eyebrow">只读对照</p>
          <h3 id="frame-original-text-panel-title">打开时原文</h3>
        </div>
        <div className="frame-original-text-panel-controls">
          {position.isSmallViewport && position.presentation === "floating" && (
            <span className="frame-original-text-panel-note" role="status">
              小屏幕下显示在编辑器内，桌面位置已保留。
            </span>
          )}
          {!position.isSmallViewport && position.presentation === "inline" && (
            <button className="secondary-button" onClick={position.requestFloating} type="button">
              浮动查看
            </button>
          )}
          {position.floatingActive && (
            <>
              <button
                aria-label="拖动原文对照浮窗；也可按方向键移动"
                className="secondary-button frame-original-text-drag-handle"
                onKeyDown={position.moveWithKeyboard}
                onLostPointerCapture={position.cancelDragEvent}
                onPointerCancel={position.cancelDragEvent}
                onPointerDown={position.beginDrag}
                onPointerMove={position.moveDrag}
                onPointerUp={position.finishDrag}
                ref={position.dragHandleRef}
                type="button"
              >
                <span aria-hidden="true">⠿</span> 拖动窗口
              </button>
              <button className="text-button" onClick={position.resetPosition} type="button">
                恢复默认位置
              </button>
              <button className="secondary-button" onClick={position.returnInline} type="button">
                放回编辑器内
              </button>
            </>
          )}
          <button className="text-button" onClick={() => {
            position.cancelDrag();
            onHide();
          }} type="button">
            隐藏对照
          </button>
        </div>
      </header>
      {notice !== null && <p className="frame-original-text-panel-feedback" role="status">{notice}</p>}
      {message !== null && <p className="frame-original-text-panel-state" role="status">{message}</p>}
      {snapshot.kind === "text" && <pre className="frame-original-text-panel-content">{snapshot.value}</pre>}
    </section>
  );
}
