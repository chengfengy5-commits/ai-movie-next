import type { AssetLibraryType, Character, Prop, Scene } from "./contracts";

export type AssetImageRecord = Character | Scene | Prop;

export type AssetImageDirectory =
  | { category: "characters"; items: Character[]; imageSources: ReadonlyMap<string, AssetImageSource> }
  | { category: "scenes"; items: Scene[]; imageSources: ReadonlyMap<string, AssetImageSource> }
  | { category: "props"; items: Prop[]; imageSources: ReadonlyMap<string, AssetImageSource> };

export interface AssetImageSource {
  readonly kind: "asset-image-source";
}

export interface AssetImageUploadReceipt {
  readonly kind: "asset-image-upload-receipt";
  readonly url: string;
}

export interface AssetImageUploadRunContext {
  actorId: string;
  seriesId: string;
  category: AssetLibraryType;
  assetId: string;
  directoryEpoch: number;
  portEpoch: number;
  asset: AssetImageRecord;
  isContextCurrent(): boolean;
}

export interface AssetImageUploadRun {
  isCurrent(): boolean;
  canSaveImage(receipt: AssetImageUploadReceipt): boolean;
  canReadImage(source: AssetImageSource | AssetImageUploadReceipt): boolean;
  readonly initialImageSource: AssetImageSource | null;
  uploadImage(file: File, signal: AbortSignal): Promise<AssetImageUploadReceipt>;
  saveImage(receipt: AssetImageUploadReceipt, signal: AbortSignal): Promise<AssetImageRecord>;
  readDirectory(signal: AbortSignal): Promise<AssetImageDirectory>;
  readImage(source: AssetImageSource | AssetImageUploadReceipt, signal: AbortSignal): Promise<Blob>;
}

export interface AssetImageUploadPort {
  beginRun(context: AssetImageUploadRunContext): AssetImageUploadRun;
}

export type AssetImageFailureKind = "not-sent" | "rejected" | "unknown" | "read-error";

export class AssetImageUploadError extends Error {
  readonly kind: AssetImageFailureKind;
  readonly status: number | null;

  constructor(kind: AssetImageFailureKind, message: string, status: number | null = null) {
    super(message);
    this.name = "AssetImageUploadError";
    this.kind = kind;
    this.status = status;
  }
}
