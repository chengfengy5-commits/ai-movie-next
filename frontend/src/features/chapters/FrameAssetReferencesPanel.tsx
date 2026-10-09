import { useEffect, useRef, useState } from "react";
import type {
  Chapter,
  StoryboardFrame,
} from "../../shared/api/contracts";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import {
  hasValidFrameAssetReference,
  projectFrameAssetDirectory,
  type FrameAssetDirectory,
  type FrameAssetReferenceCategory,
  type FrameAssetReferencesProjection,
} from "./frameAssetReferences";
import { resolveFrameAssetReferenceNavigationId } from "./frameAssetReferenceNavigation";

export interface FrameAssetReferenceOwner {
  userId: string;
  seriesId: string;
  services: WorkspaceServices;
  chapter: Chapter;
  frame: StoryboardFrame;
  position: number;
  openEpoch: number;
}

export type FrameAssetReferenceScope = Omit<FrameAssetReferenceOwner, "openEpoch">;

export interface FrameAssetReferenceAssetTarget {
  seriesId: string;
  category: FrameAssetReferenceCategory;
  assetId: string;
  isCurrent(): boolean;
}

export interface FrameAssetReferencesPanelProps {
  owner: FrameAssetReferenceOwner;
  currentScope: FrameAssetReferenceScope;
  onClose(owner: FrameAssetReferenceOwner): void;
  onNavigateToAsset?(target: FrameAssetReferenceAssetTarget): void;
  onUnauthorized(): void;
}

type ReadState =
  | { category: FrameAssetReferenceCategory; status: "local" }
  | { category: FrameAssetReferenceCategory; status: "loading" }
  | { category: FrameAssetReferenceCategory; status: "ready"; directory: FrameAssetDirectory }
  | { category: FrameAssetReferenceCategory; status: "error"; error: ApiError };

const assetCategories: Array<{ id: FrameAssetReferenceCategory; label: string }> = [
  { id: "characters", label: "角色" },
  { id: "scenes", label: "场景" },
  { id: "props", label: "道具" },
];

function sameScope(owner: FrameAssetReferenceOwner, scope: FrameAssetReferenceScope): boolean {
  return owner.userId === scope.userId
    && owner.seriesId === scope.seriesId
    && owner.services === scope.services
    && owner.chapter === scope.chapter
    && owner.frame === scope.frame
    && owner.position === scope.position;
}

function categoryLabel(category: FrameAssetReferenceCategory): string {
  switch (category) {
    case "characters":
      return "角色";
    case "scenes":
      return "场景";
    case "props":
      return "道具";
  }
}

function readDirectory(
  services: WorkspaceServices,
  category: FrameAssetReferenceCategory,
  seriesId: string,
  signal: AbortSignal,
): Promise<FrameAssetDirectory> {
  switch (category) {
    case "characters":
      return services.listCharacters(seriesId, signal).then((items) => ({ category, items }));
    case "scenes":
      return services.listScenes(seriesId, signal).then((items) => ({ category, items }));
    case "props":
      return services.listProps(seriesId, signal).then((items) => ({ category, items }));
  }
}

function errorMessage(error: ApiError): { title: string; message: string } {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const membership = ["会员", "membership", "premium", "subscription"].some((word) => detail.includes(word));
    return membership
      ? { title: "当前账号暂不可查看素材", message: "当前账号的访问资格暂不可用。" }
      : { title: "没有访问权限", message: "当前账号无权查看这类素材。" };
  }
  if (error.status === 404) {
    return { title: "素材暂不可用", message: "剧集或素材可能已被移除，请重试读取。" };
  }
  if (error.status === 422) {
    return { title: "请求未通过校验", message: "本地服务无法识别当前请求，请稍后重试。" };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "暂时无法读取素材", message: "本地服务暂时不可用，请重试。" };
  }
  if (error.kind === "timeout") {
    return { title: "请求超时", message: "本地服务响应较慢，请重试。" };
  }
  if (error.kind === "network") {
    return { title: "无法连接本地服务", message: "请确认本地服务正在运行，然后重试。" };
  }
  if (error.kind === "invalid-response") {
    return { title: "服务返回的数据无法识别", message: "返回内容无法用于显示素材详情，请重试。" };
  }
  return { title: "暂时无法读取素材", message: "读取失败，请稍后重试。" };
}

function ReferenceDetails({
  projection,
  createNavigationTarget,
  onNavigateToAsset,
}: {
  projection: FrameAssetReferencesProjection;
  createNavigationTarget?(rowIndex: number): FrameAssetReferenceAssetTarget | null;
  onNavigateToAsset?(target: FrameAssetReferenceAssetTarget): void;
}) {
  if (projection.status !== "references") {
    return null;
  }
  const resolved = projection.rows.filter((row) => row.status === "resolved");
  const unresolved = projection.rows.filter((row) => row.status === "unmatched");
  const invalid = projection.rows.filter((row) => row.status === "invalid");

  if (resolved.length === 0 && unresolved.length === 0 && invalid.length === 0) {
    return null;
  }

  return (
    <ol className="frame-asset-reference-rows" aria-label="关联素材详情">
      {projection.rows.map((row, rowIndex) => {
        if (row.status === "invalid") {
          return (
            <li className="frame-asset-reference-row is-invalid" key={row.key}>
              <strong>引用不可识别</strong>
              <span>此项无法核对，其他引用仍可单独查看。</span>
            </li>
          );
        }
        if (row.status === "unmatched") {
          return (
            <li className="frame-asset-reference-row is-unmatched" key={row.key}>
              <strong>无法对应当前素材</strong>
              <span>该项可能已移除或不属于当前剧集。</span>
            </li>
          );
        }
        const target = createNavigationTarget?.(rowIndex) ?? null;
        return (
          <li className="frame-asset-reference-row" key={row.key}>
            <strong>{row.name}</strong>
            {row.aliases.length > 0 && <p><span>别名</span> {row.aliases.join("、")}</p>}
            {row.traits.map((trait) => (
              <p key={trait.label}><span>{trait.label}</span> {trait.value}</p>
            ))}
            <p className="frame-asset-reference-description">{row.description ?? "暂无描述"}</p>
            {target !== null && onNavigateToAsset !== undefined && (
              <button
                className="text-button frame-asset-reference-navigation"
                onClick={() => {
                  if (target.isCurrent()) {
                    onNavigateToAsset(target);
                  }
                }}
                type="button"
              >
                查看素材：{row.name}
              </button>
            )}
            {target === null && onNavigateToAsset !== undefined && (
              <p className="frame-asset-reference-navigation-note" role="note">
                素材身份暂无法核对，不能定位到素材。
              </p>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function FrameAssetReferencesPanel({
  owner,
  currentScope,
  onClose,
  onNavigateToAsset,
  onUnauthorized,
}: FrameAssetReferencesPanelProps) {
  const boundOwner = useRef(owner).current;
  const currentScopeRef = useRef(currentScope);
  currentScopeRef.current = currentScope;
  const [scopeInvalidated, setScopeInvalidated] = useState(false);
  const scopeInvalidatedRef = useRef(false);
  if (
    !scopeInvalidatedRef.current
    && (owner !== boundOwner || !sameScope(boundOwner, currentScopeRef.current))
  ) {
    scopeInvalidatedRef.current = true;
    setScopeInvalidated(true);
  }

  const [selectedCategory, setSelectedCategory] = useState<FrameAssetReferenceCategory | null>(null);
  const selectedCategoryRef = useRef<FrameAssetReferenceCategory | null>(null);
  const [readState, setReadState] = useState<ReadState | null>(null);
  const readStateRef = useRef(readState);
  readStateRef.current = readState;
  const readGeneration = useRef(0);
  const activeController = useRef<AbortController | null>(null);
  const cache = useRef(new Map<FrameAssetReferenceCategory, FrameAssetDirectory>());
  const unauthorizedCallback = useRef(onUnauthorized);
  unauthorizedCallback.current = onUnauthorized;

  function invalidateRead() {
    readGeneration.current += 1;
    activeController.current?.abort();
    activeController.current = null;
  }

  useEffect(() => () => {
    invalidateRead();
  }, []);

  function loadCategory(category: FrameAssetReferenceCategory, force: boolean) {
    if (scopeInvalidatedRef.current || selectedCategoryRef.current !== category) {
      return;
    }
    if (!hasValidFrameAssetReference(boundOwner.frame, category)) {
      invalidateRead();
      setReadState({ category, status: "local" });
      return;
    }

    const cached = cache.current.get(category);
    if (!force && cached?.category === category) {
      invalidateRead();
      setReadState({ category, status: "ready", directory: cached });
      return;
    }

    cache.current.delete(category);
    invalidateRead();
    const generation = ++readGeneration.current;
    const controller = new AbortController();
    activeController.current = controller;
    setReadState({ category, status: "loading" });

    const isCurrent = () => !scopeInvalidatedRef.current
      && !controller.signal.aborted
      && activeController.current === controller
      && readGeneration.current === generation
      && selectedCategoryRef.current === category
      && sameScope(boundOwner, currentScopeRef.current);

    void readDirectory(boundOwner.services, category, boundOwner.seriesId, controller.signal).then((items) => {
      if (!isCurrent()) {
        return;
      }
      if (items.category !== category) {
        setReadState({
          category,
          status: "error",
          error: new ApiError("invalid-response", "素材分类响应格式无效。"),
        });
        return;
      }
      cache.current.set(category, items);
      setReadState({ category, status: "ready", directory: items });
    }).catch((cause: unknown) => {
      if (!isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        unauthorizedCallback.current();
        return;
      }
      setReadState({ category, status: "error", error });
    });
  }

  function selectCategory(category: FrameAssetReferenceCategory) {
    if (scopeInvalidatedRef.current || selectedCategoryRef.current === category) {
      return;
    }
    selectedCategoryRef.current = category;
    setSelectedCategory(category);
    loadCategory(category, false);
  }

  function retryCurrentCategory() {
    const category = selectedCategoryRef.current;
    if (category !== null) {
      loadCategory(category, true);
    }
  }

  function close() {
    if (scopeInvalidatedRef.current) {
      return;
    }
    scopeInvalidatedRef.current = true;
    invalidateRead();
    setScopeInvalidated(true);
    onClose(boundOwner);
  }

  if (scopeInvalidated || scopeInvalidatedRef.current || !sameScope(boundOwner, currentScopeRef.current)) {
    return null;
  }

  const currentReadState = readState?.category === selectedCategory ? readState : null;
  const baseProjection = selectedCategory === null
    ? null
    : projectFrameAssetDirectory(boundOwner.frame, selectedCategory, boundOwner.seriesId, null);
  const readyProjection = selectedCategory === null || currentReadState?.status !== "ready"
    ? null
    : projectFrameAssetDirectory(
        boundOwner.frame,
        selectedCategory,
        boundOwner.seriesId,
        currentReadState.directory,
      );
  const invalidRows = baseProjection !== null && baseProjection.status === "references"
    ? baseProjection.rows.filter((row) => row.status === "invalid")
    : [];
  const localWithoutLookup = baseProjection !== null
    && (baseProjection.status !== "references" || !baseProjection.hasValidReferences);
  const presentation = currentReadState?.status === "error" ? errorMessage(currentReadState.error) : null;

  function createNavigationTarget(rowIndex: number): FrameAssetReferenceAssetTarget | null {
    if (
      onNavigateToAsset === undefined
      || selectedCategory === null
      || currentReadState?.status !== "ready"
      || readyProjection?.status !== "references"
    ) {
      return null;
    }

    const category = selectedCategory;
    const readyState = currentReadState;
    const directory = readyState.directory;
    const generation = readGeneration.current;
    const assetId = resolveFrameAssetReferenceNavigationId(
      boundOwner.frame,
      category,
      boundOwner.seriesId,
      directory,
      readyProjection,
      rowIndex,
    );
    if (assetId === null) {
      return null;
    }

    return {
      seriesId: boundOwner.seriesId,
      category,
      assetId,
      isCurrent: () => !scopeInvalidatedRef.current
        && sameScope(boundOwner, currentScopeRef.current)
        && selectedCategoryRef.current === category
        && readStateRef.current === readyState
        && readGeneration.current === generation
        && resolveFrameAssetReferenceNavigationId(
          boundOwner.frame,
          category,
          boundOwner.seriesId,
          directory,
          readyProjection,
          rowIndex,
        ) === assetId,
    };
  }

  return (
    <section
      aria-label={"镜头 " + boundOwner.position + " 的关联素材"}
      className="frame-asset-references-panel"
    >
      <header className="frame-asset-references-heading">
        <div>
          <p className="eyebrow">只读查看 · 镜头 {boundOwner.position}</p>
          <h3>关联素材</h3>
          <p>查看本镜头关联的角色、场景和道具。</p>
        </div>
        <button className="text-button" onClick={close} type="button">关闭</button>
      </header>

      <div className="frame-asset-reference-categories" role="group" aria-label="关联素材分类">
        {assetCategories.map((category) => (
          <button
            aria-pressed={selectedCategory === category.id}
            className={selectedCategory === category.id ? "frame-asset-reference-tab active" : "frame-asset-reference-tab"}
            key={category.id}
            onClick={() => selectCategory(category.id)}
            type="button"
          >
            {category.label}
          </button>
        ))}
        {selectedCategory !== null && !localWithoutLookup && (
          <button className="secondary-button frame-asset-reference-refresh" onClick={retryCurrentCategory} type="button">
            重新读取{categoryLabel(selectedCategory)}
          </button>
        )}
      </div>

      {selectedCategory === null && (
        <p className="frame-asset-reference-state" role="status">请选择素材分类。</p>
      )}

      {baseProjection?.status === "unlinked" && (
        <p className="frame-asset-reference-state" role="status">本镜头未关联{categoryLabel(selectedCategory!)}。</p>
      )}

      {baseProjection?.status === "unreadable" && (
        <p className="frame-asset-reference-state is-warning" role="status">本镜头的{categoryLabel(selectedCategory!)}引用暂时无法识别。</p>
      )}

      {currentReadState?.status === "loading" && (
        <p className="frame-asset-reference-state" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />正在读取{categoryLabel(selectedCategory!)}…
        </p>
      )}

      {presentation !== null && (
        <div className="state-panel error-panel request frame-asset-reference-error" role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h4>{presentation.title}</h4>
            <p>{presentation.message}</p>
          </div>
          <button className="secondary-button" onClick={retryCurrentCategory} type="button">重试读取</button>
        </div>
      )}

      {readyProjection?.status === "references" && readyProjection.rows.length === 0 && (
        <p className="frame-asset-reference-state" role="status">没有可显示的关联素材。</p>
      )}

      {readyProjection !== null && (
        <ReferenceDetails
          {...(onNavigateToAsset === undefined
            ? {}
            : { createNavigationTarget, onNavigateToAsset })}
          projection={readyProjection}
        />
      )}
      {currentReadState?.status === "loading" && invalidRows.length > 0 && (
        <ReferenceDetails projection={{ status: "references", rows: invalidRows, hasValidReferences: false }} />
      )}
      {currentReadState?.status === "error" && invalidRows.length > 0 && (
        <ReferenceDetails projection={{ status: "references", rows: invalidRows, hasValidReferences: false }} />
      )}
      {localWithoutLookup && baseProjection !== null && (
        <ReferenceDetails projection={baseProjection} />
      )}
    </section>
  );
}
