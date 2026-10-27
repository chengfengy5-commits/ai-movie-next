export type FusedGenerationMemberType = "character" | "prop";

export type FusedGenerationMember =
  | { readonly kind: "asset"; readonly id: string; readonly name: string }
  | { readonly kind: "image"; readonly url: string; readonly name: string };

export interface FusedGenerationInput {
  readonly series_id: string;
  readonly member_type: FusedGenerationMemberType;
  readonly members: readonly FusedGenerationMember[];
  readonly model_id: string;
  readonly prompt_id: string;
  readonly prompt_system: string | null;
  readonly prompt_user: string | null;
  readonly size: string | null;
  readonly chapter_id: string | null;
  readonly frame_index: number | null;
  readonly name: null;
  readonly storyboard_asset_id?: string | null;
  readonly expected_frame?: Readonly<Record<string, unknown>> | null;
  readonly expected_content?: readonly unknown[] | null;
}

export interface FusedGenerationReceipt {
  readonly taskId: string;
  readonly messageId: string | null;
}

export type FusedSubmissionStatus =
  | "accepted"
  | "result_known"
  | "provider_result_unknown"
  | "media_result_unknown"
  | "charged_incomplete"
  | "rejected_after_charge"
  | "completed"
  | "failed";

export interface FusedSubmissionReceipt {
  readonly status: FusedSubmissionStatus;
  readonly feeDisposition: "debit" | "byok" | "zero" | null;
  readonly feeAmount: number | null;
  readonly taskId: string | null;
  readonly messageId: string | null;
}

export interface FusedGenerationSubmitOptions {
  readonly idempotencyKey: string;
}

export interface FusedGenerationPort {
  submit(
    input: FusedGenerationInput,
    signal: AbortSignal,
    options?: FusedGenerationSubmitOptions,
  ): Promise<FusedGenerationReceipt>;
  lookupSubmission?(idempotencyKey: string, signal: AbortSignal): Promise<FusedSubmissionReceipt>;
}

export type FusedGenerationJsonRequester = (
  input: FusedGenerationInput,
  signal: AbortSignal,
  options?: FusedGenerationSubmitOptions,
) => Promise<unknown>;

export type FusedSubmissionJsonLookup = (
  idempotencyKey: string,
  signal: AbortSignal,
) => Promise<unknown>;
