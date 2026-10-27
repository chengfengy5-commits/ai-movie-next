import type { Character, FusedAsset, Prop, Scene, StoryboardAsset, StoryboardFrame } from "../../shared/api/contracts";
import type { WorkbenchResourceCategory, WorkbenchSourceTarget } from "./sourceIntent";

export type FrameResourceDirectoryState<T> =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly items: readonly T[] }
  | { readonly status: "error"; readonly message: string };

export interface FrameResourceDirectories {
  readonly storyboard: FrameResourceDirectoryState<StoryboardAsset>;
  readonly characters: FrameResourceDirectoryState<Character>;
  readonly scenes: FrameResourceDirectoryState<Scene>;
  readonly props: FrameResourceDirectoryState<Prop>;
  readonly fused: FrameResourceDirectoryState<FusedAsset>;
}

export interface FrameResourceRow {
  readonly key: string;
  readonly category: WorkbenchResourceCategory;
  readonly id: string | null;
  readonly label: string;
  readonly imageUrl: string | null;
  readonly state: "ready" | "missing" | "ambiguous" | "invalid" | "loading" | "error";
  readonly target: WorkbenchSourceTarget | null;
}

export interface FrameResourceGroup {
  readonly category: WorkbenchResourceCategory;
  readonly label: string;
  readonly status: "ready" | "loading" | "error";
  readonly message: string | null;
  readonly rows: readonly FrameResourceRow[];
}

const categoryFields: ReadonlyArray<{
  category: WorkbenchResourceCategory;
  field: string;
  label: string;
}> = [
  { category: "storyboard", field: "storyboard", label: "故事板" },
  { category: "characters", field: "character", label: "角色" },
  { category: "scenes", field: "scene", label: "场景" },
  { category: "props", field: "prop", label: "道具" },
  { category: "fused", field: "fused", label: "融合素材" },
];

type DirectoryAsset = StoryboardAsset | Character | Scene | Prop | FusedAsset;

function referencesFor(frame: StoryboardFrame, field: string): unknown[] | null {
  const value = frame[field];
  if (value === undefined || value === null) return [];
  return Array.isArray(value) ? value : null;
}

function frameIds(frames: readonly StoryboardFrame[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const frame of frames) {
    const refs = referencesFor(frame, "storyboard");
    const id = refs !== null && typeof refs[0] === "string" ? refs[0].trim() : "";
    if (id !== "") counts.set(id, (counts.get(id) ?? 0) + 1);
  }
  return counts;
}

function assetLabel(asset: DirectoryAsset, category: WorkbenchResourceCategory): string {
  if (category === "scenes") return (asset as Scene).title;
  return (asset as Character | Prop | FusedAsset | StoryboardAsset).name;
}

function rowsForCategory(
  frame: StoryboardFrame,
  frameIndex: number,
  chapterId: string,
  seriesId: string,
  frames: readonly StoryboardFrame[],
  category: WorkbenchResourceCategory,
  field: string,
  directory: FrameResourceDirectoryState<DirectoryAsset>,
): FrameResourceGroup {
  if (directory.status !== "ready") {
    return {
      category,
      label: categoryFields.find((item) => item.category === category)?.label ?? category,
      status: directory.status,
      message: directory.status === "error" ? directory.message : null,
      rows: [],
    };
  }
  const references = referencesFor(frame, field);
  if (references === null) {
    return {
      category,
      label: categoryFields.find((item) => item.category === category)?.label ?? category,
      status: "ready",
      message: null,
      rows: [{
        key: `${category}:invalid`, category, id: null, label: "关联内容无法识别", imageUrl: null,
        state: "invalid", target: null,
      }],
    };
  }
  const ids = references.map((value) => typeof value === "string" ? value.trim() : "");
  const rows = ids.map((id, index): FrameResourceRow => {
    if (id === "") {
      return { key: `${category}:invalid:${index}`, category, id: null, label: "无效的素材编号", imageUrl: null, state: "invalid", target: null };
    }
    const candidates = directory.items.filter((asset) => asset.id === id && asset.series_id === seriesId);
    const visibleCandidates = category === "fused"
      ? candidates.filter((asset) => "status" in asset
        && !["failed", "error"].includes(asset.status.toLocaleLowerCase()))
      : candidates;
    if (visibleCandidates.length === 0) {
      return { key: `${category}:${id}:${index}`, category, id, label: `未在当前目录找到（${id}）`, imageUrl: null, state: "missing", target: null };
    }
    if (visibleCandidates.length !== 1) {
      return { key: `${category}:${id}:${index}`, category, id, label: `当前目录中有多个匹配项（${id}）`, imageUrl: null, state: "ambiguous", target: null };
    }
    const asset = visibleCandidates[0];
    if (asset === undefined) return { key: `${category}:${id}:${index}`, category, id, label: `未在当前目录找到（${id}）`, imageUrl: null, state: "missing", target: null };
    if (category === "storyboard") {
      const count = frameIds(frames).get(id) ?? 0;
      if (count !== 1 || !("chapter_id" in asset) || asset.chapter_id !== chapterId
        || !("frame_index" in asset) || asset.frame_index !== frameIndex) {
        return { key: `${category}:${id}:${index}`, category, id, label: `镜头关系暂不可核对（${id}）`, imageUrl: null, state: "missing", target: null };
      }
    } else if (category === "fused" && (asset as FusedAsset).status === "failed") {
      return { key: `${category}:${id}:${index}`, category, id, label: `融合素材生成失败（${id}）`, imageUrl: null, state: "missing", target: null };
    }
    const label = assetLabel(asset, category).trim() || `未命名${categoryFields.find((item) => item.category === category)?.label ?? "素材"}`;
    const target: WorkbenchSourceTarget | null = category === "storyboard"
      ? { kind: "frame", chapterId, storyboardAssetId: id }
      : { kind: "asset", chapterId, category, assetId: id };
    return {
      key: `${category}:${id}:${index}`,
      category,
      id,
      label,
      imageUrl: asset.image_url,
      state: "ready",
      target,
    };
  });
  return {
    category,
    label: categoryFields.find((item) => item.category === category)?.label ?? category,
    status: "ready",
    message: null,
    rows,
  };
}

export function projectFrameResources(
  frame: StoryboardFrame,
  frameIndex: number,
  chapterId: string,
  seriesId: string,
  chapterFrames: readonly StoryboardFrame[],
  directories: FrameResourceDirectories,
): FrameResourceGroup[] {
  return categoryFields.map(({ category, field }) => {
    const directory = directories[category];
    return rowsForCategory(
      frame,
      frameIndex,
      chapterId,
      seriesId,
      chapterFrames,
      category,
      field,
      directory as FrameResourceDirectoryState<DirectoryAsset>,
    );
  });
}
