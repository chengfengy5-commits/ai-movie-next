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
  readonly chapter_id: null;
  readonly frame_index: null;
  readonly name: null;
}

export interface FusedGenerationReceipt {
  readonly taskId: string;
  readonly messageId: string | null;
}

export interface FusedGenerationPort {
  submit(input: FusedGenerationInput, signal: AbortSignal): Promise<FusedGenerationReceipt>;
}

export type FusedGenerationJsonRequester = (
  input: FusedGenerationInput,
  signal: AbortSignal,
) => Promise<unknown>;
