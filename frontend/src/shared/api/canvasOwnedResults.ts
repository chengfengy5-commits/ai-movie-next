export type CanvasOwnedResultsFrameTarget =
  | {
      readonly kind: "frame_preview";
      readonly chapter_id: string;
      readonly storyboard_asset_id: string;
    }
  | {
      readonly kind: "frame_preview";
      readonly chapter_id: string;
      readonly identity_mode: "strict-no-id-v1";
      readonly frame_index: number;
      readonly expected_chapter_digest: string;
    };

export interface CanvasOwnedResultsAssetTarget {
  readonly kind: "asset_image";
  readonly chapter_id: string;
  readonly asset_type: "character" | "scene" | "prop";
  readonly asset_id: string;
}

export type CanvasOwnedResultsTarget =
  | CanvasOwnedResultsFrameTarget
  | CanvasOwnedResultsAssetTarget;

export interface CanvasOwnedResultTargetSnapshot {
  readonly kind: "canvas_frame_preview" | "canvas_asset_image";
  readonly chapter_id: string;
  readonly series_id: string;
  readonly stable_storyboard_asset_id: string | null;
  readonly asset_type: string | null;
  readonly asset_id: string | null;
  readonly compat_frame_index: number | null;
  readonly expected_text_digest: string | null;
  readonly media_identity: string | null;
  readonly content_digest: string | null;
  readonly notes_revision: number | null;
  readonly full_frame_digest: string | null;
  readonly full_chapter_digest: string | null;
  readonly expected_asset_image_url: string | null;
  readonly same_series_target_digest: string | null;
}

export type CanvasOwnedResultEligibility =
  | "ready"
  | "already_applied"
  | "stale"
  | "target_deleted"
  | "unready";

export interface CanvasOwnedResultApplicationProof {
  readonly application_ref: string;
  readonly target_revision: string;
  readonly committed_at: string;
}

export interface CanvasOwnedResultItem {
  readonly task_id: string;
  readonly result_ref: string;
  readonly operation: "image.single" | "image.batch";
  readonly completed_at: string;
  readonly expected_target: CanvasOwnedResultTargetSnapshot | null;
  readonly application_key: string | null;
  readonly eligibility: CanvasOwnedResultEligibility;
  readonly application: CanvasOwnedResultApplicationProof | null;
}

export interface CanvasOwnedResultsPage {
  readonly chapter_id: string;
  readonly target: CanvasOwnedResultsTarget;
  readonly items: readonly CanvasOwnedResultItem[];
  readonly next_cursor: string | null;
}

export interface CanvasOwnedResultsPageOptions {
  readonly limit?: number;
  readonly cursor?: string;
}

export interface CanvasOwnedResultsRunContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  isContextCurrent(): boolean;
}

export interface CanvasOwnedResultsRun {
  isCurrent(): boolean;
  page(
    target: CanvasOwnedResultsTarget,
    signal: AbortSignal,
    options?: CanvasOwnedResultsPageOptions,
  ): Promise<CanvasOwnedResultsPage>;
}

export interface CanvasOwnedResultsPort {
  beginRun(context: CanvasOwnedResultsRunContext): CanvasOwnedResultsRun;
}

export type CanvasOwnedResultsFailureKind =
  | "not-sent"
  | "read-error"
  | "unauthorized"
  | "forbidden"
  | "not-found"
  | "invalid-query"
  | "unavailable"
  | "invalid-response";

export class CanvasOwnedResultsError extends Error {
  constructor(
    readonly kind: CanvasOwnedResultsFailureKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "CanvasOwnedResultsError";
  }
}
