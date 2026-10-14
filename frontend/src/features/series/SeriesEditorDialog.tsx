import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { Series } from "../../shared/api/contracts";
import { isSafeCoverUrl } from "../../shared/api/config";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import type { CreateSeriesInput, StylePromptChoice, UpdateSeriesInput } from "../../shared/api/seriesManagement";
import { useSeriesDialogFocus } from "./useSeriesDialogFocus";

const MAX_COVER_BYTES = 10 * 1024 * 1024;
const COVER_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"]);

type EditorMode = "create" | "edit";

interface SeriesDraft {
  name: string;
  description: string;
  imageUrl: string;
  stylePromptId: string;
}

interface SeriesEditorDialogProps {
  mode: EditorMode;
  series?: Series;
  active: boolean;
  services: WorkspaceServices;
  userId: string;
  onClose(): void;
  onUnauthorized(): void;
  onSaved(series: Series, mode: EditorMode): void;
}

interface RequestContext {
  active: boolean;
  services: WorkspaceServices;
  userId: string;
  mode: EditorMode;
  seriesId: string | null;
}

function draftFromSeries(series: Series): SeriesDraft {
  return {
    name: series.name,
    description: series.description ?? "",
    imageUrl: series.image_url ?? "",
    stylePromptId: series.style_prompt_id ?? "",
  };
}

function errorMessage(error: ApiError): string {
  return error.detail ?? error.message;
}

function isUncertain(error: ApiError): boolean {
  return error.kind !== "http" || error.status === null || error.status >= 500;
}

function labelForPrompt(choice: StylePromptChoice): string {
  return choice.is_system ? `${choice.name} · 系统` : choice.name;
}

export function SeriesEditorDialog({
  mode,
  series,
  active,
  services,
  userId,
  onClose,
  onUnauthorized,
  onSaved,
}: SeriesEditorDialogProps) {
  const [initialSeries, setInitialSeries] = useState<Series | null>(null);
  const [draft, setDraft] = useState<SeriesDraft>({ name: "", description: "", imageUrl: "", stylePromptId: "" });
  const [styleChoices, setStyleChoices] = useState<StylePromptChoice[]>([]);
  const [detailLoading, setDetailLoading] = useState(mode === "edit");
  const [styleLoading, setStyleLoading] = useState(true);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [styleError, setStyleError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [verificationComplete, setVerificationComplete] = useState(false);
  const [retryConfirmed, setRetryConfirmed] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState<string | null>(null);
  const [remoteReadback, setRemoteReadback] = useState<Series | null>(null);
  const [verifiedListCount, setVerifiedListCount] = useState<number | null>(null);
  const [verificationRows, setVerificationRows] = useState<Series[]>([]);

  const seriesId = series?.id ?? null;
  const contextRef = useRef<RequestContext>({ active, services, userId, mode, seriesId });
  contextRef.current = { active, services, userId, mode, seriesId };
  const callbacksRef = useRef({ onUnauthorized, onSaved });
  callbacksRef.current = { onUnauthorized, onSaved };
  const generationRef = useRef(0);
  const controllersRef = useRef(new Set<AbortController>());
  const uploadControllerRef = useRef<AbortController | null>(null);
  const verificationControllerRef = useRef<AbortController | null>(null);
  const saveControllerRef = useRef<AbortController | null>(null);
  const saveLockRef = useRef(false);
  const draftRevisionRef = useRef(0);
  const identityRef = useRef({ active, services, userId, mode, seriesId });
  const dialogFocus = useSeriesDialogFocus({
    active,
    canDismiss: !saving && !uploading,
    onDismiss: onClose,
  });

  function changeDraft(update: Partial<SeriesDraft>): void {
    if (saveLockRef.current) {
      return;
    }
    draftRevisionRef.current += 1;
    setDraft((previous) => ({ ...previous, ...update }));
    const verificationController = verificationControllerRef.current;
    if (verificationController !== null) {
      verificationController.abort();
      controllersRef.current.delete(verificationController);
      verificationControllerRef.current = null;
      setVerifying(false);
    }
    if (uncertain) {
      setVerificationComplete(false);
      setRetryConfirmed(false);
      setVerificationRows([]);
      setRemoteReadback(null);
      setVerifiedListCount(null);
      setVerificationMessage("草稿已变化，请重新读取并核对后再确认重试。");
    }
  }

  function abortPending(): void {
    generationRef.current += 1;
    for (const controller of controllersRef.current) {
      controller.abort();
    }
    controllersRef.current.clear();
    verificationControllerRef.current = null;
    saveControllerRef.current = null;
    uploadControllerRef.current = null;
  }

  useLayoutEffect(() => {
    const previous = identityRef.current;
    const identityChanged = previous.active !== active
      || previous.services !== services
      || previous.userId !== userId
      || previous.mode !== mode
      || previous.seriesId !== seriesId;
    identityRef.current = { active, services, userId, mode, seriesId };

    if (identityChanged) {
      abortPending();
      saveLockRef.current = false;
      draftRevisionRef.current += 1;
      setInitialSeries(null);
      setDraft({ name: "", description: "", imageUrl: "", stylePromptId: "" });
      setStyleChoices([]);
      setDetailLoading(active && mode === "edit");
      setStyleLoading(active);
      setDetailError(null);
      setStyleError(null);
      setFormError(null);
      setSaving(false);
      setVerifying(false);
      setUploading(false);
      setUncertain(false);
      setVerificationComplete(false);
      setRetryConfirmed(false);
      setVerificationMessage(null);
      setRemoteReadback(null);
      setVerifiedListCount(null);
      setVerificationRows([]);
    }
    if (!active) {
      abortPending();
    }
    return () => abortPending();
  }, [active, mode, seriesId, services, userId]);

  function createController(): { controller: AbortController; generation: number; context: RequestContext } {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    return { controller, generation: generationRef.current, context: contextRef.current };
  }

  function isCurrent(
    request: { controller: AbortController; generation: number; context: RequestContext },
  ): boolean {
    const current = contextRef.current;
    return generationRef.current === request.generation
      && !request.controller.signal.aborted
      && current.active
      && current.userId === request.context.userId
      && current.services === request.context.services
      && current.mode === request.context.mode
      && current.seriesId === request.context.seriesId;
  }

  function handleUnauthorized(request: { controller: AbortController; generation: number; context: RequestContext }): void {
    if (isCurrent(request)) {
      callbacksRef.current.onUnauthorized();
    }
  }

  useEffect(() => {
    if (!active) {
      return;
    }
    let current = true;
    const controllerRequest = createController();
    const request = controllerRequest;

    if (mode === "edit") {
      if (series === undefined || services.getSeries === undefined) {
        setDetailError("当前服务暂不支持读取剧集详情。");
        setDetailLoading(false);
      } else {
        setDetailLoading(true);
        setDetailError(null);
        void services.getSeries(series.id, request.controller.signal).then((result) => {
          if (!current || !isCurrent(request)) {
            return;
          }
          if (result.id !== series.id) {
            setDetailError("服务器返回的剧集与当前选择不一致。");
            return;
          }
          setInitialSeries(result);
          setDraft(draftFromSeries(result));
        }).catch((cause: unknown) => {
          if (!current || !isCurrent(request)) {
            return;
          }
          const error = normalizeApiError(cause);
          if (error.kind === "aborted") {
            return;
          }
          if (error.status === 401) {
            handleUnauthorized(request);
            return;
          }
          setDetailError(errorMessage(error));
        }).finally(() => {
          if (current && isCurrent(request)) {
            setDetailLoading(false);
          }
        });
      }
    }

    if (services.listSeriesStylePrompts === undefined) {
      setStyleError("当前服务暂不支持读取风格提示词。");
      setStyleLoading(false);
    } else {
      setStyleLoading(true);
      setStyleError(null);
      void services.listSeriesStylePrompts(request.controller.signal).then((choices) => {
        if (!current || !isCurrent(request)) {
          return;
        }
        setStyleChoices(choices);
      }).catch((cause: unknown) => {
        if (!current || !isCurrent(request)) {
          return;
        }
        const error = normalizeApiError(cause);
        if (error.kind === "aborted") {
          return;
        }
        if (error.status === 401) {
          handleUnauthorized(request);
          return;
        }
        setStyleError(errorMessage(error));
      }).finally(() => {
        if (current && isCurrent(request)) {
          setStyleLoading(false);
        }
      });
    }

    return () => {
      current = false;
      request.controller.abort();
      controllersRef.current.delete(request.controller);
    };
  }, [active, mode, series?.id, services, userId]);

  const currentStyleLabel = initialSeries?.style_prompt_name
    ? `${initialSeries.style_prompt_name}${initialSeries.style_prompt_owner_name ? ` · ${initialSeries.style_prompt_owner_name}` : ""}`
    : "当前风格提示词";
  const choices = useMemo(() => {
    if (
      initialSeries?.style_prompt_id
      && !styleChoices.some((choice) => choice.id === initialSeries.style_prompt_id)
    ) {
      return [
        ...styleChoices,
        {
          id: initialSeries.style_prompt_id,
          name: currentStyleLabel,
          is_system: false,
          user_id: null,
        },
      ];
    }
    return styleChoices;
  }, [currentStyleLabel, initialSeries, styleChoices]);

  const coverPreview = services.apiBaseUrl === null
    ? null
    : isSafeCoverUrl(draft.imageUrl, services.apiBaseUrl);

  async function handleCoverFile(file: File | undefined): Promise<void> {
    setFormError(null);
    if (file === undefined) {
      return;
    }
    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!COVER_EXTENSIONS.has(extension)) {
      setFormError("封面仅支持 JPG、PNG、GIF、WebP 或 BMP 图片。");
      return;
    }
    if (file.size > MAX_COVER_BYTES) {
      setFormError("封面文件不能超过 10 MiB。");
      return;
    }
    if (services.mode !== "api" || services.uploadSeriesCover === undefined) {
      setFormError("演示模式暂不支持上传封面。");
      return;
    }
    uploadControllerRef.current?.abort();
    const request = createController();
    uploadControllerRef.current = request.controller;
    setUploading(true);
    try {
      const uploadedUrl = await services.uploadSeriesCover(file, request.controller.signal);
      if (!isCurrent(request)) {
        return;
      }
      const safeUrl = services.apiBaseUrl === null ? null : isSafeCoverUrl(uploadedUrl, services.apiBaseUrl);
      if (safeUrl === null) {
        throw new ApiError("invalid-response", "服务器返回的封面地址不在本机服务范围内。");
      }
      changeDraft({ imageUrl: safeUrl });
      setVerificationMessage(null);
    } catch (cause) {
      if (!isCurrent(request)) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.kind === "aborted") {
        return;
      }
      if (error.status === 401) {
        handleUnauthorized(request);
        return;
      }
      setFormError(errorMessage(error));
    } finally {
      controllersRef.current.delete(request.controller);
      if (uploadControllerRef.current === request.controller) {
        uploadControllerRef.current = null;
      }
      if (isCurrent(request)) {
        setUploading(false);
      }
    }
  }

  function clearCover(): void {
    uploadControllerRef.current?.abort();
    if (uploadControllerRef.current !== null) {
      controllersRef.current.delete(uploadControllerRef.current);
      uploadControllerRef.current = null;
    }
    setUploading(false);
    changeDraft({ imageUrl: "" });
    setFormError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (saving || saveLockRef.current || uploading || detailLoading || detailError !== null
      || (uncertain && (!verificationComplete || !retryConfirmed))) {
      return;
    }
    setFormError(null);
    setVerificationMessage(null);

    const nameDirty = mode === "create" || initialSeries === null || draft.name !== initialSeries.name;
    const descriptionDirty = mode === "create"
      || initialSeries === null
      || draft.description !== (initialSeries.description ?? "");
    const name = nameDirty ? draft.name.trim() : initialSeries?.name ?? draft.name.trim();
    const description = descriptionDirty
      ? draft.description.trim()
      : initialSeries?.description ?? draft.description.trim();
    if (nameDirty && name === "") {
      setFormError("请填写剧集名称。");
      return;
    }
    if (nameDirty && name.length > 20) {
      setFormError("剧集名称不能超过 20 个字符。");
      return;
    }
    if (descriptionDirty && description.length > 500) {
      setFormError("剧集简介不能超过 500 个字符。");
      return;
    }
    const originalImageUrl = initialSeries?.image_url ?? "";
    if (
      draft.imageUrl !== ""
      && coverPreview === null
      && (mode === "create" || draft.imageUrl !== originalImageUrl)
    ) {
      setFormError("封面地址不在本机服务范围内，请重新上传或清空封面。");
      return;
    }

    setRemoteReadback(null);
    setVerifiedListCount(null);
    setVerificationRows([]);
    if (uncertain) {
      setUncertain(false);
      setVerificationComplete(false);
      setRetryConfirmed(false);
    }

    const request = createController();
    saveControllerRef.current = request.controller;
    saveLockRef.current = true;
    setSaving(true);
    try {
      let saved: Series;
      if (mode === "create") {
        if (services.createSeries === undefined) {
          throw new ApiError("invalid-response", "当前服务暂不支持创建剧集。");
        }
        const input: CreateSeriesInput = {
          name,
          description,
          image_url: draft.imageUrl === "" ? null : draft.imageUrl,
          style_prompt_id: draft.stylePromptId === "" ? null : draft.stylePromptId,
        };
        saved = await services.createSeries(input, request.controller.signal);
      } else {
        if (series === undefined || initialSeries === null || services.updateSeries === undefined) {
          throw new ApiError("invalid-response", "当前服务暂不支持编辑剧集。");
        }
        const update: UpdateSeriesInput = {};
        if (nameDirty && name !== initialSeries.name) {
          update.name = name;
        }
        if (descriptionDirty && description !== (initialSeries.description ?? "")) {
          update.description = description;
        }
        if (draft.imageUrl !== (initialSeries.image_url ?? "")) {
          update.image_url = draft.imageUrl;
        }
        if (draft.stylePromptId !== (initialSeries.style_prompt_id ?? "")) {
          update.style_prompt_id = draft.stylePromptId;
        }
        if (Object.keys(update).length === 0) {
          setFormError("没有需要保存的修改。");
          return;
        }
        saved = await services.updateSeries(series.id, update, request.controller.signal);
      }
      if (!isCurrent(request)) {
        return;
      }
      dialogFocus.markDismissed();
      callbacksRef.current.onSaved(saved, mode);
    } catch (cause) {
      if (!isCurrent(request)) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.kind === "aborted") {
        return;
      }
      if (error.status === 401) {
        handleUnauthorized(request);
        return;
      }
      if (isUncertain(error)) {
        setUncertain(true);
        setVerificationComplete(false);
        setRetryConfirmed(false);
        setFormError("提交结果暂时无法确认。草稿已保留；请先重新读取核对，不会自动重试。");
      } else {
        setUncertain(false);
        setVerificationComplete(false);
        setRetryConfirmed(false);
        setFormError(errorMessage(error));
      }
    } finally {
      controllersRef.current.delete(request.controller);
      if (saveControllerRef.current === request.controller) {
        saveControllerRef.current = null;
        saveLockRef.current = false;
        if (isCurrent(request)) {
          setSaving(false);
        }
      }
    }
  }

  async function verifyResult(): Promise<void> {
    if (saving || verifying || services.getSeries === undefined && mode === "edit") {
      return;
    }
    setVerificationMessage(null);
    setVerificationComplete(false);
    setRetryConfirmed(false);
    setRemoteReadback(null);
    setVerifiedListCount(null);
    setVerificationRows([]);
    const previousVerification = verificationControllerRef.current;
    if (previousVerification !== null) {
      previousVerification.abort();
      controllersRef.current.delete(previousVerification);
    }
    const revision = draftRevisionRef.current;
    const request = createController();
    verificationControllerRef.current = request.controller;
    setVerifying(true);
    const isCurrentVerification = (): boolean => verificationControllerRef.current === request.controller
      && draftRevisionRef.current === revision
      && isCurrent(request);
    try {
      if (mode === "create") {
        const rows = await services.listSeries(request.controller.signal);
        if (!isCurrentVerification()) {
          return;
        }
        setVerifiedListCount(rows.length);
        setVerificationRows(rows);
        setVerificationComplete(true);
        setVerificationMessage("已重新读取剧集列表。请检查下方内容；系统不会按同名内容判断创建成功，草稿仍保留。");
        return;
      }
      if (series === undefined || services.getSeries === undefined) {
        setVerificationMessage("当前服务暂不支持重新读取剧集详情。");
        return;
      }
      const result = await services.getSeries(series.id, request.controller.signal);
      if (!isCurrentVerification()) {
        return;
      }
      if (result.id !== series.id) {
        setVerificationComplete(false);
        setVerificationMessage("重新读取返回了其他剧集，不能据此确认本次保存结果。");
        return;
      }
      setRemoteReadback(result);
      setVerificationComplete(true);
      setVerificationMessage("已重新读取服务器详情。下方显示当前服务器数据，编辑草稿保持不变。");
    } catch (cause) {
      if (!isCurrentVerification()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.kind === "aborted") {
        return;
      }
      if (error.status === 401) {
        handleUnauthorized(request);
        return;
      }
      setVerificationComplete(false);
      if (mode === "edit" && error.status === 404) {
        setRemoteReadback(null);
        setVerificationMessage("重新读取确认：当前剧集不存在。原提交仍未标记为保存成功，草稿保留。");
        return;
      }
      setVerificationMessage(error.status === 403
        ? "当前账号没有权限核实服务器状态。"
        : `暂时无法核实：${errorMessage(error)}`);
    } finally {
      controllersRef.current.delete(request.controller);
      if (verificationControllerRef.current === request.controller) {
        verificationControllerRef.current = null;
        if (draftRevisionRef.current === revision && isCurrent(request)) {
          setVerifying(false);
        }
      }
    }
  }

  const canSubmit = mode === "create"
    ? services.createSeries !== undefined
    : services.updateSeries !== undefined && initialSeries !== null;

  return (
    <div className="series-dialog-backdrop" role="presentation">
      <section
        aria-labelledby="series-editor-title"
        aria-modal="true"
        className="series-dialog"
        ref={dialogFocus.dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <div className="series-dialog-heading">
          <div>
            <p className="eyebrow">剧集信息</p>
            <h2 id="series-editor-title">{mode === "create" ? "创建剧集" : "编辑剧集"}</h2>
          </div>
          <button
            aria-label="关闭剧集编辑"
            className="icon-button"
            disabled={saving || uploading}
            onClick={dialogFocus.dismiss}
            type="button"
          >×</button>
        </div>

        {detailLoading && (
          <p className="series-dialog-status" role="status">正在读取剧集详情…</p>
        )}
        {detailError !== null && (
          <div className="series-dialog-error" role="alert">
            <span>{detailError}</span>
            {series !== undefined && services.getSeries !== undefined && (
              <button
                className="secondary-button"
                onClick={() => {
                  setDetailError(null);
                  setDetailLoading(true);
                  const request = createController();
                  void services.getSeries!(series.id, request.controller.signal).then((result) => {
                    if (!isCurrent(request)) return;
                    if (result.id !== series.id) {
                      setDetailError("服务器返回的剧集与当前选择不一致。");
                      return;
                    }
                    setInitialSeries(result);
                    setDraft(draftFromSeries(result));
                  }).catch((cause: unknown) => {
                    if (!isCurrent(request)) return;
                    const error = normalizeApiError(cause);
                    if (error.status === 401) handleUnauthorized(request);
                    else if (error.kind !== "aborted") setDetailError(errorMessage(error));
                  }).finally(() => {
                    controllersRef.current.delete(request.controller);
                    if (isCurrent(request)) setDetailLoading(false);
                  });
                }}
                type="button"
              >重新读取详情</button>
            )}
          </div>
        )}

        {!detailLoading && detailError === null && (
          <form className="series-editor-form" onSubmit={(event) => void submit(event)}>
            <label>
              剧集名称
              <input
                autoComplete="off"
                disabled={saving}
                maxLength={20}
                onChange={(event) => changeDraft({ name: event.currentTarget.value })}
                required
                value={draft.name}
              />
            </label>
            <label>
              剧集简介
              <textarea
                disabled={saving}
                maxLength={500}
                onChange={(event) => changeDraft({ description: event.currentTarget.value })}
                rows={4}
                value={draft.description}
              />
            </label>

            <div className="series-editor-field">
              <label htmlFor="series-style-prompt">风格提示词</label>
              <select
                disabled={saving || styleLoading || styleError !== null}
                id="series-style-prompt"
                onChange={(event) => changeDraft({ stylePromptId: event.currentTarget.value })}
                value={draft.stylePromptId}
              >
                <option value="">不使用风格提示词</option>
                {choices.map((choice) => (
                  <option key={choice.id} value={choice.id}>{labelForPrompt(choice)}</option>
                ))}
              </select>
              {styleLoading && <small role="status">正在读取可用风格…</small>}
              {styleError !== null && (
                <small className="series-field-warning" role="status">
                  风格提示词暂不可读取；当前绑定会保留，其他字段仍可编辑。
                </small>
              )}
            </div>

            <div className="series-editor-field">
              <span className="series-field-label">封面图片</span>
              {coverPreview !== null && (
                <img className="series-cover-preview" alt="当前剧集封面预览" src={coverPreview} />
              )}
              {draft.imageUrl !== "" && coverPreview === null && (
                <small>当前封面无法在本机安全预览；未修改时会保留原地址。</small>
              )}
              {services.mode === "api" && services.uploadSeriesCover !== undefined ? (
                <input
                  accept=".jpg,.jpeg,.png,.gif,.webp,.bmp,image/jpeg,image/png,image/gif,image/webp,image/bmp"
                  aria-label="上传剧集封面"
                  disabled={uploading || saving}
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    event.currentTarget.value = "";
                    void handleCoverFile(file);
                  }}
                  type="file"
                />
              ) : (
                <small>演示模式暂不支持上传封面。</small>
              )}
              {uploading && <small role="status">正在上传封面…</small>}
              {draft.imageUrl !== "" && (
                <button className="secondary-button series-clear-cover" disabled={uploading || saving} onClick={clearCover} type="button">
                  清空封面
                </button>
              )}
            </div>

            {formError !== null && <p className="series-dialog-error" role="alert">{formError}</p>}
            {uncertain && (
              <div className="series-verification">
                <button className="secondary-button" disabled={saving || verifying} onClick={() => void verifyResult()} type="button">
                  {verifying ? "正在重新读取…" : "重新读取核对"}
                </button>
                {verifiedListCount !== null && <span>本次列表返回 {verifiedListCount} 部剧集。</span>}
                {verificationMessage !== null && <p role="status">{verificationMessage}</p>}
                {uncertain && verificationComplete && (
                  <label className="series-retry-confirmation">
                    <input
                      checked={retryConfirmed}
                      onChange={(event) => setRetryConfirmed(event.currentTarget.checked)}
                      type="checkbox"
                    />
                    {mode === "create"
                      ? "我已核对重新读取的剧集列表，仍要提交一次新的创建请求。"
                      : "我已核对服务器详情，仍要再次提交保存请求。"}
                  </label>
                )}
                {verifiedListCount !== null && verificationRows.length > 0 && (
                  <ul className="series-verification-list">
                    {verificationRows.map((row) => (
                      <li key={row.id}>{row.name} · {row.owner_name || "未知作者"}</li>
                    ))}
                  </ul>
                )}
                {remoteReadback !== null && (
                  <dl>
                    <div><dt>服务器剧名</dt><dd>{remoteReadback.name}</dd></div>
                    <div><dt>服务器简介</dt><dd>{remoteReadback.description || "暂无简介"}</dd></div>
                    <div><dt>服务器风格</dt><dd>{remoteReadback.style_prompt_name || remoteReadback.style_prompt_id || "未设置"}</dd></div>
                    <div><dt>服务器封面</dt><dd>{remoteReadback.image_url || "未设置"}</dd></div>
                  </dl>
                )}
              </div>
            )}

            <div className="series-dialog-actions">
              <button className="secondary-button" disabled={saving || uploading} onClick={dialogFocus.dismiss} type="button">取消</button>
              <button
                className="primary-button"
                disabled={!canSubmit || saving || verifying || uploading || detailLoading
                  || (uncertain && (!verificationComplete || !retryConfirmed))}
                type="submit"
              >
                {saving ? "正在保存…" : uncertain ? "确认重新提交" : "保存剧集"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}
