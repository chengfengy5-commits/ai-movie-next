import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { Chapter, StoryboardAsset } from "../../shared/api/contracts";
import { CanvasSourceError } from "../../shared/api/canvasSources";
import { normalizeApiError } from "../../shared/api/services";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import {
  FrameAssetReferencesPanel,
  type FrameAssetReferenceAssetTarget,
  type FrameAssetReferenceOwner,
} from "./FrameAssetReferencesPanel";
import { FrameOriginalTextPanel } from "./FrameOriginalTextPanel";
import type { FrameOriginalTextSnapshot } from "./frameOriginalTextSnapshot";

export interface FrameTextSaveResult {
  status: "saved" | "unchanged" | "conflict" | "unknown";
  message?: string;
  serverText?: string | null;
  verifiedBase?: FrameTextVerifiedBase;
}

export interface FrameTextVerifiedBase {
  directory: Chapter[];
  chapter: Chapter;
  assets: StoryboardAsset[];
}

export interface FrameTextNavigationResult {
  status: "moved" | "boundary" | "conflict" | "unknown";
  message?: string;
  serverText?: string | null;
}

export interface FrameTextUnknownVerificationResult {
  status: "confirmed" | "unresolved";
  message: string;
}

type ExitIntent =
  | { kind: "close" }
  | { kind: "asset-navigation"; target: FrameAssetReferenceAssetTarget };

interface FrameTextEditorProps {
  chapter: Chapter;
  position: number;
  initialText: string;
  originalTextSnapshot?: FrameOriginalTextSnapshot;
  referenceOwner?: FrameAssetReferenceOwner;
  referenceOwnerEpoch?: number;
  userId?: string | null;
  apiBaseUrl?: string | null;
  isSessionCurrent?(): boolean;
  isReferenceOwnerCurrent?(): boolean;
  lockNotice: string | null;
  lifecycleSignal: AbortSignal;
  onClose(): void;
  onNavigateToAsset?(target: FrameAssetReferenceAssetTarget): void;
  onUnauthorized?(): void;
  onSave(
    text: string,
    signal: AbortSignal,
    explicitOverwrite: boolean,
    expectedServerText: string | null | undefined,
  ): Promise<FrameTextSaveResult>;
  onNavigateFrame?(
    direction: -1 | 1,
    text: string,
    signal: AbortSignal,
    dirty: boolean,
  ): Promise<FrameTextNavigationResult>;
  onVerifyUnknown?(signal: AbortSignal): Promise<string>;
  onVerifyUnknownText?(text: string, signal: AbortSignal): Promise<FrameTextUnknownVerificationResult>;
  allowOverwriteOnConflict?: boolean;
}

function editErrorMessage(cause: unknown): string {
  const error = normalizeApiError(cause);
  return cause instanceof CanvasSourceError ? cause.message : error.detail ?? error.message;
}

export function FrameTextEditor({
  chapter,
  position,
  initialText,
  originalTextSnapshot = { kind: "missing" },
  referenceOwner,
  referenceOwnerEpoch = 0,
  userId = null,
  apiBaseUrl = null,
  isSessionCurrent,
  isReferenceOwnerCurrent,
  lockNotice,
  lifecycleSignal,
  onClose,
  onNavigateToAsset,
  onUnauthorized = () => undefined,
  onSave,
  onNavigateFrame,
  onVerifyUnknown,
  onVerifyUnknownText,
  allowOverwriteOnConflict = true,
}: FrameTextEditorProps) {
  const [draft, setDraft] = useState(initialText);
  const [baselineText, setBaselineText] = useState<string | null>(initialText);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [serverText, setServerText] = useState<string | null | undefined>(undefined);
  const [conflicted, setConflicted] = useState(false);
  const [writeUnknown, setWriteUnknown] = useState(false);
  const [originalTextVisible, setOriginalTextVisible] = useState(true);
  const [referencesVisible, setReferencesVisible] = useState(true);
  const [exitIntent, setExitIntent] = useState<ExitIntent | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const focus = useSeriesDialogFocus({ active: true, canDismiss: true, onDismiss: requestClose });
  const dirty = baselineText === null || draft !== baselineText;
  const frameCount = chapter.content?.length ?? 0;
  const continuousEditing = referenceOwner !== undefined && onNavigateFrame !== undefined;
  const sessionIsCurrent = isSessionCurrent ?? (() => true);
  const referenceOwnerCurrent = referenceOwner !== undefined
    && (isReferenceOwnerCurrent?.() ?? sessionIsCurrent());

  useEffect(() => {
    const abortPendingAction = () => controllerRef.current?.abort();
    lifecycleSignal.addEventListener("abort", abortPendingAction);
    if (lifecycleSignal.aborted) abortPendingAction();
    return () => {
      lifecycleSignal.removeEventListener("abort", abortPendingAction);
      controllerRef.current?.abort();
    };
  }, [lifecycleSignal]);

  function isCurrentSession(): boolean {
    return !lifecycleSignal.aborted && sessionIsCurrent();
  }

  function closeImmediately(): void {
    if (!isCurrentSession()) return;
    controllerRef.current?.abort();
    controllerRef.current = null;
    focus.markDismissed();
    onClose();
  }

  function requestClose(): void {
    if (!continuousEditing) {
      closeImmediately();
      return;
    }
    if (controllerRef.current !== null || saving) return;
    if (writeUnknown) {
      setMessage("写入结果尚未确认。请先只读核对；当前编辑不会再次提交，也不能通过关闭或跳转绕过核对。");
      return;
    }
    if (dirty) {
      setExitIntent({ kind: "close" });
      return;
    }
    closeImmediately();
  }

  function navigateToAsset(target: FrameAssetReferenceAssetTarget): void {
    if (!continuousEditing || controllerRef.current !== null || saving || writeUnknown || !isCurrentSession()) return;
    if (onNavigateToAsset === undefined || !target.isCurrent()) {
      setMessage("关联素材来源已变化，请重新读取后再跳转。");
      return;
    }
    if (dirty) {
      setExitIntent({ kind: "asset-navigation", target });
      setMessage("当前文字有未保存修改。请先保存，或明确放弃修改后再跳转到素材。");
      return;
    }
    finishAssetNavigation(target);
  }

  function finishAssetNavigation(target: FrameAssetReferenceAssetTarget): void {
    if (!continuousEditing || !isCurrentSession() || !target.isCurrent() || onNavigateToAsset === undefined) {
      setMessage("关联素材来源已变化，请重新读取后再跳转。");
      setExitIntent(null);
      return;
    }
    onNavigateToAsset(target);
    closeImmediately();
  }

  async function submit(
    explicitOverwrite = false,
    afterSave?: ExitIntent,
  ): Promise<void> {
    if (controllerRef.current !== null || saving || writeUnknown
      || (conflicted && !allowOverwriteOnConflict) || !isCurrentSession()) return;
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
        setBaselineText(result.serverText ?? null);
        setMessage(result.message ?? "服务器中的镜头内容已变化。请查看当前值，再决定是否用草稿覆盖文字。");
        return;
      }
      if (result.status === "unknown") {
        setWriteUnknown(true);
        setMessage(result.message ?? "写入结果尚未确认。请保留草稿并只读核对，不要再次提交。");
        return;
      }
      if (afterSave?.kind === "asset-navigation") {
        finishAssetNavigation(afterSave.target);
      } else {
        closeImmediately();
      }
    } catch (cause) {
      if (!controller.signal.aborted) setMessage(editErrorMessage(cause));
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSaving(false);
      }
    }
  }

  async function navigateFrame(direction: -1 | 1): Promise<void> {
    if (onNavigateFrame === undefined || controllerRef.current !== null || saving || writeUnknown || conflicted || !isCurrentSession()) return;
    if (position + direction < 1 || position + direction > frameCount) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setMessage(null);
    try {
      const result = await onNavigateFrame(direction, draft, controller.signal, dirty);
      if (controller.signal.aborted || result.status === "moved") return;
      if (result.status === "conflict") {
        setConflicted(true);
        setServerText(result.serverText);
        setBaselineText(result.serverText ?? null);
      } else if (result.status === "unknown") {
        setWriteUnknown(true);
      }
      setMessage(result.message ?? (result.status === "boundary"
        ? "当前镜头已到达边界。"
        : "服务器中的章节已变化，仍停留在当前镜头。"));
    } catch (cause) {
      if (!controller.signal.aborted) setMessage(editErrorMessage(cause));
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSaving(false);
      }
    }
  }

  async function verifyUnknown(): Promise<void> {
    if ((onVerifyUnknown === undefined && onVerifyUnknownText === undefined)
      || controllerRef.current !== null || saving || !writeUnknown || !isCurrentSession()) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    try {
      if (onVerifyUnknownText !== undefined) {
        const result = await onVerifyUnknownText(draft, controller.signal);
        if (controller.signal.aborted) return;
        setMessage(result.message);
        if (result.status === "confirmed") {
          setWriteUnknown(false);
          setConflicted(false);
          setServerText(undefined);
          setBaselineText(draft);
        }
      } else if (onVerifyUnknown !== undefined) {
        const result = await onVerifyUnknown(controller.signal);
        if (!controller.signal.aborted) setMessage(result);
      }
    } catch (cause) {
      if (!controller.signal.aborted) setMessage(editErrorMessage(cause));
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSaving(false);
      }
    }
  }

  function restoreOpeningOriginal(): void {
    if (saving || writeUnknown || conflicted || originalTextSnapshot.kind !== "text"
      || originalTextSnapshot.value === "" || !isCurrentSession()) return;
    const textarea = textareaRef.current;
    if (textarea === null || typeof document.execCommand !== "function") {
      setMessage("当前浏览器不能保留撤销记录，原文未回填。");
      return;
    }
    textarea.focus();
    textarea.select();
    if (textareaRef.current !== textarea || !textarea.isConnected || !isCurrentSession()) {
      setMessage("当前镜头已变化，原文未回填。");
      return;
    }
    const before = textarea.value;
    const inserted = document.execCommand("insertText", false, originalTextSnapshot.value);
    if (textareaRef.current !== textarea || !textarea.isConnected || !isCurrentSession()) {
      setMessage("当前镜头已变化；受控草稿没有接收此次回填。");
      return;
    }
    if (!inserted && textarea.value === before) {
      setMessage("浏览器未能将原文写入可撤销编辑操作，草稿保持不变。");
      return;
    }
    setDraft(textarea.value);
    setConflicted(false);
    setServerText(undefined);
    setMessage(null);
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (!event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      void navigateFrame(-1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      void navigateFrame(1);
    }
  }

  function continueExitIntent(): void {
    const intent = exitIntent;
    if (intent === null || writeUnknown || saving) return;
    setExitIntent(null);
    if (intent.kind === "close") closeImmediately();
    else finishAssetNavigation(intent.target);
  }

  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop" role="presentation">
      <section
        aria-labelledby="frame-text-editor-title"
        aria-modal="true"
        className="series-dialog chapter-editing-dialog frame-text-editor"
        onKeyDown={continuousEditing ? handleDialogKeyDown : undefined}
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">{chapter.title || "未命名章节"} · 镜头 {position}</p>
            <h2 id="frame-text-editor-title">编辑分镜文字</h2>
          </div>
          <div className="frame-text-editor-heading-controls">
            {!originalTextVisible && (
              <button className="secondary-button" onClick={() => setOriginalTextVisible(true)} type="button">
                显示原文对照
              </button>
            )}
            {!referencesVisible && referenceOwner !== undefined && referenceOwnerCurrent && (
              <button className="secondary-button" onClick={() => setReferencesVisible(true)} type="button">
                显示关联素材
              </button>
            )}
            <button aria-label="关闭文字编辑" className="icon-button" onClick={requestClose} type="button">×</button>
          </div>
        </div>
        {(continuousEditing || originalTextVisible) && (
          <FrameOriginalTextPanel
            apiBaseUrl={apiBaseUrl}
            hidden={continuousEditing && !originalTextVisible}
            isOwnerCurrent={isCurrentSession}
            lifecycleSignal={lifecycleSignal}
            onHide={() => setOriginalTextVisible(false)}
            onRestoreToEditor={restoreOpeningOriginal}
            snapshot={originalTextSnapshot}
            userId={userId}
          />
        )}
        {referenceOwner !== undefined && (
          <FrameAssetReferencesPanel
            key={referenceOwnerEpoch}
            currentScope={{
              userId: referenceOwner.userId,
              seriesId: referenceOwner.seriesId,
              services: referenceOwner.services,
              chapter: referenceOwner.chapter,
              frame: referenceOwner.frame,
              position: referenceOwner.position,
            }}
            hidden={!referencesVisible || !referenceOwnerCurrent}
            initialPresentation="floating"
            isOwnerCurrent={isReferenceOwnerCurrent ?? sessionIsCurrent}
            onClose={() => undefined}
            onHide={() => setReferencesVisible(false)}
            {...(onNavigateToAsset === undefined ? {} : { onNavigateToAsset: navigateToAsset })}
            onUnauthorized={onUnauthorized}
            owner={referenceOwner}
          />
        )}
        {referenceOwner !== undefined && !referenceOwnerCurrent && (
          <p className="chapter-editing-lock" role="status">当前镜头关联素材来源已变化，旧读取已隐藏。</p>
        )}
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
            ref={textareaRef}
            rows={8}
            value={draft}
          />
        </label>
        <p className="chapter-editing-help">{continuousEditing
          ? "只修改当前镜头文字；原文字段和素材关联会保留。按 Ctrl+方向键可连续编辑上一镜或下一镜。"
          : "可以留空。只修改这个镜头的文字，原文、素材关联和其他内容会保留。"}</p>
        {conflicted && (
          <div className="frame-text-server-value" role="region" aria-label="服务器当前镜头文字">
            <strong>服务器当前值</strong>
            <p>{serverText === null || serverText === undefined ? "当前没有可识别的文字。" : serverText || "（空文字）"}</p>
          </div>
        )}
        {message !== null && <p className="series-dialog-error" role={conflicted || writeUnknown ? "status" : "alert"}>{message}</p>}
        {exitIntent !== null && (
          <section aria-label="离开编辑器确认" className="frame-text-editor-exit-confirmation" role="group">
            <p>{writeUnknown
              ? "写入结果尚未确认。此时不能关闭编辑器或跳转；请先只读核对来源。"
              : exitIntent.kind === "asset-navigation"
                ? "保存并确认后跳转，或明确放弃草稿后跳转。"
                : "草稿尚未保存。请保存后离开，或明确放弃修改。"}</p>
            {writeUnknown ? (
              <button className="secondary-button" disabled={saving} onClick={() => void verifyUnknown()} type="button">
                {saving ? "正在只读核对…" : "只读核对写入结果"}
              </button>
            ) : (
              <>
                <button
                  className="primary-button"
                  disabled={saving || conflicted || !isCurrentSession()}
                  onClick={() => void submit(false, exitIntent)}
                  type="button"
                >
                  {saving ? "正在核对并保存…" : exitIntent.kind === "asset-navigation" ? "保存并跳转" : "保存并关闭"}
                </button>
                <button className="secondary-button" disabled={saving} onClick={continueExitIntent} type="button">
                  {exitIntent.kind === "asset-navigation" ? "放弃修改并跳转" : "放弃修改并关闭"}
                </button>
              </>
            )}
            <button className="text-button" disabled={saving} onClick={() => setExitIntent(null)} type="button">
              继续编辑
            </button>
          </section>
        )}
        <div className="series-dialog-actions frame-text-editor-actions">
          <button className="secondary-button" onClick={requestClose} type="button">取消</button>
          {continuousEditing && (
            <>
              <button
                className="secondary-button"
                disabled={saving || writeUnknown || conflicted || position <= 1 || !isCurrentSession()}
                onClick={() => void navigateFrame(-1)}
                type="button"
              >
                上一镜头
              </button>
              <button
                className="secondary-button"
                disabled={saving || writeUnknown || conflicted || position >= frameCount || !isCurrentSession()}
                onClick={() => void navigateFrame(1)}
                type="button"
              >
                下一镜头
              </button>
            </>
          )}
          {writeUnknown && (
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
