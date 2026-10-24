export const MEDIA_EXPORT_MAX_ITEMS_PER_REQUEST = 500;
export const MEDIA_EXPORT_MAX_BYTES = 200 * 1024 * 1024;
export const MEDIA_EXPORT_TIMEOUT_MS = 10 * 60 * 1000;
export const MEDIA_EXPORT_LINK_TIMEOUT_MS = 15 * 1000;
export const MEDIA_EXPORT_LINK_BODY_MAX_BYTES = 8 * 1024 * 1024;

export type MediaExportSourceKind = "chapter" | "rough-cut";

export type MediaExportEligibility =
  | "ready"
  | "excluded"
  | "pending"
  | "missing"
  | "duplicate"
  | "invalid";

export type MediaExportItemState =
  | "not-started"
  | "queued"
  | "resolving"
  | "reading"
  | "writing"
  | "saved"
  | "download-started"
  | "failed"
  | "unknown"
  | "stopped";

export type MediaExportFailureStage =
  | "resolve"
  | "read"
  | "permission"
  | "directory"
  | "conflict"
  | "write"
  | "close"
  | "download";

export interface MediaExportQueueItem {
  readonly id: string;
  readonly sourceKind: MediaExportSourceKind;
  readonly chapterId: string;
  readonly sourceIndex: number;
  readonly frameIndex: number;
  readonly assetId: string | null;
  readonly sourceUrl: string | null;
  readonly filename: string;
  readonly eligibility: MediaExportEligibility;
  readonly state: MediaExportItemState;
  readonly message: string | null;
  readonly failureStage?: MediaExportFailureStage;
}

export interface MediaExportQueue {
  readonly runId: string;
  readonly outputDirectoryId?: string;
  readonly sourceKind: MediaExportSourceKind;
  readonly chapterId: string;
  readonly seriesId: string;
  readonly chapterName: string;
  readonly seriesName: string;
  readonly sourceRevision: string | number | null;
  readonly items: readonly MediaExportQueueItem[];
}

export interface MediaExportMedia {
  readonly blob: Blob;
  readonly mimeType: string;
  readonly extension: string;
  readonly finalUrl: string;
}

export type MediaExportSaveResult =
  | { readonly kind: "saved" }
  | { readonly kind: "download-started" };

export interface MediaExportSinkSaveContext {
  readonly signal: AbortSignal;
  readonly isCurrent: () => boolean;
}

export interface MediaExportSink {
  save(
    item: MediaExportQueueItem,
    media: MediaExportMedia,
    context?: MediaExportSinkSaveContext,
  ): Promise<MediaExportSaveResult>;
}

export type MediaExportSinkFailureKind = "failed" | "unknown";

export class MediaExportSinkError extends Error {
  constructor(
    readonly kind: MediaExportSinkFailureKind,
    readonly stage: "permission" | "directory" | "conflict" | "write" | "close" | "download",
    message: string,
  ) {
    super(message);
    this.name = "MediaExportSinkError";
  }
}
