import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Chapter } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError } from "../../shared/api/services";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";

export type ChapterManagementAction = "create" | "rename" | "delete";

interface ChapterManagementDialogProps {
  action: ChapterManagementAction;
  chapter?: Chapter;
  onClose(): void;
  onConfirm(title: string, signal: AbortSignal): Promise<void>;
  onVerify?(signal: AbortSignal): Promise<{ message: string; canRetry: boolean; chapters: Chapter[] }>;
}

function messageForFailure(error: ApiError, action: ChapterManagementAction): string {
  const detail = error.detail ?? error.message;
  if (error.status === 401) {
    return "登录状态已失效，请重新登录。";
  }
  if (error.status === 403) {
    return "当前账号没有权限执行此操作。";
  }
  if (error.status !== null && error.status < 500) {
    return action === "delete"
      ? `服务器没有确认本次删除，不能据此判断章节已删除。${detail}`
      : `服务器拒绝了本次修改。${detail}`;
  }
  return `本次请求结果未确认。${detail}`;
}

export function ChapterManagementDialog({
  action,
  chapter,
  onClose,
  onConfirm,
  onVerify,
}: ChapterManagementDialogProps) {
  const [title, setTitle] = useState(action === "rename" ? chapter?.title ?? "" : "");
  const [confirmationChecked, setConfirmationChecked] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [verificationMessage, setVerificationMessage] = useState<string | null>(null);
  const [verificationChapters, setVerificationChapters] = useState<Chapter[] | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [verifiedForRetry, setVerifiedForRetry] = useState(false);
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  const [titleError, setTitleError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const savingRef = useRef(false);
  const draftTitleRef = useRef(title);
  const draftRevisionRef = useRef(0);
  const verifiedDraftRef = useRef<{ revision: number; title: string } | null>(null);
  const identityRef = useRef({ action, chapterId: chapter?.id ?? null, chapterTitle: chapter?.title ?? null });
  const focus = useSeriesDialogFocus({ active: true, canDismiss: !saving && !verifying, onDismiss: onClose });

  useEffect(() => {
    const nextIdentity = { action, chapterId: chapter?.id ?? null, chapterTitle: chapter?.title ?? null };
    const previous = identityRef.current;
    if (
      previous.action !== nextIdentity.action
      || previous.chapterId !== nextIdentity.chapterId
      || previous.chapterTitle !== nextIdentity.chapterTitle
    ) {
      controllerRef.current?.abort();
      controllerRef.current = null;
      savingRef.current = false;
      draftRevisionRef.current += 1;
      draftTitleRef.current = action === "rename" ? chapter?.title ?? "" : "";
      verifiedDraftRef.current = null;
      setTitle(draftTitleRef.current);
      setConfirmationChecked(false);
      setSaving(false);
      setVerifying(false);
      setErrorMessage(null);
      setVerificationMessage(null);
      setVerificationChapters(null);
      setNeedsVerification(false);
      setVerifiedForRetry(false);
      setRetryConfirmed(false);
      setTitleError(null);
    }
    identityRef.current = nextIdentity;
    return () => controllerRef.current?.abort();
  }, [action, chapter?.id, chapter?.title]);

  function requestClose(): void {
    controllerRef.current?.abort();
    controllerRef.current = null;
    focus.dismiss();
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (savingRef.current) {
      return;
    }
    if (action === "rename" && title === chapter?.title) {
      if (!needsVerification) {
        focus.markDismissed();
        onClose();
      } else {
        setErrorMessage("章节名称没有变化，不会再次提交。请先核对当前列表，或重新输入要设置的名称。");
      }
      return;
    }
    const normalizedTitle = title.trim();
    const verifiedDraft = verifiedDraftRef.current;
    if (needsVerification && (
      !verifiedForRetry
      || !retryConfirmed
      || verifiedDraft?.revision !== draftRevisionRef.current
      || verifiedDraft.title !== title
    )) {
      return;
    }
    if (action !== "delete") {
      if (normalizedTitle.length === 0) {
        setTitleError("请输入章节名称。");
        return;
      }
      if (normalizedTitle.length > 100) {
        setTitleError("章节名称不能超过 100 个字符。");
        return;
      }
    } else if (!confirmationChecked) {
      return;
    }

    const controller = new AbortController();
    controllerRef.current = controller;
    savingRef.current = true;
    setSaving(true);
    setErrorMessage(null);
    setVerificationMessage(null);
    setVerificationChapters(null);
    setNeedsVerification(false);
    setVerifiedForRetry(false);
    setRetryConfirmed(false);
    verifiedDraftRef.current = null;
    setTitleError(null);
    try {
      await onConfirm(action === "delete" ? "" : normalizedTitle, controller.signal);
      if (!controller.signal.aborted) {
        focus.markDismissed();
        onClose();
      }
    } catch (cause) {
      if (controller.signal.aborted) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.kind !== "aborted") {
        setErrorMessage(messageForFailure(error, action));
        const knownHttpRejection = error.kind === "http"
          && error.status !== null
          && error.status < 500;
        setNeedsVerification(!knownHttpRejection);
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        savingRef.current = false;
        if (!controller.signal.aborted) {
          setSaving(false);
        }
      }
    }
  }

  async function verify(): Promise<void> {
    if (onVerify === undefined || verifying || saving) {
      return;
    }
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    const verifiedDraft = { revision: draftRevisionRef.current, title };
    verifiedDraftRef.current = null;
    setVerifiedForRetry(false);
    setRetryConfirmed(false);
    setVerificationChapters(null);
    setVerifying(true);
    setVerificationMessage(null);
    try {
      const result = await onVerify(controller.signal);
      if (
        !controller.signal.aborted
        && controllerRef.current === controller
        && draftRevisionRef.current === verifiedDraft.revision
        && draftTitleRef.current === verifiedDraft.title
      ) {
        verifiedDraftRef.current = result.canRetry ? verifiedDraft : null;
        setVerifiedForRetry(result.canRetry);
        setRetryConfirmed(false);
        setVerificationChapters(result.chapters);
        setVerificationMessage(result.canRetry
          ? `${result.message} 如仍需再次提交，请勾选确认；系统不会自动重发操作。`
          : result.message);
      }
    } catch (cause) {
      if (!controller.signal.aborted && controllerRef.current === controller) {
        const error = normalizeApiError(cause);
        setVerificationMessage(`暂时无法核实：${error.detail ?? error.message}`);
      }
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null;
        setVerifying(false);
      }
    }
  }

  const heading = action === "create" ? "新建章节" : action === "rename" ? "修改章节名称" : "删除章节";
  const canSubmit = (action !== "delete" || confirmationChecked)
    && (!needsVerification || (verifiedForRetry && retryConfirmed));
  const verificationDirectory = verificationChapters === null ? null : (
    <section
      aria-label="最新读取的章节目录"
      className="chapter-management-directory"
      role="region"
      tabIndex={0}
    >
      <h3>刚读取的章节</h3>
      {verificationChapters.length === 0 ? (
        <p>最新列表暂无章节。</p>
      ) : (
        <ul>
          {verificationChapters.map((entry) => (
            <li key={entry.id}>
              <strong>{entry.title}</strong>
              <time dateTime={entry.created_at}>创建于 {entry.created_at}</time>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  return (
    <div className="series-dialog-backdrop" role="presentation">
      <section
        aria-labelledby="chapter-management-title"
        aria-modal="true"
        className="series-dialog chapter-management-dialog"
        ref={focus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">章节管理</p>
            <h2 id="chapter-management-title">{heading}</h2>
          </div>
          <button
            aria-label="关闭章节管理"
            className="icon-button"
            disabled={saving || verifying}
            onClick={requestClose}
            type="button"
          >×</button>
        </div>
        {action === "delete" ? (
          <form onSubmit={(event) => void submit(event)}>
            <p className="chapter-delete-copy">
              确认删除《<strong>{chapter?.title}</strong>》？此操作无法撤销，章节中的分镜、聊天等相关数据也会一并删除。
            </p>
            <label className="chapter-delete-confirmation">
              <input
                checked={confirmationChecked}
                disabled={saving}
                onChange={(event) => setConfirmationChecked(event.currentTarget.checked)}
                type="checkbox"
              />
              我已核对章节名称，并确认删除。
            </label>
            {errorMessage !== null && <p className="series-dialog-error" role="alert">{errorMessage}</p>}
            {verificationMessage !== null && <p className="chapter-management-status" role="status">{verificationMessage}</p>}
            {verificationDirectory}
            {needsVerification && onVerify !== undefined && (
              <button className="secondary-button" disabled={verifying} onClick={() => void verify()} type="button">
                {verifying ? "正在重新读取…" : "重新读取章节列表核对"}
              </button>
            )}
            {needsVerification && verifiedForRetry && (
              <label className="chapter-delete-confirmation">
                <input
                  checked={retryConfirmed}
                  disabled={saving || verifying}
                  onChange={(event) => setRetryConfirmed(event.currentTarget.checked)}
                  type="checkbox"
                />
                我已重新核对列表，并确认仍要再次提交删除。
              </label>
            )}
            <div className="series-dialog-actions">
              <button className="secondary-button" disabled={saving || verifying} onClick={requestClose} type="button">取消</button>
              <button className="danger-button" disabled={!canSubmit || saving || verifying} type="submit">
                {saving ? "正在删除…" : "确认删除章节"}
              </button>
            </div>
          </form>
        ) : (
          <form className="series-editor-form" onSubmit={(event) => void submit(event)}>
            <label>
              章节名称
              <input
                autoComplete="off"
                disabled={saving}
                maxLength={100}
                onChange={(event) => {
                  if (savingRef.current) {
                    return;
                  }
                  const nextTitle = event.currentTarget.value;
                  draftTitleRef.current = nextTitle;
                  draftRevisionRef.current += 1;
                  verifiedDraftRef.current = null;
                  controllerRef.current?.abort();
                  controllerRef.current = null;
                  setVerifying(false);
                  setTitle(nextTitle);
                  setTitleError(null);
                  setVerificationMessage(null);
                  setVerificationChapters(null);
                  if (needsVerification) {
                    setVerifiedForRetry(false);
                    setRetryConfirmed(false);
                    setVerificationMessage(null);
                  }
                }}
                value={title}
              />
            </label>
            <small>名称最多 100 个字符。</small>
            {titleError !== null && <p className="series-dialog-error" role="alert">{titleError}</p>}
            {errorMessage !== null && <p className="series-dialog-error" role="alert">{errorMessage}</p>}
            {verificationMessage !== null && <p className="chapter-management-status" role="status">{verificationMessage}</p>}
            {verificationDirectory}
            {needsVerification && onVerify !== undefined && (
              <button className="secondary-button" disabled={verifying} onClick={() => void verify()} type="button">
                {verifying ? "正在重新读取…" : "重新读取章节列表核对"}
              </button>
            )}
            {needsVerification && verifiedForRetry && (
              <label className="chapter-delete-confirmation">
                <input
                  checked={retryConfirmed}
                  disabled={saving || verifying}
                  onChange={(event) => setRetryConfirmed(event.currentTarget.checked)}
                  type="checkbox"
                />
                我已重新核对列表，并确认仍要再次提交此操作。
              </label>
            )}
            <div className="series-dialog-actions">
              <button className="secondary-button" disabled={saving || verifying} onClick={requestClose} type="button">取消</button>
              <button className="primary-button" disabled={saving || verifying || !canSubmit} type="submit">
                {saving ? "正在保存…" : action === "create" ? "创建章节" : "保存名称"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
