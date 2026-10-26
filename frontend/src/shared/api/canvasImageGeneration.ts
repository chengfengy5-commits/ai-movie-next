import type { CanvasOwnedResultsTarget } from "./canvasOwnedResults";

export type CanvasImageGenerationTaskStatus =
  | "queued"
  | "processing"
  | "cancelling"
  | "reconciling"
  | "completed"
  | "failed";

export type CanvasImageGenerationBillingStatus =
  | "legacy_unreconciled"
  | "unbilled"
  | "charged"
  | "partially_refunded"
  | "refunded"
  | "no_charge";

export interface CanvasImageGenerationReceipt {
  readonly submissionId: string;
  readonly operation: "image.single";
  readonly taskId: string;
  readonly messageId: string | null;
  readonly status: CanvasImageGenerationTaskStatus;
  readonly quotedAmount: number;
  /** Server-reported billing state; this client does not independently reconcile it. */
  readonly billingStatus: CanvasImageGenerationBillingStatus;
}

export interface CanvasImageGenerationSubmitInput {
  readonly modelId: string;
  readonly prompt: string;
  readonly destination: CanvasOwnedResultsTarget;
  readonly size?: string;
  /** Exact key chosen by the caller for this explicit submission. */
  readonly idempotencyKey: string;
}

const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/u;

export function isValidCanvasImageGenerationSize(value: unknown): value is string {
  return typeof value === "string"
    && Array.from(value).length <= 100
    && value.trim() !== ""
    && !value.includes("://")
    && !CONTROL_CHARACTERS.test(value);
}

export interface CanvasImageGenerationRunContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  isContextCurrent(): boolean;
}

export interface CanvasImageGenerationRun {
  isCurrent(): boolean;
  submit(
    input: CanvasImageGenerationSubmitInput,
    signal: AbortSignal,
  ): Promise<CanvasImageGenerationReceipt>;
  /** A 404 is a point-in-time miss and does not resolve a pending POST's outcome. */
  lookup(
    idempotencyKey: string,
    signal: AbortSignal,
  ): Promise<CanvasImageGenerationReceipt>;
}

export interface CanvasImageGenerationPort {
  beginRun(context: CanvasImageGenerationRunContext): CanvasImageGenerationRun;
}

export type CanvasImageGenerationFailureKind =
  | "not-sent"
  | "rejected"
  | "unknown-outcome"
  | "read-error"
  | "unauthorized"
  | "forbidden"
  | "not-found"
  | "unavailable"
  | "invalid-response";

export class CanvasImageGenerationError extends Error {
  constructor(
    readonly kind: CanvasImageGenerationFailureKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "CanvasImageGenerationError";
  }
}
