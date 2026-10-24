import { useEffect, useRef, useState } from "react";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";

export interface CanvasMediaSourceEditorProps {
  title: string;
  initialValue: string;
  valueLabel: string;
  lifecycleSignal: AbortSignal;
  onClose(): void;
  onSave(value: string, signal: AbortSignal): Promise<"saved" | "conflict" | "unknown">;
  onVerify(signal: AbortSignal): Promise<string>;
}

export function CanvasMediaSourceEditor({
  title,
  initialValue,
  valueLabel,
  lifecycleSignal,
  onClose,
  onSave,
  onVerify,
}: CanvasMediaSourceEditorProps) {
  const [value, setValue] = useState(initialValue);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<"idle" | "conflict" | "unknown">("idle");
  const [message, setMessage] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const focus = useSeriesDialogFocus({ active: true, canDismiss: !busy, onDismiss: close });

  function close(): void {
    controllerRef.current?.abort();
    controllerRef.current = null;
    focus.markDismissed();
    onClose();
  }

  useEffect(() => {
    const abortPending = () => controllerRef.current?.abort();
    lifecycleSignal.addEventListener("abort", abortPending);
    if (lifecycleSignal.aborted) abortPending();
    return () => {
      lifecycleSignal.removeEventListener("abort", abortPending);
      controllerRef.current?.abort();
    };
  }, [lifecycleSignal]);

  async function save(): Promise<void> {
    if (busy || outcome !== "idle") return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setMessage(null);
    try {
      const result = await onSave(value, controller.signal);
      if (controller.signal.aborted) return;
      if (result === "saved") {
        close();
        return;
      }
      setOutcome(result);
      setMessage(result === "unknown"
        ? "写入结果尚未确认。草稿已保留，只能只读核对，不会自动重发。"
        : "章节来源已变化。当前草稿已冻结；请核对并重新打开画布后再编辑。");
    } catch (cause) {
      if (!controller.signal.aborted) {
        setMessage(cause instanceof Error ? cause.message : "媒体来源写入未完成。草稿已保留。");
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setBusy(false);
      }
    }
  }

  async function verify(): Promise<void> {
    if (busy || outcome !== "unknown") return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    try {
      const result = await onVerify(controller.signal);
      if (!controller.signal.aborted) setMessage(result);
    } catch (cause) {
      if (!controller.signal.aborted) setMessage(cause instanceof Error ? cause.message : "只读核对失败。");
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop" role="presentation">
      <section aria-labelledby="canvas-media-source-title" aria-modal="true" className="series-dialog chapter-editing-dialog"
        ref={focus.dialogRef} role="dialog" tabIndex={-1}>
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">画布来源 · 媒体</p>
            <h2 id="canvas-media-source-title">{title}</h2>
          </div>
          <button aria-label="关闭媒体来源编辑" className="icon-button" disabled={busy}
            onClick={close} type="button">×</button>
        </div>
        <label className="frame-text-editor-label" htmlFor="canvas-media-source-value">
          {valueLabel}
          <input autoComplete="off" disabled={busy || outcome !== "idle"} id="canvas-media-source-value"
            onChange={(event) => setValue(event.currentTarget.value)} type="text" value={value} />
        </label>
        <p className="chapter-editing-help">视频地址可以填写 URL 或 pending；保存按当前镜头和精确媒体槽位提交。</p>
        {message !== null && <p className="series-dialog-error" role={outcome === "idle" ? "alert" : "status"}>{message}</p>}
        <div className="series-dialog-actions">
          <button className="secondary-button" disabled={busy} onClick={close} type="button">关闭</button>
          {outcome === "unknown" && (
            <button className="secondary-button" disabled={busy} onClick={() => void verify()} type="button">
              {busy ? "正在只读核对…" : "只读核对来源"}
            </button>
          )}
          {outcome === "idle" && (
            <button className="primary-button" disabled={busy} onClick={() => void save()} type="button">
              {busy ? "正在提交…" : "保存来源"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
