import { useLayoutEffect, useRef, useState } from "react";
import type { Series } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import { useSeriesDialogFocus } from "./useSeriesDialogFocus";

interface SeriesDeleteDialogProps {
  series: Series;
  active: boolean;
  services: WorkspaceServices;
  userId: string;
  onClose(): void;
  onUnauthorized(): void;
  onDeleted(): void;
  onMissingConfirmed(): void;
}

interface RequestContext {
  active: boolean;
  services: WorkspaceServices;
  userId: string;
  seriesId: string;
}

function errorMessage(error: ApiError): string {
  return error.detail ?? error.message;
}

export function SeriesDeleteDialog({
  series,
  active,
  services,
  userId,
  onClose,
  onUnauthorized,
  onDeleted,
  onMissingConfirmed,
}: SeriesDeleteDialogProps) {
  const [confirmation, setConfirmation] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [verifiedStillExists, setVerifiedStillExists] = useState(false);
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState<string | null>(null);

  const contextRef = useRef<RequestContext>({ active, services, userId, seriesId: series.id });
  contextRef.current = { active, services, userId, seriesId: series.id };
  const callbacksRef = useRef({ onUnauthorized, onDeleted, onMissingConfirmed });
  callbacksRef.current = { onUnauthorized, onDeleted, onMissingConfirmed };
  const requestRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const deleteLockRef = useRef(false);
  const deleteControllerRef = useRef<AbortController | null>(null);
  const identityRef = useRef({ active, services, userId, seriesId: series.id });
  const dialogFocus = useSeriesDialogFocus({ active, canDismiss: !deleting, onDismiss: onClose });

  function cancelRequest(): void {
    generationRef.current += 1;
    requestRef.current?.abort();
    requestRef.current = null;
  }

  useLayoutEffect(() => {
    const previous = identityRef.current;
    const identityChanged = previous.active !== active
      || previous.services !== services
      || previous.userId !== userId
      || previous.seriesId !== series.id;
    identityRef.current = { active, services, userId, seriesId: series.id };

    if (identityChanged) {
      cancelRequest();
      deleteControllerRef.current = null;
      deleteLockRef.current = false;
      setConfirmation("");
      setDeleting(false);
      setVerifying(false);
      setError(null);
      setNeedsVerification(false);
      setVerifiedStillExists(false);
      setRetryConfirmed(false);
      setVerificationMessage(null);
    }
    if (!active) {
      cancelRequest();
    }
    return cancelRequest;
  }, [active, series.id, services, userId]);

  function startRequest(): { controller: AbortController; generation: number; context: RequestContext } {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    return { controller, generation: generationRef.current, context: contextRef.current };
  }

  function isCurrent(request: { controller: AbortController; generation: number; context: RequestContext }): boolean {
    const context = contextRef.current;
    return requestRef.current === request.controller
      && generationRef.current === request.generation
      && !request.controller.signal.aborted
      && context.active
      && context.userId === request.context.userId
      && context.services === request.context.services
      && context.seriesId === request.context.seriesId;
  }

  function unauthorized(request: { controller: AbortController; generation: number; context: RequestContext }): void {
    if (isCurrent(request)) {
      callbacksRef.current.onUnauthorized();
    }
  }

  async function remove(): Promise<void> {
    if (confirmation !== series.name || deleting || deleteLockRef.current || services.deleteSeries === undefined
      || (needsVerification && (!verifiedStillExists || !retryConfirmed))) {
      return;
    }
    setError(null);
    setVerificationMessage(null);
    setNeedsVerification(false);
    setVerifiedStillExists(false);
    setRetryConfirmed(false);
    deleteLockRef.current = true;
    setDeleting(true);
    const request = startRequest();
    deleteControllerRef.current = request.controller;
    try {
      await services.deleteSeries(series.id, request.controller.signal);
      if (isCurrent(request)) {
        dialogFocus.markDismissed();
        callbacksRef.current.onDeleted();
      }
    } catch (cause) {
      if (!isCurrent(request)) {
        return;
      }
      const next = normalizeApiError(cause);
      if (next.kind === "aborted") {
        return;
      }
      if (next.status === 401) {
        unauthorized(request);
        return;
      }
      if (next.status === 403) {
        setNeedsVerification(false);
        setError(next.detail ?? "当前账号没有删除权限；剧集仍保留在列表中。");
        return;
      }
      if (next.status === 404) {
        setError("服务器未确认删除，剧集暂时保留在列表中。请重新读取详情核对。");
        setNeedsVerification(true);
        return;
      }
      if (next.kind !== "http" || next.status === null || next.status >= 500) {
        setError("删除结果暂时无法确认。剧集仍保留在列表中；请先重新读取核对，不会自动重试。");
        setNeedsVerification(true);
        setVerifiedStillExists(false);
        setRetryConfirmed(false);
        return;
      }
      setNeedsVerification(false);
      setError(errorMessage(next));
    } finally {
      const stillCurrent = isCurrent(request);
      if (requestRef.current === request.controller) {
        requestRef.current = null;
      }
      if (deleteControllerRef.current === request.controller) {
        deleteControllerRef.current = null;
        deleteLockRef.current = false;
        if (stillCurrent) {
          setDeleting(false);
        }
      }
    }
  }

  async function verify(): Promise<void> {
    if (services.getSeries === undefined || verifying) {
      setVerificationMessage("当前服务暂不支持重新读取剧集详情。");
      return;
    }
    setVerificationMessage(null);
    setVerifiedStillExists(false);
    setRetryConfirmed(false);
    setVerifying(true);
    const request = startRequest();
    try {
      const result = await services.getSeries(series.id, request.controller.signal);
      if (isCurrent(request)) {
        if (result.id !== series.id) {
          setVerificationMessage("重新读取返回了其他剧集，不能据此确认当前删除状态。");
          return;
        }
        setVerifiedStillExists(true);
        setVerificationMessage("重新读取确认剧集仍存在。请核对后明确确认是否再次提交删除请求。");
      }
    } catch (cause) {
      if (!isCurrent(request)) {
        return;
      }
      const next = normalizeApiError(cause);
      if (next.kind === "aborted") {
        return;
      }
      if (next.status === 401) {
        unauthorized(request);
      } else if (next.status === 404) {
        dialogFocus.markDismissed();
        callbacksRef.current.onMissingConfirmed();
      } else if (next.status === 403) {
        setVerifiedStillExists(false);
        setVerificationMessage("当前账号没有权限核实剧集状态，不能据此判断剧集已删除。");
      } else {
        setVerifiedStillExists(false);
        setVerificationMessage(`暂时无法核实：${errorMessage(next)}`);
      }
    } finally {
      const stillCurrent = isCurrent(request);
      if (requestRef.current === request.controller) {
        requestRef.current = null;
      }
      if (stillCurrent) {
        setVerifying(false);
      }
    }
  }

  return (
    <div className="series-dialog-backdrop" role="presentation">
      <section
        aria-labelledby="series-delete-title"
        aria-modal="true"
        className="series-dialog series-delete-dialog"
        ref={dialogFocus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">删除剧集</p>
            <h2 id="series-delete-title">确认删除</h2>
          </div>
          <button
            aria-label="关闭删除确认"
            className="icon-button"
            disabled={deleting}
            onClick={dialogFocus.dismiss}
            type="button"
          >×</button>
        </div>
        <p className="series-delete-copy">
          将删除《<strong>{series.name}</strong>》及其章节、分镜和关联素材。此操作无法撤销。
        </p>
        <label className="series-delete-confirmation">
          输入剧集名称以确认
          <input
            autoComplete="off"
            disabled={deleting}
            onChange={(event) => {
              setConfirmation(event.currentTarget.value);
              setRetryConfirmed(false);
            }}
            value={confirmation}
          />
        </label>
        {error !== null && <p className="series-dialog-error" role="alert">{error}</p>}
        {needsVerification && (
          <div className="series-verification">
            <button className="secondary-button" disabled={verifying || deleting} onClick={() => void verify()} type="button">
              {verifying ? "正在核实…" : "重新读取详情核对"}
            </button>
            {verificationMessage !== null && <p role="status">{verificationMessage}</p>}
            {verifiedStillExists && (
              <label className="series-retry-confirmation">
                <input
                  checked={retryConfirmed}
                  onChange={(event) => setRetryConfirmed(event.currentTarget.checked)}
                  type="checkbox"
                />
                我已核对剧集仍存在，并确认再次提交删除请求。
              </label>
            )}
          </div>
        )}
        <div className="series-dialog-actions">
          <button className="secondary-button" disabled={deleting} onClick={dialogFocus.dismiss} type="button">取消</button>
          <button
            className="danger-button"
            disabled={confirmation !== series.name || deleting || services.deleteSeries === undefined
              || (needsVerification && (!verifiedStillExists || !retryConfirmed))}
            onClick={() => void remove()}
            type="button"
          >
            {deleting ? "正在删除…" : needsVerification ? "确认再次删除" : "删除剧集"}
          </button>
        </div>
      </section>
    </div>
  );
}
