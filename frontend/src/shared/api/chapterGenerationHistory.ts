export type ChapterGenerationHistoryTargetKind = "frame-preview" | "storyboard-image";
export type ChapterGenerationHistoryMode = "image" | "video";

export type ChapterGenerationHistoryReadOnlyReason =
  | "unreadable_chapter"
  | "missing_storyboard_id"
  | "duplicate_storyboard_id"
  | "asset_not_in_chapter"
  | "ambiguous_storyboard_asset"
  | "storyboard_index_mismatch"
  | "invalid_media_value";

export interface ChapterGenerationHistoryMessage {
  readonly id: string;
  readonly chapter_id: string;
  readonly frame_index: number | null;
  readonly asset_type: string | null;
  readonly asset_id: string | null;
  readonly chat_mode: string;
  readonly role: string;
  readonly content: string;
  readonly model_name: string | null;
  readonly created_at: string;
  readonly message_digest: string;
}

export interface ChapterGenerationHistoryTargetProof {
  readonly mapping_digest: string;
  readonly target_digest: string;
  readonly media_url: string | null;
}

export interface ChapterGenerationHistorySnapshot {
  readonly protocol_version: 1;
  readonly series_id: string;
  readonly chapter_id: string;
  readonly target_kind: ChapterGenerationHistoryTargetKind;
  readonly storyboard_asset_id: string | null;
  readonly frame_index: number;
  readonly chat_mode: ChapterGenerationHistoryMode;
  readonly writable: boolean;
  readonly read_only_reason: ChapterGenerationHistoryReadOnlyReason | null;
  readonly target_proof: ChapterGenerationHistoryTargetProof | null;
  readonly messages: readonly ChapterGenerationHistoryMessage[];
}

export interface ChapterGenerationHistoryContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  readonly targetKind: ChapterGenerationHistoryTargetKind;
  readonly storyboardAssetId: string | null;
  readonly frameIndex: number;
  readonly mode: ChapterGenerationHistoryMode;
  isContextCurrent(): boolean;
}

export interface ChapterGenerationHistoryLockIdentity {
  readonly user_id: string;
  readonly acquired_at: string;
}

export interface ChapterGenerationHistoryAdoptInput {
  readonly messageId: string;
  readonly expectedMessageDigest: string;
  readonly expectedMappingDigest: string;
  readonly expectedTargetDigest: string;
  readonly expectedMediaUrl: string | null;
  readonly expectedLockIdentity: ChapterGenerationHistoryLockIdentity | null;
}

export interface ChapterGenerationHistoryAdoptResult {
  readonly protocol_version: 1;
  readonly outcome: "applied" | "already_current";
  readonly series_id: string;
  readonly chapter_id: string;
  readonly target_kind: ChapterGenerationHistoryTargetKind;
  readonly storyboard_asset_id: string;
  readonly frame_index: number;
  readonly target_proof: ChapterGenerationHistoryTargetProof;
}

export interface ChapterGenerationHistoryRun {
  isCurrent(): boolean;
  readSnapshot(signal: AbortSignal): Promise<ChapterGenerationHistorySnapshot>;
  adopt(input: ChapterGenerationHistoryAdoptInput, signal: AbortSignal): Promise<ChapterGenerationHistoryAdoptResult>;
  retryOssUpload(message: ChapterGenerationHistoryMessage, signal: AbortSignal): Promise<string>;
  updateMessageContent(message: ChapterGenerationHistoryMessage, content: string, signal: AbortSignal): Promise<void>;
  deleteMessage(message: ChapterGenerationHistoryMessage, signal: AbortSignal): Promise<void>;
  readMedia(url: string, signal: AbortSignal): Promise<Blob>;
}

export interface ChapterGenerationHistoryPort {
  beginRun(context: ChapterGenerationHistoryContext): ChapterGenerationHistoryRun;
}

export type ChapterGenerationHistoryErrorKind =
  | "not-sent"
  | "rejected"
  | "unknown"
  | "conflict"
  | "read-error"
  | "unauthorized"
  | "invalid-response";

export class ChapterGenerationHistoryError extends Error {
  constructor(
    readonly kind: ChapterGenerationHistoryErrorKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "ChapterGenerationHistoryError";
  }
}
