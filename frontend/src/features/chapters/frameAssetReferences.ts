import type {
  Character,
  Prop,
  Scene,
  StoryboardFrame,
} from "../../shared/api/contracts";

export type FrameAssetReferenceCategory = "characters" | "scenes" | "props";

type AssetRecord = Character | Scene | Prop;
export type FrameAssetDirectory =
  | { category: "characters"; items: Character[] }
  | { category: "scenes"; items: Scene[] }
  | { category: "props"; items: Prop[] };

type AssetForCategory<Category extends FrameAssetReferenceCategory> = Category extends "characters"
  ? Character
  : Category extends "scenes"
    ? Scene
    : Prop;

export interface FrameAssetReferenceDetail {
  key: string;
  status: "resolved" | "unmatched" | "invalid";
  name: string | null;
  aliases: string[];
  description: string | null;
  traits: Array<{ label: string; value: string }>;
}

export type FrameAssetReferencesProjection =
  | { status: "unlinked"; rows: [] }
  | { status: "unreadable"; rows: [] }
  | { status: "references"; rows: FrameAssetReferenceDetail[]; hasValidReferences: boolean };

function referenceValue(frame: StoryboardFrame, category: FrameAssetReferenceCategory): unknown {
  switch (category) {
    case "characters":
      return frame.character;
    case "scenes":
      return frame.scene;
    case "props":
      return frame.prop;
  }
}

function assetName(category: FrameAssetReferenceCategory, asset: AssetRecord): string | null {
  const value: unknown = category === "scenes"
    ? (asset as Scene).title
    : (asset as Character | Prop).name;
  if (typeof value !== "string") {
    return null;
  }
  if (value.trim() !== "") {
    return value;
  }
  switch (category) {
    case "characters":
      return "未命名角色";
    case "scenes":
      return "未命名场景";
    case "props":
      return "未命名道具";
  }
}

function nonemptyText(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function assetTraits(category: FrameAssetReferenceCategory, asset: AssetRecord): Array<{ label: string; value: string }> {
  if (category !== "characters") {
    return [];
  }
  const character = asset as Character;
  const candidates = [
    { label: "身份", value: character.role },
    { label: "性别", value: character.gender },
    { label: "年龄", value: character.age },
    { label: "外观", value: character.appearance },
  ];
  return candidates.flatMap(({ label, value }) => {
    const text = nonemptyText(value);
    return text === null ? [] : [{ label, value: text }];
  });
}

function projectResolvedReference(
  category: FrameAssetReferenceCategory,
  asset: AssetRecord,
  index: number,
): FrameAssetReferenceDetail | null {
  const name = assetName(category, asset);
  if (name === null) {
    return null;
  }
  return {
    key: "reference-" + index,
    status: "resolved",
    name,
    aliases: Array.isArray(asset.aliases)
      ? asset.aliases.filter((alias): alias is string => typeof alias === "string" && alias.trim() !== "")
      : [],
    description: nonemptyText(asset.description),
    traits: assetTraits(category, asset),
  };
}

function invalidRow(index: number): FrameAssetReferenceDetail {
  return {
    key: "invalid-" + index,
    status: "invalid",
    name: null,
    aliases: [],
    description: null,
    traits: [],
  };
}

function unmatchedRow(index: number): FrameAssetReferenceDetail {
  return {
    key: "unmatched-" + index,
    status: "unmatched",
    name: null,
    aliases: [],
    description: null,
    traits: [],
  };
}

export function hasValidFrameAssetReference(
  frame: StoryboardFrame,
  category: FrameAssetReferenceCategory,
): boolean {
  const value = referenceValue(frame, category);
  return Array.isArray(value) && value.some((entry) => typeof entry === "string" && entry.trim() !== "");
}

export function projectFrameAssetReferences<Category extends FrameAssetReferenceCategory>(
  frame: StoryboardFrame,
  category: Category,
  seriesId: string,
  directory: AssetForCategory<Category>[],
): FrameAssetReferencesProjection {
  const value = referenceValue(frame, category);
  if (value === undefined || value === null || (Array.isArray(value) && value.length === 0)) {
    return { status: "unlinked", rows: [] };
  }
  if (!Array.isArray(value)) {
    return { status: "unreadable", rows: [] };
  }

  const rows = value.map((reference, index): FrameAssetReferenceDetail => {
    if (typeof reference !== "string" || reference.trim() === "") {
      return invalidRow(index);
    }

    const matches = (directory as AssetRecord[]).filter((asset) => asset.id === reference);
    if (matches.length !== 1 || matches[0]?.series_id !== seriesId) {
      return unmatchedRow(index);
    }

    const asset = matches[0];
    if (asset === undefined) {
      return unmatchedRow(index);
    }
    return projectResolvedReference(category, asset, index) ?? unmatchedRow(index);
  });

  return {
    status: "references",
    rows,
    hasValidReferences: value.some((entry) => typeof entry === "string" && entry.trim() !== ""),
  };
}

export function projectFrameAssetDirectory(
  frame: StoryboardFrame,
  category: FrameAssetReferenceCategory,
  seriesId: string,
  directory: FrameAssetDirectory | null,
): FrameAssetReferencesProjection {
  if (directory === null || directory.category !== category) {
    return projectFrameAssetReferences(frame, category, seriesId, []);
  }
  switch (directory.category) {
    case "characters":
      return projectFrameAssetReferences(frame, directory.category, seriesId, directory.items);
    case "scenes":
      return projectFrameAssetReferences(frame, directory.category, seriesId, directory.items);
    case "props":
      return projectFrameAssetReferences(frame, directory.category, seriesId, directory.items);
  }
}
