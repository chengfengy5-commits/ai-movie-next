import { useEffect, useLayoutEffect, useRef, useState, type ChangeEvent } from "react";
import type {
  AssetLibraryType,
  Character,
  Prop,
  Scene,
  Series,
} from "../../shared/api/contracts";
import type { AssetGenerationHistoryPort } from "../../shared/api/assetGenerationHistory";
import { ApiError } from "../../shared/api/errors";
import { normalizeApiError, type WorkspaceServices } from "../../shared/api/services";
import { AssetMaintenanceDialog } from "./maintenance/AssetMaintenanceDialog";
import {
  AssetManagementDialog,
  type AssetDirectory,
  type AssetManagementMode,
} from "./management/AssetManagementDialog";
import { searchAssetLibraryItems, type AssetLibraryItemsByCategory } from "./assetLibrarySearch";
import {
  AssetFrameUsagePanel,
  type AssetFrameUsageChapterTarget,
  type AssetFrameUsageFrameTarget,
  type AssetFrameUsageOwner,
} from "./AssetFrameUsagePanel";
import { AssetReplacementDialog } from "./replacement/AssetReplacementDialog";
import {
  AssetImageUploadDialog,
  type AssetImageUploadAttempt,
} from "./media/AssetImageUploadDialog";
import {
  CharacterAudioDialog,
  type CharacterAudioAttempt,
} from "./media/CharacterAudioDialog";
import {
  AssetGenerationHistoryDialog,
  type AssetGenerationHistoryImageAdoptionPort,
} from "./media/AssetGenerationHistoryDialog";
import type {
  AssetImageDirectory,
  AssetImageUploadRun,
} from "../../shared/api/assetImageUpload";
import type {
  CharacterAudioDirectory,
  CharacterAudioRun,
} from "../../shared/api/characterAudio";

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

interface AssetManagementRequest {
  id: number;
  scope: LibraryScope;
  category: AssetLibraryType;
  mode: AssetManagementMode;
  item: AssetRecord | null;
}

interface AssetMaintenanceRequest {
  id: number;
  scope: LibraryScope;
  category: AssetLibraryType;
  directoryResult: ScopedAssetResult;
  directoryItems: AssetRecord[];
  directoryRefreshEpoch: number;
  pendingDirectoryReloadVersion: number | null;
  acceptedDirectoryReloadVersion: number | null;
  port: NonNullable<WorkspaceServices["assetMaintenance"]>;
  orphanActionsLocked: boolean;
}

interface AssetReplacementRequest {
  id: number;
  scope: LibraryScope;
  category: AssetLibraryType;
  asset: AssetRecord;
  directoryResult: ScopedAssetResult;
  port: NonNullable<WorkspaceServices["assetReplacement"]>;
  openEpoch: number;
  writeInFlight: boolean;
  hasAttempt: boolean;
}

interface AssetImageUploadRequest {
  id: number;
  scope: LibraryScope;
  category: AssetLibraryType;
  asset: AssetRecord;
  directoryResult: ScopedAssetResult;
  port: NonNullable<WorkspaceServices["assetImageUpload"]>;
  run: AssetImageUploadRun;
  attempts: AssetImageUploadAttempt[];
  openEpoch: number;
  invalidateRun(): void;
}

interface CharacterAudioRequest {
  id: number;
  scope: LibraryScope;
  category: "characters";
  asset: Character;
  directoryResult: ScopedAssetResult;
  port: NonNullable<WorkspaceServices["characterAudio"]>;
  run: CharacterAudioRun;
  attempts: CharacterAudioAttempt[];
  openEpoch: number;
}

interface AssetGenerationHistoryRequest {
  id: number;
  scope: LibraryScope;
  category: AssetLibraryType;
  asset: AssetRecord;
  directoryResult: ScopedAssetResult;
  port: AssetGenerationHistoryPort;
  openEpoch: number;
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

function generationHistoryAssetType(category: AssetLibraryType): "character" | "scene" | "prop" {
  switch (category) {
    case "characters": return "character";
    case "scenes": return "scene";
    case "props": return "prop";
  }
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

function assetImageUrl(imageUrl: string | null, apiBaseUrl: string): string | null {
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
  const imageUrl = apiBaseUrl === null ? null : assetImageUrl(item.image_url, apiBaseUrl);
  if (imageUrl === null || failed) {
    return (
      <div className="asset-image-placeholder" aria-label={name + "没有可显示的本地图片"} role="img">
        <span aria-hidden="true">◇</span>
        <small>暂无图片</small>
      </div>
    );
  }
  return <img alt={name + "图片"} loading="lazy" onError={onError} referrerPolicy="no-referrer" src={imageUrl} />;
}

function AssetCard({
  item,
  type,
  apiBaseUrl,
  failed,
  onImageError,
  onShowUsage,
  onReplace,
  replacementUnavailable,
  onUploadImage,
  imageUploadUnavailable,
  onGenerationHistory,
  generationHistoryUnavailable,
  onCharacterAudio,
  characterAudioUnavailable,
  onEdit,
  onDelete,
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
  onReplace?: (() => void) | undefined;
  replacementUnavailable?: string | undefined;
  onUploadImage?: (() => void) | undefined;
  imageUploadUnavailable?: string | undefined;
  onGenerationHistory?: (() => void) | undefined;
  generationHistoryUnavailable?: string | undefined;
  onCharacterAudio?: (() => void) | undefined;
  characterAudioUnavailable?: string | undefined;
  onEdit?: (() => void) | undefined;
  onDelete?: (() => void) | undefined;
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
        <div className="asset-card-replacement-action">
          <button className="secondary-button" disabled={onReplace === undefined} onClick={onReplace} type="button">
            替换章节引用
          </button>
          {replacementUnavailable !== undefined && <small role="note">{replacementUnavailable}</small>}
        </div>
        <div className="asset-card-image-upload-action">
          <button className="secondary-button" disabled={onUploadImage === undefined} onClick={onUploadImage} type="button">
            上传/更换图片
          </button>
          {imageUploadUnavailable !== undefined && <small role="note">{imageUploadUnavailable}</small>}
        </div>
        <div className="asset-card-generation-history-action">
          <button className="secondary-button" disabled={onGenerationHistory === undefined} onClick={onGenerationHistory} type="button">
            生成历史
          </button>
          {generationHistoryUnavailable !== undefined && <small role="note">{generationHistoryUnavailable}</small>}
        </div>
        {character !== null && (
          <div className="asset-card-character-audio-action">
            <button className="secondary-button" disabled={onCharacterAudio === undefined} onClick={onCharacterAudio} type="button">
              音频与音色
            </button>
            {characterAudioUnavailable !== undefined && <small role="note">{characterAudioUnavailable}</small>}
          </div>
        )}
        {(onEdit !== undefined || onDelete !== undefined) && (
          <div className="asset-card-management-actions" aria-label={name + "素材操作"}>
            {onEdit !== undefined && <button onClick={onEdit} type="button">编辑</button>}
            {onDelete !== undefined && <button className="asset-card-delete" onClick={onDelete} type="button">删除</button>}
          </div>
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
  generationHistoryPort?: AssetGenerationHistoryPort | undefined;
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
  generationHistoryPort,
  onNavigationConsumed,
  onNavigationAbandoned,
}: AssetLibraryProps) {
  const [selectedType, setSelectedType] = useState<AssetLibraryType>(
    () => navigationIntent?.category ?? "characters",
  );
  const [reloadVersion, setReloadVersion] = useState(0);
  const reloadVersionRef = useRef(0);
  const [result, setResult] = useState<ScopedAssetResult | null>(null);
  const resultRef = useRef(result);
  resultRef.current = result;
  const [failedImages, setFailedImages] = useState<Set<string>>(() => new Set());
  const [searchSelection, setSearchSelection] = useState<AssetSearchSelection | null>(null);
  const searchSelectionRef = useRef(searchSelection);
  searchSelectionRef.current = searchSelection;
  const searchGenerationRef = useRef(0);
  const [usageOwner, setUsageOwner] = useState<AssetFrameUsageOwner | null>(null);
  const [managementRequest, setManagementRequest] = useState<AssetManagementRequest | null>(null);
  const managementRequestRef = useRef(managementRequest);
  managementRequestRef.current = managementRequest;
  const managementRequestIdRef = useRef(0);
  const invalidatedManagementRequestRef = useRef<AssetManagementRequest | null>(null);
  const [maintenanceRequest, setMaintenanceRequest] = useState<AssetMaintenanceRequest | null>(null);
  const maintenanceRequestRef = useRef(maintenanceRequest);
  maintenanceRequestRef.current = maintenanceRequest;
  const maintenanceRequestIdRef = useRef(0);
  const [replacementRequests, setReplacementRequests] = useState<AssetReplacementRequest[]>([]);
  const replacementRequestsRef = useRef(replacementRequests);
  replacementRequestsRef.current = replacementRequests;
  const [activeReplacementId, setActiveReplacementId] = useState<number | null>(null);
  const activeReplacementIdRef = useRef(activeReplacementId);
  activeReplacementIdRef.current = activeReplacementId;
  const replacementRequestIdRef = useRef(0);
  const [imageUploadRequests, setImageUploadRequests] = useState<AssetImageUploadRequest[]>([]);
  const imageUploadRequestsRef = useRef(imageUploadRequests);
  imageUploadRequestsRef.current = imageUploadRequests;
  const [activeImageUploadId, setActiveImageUploadId] = useState<number | null>(null);
  const activeImageUploadIdRef = useRef(activeImageUploadId);
  activeImageUploadIdRef.current = activeImageUploadId;
  const imageUploadRequestIdRef = useRef(0);
  const imageUploadAttemptIdRef = useRef(0);
  const [imageUploadIssue, setImageUploadIssue] = useState<string | null>(null);
  const [characterAudioRequests, setCharacterAudioRequests] = useState<CharacterAudioRequest[]>([]);
  const characterAudioRequestsRef = useRef(characterAudioRequests);
  characterAudioRequestsRef.current = characterAudioRequests;
  const [activeCharacterAudioId, setActiveCharacterAudioId] = useState<number | null>(null);
  const activeCharacterAudioIdRef = useRef(activeCharacterAudioId);
  activeCharacterAudioIdRef.current = activeCharacterAudioId;
  const characterAudioRequestIdRef = useRef(0);
  const characterAudioAttemptIdRef = useRef(0);
  const [characterAudioIssue, setCharacterAudioIssue] = useState<string | null>(null);
  const [generationHistoryRequest, setGenerationHistoryRequest] = useState<AssetGenerationHistoryRequest | null>(null);
  const generationHistoryRequestRef = useRef(generationHistoryRequest);
  generationHistoryRequestRef.current = generationHistoryRequest;
  const generationHistoryRequestIdRef = useRef(0);
  const [pendingLocateAsset, setPendingLocateAsset] = useState<{
    assetId: string;
    scope: LibraryScope;
    category: AssetLibraryType;
  } | null>(null);
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

  function invalidateActiveImageUpload(): void {
    const activeId = activeImageUploadIdRef.current;
    if (activeId === null) return;
    const request = imageUploadRequestsRef.current.find((candidate) => candidate.id === activeId);
    if (request !== undefined) {
      request.invalidateRun();
      const lastAttempt = request.attempts.at(-1);
      if (lastAttempt?.state === "uploading" || lastAttempt?.state === "confirming"
        || lastAttempt?.state === "saving" || lastAttempt?.state === "acknowledged") {
        const nextAttempts = request.attempts.map((attempt) => attempt.id === lastAttempt.id
          ? {
            ...attempt,
            state: lastAttempt.state === "uploading"
              ? "upload-unknown" as const
              : lastAttempt.state === "confirming"
                ? "uploaded" as const
                : lastAttempt.state === "saving" ? "save-unknown" as const : "acknowledged-read-failed" as const,
            message: lastAttempt.state === "confirming"
              ? "窗口在确认前只读核对期间关闭；目录更新未发送，上传回执已保留。"
              : "操作窗口已关闭或目录状态已变化，原写入结果尚未确认。",
          }
          : attempt);
        const nextRequests = imageUploadRequestsRef.current.map((candidate) => candidate.id === request.id
          ? { ...candidate, attempts: nextAttempts }
          : candidate);
        imageUploadRequestsRef.current = nextRequests;
        setImageUploadRequests(nextRequests);
      }
    }
    activeImageUploadIdRef.current = null;
    setActiveImageUploadId(null);
    openEpochRef.current += 1;
  }

  function invalidateActiveCharacterAudio(): void {
    const activeId = activeCharacterAudioIdRef.current;
    if (activeId === null) return;
    const request = characterAudioRequestsRef.current.find((candidate) => candidate.id === activeId);
    if (request !== undefined) {
      const lastAttempt = request.attempts.at(-1);
      if (lastAttempt?.state === "uploading" || lastAttempt?.state === "confirming"
        || lastAttempt?.state === "saving" || lastAttempt?.state === "acknowledged") {
        const state: CharacterAudioAttempt["state"] = lastAttempt.state === "uploading"
          ? "upload-unknown"
          : lastAttempt.state === "confirming"
            ? "save-not-sent"
            : lastAttempt.state === "saving" ? "save-unknown" : "acknowledged-read-failed";
        const message = lastAttempt.state === "uploading"
          ? "窗口关闭或角色目录变化时上传已发出；结果未知，不会自动重发。"
          : lastAttempt.state === "confirming"
            ? "窗口关闭或角色目录变化时仍在保存前只读核对；角色更新未发送。"
            : lastAttempt.state === "saving"
              ? "窗口关闭或角色目录变化时保存已发出；结果未知，不会自动重放。"
              : "服务器已确认保存，但独立目录读取尚未完成；可重新打开后只读核对。";
        const nextAttempts = request.attempts.map((attempt) => attempt.id === lastAttempt.id
          ? { ...attempt, state, message }
          : attempt);
        const nextRequests = characterAudioRequestsRef.current.map((candidate) => candidate.id === request.id
          ? { ...candidate, attempts: nextAttempts }
          : candidate);
        characterAudioRequestsRef.current = nextRequests;
        setCharacterAudioRequests(nextRequests);
      }
    }
    activeCharacterAudioIdRef.current = null;
    setActiveCharacterAudioId(null);
    openEpochRef.current += 1;
  }

  const previousScope = libraryScopeRef.current;
  if (previousScope.userId !== userId || previousScope.services !== services || previousScope.seriesId !== series.id) {
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    generationHistoryRequestRef.current = null;
    setGenerationHistoryRequest(null);
    libraryScopeRef.current = { userId, services, seriesId: series.id, generation: previousScope.generation + 1 };
    directoryRequestGenerationRef.current += 1;
    searchGenerationRef.current += 1;
    searchSelectionRef.current = null;
    cache.current = new Map();
    invalidatedManagementRequestRef.current = null;
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
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    observedNavigationEpochRef.current = effectiveNavigationIntent.navigationEpoch;
    directoryRequestGenerationRef.current += 1;
    searchGenerationRef.current += 1;
    searchSelectionRef.current = null;
    setSearchSelection(null);
    usageOwnerRef.current = null;
    openEpochRef.current += 1;
    invalidatedManagementRequestRef.current = null;
    setUsageOwner(null);
    managementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    setNavigationIssue(null);
    setNavigationFeedback(null);
    setSelectedType(effectiveNavigationIntent.category);
  }

  useEffect(() => {
    const currentMaintenanceRequest = maintenanceRequestRef.current;
    if (currentMaintenanceRequest !== null
      && currentMaintenanceRequest.scope === libraryScope
      && currentMaintenanceRequest.acceptedDirectoryReloadVersion === reloadVersion) {
      return undefined;
    }
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
      const invalidatedRequest = invalidatedManagementRequestRef.current;
      if (
        invalidatedRequest !== null
        && invalidatedRequest === managementRequestRef.current
        && invalidatedRequest.scope === libraryScope
        && invalidatedRequest.category === selectedType
      ) {
        invalidatedManagementRequestRef.current = null;
      }
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
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
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
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
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
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    generationHistoryRequestRef.current = null;
    setGenerationHistoryRequest(null);
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    openEpochRef.current += 1;
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
    if (replacementRequestsRef.current.some((request) => request.writeInFlight)) return;
    if (activeImageUploadIdRef.current !== null) return;
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
    invalidateUsage();

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

  function openManagement(
    mode: AssetManagementMode,
    category: AssetLibraryType,
    item: AssetRecord | null,
    directoryItems: AssetRecord[],
  ) {
    if (replacementRequestsRef.current.some((request) => request.writeInFlight)) return;
    const activeResult = resultRef.current;
    if (
      category !== selectedTypeRef.current
      || activeResult === null
      || activeResult.scope !== libraryScope
      || activeResult.result.status !== "ready"
      || activeResult.result.type !== category
      || libraryScopeRef.current !== libraryScope
    ) {
      return;
    }
    if (item !== null && !hasUniqueUsageIdentity(directoryItems, item, series.id)) {
      return;
    }
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    invalidateUsage();
    const request: AssetManagementRequest = {
      id: ++managementRequestIdRef.current,
      scope: libraryScope,
      category,
      mode,
      item,
    };
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    managementRequestRef.current = request;
    setManagementRequest(request);
  }

  function openMaintenance(): void {
    if (replacementRequestsRef.current.some((request) => request.writeInFlight)) return;
    const activeResult = resultRef.current;
    const port = services.assetMaintenance;
    if (
      port === undefined
      || activeResult === null
      || activeResult.scope !== libraryScope
      || activeResult.result.status !== "ready"
      || activeResult.result.type !== selectedType
      || libraryScopeRef.current !== libraryScope
    ) {
      return;
    }
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    const request: AssetMaintenanceRequest = {
      id: ++maintenanceRequestIdRef.current,
      scope: libraryScope,
      category: selectedType,
      directoryResult: activeResult,
      directoryItems: activeResult.result.items,
      directoryRefreshEpoch: 0,
      pendingDirectoryReloadVersion: null,
      acceptedDirectoryReloadVersion: null,
      port,
      orphanActionsLocked: true,
    };
    maintenanceRequestRef.current = request;
    setMaintenanceRequest(request);
    invalidateUsage();
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
  }

  function openReplacement(
    category: AssetLibraryType,
    item: AssetRecord,
    directoryItems: AssetRecord[],
  ): void {
    if (replacementRequestsRef.current.some((request) => request.writeInFlight)) return;
    const activeResult = resultRef.current;
    const port = services.assetReplacement;
    if (
      series.can_enter === false
      || port === undefined
      || category !== selectedTypeRef.current
      || activeResult === null
      || activeResult.scope !== libraryScope
      || activeResult.result.status !== "ready"
      || activeResult.result.type !== category
      || libraryScopeRef.current !== libraryScope
      || !hasValidCategoryName(category, item)
      || !hasUniqueUsageIdentity(directoryItems, item, series.id)
    ) {
      return;
    }

    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    invalidateUsage();
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    resetAssetSearch();

    const existing = replacementRequestsRef.current.find((request) => request.scope === libraryScope
      && request.category === category
      && request.asset.id === item.id
      && request.port === port);
    const requestEpoch = ++openEpochRef.current;
    const request: AssetReplacementRequest = existing === undefined
      ? {
        id: ++replacementRequestIdRef.current,
        scope: libraryScope,
        category,
        asset: item,
        directoryResult: activeResult,
        port,
        openEpoch: requestEpoch,
        writeInFlight: false,
        hasAttempt: false,
      }
      : { ...existing, asset: item, directoryResult: activeResult, openEpoch: requestEpoch, writeInFlight: false };
    const nextRequests = existing === undefined
      ? [...replacementRequestsRef.current, request]
      : replacementRequestsRef.current.map((candidate) => candidate.id === existing.id ? request : candidate);
    replacementRequestsRef.current = nextRequests;
    setReplacementRequests(nextRequests);
    activeReplacementIdRef.current = request.id;
    setActiveReplacementId(request.id);
  }

  function isCurrentImageUploadContext(requestId: number): boolean {
    const request = imageUploadRequestsRef.current.find((candidate) => candidate.id === requestId);
    const activeResult = resultRef.current;
    return request !== undefined
      && request.scope === libraryScopeRef.current
      && request.scope.userId === userId
      && request.scope.services === services
      && request.scope.seriesId === series.id
      && request.category === selectedTypeRef.current
      && request.port === services.assetImageUpload
      && series.can_enter !== false
      && activeResult === request.directoryResult
      && activeResult.result.status === "ready"
      && activeResult.result.type === request.category
      && directoryRequestGenerationRef.current === request.directoryResult.requestGeneration
      && hasUniqueUsageIdentity(activeResult.result.items, request.asset, series.id);
  }

  function isCurrentImageUpload(request: AssetImageUploadRequest, requestEpoch: number): boolean {
    const activeRequest = imageUploadRequestsRef.current.find((candidate) => candidate.id === request.id);
    return activeRequest !== undefined
      && activeRequest.openEpoch === requestEpoch
      && activeImageUploadIdRef.current === request.id
      && openEpochRef.current === requestEpoch
      && activeRequest.scope === libraryScopeRef.current
      && activeRequest.scope.userId === userId
      && activeRequest.scope.services === services
      && activeRequest.scope.seriesId === series.id
      && activeRequest.category === selectedTypeRef.current
      && activeRequest.port === services.assetImageUpload
      && series.can_enter !== false
      && resultRef.current === activeRequest.directoryResult
      && resultRef.current.result.status === "ready"
      && resultRef.current.result.type === activeRequest.category
      && directoryRequestGenerationRef.current === activeRequest.directoryResult.requestGeneration
      && hasUniqueUsageIdentity(resultRef.current.result.items, activeRequest.asset, series.id);
  }

  function isCurrentCharacterAudioContext(requestId: number): boolean {
    const request = characterAudioRequestsRef.current.find((candidate) => candidate.id === requestId);
    const activeResult = resultRef.current;
    return request !== undefined
      && request.scope === libraryScopeRef.current
      && request.scope.userId === userId
      && request.scope.services === services
      && request.scope.seriesId === series.id
      && request.category === selectedTypeRef.current
      && request.port === services.characterAudio
      && series.can_enter !== false
      && activeResult === request.directoryResult
      && activeResult.result.status === "ready"
      && activeResult.result.type === "characters"
      && directoryRequestGenerationRef.current === request.directoryResult.requestGeneration
      && hasUniqueUsageIdentity(activeResult.result.items, request.asset, series.id);
  }

  function isCurrentCharacterAudio(request: CharacterAudioRequest, requestEpoch: number): boolean {
    const activeRequest = characterAudioRequestsRef.current.find((candidate) => candidate.id === request.id);
    return activeRequest !== undefined
      && activeRequest.openEpoch === requestEpoch
      && activeCharacterAudioIdRef.current === request.id
      && openEpochRef.current === requestEpoch
      && activeRequest.scope === libraryScopeRef.current
      && activeRequest.scope.userId === userId
      && activeRequest.scope.services === services
      && activeRequest.scope.seriesId === series.id
      && activeRequest.category === selectedTypeRef.current
      && activeRequest.port === services.characterAudio
      && series.can_enter !== false
      && resultRef.current === activeRequest.directoryResult
      && resultRef.current.result.status === "ready"
      && resultRef.current.result.type === "characters"
      && directoryRequestGenerationRef.current === activeRequest.directoryResult.requestGeneration
      && hasUniqueUsageIdentity(resultRef.current.result.items, activeRequest.asset, series.id);
  }

  function isCurrentGenerationHistory(request: AssetGenerationHistoryRequest): boolean {
    const activeRequest = generationHistoryRequestRef.current;
    const activeResult = resultRef.current;
    return activeRequest === request
      && request.openEpoch === openEpochRef.current
      && request.scope === libraryScopeRef.current
      && request.scope.userId === userId
      && request.scope.services === services
      && request.scope.seriesId === series.id
      && request.category === selectedTypeRef.current
      && request.port === generationHistoryPort
      && series.can_enter !== false
      && activeResult === request.directoryResult
      && activeResult.result.status === "ready"
      && activeResult.result.type === request.category
      && directoryRequestGenerationRef.current === request.directoryResult.requestGeneration
      && hasValidCategoryName(request.category, request.asset)
      && hasUniqueUsageIdentity(activeResult.result.items, request.asset, series.id);
  }

  function readGenerationHistoryAssets(
    category: AssetLibraryType,
    signal: AbortSignal,
  ): Promise<AssetRecord[]> {
    switch (category) {
      case "characters": return services.listCharacters(series.id, signal);
      case "scenes": return services.listScenes(series.id, signal);
      case "props": return services.listProps(series.id, signal);
    }
  }

  function publishGenerationHistoryAssets(
    request: AssetGenerationHistoryRequest,
    items: AssetRecord[],
  ): AssetRecord | null {
    if (!isCurrentGenerationHistory(request)) return null;
    const matches = items.filter((item) => item.id === request.asset.id);
    const target = matches[0];
    if (matches.length !== 1 || target === undefined
      || target.series_id !== request.scope.seriesId
      || !hasValidCategoryName(request.category, target)
      || !hasUniqueUsageIdentity(items, target, request.scope.seriesId)) {
      return null;
    }

    let ready: AssetReadyResult;
    switch (request.category) {
      case "characters":
        ready = { type: "characters", status: "ready", items: items as Character[] };
        break;
      case "scenes":
        ready = { type: "scenes", status: "ready", items: items as Scene[] };
        break;
      case "props":
        ready = { type: "props", status: "ready", items: items as Prop[] };
        break;
    }
    const nextResult: ScopedAssetResult = {
      ...request.directoryResult,
      result: ready,
    };
    request.asset = target;
    request.directoryResult = nextResult;
    cache.current.set(request.category, ready);
    resultRef.current = nextResult;
    setResult(nextResult);
    return target;
  }

  function generationHistoryImageAdoptionPort(
    request: AssetGenerationHistoryRequest,
  ): AssetGenerationHistoryImageAdoptionPort | undefined {
    const supportsUpdate = request.category === "characters"
      ? services.updateCharacter !== undefined
      : request.category === "scenes"
        ? services.updateScene !== undefined
        : services.updateProp !== undefined;
    if (!supportsUpdate) return undefined;

    const isOperationCurrent = (
      signal: AbortSignal,
      isHistoryCurrent: () => boolean,
    ) => !signal.aborted
      && isHistoryCurrent()
      && series.can_enter !== false
      && isCurrentGenerationHistory(request);

    const listFresh = async (
      signal: AbortSignal,
      isHistoryCurrent: () => boolean,
    ): Promise<AssetRecord[] | null> => {
      if (!isOperationCurrent(signal, isHistoryCurrent)) return null;
      const items = await readGenerationHistoryAssets(request.category, signal);
      if (!isOperationCurrent(signal, isHistoryCurrent)) return null;
      return items;
    };

    const findUniqueTarget = (items: AssetRecord[]): AssetRecord | null => {
      const matches = items.filter((item) => item.id === request.asset.id);
      const target = matches[0];
      return matches.length === 1
        && target !== undefined
        && target.series_id === request.scope.seriesId
        && hasValidCategoryName(request.category, target)
        && hasUniqueUsageIdentity(items, target, request.scope.seriesId)
        ? target
        : null;
    };

    const updateImage = async (imageUrl: string, signal: AbortSignal): Promise<void> => {
      switch (request.category) {
        case "characters": {
          const update = services.updateCharacter;
          if (update === undefined) throw new Error("角色图片更新接口不可用。");
          await update.call(services, request.scope.seriesId, request.asset.id, { image_url: imageUrl }, signal);
          return;
        }
        case "scenes": {
          const update = services.updateScene;
          if (update === undefined) throw new Error("场景图片更新接口不可用。");
          await update.call(services, request.scope.seriesId, request.asset.id, { image_url: imageUrl }, signal);
          return;
        }
        case "props": {
          const update = services.updateProp;
          if (update === undefined) throw new Error("道具图片更新接口不可用。");
          await update.call(services, request.scope.seriesId, request.asset.id, { image_url: imageUrl }, signal);
        }
      }
    };

    const definitelyRejected = (error: unknown): boolean => error instanceof ApiError
      && error.kind === "http"
      && error.status !== null
      && error.status >= 400
      && error.status < 500
      && error.status !== 408;

    return {
      async inspect(signal, isHistoryCurrent) {
        const items = await listFresh(signal, isHistoryCurrent);
        if (items === null) return { status: "not-current" };
        const target = findUniqueTarget(items);
        if (target === null) return { status: "conflict" };
        const expectedImageUrl = request.asset.image_url;
        publishGenerationHistoryAssets(request, items);
        if (target.image_url !== expectedImageUrl) return { status: "conflict" };
        return { status: "ready", imageUrl: target.image_url };
      },
      async apply(input, isHistoryCurrent) {
        const items = await listFresh(input.signal, isHistoryCurrent);
        if (items === null) return "not-current";
        const target = findUniqueTarget(items);
        if (target === null) return "conflict";
        if (target.image_url !== input.expectedImageUrl) {
          publishGenerationHistoryAssets(request, items);
          return target.image_url === input.imageUrl ? "already-applied" : "conflict";
        }
        if (target.image_url === input.imageUrl) {
          publishGenerationHistoryAssets(request, items);
          return "already-applied";
        }
        if (!isOperationCurrent(input.signal, isHistoryCurrent)) return "not-current";

        try {
          await updateImage(input.imageUrl, input.signal);
        } catch (error) {
          if (!isOperationCurrent(input.signal, isHistoryCurrent)) return "not-current";
          if (definitelyRejected(error)) throw error;
        }

        let updatedItems: AssetRecord[];
        try {
          const freshItems = await listFresh(input.signal, isHistoryCurrent);
          if (freshItems === null) return "not-current";
          updatedItems = freshItems;
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) throw error;
          if (!isOperationCurrent(input.signal, isHistoryCurrent)) return "not-current";
          return "unknown";
        }
        const updatedTarget = publishGenerationHistoryAssets(request, updatedItems);
        if (updatedTarget === null) return "unknown";
        return updatedTarget.image_url === input.imageUrl ? "applied" : "unknown";
      },
    };
  }

  function closeGenerationHistory(request: AssetGenerationHistoryRequest): void {
    if (generationHistoryRequestRef.current !== request) return;
    generationHistoryRequestRef.current = null;
    setGenerationHistoryRequest(null);
    openEpochRef.current += 1;
  }

  function openGenerationHistory(
    category: AssetLibraryType,
    item: AssetRecord,
    directoryItems: AssetRecord[],
  ): void {
    const activeResult = resultRef.current;
    const port = generationHistoryPort;
    if (
      series.can_enter === false
      || port === undefined
      || category !== selectedTypeRef.current
      || activeResult === null
      || activeResult.scope !== libraryScope
      || activeResult.result.status !== "ready"
      || activeResult.result.type !== category
      || libraryScopeRef.current !== libraryScope
      || directoryRequestGenerationRef.current !== activeResult.requestGeneration
      || !hasValidCategoryName(category, item)
      || !hasUniqueUsageIdentity(directoryItems, item, series.id)
    ) {
      return;
    }

    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    invalidateUsage();
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    const openEpoch = ++openEpochRef.current;
    const request: AssetGenerationHistoryRequest = {
      id: ++generationHistoryRequestIdRef.current,
      scope: libraryScope,
      category,
      asset: item,
      directoryResult: activeResult,
      port,
      openEpoch,
    };
    generationHistoryRequestRef.current = request;
    setGenerationHistoryRequest(request);
  }

  function openImageUpload(
    category: AssetLibraryType,
    item: AssetRecord,
    directoryItems: AssetRecord[],
  ): void {
    const activeResult = resultRef.current;
    const port = services.assetImageUpload;
    if (
      replacementRequestsRef.current.some((request) => request.writeInFlight)
      ||
      series.can_enter === false
      || port === undefined
      || category !== selectedTypeRef.current
      || activeResult === null
      || activeResult.scope !== libraryScope
      || activeResult.result.status !== "ready"
      || activeResult.result.type !== category
      || libraryScopeRef.current !== libraryScope
      || !hasValidCategoryName(category, item)
      || !hasUniqueUsageIdentity(directoryItems, item, series.id)
    ) {
      return;
    }

    const existing = imageUploadRequestsRef.current.find((request) => request.scope === libraryScope
      && request.category === category
      && request.asset.id === item.id
      && request.port === port
      && request.directoryResult === activeResult
      && request.run.isCurrent());
    if (existing !== undefined) {
      invalidateActiveImageUpload();
      invalidateActiveCharacterAudio();
      invalidateUsage();
      managementRequestRef.current = null;
      invalidatedManagementRequestRef.current = null;
      setManagementRequest(null);
      maintenanceRequestRef.current = null;
      setMaintenanceRequest(null);
      const openEpoch = ++openEpochRef.current;
      const nextRequest = { ...existing, asset: item, openEpoch };
      const nextRequests = imageUploadRequestsRef.current.map((request) => request.id === existing.id ? nextRequest : request);
      imageUploadRequestsRef.current = nextRequests;
      setImageUploadRequests(nextRequests);
      activeImageUploadIdRef.current = nextRequest.id;
      setActiveImageUploadId(nextRequest.id);
      setImageUploadIssue(null);
      return;
    }

    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    invalidateUsage();
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    resetAssetSearch();
    const previous = imageUploadRequestsRef.current.filter((request) => request.scope === libraryScope
      && request.category === category
      && request.asset.id === item.id
      && request.port === port).at(-1);
    const requestId = ++imageUploadRequestIdRef.current;
    const requestEpoch = ++openEpochRef.current;
    let runIsOpen = true;
    let request: AssetImageUploadRequest;
    try {
      const run = port.beginRun({
        actorId: userId,
        seriesId: series.id,
        category,
        assetId: item.id,
        directoryEpoch: activeResult.requestGeneration,
        portEpoch: requestId,
        asset: item,
        isContextCurrent: () => runIsOpen && isCurrentImageUploadContext(requestId),
      });
      request = {
        id: requestId,
        scope: libraryScope,
        category,
        asset: item,
        directoryResult: activeResult,
        port,
        run,
        attempts: previous?.attempts ?? [],
        openEpoch: requestEpoch,
        invalidateRun: () => { runIsOpen = false; },
      };
    } catch (error) {
      setImageUploadIssue(error instanceof Error ? error.message : "无法创建图片操作，请重新读取素材目录。");
      return;
    }
    const nextRequests = [...imageUploadRequestsRef.current, request];
    imageUploadRequestsRef.current = nextRequests;
    setImageUploadRequests(nextRequests);
    activeImageUploadIdRef.current = request.id;
    setActiveImageUploadId(request.id);
    setImageUploadIssue(null);
  }

  function openCharacterAudio(item: Character, directoryItems: Character[]): void {
    const activeResult = resultRef.current;
    const port = services.characterAudio;
    if (
      replacementRequestsRef.current.some((request) => request.writeInFlight)
      || series.can_enter === false
      || port === undefined
      || selectedTypeRef.current !== "characters"
      || activeResult === null
      || activeResult.scope !== libraryScope
      || activeResult.result.status !== "ready"
      || activeResult.result.type !== "characters"
      || libraryScopeRef.current !== libraryScope
      || !hasValidCategoryName("characters", item)
      || !hasUniqueUsageIdentity(directoryItems, item, series.id)
    ) {
      return;
    }

    const existing = characterAudioRequestsRef.current.find((request) => request.scope === libraryScope
      && request.category === "characters"
      && request.asset.id === item.id
      && request.port === port
      && request.directoryResult === activeResult
      && request.run.isCurrent());
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    invalidateUsage();
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    resetAssetSearch();

    if (existing !== undefined) {
      const openEpoch = ++openEpochRef.current;
      const nextRequest = { ...existing, asset: item, directoryResult: activeResult, openEpoch };
      const nextRequests = characterAudioRequestsRef.current.map((request) => request.id === existing.id
        ? nextRequest
        : request);
      characterAudioRequestsRef.current = nextRequests;
      setCharacterAudioRequests(nextRequests);
      activeCharacterAudioIdRef.current = nextRequest.id;
      setActiveCharacterAudioId(nextRequest.id);
      setCharacterAudioIssue(null);
      return;
    }

    const previous = characterAudioRequestsRef.current.filter((request) => request.scope === libraryScope
      && request.category === "characters"
      && request.asset.id === item.id
      && request.port === port).at(-1);
    const requestId = ++characterAudioRequestIdRef.current;
    const requestEpoch = ++openEpochRef.current;
    let request: CharacterAudioRequest;
    try {
      const run = port.beginRun({
        actorId: userId,
        seriesId: series.id,
        characterId: item.id,
        directoryEpoch: activeResult.requestGeneration,
        portEpoch: requestId,
        character: item,
        isContextCurrent: () => isCurrentCharacterAudioContext(requestId),
      });
      request = {
        id: requestId,
        scope: libraryScope,
        category: "characters",
        asset: item,
        directoryResult: activeResult,
        port,
        run,
        attempts: previous?.attempts ?? [],
        openEpoch: requestEpoch,
      };
    } catch (error) {
      setCharacterAudioIssue(error instanceof Error ? error.message : "无法创建音频操作，请重新读取角色目录。");
      return;
    }
    const nextRequests = [...characterAudioRequestsRef.current, request];
    characterAudioRequestsRef.current = nextRequests;
    setCharacterAudioRequests(nextRequests);
    activeCharacterAudioIdRef.current = request.id;
    setActiveCharacterAudioId(request.id);
    setCharacterAudioIssue(null);
  }

  function setImageUploadAttempts(
    request: AssetImageUploadRequest,
    requestEpoch: number,
    attempts: AssetImageUploadAttempt[],
  ): void {
    if (!isCurrentImageUpload(request, requestEpoch)) return;
    const nextRequests = imageUploadRequestsRef.current.map((candidate) => candidate.id === request.id
      ? { ...candidate, attempts }
      : candidate);
    imageUploadRequestsRef.current = nextRequests;
    setImageUploadRequests(nextRequests);
  }

  function acceptImageUploadDirectory(
    request: AssetImageUploadRequest,
    directory: AssetImageDirectory,
    requestEpoch: number,
  ): void {
    if (!isCurrentImageUpload(request, requestEpoch) || directory.category !== request.category) return;
    let ready: AssetReadyResult;
    switch (directory.category) {
      case "characters": ready = { type: "characters", status: "ready", items: directory.items }; break;
      case "scenes": ready = { type: "scenes", status: "ready", items: directory.items }; break;
      case "props": ready = { type: "props", status: "ready", items: directory.items }; break;
    }
    const requestGeneration = ++directoryRequestGenerationRef.current;
    cache.current.set(directory.category, ready);
    resetAssetSearch();
    usageOwnerRef.current = null;
    setUsageOwner(null);
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    setNavigationFeedback(null);
    const nextResult: ScopedAssetResult = {
      scope: request.scope,
      requestGeneration,
      navigationEpoch: null,
      result: ready,
    };
    const nextRequests = imageUploadRequestsRef.current.map((candidate) => candidate.id === request.id
      ? { ...candidate, asset: ready.items.find((item) => item.id === candidate.asset.id) ?? candidate.asset, directoryResult: nextResult }
      : candidate);
    imageUploadRequestsRef.current = nextRequests;
    setImageUploadRequests(nextRequests);
    resultRef.current = nextResult;
    setResult(nextResult);
  }

  function closeImageUpload(request: AssetImageUploadRequest, requestEpoch: number): void {
    if (!isCurrentImageUpload(request, requestEpoch)) return;
    invalidateActiveImageUpload();
  }

  function setCharacterAudioAttempts(
    request: CharacterAudioRequest,
    requestEpoch: number,
    attempts: CharacterAudioAttempt[],
  ): void {
    if (!isCurrentCharacterAudio(request, requestEpoch)) return;
    const nextRequests = characterAudioRequestsRef.current.map((candidate) => candidate.id === request.id
      ? { ...candidate, attempts }
      : candidate);
    characterAudioRequestsRef.current = nextRequests;
    setCharacterAudioRequests(nextRequests);
  }

  function acceptCharacterAudioDirectory(
    request: CharacterAudioRequest,
    directory: CharacterAudioDirectory,
    requestEpoch: number,
  ): void {
    if (!isCurrentCharacterAudio(request, requestEpoch) || directory.category !== "characters") return;
    const ready: AssetReadyResult = { type: "characters", status: "ready", items: directory.items };
    const requestGeneration = ++directoryRequestGenerationRef.current;
    cache.current.set("characters", ready);
    resetAssetSearch();
    usageOwnerRef.current = null;
    setUsageOwner(null);
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    setNavigationFeedback(null);
    const nextResult: ScopedAssetResult = {
      scope: request.scope,
      requestGeneration,
      navigationEpoch: null,
      result: ready,
    };
    const updatedCharacter = directory.items.find((item) => item.id === request.asset.id);
    const nextRequests = characterAudioRequestsRef.current.map((candidate) => candidate.id === request.id
      ? {
        ...candidate,
        asset: updatedCharacter ?? candidate.asset,
        directoryResult: nextResult,
      }
      : candidate);
    characterAudioRequestsRef.current = nextRequests;
    setCharacterAudioRequests(nextRequests);
    resultRef.current = nextResult;
    setResult(nextResult);
  }

  function closeCharacterAudio(request: CharacterAudioRequest, requestEpoch: number): void {
    if (!isCurrentCharacterAudio(request, requestEpoch)) return;
    invalidateActiveCharacterAudio();
  }

  function openReplacementRecord(request: AssetReplacementRequest): void {
    const current = replacementRequestsRef.current.find((candidate) => candidate.id === request.id);
    if (
      current === undefined
      || !current.hasAttempt
      || current.writeInFlight
      || current.scope !== libraryScope
      || current.category !== selectedTypeRef.current
      || current.port !== services.assetReplacement
      || series.can_enter === false
      || libraryScopeRef.current !== libraryScope
    ) {
      return;
    }

    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    invalidateUsage();
    managementRequestRef.current = null;
    invalidatedManagementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    resetAssetSearch();

    const nextRequest = { ...current, openEpoch: ++openEpochRef.current };
    const nextRequests = replacementRequestsRef.current.map((candidate) => candidate.id === current.id
      ? nextRequest
      : candidate);
    replacementRequestsRef.current = nextRequests;
    setReplacementRequests(nextRequests);
    activeReplacementIdRef.current = nextRequest.id;
    setActiveReplacementId(nextRequest.id);
  }

  function isCurrentReplacement(request: AssetReplacementRequest, requestEpoch: number): boolean {
    const currentRequest = replacementRequestsRef.current.find((candidate) => candidate.id === request.id);
    return currentRequest !== undefined
      && currentRequest.openEpoch === requestEpoch
      && activeReplacementIdRef.current === request.id
      && openEpochRef.current === requestEpoch
      && libraryScopeRef.current === request.scope
      && request.scope.userId === userId
      && request.scope.services === services
      && request.scope.seriesId === series.id
      && selectedTypeRef.current === request.category
      && series.can_enter !== false
      && services.assetReplacement === request.port;
  }

  function closeReplacement(request: AssetReplacementRequest, requestEpoch: number): void {
    if (!isCurrentReplacement(request, requestEpoch)) return;
    activeReplacementIdRef.current = null;
    setActiveReplacementId(null);
    openEpochRef.current += 1;
  }

  function setReplacementWriteInFlight(request: AssetReplacementRequest, requestEpoch: number, busy: boolean): void {
    const current = replacementRequestsRef.current.find((candidate) => candidate.id === request.id);
    if (current === undefined || current.openEpoch !== requestEpoch || current.writeInFlight === busy) return;
    const next = replacementRequestsRef.current.map((candidate) => candidate.id === request.id
      ? { ...candidate, writeInFlight: busy, hasAttempt: candidate.hasAttempt || busy }
      : candidate);
    replacementRequestsRef.current = next;
    setReplacementRequests(next);
  }

  function acceptReplacementDirectory(
    request: AssetReplacementRequest,
    items: AssetRecord[],
    requestEpoch: number,
  ): void {
    if (!isCurrentReplacement(request, requestEpoch)) return;
    let ready: AssetReadyResult;
    switch (request.category) {
      case "characters":
        ready = { type: "characters", status: "ready", items: items as Character[] };
        break;
      case "scenes":
        ready = { type: "scenes", status: "ready", items: items as Scene[] };
        break;
      case "props":
        ready = { type: "props", status: "ready", items: items as Prop[] };
        break;
    }
    const requestGeneration = ++directoryRequestGenerationRef.current;
    cache.current.set(request.category, ready);
    resetAssetSearch();
    setNavigationFeedback(null);
    const nextResult: ScopedAssetResult = {
      scope: request.scope,
      requestGeneration,
      navigationEpoch: null,
      result: ready,
    };
    request.directoryResult = nextResult;
    resultRef.current = nextResult;
    setResult(nextResult);
  }

  function isCurrentMaintenance(request: AssetMaintenanceRequest): boolean {
    return maintenanceRequestRef.current === request
      && libraryScopeRef.current === request.scope
      && request.scope.userId === userId
      && request.scope.services === services
      && request.scope.seriesId === series.id
      && selectedTypeRef.current === request.category
      && services.assetMaintenance === request.port;
  }

  function isCurrentMaintenanceDirectory(request: AssetMaintenanceRequest, directoryEpoch: number): boolean {
    const activeResult = resultRef.current;
    return isCurrentMaintenance(request)
      && directoryEpoch === request.directoryResult.requestGeneration
      && activeResult === request.directoryResult
      && activeResult.result.status === "ready"
      && activeResult.result.type === request.category
      && activeResult.result.items === request.directoryItems
      && directoryRequestGenerationRef.current === request.directoryResult.requestGeneration;
  }

  function closeMaintenance(request: AssetMaintenanceRequest): void {
    if (request.orphanActionsLocked || !isCurrentMaintenance(request)) return;
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
  }

  function invalidateMaintenanceDirectory(request: AssetMaintenanceRequest): number | null {
    if (!isCurrentMaintenance(request)) return null;
    request.directoryRefreshEpoch += 1;
    request.acceptedDirectoryReloadVersion = null;
    cache.current.delete(request.category);
    request.pendingDirectoryReloadVersion = reloadCurrentType();
    return request.directoryRefreshEpoch;
  }

  function acceptMaintenanceDirectory(
    request: AssetMaintenanceRequest,
    directory: AssetDirectory,
    refreshEpoch: number,
  ): void {
    if (!isCurrentMaintenance(request)
      || request.directoryRefreshEpoch !== refreshEpoch
      || directory.category !== request.category) return;
    const requestGeneration = ++directoryRequestGenerationRef.current;
    let ready: AssetReadyResult;
    switch (directory.category) {
      case "characters":
        ready = { type: "characters", status: "ready", items: directory.items };
        break;
      case "scenes":
        ready = { type: "scenes", status: "ready", items: directory.items };
        break;
      case "props":
        ready = { type: "props", status: "ready", items: directory.items };
        break;
    }
    cache.current.set(directory.category, ready);
    resetAssetSearch();
    invalidateUsage();
    setNavigationFeedback(null);
    const nextResult: ScopedAssetResult = {
      scope: request.scope,
      requestGeneration,
      navigationEpoch: null,
      result: ready,
    };
    request.directoryResult = nextResult;
    request.directoryItems = directory.items;
    request.acceptedDirectoryReloadVersion = request.pendingDirectoryReloadVersion;
    request.pendingDirectoryReloadVersion = null;
    resultRef.current = nextResult;
    setResult(nextResult);
  }

  function locateMaintenanceAsset(request: AssetMaintenanceRequest, item: AssetRecord, directoryEpoch: number): void {
    if (request.orphanActionsLocked
      || !isCurrentMaintenanceDirectory(request, directoryEpoch)
      || !hasUniqueUsageIdentity(request.directoryItems, item, series.id)) return;
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    resetAssetSearch();
    setPendingLocateAsset({ assetId: item.id, scope: request.scope, category: request.category });
  }

  function openOrphanDelete(request: AssetMaintenanceRequest, item: AssetRecord, directoryEpoch: number): void {
    if (request.orphanActionsLocked
      || !isCurrentMaintenanceDirectory(request, directoryEpoch)
      || !hasUniqueUsageIdentity(request.directoryItems, item, series.id)) return;
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
    resetAssetSearch();
    openManagement("delete", request.category, item, request.directoryItems);
  }

  function isCurrentManagement(request: AssetManagementRequest): boolean {
    return managementRequestRef.current === request
      && libraryScopeRef.current === request.scope
      && request.scope.userId === userId
      && request.scope.services === services
      && request.scope.seriesId === series.id
      && selectedTypeRef.current === request.category;
  }

  function closeManagement(request: AssetManagementRequest) {
    if (!isCurrentManagement(request)) {
      return;
    }
    const needsFreshRead = invalidatedManagementRequestRef.current === request;
    invalidatedManagementRequestRef.current = null;
    managementRequestRef.current = null;
    setManagementRequest(null);
    if (needsFreshRead) {
      reloadCurrentType();
    }
  }

  function invalidateManagedDirectory(request: AssetManagementRequest) {
    if (!isCurrentManagement(request)) {
      return;
    }
    invalidatedManagementRequestRef.current = request;
    cache.current.delete(request.category);
    resultRef.current = null;
    reloadCurrentType();
  }

  function acceptManagedDirectory(request: AssetManagementRequest, directory: AssetDirectory) {
    if (!isCurrentManagement(request) || directory.category !== request.category) {
      return;
    }
    if (invalidatedManagementRequestRef.current === request) {
      invalidatedManagementRequestRef.current = null;
    }
    const requestGeneration = ++directoryRequestGenerationRef.current;
    let ready: AssetReadyResult;
    switch (directory.category) {
      case "characters":
        ready = { type: "characters", status: "ready", items: directory.items };
        break;
      case "scenes":
        ready = { type: "scenes", status: "ready", items: directory.items };
        break;
      case "props":
        ready = { type: "props", status: "ready", items: directory.items };
        break;
    }
    cache.current.set(directory.category, ready);
    resetAssetSearch();
    invalidateUsage();
    setNavigationFeedback(null);
    const nextResult: ScopedAssetResult = {
      scope: request.scope,
      requestGeneration,
      navigationEpoch: null,
      result: ready,
    };
    resultRef.current = nextResult;
    setResult(nextResult);
  }

  function reloadCurrentType(): number {
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    generationHistoryRequestRef.current = null;
    setGenerationHistoryRequest(null);
    const nextReloadVersion = reloadVersionRef.current + 1;
    reloadVersionRef.current = nextReloadVersion;
    directoryRequestGenerationRef.current += 1;
    resetAssetSearch();
    invalidateUsage();
    setNavigationFeedback(null);
    setNavigationIssue(null);
    cache.current.delete(selectedType);
    const loadingResult: ScopedAssetResult = {
      scope: libraryScope,
      requestGeneration: directoryRequestGenerationRef.current,
      navigationEpoch: navigationIntentRef.current?.navigationEpoch ?? null,
      result: { type: selectedType, status: "loading" },
    };
    resultRef.current = loadingResult;
    setResult(loadingResult);
    setFailedImages((previous) => {
      const next = new Set(previous);
      for (const key of next) {
        if (key.startsWith(libraryScope.generation + ":" + selectedType + ":")) {
          next.delete(key);
        }
      }
      return next;
    });
    setReloadVersion(nextReloadVersion);
    return nextReloadVersion;
  }

  function goBack() {
    invalidateActiveImageUpload();
    invalidateActiveCharacterAudio();
    generationHistoryRequestRef.current = null;
    setGenerationHistoryRequest(null);
    abandonNavigationIntent();
    invalidateUsage();
    invalidatedManagementRequestRef.current = null;
    managementRequestRef.current = null;
    setManagementRequest(null);
    maintenanceRequestRef.current = null;
    setMaintenanceRequest(null);
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
    const replacementUnavailable = !identityIsUnique
      ? "素材身份暂无法核对，不能替换章节引用。"
        : series.can_enter === false
        ? "当前账号不能进入此剧集，不能替换章节引用。"
        : services.assetReplacement === undefined
          ? services.mode === "demo" ? "演示模式暂未接入素材替换服务。" : "当前服务暂未接入素材替换接口。"
          : undefined;
    const imageUploadUnavailable = !identityIsUnique
      ? "素材身份暂无法核对，不能更新图片。"
      : series.can_enter === false
        ? "当前账号不能进入此剧集，不能更新图片。"
        : replacementRequestsRef.current.some((request) => request.writeInFlight)
          ? "章节素材替换仍在进行，暂不能更新图片。"
        : services.assetImageUpload === undefined
          ? services.mode === "demo" ? "演示模式暂未接入图片上传服务。" : "当前服务暂未接入图片上传接口。"
          : undefined;
    const characterAudioUnavailable = category !== "characters"
      ? undefined
      : !identityIsUnique
        ? "角色身份暂无法核对，不能维护角色音频。"
        : series.can_enter === false
          ? "当前账号不能进入此剧集，不能维护角色音频。"
          : replacementRequestsRef.current.some((request) => request.writeInFlight)
            ? "章节素材替换仍在进行，暂不能维护角色音频。"
            : services.characterAudio === undefined
              ? services.mode === "demo" ? "演示模式暂未接入角色音频服务。" : "当前服务暂未接入角色音频接口。"
              : undefined;
    const generationHistoryUnavailable = !identityIsUnique
      ? "素材身份暂无法核对，不能读取生成历史。"
      : series.can_enter === false
        ? "当前账号不能进入此剧集，不能读取生成历史。"
        : generationHistoryPort === undefined
          ? services.mode === "demo" ? "演示模式暂未接入生成历史服务。" : "当前服务暂未接入生成历史接口。"
          : undefined;
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
        onReplace={replacementUnavailable === undefined ? () => openReplacement(category, item, items) : undefined}
        replacementUnavailable={replacementUnavailable}
        onUploadImage={imageUploadUnavailable === undefined ? () => openImageUpload(category, item, items) : undefined}
        imageUploadUnavailable={imageUploadUnavailable}
        onGenerationHistory={generationHistoryUnavailable === undefined
          ? () => openGenerationHistory(category, item, items)
          : undefined}
        generationHistoryUnavailable={generationHistoryUnavailable}
        onCharacterAudio={category === "characters" && characterAudioUnavailable === undefined
          ? () => openCharacterAudio(item as Character, items as Character[])
          : undefined}
        characterAudioUnavailable={characterAudioUnavailable}
        onEdit={identityIsUnique ? () => openManagement("edit", category, item, items) : undefined}
        onDelete={identityIsUnique ? () => openManagement("delete", category, item, items) : undefined}
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

  useEffect(() => {
    if (managementRequest !== null && managementRequest.scope !== libraryScope) {
      managementRequestRef.current = null;
      setManagementRequest(null);
    }
  }, [libraryScope, managementRequest]);

  useEffect(() => {
    if (generationHistoryRequest !== null && !isCurrentGenerationHistory(generationHistoryRequest)) {
      generationHistoryRequestRef.current = null;
      setGenerationHistoryRequest(null);
      openEpochRef.current += 1;
    }
  }, [currentResult, generationHistoryPort, generationHistoryRequest, libraryScope, selectedType, series.can_enter, series.id, services, userId]);

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

  useLayoutEffect(() => {
    if (pendingLocateAsset === null) return;
    if (pendingLocateAsset.scope !== libraryScope || pendingLocateAsset.category !== selectedType) {
      setPendingLocateAsset(null);
      return;
    }
    if (currentResult === null || currentResult.status !== "ready" || currentResult.type !== pendingLocateAsset.category) {
      return;
    }
    const matches = currentResult.items.filter((item) => item.id === pendingLocateAsset.assetId);
    const element = assetCardElementsRef.current.get(pendingLocateAsset.assetId);
    if (matches.length === 1 && element !== undefined && element.isConnected && element.closest("[hidden], [inert]") === null) {
      element.focus({ preventScroll: true });
      element.scrollIntoView?.({ behavior: "auto", block: "center" });
    }
    setPendingLocateAsset(null);
  }, [currentResult, libraryScope, pendingLocateAsset, selectedType]);

  return (
    <section className="asset-library" aria-labelledby="asset-library-heading" ref={libraryElementRef}>
      <div className="asset-library-heading">
        <div>
          <p className="eyebrow">素材目录 · 手工维护</p>
          <h1 id="asset-library-heading">{series.name} · 素材库</h1>
          <p>查看并维护剧集已有的角色、场景与道具文字资料。</p>
        </div>
        <div className="asset-library-actions">
          <button
            className="secondary-button"
            disabled={currentResult?.status !== "ready" || services.assetMaintenance === undefined}
            onClick={openMaintenance}
            type="button"
          >
            整理素材
          </button>
          {replacementRequests
            .filter((request) => request.hasAttempt
              && request.scope === libraryScope
              && request.category === selectedType
              && request.port === services.assetReplacement)
            .map((request) => (
              <button
                className="secondary-button"
                disabled={request.writeInFlight || series.can_enter === false}
                key={request.id}
                onClick={() => openReplacementRecord(request)}
                type="button"
              >
                查看替换记录：{assetName(request.category, request.asset)}（{request.asset.id}）
              </button>
            ))}
          <button
            className="primary-button"
            disabled={currentResult?.status !== "ready"}
            onClick={() => {
              if (currentResult?.status === "ready") {
                openManagement("create", selectedType, null, currentResult.items);
              }
            }}
            type="button"
          >
            新增{assetTypes.find((type) => type.id === selectedType)?.label}
          </button>
          <button className="secondary-button asset-refresh-button" onClick={reloadCurrentType} type="button">
            重新读取
          </button>
          <button className="secondary-button chapter-back-button" onClick={goBack} type="button">
            <span aria-hidden="true">←</span> 返回剧集列表
          </button>
        </div>
      </div>
      {services.assetMaintenance === undefined && currentResult?.status === "ready" && (
        <p className="asset-maintenance-unavailable" role="note">
          {services.mode === "demo" ? "演示模式暂未接入素材维护服务。" : "当前服务暂未接入素材维护接口。"}
        </p>
      )}
      {imageUploadIssue !== null && (
        <p className="asset-image-upload-issue" role="alert">{imageUploadIssue}</p>
      )}
      {characterAudioIssue !== null && (
        <p className="asset-image-upload-issue" role="alert">{characterAudioIssue}</p>
      )}

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
          <p>可以用右上角的新增按钮创建第一项素材。</p>
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
      {managementRequest !== null
        && managementRequest.scope === libraryScope
        && managementRequest.category === selectedType && (
          <AssetManagementDialog
            category={managementRequest.category}
            isCurrent={() => isCurrentManagement(managementRequest)}
            item={managementRequest.item}
            key={managementRequest.id}
            mode={managementRequest.mode}
            onClose={() => closeManagement(managementRequest)}
            onDirectoryUpdated={(directory) => acceptManagedDirectory(managementRequest, directory)}
            onDirectoryNeedsRefresh={() => invalidateManagedDirectory(managementRequest)}
            onUnauthorized={() => {
              if (isCurrentManagement(managementRequest)) {
                unauthorizedHandlerRef.current();
              }
            }}
            seriesId={series.id}
            seriesName={series.name}
            services={services}
          />
        )}
      {maintenanceRequest !== null
        && maintenanceRequest.scope === libraryScope
        && maintenanceRequest.category === selectedType
        && services.assetMaintenance === maintenanceRequest.port && (
          <AssetMaintenanceDialog
            apiBaseUrl={services.apiBaseUrl}
            category={maintenanceRequest.category}
            directoryItems={maintenanceRequest.directoryItems}
            directoryEpoch={maintenanceRequest.directoryResult.requestGeneration}
            isCurrent={() => isCurrentMaintenance(maintenanceRequest)}
            isDirectoryCurrent={(items, directoryEpoch) => isCurrentMaintenanceDirectory(maintenanceRequest, directoryEpoch)
              && items === maintenanceRequest.directoryItems}
            key={maintenanceRequest.id}
            onClose={() => closeMaintenance(maintenanceRequest)}
            onDirectoryNeedsRefresh={() => invalidateMaintenanceDirectory(maintenanceRequest)}
            onDirectoryUpdated={(directory, refreshEpoch) => acceptMaintenanceDirectory(maintenanceRequest, directory, refreshEpoch)}
            onOrphanActionsLockedChange={(locked) => {
              if (maintenanceRequestRef.current === maintenanceRequest) {
                maintenanceRequest.orphanActionsLocked = locked;
              }
            }}
            onLocateAsset={(item, directoryEpoch) => locateMaintenanceAsset(maintenanceRequest, item, directoryEpoch)}
            onOpenDelete={(item, directoryEpoch) => openOrphanDelete(maintenanceRequest, item, directoryEpoch)}
            onUnauthorized={() => {
              if (isCurrentMaintenance(maintenanceRequest)) unauthorizedHandlerRef.current();
            }}
            port={maintenanceRequest.port}
            seriesId={series.id}
            seriesName={series.name}
            services={services}
            userId={userId}
          />
        )}
      {replacementRequests.filter((request) => request.scope === libraryScope
        && services.assetReplacement === request.port).map((request) => (
        <AssetReplacementDialog
          asset={request.asset}
          canEnter={series.can_enter !== false}
          category={request.category}
          epoch={request.openEpoch}
          isCurrent={(requestEpoch) => isCurrentReplacement(request, requestEpoch)}
          key={request.id}
          onClose={(requestEpoch) => closeReplacement(request, requestEpoch)}
          onBusyChange={(requestEpoch, busy) => setReplacementWriteInFlight(request, requestEpoch, busy)}
          onDirectoryUpdated={(items, requestEpoch) => acceptReplacementDirectory(request, items, requestEpoch)}
          onUnauthorized={(requestEpoch) => {
            if (isCurrentReplacement(request, requestEpoch)) unauthorizedHandlerRef.current();
          }}
          open={activeReplacementId === request.id && selectedType === request.category && series.can_enter !== false}
          seriesId={series.id}
          seriesName={series.name}
          services={services}
        />
      ))}
      {imageUploadRequests.filter((request) => request.id === activeImageUploadId
        && request.scope === libraryScope
        && request.category === selectedType
        && request.port === services.assetImageUpload).map((request) => (
        <AssetImageUploadDialog
          allocateAttemptId={() => ++imageUploadAttemptIdRef.current}
          asset={request.asset}
          attempts={request.attempts}
          category={request.category}
          isCurrent={() => isCurrentImageUpload(request, request.openEpoch)}
          key={request.id}
          onAttemptsChange={(attempts) => setImageUploadAttempts(request, request.openEpoch, attempts)}
          onClose={() => closeImageUpload(request, request.openEpoch)}
          onDirectoryUpdated={(directory) => acceptImageUploadDirectory(request, directory, request.openEpoch)}
          onUnauthorized={() => {
            if (isCurrentImageUpload(request, request.openEpoch)) unauthorizedHandlerRef.current();
          }}
          run={request.run}
          runId={request.id}
          seriesName={series.name}
        />
      ))}
      {characterAudioRequests.filter((request) => request.id === activeCharacterAudioId
        && request.scope === libraryScope
        && request.category === "characters"
        && request.port === services.characterAudio).map((request) => (
        <CharacterAudioDialog
          allocateAttemptId={() => ++characterAudioAttemptIdRef.current}
          attempts={request.attempts}
          character={request.asset}
          isCurrent={() => isCurrentCharacterAudio(request, request.openEpoch)}
          key={request.id}
          onAttemptsChange={(attempts) => setCharacterAudioAttempts(request, request.openEpoch, attempts)}
          onClose={() => closeCharacterAudio(request, request.openEpoch)}
          onDirectoryUpdated={(directory) => acceptCharacterAudioDirectory(request, directory, request.openEpoch)}
          onUnauthorized={() => {
            if (isCurrentCharacterAudio(request, request.openEpoch)) unauthorizedHandlerRef.current();
          }}
          run={request.run}
          runId={request.id}
          seriesName={series.name}
        />
      ))}
      {generationHistoryRequest !== null
        && generationHistoryRequest.scope === libraryScope
        && generationHistoryRequest.category === selectedType
        && generationHistoryRequest.port === generationHistoryPort
        && isCurrentGenerationHistory(generationHistoryRequest) && (
        <AssetGenerationHistoryDialog
          actorId={userId}
          apiBaseUrl={services.apiBaseUrl}
          assetId={generationHistoryRequest.asset.id}
          assetName={assetName(generationHistoryRequest.category, generationHistoryRequest.asset)}
          assetType={generationHistoryAssetType(generationHistoryRequest.category)}
          isCurrent={() => isCurrentGenerationHistory(generationHistoryRequest)}
          key={generationHistoryRequest.id}
          listChapters={services.listChapters}
          onClose={() => closeGenerationHistory(generationHistoryRequest)}
          onUnauthorized={() => {
            if (isCurrentGenerationHistory(generationHistoryRequest)) unauthorizedHandlerRef.current();
          }}
          imageAdoptionPort={generationHistoryImageAdoptionPort(generationHistoryRequest)}
          port={generationHistoryRequest.port}
          seriesId={series.id}
          seriesName={series.name}
        />
      )}
    </section>
  );
}
