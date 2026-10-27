export type AssetGenerationHistoryAssetType = "character" | "scene" | "prop";
export type AssetGenerationHistoryMode = "image" | "video";

export interface AssetGenerationHistoryMessage {
  readonly id: string;
  readonly chapter_id: string;
  readonly asset_type: string | null;
  readonly asset_id: string | null;
  readonly chat_mode: string;
  readonly role: string;
  readonly content: string;
  readonly model_name: string | null;
  readonly created_at: string;
}

export interface AssetGenerationHistoryRunContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly assetType: AssetGenerationHistoryAssetType;
  readonly assetId: string;
  readonly mode: AssetGenerationHistoryMode;
  isContextCurrent(): boolean;
}

export interface AssetGenerationHistoryRun {
  isCurrent(): boolean;
  readMessages(signal: AbortSignal): Promise<readonly AssetGenerationHistoryMessage[]>;
  readMedia(url: string, signal: AbortSignal): Promise<Blob>;
  retryOssUpload(url: string, signal: AbortSignal): Promise<string>;
  updateMessageContent(messageId: string, content: string, signal: AbortSignal): Promise<void>;
  deleteMessage(messageId: string, signal: AbortSignal): Promise<void>;
}

export interface AssetGenerationHistoryPort {
  beginRun(context: AssetGenerationHistoryRunContext): AssetGenerationHistoryRun;
}

export type AssetGenerationHistoryErrorKind =
  | "not-sent"
  | "rejected"
  | "unknown"
  | "read-error"
  | "unauthorized"
  | "invalid-response";

export class AssetGenerationHistoryError extends Error {
  constructor(
    readonly kind: AssetGenerationHistoryErrorKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "AssetGenerationHistoryError";
  }
}
