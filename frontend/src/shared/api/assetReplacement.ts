import { InvalidResponseError } from "./contracts";
import type { AssetLibraryType } from "./contracts";

export type AssetReplacementType = "character" | "scene" | "prop";

export interface ReplaceChapterAssetInput {
  old_asset_id: string;
  new_asset_id: string;
  asset_type: AssetReplacementType;
}

export interface AssetReplacementReceipt {
  message: string;
}

export interface AssetReplacementRun {
  isCurrent(): boolean;
  replaceChapter(
    chapterId: string,
    input: ReplaceChapterAssetInput,
    signal: AbortSignal,
  ): Promise<AssetReplacementReceipt>;
}

export interface AssetReplacementPort {
  beginRun(): AssetReplacementRun;
}

export type AssetReplacementFailureKind = "not-sent" | "rejected" | "unknown";

export class AssetReplacementError extends Error {
  readonly kind: AssetReplacementFailureKind;
  readonly status: number | null;

  constructor(kind: AssetReplacementFailureKind, message: string, status: number | null = null) {
    super(message);
    this.name = "AssetReplacementError";
    this.kind = kind;
    this.status = status;
  }
}

export function toAssetReplacementType(category: AssetLibraryType): AssetReplacementType {
  switch (category) {
    case "characters": return "character";
    case "scenes": return "scene";
    case "props": return "prop";
  }
}

export function parseAssetReplacementReceipt(value: unknown): AssetReplacementReceipt {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new InvalidResponseError("素材替换回执格式无效。");
  }
  const message = (value as Record<string, unknown>).message;
  if (typeof message !== "string" || message.trim() === "") {
    throw new InvalidResponseError("素材替换回执缺少有效消息。");
  }
  return { message };
}
