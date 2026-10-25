import type { CanvasResultApplication } from "./canvasResultApplications";
import type { CanvasOwnedResultTargetSnapshot, CanvasOwnedResultsTarget } from "./canvasOwnedResults";

export type CanvasGenerationApplicationDestination = CanvasOwnedResultsTarget;

export interface CanvasGenerationApplicationCandidate {
  readonly task_id: string;
  readonly result_ref: string;
  readonly expected_target: CanvasOwnedResultTargetSnapshot;
  readonly application_key: string;
}

export type CanvasGenerationApplicationStatus = "applied" | "already_applied";

export interface CanvasGenerationApplicationResult {
  readonly task_id: string;
  readonly result_ref: string;
  readonly status: CanvasGenerationApplicationStatus;
  readonly application: CanvasResultApplication;
}

export interface CanvasGenerationApplicationRunContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  isContextCurrent(): boolean;
}

export interface CanvasGenerationApplicationRun {
  isCurrent(): boolean;
  apply(
    destination: CanvasGenerationApplicationDestination,
    candidate: CanvasGenerationApplicationCandidate,
    signal: AbortSignal,
  ): Promise<CanvasGenerationApplicationResult>;
}

export interface CanvasGenerationApplicationsPort {
  beginRun(context: CanvasGenerationApplicationRunContext): CanvasGenerationApplicationRun;
}

export type CanvasGenerationApplicationFailureKind =
  | "not-sent"
  | "unauthorized"
  | "forbidden"
  | "not-found"
  | "conflict"
  | "invalid-request"
  | "unknown-outcome";

export class CanvasGenerationApplicationError extends Error {
  constructor(
    readonly kind: CanvasGenerationApplicationFailureKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "CanvasGenerationApplicationError";
  }
}
