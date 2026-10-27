import type { Character, Prop, Scene, StoryboardFrame } from "../../shared/api/contracts";

export const BATCH_CHECK_FIELDS = ["character", "scene", "prop"] as const;

export type BatchCheckField = (typeof BATCH_CHECK_FIELDS)[number];

export type BatchGenerationChecks = Record<BatchCheckField, boolean>;

export interface BatchStoryboardContext {
  readonly frameFound: boolean;
  readonly description: string;
  readonly referenceImages: readonly string[];
  readonly missingNames: readonly string[];
}

export function mergeBatchReferenceImages(
  publicReferences: readonly string[],
  automaticReferences: readonly string[] = [],
): readonly string[] {
  return [...new Set([...publicReferences, ...automaticReferences])];
}

export function defaultBatchGenerationChecks(): BatchGenerationChecks {
  return { character: true, scene: true, prop: true };
}

export function restoreBatchGenerationChecks(value: unknown): BatchGenerationChecks {
  const saved = value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  return {
    character: saved.character !== false,
    scene: saved.scene !== false,
    prop: saved.prop !== false,
  };
}

interface CollectBatchStoryboardContextInput {
  readonly storyboardId: string;
  readonly fallbackDescription: string;
  readonly chapterContent: readonly StoryboardFrame[] | null;
  readonly characters: readonly Character[];
  readonly scenes: readonly Scene[];
  readonly props: readonly Prop[];
  readonly checks: BatchGenerationChecks;
  readonly publicReferences: readonly string[];
}

function isReferenceId(value: unknown): value is string | number {
  return typeof value === "string" || typeof value === "number";
}

function imageUrl(value: string | null): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

export function collectBatchStoryboardContext({
  storyboardId,
  fallbackDescription,
  chapterContent,
  characters,
  scenes,
  props,
  checks,
  publicReferences,
}: CollectBatchStoryboardContextInput): BatchStoryboardContext {
  const referenceImages: string[] = [];
  const referenceIndexes = new Map<string, number>();
  const appendReference = (url: string): number => {
    const existing = referenceIndexes.get(url);
    if (existing !== undefined) return existing;
    referenceImages.push(url);
    const index = referenceImages.length;
    referenceIndexes.set(url, index);
    return index;
  };
  publicReferences.forEach(appendReference);

  const frame = chapterContent?.find((candidate) => (
    Array.isArray(candidate.storyboard) && candidate.storyboard.includes(storyboardId)
  ));
  if (frame === undefined) {
    return {
      frameFound: false,
      description: fallbackDescription,
      referenceImages,
      missingNames: [],
    };
  }

  let description = typeof frame.text === "string" && frame.text.trim() !== ""
    ? frame.text
    : fallbackDescription;
  const contextParts: string[] = [];
  const missingNames: string[] = [];

  const collectCategory = <T extends { readonly id: string; readonly image_url: string | null }>(
    field: BatchCheckField,
    ids: unknown,
    assets: readonly T[],
    labelFor: (asset: T | undefined) => string,
  ) => {
    if (!checks[field] || !Array.isArray(ids)) return;
    const labels: string[] = [];
    for (const rawId of ids) {
      if (!isReferenceId(rawId)) continue;
      const asset = assets.find((candidate) => candidate.id === String(rawId));
      const label = labelFor(asset);
      const url = asset === undefined ? null : imageUrl(asset.image_url);
      if (url === null) {
        missingNames.push(label);
        continue;
      }
      labels.push(`图片${appendReference(url)} = ${label}`);
    }
    if (labels.length > 0) {
      const title = field === "character" ? "角色" : field === "scene" ? "场景" : "道具";
      contextParts.push(`【${title}资产对照】\n${labels.join("\n")}`);
    }
  };

  collectCategory("character", frame.character, characters, (asset) => asset?.name || "角色");
  collectCategory("scene", frame.scene, scenes, (asset) => asset?.title || "场景");
  collectCategory("prop", frame.prop, props, (asset) => asset?.name || "道具");

  const uniqueMissingNames = [...new Set(missingNames)];
  if (contextParts.length > 0) {
    description = `${description ? `${description}\n\n` : ""}${contextParts.join("\n\n")}`;
  }
  return {
    frameFound: true,
    description,
    referenceImages,
    missingNames: uniqueMissingNames,
  };
}
