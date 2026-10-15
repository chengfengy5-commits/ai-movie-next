import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Chapter, Character, Prop, Scene } from "../../../shared/api/contracts";
import { normalizeApiError } from "../../../shared/api/services";
import { useSeriesDialogFocus } from "../../series/useSeriesDialogFocus";
import type { FrameStructureDraft } from "./contract";
import "./frameStructureEditor.css";

export type FrameStructureMode =
  | { kind: "insert"; afterPosition: number }
  | { kind: "delete"; position: number };

export type FrameStructureSubmitResult =
  | { status: "saved"; message?: string }
  | { status: "conflict"; message: string }
  | { status: "unknown"; message: string };

export type FrameStructureVerifyResult =
  | { status: "saved"; message: string }
  | { status: "not-applied"; message: string }
  | { status: "unknown"; message: string };

interface FrameStructureEditorProps {
  chapter: Chapter;
  mode: FrameStructureMode;
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
  lockNotice: string | null;
  lifecycleSignal: AbortSignal;
  onClose(): void;
  onDraftDirtyChange?(dirty: boolean): void;
  onRegisterDiscardRequest?(request: (() => Promise<boolean>) | null): void;
  onSubmit(draft: FrameStructureDraft | null, signal: AbortSignal): Promise<FrameStructureSubmitResult>;
  onVerify(signal: AbortSignal): Promise<FrameStructureVerifyResult>;
}

type ReferenceCategory = "characterAssetIds" | "sceneAssetIds" | "propAssetIds";

export function FrameStructureEditor({
  chapter,
  mode,
  characters,
  scenes,
  props,
  lockNotice,
  lifecycleSignal,
  onClose,
  onDraftDirtyChange,
  onRegisterDiscardRequest,
  onSubmit,
  onVerify,
}: FrameStructureEditorProps) {
  const [text, setText] = useState("");
  const [selectedReferences, setSelectedReferences] = useState<Record<ReferenceCategory, Set<string>>>(() => ({
    characterAssetIds: new Set(),
    sceneAssetIds: new Set(),
    propAssetIds: new Set(),
  }));
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [writeUnknown, setWriteUnknown] = useState(false);
  const [conflicted, setConflicted] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [discardConfirmationOpen, setDiscardConfirmationOpen] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const discardResolutionRef = useRef<{ promise: Promise<boolean>; resolve(value: boolean): void } | null>(null);
  const continueEditingButtonRef = useRef<HTMLButtonElement | null>(null);
  const busy = saving || verifying;
  const insertion = mode.kind === "insert";
  const dirty = insertion && (text !== "" || Object.values(selectedReferences).some((ids) => ids.size > 0));
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const requestDiscardConfirmation = useCallback((): Promise<boolean> => {
    if (!dirtyRef.current) return Promise.resolve(true);
    if (discardResolutionRef.current !== null) return discardResolutionRef.current.promise;
    let resolve!: (value: boolean) => void;
    const promise = new Promise<boolean>((complete) => { resolve = complete; });
    discardResolutionRef.current = { promise, resolve };
    setDiscardConfirmationOpen(true);
    return promise;
  }, []);
  const focus = useSeriesDialogFocus({
    active: true,
    canDismiss: !busy,
    onDismiss: () => {
      if (discardConfirmationOpen) settleDiscardConfirmation(false);
      else close();
    },
  });
  const positionLabel = insertion
    ? mode.afterPosition === 0 ? "在章节开头插入" : `在镜头 ${mode.afterPosition} 后插入`
    : `镜头 ${mode.position}`;

  useEffect(() => {
    const abortPendingRequest = () => {
      controllerRef.current?.abort();
      settleDiscardConfirmation(false);
    };
    lifecycleSignal.addEventListener("abort", abortPendingRequest);
    if (lifecycleSignal.aborted) abortPendingRequest();
    return () => {
      lifecycleSignal.removeEventListener("abort", abortPendingRequest);
      controllerRef.current?.abort();
      settleDiscardConfirmation(false);
    };
  }, [lifecycleSignal]);

  useLayoutEffect(() => {
    onDraftDirtyChange?.(dirty);
    return () => onDraftDirtyChange?.(false);
  }, [dirty, onDraftDirtyChange]);

  useEffect(() => {
    onRegisterDiscardRequest?.(requestDiscardConfirmation);
    return () => onRegisterDiscardRequest?.(null);
  }, [onRegisterDiscardRequest, requestDiscardConfirmation]);

  useEffect(() => {
    if (discardConfirmationOpen) continueEditingButtonRef.current?.focus();
  }, [discardConfirmationOpen]);

  function settleDiscardConfirmation(discard: boolean): void {
    const pending = discardResolutionRef.current;
    if (pending === null) return;
    discardResolutionRef.current = null;
    setDiscardConfirmationOpen(false);
    if (discard) focus.markDismissed();
    pending.resolve(discard);
  }

  function finishClose(): void {
    controllerRef.current?.abort();
    controllerRef.current = null;
    focus.markDismissed();
    onClose();
  }

  function close(): void {
    if (busy || discardConfirmationOpen) return;
    if (!dirtyRef.current) {
      finishClose();
      return;
    }
    void requestDiscardConfirmation().then((discard) => {
      if (discard && !lifecycleSignal.aborted) finishClose();
    });
  }

  function toggleReference(category: ReferenceCategory, assetId: string): void {
    setSelectedReferences((current) => {
      const next = new Set(current[category]);
      if (next.has(assetId)) next.delete(assetId);
      else next.add(assetId);
      return { ...current, [category]: next };
    });
  }

  async function submit(): Promise<void> {
    if (busy || conflicted || writeUnknown) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setSaving(true);
    setMessage(null);
    const draft: FrameStructureDraft | null = insertion ? {
      text,
      characterAssetIds: [...selectedReferences.characterAssetIds],
      sceneAssetIds: [...selectedReferences.sceneAssetIds],
      propAssetIds: [...selectedReferences.propAssetIds],
    } : null;
    try {
      const result = await onSubmit(draft, controller.signal);
      if (controller.signal.aborted) return;
      setMessage(result.message ?? null);
      if (result.status === "saved") {
        focus.markDismissed();
        onClose();
      } else if (result.status === "unknown") {
        setWriteUnknown(true);
      } else {
        setConflicted(true);
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        const error = normalizeApiError(cause);
        setMessage(error.detail ?? error.message);
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setSaving(false);
      }
    }
  }

  async function verify(): Promise<void> {
    if (busy || !writeUnknown) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setVerifying(true);
    setMessage(null);
    try {
      const result = await onVerify(controller.signal);
      if (controller.signal.aborted) return;
      setMessage(result.message);
      if (result.status === "saved") {
        focus.markDismissed();
        onClose();
      } else if (result.status === "not-applied") {
        setWriteUnknown(false);
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        const error = normalizeApiError(cause);
        setMessage(error.detail ?? error.message);
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setVerifying(false);
      }
    }
  }

  return (
    <div className="series-dialog-backdrop chapter-editing-backdrop" role="presentation">
      <section
        aria-labelledby="frame-structure-editor-title"
        aria-modal="true"
        className="series-dialog chapter-editing-dialog frame-structure-editor"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">{chapter.title || "未命名章节"} · {positionLabel}</p>
            <h2 id="frame-structure-editor-title">{insertion ? "插入分镜" : "删除分镜"}</h2>
          </div>
          <button
            aria-label="关闭分镜结构编辑"
            className="icon-button"
            disabled={busy || discardConfirmationOpen}
            onClick={close}
            type="button"
          >×</button>
        </div>
        {lockNotice !== null && <p className="chapter-editing-lock" role="status">{lockNotice}</p>}
        {insertion ? (
          <>
            <label className="frame-text-editor-label">
              分镜文字
              <textarea
                autoFocus
                disabled={busy || conflicted || writeUnknown || discardConfirmationOpen}
                onChange={(event) => setText(event.currentTarget.value)}
                rows={6}
                value={text}
              />
            </label>
            <p className="chapter-editing-help">可以留空。只选择本剧集已有的角色、场景和道具素材；新镜头会创建自己的分镜素材。</p>
            <ReferenceChoices
              category="characterAssetIds"
              choices={characters}
              disabled={busy || conflicted || writeUnknown || discardConfirmationOpen}
              onToggle={toggleReference}
              selected={selectedReferences.characterAssetIds}
              title="角色素材"
            />
            <ReferenceChoices
              category="sceneAssetIds"
              choices={scenes}
              disabled={busy || conflicted || writeUnknown || discardConfirmationOpen}
              onToggle={toggleReference}
              selected={selectedReferences.sceneAssetIds}
              title="场景素材"
            />
            <ReferenceChoices
              category="propAssetIds"
              choices={props}
              disabled={busy || conflicted || writeUnknown || discardConfirmationOpen}
              onToggle={toggleReference}
              selected={selectedReferences.propAssetIds}
              title="道具素材"
            />
          </>
        ) : (
          <div className="frame-structure-delete-warning" role="note">
            删除后会清理该镜头关联的聊天记录，并将后续镜头索引顺移。章节至少保留一个镜头。
          </div>
        )}
        {message !== null && (
          <p className={writeUnknown || conflicted ? "frame-structure-status" : "series-dialog-error"}
            role={writeUnknown || conflicted ? "status" : "alert"}>
            {message}
          </p>
        )}
        {discardConfirmationOpen && (
          <div aria-describedby="frame-structure-discard-description"
            aria-labelledby="frame-structure-discard-title"
            className="frame-structure-discard-confirmation"
            role="alertdialog">
            <h3 id="frame-structure-discard-title">放弃分镜草稿？</h3>
            <p id="frame-structure-discard-description">
              {writeUnknown
                ? "请求结果仍未确认。离开后会重新读取章节与素材，但原请求仍可能迟到完成。"
                : "当前输入和素材选择尚未保存。离开后将丢弃这份草稿。"}
            </p>
            <div className="series-dialog-actions">
              <button
                className="secondary-button"
                onClick={() => settleDiscardConfirmation(false)}
                ref={continueEditingButtonRef}
                type="button"
              >继续编辑</button>
              <button
                className="primary-button"
                onClick={() => settleDiscardConfirmation(true)}
                type="button"
              >放弃草稿并离开</button>
            </div>
          </div>
        )}
        <div className="series-dialog-actions frame-structure-editor-actions">
          <button className="secondary-button" disabled={busy || discardConfirmationOpen} onClick={close} type="button">
            {writeUnknown || conflicted ? "结束本次核对并关闭" : "取消"}
          </button>
          {writeUnknown ? (
            <button className="primary-button" disabled={busy || discardConfirmationOpen} onClick={() => void verify()} type="button">
              {verifying ? "正在重新读取…" : "只核对服务器状态"}
            </button>
          ) : (
            <button
              className="primary-button"
              disabled={busy || conflicted || discardConfirmationOpen}
              onClick={() => void submit()}
              type="button"
            >
              {saving ? "正在核对并保存…" : insertion ? "确认插入" : "确认删除"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

interface ReferenceChoice {
  id: string;
  name?: string;
  title?: string;
}

function ReferenceChoices<T extends ReferenceChoice>({
  category,
  choices,
  disabled,
  onToggle,
  selected,
  title,
}: {
  category: ReferenceCategory;
  choices: T[];
  disabled: boolean;
  onToggle(category: ReferenceCategory, assetId: string): void;
  selected: Set<string>;
  title: string;
}) {
  return (
    <fieldset className="frame-reference-choices" disabled={disabled}>
      <legend>{title}</legend>
      {choices.length === 0 ? (
        <p className="frame-reference-empty">本剧集暂无可选素材</p>
      ) : (
        <div className="frame-reference-choice-list">
          {choices.map((choice) => {
            const inputId = `frame-${category}-${choice.id}`;
            return (
              <label className="frame-reference-choice" htmlFor={inputId} key={choice.id}>
                <input
                  checked={selected.has(choice.id)}
                  id={inputId}
                  onChange={() => onToggle(category, choice.id)}
                  type="checkbox"
                />
                <span>{choice.name || choice.title || "未命名素材"}</span>
              </label>
            );
          })}
        </div>
      )}
    </fieldset>
  );
}
