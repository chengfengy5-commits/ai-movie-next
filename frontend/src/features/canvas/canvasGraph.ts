import type { Character, Chapter, Prop, Scene, StoryboardAsset } from "../../shared/api/contracts";
import { isRecord } from "../../shared/api/canvasData";
import { canvasLayout, hasCanvasLayout, type CanvasDocument } from "./canvasDocument";

export type CanvasNodeKind = "character" | "scene" | "prop" | "frame" | "media" | "preview" | "video" | "note" | "card";

export interface CanvasGraphNode {
  key: string;
  ref: string;
  kind: CanvasNodeKind;
  title: string;
  detail: string;
  x: number;
  y: number;
  width: number;
  height: number;
  imageUrl: string | null;
  itemId?: string;
  frameIndex?: number;
  itemCollection?: "notes" | "cards";
  itemTextField?: "text" | "label";
}

export interface CanvasGraphEdge {
  id: string;
  sourceRef: string;
  targetRef: string;
  relation: string;
  manual: boolean;
}

export interface CanvasGraph {
  nodes: CanvasGraphNode[];
  edges: CanvasGraphEdge[];
}

type Asset = Character | Scene | Prop;

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim() !== "") : [];
}

function referenceIds(frame: Record<string, unknown>, field: string): string[] {
  return strings(frame[field]);
}

function assetIdentity(asset: Asset): string {
  return asset.id;
}

function assetLabel(kind: "character" | "scene" | "prop", asset: Asset): string {
  if (kind === "scene") return (asset as Scene).title;
  return (asset as Character | Prop).name;
}

function validPoint(value: unknown): { x: number; y: number } | null {
  if (!isRecord(value) || typeof value.x !== "number" || typeof value.y !== "number"
    || !Number.isFinite(value.x) || !Number.isFinite(value.y)) return null;
  return { x: value.x, y: value.y };
}

function storedItems(document: CanvasDocument, key: "notes" | "cards" | "manualEdges"): Record<string, unknown>[] {
  const value = document[key];
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

export function buildCanvasGraph(
  chapter: Chapter,
  storyboardAssets: StoryboardAsset[],
  characters: Character[],
  scenes: Scene[],
  props: Prop[],
  document: CanvasDocument,
): CanvasGraph {
  const nodes: CanvasGraphNode[] = [];
  const edges: CanvasGraphEdge[] = [];
  const frames = chapter.content ?? [];
  const referenced = new Map<string, Set<string>>();

  for (const frame of frames) {
    if (!isRecord(frame)) continue;
    for (const kind of ["character", "scene", "prop"] as const) {
      for (const id of referenceIds(frame, kind)) {
        const values = referenced.get(kind) ?? new Set<string>();
        values.add(id);
        referenced.set(kind, values);
      }
    }
  }

  const sources: Array<{ kind: "character" | "scene" | "prop"; items: Asset[] }> = [
    { kind: "character", items: characters },
    { kind: "scene", items: scenes },
    { kind: "prop", items: props },
  ];
  for (const source of sources) {
    const requested = referenced.get(source.kind) ?? new Set<string>();
    const seen = new Map<string, number>();
    for (const asset of source.items) seen.set(assetIdentity(asset), (seen.get(assetIdentity(asset)) ?? 0) + 1);
    for (const asset of source.items) {
      const id = assetIdentity(asset);
      const ref = source.kind + ":" + id;
      if (asset.series_id !== chapter.series_id || seen.get(id) !== 1
        || (!requested.has(id) && !hasCanvasLayout(document, ref))) continue;
      const layout = canvasLayout(document, ref);
      const x = layout?.x ?? 40 + sources.findIndex((item) => item.kind === source.kind) * 300;
      const y = layout?.y ?? 50 + nodes.filter((node) => node.kind === source.kind).length * 150;
      nodes.push({
        key: ref,
        ref,
        kind: source.kind,
        title: assetLabel(source.kind, asset),
        detail: text(asset.description) || "当前剧集素材",
        x,
        y,
        width: 250,
        height: 120,
        imageUrl: asset.image_url,
      });
    }
  }

  const storyboardById = new Map<string, StoryboardAsset[]>();
  for (const asset of storyboardAssets) {
    if (asset.chapter_id !== chapter.id || asset.series_id !== chapter.series_id) continue;
    const values = storyboardById.get(asset.id) ?? [];
    values.push(asset);
    storyboardById.set(asset.id, values);
  }

  let defaultMediaLaneIndex = 0;
  frames.forEach((frame, index) => {
    if (!isRecord(frame)) return;
    const frameRef = "frame:" + index;
    const frameLayout = canvasLayout(document, frameRef);
    const frameNode: CanvasGraphNode = {
      key: frameRef,
      ref: frameRef,
      kind: "frame",
      title: "分镜 " + (index + 1),
      detail: text(frame.text) || text(frame.original_text) || "暂无分镜文字",
      x: frameLayout?.x ?? 940,
      y: frameLayout?.y ?? 60 + index * 190,
      width: 270,
      height: 145,
      imageUrl: null,
      frameIndex: index,
    };
    nodes.push(frameNode);

    for (const kind of ["character", "scene", "prop"] as const) {
      for (const id of referenceIds(frame, kind)) {
        const assetRef = kind + ":" + id;
        if (nodes.some((node) => node.ref === assetRef)) {
          edges.push({ id: "uses:" + assetRef + ":" + frameRef, sourceRef: assetRef, targetRef: frameRef, relation: "uses", manual: false });
        }
      }
    }

    for (const storyboardId of strings(frame.storyboard)) {
      const imageMatches = storyboardById.get(storyboardId) ?? [];
      if (imageMatches.length !== 1 || !imageMatches[0]?.image_url) continue;
      const image = imageMatches[0];
      const mediaRef = "storyboard:" + image.id;
      if (!nodes.some((node) => node.ref === mediaRef)) {
        const mediaLayout = canvasLayout(document, mediaRef);
        nodes.push({
          key: "storyboard-node:" + image.id,
          ref: mediaRef,
          kind: "media",
          title: image.name || "故事板构图",
          detail: "分镜 " + (index + 1) + " 的故事板构图",
          x: mediaLayout?.x ?? 1240,
          y: mediaLayout?.y ?? 50 + nodes.filter((node) => node.ref.startsWith("storyboard:")).length * 190,
          width: 250,
          height: 160,
          imageUrl: image.image_url,
          itemId: image.id,
          frameIndex: index,
        });
      }
      edges.push({ id: "reference:" + frameRef + ":" + mediaRef, sourceRef: frameRef, targetRef: mediaRef, relation: "reference", manual: false });
    }

    const preview = frame.preview;
    const previewReservesOrdinal = Boolean(preview);
    const hasPreview = typeof preview === "string" && preview.trim() !== "";
    if (hasPreview) {
      const previewRef = "media:" + index + ":0";
      const previewLayout = canvasLayout(document, previewRef);
      nodes.push({
        key: "preview-node:" + index,
        ref: previewRef,
        kind: "preview",
        title: "分镜预览",
        detail: preview,
        x: previewLayout?.x ?? 1540,
        y: previewLayout?.y ?? 60 + defaultMediaLaneIndex * 180,
        width: 250,
        height: 130,
        imageUrl: /^https?:\/\//iu.test(preview) || preview.startsWith("/uploads/") ? preview : null,
        frameIndex: index,
      });
      edges.push({ id: "generates:" + frameRef + ":" + previewRef, sourceRef: frameRef, targetRef: previewRef, relation: "generates", manual: false });
      defaultMediaLaneIndex += 1;
      for (const kind of ["character", "scene", "prop"] as const) {
        for (const id of referenceIds(frame, kind)) {
          const assetRef = kind + ":" + id;
          if (nodes.some((node) => node.ref === assetRef)) {
            edges.push({ id: "reference:" + assetRef + ":" + previewRef, sourceRef: assetRef, targetRef: previewRef,
              relation: "reference", manual: false });
          }
        }
      }
    }

    let videoOrdinal = previewReservesOrdinal ? 1 : 0;
    let visibleVideoIndex = 0;
    const videos = Array.isArray(frame.videos) ? frame.videos : [];
    videos.forEach((video) => {
      if (typeof video !== "string") return;
      const mediaOrdinal = videoOrdinal;
      videoOrdinal += 1;
      if (video.trim() === "") return;

      const ref = "media:" + index + ":" + mediaOrdinal;
      const layout = canvasLayout(document, ref);
      nodes.push({
        key: "video-node:" + index + ":" + mediaOrdinal,
        ref,
        kind: "video",
        title: "视频 " + (visibleVideoIndex + 1),
        detail: video,
        x: layout?.x ?? 1540,
        y: layout?.y ?? 60 + defaultMediaLaneIndex * 180,
        width: 250,
        height: 120,
        imageUrl: null,
        frameIndex: index,
      });
      visibleVideoIndex += 1;
      defaultMediaLaneIndex += 1;
    });
  });

  for (const [collection, kind] of [["notes", "note"], ["cards", "card"]] as const) {
    const items = storedItems(document, collection);
    const idCounts = new Map<string, number>();
    for (const item of items) {
      if (typeof item.id === "string") idCounts.set(item.id, (idCounts.get(item.id) ?? 0) + 1);
    }
    items.forEach((item, index) => {
      const id = item.id;
      if (typeof id !== "string" || idCounts.get(id) !== 1) return;
      const position = validPoint(item) ?? { x: 40 + (index % 3) * 280, y: 470 + Math.floor(index / 3) * 190 };
      nodes.push({
        key: (collection === "notes" ? "note:" : "card:") + id,
        ref: collection === "notes" ? id : "card:" + id,
        kind,
        title: kind === "note" ? "便签" : text(item.type ?? item.kind) === "chapter-card" ? "章节卡片" : "文本卡片",
        detail: text(item.text ?? item.label),
        x: position.x,
        y: position.y,
        width: typeof item.width === "number" && item.width > 0 ? item.width : 230,
        height: typeof item.height === "number" && item.height > 0 ? item.height : 145,
        imageUrl: null,
        itemId: id,
        itemCollection: collection,
        itemTextField: typeof item.text === "string" || kind === "note" ? "text" : "label",
      });
    });
  }

  const knownRefs = new Set(nodes.map((node) => node.ref));
  for (const item of storedItems(document, "manualEdges")) {
    if (typeof item.id !== "string" || typeof item.sourceRef !== "string" || typeof item.targetRef !== "string") continue;
    if (knownRefs.has(item.sourceRef) && knownRefs.has(item.targetRef)) {
      edges.push({ id: item.id, sourceRef: item.sourceRef, targetRef: item.targetRef,
        relation: typeof item.relation === "string" ? item.relation : "manual", manual: true });
    }
  }
  const hidden = new Set(strings(document.hiddenRelations));
  const visibleEdges = edges.filter((edge) => !hidden.has(edge.relation));
  return { nodes, edges: visibleEdges };
}
