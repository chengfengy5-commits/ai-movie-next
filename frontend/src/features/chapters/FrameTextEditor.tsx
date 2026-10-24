import { useEffect, useRef, useState } from "react";
import type { Chapter } from "../../shared/api/contracts";
import { CanvasSourceError } from "../../shared/api/canvasSources";
import { normalizeApiError } from "../../shared/api/services";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";

export interface FrameTextSaveResult {
  status: "saved" | "unchanged" | "conflict" | "unknown";
  message?: string;
  serverText?: string | null;
}

interface FrameTextEditorProps {
  chapter: Chapter;
  position: number;
  initialText: string;
  lockNotice: string | null;
  lifecycleSignal: AbortSignal;
  onClose(): void;
  onSave(
    text: string,
    signal: AbortSignal,
    explicitOverwrite: boolean,
    expectedServerText: string | null | undefined,
  ): Promise<FrameTextSaveResult>;
  allowOverwriteOnConflict?: boolean;
  onVerifyUnknown?(signal: AbortSignal): Promise<string>;
}

export function FrameTextEditor({
  chapter,
  position,
  initialText,
  lockNotice,
  lifecycleSignal,
  onClose,
  onSave,
  allowOverwriteOnConflict = true,
  onVerifyUnknown,
}: FrameTextEditorProps) {
  const [draft, setDraft] = useState(initialText);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [serverText, setServerText] = useState<string | null | undefined>(undefined);
  const [conflicted, setConflicted] = useState(false);
  const [writeUnknown, setWriteUnknown] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: close });

  useEffect(() => {
    const abortPendingSave = () => controllerRef.current?.abort();
    lifecycleSignal.addEventListener("abort", abortPendingSave);
    if (lifecycleSignal.aborted) abortPendingSave();
    return () => {
      lifecycleSignal.removeEventListener("abort", abortPendingSave);
      controllerRef.current?.abort();
    };
  }, [lifecycleSignal]);

  function close(): void {
    controllerRef.current?.abort();
    controllerRef.current = null;
    focus.markDismissed();
    onClose();
  }

  async function submit(explicitOverwrite = false): Promise<void> {
    if (saving || writeUnknown || (conflicted && !allowOverwriteOnConflict)) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setMessage(null);
    try {
      const result = await onSave(
        draft,
        controller.signal,
        explicitOverwrite,
        explicitOverwrite ? serverText : undefined,
      );
      if (controller.signal.aborted) return;
      if (result.status === "conflict") {
        setConflicted(true);
        setServerText(result.serverText);
        setMessage(result.message ?? "服务器中的镜头内容已变化。请查看当前值，再决定是否用草稿覆盖文字。");
        return;
      }
      if (result.status === "unknown") {
        setWriteUnknown(true);
        setMessage(result.message ?? "写入结果尚未确认。请保留草稿并只读核对，不要再次提交。");
        return;
      }
      focus.markDismissed();
      onClose();
    } catch (cause) {
      if (!controller.signal.aborted) {
        const error = normalizeApiError(cause);
        setMessage(cause instanceof CanvasSourceError ? cause.message : error.detail ?? error.message);
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSaving(false);
      }
    }
  }

  async function verifyUnknown(): Promise<void> {
    if (saving || !writeUnknown || onVerifyUnknown === undefined) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    try {
      const verificationMessage = await onVerifyUnknown(controller.signal);
      if (!controller.signal.aborted) setMessage(verificationMessage);
    } catch (cause) {
      if (!controller.signal.aborted) {
        const error = normalizeApiError(cause);
        setMessage(cause instanceof CanvasSourceError ? cause.message : error.detail ?? error.message);
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSaving(false);
      }
    }
  }

  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop" role="presentation">
      <section
        aria-labelledby="frame-text-editor-title"
        aria-modal="true"
        className="series-dialog chapter-editing-dialog frame-text-editor"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">{chapter.title || "未命名章节"} · 镜头 {position}</p>
            <h2 id="frame-text-editor-title">编辑分镜文字</h2>
          </div>
          <button aria-label="关闭文字编辑" className="icon-button" onClick={close} type="button">×</button>
        </div>
        {lockNotice !== null && <p className="chapter-editing-lock" role="status">{lockNotice}</p>}
        <label className="frame-text-editor-label">
          镜头文字
          <textarea
            autoFocus
            disabled={saving || writeUnknown || (conflicted && !allowOverwriteOnConflict)}
            onChange={(event) => {
              setDraft(event.currentTarget.value);
              setConflicted(false);
              setServerText(undefined);
              setMessage(null);
            }}
            rows={8}
            value={draft}
          />
        </label>
        <p className="chapter-editing-help">可以留空。只修改这个镜头的文字，原文、素材关联和其他内容会保留。</p>
        {conflicted && (
          <div className="frame-text-server-value" role="region" aria-label="服务器当前镜头文字">
            <strong>服务器当前值</strong>
            <p>{serverText === null || serverText === undefined ? "当前没有可识别的文字。" : serverText || "（空文字）"}</p>
          </div>
        )}
        {message !== null && <p className="series-dialog-error" role={conflicted ? "status" : "alert"}>{message}</p>}
        <div className="series-dialog-actions frame-text-editor-actions">
          <button className="secondary-button" onClick={close} type="button">取消</button>
          {writeUnknown && onVerifyUnknown !== undefined && (
            <button className="secondary-button" disabled={saving} onClick={() => void verifyUnknown()} type="button">
              {saving ? "正在只读核对…" : "只读核对来源"}
            </button>
          )}
          {conflicted && allowOverwriteOnConflict && (
            <button className="secondary-button" disabled={saving} onClick={() => void submit(true)} type="button">
              {saving ? "正在核对…" : "以草稿覆盖当前文字"}
            </button>
          )}
          {!conflicted && !writeUnknown && (
            <button className="primary-button" disabled={saving} onClick={() => void submit(false)} type="button">
              {saving ? "正在核对并保存…" : "保存文字"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
