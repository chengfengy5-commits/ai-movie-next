import { useCallback, useEffect, useRef, useState } from "react";
import type { FusedAsset, Series } from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import "./fusedAssets.css";

interface FusedAssetsPageProps {
  readonly services: WorkspaceServices;
  readonly series: Series;
  readonly userId: string;
  readonly sessionGeneration: number;
  onBack(): void;
  onUnauthorized(): void;
  onActivityChange?(active: boolean): void;
}

interface PageScope {
  readonly services: WorkspaceServices;
  readonly seriesId: string;
  readonly userId: string;
  readonly sessionGeneration: number;
}

interface ActiveWrite {
  readonly scope: PageScope;
  readonly assetId: string;
  readonly controller: AbortController;
  stopped: boolean;
}

interface MutationFeedback {
  readonly scope: PageScope;
  readonly assetId: string;
  readonly kind: "saving" | "success" | "failure" | "unknown";
  readonly message: string;
}

function sameScope(left: PageScope, right: PageScope): boolean {
  return left.services === right.services
    && left.seriesId === right.seriesId
    && left.userId === right.userId
    && left.sessionGeneration === right.sessionGeneration;
}

function displayName(asset: FusedAsset): string {
  return asset.name.trim() || "未命名融合素材";
}

function fusedAssetImageUrl(imageUrl: string | null, apiBaseUrl: string): string | null {
  if (imageUrl === null || imageUrl === "" || imageUrl !== imageUrl.trim() || /[\u0000-\u0020\\]/u.test(imageUrl)) {
    return null;
  }
  try {
    const api = new URL(apiBaseUrl);
    const image = new URL(imageUrl, api);
    if (
      image.username !== ""
      || image.password !== ""
      || (image.protocol !== "http:" && image.protocol !== "https:")
    ) return null;

    if (image.origin === api.origin) return image.href;
    return image.protocol === "https:" && /^https:\/\//iu.test(imageUrl) ? image.href : null;
  } catch {
    return null;
  }
}

function memberNames(members: unknown[] | null): string[] {
  if (members === null) return [];
  return members.flatMap((member) => {
    if (member === null || typeof member !== "object" || Array.isArray(member)) return [];
    const name = (member as Record<string, unknown>).name;
    return typeof name === "string" && name.trim() !== "" ? [name.trim()] : [];
  });
}

function statusLabel(status: string): string {
  switch (status.toLowerCase()) {
    case "completed":
    case "succeeded":
    case "success":
      return "已完成";
    case "failed":
    case "error":
      return "生成失败";
    case "queued":
    case "pending":
      return "排队中";
    case "processing":
    case "running":
      return "生成中";
    case "unknown":
      return "结果未知";
    default:
      return "状态待确认";
  }
}

function statusClass(status: string): string {
  switch (status.toLowerCase()) {
    case "completed":
    case "succeeded":
    case "success":
      return "completed";
    case "failed":
    case "error":
      return "failed";
    case "queued":
    case "pending":
      return "pending";
    case "processing":
    case "running":
      return "processing";
    default:
      return "unknown";
  }
}

function isUnknownWriteOutcome(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  if (error.kind === "network" || error.kind === "timeout" || error.kind === "invalid-response") {
    return true;
  }
  return error.kind === "http" && (error.status === 408 || (error.status !== null && error.status >= 500));
}

function plainErrorMessage(error: unknown): string {
  const normalized = normalizeApiError(error);
  return normalized.detail ?? normalized.message;
}

function deleteErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "删除未完成：本剧集的相关章节无法安全处理，或内容已发生变化。请刷新列表后核对。";
  }
  return plainErrorMessage(error);
}

export function FusedAssetsPage({
  services,
  series,
  userId,
  sessionGeneration,
  onBack,
  onUnauthorized,
  onActivityChange,
}: FusedAssetsPageProps) {
  const [assets, setAssets] = useState<FusedAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadedScope, setLoadedScope] = useState<PageScope | null>(null);
  const [editingAssetId, setEditingAssetId] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [feedback, setFeedback] = useState<MutationFeedback | null>(null);
  const [failedImages, setFailedImages] = useState<ReadonlySet<string>>(() => new Set());
  const scopeRef = useRef<PageScope>({
    services,
    seriesId: series.id,
    userId,
    sessionGeneration,
  });
  scopeRef.current = { services, seriesId: series.id, userId, sessionGeneration };
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;
  const activityChangeRef = useRef(onActivityChange);
  activityChangeRef.current = onActivityChange;
  const mountedRef = useRef(false);
  const controllersRef = useRef(new Set<AbortController>());
  const readControllerRef = useRef<AbortController | null>(null);
  const readGenerationRef = useRef(0);
  const activeWriteRef = useRef<ActiveWrite | null>(null);
  const loadErrorScopeRef = useRef<PageScope | null>(null);

  const isCurrentScope = useCallback((scope: PageScope, signal?: AbortSignal): boolean => (
    mountedRef.current
    && signal?.aborted !== true
    && sameScope(scopeRef.current, scope)
  ), []);

  const readDirectory = useCallback(async (
    scope: PageScope,
    controller: AbortController,
  ): Promise<FusedAsset[] | null> => {
    if (scope.services.listFusedAssets === undefined) {
      if (isCurrentScope(scope, controller.signal)) {
        setLoadedScope(scope);
        setLoading(false);
        loadErrorScopeRef.current = scope;
        setLoadError("当前服务没有接入融合素材列表。");
      }
      return null;
    }

    const generation = ++readGenerationRef.current;
    if (readControllerRef.current !== null && readControllerRef.current !== controller) {
      readControllerRef.current.abort();
    }
    readControllerRef.current = controller;
    setLoading(true);
    setLoadError(null);
    try {
      const result = await scope.services.listFusedAssets(scope.seriesId, controller.signal);
      if (!isCurrentScope(scope, controller.signal) || generation !== readGenerationRef.current) {
        return null;
      }
      setAssets(result);
      setLoadedScope(scope);
      setFailedImages(new Set());
      setLoadError(null);
      loadErrorScopeRef.current = null;
      return result;
    } catch (error) {
      if (!isCurrentScope(scope, controller.signal) || generation !== readGenerationRef.current) {
        return null;
      }
      if (error instanceof ApiError && error.status === 401) {
        setLoadedScope(scope);
        unauthorizedRef.current();
      } else if (!(error instanceof ApiError && error.kind === "aborted")) {
        setLoadedScope(scope);
        loadErrorScopeRef.current = scope;
        setLoadError(plainErrorMessage(error));
      }
      return null;
    } finally {
      if (isCurrentScope(scope) && generation === readGenerationRef.current) {
        setLoading(false);
        if (readControllerRef.current === controller) readControllerRef.current = null;
      }
    }
  }, [isCurrentScope]);

  useEffect(() => {
    mountedRef.current = true;
    const scope = scopeRef.current;
    setEditingAssetId(null);
    setDraftName("");
    setFeedback(null);
    setLoadError(null);
    loadErrorScopeRef.current = null;
    setLoading(true);
    const controller = new AbortController();
    controllersRef.current.add(controller);
    void readDirectory(scope, controller).finally(() => controllersRef.current.delete(controller));
    return () => {
      mountedRef.current = false;
      if (activeWriteRef.current !== null) {
        activeWriteRef.current.stopped = true;
        activityChangeRef.current?.(false);
      }
      for (const activeController of controllersRef.current) activeController.abort();
      controllersRef.current.clear();
      readControllerRef.current = null;
      activeWriteRef.current = null;
    };
  }, [readDirectory, services, series.id, userId, sessionGeneration]);

  function startController(): AbortController {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    return controller;
  }

  function finishController(controller: AbortController): void {
    controllersRef.current.delete(controller);
    if (readControllerRef.current === controller) readControllerRef.current = null;
  }

  function handleImageError(asset: FusedAsset): void {
    const key = asset.id + ":" + (asset.image_url ?? "");
    setFailedImages((current) => new Set(current).add(key));
  }

  async function refreshDirectory(): Promise<void> {
    const scope = scopeRef.current;
    const controller = startController();
    try {
      await readDirectory(scope, controller);
    } finally {
      finishController(controller);
    }
  }

  function beginRename(asset: FusedAsset): void {
    if (activeWriteRef.current !== null || services.updateFusedAsset === undefined) return;
    setEditingAssetId(asset.id);
    setDraftName(asset.name);
    setFeedback(null);
  }

  function stopLocalTracking(): void {
    const activeWrite = activeWriteRef.current;
    if (activeWrite === null || activeWrite.stopped) return;
    activeWrite.stopped = true;
    activeWrite.controller.abort();
    activityChangeRef.current?.(false);
    setFeedback({
      scope: activeWrite.scope,
      assetId: activeWrite.assetId,
      kind: "unknown",
      message: "已停止本地跟踪；服务器操作仍可能完成。请刷新目录只读核对，系统不会自动重试。",
    });
  }

  async function saveRename(asset: FusedAsset): Promise<void> {
    const update = services.updateFusedAsset;
    const name = draftName.trim();
    if (update === undefined || activeWriteRef.current !== null) return;
    if (name === "") {
      setFeedback({ scope: scopeRef.current, assetId: asset.id, kind: "failure", message: "名称不能为空。" });
      return;
    }

    const scope = scopeRef.current;
    const controller = startController();
    const activeWrite: ActiveWrite = { scope, assetId: asset.id, controller, stopped: false };
    activeWriteRef.current = activeWrite;
    activityChangeRef.current?.(true);
    setFeedback({ scope, assetId: asset.id, kind: "saving", message: "正在保存名称…" });

    try {
      const updated = await update(scope.seriesId, asset.id, { name }, controller.signal);
      if (!isCurrentScope(scope, controller.signal) || activeWrite.stopped) return;
      setAssets((current) => current.map((item) => item.id === asset.id ? updated : item));
      setEditingAssetId(null);
      setFeedback({ scope, assetId: asset.id, kind: "success", message: "名称已保存，正在回读目录…" });
      const reread = await readDirectory(scope, controller);
      if (!isCurrentScope(scope, controller.signal) || activeWrite.stopped) return;
      setFeedback({
        scope,
        assetId: asset.id,
        kind: "success",
        message: reread === null
          ? "名称已保存；目录回读失败，请手动刷新核对。"
          : "名称已保存，并已回读当前剧集目录。",
      });
    } catch (error) {
      if (activeWrite.stopped || !isCurrentScope(scope, controller.signal)) return;
      if (error instanceof ApiError && error.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!isUnknownWriteOutcome(error)) {
        setFeedback({ scope, assetId: asset.id, kind: "failure", message: plainErrorMessage(error) });
        return;
      }

      setEditingAssetId(null);
      setFeedback({
        scope,
        assetId: asset.id,
        kind: "unknown",
        message: "改名结果未知，正在只读核对当前剧集目录；系统不会自动重试。",
      });
      const reread = await readDirectory(scope, controller);
      if (!isCurrentScope(scope, controller.signal) || activeWrite.stopped) return;
      setFeedback({
        scope,
        assetId: asset.id,
        kind: "unknown",
        message: reread === null
          ? "改名结果未知，目录核对失败。请稍后手动刷新；系统不会自动重试。"
          : "改名结果未知，已只读刷新当前剧集目录。请按当前名称核对；系统不会自动重试。",
      });
    } finally {
      finishController(controller);
      if (activeWriteRef.current === activeWrite) {
        activeWriteRef.current = null;
        if (isCurrentScope(scope)) activityChangeRef.current?.(false);
      }
    }
  }

  async function deleteAsset(asset: FusedAsset): Promise<void> {
    const remove = services.deleteFusedAsset;
    if (remove === undefined || activeWriteRef.current !== null) return;
    if (!window.confirm(
      `删除“${displayName(asset)}”？系统会同时清理本剧集中对此融合素材的引用；如果相关章节无法安全读取或内容已变化，删除会被拒绝。此操作无法撤销。`,
    )) return;

    const scope = scopeRef.current;
    const controller = startController();
    const activeWrite: ActiveWrite = { scope, assetId: asset.id, controller, stopped: false };
    activeWriteRef.current = activeWrite;
    activityChangeRef.current?.(true);
    setFeedback({ scope, assetId: asset.id, kind: "saving", message: "正在删除，并核对当前剧集目录…" });

    try {
      await remove(asset.id, controller.signal);
      if (!isCurrentScope(scope, controller.signal) || activeWrite.stopped) return;
      const reread = await readDirectory(scope, controller);
      if (!isCurrentScope(scope, controller.signal) || activeWrite.stopped) return;
      const removedFromDirectory = reread !== null && !reread.some((item) => item.id === asset.id);
      setFeedback({
        scope,
        assetId: asset.id,
        kind: removedFromDirectory ? "success" : "unknown",
        message: removedFromDirectory
          ? "融合素材已删除，并已从当前剧集目录确认移除。"
          : "服务器已接受删除请求，但当前目录未能确认结果。请刷新列表核对；系统不会自动重复删除。",
      });
    } catch (error) {
      if (activeWrite.stopped || !isCurrentScope(scope, controller.signal)) return;
      if (error instanceof ApiError && error.status === 401) {
        unauthorizedRef.current();
        return;
      }
      if (!isUnknownWriteOutcome(error)) {
        setFeedback({ scope, assetId: asset.id, kind: "failure", message: deleteErrorMessage(error) });
        return;
      }

      setFeedback({
        scope,
        assetId: asset.id,
        kind: "unknown",
        message: "删除结果未知，正在只读核对当前剧集目录；系统不会自动重复删除。",
      });
      const reread = await readDirectory(scope, controller);
      if (!isCurrentScope(scope, controller.signal) || activeWrite.stopped) return;
      const stillListed = reread?.some((item) => item.id === asset.id) ?? null;
      setFeedback({
        scope,
        assetId: asset.id,
        kind: "unknown",
        message: stillListed === null
          ? "删除结果未知，目录核对失败。请手动刷新当前剧集列表；系统不会自动重复删除。"
          : stillListed
            ? "删除结果未知，只读刷新后目录仍显示此素材。请核对后再决定下一步；系统不会自动重复删除。"
            : "删除结果未知，只读刷新后当前目录已不显示此素材。请核对；系统不会自动重复删除。",
      });
    } finally {
      finishController(controller);
      if (activeWriteRef.current === activeWrite) {
        activeWriteRef.current = null;
        if (isCurrentScope(scope)) activityChangeRef.current?.(false);
      }
    }
  }

  const isWriting = activeWriteRef.current !== null;
  const visibleAssets = loadedScope !== null && sameScope(loadedScope, scopeRef.current) ? assets : [];
  const visibleLoadError = loadErrorScopeRef.current !== null
    && sameScope(loadErrorScopeRef.current, scopeRef.current)
    ? loadError
    : null;
  const visibleLoading = loading
    || (loadedScope !== null && !sameScope(loadedScope, scopeRef.current))
    || (loadedScope === null && visibleLoadError === null);
  const visibleFeedback = feedback !== null && sameScope(feedback.scope, scopeRef.current) ? feedback : null;
  const directoryFeedback = visibleFeedback !== null
    && !visibleAssets.some((asset) => asset.id === visibleFeedback.assetId)
    ? visibleFeedback
    : null;

  return (
    <section className="fused-assets-page" aria-label={series.name + " · 融合素材"}>
      <header className="fused-assets-heading">
        <div>
          <p className="eyebrow">剧集素材</p>
          <h1>{series.name} · 融合素材</h1>
          <p>查看本剧集已保存的融合素材与生成状态，并管理素材名称和删除。这里不会发起生成或费用请求。</p>
        </div>
        <div className="fused-assets-actions">
          {isWriting && (
            <button className="text-button" onClick={stopLocalTracking} type="button">
              停止本地跟踪
            </button>
          )}
          <button className="secondary-button" onClick={onBack} type="button">
            返回剧集
          </button>
          <button
            className="primary-button"
            disabled={loading || isWriting}
            onClick={() => void refreshDirectory()}
            type="button"
          >
            {loading ? "正在刷新…" : "刷新列表"}
          </button>
        </div>
      </header>

      {visibleLoadError !== null && <p className="fused-assets-alert" role="alert">{visibleLoadError}</p>}
      {directoryFeedback !== null && (
        <p className={"fused-asset-feedback fused-asset-feedback-" + directoryFeedback.kind} role="status">
          {directoryFeedback.message}
        </p>
      )}
      {visibleLoading && visibleAssets.length === 0 && <p className="fused-assets-state" role="status">正在加载融合素材…</p>}
      {!visibleLoading && visibleLoadError === null && visibleAssets.length === 0 && (
        <p className="fused-assets-state">当前剧集还没有融合素材。</p>
      )}

      {visibleAssets.length > 0 && (
        <div className="fused-assets-grid" aria-busy={visibleLoading}>
          {visibleAssets.map((asset) => {
            const name = displayName(asset);
            const safeImage = services.apiBaseUrl === null
              ? null
              : fusedAssetImageUrl(asset.image_url, services.apiBaseUrl);
            const imageKey = asset.id + ":" + (asset.image_url ?? "");
            const names = memberNames(asset.members);
            const feedbackForAsset = visibleFeedback?.assetId === asset.id ? visibleFeedback : null;
            const isEditing = editingAssetId === asset.id;

            return (
              <article className="fused-asset-card" key={asset.id} aria-label={name + "融合素材"}>
                <div className="fused-asset-image">
                  {safeImage !== null && !failedImages.has(imageKey)
                    ? <img
                        alt={name + "图片"}
                        loading="lazy"
                        onError={() => handleImageError(asset)}
                        referrerPolicy="no-referrer"
                        src={safeImage}
                      />
                    : <span role="img" aria-label={name + "没有可显示的图片"}>暂无图片</span>}
                </div>
                <div className="fused-asset-card-content">
                  <div className="fused-asset-title-row">
                    <h2>{name}</h2>
                    <span className={"fused-asset-status fused-asset-status-" + statusClass(asset.status)}>
                      {statusLabel(asset.status)}
                    </span>
                  </div>
                  <dl className="fused-asset-details">
                    <div>
                      <dt>类型</dt>
                      <dd>{asset.member_type === "character" ? "角色融合" : asset.member_type === "prop" ? "道具融合" : "融合素材"}</dd>
                    </div>
                    <div>
                      <dt>尺寸</dt>
                      <dd>{asset.size?.trim() || "未记录"}</dd>
                    </div>
                    <div>
                      <dt>成员</dt>
                      <dd>{names.length > 0 ? names.join("、") : "未记录成员名称"}</dd>
                    </div>
                  </dl>
                  {statusClass(asset.status) === "failed" && asset.error_message !== null && asset.error_message.trim() !== "" && (
                    <p className="fused-asset-error">失败原因：{asset.error_message}</p>
                  )}

                  {isEditing ? (
                    <form
                      className="fused-asset-rename-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void saveRename(asset);
                      }}
                    >
                      <label>
                        <span>素材名称</span>
                        <input
                          autoFocus
                          maxLength={120}
                          onChange={(event) => setDraftName(event.currentTarget.value)}
                          value={draftName}
                        />
                      </label>
                      <div className="fused-asset-actions">
                        <button className="primary-button" disabled={isWriting} type="submit">保存名称</button>
                        <button
                          className="secondary-button"
                          disabled={isWriting}
                          onClick={() => setEditingAssetId(null)}
                          type="button"
                        >
                          取消
                        </button>
                      </div>
                    </form>
                  ) : (
                    <div className="fused-asset-actions">
                      {services.updateFusedAsset !== undefined ? (
                        <button
                          className="secondary-button fused-asset-rename-button"
                          disabled={isWriting}
                          onClick={() => beginRename(asset)}
                          type="button"
                        >
                          改名
                        </button>
                      ) : <span className="fused-assets-note">当前服务未提供改名操作。</span>}
                      {services.deleteFusedAsset !== undefined ? (
                        <button
                          className="danger-button"
                          disabled={isWriting}
                          onClick={() => void deleteAsset(asset)}
                          type="button"
                        >
                          删除
                        </button>
                      ) : <span className="fused-assets-note">当前服务未提供删除操作。</span>}
                    </div>
                  )}

                  {feedbackForAsset !== null && (
                    <p className={"fused-asset-feedback fused-asset-feedback-" + feedbackForAsset.kind} role="status">
                      {feedbackForAsset.message}
                    </p>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
