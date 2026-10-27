import type {
  Chapter,
  ChapterEditFrameSource,
  ChapterEditGuard,
  ChapterEditStoryboardBaseline,
  Scene,
  StoryboardAsset,
  StoryboardFrame,
  Character,
  Prop,
  UpdateChapterInput,
} from "../../shared/api/contracts";
import type { AssetOrphanCandidate } from "../../shared/api/assetMaintenance";
import { areJsonValuesEqual } from "./chapterEditing";

export interface ContinuousDraftFrame {
  readonly draftId: string;
  readonly sourceIndex: number | null;
  readonly storyboardId: string | null;
  readonly originalFrame: StoryboardFrame | null;
  readonly text: string;
  readonly characterIds: readonly string[];
  readonly sceneIds: readonly string[];
  readonly propIds: readonly string[];
}

export interface ContinuousAssetCatalog {
  readonly characters: readonly Character[];
  readonly scenes: readonly Scene[];
  readonly props: readonly Prop[];
}

export interface ContinuousChapterDraftSnapshot {
  readonly chapter: Chapter;
  readonly frames: readonly ContinuousDraftFrame[];
  readonly expectedStoryboards: readonly StoryboardAsset[];
  readonly lockIdentity: { readonly user_id: string; readonly acquired_at: string } | null;
}

export class ContinuousChapterEditError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContinuousChapterEditError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readStoryboardId(frame: StoryboardFrame, index: number): string | null {
  const value = frame.storyboard;
  if (value === undefined || (Array.isArray(value) && value.length === 0)) return null;
  if (!Array.isArray(value) || value.length !== 1 || typeof value[0] !== "string" || value[0] === "") {
    throw new ContinuousChapterEditError(`第 ${index + 1} 个镜头的分镜素材身份无法唯一确认。`);
  }
  return value[0];
}

function readAssetIds(frame: StoryboardFrame, key: "character" | "scene" | "prop", index: number): readonly string[] {
  const value = frame[key];
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || item.trim() === "")) {
    throw new ContinuousChapterEditError(`第 ${index + 1} 个镜头的${key}素材引用格式无法识别。`);
  }
  return [...value] as string[];
}

function requireContentFrames(chapter: Chapter): StoryboardFrame[] {
  if (chapter.content === null) return [];
  if (!Array.isArray(chapter.content) || chapter.content.some((frame) => !isRecord(frame))) {
    throw new ContinuousChapterEditError("章节包含无法完整保留的镜头结构，暂不能连续编辑。");
  }
  return chapter.content;
}

export function createContinuousChapterDraft(
  chapter: Chapter,
  expectedStoryboards: readonly StoryboardAsset[],
  lockIdentity: ContinuousChapterDraftSnapshot["lockIdentity"],
): ContinuousChapterDraftSnapshot {
  const content = requireContentFrames(chapter);
  const seenStoryboardIds = new Set<string>();
  const frames = content.map((frame, sourceIndex): ContinuousDraftFrame => {
    const text = frame.text;
    if (typeof text !== "string") {
      throw new ContinuousChapterEditError(`第 ${sourceIndex + 1} 个镜头的文字无法识别。`);
    }
    const storyboardId = readStoryboardId(frame, sourceIndex);
    if (storyboardId !== null && seenStoryboardIds.has(storyboardId)) {
      throw new ContinuousChapterEditError("章节中存在重复的分镜素材身份，暂不能连续编辑。");
    }
    if (storyboardId !== null) seenStoryboardIds.add(storyboardId);
    return {
      draftId: `source:${sourceIndex}`,
      sourceIndex,
      storyboardId,
      originalFrame: frame,
      text,
      characterIds: readAssetIds(frame, "character", sourceIndex),
      sceneIds: readAssetIds(frame, "scene", sourceIndex),
      propIds: readAssetIds(frame, "prop", sourceIndex),
    };
  });
  const storyboardIds = new Set<string>();
  for (const asset of expectedStoryboards) {
    if (asset.series_id !== chapter.series_id || asset.chapter_id !== chapter.id
      || storyboardIds.has(asset.id) || !Number.isSafeInteger(asset.frame_index) || asset.frame_index < 0) {
      throw new ContinuousChapterEditError("分镜素材目录与当前章节不一致，暂不能连续编辑。");
    }
    storyboardIds.add(asset.id);
  }
  for (const frame of frames) {
    if (frame.storyboardId !== null && !storyboardIds.has(frame.storyboardId)) {
      throw new ContinuousChapterEditError("有镜头引用的分镜素材不在当前目录中，暂不能连续编辑。");
    }
  }
  return { chapter, frames, expectedStoryboards: [...expectedStoryboards], lockIdentity };
}

export function addContinuousDraftFrame(
  frames: readonly ContinuousDraftFrame[],
  afterIndex: number,
  draftId: string,
): ContinuousDraftFrame[] {
  const insertAt = Math.max(0, Math.min(frames.length, afterIndex + 1));
  const next = [...frames];
  next.splice(insertAt, 0, {
    draftId,
    sourceIndex: null,
    storyboardId: null,
    originalFrame: null,
    text: "",
    characterIds: [],
    sceneIds: [],
    propIds: [],
  });
  return next;
}

export function deleteContinuousDraftFrame(
  frames: readonly ContinuousDraftFrame[],
  index: number,
): ContinuousDraftFrame[] {
  return frames.filter((_frame, frameIndex) => frameIndex !== index);
}

export function moveContinuousDraftFrame(
  frames: readonly ContinuousDraftFrame[],
  index: number,
  direction: -1 | 1,
): ContinuousDraftFrame[] {
  const target = index + direction;
  if (index < 0 || index >= frames.length || target < 0 || target >= frames.length) return [...frames];
  const next = [...frames];
  [next[index], next[target]] = [next[target]!, next[index]!];
  return next;
}

export function splitContinuousDraftFrame(
  frames: readonly ContinuousDraftFrame[],
  index: number,
  splitAt: number,
  newDraftId: string,
): ContinuousDraftFrame[] {
  const current = frames[index];
  if (current === undefined) return [...frames];
  const position = Math.max(0, Math.min(current.text.length, splitAt));
  const next = [...frames];
  next.splice(index, 1,
    { ...current, text: current.text.slice(0, position) },
    {
      draftId: newDraftId,
      sourceIndex: null,
      storyboardId: null,
      originalFrame: null,
      text: current.text.slice(position),
      characterIds: [...current.characterIds],
      sceneIds: [...current.sceneIds],
      propIds: [...current.propIds],
    },
  );
  return next;
}

export function mergeContinuousDraftFrames(
  frames: readonly ContinuousDraftFrame[],
  index: number,
  keep: "left" | "right",
): ContinuousDraftFrame[] {
  const left = frames[index];
  const right = frames[index + 1];
  if (left === undefined || right === undefined) return [...frames];
  const retained = keep === "left" ? left : right;
  const nextFrame: ContinuousDraftFrame = {
    ...retained,
    text: `${left.text}\n\n${right.text}`,
    characterIds: [...new Set([...left.characterIds, ...right.characterIds])],
    sceneIds: [...new Set([...left.sceneIds, ...right.sceneIds])],
    propIds: [...new Set([...left.propIds, ...right.propIds])],
  };
  const next = [...frames];
  next.splice(index, 2, nextFrame);
  return next;
}

function storyboardBaseline(assets: readonly StoryboardAsset[]): ChapterEditStoryboardBaseline[] {
  return assets.map(({ id, series_id, chapter_id, frame_index }) => ({ id, series_id, chapter_id, frame_index }));
}

function buildFrame(frame: ContinuousDraftFrame): StoryboardFrame {
  if (frame.sourceIndex === null || frame.originalFrame === null) {
    return {
      text: frame.text,
      character: [...frame.characterIds],
      scene: [...frame.sceneIds],
      prop: [...frame.propIds],
      storyboard: [],
      preview: null,
    };
  }
  const next = { ...frame.originalFrame } as Record<string, unknown>;
  next.text = frame.text;
  next.character = [...frame.characterIds];
  next.scene = [...frame.sceneIds];
  next.prop = [...frame.propIds];
  next.storyboard = frame.storyboardId === null ? [] : [frame.storyboardId];
  return next as StoryboardFrame;
}

export function buildGuardedChapterUpdate(
  snapshot: ContinuousChapterDraftSnapshot,
  frames: readonly ContinuousDraftFrame[],
): UpdateChapterInput {
  const oldContent = snapshot.chapter.content;
  const retainedSourceIndexes = new Set<number>();
  const frameSources: ChapterEditFrameSource[] = [];
  const content = frames.map((frame) => {
    if (frame.sourceIndex === null) {
      frameSources.push({ kind: "new" });
    } else {
      if (retainedSourceIndexes.has(frame.sourceIndex)) {
        throw new ContinuousChapterEditError("一个原镜头被重复映射，暂不能保存。");
      }
      retainedSourceIndexes.add(frame.sourceIndex);
      frameSources.push({
        kind: "retained",
        source_index: frame.sourceIndex,
        storyboard_id: frame.storyboardId,
      });
    }
    return buildFrame(frame);
  });
  const deletedSourceIndexes = (oldContent ?? [])
    .map((_frame, index) => index)
    .filter((index) => !retainedSourceIndexes.has(index));
  if (retainedSourceIndexes.size + deletedSourceIndexes.length !== (oldContent?.length ?? 0)) {
    throw new ContinuousChapterEditError("镜头来源映射不完整，暂不能保存。");
  }
  const guard: ChapterEditGuard = {
    version: 1,
    expected_chapter_id: snapshot.chapter.id,
    expected_series_id: snapshot.chapter.series_id,
    expected_order: snapshot.chapter.order,
    expected_content: oldContent === null ? null : [...oldContent],
    expected_storyboards: storyboardBaseline(snapshot.expectedStoryboards),
    expected_lock_identity: snapshot.lockIdentity,
    frame_sources: frameSources,
    deleted_source_indexes: deletedSourceIndexes,
  };
  return { content, chapter_edit_guard: guard };
}

export function removedContinuousAssetCandidates(
  snapshot: ContinuousChapterDraftSnapshot,
  nextContent: readonly StoryboardFrame[],
): AssetOrphanCandidate[] {
  const retained = new Map<AssetOrphanCandidate["type"], Set<string>>([
    ["character", new Set()],
    ["scene", new Set()],
    ["prop", new Set()],
  ]);
  const fields = [
    ["character", "character"],
    ["scene", "scene"],
    ["prop", "prop"],
  ] as const;
  for (const frame of nextContent) {
    for (const [type, field] of fields) {
      const values = frame[field];
      if (!Array.isArray(values)) continue;
      for (const id of values) {
        if (typeof id === "string") retained.get(type)!.add(id);
      }
    }
  }

  const candidates = new Map<string, AssetOrphanCandidate>();
  for (const frame of snapshot.frames) {
    for (const [type, ids] of [
      ["character", frame.characterIds],
      ["scene", frame.sceneIds],
      ["prop", frame.propIds],
    ] as const) {
      for (const id of ids) {
        if (retained.get(type)!.has(id)) continue;
        candidates.set(`${type}:${id}`, { type, id });
      }
    }
  }
  return [...candidates.values()];
}

export function updateContinuousDraftFrame(
  frames: readonly ContinuousDraftFrame[],
  index: number,
  update: Partial<Pick<ContinuousDraftFrame, "text" | "characterIds" | "sceneIds" | "propIds">>,
): ContinuousDraftFrame[] {
  return frames.map((frame, frameIndex) => frameIndex === index ? { ...frame, ...update } : frame);
}

export function continuousWriteReadbackMatches(
  expectedContent: readonly StoryboardFrame[],
  chapter: Chapter,
  assets: readonly StoryboardAsset[],
  seriesId: string,
): boolean {
  if (chapter.series_id !== seriesId || !Array.isArray(chapter.content)
    || chapter.content.length !== expectedContent.length) return false;
  const normalizedExpected = expectedContent.map((frame) => ({ ...frame }) as Record<string, unknown>);
  const seenIds = new Set<string>();
  for (let index = 0; index < chapter.content.length; index += 1) {
    const frame: unknown = chapter.content[index];
    if (!isRecord(frame)) return false;
    const refs = frame.storyboard;
    if (!Array.isArray(refs) || refs.length !== 1 || typeof refs[0] !== "string" || refs[0].trim() === "") return false;
    const id = refs[0];
    if (seenIds.has(id)) return false;
    seenIds.add(id);
    const matches = assets.filter((asset) => asset.id === id
      && asset.series_id === seriesId
      && asset.chapter_id === chapter.id
      && asset.frame_index === index);
    if (matches.length !== 1) return false;
    const expectedRefs = normalizedExpected[index]!["storyboard"];
    if (Array.isArray(expectedRefs) && expectedRefs.length === 0) {
      normalizedExpected[index]!["storyboard"] = [id];
    } else if (!Array.isArray(expectedRefs) || expectedRefs.length !== 1 || expectedRefs[0] !== id) {
      return false;
    }
  }
  return normalizedExpected.every((frame, index) => areJsonValuesEqual(frame, chapter.content?.[index]));
}

export function continuousAssetNameIds(
  names: readonly string[],
  assets: readonly { readonly id: string; readonly name: string }[],
): { readonly ids: string[]; readonly missing: string[]; readonly ambiguous: string[] } {
  const ids: string[] = [];
  const missing: string[] = [];
  const ambiguous: string[] = [];
  for (const rawName of names) {
    const name = rawName.trim();
    if (name === "") continue;
    const matches = assets.filter((asset) => asset.name === name);
    if (matches.length === 0) missing.push(name);
    else if (matches.length > 1) ambiguous.push(name);
    else if (!ids.includes(matches[0]!.id)) ids.push(matches[0]!.id);
  }
  return { ids, missing, ambiguous };
}

export function continuousAssetNames(
  ids: readonly string[],
  assets: readonly { readonly id: string; readonly name: string }[],
): { readonly names: string[]; readonly missing: string[] } {
  const names: string[] = [];
  const missing: string[] = [];
  for (const id of ids) {
    const matches = assets.filter((asset) => asset.id === id);
    if (matches.length !== 1) missing.push(id);
    else names.push(matches[0]!.name);
  }
  return { names, missing };
}

export function continuousAssetCatalogNames(catalog: ContinuousAssetCatalog): {
  readonly characters: { readonly id: string; readonly name: string }[];
  readonly scenes: { readonly id: string; readonly name: string }[];
  readonly props: { readonly id: string; readonly name: string }[];
} {
  return {
    characters: catalog.characters.map(({ id, name }) => ({ id, name })),
    scenes: catalog.scenes.map(({ id, title }) => ({ id, name: title })),
    props: catalog.props.map(({ id, name }) => ({ id, name })),
  };
}
