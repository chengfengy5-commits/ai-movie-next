import { useEffect, useLayoutEffect, useRef, useState, type ChangeEvent } from "react";
import { isSafeCoverUrl } from "../../shared/api/config";
import type {
  AssetLibraryType,
  Character,
  Prop,
  Scene,
  Series,
} from "../../shared/api/contracts";
import type { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import { searchAssetLibraryItems, type AssetLibraryItemsByCategory } from "./assetLibrarySearch";
import {
  AssetFrameUsagePanel,
  type AssetFrameUsageChapterTarget,
  type AssetFrameUsageFrameTarget,
  type AssetFrameUsageOwner,
} from "./AssetFrameUsagePanel";

type AssetRecord = Character | Scene | Prop;

type AssetByCategory = AssetLibraryItemsByCategory;

type AssetReadyResult =
  | { type: "characters"; status: "ready"; items: Character[] }
  | { type: "scenes"; status: "ready"; items: Scene[] }
  | { type: "props"; status: "ready"; items: Prop[] };

type AssetResult =
  | { type: AssetLibraryType; status: "loading" }
  | AssetReadyResult
  | { type: AssetLibraryType; status: "error"; error: ApiError };

export interface AssetNavigationIntent {
  userId: string;
  services: WorkspaceServices;
  seriesId: string;
  category: AssetLibraryType;
  assetId: string;
  navigationEpoch: number;
}

interface LibraryScope {
  userId: string;
  services: WorkspaceServices;
  seriesId: string;
  generation: number;
}

interface ScopedAssetResult {
  scope: LibraryScope;
  requestGeneration: number;
  navigationEpoch: number | null;
  result: AssetResult;
}

interface AssetNavigationFeedback {
  scope: LibraryScope;
  category: AssetLibraryType;
  navigationEpoch: number;
  assetId: string;
}

interface AssetSearchSelection {
  scope: LibraryScope;
  category: AssetLibraryType;
  result: ScopedAssetResult;
  requestGeneration: number;
  generation: number;
  query: string;
}

interface AssetSearchTicket {
  scope: LibraryScope;
  category: AssetLibraryType;
  result: ScopedAssetResult;
  requestGeneration: number;
  generation: number;
}

const assetTypes: Array<{ id: AssetLibraryType; label: string }> = [
  { id: "characters", label: "角色" },
  { id: "scenes", label: "场景" },
  { id: "props", label: "道具" },
];

function readAssets(
  services: WorkspaceServices,
  type: "characters",
  seriesId: string,
  signal: AbortSignal,
): Promise<Extract<AssetReadyResult, { type: "characters" }>>;
function readAssets(
  services: WorkspaceServices,
  type: "scenes",
  seriesId: string,
  signal: AbortSignal,
): Promise<Extract<AssetReadyResult, { type: "scenes" }>>;
function readAssets(
  services: WorkspaceServices,
  type: "props",
  seriesId: string,
  signal: AbortSignal,
): Promise<Extract<AssetReadyResult, { type: "props" }>>;
function readAssets(
  services: WorkspaceServices,
  type: AssetLibraryType,
  seriesId: string,
  signal: AbortSignal,
): Promise<AssetReadyResult>;
function readAssets(
  services: WorkspaceServices,
  type: AssetLibraryType,
  seriesId: string,
  signal: AbortSignal,
): Promise<AssetReadyResult> {
  switch (type) {
    case "characters":
      return services.listCharacters(seriesId, signal).then((items) => ({ type, status: "ready", items }));
    case "scenes":
      return services.listScenes(seriesId, signal).then((items) => ({ type, status: "ready", items }));
    case "props":
      return services.listProps(seriesId, signal).then((items) => ({ type, status: "ready", items }));
  }
}

function errorPresentation(error: ApiError): { title: string; message: string } {
  if (error.status === 403) {
    const detail = error.detail?.toLowerCase() ?? "";
    const isMembership = ["会员", "membership", "premium", "subscription"].some((word) => detail.includes(word));
    if (isMembership) {
      return {
        title: "当前账号暂不可查看素材",
        message: error.detail ?? "当前账号的访问资格暂不可用。",
      };
    }
    return { title: "没有访问权限", message: error.detail ?? "当前账号无权查看这些素材。" };
  }
  if (error.status === 404) {
    return { title: "素材暂不可用", message: error.detail ?? "剧集或素材可能已被移除。" };
  }
  if (error.status === 422) {
    return { title: "请求未通过校验", message: error.detail ?? "本地服务无法识别当前请求。" };
  }
  if (error.status !== null && error.status >= 500) {
    return { title: "暂时无法读取素材", message: "本地服务暂时不可用，请稍后重试。" };
  }
  if (error.kind === "timeout") {
    return { title: "请求超时", message: "本地服务响应较慢，请重试。" };
  }
  if (error.kind === "network") {
    return { title: "无法连接本地服务", message: "请确认本地服务正在运行，然后重试。" };
  }
  if (error.kind === "invalid-response") {
    return { title: "服务返回的数据无法识别", message: error.message };
  }
  return { title: "暂时无法读取素材", message: error.detail ?? error.message };
}

function assetName(type: AssetLibraryType, item: AssetRecord): string {
  const value = type === "scenes"
    ? ("title" in item && typeof item.title === "string" ? item.title : "")
    : ("name" in item && typeof item.name === "string" ? item.name : "");
  const fallback = type === "characters" ? "未命名角色" : type === "scenes" ? "未命名场景" : "未命名道具";
  return value.trim() === "" ? fallback : value;
}

function hasValidCategoryName(type: AssetLibraryType, item: AssetRecord): boolean {
  if (type === "scenes") {
    return "title" in item && typeof item.title === "string";
  }
  return "name" in item && typeof item.name === "string";
}

function hasUniqueUsageIdentity(
  items: AssetRecord[],
  item: AssetRecord,
  seriesId: string,
): boolean {
  return typeof item.id === "string"
    && item.id.trim() !== ""
    && item.series_id === seriesId
    && items.filter((candidate) => candidate.id === item.id).length === 1
    && items.includes(item);
}

function matchedAssetIndexes(ready: AssetReadyResult, query: string): ReadonlySet<number> {
  switch (ready.type) {
    case "characters":
      return new Set(searchAssetLibraryItems("characters", ready.items, query).map((entry) => entry.originalIndex));
    case "scenes":
      return new Set(searchAssetLibraryItems("scenes", ready.items, query).map((entry) => entry.originalIndex));
    case "props":
      return new Set(searchAssetLibraryItems("props", ready.items, query).map((entry) => entry.originalIndex));
  }
}

function assetSearchLabel(type: AssetLibraryType): string {
  switch (type) {
    case "characters":
      return "搜索角色素材";
    case "scenes":
      return "搜索场景素材";
    case "props":
      return "搜索道具素材";
  }
}

function AssetImage({
  item,
  name,
  apiBaseUrl,
  failed,
  onError,
}: {
  item: AssetRecord;
  name: string;
  apiBaseUrl: string | null;
  failed: boolean;
  onError(): void;
}) {
  const imageUrl = apiBaseUrl === null ? null : isSafeCoverUrl(item.image_url, apiBaseUrl);
  if (imageUrl === null || failed) {
    return (
      <div className="asset-image-placeholder" aria-label={name + "没有可显示的本地图片"} role="img">
        <span aria-hidden="true">◇</span>
        <small>暂无图片</small>
      </div>
    );
  }
  return <img alt={name + "图片"} loading="lazy" onError={onError} src={imageUrl} />;
}

function AssetCard({
  item,
  type,
  apiBaseUrl,
  failed,
  onImageError,
  onShowUsage,
  usageUnavailable,
  cardRef,
  highlighted,
  onCardBlur,
  hidden,
}: {
  item: AssetRecord;
  type: AssetLibraryType;
  apiBaseUrl: string | null;
  failed: boolean;
  onImageError(): void;
  onShowUsage?: (() => void) | undefined;
  usageUnavailable: boolean;
  cardRef?(element: HTMLElement | null): void;
  highlighted: boolean;
  onCardBlur?(): void;
  hidden: boolean;
}) {
  const name = assetName(type, item);
  const aliases = item.aliases?.filter((alias) => alias.trim() !== "") ?? [];
  const character = type === "characters" && "role" in item ? item : null;

  return (
    <article
      aria-label={name + "素材卡片"}
      className={highlighted ? "asset-card asset-card-navigated" : "asset-card"}
      hidden={hidden}
      inert={hidden}
      onBlur={onCardBlur}
      ref={cardRef}
      tabIndex={-1}
    >
      <div className="asset-image-wrap">
        <AssetImage
          apiBaseUrl={apiBaseUrl}
          failed={failed}
          item={item}
          name={name}
          onError={onImageError}
        />
      </div>
      <div className="asset-card-body">
        <h2>{name}</h2>
        {aliases.length > 0 && (
          <p className="asset-aliases">
            <span>别名</span> {aliases.join("、")}
          </p>
        )}
        {character !== null && (
          <dl className="character-details">
            {character.role !== null && <div><dt>身份</dt><dd>{character.role}</dd></div>}
            {(character.gender !== null || character.age !== null) && (
              <div>
                <dt>基本信息</dt>
                <dd>{[character.gender, character.age].filter((value) => value !== null && value !== "").join(" · ") || "未填写"}</dd>
              </div>
            )}
            {character.appearance !== null && <div><dt>外观</dt><dd>{character.appearance}</dd></div>}
          </dl>
        )}
        <p className="asset-description">{item.description?.trim() || "暂无描述"}</p>
        {onShowUsage !== undefined && (
          <button
            className="asset-usage-button"
            onClick={onShowUsage}
            type="button"
          >
            查看关联镜头：{name}
          </button>
        )}
        {usageUnavailable && (
          <p className="asset-usage-unavailable" role="note">
            素材身份暂无法核对，不能查看关联镜头。
          </p>
        )}
      </div>
    </article>
  );
}

export interface AssetLibraryProps {
  series: Series;
  services: WorkspaceServices;
  userId: string;
  onBack(): void;
  onUnauthorized(): void;
  onViewChapter?(target: AssetFrameUsageChapterTarget): void;
  onViewFrame?(target: AssetFrameUsageFrameTarget): void;
  navigationIntent?: AssetNavigationIntent | null;
  onNavigationConsumed?(navigationEpoch: number): void;
  onNavigationAbandoned?(navigationEpoch: number): void;
}

export function AssetLibrary({
  series,
  services,
  userId,
  onBack,
  onUnauthorized,
  onViewChapter,
  onViewFrame,
  navigationIntent = null,
  onNavigationConsumed,
  onNavigationAbandoned,
}: AssetLibraryProps) {
  const [selectedType, setSelectedType] = useState<AssetLibraryType>(
    () => navigationIntent?.category ?? "characters",
  );
  const [reloadVersion, setReloadVersion] = useState(0);
  const [result, setResult] = useState<ScopedAssetResult | null>(null);
  const resultRef = useRef(result);
  resultRef.current = result;
  const [failedImages, setFailedImages] = useState<Set<string>>(() => new Set());
  const [searchSelection, setSearchSelection] = useState<AssetSearchSelection | null>(null);
  const searchSelectionRef = useRef(searchSelection);
  searchSelectionRef.current = searchSelection;
  const searchGenerationRef = useRef(0);
  const [usageOwner, setUsageOwner] = useState<AssetFrameUsageOwner | null>(null);
  const [navigationIssue, setNavigationIssue] = useState<{ epoch: number; message: string } | null>(null);
  const [navigationFeedback, setNavigationFeedback] = useState<AssetNavigationFeedback | null>(null);
  const cache = useRef(new Map<AssetLibraryType, AssetReadyResult>());
  const libraryScopeRef = useRef<LibraryScope>({ userId, services, seriesId: series.id, generation: 0 });
  const directoryRequestGenerationRef = useRef(0);
  const openEpochRef = useRef(0);
  const usageOwnerRef = useRef<AssetFrameUsageOwner | null>(null);
  const navigationIntentRef = useRef<AssetNavigationIntent | null>(navigationIntent);
  const observedNavigationEpochRef = useRef<number | null>(navigationIntent?.navigationEpoch ?? null);
  const navigationIntentScopesRef = useRef(new Map<number, LibraryScope>());
  const invalidatedNavigationEpochsRef = useRef(new Set<number>());
  const navigationConsumedEpochsRef = useRef(new Set<number>());
  const navigationAbandonedEpochsRef = useRef(new Set<number>());
  const assetCardElementsRef = useRef(new Map<string, HTMLElement>());
  const libraryElementRef = useRef<HTMLElement | null>(null);
  const navigationConsumedHandlerRef = useRef(onNavigationConsumed);
  const navigationAbandonedHandlerRef = useRef(onNavigationAbandoned);
  const selectedTypeRef = useRef(selectedType);
  const unauthorizedHandlerRef = useRef(onUnauthorized);
  selectedTypeRef.current = selectedType;
  navigationConsumedHandlerRef.current = onNavigationConsumed;
  navigationAbandonedHandlerRef.current = onNavigationAbandoned;
  unauthorizedHandlerRef.current = onUnauthorized;

  const previousScope = libraryScopeRef.current;
  if (previousScope.userId !== userId || previousScope.services !== services || previousScope.seriesId !== series.id) {
    libraryScopeRef.current = { userId, services, seriesId: series.id, generation: previousScope.generation + 1 };
    directoryRequestGenerationRef.current += 1;
    searchGenerationRef.current += 1;
    searchSelectionRef.current = null;
    cache.current = new Map();
    usageOwnerRef.current = null;
    openEpochRef.current += 1;
  }
  const libraryScope = libraryScopeRef.current;
  const incomingIntentMatchesScope = navigationIntent !== null
    && navigationIntent.userId === userId
    && navigationIntent.services === services
    && navigationIntent.seriesId === series.id;
  let effectiveNavigationIntent: AssetNavigationIntent | null = null;
  if (navigationIntent !== null) {
    const boundScope = navigationIntentScopesRef.current.get(navigationIntent.navigationEpoch);
    if (boundScope !== undefined && boundScope !== libraryScope) {
      invalidatedNavigationEpochsRef.current.add(navigationIntent.navigationEpoch);
    }
    if (
      incomingIntentMatchesScope
      && !invalidatedNavigationEpochsRef.current.has(navigationIntent.navigationEpoch)
      && !navigationConsumedEpochsRef.current.has(navigationIntent.navigationEpoch)
      && !navigationAbandonedEpochsRef.current.has(navigationIntent.navigationEpoch)
    ) {
      if (boundScope === undefined) {
        navigationIntentScopesRef.current.set(navigationIntent.navigationEpoch, libraryScope);
      }
      if (navigationIntentScopesRef.current.get(navigationIntent.navigationEpoch) === libraryScope) {
        effectiveNavigationIntent = navigationIntent;
      }
    }
  }
  navigationIntentRef.current = effectiveNavigationIntent;

  if (effectiveNavigationIntent === null) {
    observedNavigationEpochRef.current = null;
  } else if (observedNavigationEpochRef.current !== effectiveNavigationIntent.navigationEpoch) {
    observedNavigationEpochRef.current = effectiveNavigationIntent.navigationEpoch;
    directoryRequestGenerationRef.current += 1;
    searchGenerationRef.current += 1;
    searchSelectionRef.current = null;
    setSearchSelection(null);
    usageOwnerRef.current = null;
    openEpochRef.current += 1;
    setUsageOwner(null);
    setNavigationIssue(null);
    setNavigationFeedback(null);
    setSelectedType(effectiveNavigationIntent.category);
  }

  useEffect(() => {
    const requestGeneration = ++directoryRequestGenerationRef.current;
    const intent = effectiveNavigationIntent;
    const forcedFreshRead = intent !== null
      && !navigationConsumedEpochsRef.current.has(intent.navigationEpoch)
      && !navigationAbandonedEpochsRef.current.has(intent.navigationEpoch)
      && intent.userId === userId
      && intent.services === services
      && intent.seriesId === series.id
      && intent.category === selectedType;
    if (forcedFreshRead) {
      cache.current.delete(selectedType);
    }
    const requestNavigationEpoch = forcedFreshRead ? intent.navigationEpoch : null;
    const cached = forcedFreshRead ? undefined : cache.current.get(selectedType);
    if (cached !== undefined) {
      setResult({ scope: libraryScope, requestGeneration, navigationEpoch: null, result: cached });
      return undefined;
    }

    const controller = new AbortController();
    let current = true;
    setResult({
      scope: libraryScope,
      requestGeneration,
      navigationEpoch: requestNavigationEpoch,
      result: { type: selectedType, status: "loading" },
    });
    const isCurrent = () => current
      && !controller.signal.aborted
      && directoryRequestGenerationRef.current === requestGeneration
      && libraryScopeRef.current === libraryScope
      && selectedTypeRef.current === selectedType;

    void Promise.resolve().then(() => {
      if (!isCurrent()) {
        return undefined;
      }
      return readAssets(services, selectedType, series.id, controller.signal);
    }).then((readyResult) => {
      if (readyResult === undefined || !isCurrent()) {
        return;
      }
      cache.current.set(selectedType, readyResult);
      setResult({ scope: libraryScope, requestGeneration, navigationEpoch: requestNavigationEpoch, result: readyResult });
    }).catch((cause: unknown) => {
      if (!isCurrent()) {
        return;
      }
      const error = normalizeApiError(cause);
      if (error.status === 401) {
        unauthorizedHandlerRef.current();
        return;
      }
      setResult({
        scope: libraryScope,
        requestGeneration,
        navigationEpoch: requestNavigationEpoch,
        result: { type: selectedType, status: "error", error },
      });
    });

    return () => {
      current = false;
      controller.abort();
      if (directoryRequestGenerationRef.current === requestGeneration) {
        directoryRequestGenerationRef.current += 1;
      }
    };
  }, [effectiveNavigationIntent, libraryScope, reloadVersion, selectedType, series.id, services, userId]);

  const resultMatchesNavigation = effectiveNavigationIntent === null
    || result?.navigationEpoch === effectiveNavigationIntent.navigationEpoch;
  const currentResult = result?.scope === libraryScope
    && result.result.type === selectedType
    && resultMatchesNavigation
    ? result.result
    : null;
  const presentation = currentResult?.status === "error" ? errorPresentation(currentResult.error) : null;
  const activeSearchTicket: AssetSearchTicket | null = currentResult?.status === "ready" && result !== null
    ? {
      scope: libraryScope,
      category: selectedType,
      result,
      requestGeneration: result.requestGeneration,
      generation: searchGenerationRef.current,
    }
    : null;
  const currentSearchSelection = activeSearchTicket !== null
    && searchSelection !== null
    && searchSelection.scope === activeSearchTicket.scope
    && searchSelection.category === activeSearchTicket.category
    && searchSelection.result === activeSearchTicket.result
    && searchSelection.requestGeneration === activeSearchTicket.requestGeneration
    && searchSelection.generation === activeSearchTicket.generation
    ? searchSelection
    : null;
  const searchQuery = currentSearchSelection?.query ?? "";
  const visibleAssetIndexes = currentResult?.status === "ready"
    ? matchedAssetIndexes(currentResult, searchQuery)
    : new Set<number>();

  function isCurrentSearchTicket(ticket: AssetSearchTicket): boolean {
    const activeResult = resultRef.current;
    return libraryScopeRef.current === ticket.scope
      && selectedTypeRef.current === ticket.category
      && activeResult === ticket.result
      && activeResult.requestGeneration === ticket.requestGeneration
      && activeResult.result.status === "ready"
      && activeResult.result.type === ticket.category
      && directoryRequestGenerationRef.current === ticket.requestGeneration
      && searchGenerationRef.current === ticket.generation;
  }

  function updateSearchQuery(query: string, ticket: AssetSearchTicket | null) {
    if (ticket === null || !isCurrentSearchTicket(ticket)) {
      return;
    }
    const selection = searchSelectionRef.current;
    const currentQuery = selection !== null
      && selection.scope === ticket.scope
      && selection.category === ticket.category
      && selection.result === ticket.result
      && selection.requestGeneration === ticket.requestGeneration
      && selection.generation === ticket.generation
      ? selection.query
      : "";
    if (query === currentQuery) {
      return;
    }

    const generation = searchGenerationRef.current + 1;
    searchGenerationRef.current = generation;
    const nextSelection = query === "" ? null : { ...ticket, generation, query };
    searchSelectionRef.current = nextSelection;
    setSearchSelection(nextSelection);
    invalidateUsage();
    setNavigationFeedback(null);
  }

  function resetAssetSearch() {
    searchGenerationRef.current += 1;
    searchSelectionRef.current = null;
    setSearchSelection(null);
  }

  function invalidateUsage() {
    openEpochRef.current += 1;
    usageOwnerRef.current = null;
    setUsageOwner(null);
  }

  function abandonNavigationIntent() {
    const intent = navigationIntentRef.current;
    if (
      intent === null
      || navigationConsumedEpochsRef.current.has(intent.navigationEpoch)
      || navigationAbandonedEpochsRef.current.has(intent.navigationEpoch)
    ) {
      return;
    }
    navigationAbandonedEpochsRef.current.add(intent.navigationEpoch);
    setNavigationIssue(null);
    setNavigationFeedback((current) => current?.navigationEpoch === intent.navigationEpoch ? null : current);
    navigationAbandonedHandlerRef.current?.(intent.navigationEpoch);
  }

  function chooseType(type: AssetLibraryType) {
    if (type === selectedType) {
      return;
    }
    directoryRequestGenerationRef.current += 1;
    resetAssetSearch();
    if (navigationIntentRef.current?.category !== type) {
      abandonNavigationIntent();
    }
    invalidateUsage();
    setNavigationFeedback(null);
    setNavigationIssue(null);
    setSelectedType(type);
  }

  function openUsage<Category extends AssetLibraryType>(
    category: Category,
    item: AssetByCategory[Category],
    directorySnapshot: AssetByCategory[Category][],
  ) {
    const activeOwner = usageOwnerRef.current;
    if (
      activeOwner !== null
      && activeOwner.openEpoch === openEpochRef.current
      && activeOwner.userId === userId
      && activeOwner.services === services
      && activeOwner.seriesId === series.id
      && activeOwner.category === category
      && activeOwner.asset === item
      && activeOwner.directorySnapshot === directorySnapshot
    ) {
      return;
    }
    if (
      category !== selectedType
      || !hasValidCategoryName(category, item)
      || !hasUniqueUsageIdentity(directorySnapshot, item, series.id)
      || libraryScopeRef.current !== libraryScope
    ) {
      return;
    }

    const owner: AssetFrameUsageOwner = {
      userId,
      services,
      seriesId: series.id,
      category,
      asset: item,
      directorySnapshot,
      openEpoch: ++openEpochRef.current,
    } as AssetFrameUsageOwner;
    usageOwnerRef.current = owner;
    setUsageOwner(owner);
  }

  function closeUsage(owner: AssetFrameUsageOwner) {
    if (usageOwnerRef.current !== owner) {
      return;
    }
    invalidateUsage();
  }

  function reloadCurrentType() {
    directoryRequestGenerationRef.current += 1;
    resetAssetSearch();
    invalidateUsage();
    setNavigationFeedback(null);
    setNavigationIssue(null);
    cache.current.delete(selectedType);
    setResult({
      scope: libraryScope,
      requestGeneration: directoryRequestGenerationRef.current,
      navigationEpoch: navigationIntentRef.current?.navigationEpoch ?? null,
      result: { type: selectedType, status: "loading" },
    });
    setFailedImages((previous) => {
      const next = new Set(previous);
      for (const key of next) {
        if (key.startsWith(libraryScope.generation + ":" + selectedType + ":")) {
          next.delete(key);
        }
      }
      return next;
    });
    setReloadVersion((version) => version + 1);
  }

  function goBack() {
    abandonNavigationIntent();
    invalidateUsage();
    onBack();
  }

  function renderAssetCard<Category extends AssetLibraryType>(
    item: AssetByCategory[Category],
    category: Category,
    items: AssetByCategory[Category][],
    itemPosition: number,
    hidden: boolean,
  ) {
    const imageKey = libraryScope.generation + ":" + category + ":" + item.id + ":" + (item.image_url ?? "");
    const identityIsUnique = hasUniqueUsageIdentity(items, item, series.id) && hasValidCategoryName(category, item);
    return (
      <AssetCard
        apiBaseUrl={services.apiBaseUrl}
        cardRef={(element) => {
          if (element === null) {
            assetCardElementsRef.current.delete(item.id);
          } else {
            assetCardElementsRef.current.set(item.id, element);
          }
        }}
        highlighted={navigationFeedback?.scope === libraryScope
          && navigationFeedback.category === category
          && navigationFeedback.assetId === item.id}
        hidden={hidden}
        onCardBlur={() => {
          setNavigationFeedback((current) => (
            current?.scope === libraryScope
            && current.category === category
            && current.assetId === item.id
              ? null
              : current
          ));
        }}
        failed={failedImages.has(imageKey)}
        item={item}
        key={category + ":" + item.id + ":" + itemPosition}
        onImageError={() => setFailedImages((previous) => new Set(previous).add(imageKey))}
        onShowUsage={identityIsUnique ? () => openUsage(category, item, items) : undefined}
        usageUnavailable={!identityIsUnique}
        type={category}
      />
    );
  }

  function renderAssetCards(ready: AssetReadyResult) {
    switch (ready.type) {
      case "characters":
        return ready.items.map((item, index) => renderAssetCard(
          item,
          "characters",
          ready.items,
          index,
          !visibleAssetIndexes.has(index),
        ));
      case "scenes":
        return ready.items.map((item, index) => renderAssetCard(
          item,
          "scenes",
          ready.items,
          index,
          !visibleAssetIndexes.has(index),
        ));
      case "props":
        return ready.items.map((item, index) => renderAssetCard(
          item,
          "props",
          ready.items,
          index,
          !visibleAssetIndexes.has(index),
        ));
    }
  }

  const visibleUsageOwner = usageOwner !== null
    && usageOwnerRef.current === usageOwner
    && usageOwner.openEpoch === openEpochRef.current
    && usageOwner.userId === userId
    && usageOwner.services === services
    && usageOwner.seriesId === series.id
    && usageOwner.category === selectedType
    && currentResult !== null
    && currentResult.status === "ready"
    && currentResult.type === usageOwner.category
    && currentResult.items === usageOwner.directorySnapshot
    && currentResult.items.some((item) => item === usageOwner.asset)
    ? usageOwner
    : null;

  function isCurrentUsageOwner(owner: AssetFrameUsageOwner): boolean {
    const activeOwner = usageOwnerRef.current;
    const activeResult = currentResult;
    if (activeResult === null || activeResult.status !== "ready") {
      return false;
    }
    const activeItems: AssetRecord[] = activeResult.items;
    return activeOwner === owner
      && owner.openEpoch === openEpochRef.current
      && owner.userId === userId
      && owner.services === services
      && owner.seriesId === series.id
      && owner.category === selectedTypeRef.current
      && libraryScopeRef.current === libraryScope
      && activeResult.type === owner.category
      && activeItems === owner.directorySnapshot
      && activeItems.includes(owner.asset);
  }

  useEffect(() => {
    if (usageOwner !== null && usageOwnerRef.current !== usageOwner) {
      setUsageOwner(null);
    }
  }, [usageOwner, libraryScope]);

  useLayoutEffect(() => {
    const intent = effectiveNavigationIntent;
    if (
      intent === null
      || navigationConsumedEpochsRef.current.has(intent.navigationEpoch)
      || navigationAbandonedEpochsRef.current.has(intent.navigationEpoch)
      || intent.userId !== userId
      || intent.services !== services
      || intent.seriesId !== series.id
      || intent.category !== selectedType
      || currentResult === null
      || currentResult.status !== "ready"
      || currentResult.type !== intent.category
      || result?.navigationEpoch !== intent.navigationEpoch
    ) {
      return;
    }

    const scopedResult = result;
    const requestGeneration = scopedResult?.requestGeneration;
    if (scopedResult === null || requestGeneration === undefined) {
      return;
    }
    const isCurrent = () => navigationIntentRef.current === intent
      && !navigationConsumedEpochsRef.current.has(intent.navigationEpoch)
      && !navigationAbandonedEpochsRef.current.has(intent.navigationEpoch)
      && libraryScopeRef.current === libraryScope
      && libraryScope.userId === userId
      && libraryScope.services === services
      && libraryScope.seriesId === series.id
      && selectedTypeRef.current === intent.category
      && resultRef.current === scopedResult
      && directoryRequestGenerationRef.current === requestGeneration;
    if (!isCurrent()) {
      return;
    }

    const matches = currentResult.items.filter((item) => item.id === intent.assetId);
    if (matches.length !== 1) {
      setNavigationIssue({
        epoch: intent.navigationEpoch,
        message: matches.length === 0
          ? "当前分类中没有找到对应素材。可重新核对。"
          : "当前分类中有多项素材身份相同，暂时无法定位。可重新核对。",
      });
      return;
    }

    const [match] = matches;
    if (match === undefined || match.series_id !== intent.seriesId) {
      setNavigationIssue({ epoch: intent.navigationEpoch, message: "该素材不属于当前剧集，暂时无法定位。可重新核对。" });
      return;
    }

    const element = assetCardElementsRef.current.get(intent.assetId);
    const libraryElement = libraryElementRef.current;
    if (
      element === undefined
      || libraryElement === null
      || !element.isConnected
      || !libraryElement.contains(element)
      || element.closest("[hidden], [inert]") !== null
    ) {
      setNavigationIssue({ epoch: intent.navigationEpoch, message: "素材卡片当前不可见，暂时无法定位。可重新核对。" });
      return;
    }

    try {
      element.focus({ preventScroll: true });
    } catch {
      setNavigationIssue({ epoch: intent.navigationEpoch, message: "浏览器未能定位素材卡片。可重新核对。" });
      return;
    }

    if (
      document.activeElement !== element
      || !isCurrent()
      || resultRef.current !== scopedResult
      || directoryRequestGenerationRef.current !== requestGeneration
      || assetCardElementsRef.current.get(intent.assetId) !== element
      || currentResult.items.filter((item) => item.id === intent.assetId).length !== 1
      || !element.isConnected
      || !libraryElement.contains(element)
      || element.closest("[hidden], [inert]") !== null
    ) {
      setNavigationIssue({ epoch: intent.navigationEpoch, message: "浏览器未能将焦点移到素材卡片。可重新核对。" });
      return;
    }

    element.scrollIntoView?.({ behavior: "auto", block: "center" });
    navigationConsumedEpochsRef.current.add(intent.navigationEpoch);
    setNavigationIssue(null);
    setNavigationFeedback({
      scope: libraryScope,
      category: intent.category,
      navigationEpoch: intent.navigationEpoch,
      assetId: intent.assetId,
    });
    navigationConsumedHandlerRef.current?.(intent.navigationEpoch);
  }, [currentResult, effectiveNavigationIntent, libraryScope, result, selectedType, series.id, services, userId]);

  const currentNavigationFeedback = navigationFeedback !== null
    && navigationFeedback.scope === libraryScope
    && navigationFeedback.category === selectedType
    && (effectiveNavigationIntent === null
      ? navigationConsumedEpochsRef.current.has(navigationFeedback.navigationEpoch)
      : effectiveNavigationIntent.navigationEpoch === navigationFeedback.navigationEpoch)
    ? navigationFeedback
    : null;

  const currentNavigationIssue = effectiveNavigationIntent !== null
    && navigationIssue?.epoch === effectiveNavigationIntent.navigationEpoch
    ? navigationIssue.message
    : null;

  return (
    <section className="asset-library" aria-labelledby="asset-library-heading" ref={libraryElementRef}>
      <div className="asset-library-heading">
        <div>
          <p className="eyebrow">只读浏览 · 素材</p>
          <h1 id="asset-library-heading">{series.name} · 素材库</h1>
          <p>查看剧集已有的角色、场景与道具素材。</p>
        </div>
        <div className="asset-library-actions">
          <button className="secondary-button asset-refresh-button" onClick={reloadCurrentType} type="button">
            重新读取
          </button>
          <button className="secondary-button chapter-back-button" onClick={goBack} type="button">
            <span aria-hidden="true">←</span> 返回剧集列表
          </button>
        </div>
      </div>

      <div className="asset-library-toolbar">
        <div className="asset-type-tabs" role="group" aria-label="素材分类">
          {assetTypes.map((type) => (
            <button
              aria-pressed={selectedType === type.id}
              className={selectedType === type.id ? "asset-type-tab active" : "asset-type-tab"}
              key={type.id}
              onClick={() => chooseType(type.id)}
              type="button"
            >
              {type.label}
              {currentResult?.type === type.id && currentResult.status === "ready" && (
                <span className="filter-count">{currentResult.items.length}</span>
              )}
            </button>
          ))}
        </div>
        <span className="list-order-note">保持列表原始顺序</span>
      </div>

      {activeSearchTicket !== null && currentResult?.status === "ready" && currentResult.items.length > 0 && (
        <section className="asset-library-search" aria-label="当前分类本地搜索">
          <label htmlFor="asset-library-search-input">{assetSearchLabel(selectedType)}</label>
          <div className="asset-library-search-controls">
            <input
              aria-label={assetSearchLabel(selectedType)}
              autoComplete="off"
              id="asset-library-search-input"
              onChange={(event: ChangeEvent<HTMLInputElement>) => updateSearchQuery(event.currentTarget.value, activeSearchTicket)}
              type="search"
              value={searchQuery}
            />
            <button
              aria-label="清空搜索"
              className="asset-library-search-clear"
              disabled={searchQuery === ""}
              onClick={() => updateSearchQuery("", activeSearchTicket)}
              type="button"
            >
              清空
            </button>
          </div>
          <p className="asset-library-search-count" aria-live="polite">
            显示 {visibleAssetIndexes.size} / {currentResult.items.length} 项素材
          </p>
        </section>
      )}

      {currentNavigationFeedback !== null && (
        <p className="asset-navigation-feedback" role="status">已定位到对应素材。</p>
      )}
      {currentNavigationIssue !== null && (
        <div className="asset-navigation-issue" role="status">
          <p>{currentNavigationIssue}</p>
          <button className="secondary-button" onClick={reloadCurrentType} type="button">重新核对</button>
        </div>
      )}
      {currentResult === null || currentResult.status === "loading" ? (
        <div className="state-panel loading-panel" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>正在读取{assetTypes.find((type) => type.id === selectedType)?.label}…</span>
        </div>
      ) : currentResult.status === "error" ? (
        <div className="state-panel error-panel" role="alert">
          <div className="state-icon" aria-hidden="true">!</div>
          <div className="state-copy">
            <h2>{presentation?.title}</h2>
            <p>{presentation?.message}</p>
          </div>
          <button className="secondary-button" onClick={reloadCurrentType} type="button">重试读取</button>
        </div>
      ) : currentResult.items.length === 0 ? (
        <div className="state-panel empty-panel" role="status">
          <div className="empty-icon" aria-hidden="true">◌</div>
          <h2>还没有{assetTypes.find((type) => type.id === selectedType)?.label}素材</h2>
          <p>新增与编辑功能暂未开放。</p>
        </div>
      ) : (
        <>
          {visibleAssetIndexes.size === 0 && searchQuery.trim() !== "" && (
            <div className="asset-search-empty" role="status">
              <p>当前分类中没有匹配的素材。</p>
              <span>本目录共 {currentResult.items.length} 项；清空搜索可查看全部。</span>
            </div>
          )}
          <div className="asset-grid" aria-live="polite">
            {renderAssetCards(currentResult)}
          </div>
        </>
      )}
      {visibleUsageOwner !== null && (
        <AssetFrameUsagePanel
          assetLabel={assetName(visibleUsageOwner.category, visibleUsageOwner.asset)}
          currentScope={visibleUsageOwner}
          key={visibleUsageOwner.openEpoch}
          onClose={closeUsage}
          {...(series.can_enter === true && onViewChapter !== undefined
            ? {
              onNavigateToChapter: (target: AssetFrameUsageChapterTarget) => {
                if (!isCurrentUsageOwner(visibleUsageOwner) || target.seriesId !== series.id || !target.isCurrent()) {
                  return;
                }
                onViewChapter(target);
              },
            }
            : {})}
          {...(series.can_enter === true && onViewFrame !== undefined
            ? {
              onNavigateToFrame: (target: AssetFrameUsageFrameTarget) => {
                if (
                  !isCurrentUsageOwner(visibleUsageOwner)
                  || target.seriesId !== series.id
                  || target.category !== visibleUsageOwner.category
                  || target.assetId !== visibleUsageOwner.asset.id
                  || target.storyboardAssetId.trim() === ""
                  || !target.isCurrent()
                ) {
                  return;
                }
                onViewFrame(target);
              },
            }
            : {})}
          onUnauthorized={onUnauthorized}
          owner={visibleUsageOwner}
        />
      )}
    </section>
  );
}
