export type MediaGenerationOperation = "image.single" | "video.single" | "image.batch";

export type MediaGenerationAssetType = "character" | "scene" | "prop" | "storyboard";

export type MediaGenerationSingleTarget =
  | { readonly kind: "standalone" }
  | {
      readonly kind: "frame_message";
      readonly chapter_id: string;
      readonly storyboard_asset_id: string;
    }
  | {
      readonly kind: "asset_message";
      readonly chapter_id: string;
      readonly asset_type: MediaGenerationAssetType;
      readonly asset_id: string;
    };

export type MediaGenerationVideoTarget = MediaGenerationSingleTarget;

export interface MediaGenerationImageInput {
  readonly modelId: string;
  readonly prompt: string;
  readonly target: MediaGenerationSingleTarget;
  readonly size?: string;
  readonly referenceImages: readonly string[];
  readonly idempotencyKey: string;
}

export interface MediaGenerationVideoInput {
  readonly modelId: string;
  readonly prompt: string;
  readonly target: MediaGenerationVideoTarget;
  readonly size?: string;
  readonly duration?: number;
  readonly resolution?: string;
  readonly referenceImages: readonly string[];
  readonly referenceAudios: readonly string[];
  readonly referenceVideos: readonly string[];
  readonly idempotencyKey: string;
}

export interface MediaGenerationBatchAssetInput {
  readonly type: MediaGenerationAssetType;
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly modelId: string;
  readonly promptId: string;
  readonly size?: string;
  readonly referenceImages: readonly string[];
}

export interface MediaGenerationBatchInput {
  readonly chapterId: string;
  readonly assets: readonly MediaGenerationBatchAssetInput[];
  readonly idempotencyKey: string;
}

export type MediaGenerationTaskStatus =
  | "queued"
  | "processing"
  | "cancelling"
  | "reconciling"
  | "completed"
  | "failed";

export type MediaGenerationBillingStatus =
  | "legacy_unreconciled"
  | "unbilled"
  | "charged"
  | "partially_refunded"
  | "refunded"
  | "no_charge";

export interface VideoGenerationReceipt {
  readonly operation: "video.single";
  readonly submissionId: string;
  readonly taskId: string;
  readonly messageId: string | null;
  readonly status: MediaGenerationTaskStatus;
  readonly quotedAmount: number;
  readonly billingStatus: MediaGenerationBillingStatus;
}

export interface ImageGenerationReceipt {
  readonly operation: "image.single";
  readonly submissionId: string;
  readonly taskId: string;
  readonly messageId: string | null;
  readonly status: MediaGenerationTaskStatus;
  readonly quotedAmount: number;
  readonly billingStatus: MediaGenerationBillingStatus;
}

export interface BatchImageTaskReceipt {
  readonly type: MediaGenerationAssetType;
  readonly assetId: string;
  readonly assetName: string;
  readonly messageId: string;
  readonly taskId: string;
}

export interface BatchImageGenerationReceipt {
  readonly operation: "image.batch";
  readonly tasks: readonly BatchImageTaskReceipt[];
}

export type MediaGenerationReceipt = ImageGenerationReceipt | VideoGenerationReceipt | BatchImageGenerationReceipt;

export interface MediaGenerationRunContext {
  readonly actorId: string;
  isContextCurrent(): boolean;
}

export interface MediaGenerationLookupExpectation {
  readonly operation: MediaGenerationOperation;
  readonly idempotencyKey: string;
  readonly batchAssets?: readonly Pick<MediaGenerationBatchAssetInput, "type" | "id">[];
}

export interface MediaGenerationRun {
  isCurrent(): boolean;
  submitImage(input: MediaGenerationImageInput, signal: AbortSignal): Promise<ImageGenerationReceipt>;
  submitVideo(input: MediaGenerationVideoInput, signal: AbortSignal): Promise<VideoGenerationReceipt>;
  submitBatch(input: MediaGenerationBatchInput, signal: AbortSignal): Promise<BatchImageGenerationReceipt>;
  lookup(expectation: MediaGenerationLookupExpectation, signal: AbortSignal): Promise<MediaGenerationReceipt>;
}

export interface MediaGenerationPort {
  beginRun(context: MediaGenerationRunContext): MediaGenerationRun;
}

export type MediaGenerationFailureKind =
  | "not-sent"
  | "rejected"
  | "unknown-outcome"
  | "read-error"
  | "unauthorized"
  | "invalid-response";

export class MediaGenerationError extends Error {
  constructor(
    readonly kind: MediaGenerationFailureKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "MediaGenerationError";
  }
}
