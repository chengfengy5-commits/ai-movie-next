import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";
import type { Chapter, ChapterEditLockLeaseHandle, Series } from "../../shared/api/contracts";
import { isRecord, type CanvasDataPort } from "../../shared/api/canvasData";
import { ApiError } from "../../shared/api/errors";
import {
  CanvasGenerationApplicationError,
  type CanvasGenerationApplicationCandidate,
  type CanvasGenerationApplicationDestination,
} from "../../shared/api/canvasGenerationApplications";
import type { CanvasOwnedImageRun } from "../../shared/api/canvasOwnedImage";
import { CanvasOwnedResultsError, type CanvasOwnedResultItem, type CanvasOwnedResultsTarget } from "../../shared/api/canvasOwnedResults";
import { CanvasResultApplicationError, type CanvasResultApplicationLookup } from "../../shared/api/canvasResultApplications";
import {
  CANVAS_SOURCE_CAPABILITY,
  CanvasSourceError,
  type CanvasAssetKind,
  type CanvasCreationReceipt,
  type CanvasFrameTarget,
  type CanvasSourceCommand,
  type CanvasSourceSnapshot,
  resolveCanvasFrameGenerationDestination,
  resolveCanvasFrameTarget,
} from "../../shared/api/canvasSources";
import type {
  CharacterCreateInput,
  CharacterUpdateInput,
  PropCreateInput,
  PropUpdateInput,
  SceneCreateInput,
  SceneUpdateInput,
} from "../../shared/api/assetManagement";
import type { WorkspaceServices } from "../../shared/api/services";
import { FrameTextEditor } from "../chapters/FrameTextEditor";
import type { FrameTextSaveResult } from "../chapters/FrameTextEditor";
import { FrameStructureEditor, type FrameStructureMode, type FrameStructureSubmitResult } from "../chapters/frame-structure/FrameStructureEditor";
import type { FrameStructureDraft } from "../chapters/frame-structure/contract";
import { AssetManagementDialog, type AssetDirectory, type GuardedAssetMutation } from "../assets/management/AssetManagementDialog";
import { CanvasMediaSourceEditor } from "./CanvasMediaSourceEditor";
import { CanvasGenerationResultsDialog, type PendingCanvasGenerationIntent } from "./generation-results/CanvasGenerationResultsDialog";
import { CanvasImageGenerationDialog } from "./image-generation/CanvasImageGenerationDialog";
import {
  useCanvasImageGenerationController,
  type CanvasImageGenerationLockResult,
  type CanvasImageGenerationTarget,
} from "./image-generation/useCanvasImageGeneration";
import { useSeriesDialogFocus } from "../series/useSeriesDialogFocus";
import { CanvasMediaPreview } from "./CanvasMediaPreview";
import {
  addCanvasItem,
  canvasViewport,
  documentEqual,
  patchCanvasItem,
  patchCanvasViewport,
  patchHiddenRelation,
  hasCanvasLayout,
  patchNodeLayout,
  removeCanvasItem,
  type CanvasDocument,
  type CanvasViewport,
} from "./canvasDocument";
import { buildCanvasGraph, type CanvasGraphNode, type CanvasNodeKind } from "./canvasGraph";
import { bindCanvasSourceDocumentTransition, type CanvasSourceTransition } from "./canvasSourceBindings";
import { EMPTY_CANVAS_HISTORY, pushCanvasHistory, redoCanvasHistory, undoCanvasHistory, type CanvasHistory } from "./canvasHistory";
import { useCanvasDocument } from "./useCanvasDocument";
import "./canvas.css";

type CanvasSourceEditor =
  | { kind: "frame-text"; node: CanvasGraphNode; frozen: CanvasSourceSnapshot; lifecycle: AbortController }
  | { kind: "frame-structure"; mode: FrameStructureMode; frozen: CanvasSourceSnapshot; lifecycle: AbortController }
  | {
      kind: "asset";
      mode: "create" | "edit";
      assetKind: CanvasAssetKind;
      item: CanvasSourceSnapshot["characters"][number] | CanvasSourceSnapshot["scenes"][number]
        | CanvasSourceSnapshot["props"][number] | null;
      frozen: CanvasSourceSnapshot;
      lifecycle: AbortController;
    }
  | {
      kind: "media";
      target: CanvasFrameTarget;
      field: "preview" | "video";
      slot: number | null;
      initialValue: string;
      append: boolean;
      frozen: CanvasSourceSnapshot;
      lifecycle: AbortController;
    };

type WithoutLifecycle<T> = T extends { lifecycle: AbortController } ? Omit<T, "lifecycle"> : never;

interface UnknownSourceIntent {
  kind: "unknown" | "conflict";
  command: CanvasSourceCommand;
  frozen: CanvasSourceSnapshot;
  frozenVersion: string;
  verifiedSnapshot: CanvasSourceSnapshot | null;
}

interface PendingSourceAcknowledgement {
  command: CanvasSourceCommand;
  frozen: CanvasSourceSnapshot;
  acknowledged: CanvasSourceSnapshot;
  receipt: CanvasCreationReceipt | null;
}

interface CanvasCreationUndo {
  id: string;
  sourceVersion: string;
  receipt: CanvasCreationReceipt;
  label: string;
}

interface OwnedAssetPlacement {
  ref: string;
  x: number;
  y: number;
}

interface CanvasGenerationDialogModel {
  id: number;
  selectedRef: string | null;
  target: CanvasOwnedResultsTarget;
  targetLabel: string;
  sourceVersion: string | null;
  requiresLock: boolean;
  items: readonly CanvasOwnedResultItem[];
  cursor: string | undefined;
  nextCursor: string | null;
  loading: boolean;
  error: string | null;
  message: string | null;
}

interface WorkspaceGenerationIntent extends PendingCanvasGenerationIntent {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly sessionGeneration: number;
  readonly scopeKey: string;
  readonly workspaceEpoch: number;
  readonly sourceEpoch: number;
  readonly servicesIdentity: object;
  readonly requiresLock: boolean;
}

interface GenerationWorkspaceScope {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly sessionGeneration: number;
  readonly scopeKey: string;
  readonly workspaceEpoch: number;
  readonly sourceEpoch: number;
  readonly servicesIdentity: object;
  readonly dialogId?: number;
  readonly selectedRef?: string | null;
  readonly sourceVersion?: string;
}

interface ConfirmedGenerationProof {
  readonly resultRef: string;
  readonly status: "applied" | "already_applied";
  readonly applicationRef: string;
  readonly targetRevision: string;
  readonly committedAt: string;
}

type CanvasSourceWriteOutcome =
  | { status: "saved"; snapshot: CanvasSourceSnapshot; receipt: CanvasCreationReceipt | null }
  | { status: "conflict"; message: string }
  | { status: "unknown"; message: string };

function frameText(frame: unknown): string {
  if (frame === null || typeof frame !== "object" || Array.isArray(frame)) return "";
  const record = frame as Record<string, unknown>;
  return typeof record.text === "string" ? record.text
    : typeof record.original_text === "string" ? record.original_text : "";
}

function asCanvasSourceError(cause: unknown): CanvasSourceError {
  if (cause instanceof CanvasSourceError) return cause;
  if (cause instanceof ApiError) {
    if (cause.status === 401) return new CanvasSourceError("unauthorized", cause.message, cause.status);
    if (cause.status === 409) return new CanvasSourceError("conflict", cause.message, cause.status);
    if (cause.kind === "timeout") return new CanvasSourceError("timeout", cause.message, cause.status);
    if (cause.kind === "network") return new CanvasSourceError("network", cause.message, cause.status);
    if (cause.kind === "aborted") return new CanvasSourceError("aborted", cause.message, cause.status);
    return new CanvasSourceError("rejected", cause.message, cause.status);
  }
  return new CanvasSourceError("rejected", cause instanceof Error ? cause.message : "来源操作未完成。");
}

function lockIsOwnedBy(lock: { locked: boolean; user_id?: string; is_mine?: boolean }, userId: string): boolean {
  return lock.locked && lock.is_mine === true && lock.user_id === userId;
}

function validCurrentLock(lock: { locked: boolean; user_id?: string; is_mine?: boolean; expires_at?: string }, userId: string): boolean {
  if (!lockIsOwnedBy(lock, userId) || lock.expires_at === undefined) return false;
  const expiry = Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/iu.test(lock.expires_at) ? lock.expires_at : `${lock.expires_at}Z`);
  return Number.isFinite(expiry) && expiry > Date.now();
}

function assetCollection(snapshot: CanvasSourceSnapshot, kind: CanvasAssetKind) {
  if (kind === "character") return snapshot.characters;
  if (kind === "scene") return snapshot.scenes;
  return snapshot.props;
}

function characterCreateValues(values: Record<string, string>): CharacterCreateInput {
  return {
    name: values.name?.trim() ?? "",
    gender: values.gender ?? "",
    age: values.age ?? "",
    role: values.role ?? "",
    appearance: values.appearance ?? "",
    description: values.description ?? "",
    image_url: values.image_url ?? "",
  };
}

function characterUpdateValues(values: Record<string, string>): CharacterUpdateInput {
  return {
    ...(values.name === undefined ? {} : { name: values.name.trim() }),
    ...(values.gender === undefined ? {} : { gender: values.gender }),
    ...(values.age === undefined ? {} : { age: values.age }),
    ...(values.role === undefined ? {} : { role: values.role }),
    ...(values.appearance === undefined ? {} : { appearance: values.appearance }),
    ...(values.description === undefined ? {} : { description: values.description }),
    ...(values.image_url === undefined ? {} : { image_url: values.image_url }),
  };
}

function sceneCreateValues(values: Record<string, string>): SceneCreateInput {
  return {
    title: values.title?.trim() ?? "",
    description: values.description ?? "",
    image_url: values.image_url ?? "",
  };
}

function sceneUpdateValues(values: Record<string, string>): SceneUpdateInput {
  return {
    ...(values.title === undefined ? {} : { title: values.title.trim() }),
    ...(values.description === undefined ? {} : { description: values.description }),
    ...(values.image_url === undefined ? {} : { image_url: values.image_url }),
  };
}

function propCreateValues(values: Record<string, string>): PropCreateInput {
  return {
    name: values.name?.trim() ?? "",
    description: values.description ?? "",
    image_url: values.image_url ?? "",
  };
}

function propUpdateValues(values: Record<string, string>): PropUpdateInput {
  return {
    ...(values.name === undefined ? {} : { name: values.name.trim() }),
    ...(values.description === undefined ? {} : { description: values.description }),
    ...(values.image_url === undefined ? {} : { image_url: values.image_url }),
  };
}

function assetKindForCategory(category: AssetDirectory["category"]): CanvasAssetKind {
  return category === "characters" ? "character" : category === "scenes" ? "scene" : "prop";
}

function assetCategoryForKind(kind: CanvasAssetKind): AssetDirectory["category"] {
  return kind === "character" ? "characters" : kind === "scene" ? "scenes" : "props";
}

function sourceFrameTarget(snapshot: CanvasSourceSnapshot, index: number): CanvasFrameTarget | null {
  return resolveCanvasFrameTarget(snapshot.chapter, snapshot.storyboard_assets, index);
}

function isValidGenerationAssetId(value: string): boolean {
  const characterCount = Array.from(value).length;
  return characterCount >= 1 && characterCount <= 36 && value.trim() === value
    && !/[\u0000-\u001f\u007f-\u009f]/u.test(value)
    && !value.includes("/") && !value.includes("\\") && value !== "." && value !== "..";
}

function generationTargetForNode(
  snapshot: CanvasSourceSnapshot,
  node: CanvasGraphNode,
): { target: CanvasOwnedResultsTarget; label: string } | null {
  if (snapshot.chapter_id !== snapshot.chapter.id || snapshot.series_id !== snapshot.chapter.series_id) return null;

  const frameIndex = node.frameIndex;
  if ((node.kind === "frame" || node.kind === "preview") && frameIndex !== undefined
    && Number.isSafeInteger(frameIndex) && frameIndex >= 0) {
    const target = resolveCanvasFrameGenerationDestination(snapshot, frameIndex);
    if (target === null) return null;
    const label = node.kind === "preview"
      ? `分镜 ${frameIndex + 1} 预览`
      : `分镜 ${frameIndex + 1}`;
    return { target, label };
  }

  if (node.kind !== "character" && node.kind !== "scene" && node.kind !== "prop") return null;
  if (node.itemId === undefined || !isValidGenerationAssetId(node.itemId)) return null;
  const matches = assetCollection(snapshot, node.kind).filter((asset) => asset.id === node.itemId
    && asset.series_id === snapshot.series_id);
  if (matches.length !== 1) return null;
  return {
    target: {
      kind: "asset_image",
      chapter_id: snapshot.chapter_id,
      asset_type: node.kind,
      asset_id: node.itemId,
    },
    label: node.kind === "character" ? `角色素材 · ${node.title}`
      : node.kind === "scene" ? `场景素材 · ${node.title}` : `道具素材 · ${node.title}`,
  };
}

function imageGenerationTargetKey(
  selectedRef: string,
  target: CanvasOwnedResultsTarget,
  sourceVersion: string,
): string {
  return JSON.stringify([selectedRef, sourceVersion, target]);
}

function imageGenerationPromptSeed(snapshot: CanvasSourceSnapshot, node: CanvasGraphNode): string {
  if ((node.kind === "frame" || node.kind === "preview") && node.frameIndex !== undefined) {
    return frameText(snapshot.chapter.content?.[node.frameIndex]);
  }
  if ((node.kind === "character" || node.kind === "scene" || node.kind === "prop") && node.itemId !== undefined) {
    const matches = assetCollection(snapshot, node.kind).filter((asset) => asset.id === node.itemId
      && asset.series_id === snapshot.series_id);
    if (matches.length !== 1) return "";
    return matches[0]?.description?.trim() || node.title;
  }
  return "";
}

function sameGenerationTarget(left: CanvasOwnedResultsTarget, right: CanvasOwnedResultsTarget): boolean {
  if (left.kind !== right.kind || left.chapter_id !== right.chapter_id) return false;
  if (left.kind === "asset_image" && right.kind === "asset_image") {
    return left.asset_type === right.asset_type && left.asset_id === right.asset_id;
  }
  if (left.kind !== "frame_preview" || right.kind !== "frame_preview") return false;
  if ("storyboard_asset_id" in left && "storyboard_asset_id" in right) {
    return left.storyboard_asset_id === right.storyboard_asset_id;
  }
  return "identity_mode" in left && "identity_mode" in right
    && left.identity_mode === right.identity_mode
    && left.frame_index === right.frame_index
    && left.expected_chapter_digest === right.expected_chapter_digest;
}

function generationTargetLabel(target: CanvasOwnedResultsTarget): string {
  if (target.kind === "asset_image") {
    const kind = target.asset_type === "character" ? "角色素材"
      : target.asset_type === "scene" ? "场景素材" : "道具素材";
    return `${kind}图片`;
  }
  return "分镜图片";
}

function generationOutcomeLabel(status: "applied" | "already_applied"): string {
  return status === "applied" ? "应用成功" : "此前已应用";
}

function generationFailureMessage(cause: unknown): string {
  if (cause instanceof CanvasOwnedResultsError || cause instanceof CanvasGenerationApplicationError
    || cause instanceof CanvasResultApplicationError || cause instanceof CanvasSourceError || cause instanceof ApiError) {
    return cause.message;
  }
  return cause instanceof Error ? cause.message : "生成图片记录操作失败。";
}

interface DragState {
  pointerId: number;
  startX: number;
  startY: number;
  node: CanvasGraphNode | null;
  nodeX: number;
  nodeY: number;
  viewport: CanvasViewport;
  startDocument: CanvasDocument | null;
}

const FILTERS: Array<{ id: "all" | CanvasNodeKind; label: string }> = [
  { id: "all", label: "全部" }, { id: "character", label: "角色" },
  { id: "scene", label: "场景" }, { id: "prop", label: "道具" },
  { id: "frame", label: "分镜" }, { id: "media", label: "故事板构图" },
  { id: "preview", label: "预览" }, { id: "video", label: "视频" },
  { id: "note", label: "便签" }, { id: "card", label: "卡片" },
];

function itemId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : "canvas-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function removeOwnedAssetPlacement(document: CanvasDocument, placement: OwnedAssetPlacement): CanvasDocument | null {
  const layouts = document.nodeLayouts;
  if (Array.isArray(layouts)) {
    const matches = layouts.flatMap((item, index) => isRecord(item) && item.refId === placement.ref ? [index] : []);
    if (matches.length !== 1) return null;
    const index = matches[0]!;
    const layout = layouts[index];
    if (!isRecord(layout) || Object.keys(layout).length !== 3
      || layout.x !== placement.x || layout.y !== placement.y) return null;
    return { ...document, nodeLayouts: layouts.filter((_item, itemIndex) => itemIndex !== index) };
  }
  if (!isRecord(layouts) || !Object.prototype.hasOwnProperty.call(layouts, placement.ref)) return null;
  const layout = layouts[placement.ref];
  if (!isRecord(layout) || Object.keys(layout).length !== 2
    || layout.x !== placement.x || layout.y !== placement.y) return null;
  const nextLayouts = { ...layouts };
  delete nextLayouts[placement.ref];
  return { ...document, nodeLayouts: nextLayouts };
}

function isTextInput(target: EventTarget | null): boolean {
  return target instanceof HTMLElement
    && (target.isContentEditable || target.matches("input, textarea, select, [contenteditable='true']"));
}

function clampZoom(zoom: number): number {
  return Math.min(3, Math.max(0.2, zoom));
}

export function CanvasWorkspace({
  chapter: selectedChapter,
  series,
  services,
  port,
  userId,
  sessionGeneration,
  onClose,
  onUnauthorized,
  onSourceChanged,
}: {
  chapter: Chapter;
  series: Series;
  services: WorkspaceServices;
  port: CanvasDataPort;
  userId: string;
  sessionGeneration: number;
  onClose(): void;
  onUnauthorized(): void;
  onSourceChanged(chapterId: string): void;
}) {
  const openEpoch = useRef(0);
  if (openEpoch.current === 0) openEpoch.current = Math.floor(Math.random() * Number.MAX_SAFE_INTEGER) || 1;
  const scopeKey = userId + ":" + sessionGeneration + ":" + series.id + ":" + selectedChapter.id + ":" + openEpoch.current;
  const serviceIdentity = useMemo(() => ({ services, port }), [services, port]);
  const documentState = useCanvasDocument({
    port,
    chapterId: selectedChapter.id,
    actorId: userId,
    sessionGeneration,
    seriesId: series.id,
    serviceIdentity,
    onUnauthorized,
  });
  const [sources, setSources] = useState<CanvasSourceSnapshot | null>(null);
  const sourcesRef = useRef<CanvasSourceSnapshot | null>(sources);
  sourcesRef.current = sources;
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [sourceLoading, setSourceLoading] = useState(true);
  const [sourceMessage, setSourceMessage] = useState<string | null>(null);
  const [sourceMutationPending, setSourceMutationPending] = useState(false);
  const sourceMutationPendingRef = useRef(false);
  const [unknownSourceIntent, setUnknownSourceIntent] = useState<UnknownSourceIntent | null>(null);
  const unknownSourceIntentRef = useRef<UnknownSourceIntent | null>(unknownSourceIntent);
  unknownSourceIntentRef.current = unknownSourceIntent;
  const [sourceReadbackPending, setSourceReadbackPending] = useState(false);
  const pendingSourceAcknowledgementRef = useRef<PendingSourceAcknowledgement | null>(null);
  const [sourceEditor, setSourceEditor] = useState<CanvasSourceEditor | null>(null);
  const sourceEditorRef = useRef<CanvasSourceEditor | null>(sourceEditor);
  sourceEditorRef.current = sourceEditor;
  const [creationUndos, setCreationUndos] = useState<CanvasCreationUndo[]>([]);
  const createdAssetPlacementsRef = useRef(new Map<string, OwnedAssetPlacement>());
  const sourceOperationControllerRef = useRef<AbortController | null>(null);
  const acquiredLockLeaseRef = useRef<{
    chapterId: string;
    lease: ChapterEditLockLeaseHandle;
  } | null>(null);
  const [history, setHistory] = useState<CanvasHistory>(EMPTY_CANVAS_HISTORY);
  const sourceBindingDraftRef = useRef(false);
  const [filter, setFilter] = useState<"all" | CanvasNodeKind>("all");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const selectedNodeRef = useRef<string | null>(selectedNode);
  selectedNodeRef.current = selectedNode;
  function selectCanvasNode(ref: string | null): void {
    selectedNodeRef.current = ref;
    setSelectedNode(ref);
  }
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connectionSource, setConnectionSource] = useState<string | null>(null);
  const [closePrompt, setClosePrompt] = useState(false);
  const [closeBusy, setCloseBusy] = useState(false);
  const [drag, setDrag] = useState<DragState | null>(null);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const scopeEpochRef = useRef(0);
  const generationWorkspaceEpochRef = useRef(0);
  const generationWorkspaceIdentityRef = useRef({ scopeKey, serviceIdentity });
  generationWorkspaceIdentityRef.current = { scopeKey, serviceIdentity };
  const generationDialogSequenceRef = useRef(0);
  const [generationDialog, setGenerationDialog] = useState<CanvasGenerationDialogModel | null>(null);
  const generationDialogRef = useRef<CanvasGenerationDialogModel | null>(generationDialog);
  generationDialogRef.current = generationDialog;
  const [generationIntent, setGenerationIntent] = useState<WorkspaceGenerationIntent | null>(null);
  const generationIntentRef = useRef<WorkspaceGenerationIntent | null>(generationIntent);
  generationIntentRef.current = generationIntent;
  const [generationHistoryLookups, setGenerationHistoryLookups] = useState<Map<string, CanvasResultApplicationLookup>>(() => new Map());
  const [generationBusy, setGenerationBusy] = useState(false);
  const generationBusyRef = useRef(false);
  const [imageGenerationBlocked, setImageGenerationBlocked] = useState(false);
  const imageGenerationBlockedRef = useRef(false);
  const imageGenerationModeRef = useRef(false);
  const imageGenerationUnresolvedRef = useRef(false);
  const imageGenerationBusyRef = useRef(false);
  const [generationLockRequired, setGenerationLockRequired] = useState(false);
  const [generationLockUnavailable, setGenerationLockUnavailable] = useState(false);
  const [generationLockPending, setGenerationLockPending] = useState(false);
  const [generationLockMessage, setGenerationLockMessage] = useState<string | null>(null);
  const [generationConfirmedProof, setGenerationConfirmedProof] = useState<ConfirmedGenerationProof | null>(null);
  const generationPageControllerRef = useRef<AbortController | null>(null);
  const generationActionControllerRef = useRef<AbortController | null>(null);
  const generationMutationPendingRef = useRef(false);
  const unauthorizedRef = useRef(onUnauthorized);
  unauthorizedRef.current = onUnauthorized;

  const updateImageGenerationGuard = useCallback((blocked: boolean, unresolved: boolean, busy: boolean) => {
    imageGenerationBlockedRef.current = blocked;
    imageGenerationUnresolvedRef.current = unresolved;
    imageGenerationBusyRef.current = busy;
    setImageGenerationBlocked(blocked);
  }, []);

  useEffect(() => {
    const epoch = ++scopeEpochRef.current;
    const controller = new AbortController();
    sourceOperationControllerRef.current?.abort();
    sourceOperationControllerRef.current = null;
    sourceEditorRef.current?.lifecycle.abort();
    sourceEditorRef.current = null;
    sourcesRef.current = null;
    pendingSourceAcknowledgementRef.current = null;
    sourceMutationPendingRef.current = false;
    sourceBindingDraftRef.current = false;
    setSourceEditor(null);
    setUnknownSourceIntent(null);
    setSourceReadbackPending(false);
    setSourceMutationPending(false);
    setCreationUndos([]);
    createdAssetPlacementsRef.current.clear();
    setSourceMessage(null);
    selectCanvasNode(null);
    setSelectedEdge(null);
    setConnectionSource(null);
    setConnecting(false);
    setClosePrompt(false);
    setSourceLoading(true);
    setSourceError(null);
    setSources(null);
    setHistory(EMPTY_CANVAS_HISTORY);
    const sourcePort = services.canvasSource;
    void services.listChapters(series.id, controller.signal).then(async (chapters) => {
      if (controller.signal.aborted || scopeEpochRef.current !== epoch) return null;
      const chapter = chapters.find((item) => item.id === selectedChapter.id && item.series_id === series.id);
      if (chapter === undefined) {
        throw new CanvasSourceError("conflict", "章节已不属于当前剧集，画布来源编辑已停用。", 409);
      }
      if (sourcePort === undefined) {
        const [storyboard_assets, characters, scenes, props] = await Promise.all([
          services.listStoryboardAssets(series.id, selectedChapter.id, controller.signal),
          services.listCharacters(series.id, controller.signal),
          services.listScenes(series.id, controller.signal),
          services.listProps(series.id, controller.signal),
        ]);
        return {
          capability: CANVAS_SOURCE_CAPABILITY,
          series_id: series.id,
          chapter_id: selectedChapter.id,
          version: "legacy-read-only",
          requires_lock: false,
          chapter,
          storyboard_assets,
          characters,
          scenes,
          props,
        } satisfies CanvasSourceSnapshot;
      }
      return sourcePort.read(selectedChapter.id, controller.signal);
    }).then((snapshot) => {
      if (snapshot === null) return;
      if (controller.signal.aborted || scopeEpochRef.current !== epoch) return;
      if (snapshot.chapter_id !== selectedChapter.id || snapshot.series_id !== series.id
        || snapshot.chapter.id !== selectedChapter.id || snapshot.chapter.series_id !== series.id) {
        setSourceError("来源快照未能确认当前剧集和章节，已停用来源编辑。");
        return;
      }
      setSources(snapshot);
      if (sourcePort === undefined) {
        setSourceMessage("当前模式仅提供章节与素材查看；来源编辑接口未接入，章节来源写入已停用。");
      }
    }).catch((error: unknown) => {
      if (controller.signal.aborted || scopeEpochRef.current !== epoch) return;
      if (asCanvasSourceError(error).kind === "unauthorized") unauthorizedRef.current();
      setSourceError(error instanceof Error ? error.message : "画布来源读取失败，请重试。");
    }).finally(() => {
      if (!controller.signal.aborted && scopeEpochRef.current === epoch) setSourceLoading(false);
    });
    return () => {
      controller.abort();
      scopeEpochRef.current += 1;
    };
  }, [selectedChapter.id, series.id, services, services.canvasSource]);

  useEffect(() => () => {
    sourceOperationControllerRef.current?.abort();
    sourceEditorRef.current?.lifecycle.abort();
    const held = acquiredLockLeaseRef.current;
    acquiredLockLeaseRef.current = null;
    if (held !== null && held.chapterId === selectedChapter.id && services.releaseChapterEditLock !== undefined) {
      void services.releaseChapterEditLock(held.chapterId, new AbortController().signal, held.lease).catch(() => undefined);
    }
  }, [selectedChapter.id, services]);

  const displayDocument = documentState.document ?? {};
  const graph = useMemo(() => sources === null
    ? { nodes: [], edges: [] }
    : buildCanvasGraph(
      sources.chapter,
      sources.storyboard_assets,
      sources.characters,
      sources.scenes,
      sources.props,
      displayDocument,
      services.canvasSource === undefined ? undefined : sources.version,
    ), [displayDocument, services.canvasSource, sources]);
  const visibleNodes = graph.nodes.filter((node) => filter === "all" || node.kind === filter);
  const visibleRefs = new Set(visibleNodes.map((node) => node.ref));
  const visibleEdges = graph.edges.filter((edge) => visibleRefs.has(edge.sourceRef) && visibleRefs.has(edge.targetRef));
  const nodeByRef = new Map(graph.nodes.map((node) => [node.ref, node]));
  const viewport = canvasViewport(displayDocument);
  const closeRef = useRef<() => void>(() => undefined);

  const sourceWritesBlocked = services.canvasSource === undefined || sourceMutationPending || sourceReadbackPending
    || unknownSourceIntent !== null || generationBusy || generationMutationPendingRef.current || generationIntent !== null
    || imageGenerationBlocked
    || sourceError !== null || sources === null;
  const sourceOutcomeUnresolved = sourceMutationPending || sourceReadbackPending || unknownSourceIntent !== null
    || generationBusy || generationMutationPendingRef.current || generationIntent !== null || imageGenerationBlocked;

  function sourceSnapshotMatchesScope(snapshot: CanvasSourceSnapshot): boolean {
    return snapshot.chapter_id === selectedChapter.id && snapshot.series_id === series.id
      && snapshot.chapter.id === selectedChapter.id && snapshot.chapter.series_id === series.id;
  }

  function createGenerationWorkspaceScope(
    options: Pick<GenerationWorkspaceScope, "dialogId" | "selectedRef" | "sourceVersion"> = {},
  ): GenerationWorkspaceScope {
    return {
      actorId: userId,
      seriesId: series.id,
      chapterId: selectedChapter.id,
      sessionGeneration,
      scopeKey,
      workspaceEpoch: generationWorkspaceEpochRef.current,
      sourceEpoch: scopeEpochRef.current,
      servicesIdentity: serviceIdentity,
      ...options,
    };
  }

  function isGenerationWorkspaceScopeCurrent(scope: GenerationWorkspaceScope): boolean {
    const identity = generationWorkspaceIdentityRef.current;
    return identity.scopeKey === scope.scopeKey
      && identity.serviceIdentity === scope.servicesIdentity
      && generationWorkspaceEpochRef.current === scope.workspaceEpoch
      && scopeEpochRef.current === scope.sourceEpoch
      && userId === scope.actorId
      && sessionGeneration === scope.sessionGeneration
      && series.id === scope.seriesId
      && selectedChapter.id === scope.chapterId;
  }

  function isGenerationDialogScopeCurrent(scope: GenerationWorkspaceScope): boolean {
    return isGenerationWorkspaceScopeCurrent(scope)
      && scope.dialogId !== undefined
      && generationDialogRef.current?.id === scope.dialogId
      && (scope.selectedRef === undefined || selectedNodeRef.current === scope.selectedRef)
      && (scope.sourceVersion === undefined || sourcesRef.current?.version === scope.sourceVersion);
  }

  function setWorkspaceGenerationIntent(intent: WorkspaceGenerationIntent | null): void {
    generationIntentRef.current = intent;
    setGenerationIntent(intent);
    if (intent !== null) setGenerationConfirmedProof(null);
  }

  function closeGenerationDialog(): void {
    generationDialogSequenceRef.current += 1;
    generationPageControllerRef.current?.abort();
    generationPageControllerRef.current = null;
    generationActionControllerRef.current?.abort();
    generationDialogRef.current = null;
    setGenerationDialog(null);
    setGenerationHistoryLookups(new Map());
    setGenerationLockRequired(false);
    setGenerationLockUnavailable(false);
    setGenerationLockPending(false);
    setGenerationLockMessage(null);
  }

  function setGenerationBusyState(value: boolean): void {
    generationBusyRef.current = value;
    setGenerationBusy(value);
  }

  useEffect(() => {
    generationWorkspaceEpochRef.current += 1;
    closeGenerationDialog();
    setWorkspaceGenerationIntent(null);
    setGenerationBusyState(false);
    generationMutationPendingRef.current = false;
    generationActionControllerRef.current = null;
    sourceMutationPendingRef.current = false;
    setSourceMutationPending(false);
    setGenerationLockPending(false);
    setGenerationConfirmedProof(null);
    return () => {
      generationWorkspaceEpochRef.current += 1;
      generationDialogSequenceRef.current += 1;
      generationPageControllerRef.current?.abort();
      generationActionControllerRef.current?.abort();
      generationPageControllerRef.current = null;
      generationActionControllerRef.current = null;
      generationMutationPendingRef.current = false;
    };
  }, [scopeKey, serviceIdentity]);

  useEffect(() => {
    const current = generationDialogRef.current;
    if (current === null || current.selectedRef === null || current.selectedRef === selectedNode) return;
    closeGenerationDialog();
    setSourceMessage("选中目标已变化，已停止旧的生成记录请求。未确认的应用意图仍保留在画布中。");
  }, [selectedNode]);

  useEffect(() => {
    const current = generationDialogRef.current;
    if (current === null || current.sourceVersion === null || current.sourceVersion === sources?.version) return;
    closeGenerationDialog();
    setSourceMessage("章节来源版本已变化，已停止旧的生成记录请求。请重新选择目标后读取记录。");
  }, [sources?.version]);

  const applyDocument = useCallback((next: CanvasDocument) => {
    if (sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationMutationPendingRef.current || !documentState.writable || documentState.document === null
      || documentEqual(documentState.document, next)) return;
    sourceBindingDraftRef.current = false;
    setHistory((current) => pushCanvasHistory(current, documentState.document!));
    documentState.edit(next);
  }, [documentState, sourceOutcomeUnresolved]);

  const changeViewport = useCallback((next: CanvasViewport) => {
    if (documentState.document === null) return;
    applyDocument(patchCanvasViewport(documentState.document, next));
  }, [applyDocument, documentState.document]);

  function commitNodePosition(node: CanvasGraphNode, x: number, y: number): void {
    if (sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationMutationPendingRef.current || documentState.document === null) return;
    sourceBindingDraftRef.current = false;
    if (node.itemCollection !== undefined && node.itemId !== undefined) {
      documentState.edit(patchCanvasItem(documentState.document, node.itemCollection, node.itemId, { x, y }));
    } else {
      documentState.edit(patchNodeLayout(documentState.document, node.layoutRef ?? node.ref, { x, y }));
    }
  }

  function beginDrag(event: ReactPointerEvent<HTMLElement>, node: CanvasGraphNode | null): void {
    if (node !== null) {
      event.stopPropagation();
      selectCanvasNode(node.ref);
      if (connecting) {
        if (connectionSource === null) setConnectionSource(node.ref);
        else if (connectionSource !== node.ref) {
          const document = documentState.document;
          if (document !== null) {
            const currentEdges = Array.isArray(document.manualEdges) ? document.manualEdges : [];
            const duplicate = currentEdges.some((edge) => edge !== null && typeof edge === "object"
              && "sourceRef" in edge && "targetRef" in edge
              && edge.sourceRef === connectionSource && edge.targetRef === node.ref);
            if (!duplicate) {
              applyDocument(addCanvasItem(document, "manualEdges", {
                id: itemId(), sourceRef: connectionSource, targetRef: node.ref, relation: "manual",
              }));
            }
          }
          setConnectionSource(null);
          setConnecting(false);
        }
        return;
      }
    }
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDrag({
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      node,
      nodeX: node?.x ?? 0,
      nodeY: node?.y ?? 0,
      viewport,
      startDocument: documentState.document,
    });
  }

  function movePointer(event: ReactPointerEvent<HTMLElement>): void {
    if (sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationMutationPendingRef.current || drag === null || event.pointerId !== drag.pointerId) return;
    const dx = (event.clientX - drag.startX) / (drag.node === null ? 1 : viewport.zoom);
    const dy = (event.clientY - drag.startY) / (drag.node === null ? 1 : viewport.zoom);
    if (drag.node === null) {
      if (documentState.document !== null) {
        sourceBindingDraftRef.current = false;
        documentState.edit(patchCanvasViewport(documentState.document, {
          ...drag.viewport,
          x: drag.viewport.x + dx,
          y: drag.viewport.y + dy,
        }));
      }
    } else if (Math.abs(dx) + Math.abs(dy) >= 2) {
      commitNodePosition(drag.node, drag.nodeX + dx, drag.nodeY + dy);
    }
  }

  function finishDrag(pointerId: number): void {
    if (drag === null || drag.pointerId !== pointerId) return;
    setDrag(null);
    if (drag.startDocument !== null && documentState.document !== null
      && !documentEqual(drag.startDocument, documentState.document)) {
      sourceBindingDraftRef.current = false;
      setHistory((current) => pushCanvasHistory(current, drag.startDocument!));
    }
  }

  function zoomAt(event: WheelEvent<HTMLDivElement>): void {
    event.preventDefault();
    const element = viewportRef.current;
    if (element === null) return;
    const rect = element.getBoundingClientRect();
    const cursorX = event.clientX - rect.left;
    const cursorY = event.clientY - rect.top;
    const nextZoom = clampZoom(viewport.zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12));
    const worldX = (cursorX - viewport.x) / viewport.zoom;
    const worldY = (cursorY - viewport.y) / viewport.zoom;
    changeViewport({ x: cursorX - worldX * nextZoom, y: cursorY - worldY * nextZoom, zoom: nextZoom });
  }

  function fitContent(): void {
    const element = viewportRef.current;
    if (element === null || visibleNodes.length === 0) return;
    const rect = element.getBoundingClientRect();
    const minX = Math.min(...visibleNodes.map((node) => node.x));
    const minY = Math.min(...visibleNodes.map((node) => node.y));
    const maxX = Math.max(...visibleNodes.map((node) => node.x + node.width));
    const maxY = Math.max(...visibleNodes.map((node) => node.y + node.height));
    const zoom = clampZoom(Math.min((rect.width - 72) / Math.max(1, maxX - minX), (rect.height - 72) / Math.max(1, maxY - minY)));
    changeViewport({
      zoom,
      x: (rect.width - (maxX - minX) * zoom) / 2 - minX * zoom,
      y: (rect.height - (maxY - minY) * zoom) / 2 - minY * zoom,
    });
  }

  function updateText(node: CanvasGraphNode, value: string): void {
    if (documentState.document === null || node.itemCollection === undefined || node.itemId === undefined) return;
    applyDocument(patchCanvasItem(documentState.document, node.itemCollection, node.itemId, {
      [node.itemTextField ?? "text"]: value,
    }));
  }

  function createTextItem(collection: "notes" | "cards"): void {
    if (documentState.document === null) return;
    const centerX = viewportRef.current?.clientWidth ?? 700;
    const centerY = viewportRef.current?.clientHeight ?? 450;
    const id = itemId();
    applyDocument(addCanvasItem(documentState.document, collection, {
      id,
      text: collection === "notes" ? "新便签" : "新文本卡片",
      x: (centerX / 2 - viewport.x) / viewport.zoom,
      y: (centerY / 2 - viewport.y) / viewport.zoom,
      width: 230,
      height: 145,
    }));
    selectCanvasNode(collection === "notes" ? id : "card:" + id);
  }

  function removeSelected(): void {
    if (documentState.document === null) return;
    const edge = graph.edges.find((candidate) => candidate.id === selectedEdge && candidate.manual);
    if (edge !== undefined) {
      applyDocument(removeCanvasItem(documentState.document, "manualEdges", edge.id));
      setSelectedEdge(null);
      return;
    }
    const node = graph.nodes.find((candidate) => candidate.ref === selectedNode);
    if (node?.itemCollection !== undefined && node.itemId !== undefined) {
      applyDocument(removeCanvasItem(documentState.document, node.itemCollection, node.itemId));
      selectCanvasNode(null);
    }
  }

  function undo(): void {
    if (sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationMutationPendingRef.current || documentState.document === null) return;
    const result = undoCanvasHistory(history, documentState.document);
    if (result === null) return;
    sourceBindingDraftRef.current = false;
    setHistory(result.history);
    documentState.edit(result.document);
  }

  function redo(): void {
    if (sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationMutationPendingRef.current || documentState.document === null) return;
    const result = redoCanvasHistory(history, documentState.document);
    if (result === null) return;
    sourceBindingDraftRef.current = false;
    setHistory(result.history);
    documentState.edit(result.document);
  }

  function toggleRelation(relation: string): void {
    if (documentState.document === null) return;
    const hidden = Array.isArray(documentState.document.hiddenRelations)
      ? documentState.document.hiddenRelations
      : [];
    const count = hidden.filter((item) => item === relation).length;
    if (count > 1) return;
    applyDocument(patchHiddenRelation(documentState.document, relation, count === 0));
  }

  function applySourceSnapshot(
    snapshot: CanvasSourceSnapshot,
    transition?: CanvasSourceTransition,
    transformDocument?: (document: CanvasDocument | null) => CanvasDocument | null,
  ): void {
    const previous = sourcesRef.current;
    const currentDocument = documentState.document;
    let nextDocument = currentDocument;
    if (transition !== undefined && previous !== null && sourceSnapshotMatchesScope(snapshot)
      && previous.version !== snapshot.version && previous.series_id === snapshot.series_id
      && previous.chapter_id === snapshot.chapter_id) {
      const reconciledCurrent = currentDocument === null
        ? null
        : bindCanvasSourceDocumentTransition(currentDocument, previous, snapshot, transition);
      const refMap = reconciledCurrent?.refs ?? new Map<string, string>();
      if (refMap.size > 0) {
        setHistory((current) => ({
          undo: current.undo.map((item) => bindCanvasSourceDocumentTransition(item, previous, snapshot, transition).document),
          redo: current.redo.map((item) => bindCanvasSourceDocumentTransition(item, previous, snapshot, transition).document),
        }));
        const selected = selectedNodeRef.current;
        selectCanvasNode(selected === null ? null : refMap.get(selected) ?? selected);
        setConnectionSource((current) => current === null ? null : refMap.get(current) ?? current);
        if (reconciledCurrent !== null && documentState.writable) {
          const remapped = reconciledCurrent.document;
          if (!documentEqual(currentDocument, remapped)) {
            sourceBindingDraftRef.current = sourceBindingDraftRef.current || !documentState.dirty;
          }
          nextDocument = remapped;
        }
      }
    }
    if (transformDocument !== undefined) nextDocument = transformDocument(nextDocument);
    if (documentState.writable && currentDocument !== null && nextDocument !== null
      && !documentEqual(currentDocument, nextDocument)) {
      documentState.edit(nextDocument);
    }
    sourcesRef.current = snapshot;
    setSources(snapshot);
  }

  function canPersistAssetPlacement(): boolean {
    const document = documentState.document;
    return documentState.writable && document !== null
      && (document.nodeLayouts === undefined || Array.isArray(document.nodeLayouts) || isRecord(document.nodeLayouts));
  }

  function placeConfirmedAsset(
    command: Extract<CanvasSourceCommand, { type: "asset.create" }>,
    snapshot: CanvasSourceSnapshot,
    receipt: CanvasCreationReceipt | null,
    document: CanvasDocument | null,
  ): CanvasDocument | null {
    if (receipt === null || receipt.kind !== command.kind || typeof receipt.target.id !== "string") return document;
    const id = receipt.target.id;
    const matches = assetCollection(snapshot, command.kind).filter((asset) => asset.id === id);
    if (matches.length !== 1 || matches[0]?.series_id !== series.id) return document;

    if (!documentState.writable || document === null) {
      setSourceMessage("服务器已确认素材创建，但当前画布无法安全保存位置；未按名称或章节引用猜测布局。");
      return document;
    }

    const ref = `${command.kind}:${id}`;
    if (hasCanvasLayout(document, ref)) {
      selectCanvasNode(ref);
      return document;
    }

    const kindOrder = command.kind === "character" ? 0 : command.kind === "scene" ? 1 : 2;
    const kindCount = graph.nodes.filter((node) => node.kind === command.kind).length;
    const placement = { ref, x: 40 + kindOrder * 300, y: 50 + kindCount * 150 };
    try {
      const next = patchNodeLayout(document, ref, { x: placement.x, y: placement.y });
      if (documentEqual(document, next)) return document;
      createdAssetPlacementsRef.current.set(receipt.token, placement);
      selectCanvasNode(ref);
      return next;
    } catch {
      setSourceMessage("服务器已确认素材创建，但画布布局结构不支持安全放置；原创建回执已保留。");
      return document;
    }
  }

  function removeConfirmedAssetPlacement(
    receiptToken: string,
    receipt: CanvasCreationReceipt | null,
    document: CanvasDocument | null,
  ): CanvasDocument | null {
    const placement = createdAssetPlacementsRef.current.get(receiptToken);
    if (placement === undefined || receipt === null
      || (receipt.kind !== "character" && receipt.kind !== "scene" && receipt.kind !== "prop")
      || typeof receipt.target.id !== "string" || placement.ref !== `${receipt.kind}:${receipt.target.id}`) return document;

    createdAssetPlacementsRef.current.delete(receiptToken);
    if (!documentState.writable || document === null) return document;
    const next = removeOwnedAssetPlacement(document, placement);
    return next !== null && !documentEqual(document, next) ? next : document;
  }

  function editorIsCurrent(lifecycle: AbortController): boolean {
    return !lifecycle.signal.aborted
      && sourceEditorRef.current?.lifecycle === lifecycle
      && sourcesRef.current !== null;
  }

  function closeSourceEditor(lifecycle: AbortController): void {
    lifecycle.abort();
    setSourceEditor((current) => current?.lifecycle === lifecycle ? null : current);
  }

  function openSourceEditor(editor: WithoutLifecycle<CanvasSourceEditor>): void {
    if (sourceWritesBlocked || imageGenerationBlockedRef.current || (documentState.dirty && !sourceBindingDraftRef.current) || documentState.status === "saving"
      || documentState.status === "unknown" || documentState.status === "conflict"
      || generationMutationPendingRef.current) return;
    const lifecycle = new AbortController();
    setSourceMessage(null);
    setSourceEditor({ ...editor, lifecycle } as CanvasSourceEditor);
  }

  async function ensureSourceEditLock(frozen: CanvasSourceSnapshot, signal: AbortSignal): Promise<void> {
    if (!frozen.requires_lock) return;

    const getLock = services.getChapterEditLock;
    const acquireLock = services.acquireChapterEditLock;
    if (getLock === undefined || acquireLock === undefined) {
      throw new CanvasSourceError("unsupported", "当前运行模式没有接入章节编辑锁，未发送来源变更。");
    }

    let state = await getLock.call(services, selectedChapter.id, signal);
    if (validCurrentLock(state, userId)) return;
    if (state.locked) {
      throw new CanvasSourceError("conflict", "章节当前由其他成员编辑，未发送来源变更。", 409);
    }

    const acquired = await acquireLock.call(services, selectedChapter.id, signal);
    state = await getLock.call(services, selectedChapter.id, signal);
    if (!validCurrentLock(state, userId)) {
      throw new CanvasSourceError("conflict", "未能确认当前账号持有有效章节锁，未发送来源变更。", 409);
    }
    if (acquired.lease !== null) {
      acquiredLockLeaseRef.current = { chapterId: selectedChapter.id, lease: acquired.lease };
    }
  }

  async function runSourceCommand(
    command: CanvasSourceCommand,
    frozen: CanvasSourceSnapshot,
    requestSignal: AbortSignal,
    ownerSignal?: AbortSignal,
  ): Promise<CanvasSourceWriteOutcome> {
    if (sourceMutationPendingRef.current || generationMutationPendingRef.current || imageGenerationBlockedRef.current || pendingSourceAcknowledgementRef.current !== null
      || unknownSourceIntentRef.current !== null || sourceReadbackPending) {
      return { status: "conflict", message: "上一项来源操作尚未核对，当前操作没有发送。" };
    }
    if ((documentState.dirty && !sourceBindingDraftRef.current) || documentState.status === "saving" || documentState.status === "unknown"
      || documentState.status === "conflict") {
      return { status: "conflict", message: "请先处理画布布局草稿或未确认的布局保存，再编辑章节来源。" };
    }
    if (sourcesRef.current?.version !== frozen.version || frozen.chapter_id !== selectedChapter.id
      || frozen.series_id !== series.id) {
      const message = "冻结的章节来源已过期；没有把本次意图改绑到新来源。请只读核对后重新打开编辑。";
      setUnknownSourceIntent({ kind: "conflict", command, frozen, frozenVersion: frozen.version, verifiedSnapshot: null });
      setSourceMessage(message);
      return { status: "conflict", message };
    }
    if (command.type === "asset.create" && !canPersistAssetPlacement()) {
      const message = "画布布局尚未可安全保存，未发送素材创建。请等待画布载入或使用支持受控布局保存的模式。";
      setSourceMessage(message);
      return { status: "conflict", message };
    }
    const sourcePort = services.canvasSource;
    if (sourcePort === undefined) {
      throw new CanvasSourceError("unsupported", "当前服务没有接入受控章节来源，不会改用旧接口写入。");
    }

    const epoch = scopeEpochRef.current;
    const operation = new AbortController();
    const abortForRequest = () => operation.abort();
    requestSignal.addEventListener("abort", abortForRequest, { once: true });
    ownerSignal?.addEventListener("abort", abortForRequest, { once: true });
    if (requestSignal.aborted || ownerSignal?.aborted) operation.abort();
    sourceOperationControllerRef.current = operation;
    sourceMutationPendingRef.current = true;
    setSourceMutationPending(true);
    setSourceMessage(null);
    let writeDispatched = false;

    const isCurrent = () => !operation.signal.aborted && scopeEpochRef.current === epoch
      && sourcesRef.current?.version === frozen.version;

    try {
      await ensureSourceEditLock(frozen, operation.signal);
      if (!isCurrent()) {
        if (operation.signal.aborted) throw new CanvasSourceError("aborted", "来源操作已取消。");
        throw new CanvasSourceError("conflict", "章节来源已变化，未发送来源变更。", 409);
      }
      writeDispatched = true;
      const acknowledgement = await sourcePort.write(selectedChapter.id, frozen, command, operation.signal);
      if (scopeEpochRef.current !== epoch) {
        return { status: "unknown", message: "画布已关闭或切换；服务器回执未并入新的画布实例。" };
      }
      if (!sourceSnapshotMatchesScope(acknowledgement.snapshot)) {
        throw new CanvasSourceError("unknown", "服务器写回执未能确认当前剧集和章节；保留原意图并只读核对。", null);
      }
      const transition: CanvasSourceTransition = {
        command,
        receipt: command.type === "creation.undo"
          ? creationUndos.find((item) => item.receipt.token === command.receipt)?.receipt ?? null
          : acknowledgement.creation_receipt,
      };
      applySourceSnapshot(acknowledgement.snapshot, transition, (document) => {
        if (command.type === "asset.create") {
          return placeConfirmedAsset(command, acknowledgement.snapshot, acknowledgement.creation_receipt, document);
        }
        if (command.type === "creation.undo") {
          return removeConfirmedAssetPlacement(command.receipt, transition.receipt, document);
        }
        return document;
      });
      if (command.type === "creation.undo") {
        setCreationUndos((current) => current.filter((item) => item.receipt.token !== command.receipt));
      } else setCreationUndos([]);
      pendingSourceAcknowledgementRef.current = {
        command,
        frozen,
        acknowledged: acknowledgement.snapshot,
        receipt: acknowledgement.creation_receipt,
      };
      setSourceReadbackPending(true);

      try {
        const readback = await sourcePort.read(selectedChapter.id, operation.signal);
        if (scopeEpochRef.current !== epoch) {
          return { status: "saved", snapshot: acknowledgement.snapshot, receipt: acknowledgement.creation_receipt };
        }
        if (!sourceSnapshotMatchesScope(readback)) {
          setSourceMessage("服务器已确认来源写入，但独立读取指向其他剧集或章节。原回执与意图已保留，请在当前权限范围内重新核对；不会重发。");
          onSourceChanged(selectedChapter.id);
          return { status: "saved", snapshot: acknowledgement.snapshot, receipt: acknowledgement.creation_receipt };
        }
        const sameWrite = readback.series_id === frozen.series_id
          && readback.chapter_id === frozen.chapter_id
          && readback.version === acknowledgement.snapshot.version;
        pendingSourceAcknowledgementRef.current = null;
        setSourceReadbackPending(false);
        applySourceSnapshot(readback, sameWrite ? transition : undefined);
        onSourceChanged(selectedChapter.id);
        if (sameWrite) {
          if (acknowledgement.creation_receipt !== null) {
            const label = acknowledgement.creation_receipt.kind === "frame" ? "分镜"
              : acknowledgement.creation_receipt.kind === "video" ? "视频"
                : `${acknowledgement.creation_receipt.kind} 素材`;
            setCreationUndos([{
              id: itemId(),
              sourceVersion: readback.version,
              receipt: acknowledgement.creation_receipt,
              label,
            }]);
          }
          setSourceMessage("章节来源写入已获服务器回执，独立读取也确认了同一版本。");
        } else {
          const message = "服务器已确认本次来源写入，但独立读取发现来源随后又变化；未重发原操作。请核对并明确采用当前读取。";
          setUnknownSourceIntent({
            kind: "conflict",
            command,
            frozen,
            frozenVersion: frozen.version,
            verifiedSnapshot: readback,
          });
          setSourceMessage(message);
        }
        return { status: "saved", snapshot: acknowledgement.snapshot, receipt: acknowledgement.creation_receipt };
      } catch (cause) {
        if (scopeEpochRef.current !== epoch) {
          return { status: "saved", snapshot: acknowledgement.snapshot, receipt: acknowledgement.creation_receipt };
        }
        const readError = asCanvasSourceError(cause);
        if (readError.kind === "unauthorized") unauthorizedRef.current();
        setSourceMessage(readError.kind === "unauthorized"
          ? "服务器已确认来源写入，但独立读取返回登录失效。原写回执已保留，未重发；请重新登录后只读核对。"
          : readError.kind === "conflict"
            ? `${readError.message}服务器写回执已保留，未重发原操作。`
            : "服务器已确认来源写入，但独立来源读取暂未完成。原写回执已保留，已暂停后续来源和布局写入；请只读核对。");
        onSourceChanged(selectedChapter.id);
        return { status: "saved", snapshot: acknowledgement.snapshot, receipt: acknowledgement.creation_receipt };
      }
    } catch (cause) {
      if (scopeEpochRef.current !== epoch) {
        return { status: writeDispatched ? "unknown" : "conflict", message: "画布已关闭或切换，未在新实例重试来源操作。" };
      }
      const error = asCanvasSourceError(cause);
      if (error.kind === "unauthorized") unauthorizedRef.current();
      if (writeDispatched && (error.kind === "unknown" || error.kind === "network" || error.kind === "timeout" || error.kind === "aborted")) {
        const message = "写入结果尚未确认。原意图已保留，只能只读核对，不会自动重发。";
        setUnknownSourceIntent({ kind: "unknown", command, frozen, frozenVersion: frozen.version, verifiedSnapshot: null });
        setSourceMessage(message);
        return { status: "unknown", message };
      }
      if (error.kind === "conflict") {
        const message = error.message || "章节来源或编辑锁已变化；原意图保留且没有自动重试。";
        setUnknownSourceIntent({ kind: "conflict", command, frozen, frozenVersion: frozen.version, verifiedSnapshot: null });
        setSourceMessage(message);
        return { status: "conflict", message };
      }
      if (error.kind === "aborted") return { status: "conflict", message: error.message };
      throw error;
    } finally {
      requestSignal.removeEventListener("abort", abortForRequest);
      ownerSignal?.removeEventListener("abort", abortForRequest);
      if (sourceOperationControllerRef.current === operation) {
        sourceOperationControllerRef.current = null;
        sourceMutationPendingRef.current = false;
        if (scopeEpochRef.current === epoch) setSourceMutationPending(false);
      }
    }
  }

  async function verifySourceRead(signal: AbortSignal): Promise<string> {
    if (sourceMutationPendingRef.current || generationMutationPendingRef.current || imageGenerationBlockedRef.current) return "来源操作正在进行，请等待当前状态。";
    const sourcePort = services.canvasSource;
    if (sourcePort === undefined) throw new CanvasSourceError("unsupported", "当前服务没有接入受控章节来源。");
    const epoch = scopeEpochRef.current;
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) controller.abort();
    sourceOperationControllerRef.current = controller;
    sourceMutationPendingRef.current = true;
    setSourceMutationPending(true);
    try {
      const snapshot = await sourcePort.read(selectedChapter.id, controller.signal);
      if (controller.signal.aborted || scopeEpochRef.current !== epoch) return "核对已取消。";
      if (!sourceSnapshotMatchesScope(snapshot)) {
        const message = "只读响应指向其他剧集或章节，没有并入当前画布。原回执或意图仍保留。";
        const intent = unknownSourceIntentRef.current;
        if (intent !== null) setUnknownSourceIntent({ ...intent, verifiedSnapshot: null });
        else if (pendingSourceAcknowledgementRef.current === null) setSourceError(message);
        setSourceMessage(message);
        return message;
      }
      const pending = pendingSourceAcknowledgementRef.current;
      if (pending !== null) {
        pendingSourceAcknowledgementRef.current = null;
        setSourceReadbackPending(false);
        applySourceSnapshot(snapshot);
        if (snapshot.version === pending.acknowledged.version) {
          if (pending.receipt !== null) {
            const label = pending.receipt.kind === "frame" ? "分镜"
              : pending.receipt.kind === "video" ? "视频" : `${pending.receipt.kind} 素材`;
            setCreationUndos([{
              id: itemId(), sourceVersion: snapshot.version, receipt: pending.receipt, label,
            }]);
          }
          setSourceMessage("只读读取与服务器写入回执版本一致；来源状态已核对。");
          onSourceChanged(selectedChapter.id);
          return "只读读取与服务器写入回执版本一致；来源状态已核对。";
        }
        const message = "服务器已确认原写入，但当前只读来源是更新版本；未重发、未撤销原操作。请采用当前读取后重新编辑。";
        setUnknownSourceIntent({
          kind: "conflict",
          command: pending.command,
          frozen: pending.frozen,
          frozenVersion: pending.frozen.version,
          verifiedSnapshot: snapshot,
        });
        setSourceMessage(message);
        onSourceChanged(selectedChapter.id);
        return message;
      }

      const intent = unknownSourceIntentRef.current;
      if (intent === null) {
        applySourceSnapshot(snapshot);
        setSourceError(null);
        setSourceLoading(false);
        setSourceMessage("已只读刷新章节来源；没有发送或重试任何写入。");
        onSourceChanged(selectedChapter.id);
        return "已只读刷新章节来源；没有发送或重试任何写入。";
      }
      setUnknownSourceIntent({ ...intent, verifiedSnapshot: snapshot });
      const matchesFrozen = snapshot.version === intent.frozenVersion;
      const message = matchesFrozen
        ? "只读读取仍是原冻结版本；这不能证明之前的未知请求没有迟到提交，原意图仍保留且不会重发。"
        : "只读读取已发现不同来源版本；不能把原意图改绑到新来源。请明确采用当前读取并放弃原意图。";
      setSourceMessage(message);
      return message;
    } catch (cause) {
      if (controller.signal.aborted || scopeEpochRef.current !== epoch) return "核对已取消。";
      if (asCanvasSourceError(cause).kind === "unauthorized") unauthorizedRef.current();
      throw cause;
    } finally {
      signal.removeEventListener("abort", abort);
      if (sourceOperationControllerRef.current === controller) {
        sourceOperationControllerRef.current = null;
        sourceMutationPendingRef.current = false;
        if (scopeEpochRef.current === epoch) setSourceMutationPending(false);
      }
    }
  }

  function adoptVerifiedSource(): void {
    const intent = unknownSourceIntentRef.current;
    if (intent?.verifiedSnapshot === null || intent?.verifiedSnapshot === undefined) return;
    const snapshot = intent.verifiedSnapshot;
    if (!sourceSnapshotMatchesScope(snapshot) || intent.frozen.chapter_id !== selectedChapter.id
      || intent.frozen.series_id !== series.id) {
      setUnknownSourceIntent({ ...intent, verifiedSnapshot: null });
      setSourceMessage("已核对快照不属于当前剧集或章节，不能采用。原意图仍保留。");
      return;
    }
    applySourceSnapshot(snapshot);
    setUnknownSourceIntent(null);
    unknownSourceIntentRef.current = null;
    const editor = sourceEditorRef.current;
    if (editor !== null) closeSourceEditor(editor.lifecycle);
    setSourceMessage("已采用明确核对到的当前章节来源，并放弃原冻结意图；原操作未重发也未撤销。");
    onSourceChanged(selectedChapter.id);
  }

  function openFrameTextEditor(node: CanvasGraphNode): void {
    const frozen = sourcesRef.current;
    if (frozen === null || node.kind !== "frame" || node.sourceTarget === null
      || node.sourceTarget === undefined || node.frameIndex === undefined) return;
    openSourceEditor({ kind: "frame-text", node, frozen });
  }

  function openFrameStructureEditor(mode: FrameStructureMode): void {
    const frozen = sourcesRef.current;
    if (frozen === null) return;
    openSourceEditor({ kind: "frame-structure", mode, frozen });
  }

  function openAssetEditor(assetKind: CanvasAssetKind, mode: "create" | "edit", item: CanvasSourceSnapshot["characters"][number] | CanvasSourceSnapshot["scenes"][number] | CanvasSourceSnapshot["props"][number] | null): void {
    const frozen = sourcesRef.current;
    if (frozen === null || (mode === "edit" && item === null)) return;
    openSourceEditor({ kind: "asset", mode, assetKind, item, frozen });
  }

  function openMediaEditor(target: CanvasFrameTarget, field: "preview" | "video", slot: number | null, append: boolean): void {
    const frozen = sourcesRef.current;
    const frame = frozen?.chapter.content?.[target.index];
    if (frozen === null || frame === undefined || typeof frame !== "object" || frame === null || Array.isArray(frame)) return;
    const record = frame as Record<string, unknown>;
    const initialValue = append ? ""
      : field === "preview" ? typeof record.preview === "string" ? record.preview : ""
        : Array.isArray(record.videos) && slot !== null && typeof record.videos[slot] === "string"
          ? record.videos[slot] as string : "";
    openSourceEditor({ kind: "media", target, field, slot, initialValue, append, frozen });
  }

  function createGuardedAssetMutation(editor: Extract<CanvasSourceEditor, { kind: "asset" }>): GuardedAssetMutation {
    return {
      async save(input, signal) {
        if (!editorIsCurrent(editor.lifecycle)) {
          throw new CanvasSourceError("aborted", "素材编辑窗口已关闭，未发送来源变更。");
        }
        const kind = assetKindForCategory(input.category);
        let command: CanvasSourceCommand;
        if (input.mode === "create") {
          command = kind === "character"
            ? { type: "asset.create", kind, values: characterCreateValues(input.values) }
            : kind === "scene"
              ? { type: "asset.create", kind, values: sceneCreateValues(input.values) }
              : { type: "asset.create", kind, values: propCreateValues(input.values) };
        } else {
          if (input.item === null || input.item.id !== editor.item?.id) {
            throw new CanvasSourceError("conflict", "素材身份与冻结来源不一致，没有发送变更。", 409);
          }
          command = kind === "character"
            ? { type: "asset.update", kind, id: input.item.id, values: characterUpdateValues(input.values) }
            : kind === "scene"
              ? { type: "asset.update", kind, id: input.item.id, values: sceneUpdateValues(input.values) }
              : { type: "asset.update", kind, id: input.item.id, values: propUpdateValues(input.values) };
        }

        const result = await runSourceCommand(command, editor.frozen, signal, editor.lifecycle.signal);
        if (result.status !== "saved") {
          throw new CanvasSourceError(result.status, result.message, result.status === "conflict" ? 409 : null);
        }
        const entityId = command.type === "asset.create"
          ? result.receipt?.target.id
          : command.type === "asset.update" ? command.id : undefined;
        if (typeof entityId !== "string") {
          throw new CanvasSourceError("unknown", "服务器没有返回可确认的素材 ID；素材创建结果需要只读核对。");
        }
        const entity = assetCollection(result.snapshot, kind).find((candidate) => candidate.id === entityId);
        if (entity === undefined) {
          throw new CanvasSourceError("unknown", "回执中的素材 ID 未出现在服务器来源快照中；不会按名称猜测素材身份。");
        }
        return { entity, receipt: result.receipt };
      },
      verify: (signal) => verifySourceRead(signal),
    };
  }

  async function saveFrameText(
    editor: Extract<CanvasSourceEditor, { kind: "frame-text" }>,
    text: string,
    signal: AbortSignal,
  ): Promise<FrameTextSaveResult> {
    if (frameText(editor.frozen.chapter.content?.[editor.node.frameIndex!]) === text) {
      return { status: "unchanged" };
    }
    if (editor.node.sourceTarget === null || editor.node.sourceTarget === undefined) {
      return { status: "conflict", message: "此分镜的来源身份有歧义，无法安全写入。" };
    }
    const result = await runSourceCommand({
      type: "frame.update",
      target: editor.node.sourceTarget,
      text,
    }, editor.frozen, signal, editor.lifecycle.signal);
    return result.status === "saved"
      ? { status: "saved" }
      : { status: result.status, message: result.message };
  }

  async function submitFrameStructure(
    editor: Extract<CanvasSourceEditor, { kind: "frame-structure" }>,
    draft: FrameStructureDraft | null,
    signal: AbortSignal,
  ): Promise<FrameStructureSubmitResult> {
    let command: CanvasSourceCommand;
    if (editor.mode.kind === "insert") {
      if (draft === null) return { status: "conflict", message: "插入草稿缺失，未发送变更。" };
      const after = editor.mode.afterPosition === 0
        ? null
        : sourceFrameTarget(editor.frozen, editor.mode.afterPosition - 1);
      if (editor.mode.afterPosition > 0 && after === null) {
        return { status: "conflict", message: "插入位置的来源身份有歧义，未发送变更。" };
      }
      command = {
        type: "frame.insert",
        after,
        frame: {
          text: draft.text,
          character: [...draft.characterAssetIds],
          scene: [...draft.sceneAssetIds],
          prop: [...draft.propAssetIds],
          storyboard: [],
          preview: null,
        },
      };
    } else {
      const target = sourceFrameTarget(editor.frozen, editor.mode.position - 1);
      if (target === null) return { status: "conflict", message: "待删除分镜的来源身份有歧义，未发送变更。" };
      command = { type: "frame.delete", target };
    }
    const result = await runSourceCommand(command, editor.frozen, signal, editor.lifecycle.signal);
    if (result.status === "saved") return { status: "saved" };
    if (result.status === "conflict") return { status: "conflict", message: result.message };
    return { status: "unknown", message: result.message };
  }

  async function saveMediaSource(
    editor: Extract<CanvasSourceEditor, { kind: "media" }>,
    value: string,
    signal: AbortSignal,
  ): Promise<"saved" | "conflict" | "unknown"> {
    const command: CanvasSourceCommand = editor.append
      ? { type: "media.append", target: editor.target, value }
      : { type: "media.update", target: editor.target, field: editor.field, slot: editor.slot, value };
    const result = await runSourceCommand(command, editor.frozen, signal, editor.lifecycle.signal);
    return result.status;
  }

  async function undoCreation(undo: CanvasCreationUndo): Promise<void> {
    const frozen = sourcesRef.current;
    if (frozen === null || frozen.version !== undo.sourceVersion || sourceWritesBlocked || imageGenerationBlockedRef.current) return;
    const controller = new AbortController();
    try {
      await runSourceCommand({ type: "creation.undo", receipt: undo.receipt.token }, frozen, controller.signal);
    } catch (cause) {
      if (sourceSnapshotMatchesScope(frozen)) setSourceMessage(asCanvasSourceError(cause).message);
    }
  }

  function saveLayout(): void {
    if (sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationMutationPendingRef.current) return;
    void documentState.saveNow();
  }

  async function saveAndClose(): Promise<void> {
    if (sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationIntentRef.current !== null
      || generationBusyRef.current || generationMutationPendingRef.current) return;
    setCloseBusy(true);
    const saved = await documentState.saveNow();
    setCloseBusy(false);
    if (saved) {
      focus.markDismissed();
      onClose();
    }
  }

  function discardAndClose(): void {
    if (generationIntentRef.current !== null || generationBusyRef.current || generationMutationPendingRef.current || imageGenerationBlockedRef.current) return;
    documentState.discard();
    sourceOperationControllerRef.current?.abort();
    sourceEditorRef.current?.lifecycle.abort();
    focus.markDismissed();
    onClose();
  }

  const displayedStatus = documentState.status === "loading" || sourceLoading ? "正在读取章节、素材与画布…" :
    documentState.status === "saving" ? "正在保存…" :
      documentState.status === "conflict" ? "存在版本冲突" :
        documentState.status === "unknown" ? "保存结果待核对" :
          documentState.dirty ? "有未保存更改" :
            documentState.status === "unsupported" ? "只读" : "已保存";

  const selectedSourceNode = graph.nodes.find((node) => node.ref === selectedNode) ?? null;
  const selectedFrameNode = selectedSourceNode?.kind === "frame" ? selectedSourceNode : null;
  const selectedMediaNode = selectedSourceNode?.kind === "preview" || selectedSourceNode?.kind === "video"
    ? selectedSourceNode : null;
  const selectedAssetKind: CanvasAssetKind | null = selectedSourceNode?.kind === "character"
    || selectedSourceNode?.kind === "scene" || selectedSourceNode?.kind === "prop"
    ? selectedSourceNode.kind : null;
  const selectedAsset = selectedAssetKind !== null && selectedSourceNode?.itemId !== undefined && sources !== null
    ? assetCollection(sources, selectedAssetKind).find((item) => item.id === selectedSourceNode.itemId) ?? null
    : null;
  const sourceActionDisabled = services.canvasSource === undefined || sourceWritesBlocked
    || (documentState.dirty && !sourceBindingDraftRef.current) || documentState.status === "saving"
    || documentState.status === "unknown" || documentState.status === "conflict";
  const selectedFrameTarget = selectedFrameNode?.sourceTarget ?? null;
  const selectedMediaTarget = selectedMediaNode?.sourceTarget ?? null;
  const selectedFrameCount = Array.isArray(sources?.chapter.content) ? sources.chapter.content.length : 0;
  const selectedGenerationTarget = sources !== null && selectedSourceNode !== null
    ? generationTargetForNode(sources, selectedSourceNode)
    : null;
  const generationPortsAvailable = services.canvasSource !== undefined
    && services.canvasOwnedResults !== undefined
    && services.canvasGenerationApplications !== undefined
    && services.canvasResultApplications !== undefined;
  const generationEntryDisabled = !generationPortsAvailable || selectedGenerationTarget === null
    || sourceActionDisabled || documentState.dirty || generationDialog !== null || generationMutationPendingRef.current
    || imageGenerationBlockedRef.current;
  const selectedImageGenerationTargetKey = sources !== null && selectedSourceNode !== null && selectedGenerationTarget !== null
    ? imageGenerationTargetKey(selectedSourceNode.ref, selectedGenerationTarget.target, sources.version)
    : null;
  const imageGeneration = useCanvasImageGenerationController({
    contextKey: scopeKey,
    serviceIdentity,
    selectedTargetKey: selectedImageGenerationTargetKey,
    creatorSettings: services.creatorSettings,
    port: services.canvasImageGeneration,
    validateTarget: validateImageGenerationTarget,
    checkEditLock: checkImageGenerationEditLock,
    acquireEditLock: acquireImageGenerationEditLock,
    onGuardChange: updateImageGenerationGuard,
  });
  imageGenerationModeRef.current = imageGeneration.state.mode !== null;

  const focus = useSeriesDialogFocus({
    active: !closePrompt && sourceEditor === null && generationDialog === null && imageGeneration.state.mode === null,
    canDismiss: true,
    onDismiss: () => closeRef.current(),
  });
  const close = useCallback(() => {
    if (documentState.hasPendingWork() || sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationIntentRef.current !== null
      || generationBusyRef.current || generationMutationPendingRef.current) {
      setClosePrompt(true);
      return;
    }
    focus.markDismissed();
    onClose();
  }, [documentState, focus, onClose, sourceOutcomeUnresolved]);
  closeRef.current = close;
  const closePromptFocus = useSeriesDialogFocus({
    active: closePrompt,
    canDismiss: true,
    onDismiss: () => setClosePrompt(false),
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (closePrompt || sourceEditor !== null || imageGenerationModeRef.current || !(event.target instanceof Node)
        || !focus.dialogRef.current?.contains(event.target)) return;
      const command = event.metaKey || event.ctrlKey;
      const input = isTextInput(event.target);
      if (command && event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (!sourceOutcomeUnresolved && !imageGenerationBlockedRef.current && !generationMutationPendingRef.current) void documentState.saveNow();
        return;
      }
      if (input) return;
      if (command && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) redo();
        else undo();
      } else if (event.key.toLowerCase() === "f") {
        event.preventDefault();
        fitContent();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removeSelected();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  });

  function createImageGenerationContext() {
    const scope = createGenerationWorkspaceScope();
    return {
      actorId: scope.actorId,
      seriesId: scope.seriesId,
      chapterId: scope.chapterId,
      isCurrent: () => isGenerationWorkspaceScopeCurrent(scope),
    };
  }

  async function validateImageGenerationTarget(
    target: CanvasImageGenerationTarget,
    signal: AbortSignal,
    requireSelection: boolean,
  ): Promise<boolean> {
    const sourcePort = services.canvasSource;
    if (sourcePort === undefined || !target.context.isCurrent()) return false;
    if (requireSelection && selectedNodeRef.current !== target.selectedRef) return false;

    const snapshot = await sourcePort.read(target.context.chapterId, signal);
    if (signal.aborted || !target.context.isCurrent()) return false;
    if (!sourceSnapshotMatchesScope(snapshot)) {
      setSourceMessage("章节来源响应与当前剧集或章节不匹配，未提交图片任务。");
      return false;
    }

    const selected = graph.nodes.find((node) => node.ref === target.selectedRef);
    const freshTarget = selected === undefined ? null : generationTargetForNode(snapshot, selected);
    if (snapshot.version !== target.sourceVersion || snapshot.requires_lock !== target.requiresLock
      || freshTarget === null || !sameGenerationTarget(freshTarget.target, target.destination)) {
      applySourceSnapshot(snapshot);
      if (requireSelection) selectCanvasNode(null);
      setSourceMessage("原分镜或素材来源版本已变化。已只读刷新当前来源，未创建新任务或改绑原操作。");
      return false;
    }
    if (requireSelection && selectedNodeRef.current !== target.selectedRef) return false;
    return true;
  }

  async function checkImageGenerationEditLock(
    target: CanvasImageGenerationTarget,
    signal: AbortSignal,
  ): Promise<CanvasImageGenerationLockResult> {
    if (!target.requiresLock) return { status: "ready", message: "" };
    const getLock = services.getChapterEditLock;
    if (getLock === undefined) {
      return { status: "unavailable", message: "当前无法确认章节编辑锁，未发送图片任务。" };
    }
    const lock = await getLock.call(services, target.context.chapterId, signal);
    if (signal.aborted || !target.context.isCurrent()) {
      return { status: "unavailable", message: "章节或会话已变化，未发送图片任务。" };
    }
    if (validCurrentLock(lock, target.context.actorId)) {
      return { status: "ready", message: "已核实当前账号持有有效章节编辑锁。" };
    }
    if (lock.locked) {
      return {
        status: "needed",
        message: lockIsOwnedBy(lock, target.context.actorId)
          ? "当前账号的编辑锁已失效，请重新核对后再提交。"
          : "章节编辑锁由其他成员持有，未发送图片任务。",
      };
    }
    return { status: "needed", message: "当前账号尚未持有有效章节编辑锁。请明确取得锁后再次提交。" };
  }

  async function acquireImageGenerationEditLock(
    target: CanvasImageGenerationTarget,
    signal: AbortSignal,
  ): Promise<CanvasImageGenerationLockResult> {
    if (!target.requiresLock) return { status: "ready", message: "此章节不需要额外编辑锁。" };
    const getLock = services.getChapterEditLock;
    const acquireLock = services.acquireChapterEditLock;
    if (getLock === undefined || acquireLock === undefined) {
      return { status: "unavailable", message: "当前无法操作章节编辑锁，没有发送图片任务。" };
    }

    let lock = await getLock.call(services, target.context.chapterId, signal);
    if (signal.aborted || !target.context.isCurrent()) {
      return { status: "unavailable", message: "章节或会话已变化，没有取得编辑锁。" };
    }
    if (validCurrentLock(lock, target.context.actorId)) {
      return { status: "ready", message: "已核实当前账号持有有效章节编辑锁。请再次明确提交图片任务。" };
    }
    if (lock.locked) {
      return {
        status: "needed",
        message: lockIsOwnedBy(lock, target.context.actorId)
          ? "当前账号的编辑锁已失效；没有自动续期或发送图片任务。"
          : "其他成员正在编辑；未更改其锁，也没有发送图片任务。",
      };
    }

    const acquired = await acquireLock.call(services, target.context.chapterId, signal);
    if (acquired.lease !== null && target.context.isCurrent()) {
      acquiredLockLeaseRef.current = { chapterId: target.context.chapterId, lease: acquired.lease };
    }
    if (signal.aborted || !target.context.isCurrent()) {
      return { status: "unavailable", message: "章节或会话已变化，没有确认编辑锁。" };
    }
    lock = await getLock.call(services, target.context.chapterId, signal);
    if (signal.aborted || !target.context.isCurrent()) {
      return { status: "unavailable", message: "章节或会话已变化，没有确认编辑锁。" };
    }
    return validCurrentLock(lock, target.context.actorId)
      ? { status: "ready", message: "已取得并核实当前章节编辑锁。请再次明确提交图片任务。" }
      : { status: "needed", message: "取得锁后仍无法确认当前账号持有编辑锁，没有发送图片任务。" };
  }

  function captureImageGenerationTarget(): CanvasImageGenerationTarget | null {
    const snapshot = sourcesRef.current;
    const selectedRef = selectedNodeRef.current;
    const selected = selectedRef === null ? null : graph.nodes.find((node) => node.ref === selectedRef) ?? null;
    const resolved = snapshot === null || selected === null ? null : generationTargetForNode(snapshot, selected);
    if (snapshot === null || selectedRef === null || selected === null || resolved === null) return null;
    const context = createImageGenerationContext();
    return {
      selectedRef,
      targetKey: imageGenerationTargetKey(selectedRef, resolved.target, snapshot.version),
      destination: resolved.target,
      targetLabel: resolved.label,
      sourceVersion: snapshot.version,
      requiresLock: snapshot.requires_lock,
      promptSeed: imageGenerationPromptSeed(snapshot, selected),
      context,
    };
  }

  function openImageGeneration(): void {
    if (services.canvasImageGeneration === undefined || services.creatorSettings === undefined
      || services.canvasSource === undefined || imageGenerationBlockedRef.current) {
      setSourceMessage("当前服务暂不能提交图片任务，请检查图片模型和画布服务接入。");
      return;
    }
    const target = captureImageGenerationTarget();
    if (target === null || !imageGeneration.openNew(target)) {
      setSourceMessage("请选择身份可确认的分镜预览或角色、场景、道具素材后再生成图片。");
    }
  }

  function openPendingImageGeneration(): void {
    if (!imageGeneration.openPendingIntent()) setSourceMessage("当前没有可核对的图片任务提交。");
  }

  function openImageGenerationReceipt(): void {
    if (!imageGeneration.openReceipt()) setSourceMessage("当前没有已确认的图片任务记录。");
  }

  function startAnotherImageGeneration(): void {
    const target = captureImageGenerationTarget();
    if (target === null || !imageGeneration.startNewFromReceipt(target)) {
      setSourceMessage("请重新选择可确认的分镜或素材，再创建新的图片任务。");
    }
  }

  const imageGenerationEntryDisabled = services.canvasSource === undefined
    || services.creatorSettings === undefined || services.canvasImageGeneration === undefined
    || selectedGenerationTarget === null || sourceActionDisabled || documentState.dirty
    || generationDialog !== null || imageGeneration.state.mode !== null
    || imageGenerationBlockedRef.current;
  const imageGenerationDialogTargetLabel = imageGeneration.state.mode === "form"
    ? imageGeneration.state.target?.targetLabel
    : imageGeneration.state.mode === "intent"
      ? imageGeneration.state.intent?.targetLabel ?? imageGeneration.state.target?.targetLabel
      : imageGeneration.state.mode === "receipt"
        ? imageGeneration.state.receipt?.targetLabel ?? imageGeneration.state.target?.targetLabel
        : undefined;
  const generationImagePreviewScopeKey = generationDialog === null
    ? ""
    : JSON.stringify([
      scopeKey,
      generationDialog.id,
      generationDialog.selectedRef,
      generationDialog.sourceVersion,
      selectedNode,
      sources?.version ?? null,
      generationWorkspaceEpochRef.current,
      scopeEpochRef.current,
    ]);

  function generationDialogScope(dialog: CanvasGenerationDialogModel): GenerationWorkspaceScope {
    return createGenerationWorkspaceScope({
      dialogId: dialog.id,
      ...(dialog.selectedRef === null ? {} : { selectedRef: dialog.selectedRef }),
      ...(dialog.sourceVersion === null ? {} : { sourceVersion: dialog.sourceVersion }),
    });
  }

  function updateGenerationDialogById(
    dialogId: number,
    update: (current: CanvasGenerationDialogModel) => CanvasGenerationDialogModel,
  ): void {
    const current = generationDialogRef.current;
    if (current === null || current.id !== dialogId) return;
    const next = update(current);
    generationDialogRef.current = next;
    setGenerationDialog(next);
  }

  async function ensureFreshGenerationTarget(
    dialog: CanvasGenerationDialogModel,
    scope: GenerationWorkspaceScope,
    signal: AbortSignal,
  ): Promise<boolean> {
    const sourcePort = services.canvasSource;
    if (sourcePort === undefined || dialog.selectedRef === null || dialog.sourceVersion === null) return false;
    const snapshot = await sourcePort.read(selectedChapter.id, signal);
    if (signal.aborted || !isGenerationDialogScopeCurrent(scope)) return false;
    if (!sourceSnapshotMatchesScope(snapshot)) {
      updateGenerationDialogById(dialog.id, (current) => ({
        ...current,
        error: "章节来源响应不属于当前剧集或章节；已停止读取生成记录。",
        loading: false,
      }));
      return false;
    }
    const selected = graph.nodes.find((node) => node.ref === dialog.selectedRef);
    const freshTarget = selected === undefined ? null : generationTargetForNode(snapshot, selected);
    if (snapshot.version !== dialog.sourceVersion || snapshot.requires_lock !== dialog.requiresLock || freshTarget === null
      || !sameGenerationTarget(freshTarget.target, dialog.target)) {
      applySourceSnapshot(snapshot);
      selectCanvasNode(null);
      setSourceMessage("章节来源或目标身份已变化。已只读刷新当前来源，没有把旧记录改绑到新目标；请重新选择后再读取。");
      closeGenerationDialog();
      return false;
    }
    return true;
  }

  async function loadGenerationPage(cursor: string | undefined, append: boolean): Promise<void> {
    const dialog = generationDialogRef.current;
    if (dialog === null || dialog.selectedRef === null || dialog.sourceVersion === null || dialog.loading
      || generationBusyRef.current) return;
    const scope = generationDialogScope(dialog);
    const controller = new AbortController();
    generationPageControllerRef.current?.abort();
    generationPageControllerRef.current = controller;
    updateGenerationDialogById(dialog.id, (current) => ({ ...current, loading: true, error: null, message: null }));
    try {
      if (!await ensureFreshGenerationTarget(dialog, scope, controller.signal)) return;
      const port = services.canvasOwnedResults;
      if (port === undefined) throw new CanvasOwnedResultsError("not-sent", "当前服务没有接入生成记录读取。", null);
      const run = port.beginRun({
        actorId: scope.actorId,
        seriesId: scope.seriesId,
        chapterId: scope.chapterId,
        isContextCurrent: () => isGenerationDialogScopeCurrent(scope),
      });
      if (!run.isCurrent() || controller.signal.aborted) {
        if (!controller.signal.aborted) {
          throw new CanvasOwnedResultsError("not-sent", "登录、章节或查询上下文已变化，没有读取生成记录。", null);
        }
        return;
      }
      const page = await run.page(dialog.target, controller.signal, cursor === undefined ? undefined : { cursor });
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) return;
      if (page.chapter_id !== scope.chapterId || !sameGenerationTarget(page.target, dialog.target)) {
        throw new CanvasOwnedResultsError("invalid-response", "生成记录响应与当前查询目标不一致。", null);
      }
      updateGenerationDialogById(dialog.id, (current) => {
        const items = append
          ? [...current.items, ...page.items.filter((item) => !current.items.some((existing) => existing.result_ref === item.result_ref))]
          : page.items;
        return {
          ...current,
          items,
          cursor,
          nextCursor: page.next_cursor,
          loading: false,
          error: null,
          message: null,
        };
      });
    } catch (cause) {
      if (controller.signal.aborted || !isGenerationWorkspaceScopeCurrent(scope)) return;
      updateGenerationDialogById(dialog.id, (current) => ({
        ...current,
        loading: false,
        error: generationFailureMessage(cause),
      }));
    } finally {
      if (generationPageControllerRef.current === controller) generationPageControllerRef.current = null;
      if (!controller.signal.aborted) {
        updateGenerationDialogById(dialog.id, (current) => current.loading ? { ...current, loading: false } : current);
      }
    }
  }

  function openGenerationResults(): void {
    const snapshot = sourcesRef.current;
    const selected = selectedNodeRef.current === null
      ? null
      : graph.nodes.find((node) => node.ref === selectedNodeRef.current) ?? null;
    const resolved = snapshot === null || selected === null ? null : generationTargetForNode(snapshot, selected);
    if (!generationPortsAvailable || snapshot === null || selected === null || resolved === null || generationEntryDisabled) {
      setSourceMessage(!generationPortsAvailable
        ? "当前还不能读取或应用生成图片记录。"
        : "请选择身份可确认的分镜、预览或角色/场景/道具素材；视频和无法唯一确认的目标不支持。");
      return;
    }
    const dialog: CanvasGenerationDialogModel = {
      id: ++generationDialogSequenceRef.current,
      selectedRef: selected.ref,
      target: resolved.target,
      targetLabel: resolved.label,
      sourceVersion: snapshot.version,
      requiresLock: snapshot.requires_lock,
      items: [],
      cursor: undefined,
      nextCursor: null,
      loading: false,
      error: null,
      message: null,
    };
    generationDialogRef.current = dialog;
    setGenerationDialog(dialog);
    setGenerationHistoryLookups(new Map());
    setGenerationConfirmedProof(null);
    setGenerationLockRequired(false);
    setGenerationLockUnavailable(false);
    setGenerationLockPending(false);
    setGenerationLockMessage(null);
    void loadGenerationPage(undefined, false);
  }

  function openPendingGenerationIntent(): void {
    const intent = generationIntentRef.current;
    if (intent === null || generationDialogRef.current !== null) return;
    const dialog: CanvasGenerationDialogModel = {
      id: ++generationDialogSequenceRef.current,
      selectedRef: null,
      target: intent.destination,
      targetLabel: generationTargetLabel(intent.destination),
      sourceVersion: null,
      requiresLock: intent.requiresLock,
      items: [],
      cursor: undefined,
      nextCursor: null,
      loading: false,
      error: null,
      message: null,
    };
    generationDialogRef.current = dialog;
    setGenerationDialog(dialog);
    setGenerationHistoryLookups(new Map());
    setGenerationLockRequired(false);
    setGenerationLockUnavailable(false);
    setGenerationLockPending(false);
    setGenerationLockMessage(null);
  }

  async function confirmGenerationEditLock(
    scope: GenerationWorkspaceScope,
    required: boolean,
    signal: AbortSignal,
  ): Promise<boolean> {
    if (!required) return true;
    const getLock = services.getChapterEditLock;
    if (getLock === undefined) {
      setGenerationLockUnavailable(true);
      setGenerationLockRequired(true);
      setGenerationLockMessage("当前无法确认编辑锁，暂时不能应用此结果。");
      return false;
    }
    const lock = await getLock.call(services, scope.chapterId, signal);
    if (signal.aborted || !isGenerationDialogScopeCurrent(scope)) return false;
    if (validCurrentLock(lock, scope.actorId)) {
      setGenerationLockRequired(false);
      setGenerationLockUnavailable(false);
      setGenerationLockMessage(null);
      return true;
    }
    setGenerationLockRequired(true);
    setGenerationLockUnavailable(services.acquireChapterEditLock === undefined);
    setGenerationLockMessage(lock.locked
      ? "章节编辑锁由其他成员持有；未应用此结果。"
      : "当前账号尚未持有有效章节编辑锁；未应用此结果。请明确取得锁后再操作。");
    return false;
  }

  async function acquireGenerationEditLock(): Promise<void> {
    const dialog = generationDialogRef.current;
    if (dialog === null || !dialog.requiresLock || generationBusyRef.current) return;
    const getLock = services.getChapterEditLock;
    const acquireLock = services.acquireChapterEditLock;
    if (getLock === undefined || acquireLock === undefined) {
      setGenerationLockUnavailable(true);
      setGenerationLockRequired(true);
      setGenerationLockMessage("当前无法操作章节编辑锁，没有取得锁。");
      return;
    }

    const scope = generationDialogScope(dialog);
    const controller = new AbortController();
    generationActionControllerRef.current?.abort();
    generationActionControllerRef.current = controller;
    setGenerationBusyState(true);
    setGenerationLockPending(true);
    setGenerationLockMessage(null);
    generationMutationPendingRef.current = true;
    try {
      let lock = await getLock.call(services, scope.chapterId, controller.signal);
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) return;
      if (validCurrentLock(lock, scope.actorId)) {
        setGenerationLockRequired(false);
        setGenerationLockMessage("已核实当前账号持有有效章节编辑锁。请再明确点击应用；不会自动提交。");
        return;
      }
      if (lock.locked) {
        setGenerationLockRequired(true);
        setGenerationLockMessage("其他成员正在编辑，未更改其锁；请稍后再试。");
        return;
      }

      const acquired = await acquireLock.call(services, scope.chapterId, controller.signal);
      if (acquired.lease !== null && isGenerationWorkspaceScopeCurrent(scope)) {
        acquiredLockLeaseRef.current = { chapterId: scope.chapterId, lease: acquired.lease };
      }
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) return;
      lock = await getLock.call(services, scope.chapterId, controller.signal);
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) return;
      if (!validCurrentLock(lock, scope.actorId)) {
        setGenerationLockRequired(true);
        setGenerationLockMessage("取得锁后仍无法确认当前账号持有锁；未应用此结果。");
        return;
      }
      setGenerationLockRequired(false);
      setGenerationLockUnavailable(false);
      setGenerationLockMessage("已取得并核实当前章节编辑锁。请再明确点击应用；不会自动提交。");
    } catch (cause) {
      if (controller.signal.aborted || !isGenerationWorkspaceScopeCurrent(scope)) return;
      updateGenerationDialogById(dialog.id, (current) => ({
        ...current,
        error: generationFailureMessage(cause),
      }));
    } finally {
      setGenerationLockPending(false);
      if (generationActionControllerRef.current === controller) {
        generationActionControllerRef.current = null;
        generationMutationPendingRef.current = false;
        setGenerationBusyState(false);
      }
    }
  }

  async function refreshSourceAfterGenerationProof(
    scope: GenerationWorkspaceScope,
    signal: AbortSignal,
  ): Promise<void> {
    try {
      onSourceChanged(scope.chapterId);
    } catch {
      setSourceMessage("应用记录已确认；章节刷新通知未完成，仍将重新读取当前内容。");
    }
    const sourcePort = services.canvasSource;
    if (sourcePort === undefined) {
      setSourceMessage("应用记录已确认；当前章节内容暂时无法刷新。");
      return;
    }
    try {
      const snapshot = await sourcePort.read(scope.chapterId, signal);
      if (signal.aborted || !isGenerationWorkspaceScopeCurrent(scope)
        || (scope.dialogId !== undefined && generationDialogRef.current?.id !== scope.dialogId)) return;
      if (!sourceSnapshotMatchesScope(snapshot)) {
        setSourceMessage("应用记录已确认；重新读取的内容属于其他剧集或章节，未并入当前画布。");
        return;
      }
      applySourceSnapshot(snapshot);
      setSourceError(null);
      setSourceLoading(false);
      setSourceMessage("应用记录已确认；当前章节内容已刷新。该记录不代表当前图像仍与当时相同。");
    } catch (cause) {
      if (signal.aborted || !isGenerationWorkspaceScopeCurrent(scope)
        || (scope.dialogId !== undefined && generationDialogRef.current?.id !== scope.dialogId)) return;
      setSourceMessage(`应用记录已确认，但当前章节内容未能刷新：${generationFailureMessage(cause)}。应用记录与当前图像状态分开显示。`);
    }
  }

  function acceptGenerationProof(
    resultRef: string,
    status: "applied" | "already_applied",
    application: { application_ref: string; target_revision: string; committed_at: string },
  ): void {
    setGenerationConfirmedProof({
      resultRef,
      status,
      applicationRef: application.application_ref,
      targetRevision: application.target_revision,
      committedAt: application.committed_at,
    });
  }

  function createUnknownGenerationIntent(
    scope: GenerationWorkspaceScope,
    destination: CanvasGenerationApplicationDestination,
    candidate: CanvasGenerationApplicationCandidate,
    sourceVersion: string,
    requiresLock: boolean,
  ): WorkspaceGenerationIntent {
    return {
      destination,
      candidate,
      sourceVersion,
      state: "unknown",
      lookup: null,
      actorId: scope.actorId,
      seriesId: scope.seriesId,
      chapterId: scope.chapterId,
      sessionGeneration: scope.sessionGeneration,
      scopeKey: scope.scopeKey,
      workspaceEpoch: scope.workspaceEpoch,
      sourceEpoch: scope.sourceEpoch,
      servicesIdentity: scope.servicesIdentity,
      requiresLock,
    };
  }

  async function sendGenerationApplication(
    destination: CanvasGenerationApplicationDestination,
    candidate: CanvasGenerationApplicationCandidate,
    requiresLock: boolean,
    sourceVersion: string,
    retryIntent: WorkspaceGenerationIntent | null,
  ): Promise<void> {
    const dialog = generationDialogRef.current;
    if (dialog === null || generationBusyRef.current || generationMutationPendingRef.current) return;
    if (retryIntent === null && (dialog.selectedRef === null || dialog.sourceVersion === null
      || !dialog.items.some((item) => item.task_id === candidate.task_id && item.result_ref === candidate.result_ref
        && item.eligibility === "ready"))) return;
    if (retryIntent !== null && (generationIntentRef.current !== retryIntent
      || retryIntent.state !== "not_applied" || retryIntent.lookup?.status !== "not_applied")) return;

    const scope = generationDialogScope(dialog);
    if (!isGenerationDialogScopeCurrent(scope)) return;
    if (destination.chapter_id !== scope.chapterId) return;
    if (retryIntent !== null && (retryIntent.actorId !== scope.actorId
      || retryIntent.seriesId !== scope.seriesId || retryIntent.chapterId !== scope.chapterId
      || retryIntent.sessionGeneration !== scope.sessionGeneration || retryIntent.scopeKey !== scope.scopeKey
      || retryIntent.workspaceEpoch !== scope.workspaceEpoch || retryIntent.sourceEpoch !== scope.sourceEpoch
      || retryIntent.servicesIdentity !== scope.servicesIdentity)) return;
    const controller = new AbortController();
    generationActionControllerRef.current?.abort();
    generationActionControllerRef.current = controller;
    generationMutationPendingRef.current = true;
    setGenerationBusyState(true);
    setGenerationLockMessage(null);
    updateGenerationDialogById(dialog.id, (current) => ({ ...current, error: null, message: null }));
    let requestStarted = false;
    try {
      if (retryIntent === null && !await ensureFreshGenerationTarget(dialog, scope, controller.signal)) return;
      let currentLockRequired = requiresLock;
      if (retryIntent !== null) {
        const sourcePort = services.canvasSource;
        if (sourcePort === undefined) {
          throw new CanvasSourceError("unsupported", "当前无法确认章节内容，未重新提交原操作。");
        }
        const snapshot = await sourcePort.read(scope.chapterId, controller.signal);
        if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) return;
        if (!sourceSnapshotMatchesScope(snapshot)) {
          throw new CanvasSourceError("conflict", "当前章节身份无法确认，未重新提交原操作。", 409);
        }
        currentLockRequired = currentLockRequired || snapshot.requires_lock;
      }
      if (!await confirmGenerationEditLock(scope, currentLockRequired, controller.signal)) return;
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) return;
      const port = services.canvasGenerationApplications;
      if (port === undefined) throw new CanvasGenerationApplicationError("not-sent", "当前服务没有接入生成结果应用。", null);
      const run = port.beginRun({
        actorId: scope.actorId,
        seriesId: scope.seriesId,
        chapterId: scope.chapterId,
        isContextCurrent: () => isGenerationDialogScopeCurrent(scope),
      });
      if (!run.isCurrent() || controller.signal.aborted) {
        if (!controller.signal.aborted) {
          throw new CanvasGenerationApplicationError("not-sent", "登录、章节或应用上下文已变化，没有发送生成结果应用。", null);
        }
        return;
      }
      requestStarted = true;
      if (retryIntent !== null) {
        setWorkspaceGenerationIntent({ ...retryIntent, state: "unknown", lookup: null });
      }
      const result = await run.apply(destination, candidate, controller.signal);
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) {
        if (isGenerationWorkspaceScopeCurrent(scope)) {
          setWorkspaceGenerationIntent(retryIntent === null
            ? createUnknownGenerationIntent(scope, destination, candidate, sourceVersion, requiresLock)
            : { ...retryIntent, state: "unknown", lookup: null });
          setSourceMessage("应用结果在对话或目标变化后才到达，未并入当前画布。原操作已保留，请重新核对应用记录。");
        }
        return;
      }
      if (result.task_id !== candidate.task_id || result.result_ref !== candidate.result_ref) {
        throw new CanvasGenerationApplicationError("unknown-outcome", "应用结果与原操作不一致；处理结果暂时无法确认。", null);
      }
      acceptGenerationProof(result.result_ref, result.status, result.application);
      if (generationIntentRef.current !== null
        && generationIntentRef.current.candidate.task_id === candidate.task_id
        && generationIntentRef.current.candidate.result_ref === candidate.result_ref) {
        setWorkspaceGenerationIntent(null);
      }
      updateGenerationDialogById(dialog.id, (current) => ({
        ...current,
        error: null,
        message: `${generationOutcomeLabel(result.status)}。正在刷新章节内容；应用记录不代表当前图像仍与当时相同。`,
      }));
      await refreshSourceAfterGenerationProof(scope, controller.signal);
    } catch (cause) {
      if (!isGenerationWorkspaceScopeCurrent(scope)) return;
      const knownNotSent = cause instanceof CanvasGenerationApplicationError && cause.kind === "not-sent";
      const unknownOutcome = requestStarted && !knownNotSent
        && (!(cause instanceof CanvasGenerationApplicationError) || cause.kind === "unknown-outcome");
      if (retryIntent !== null && knownNotSent) {
        setWorkspaceGenerationIntent(retryIntent);
      } else if (unknownOutcome || (retryIntent !== null && requestStarted && !knownNotSent)) {
        setWorkspaceGenerationIntent(retryIntent === null
          ? createUnknownGenerationIntent(scope, destination, candidate, sourceVersion, requiresLock)
          : { ...retryIntent, state: "unknown", lookup: null });
        setGenerationConfirmedProof(null);
        setSourceMessage("上次提交结果暂时无法确认。原操作已保留；请先核对应用记录，不会自动重新提交。");
      }
      if (isGenerationDialogScopeCurrent(scope)) {
        updateGenerationDialogById(dialog.id, (current) => ({
          ...current,
          error: generationFailureMessage(cause),
        }));
      }
    } finally {
      if (generationActionControllerRef.current === controller) {
        generationActionControllerRef.current = null;
        generationMutationPendingRef.current = false;
        setGenerationBusyState(false);
      }
    }
  }

  function applyGenerationItem(item: CanvasOwnedResultItem): void {
    if (item.eligibility !== "ready" || item.expected_target === null || item.application_key === null) return;
    const dialog = generationDialogRef.current;
    if (dialog === null || dialog.selectedRef === null || dialog.sourceVersion === null) return;
    const candidate: CanvasGenerationApplicationCandidate = {
      task_id: item.task_id,
      result_ref: item.result_ref,
      expected_target: item.expected_target,
      application_key: item.application_key,
    };
    void sendGenerationApplication(dialog.target, candidate, dialog.requiresLock, dialog.sourceVersion, null);
  }

  function retryPendingGenerationIntent(): void {
    const intent = generationIntentRef.current;
    if (intent === null || intent.state !== "not_applied" || intent.lookup?.status !== "not_applied") return;
    void sendGenerationApplication(
      intent.destination,
      intent.candidate,
      intent.requiresLock,
      intent.sourceVersion,
      intent,
    );
  }

  async function lookupGenerationResult(item: CanvasOwnedResultItem): Promise<void> {
    const dialog = generationDialogRef.current;
    const port = services.canvasResultApplications;
    if (dialog === null || port === undefined || generationBusyRef.current) return;
    const scope = generationDialogScope(dialog);
    if (!isGenerationDialogScopeCurrent(scope)) return;
    const controller = new AbortController();
    generationActionControllerRef.current?.abort();
    generationActionControllerRef.current = controller;
    setGenerationBusyState(true);
    try {
      const run = port.beginRun({
        actorId: scope.actorId,
        seriesId: scope.seriesId,
        chapterId: scope.chapterId,
        isContextCurrent: () => isGenerationDialogScopeCurrent(scope),
      });
      if (!run.isCurrent() || controller.signal.aborted) {
        if (!controller.signal.aborted) {
          throw new CanvasResultApplicationError("not-sent", "登录或章节状态已变化，未读取应用记录。", null);
        }
        return;
      }
      const lookup = await run.lookup({ taskId: item.task_id, resultRef: item.result_ref }, controller.signal);
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)) return;
      setGenerationHistoryLookups((current) => new Map(current).set(item.result_ref, lookup));
      if (lookup.status === "applied" && lookup.application !== null) {
        acceptGenerationProof(item.result_ref, "already_applied", lookup.application);
        updateGenerationDialogById(dialog.id, (current) => ({
          ...current,
          message: "此前已应用。正在刷新章节内容；应用记录不代表当前图像仍与当时相同。",
        }));
        await refreshSourceAfterGenerationProof(scope, controller.signal);
      }
    } catch (cause) {
      if (controller.signal.aborted || !isGenerationWorkspaceScopeCurrent(scope)) return;
      updateGenerationDialogById(dialog.id, (current) => ({ ...current, error: generationFailureMessage(cause) }));
    } finally {
      if (generationActionControllerRef.current === controller) {
        generationActionControllerRef.current = null;
        setGenerationBusyState(false);
      }
    }
  }

  function beginGenerationImagePreview(item: CanvasOwnedResultItem): CanvasOwnedImageRun | null {
    const dialog = generationDialogRef.current;
    const port = services.canvasOwnedImage;
    if (dialog === null || port === undefined || item.eligibility === "unready"
      || !dialog.items.some((candidate) => candidate.task_id === item.task_id
        && candidate.result_ref === item.result_ref)) return null;

    const dialogScope = generationDialogScope(dialog);
    const scope: GenerationWorkspaceScope = {
      ...dialogScope,
      selectedRef: dialog.selectedRef ?? selectedNodeRef.current,
      ...(dialogScope.sourceVersion !== undefined || sourcesRef.current === null
        ? {}
        : { sourceVersion: sourcesRef.current.version }),
    };
    if (!isGenerationDialogScopeCurrent(scope)) return null;

    const run = port.beginRun({
      actorId: scope.actorId,
      seriesId: scope.seriesId,
      chapterId: scope.chapterId,
      isContextCurrent: () => isGenerationDialogScopeCurrent(scope),
    });
    return run.isCurrent() ? run : null;
  }

  async function lookupPendingGenerationIntent(): Promise<void> {
    const dialog = generationDialogRef.current;
    const intent = generationIntentRef.current;
    const port = services.canvasResultApplications;
    if (dialog === null || intent === null || port === undefined || generationBusyRef.current) return;
    const scope = generationDialogScope(dialog);
    if (!isGenerationWorkspaceScopeCurrent(scope)
      || intent.actorId !== scope.actorId || intent.seriesId !== scope.seriesId || intent.chapterId !== scope.chapterId) return;
    const controller = new AbortController();
    generationActionControllerRef.current?.abort();
    generationActionControllerRef.current = controller;
    setGenerationBusyState(true);
    try {
      const run = port.beginRun({
        actorId: intent.actorId,
        seriesId: intent.seriesId,
        chapterId: intent.chapterId,
        isContextCurrent: () => isGenerationDialogScopeCurrent(scope)
          && generationIntentRef.current?.candidate.task_id === intent.candidate.task_id
          && generationIntentRef.current?.candidate.result_ref === intent.candidate.result_ref,
      });
      if (!run.isCurrent() || controller.signal.aborted) {
        if (!controller.signal.aborted) {
          throw new CanvasResultApplicationError("not-sent", "登录或章节状态已变化，未核对原操作。", null);
        }
        return;
      }
      const lookup = await run.lookup({
        taskId: intent.candidate.task_id,
        resultRef: intent.candidate.result_ref,
      }, controller.signal);
      if (controller.signal.aborted || !isGenerationDialogScopeCurrent(scope)
        || generationIntentRef.current?.candidate.task_id !== intent.candidate.task_id
        || generationIntentRef.current?.candidate.result_ref !== intent.candidate.result_ref) return;
      if (lookup.status === "applied" && lookup.application !== null) {
        acceptGenerationProof(lookup.result_ref, "already_applied", lookup.application);
        setWorkspaceGenerationIntent(null);
        updateGenerationDialogById(dialog.id, (current) => ({
          ...current,
          message: "此前已应用。正在刷新章节内容；应用记录不代表当前图像仍与当时相同。",
        }));
        await refreshSourceAfterGenerationProof(scope, controller.signal);
      } else {
        setWorkspaceGenerationIntent({ ...intent, state: "not_applied", lookup });
        updateGenerationDialogById(dialog.id, (current) => ({
          ...current,
          message: "暂未查到应用记录，之前请求仍可能稍后完成。只有明确选择重新提交原操作时，系统才会再次提交。",
        }));
      }
    } catch (cause) {
      if (controller.signal.aborted || !isGenerationWorkspaceScopeCurrent(scope)) return;
      updateGenerationDialogById(dialog.id, (current) => ({ ...current, error: generationFailureMessage(cause) }));
    } finally {
      if (generationActionControllerRef.current === controller) {
        generationActionControllerRef.current = null;
        setGenerationBusyState(false);
      }
    }
  }

  return (
    <div className="canvas-dialog-backdrop">
      <section aria-labelledby="canvas-title" aria-modal="true" className="canvas-dialog" ref={focus.dialogRef} role="dialog" tabIndex={-1}>
        <header className="canvas-header">
          <div>
            <p className="eyebrow">{series.name}</p>
            <h2 id="canvas-title">{sources?.chapter.title ?? selectedChapter.title} · 创作画布</h2>
            <span className="canvas-save-status" role="status">{displayedStatus}</span>
          </div>
          <div className="canvas-header-actions">
            <button className="secondary-button" disabled={sourceOutcomeUnresolved || !documentState.writable || history.undo.length === 0} onClick={undo} type="button">撤销</button>
            <button className="secondary-button" disabled={sourceOutcomeUnresolved || !documentState.writable || history.redo.length === 0} onClick={redo} type="button">重做</button>
            <button className="primary-button" disabled={sourceOutcomeUnresolved || !documentState.dirty || documentState.status === "saving" || documentState.status === "unknown" || documentState.status === "conflict" || !documentState.writable} onClick={saveLayout} type="button">保存布局</button>
            <button aria-label="关闭创作画布" className="canvas-close-button" onClick={close} type="button">关闭</button>
          </div>
        </header>

        <div className="canvas-toolbar">
          <div className="canvas-filter-row" aria-label="节点筛选">
            {FILTERS.map((item) => (
              <button
                aria-pressed={filter === item.id}
                className={filter === item.id ? "is-active" : ""}
                key={item.id}
                onClick={() => setFilter(item.id)}
                type="button"
              >{item.label}</button>
            ))}
          </div>
          <div className="canvas-relation-row" aria-label="关系显示">
            {(["uses", "reference", "generates", "manual"] as const).map((relation) => {
              const hidden = Array.isArray(displayDocument.hiddenRelations)
                && displayDocument.hiddenRelations.includes(relation);
              const duplicate = Array.isArray(displayDocument.hiddenRelations)
                && displayDocument.hiddenRelations.filter((item) => item === relation).length > 1;
              return (
                <button
                  aria-pressed={!hidden}
                  disabled={!documentState.writable || sourceOutcomeUnresolved || duplicate}
                  key={relation}
                  onClick={() => toggleRelation(relation)}
                  type="button"
                >{hidden ? "显示 " : "隐藏 "}{relation} 关系</button>
              );
            })}
          </div>
          <div className="canvas-tool-row">
            <button disabled={!documentState.writable || sourceOutcomeUnresolved} onClick={() => createTextItem("notes")} type="button">新增便签</button>
            <button disabled={!documentState.writable || sourceOutcomeUnresolved} onClick={() => createTextItem("cards")} type="button">新增文本卡片</button>
            <button disabled={imageGenerationEntryDisabled} onClick={openImageGeneration} type="button">生成图片</button>
            <button disabled={generationEntryDisabled} onClick={openGenerationResults} type="button">生成图片记录</button>
            {imageGeneration.busy && (
              <span className="canvas-save-status" role="status">正在确认单张图片任务状态。请等待本次操作结束。</span>
            )}
            {imageGeneration.intent !== null && (
              <>
                <span className="canvas-save-status" role="alert">
                  {imageGeneration.intent.state === "not-found"
                    ? "暂未查到图片任务记录，之前请求仍可能稍后完成。"
                    : "上次图片任务提交结果暂时无法确认，原操作已保留。"}
                </span>
                <button disabled={imageGeneration.busy || generationDialog !== null} onClick={openPendingImageGeneration} type="button">
                  继续核对原提交
                </button>
              </>
            )}
            {imageGeneration.receipt !== null && imageGeneration.state.mode === null && (
              <>
                <span className="canvas-save-status" role="status">最近的单张图片任务已有受理记录。</span>
                <button onClick={openImageGenerationReceipt} type="button">查看生成任务状态</button>
              </>
            )}
            {(services.creatorSettings === undefined || services.canvasImageGeneration === undefined) && (
              <span className="canvas-save-status" role="status">单张图片任务服务未接入。</span>
            )}
            {generationIntent !== null && (
              <>
                <span className="canvas-save-status" role="alert">
                  {generationIntent.state === "unknown"
                    ? "上次提交结果暂时无法确认，原操作已保留。"
                    : "暂未查到应用记录，之前请求仍可能稍后完成。"}
                </span>
                <button disabled={generationDialog !== null || generationBusy} onClick={openPendingGenerationIntent} type="button">
                  继续核对原操作
                </button>
              </>
            )}
            <button aria-pressed={connecting} disabled={!documentState.writable || sourceOutcomeUnresolved} onClick={() => {
              setConnecting((value) => !value);
              setConnectionSource(null);
            }} type="button">{connecting ? "取消连线" : "手动连线"}</button>
            <button disabled={sourceOutcomeUnresolved} onClick={() => changeViewport({ ...viewport, zoom: clampZoom(viewport.zoom / 1.15) })} type="button" aria-label="缩小">−</button>
            <span>{Math.round(viewport.zoom * 100)}%</span>
            <button disabled={sourceOutcomeUnresolved} onClick={() => changeViewport({ ...viewport, zoom: clampZoom(viewport.zoom * 1.15) })} type="button" aria-label="放大">＋</button>
            <button disabled={sourceOutcomeUnresolved} onClick={fitContent} type="button">适配内容</button>
            <button disabled={!documentState.writable || sourceOutcomeUnresolved || (selectedNode === null && selectedEdge === null)} onClick={removeSelected} type="button">删除选中</button>
            {!generationPortsAvailable && <span className="canvas-save-status" role="status">生成记录服务未完整接入。</span>}
            {generationPortsAvailable && selectedGenerationTarget === null && (
              <span className="canvas-save-status" role="status">选中身份可确认的分镜、预览或角色/场景/道具素材；视频不支持。</span>
            )}
            {generationConfirmedProof !== null && (
              <span className="canvas-save-status" role="status">
                {generationOutcomeLabel(generationConfirmedProof.status)}。已找到对应记录；当前图像可能已有后续修改。
              </span>
            )}
          </div>
          <div aria-label="章节来源编辑" className="canvas-tool-row">
            <button disabled={sourceActionDisabled || (selectedFrameNode !== null && selectedFrameTarget === null)}
              onClick={() => openFrameStructureEditor({
                kind: "insert",
                afterPosition: selectedFrameNode?.frameIndex === undefined ? 0 : selectedFrameNode.frameIndex + 1,
              })} type="button">插入分镜</button>
            <button disabled={sourceActionDisabled || selectedFrameNode === null || selectedFrameTarget === null}
              onClick={() => selectedFrameNode !== null && openFrameTextEditor(selectedFrameNode)} type="button">编辑分镜文字</button>
            <button disabled={sourceActionDisabled || selectedFrameNode === null || selectedFrameTarget === null || selectedFrameCount <= 1}
              onClick={() => selectedFrameNode?.frameIndex !== undefined
                && openFrameStructureEditor({ kind: "delete", position: selectedFrameNode.frameIndex + 1 })} type="button">删除选中分镜</button>
            <button disabled={sourceActionDisabled} onClick={() => openAssetEditor("character", "create", null)} type="button">新增角色素材</button>
            <button disabled={sourceActionDisabled} onClick={() => openAssetEditor("scene", "create", null)} type="button">新增场景素材</button>
            <button disabled={sourceActionDisabled} onClick={() => openAssetEditor("prop", "create", null)} type="button">新增道具素材</button>
            <button disabled={sourceActionDisabled || selectedAssetKind === null || selectedAsset === null}
              onClick={() => selectedAssetKind !== null && selectedAsset !== null
                && openAssetEditor(selectedAssetKind, "edit", selectedAsset)} type="button">编辑选中素材</button>
            <button disabled={sourceActionDisabled || selectedFrameNode === null || selectedFrameTarget === null}
              onClick={() => selectedFrameTarget !== null && openMediaEditor(selectedFrameTarget, "preview", null, false)} type="button">设置镜头预览 URL</button>
            <button disabled={sourceActionDisabled || selectedFrameNode === null || selectedFrameTarget === null}
              onClick={() => selectedFrameTarget !== null && openMediaEditor(selectedFrameTarget, "video", null, true)} type="button">追加视频 URL 或 pending</button>
            <button disabled={sourceActionDisabled || selectedMediaNode === null || selectedMediaTarget === null
              || selectedMediaNode.sourceField === undefined}
              onClick={() => selectedMediaNode?.sourceField !== undefined && selectedMediaTarget !== null
                && openMediaEditor(selectedMediaTarget, selectedMediaNode.sourceField, selectedMediaNode.sourceSlot ?? null, false)} type="button">编辑选中媒体来源</button>
            {creationUndos.map((undo) => (
              <button disabled={sourceActionDisabled || sources?.version !== undo.sourceVersion}
                key={undo.id} onClick={() => void undoCreation(undo)} type="button">撤销刚创建的{undo.label}</button>
            ))}
          </div>
        </div>

        {sourceError !== null && <p className="canvas-error" role="alert">{sourceError}</p>}
        {sourceError !== null && services.canvasSource !== undefined && (
          <button className="secondary-button" disabled={sourceMutationPending}
            onClick={() => void verifySourceRead(new AbortController().signal).catch((cause: unknown) => {
              setSourceMessage(cause instanceof Error ? cause.message : "只读读取章节来源失败。");
            })} type="button">重试读取受控章节来源</button>
        )}
        {sourceMessage !== null && <p className="canvas-message" role="status">{sourceMessage}</p>}
        {sourceReadbackPending && (
          <div className="canvas-message" role="alert">
            <p>来源写接口已返回服务器确认，但独立 GET 尚未完成。原请求不会重发，来源和布局写入暂时暂停。</p>
            <button disabled={sourceMutationPending} onClick={() => void verifySourceRead(new AbortController().signal).catch((cause: unknown) => {
              setSourceMessage(cause instanceof Error ? cause.message : "只读核对来源失败。");
            })} type="button">只读核对当前来源</button>
          </div>
        )}
        {unknownSourceIntent !== null && (
          <div className="canvas-message" role="alert">
            <p>{unknownSourceIntent.kind === "unknown"
              ? "来源写入结果未知。保留原冻结意图，只能读取当前来源核对；不会自动重发，也不会按名称推断创建成功。"
              : "来源已冲突。原意图仍绑定旧版本，不会自动改绑到最新镜头或文字。"}</p>
            <button disabled={sourceMutationPending} onClick={() => void verifySourceRead(new AbortController().signal).catch((cause: unknown) => {
              setSourceMessage(cause instanceof Error ? cause.message : "只读核对来源失败。");
            })} type="button">只读核对当前来源</button>
            {unknownSourceIntent.verifiedSnapshot !== null && (
              <button onClick={adoptVerifiedSource} type="button">采用当前读取并放弃原意图</button>
            )}
          </div>
        )}
        {documentState.message !== null && <p className="canvas-message" role="status">{documentState.message}</p>}
        {(documentState.status === "conflict" || documentState.status === "unknown") && (
          <div className="canvas-message">
            <p>{documentState.status === "unknown"
              ? "保存结果尚未确认。本地草稿已保留，自动保存与快捷键保存已暂停；读取服务器内容不会确认之前的请求结果。"
              : "服务器内容已变化。本地草稿已保留，自动保存与快捷键保存已暂停；请核对后选择下一步。"}</p>
            <button onClick={() => void documentState.reviewServer()} type="button">核对服务器内容</button>
            {documentState.reviewedServer !== null && (
              <>
                <button
                  disabled={documentState.reviewedServer.save_capability !== "guarded-v1"}
                  onClick={() => void documentState.resumeReviewedDraft()}
                  type="button"
                >基于服务器版本继续保存草稿</button>
                <button onClick={() => { documentState.adoptReviewedServer(); setHistory(EMPTY_CANVAS_HISTORY); }} type="button">
                  采用服务器内容并放弃本地草稿
                </button>
              </>
            )}
          </div>
        )}

        <div
          className="canvas-viewport"
          onPointerDown={(event) => beginDrag(event, null)}
          onPointerMove={movePointer}
          onPointerUp={(event) => finishDrag(event.pointerId)}
          onPointerCancel={() => { if (drag !== null) finishDrag(drag.pointerId); }}
          onWheel={zoomAt}
          ref={viewportRef}
          role="region"
          aria-label="画布工作区"
        >
          {sourceLoading || documentState.status === "loading" ? (
            <div className="canvas-empty-state" role="status">正在读取当前章节的真实内容…</div>
          ) : sourceError !== null ? (
            <div className="canvas-empty-state" role="alert">{sourceError}</div>
          ) : (
            <div className="canvas-scene" style={{ transform: "translate(" + viewport.x + "px, " + viewport.y + "px) scale(" + viewport.zoom + ")" }}>
              <svg aria-label="节点关系" className="canvas-edge-layer" height="3000" width="4000">
                {visibleEdges.map((edge) => {
                  const source = nodeByRef.get(edge.sourceRef);
                  const target = nodeByRef.get(edge.targetRef);
                  if (source === undefined || target === undefined) return null;
                  const x1 = source.x + source.width;
                  const y1 = source.y + source.height / 2;
                  const x2 = target.x;
                  const y2 = target.y + target.height / 2;
                  const bend = Math.max(70, Math.abs(x2 - x1) / 2);
                  return (
                    <path
                      className={edge.manual ? "canvas-edge is-manual" : "canvas-edge"}
                      d={"M " + x1 + " " + y1 + " C " + (x1 + bend) + " " + y1 + ", " + (x2 - bend) + " " + y2 + ", " + x2 + " " + y2}
                      key={edge.id}
                      onClick={() => edge.manual && setSelectedEdge(edge.id)}
                      stroke={selectedEdge === edge.id ? "#7c3aed" : undefined}
                    />
                  );
                })}
              </svg>
              {visibleNodes.map((node) => (
                <article
                  aria-label={node.title}
                  className={"canvas-node canvas-node-" + node.kind + (selectedNode === node.ref ? " is-selected" : "") + (connecting ? " is-connectable" : "")}
                  key={node.key}
                  onClick={() => { selectCanvasNode(node.ref); setSelectedEdge(null); }}
                  onPointerDown={(event) => beginDrag(event, node)}
                  style={{ left: node.x, top: node.y, width: node.width, minHeight: node.height }}
                  tabIndex={0}
                >
                  <strong className="canvas-node-title">{node.title}</strong>
                  {node.imageUrl !== null && <CanvasMediaPreview imageUrl={node.imageUrl} key={scopeKey + ":" + node.imageUrl} port={port} scopeKey={scopeKey} />}
                  {node.itemCollection !== undefined ? (
                    <textarea
                      aria-label={node.title + "文字"}
                      onChange={(event) => updateText(node, event.currentTarget.value)}
                      onPointerDown={(event) => event.stopPropagation()}
                      value={node.detail}
                    />
                  ) : <p>{node.detail}</p>}
                </article>
              ))}
            </div>
          )}
          {connecting && <div className="canvas-connect-hint" role="status">
            {connectionSource === null ? "选择连线起点，再选择终点。" : "已选起点，请选择终点。"}
          </div>}
        </div>

        {sourceEditor?.kind === "frame-text" && (
          <FrameTextEditor
            allowOverwriteOnConflict={false}
            chapter={sourceEditor.frozen.chapter}
            initialText={frameText(sourceEditor.frozen.chapter.content?.[sourceEditor.node.frameIndex!])}
            lifecycleSignal={sourceEditor.lifecycle.signal}
            lockNotice="提交前会重新确认章节归属、来源版本和当前编辑锁。"
            onClose={() => closeSourceEditor(sourceEditor.lifecycle)}
            onSave={(text, signal) => saveFrameText(sourceEditor, text, signal)}
            onVerifyUnknown={(signal) => verifySourceRead(signal)}
            position={(sourceEditor.node.frameIndex ?? 0) + 1}
          />
        )}
        {sourceEditor?.kind === "frame-structure" && (
          <FrameStructureEditor
            chapter={sourceEditor.frozen.chapter}
            characters={sourceEditor.frozen.characters}
            lifecycleSignal={sourceEditor.lifecycle.signal}
            lockNotice="提交前会重新确认章节归属、来源版本和当前编辑锁。"
            mode={sourceEditor.mode}
            onClose={() => closeSourceEditor(sourceEditor.lifecycle)}
            onSubmit={(draft, signal) => submitFrameStructure(sourceEditor, draft, signal)}
            onVerify={async (signal) => ({
              status: "unknown",
              message: await verifySourceRead(signal),
            })}
            props={sourceEditor.frozen.props}
            scenes={sourceEditor.frozen.scenes}
          />
        )}
        {sourceEditor?.kind === "asset" && (
          <AssetManagementDialog
            category={assetCategoryForKind(sourceEditor.assetKind)}
            isCurrent={() => editorIsCurrent(sourceEditor.lifecycle)}
            item={sourceEditor.item}
            mode={sourceEditor.mode}
            onClose={() => closeSourceEditor(sourceEditor.lifecycle)}
            onConfirmedEntity={(entity) => {
              const label = sourceEditor.assetKind === "character" ? "角色"
                : sourceEditor.assetKind === "scene" ? "场景" : "道具";
              const name = "title" in entity ? entity.title : "name" in entity ? entity.name : "";
              const action = sourceEditor.mode === "create" ? "创建" : "修改";
              setSourceMessage(name.trim() === ""
                ? `服务器已确认${label}素材${action}。`
                : `服务器已确认${label}素材“${name}”${action}。`);
            }}
            onDirectoryNeedsRefresh={() => undefined}
            onDirectoryUpdated={() => undefined}
            onUnauthorized={onUnauthorized}
            guardedMutation={createGuardedAssetMutation(sourceEditor)}
            seriesId={series.id}
            seriesName={series.name}
            services={services}
          />
        )}
        {sourceEditor?.kind === "media" && (
          <CanvasMediaSourceEditor
            initialValue={sourceEditor.initialValue}
            lifecycleSignal={sourceEditor.lifecycle.signal}
            onClose={() => closeSourceEditor(sourceEditor.lifecycle)}
            onSave={(value, signal) => saveMediaSource(sourceEditor, value, signal)}
            onVerify={(signal) => verifySourceRead(signal)}
            title={sourceEditor.append ? "追加视频来源" : sourceEditor.field === "preview" ? "编辑预览 URL" : `编辑视频槽 ${Number(sourceEditor.slot) + 1}`}
            valueLabel={sourceEditor.field === "preview" ? "预览 URL" : "视频 URL 或 pending"}
          />
        )}

        {generationDialog !== null && (
          <CanvasGenerationResultsDialog
            busy={generationBusy}
            confirmedProof={generationConfirmedProof}
            error={generationDialog.error}
            imagePreviewAvailable={services.canvasOwnedImage !== undefined}
            imagePreviewScopeKey={generationImagePreviewScopeKey}
            imagePreviewServiceIdentity={serviceIdentity}
            historyLookups={generationHistoryLookups}
            intentOnly={generationDialog.selectedRef === null}
            items={generationDialog.items}
            loading={generationDialog.loading}
            lockMessage={generationLockMessage}
            lockPending={generationLockPending}
            lockRequired={generationLockRequired}
            lockUnavailable={generationLockUnavailable}
            message={generationDialog.message}
            nextCursor={generationDialog.nextCursor}
            onAcquireLock={() => void acquireGenerationEditLock()}
            onApply={applyGenerationItem}
            onBeginImagePreview={beginGenerationImagePreview}
            onClose={closeGenerationDialog}
            onLoadMore={() => {
              const current = generationDialogRef.current;
              if (current?.nextCursor !== null && current?.nextCursor !== undefined) {
                void loadGenerationPage(current.nextCursor, true);
              }
            }}
            onLookupPendingIntent={() => void lookupPendingGenerationIntent()}
            onRefresh={() => {
              const current = generationDialogRef.current;
              if (current !== null) void loadGenerationPage(current.cursor, false);
            }}
            onRetryPendingIntent={retryPendingGenerationIntent}
            onVerify={(item) => void lookupGenerationResult(item)}
            pendingIntent={generationIntent}
            targetLabel={generationDialog.targetLabel}
          />
        )}

        {imageGeneration.state.mode !== null && (
          <CanvasImageGenerationDialog
            busy={imageGeneration.state.busy}
            error={imageGeneration.state.error}
            lockPending={imageGeneration.state.lockPending}
            lockReady={imageGeneration.state.lockReady}
            lockRequired={imageGeneration.state.target?.requiresLock ?? false}
            lockActionAvailable={services.getChapterEditLock !== undefined && services.acquireChapterEditLock !== undefined}
            message={imageGeneration.state.lockMessage ?? imageGeneration.state.message}
            mode={imageGeneration.state.mode}
            models={imageGeneration.state.models}
            modelsLoading={imageGeneration.state.modelsLoading}
            promptTemplates={imageGeneration.state.promptTemplates}
            promptTemplatesLoading={imageGeneration.state.promptTemplatesLoading}
            promptTemplateError={imageGeneration.state.promptTemplateError}
            promptTemplateSelectionInvalid={imageGeneration.state.promptTemplateSelectionInvalid}
            selectedPromptTemplateId={imageGeneration.state.selectedPromptTemplateId}
            selectedSize={imageGeneration.state.selectedSize}
            sizeSelectionInvalid={imageGeneration.state.sizeSelectionInvalid}
            onAcquireLock={() => void imageGeneration.acquireLock()}
            onChangePrompt={imageGeneration.setPrompt}
            onClose={imageGeneration.close}
            onLookup={() => void imageGeneration.lookupPendingIntent()}
            onRetry={() => void imageGeneration.retryOriginalIntent()}
            onSelectModel={imageGeneration.setSelectedModelId}
            onSelectSize={imageGeneration.setSelectedSize}
            onSelectPromptTemplate={imageGeneration.setSelectedPromptTemplateId}
            onStartNew={startAnotherImageGeneration}
            onSubmit={() => void imageGeneration.submitNew()}
            pendingIntent={imageGeneration.state.intent}
            prompt={imageGeneration.state.prompt}
            receipt={imageGeneration.state.receipt}
            selectedModelId={imageGeneration.state.selectedModelId}
            targetLabel={imageGenerationDialogTargetLabel ?? "单张图片任务"}
          />
        )}

        {closePrompt && (
          <div className="canvas-confirm-backdrop">
            <section aria-labelledby="canvas-close-title" aria-modal="true" className="canvas-close-confirm"
              ref={closePromptFocus.dialogRef} role="dialog" tabIndex={-1}>
              <h3 id="canvas-close-title">关闭创作画布</h3>
              {imageGenerationUnresolvedRef.current
                ? <p>单张图片任务提交结果仍未确认。关闭画布不会取消可能已经提交的任务；请留在当前画布核对原操作。</p>
                : imageGenerationBusyRef.current
                  ? <p>单张图片任务正在处理。等待本次提交状态确认后再关闭画布。</p>
                : sourceOutcomeUnresolved
                ? <p>应用仍未确认。关闭画布不会撤回可能已完成的操作，也不会自动重新提交；请留在当前画布核对应用记录。</p>
                : documentState.status === "unknown"
                ? <p>保存结果尚未确认。放弃仅清除本地草稿，之前的服务器请求仍可能已经提交。</p>
                : documentState.status === "saving"
                  ? <p>保存请求正在进行。关闭并放弃本地草稿不会撤销已经到达服务器的请求，请在核对状态后再决定。</p>
                : <p>还有未确认的画布编辑。保存完成后再关闭，或继续留在画布中。</p>}
              <div>
                <button
                  disabled={sourceOutcomeUnresolved || imageGenerationBlockedRef.current || generationMutationPendingRef.current || closeBusy || !documentState.writable || documentState.status === "unknown" || documentState.status === "conflict"}
                  onClick={() => void saveAndClose()}
                  type="button"
                >
                  {closeBusy ? "正在等待保存…" : "保存并关闭"}
                </button>
                <button onClick={() => setClosePrompt(false)} type="button">继续编辑</button>
                <button
                  disabled={generationIntentRef.current !== null || generationBusyRef.current || generationMutationPendingRef.current || imageGenerationBlockedRef.current}
                  onClick={discardAndClose}
                  type="button"
                >
                  放弃本地草稿并关闭
                </button>
              </div>
            </section>
          </div>
        )}
      </section>
    </div>
  );
}
