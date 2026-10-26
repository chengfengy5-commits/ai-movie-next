import type {
  Character,
  Chapter,
  FusedAsset,
  Prop,
  Scene,
  StoryboardAsset,
} from "../../shared/api/contracts";

export type ChapterChecklistAssetType = "character" | "scene" | "prop" | "storyboard" | "fused";
export type ChapterChecklistAssetStatus = "missing-reference" | "ambiguous-reference" | "missing-image";

export interface ChapterChecklistFrameTarget {
  frameIndex: number;
  storyboardAssetId: string | null;
  navigable: boolean;
}

export type ChapterChecklistNavigation =
  | { kind: "asset"; category: "characters" | "scenes" | "props"; assetId: string }
  | { kind: "frame"; storyboardAssetId: string };

export interface ChapterChecklistAssetItem {
  key: string;
  kind: "asset";
  status: ChapterChecklistAssetStatus;
  assetType: ChapterChecklistAssetType;
  name: string;
  frames: ChapterChecklistFrameTarget[];
  navigation: ChapterChecklistNavigation | null;
}

export interface ChapterChecklistFrameItem {
  key: string;
  kind: "preview" | "video";
  status: "missing" | "unknown";
  frame: ChapterChecklistFrameTarget;
}

export type ChapterChecklistItem = ChapterChecklistAssetItem | ChapterChecklistFrameItem;
export type ChapterChecklistResult =
  | { state: "unreadable"; items: [] }
  | { state: "empty"; items: [] }
  | { state: "ready"; items: ChapterChecklistItem[] };

export interface ChapterChecklistDirectories {
  characters: Character[];
  scenes: Scene[];
  props: Prop[];
  storyboard: StoryboardAsset[];
  fused: FusedAsset[];
}

type ChecklistAsset = { id: string; image_url: string | null; name?: string; title?: string };

const referenceTypes: Array<{ type: ChapterChecklistAssetType; field: string }> = [
  { type: "character", field: "character" },
  { type: "scene", field: "scene" },
  { type: "prop", field: "prop" },
  { type: "storyboard", field: "storyboard" },
  { type: "fused", field: "fused" },
];

function normalizeId(value: unknown): string | null {
  if (typeof value === "string") {
    const normalized = value.trim();
    return normalized === "" ? null : normalized;
  }
  return typeof value === "number" && Number.isFinite(value) ? String(value) : null;
}

function frameStoryboardId(frame: unknown): string | null {
  if (typeof frame !== "object" || frame === null || Array.isArray(frame)) return null;
  const storyboard = (frame as Record<string, unknown>).storyboard;
  return Array.isArray(storyboard) ? normalizeId(storyboard[0]) : null;
}

function mediaType(value: unknown): "missing" | "image" | "video" | "unknown" {
  if (typeof value !== "string" || value.trim() === "") return "missing";
  try {
    const pathname = new URL(value, "https://local.invalid").pathname;
    if (/\.(mp4|webm|mov|avi|mkv|m4v)$/i.test(pathname)) return "video";
    return /\.(apng|avif|gif|jpe?g|png|svg|webp)$/i.test(pathname) ? "image" : "unknown";
  } catch {
    return "unknown";
  }
}

function assetName(asset: ChecklistAsset | undefined): string {
  const value = asset?.name ?? asset?.title;
  return typeof value === "string" ? value : "";
}

function navigationFor(
  item: ChapterChecklistAssetItem,
  matches: ChecklistAsset[],
  frame: ChapterChecklistFrameTarget,
): ChapterChecklistNavigation | null {
  if (item.status === "missing-image" && matches.length === 1) {
    const category = item.assetType === "character" ? "characters"
      : item.assetType === "scene" ? "scenes"
        : item.assetType === "prop" ? "props" : null;
    if (category !== null) {
      const assetId = normalizeId(matches[0]?.id);
      if (assetId !== null) return { kind: "asset", category, assetId };
    }
  }
  return frame.navigable && frame.storyboardAssetId !== null
    ? { kind: "frame", storyboardAssetId: frame.storyboardAssetId }
    : null;
}

export function collectChapterProductionChecklist(
  chapter: Chapter,
  directories: ChapterChecklistDirectories,
): ChapterChecklistResult {
  if (!Array.isArray(chapter.content)) return { state: "unreadable", items: [] };
  if (chapter.content.length === 0) return { state: "empty", items: [] };

  const stableIds = chapter.content.map(frameStoryboardId);
  const stableIdCounts = new Map<string, number>();
  for (const id of stableIds) {
    if (id !== null) stableIdCounts.set(id, (stableIdCounts.get(id) ?? 0) + 1);
  }
  const frames = chapter.content.map((_, frameIndex): ChapterChecklistFrameTarget => {
    const storyboardAssetId = stableIds[frameIndex] ?? null;
    return {
      frameIndex,
      storyboardAssetId,
      navigable: storyboardAssetId !== null && stableIdCounts.get(storyboardAssetId) === 1,
    };
  });

  const sources: Record<ChapterChecklistAssetType, ChecklistAsset[]> = {
    character: directories.characters,
    scene: directories.scenes,
    prop: directories.props,
    storyboard: directories.storyboard,
    fused: directories.fused,
  };
  const indexes = new Map<ChapterChecklistAssetType, Map<string, ChecklistAsset[]>>();
  for (const { type } of referenceTypes) {
    const index = new Map<string, ChecklistAsset[]>();
    for (const asset of sources[type]) {
      const id = normalizeId(asset.id);
      if (id === null) continue;
      const matches = index.get(id) ?? [];
      matches.push(asset);
      index.set(id, matches);
    }
    indexes.set(type, index);
  }

  const items: ChapterChecklistItem[] = [];
  const assetItems = new Map<string, ChapterChecklistAssetItem>();
  chapter.content.forEach((frameValue, frameIndex) => {
    const frameTarget = frames[frameIndex];
    if (frameTarget === undefined || typeof frameValue !== "object" || frameValue === null || Array.isArray(frameValue)) {
      return;
    }
    const frameRecord = frameValue as Record<string, unknown>;
    for (const { type, field } of referenceTypes) {
      const rawReferences = frameRecord[field];
      const references = Array.isArray(rawReferences) ? rawReferences : [];
      references.forEach((rawId, referenceIndex) => {
        const assetId = normalizeId(rawId);
        const matches = assetId === null ? [] : indexes.get(type)?.get(assetId) ?? [];
        const status: ChapterChecklistAssetStatus | null = assetId === null || matches.length === 0
          ? "missing-reference"
          : matches.length > 1
            ? "ambiguous-reference"
            : typeof matches[0]?.image_url !== "string" || matches[0].image_url.trim() === ""
              ? "missing-image" : null;
        if (status === null) return;

        const key = assetId === null
          ? `asset:${type}:invalid:${frameIndex}:${referenceIndex}`
          : `asset:${type}:${assetId}`;
        let item = assetItems.get(key);
        if (item === undefined) {
          item = {
            key,
            kind: "asset",
            status,
            assetType: type,
            name: assetName(matches[0]),
            frames: [],
            navigation: null,
          };
          assetItems.set(key, item);
          items.push(item);
        }
        if (!item.frames.some((related) => related.frameIndex === frameIndex)) {
          item.frames.push(frameTarget);
        }
        if (item.navigation === null) {
          item.navigation = navigationFor(item, matches, frameTarget);
        }
      });
    }

    const previewType = mediaType(frameRecord.preview);
    if (previewType === "missing" || previewType === "unknown") {
      items.push({
        key: `frame:${frameTarget.storyboardAssetId ?? "unstable"}:${frameIndex}:preview`,
        kind: "preview",
        status: previewType,
        frame: frameTarget,
      });
    }
    if (previewType !== "video") {
      items.push({
        key: `frame:${frameTarget.storyboardAssetId ?? "unstable"}:${frameIndex}:video`,
        kind: "video",
        status: "missing",
        frame: frameTarget,
      });
    }
  });

  for (const item of assetItems.values()) {
    if (item.navigation !== null) continue;
    const frame = item.frames.find((candidate) => candidate.navigable && candidate.storyboardAssetId !== null);
    if (frame?.storyboardAssetId !== null && frame !== undefined) {
      item.navigation = { kind: "frame", storyboardAssetId: frame.storyboardAssetId };
    }
  }
  return { state: "ready", items };
}

function cleanText(value: unknown): string {
  return typeof value === "string"
    ? value.replace(/\r\n?/g, " ").replace(/[\u0000-\u001F\u007F-\u009F]/g, " ").replace(/\s+/g, " ").trim()
    : "";
}

const assetTypeLabels: Record<ChapterChecklistAssetType, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
  storyboard: "故事板素材",
  fused: "融合素材",
};

const assetStatusLabels: Record<ChapterChecklistAssetStatus, string> = {
  "missing-reference": "引用未在当前已读取资产中找到",
  "ambiguous-reference": "资产标识不唯一",
  "missing-image": "缺少图片",
};

export function formatChapterProductionChecklistText(result: ChapterChecklistResult): string | null {
  if (result.state === "unreadable") return null;
  const lines = ["本章制作缺项", "仅依据当前已读取的数据整理；链接已配置不代表可访问。", ""];
  if (result.state === "empty") {
    lines.push("当前章节暂无分镜。");
    return `${lines.join("\n")}\n`;
  }
  if (result.items.length === 0) {
    lines.push("当前已读取数据中暂无上述缺项。");
    return `${lines.join("\n")}\n`;
  }
  for (const item of result.items) {
    if (item.kind === "asset") {
      const relatedFrames = [...new Set(item.frames.map(({ frameIndex }) => frameIndex))]
        .filter((frameIndex) => Number.isSafeInteger(frameIndex) && frameIndex >= 0)
        .map((frameIndex) => `分镜 ${frameIndex + 1}`);
      const related = relatedFrames.length > 0 ? `（关联：${relatedFrames.join("、")}）` : "";
      const name = cleanText(item.name) || "未命名素材";
      lines.push(`${assetTypeLabels[item.assetType]}「${name}」：${assetStatusLabels[item.status]}${related}`);
      continue;
    }
    const frameIndex = item.frame.frameIndex;
    if (!Number.isSafeInteger(frameIndex) || frameIndex < 0) continue;
    if (item.kind === "preview" && item.status === "missing") lines.push(`分镜 ${frameIndex + 1}：预览未配置`);
    if (item.kind === "preview" && item.status === "unknown") lines.push(`分镜 ${frameIndex + 1}：预览链接已配置，但媒体类型未识别`);
    if (item.kind === "video" && item.status === "missing") lines.push(`分镜 ${frameIndex + 1}：尚未配置可识别的视频`);
  }
  return `${lines.join("\n")}\n`;
}

export function chapterProductionChecklistFilename(title: string): string {
  const safeTitle = cleanText(title)
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\.{2,}/g, "_")
    .replace(/^[\s._]+|[\s._]+$/g, "")
    .slice(0, 64);
  return safeTitle ? `本章制作缺项-${safeTitle}.txt` : "本章制作缺项.txt";
}
