export interface CanvasResultApplication {
  readonly application_ref: string;
  readonly target_revision: string;
  readonly committed_at: string;
}

export type CanvasResultApplicationStatus = "applied" | "not_applied";

export interface CanvasResultApplicationLookup {
  readonly task_id: string;
  readonly result_ref: string;
  /** A point-in-time read; not_applied does not prove an earlier request can never commit later. */
  readonly status: CanvasResultApplicationStatus;
  readonly application: CanvasResultApplication | null;
}

export interface CanvasResultApplicationSelector {
  readonly taskId: string;
  readonly resultRef: string;
}

export interface CanvasResultApplicationRunContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  isContextCurrent(): boolean;
}

export interface CanvasResultApplicationRun {
  isCurrent(): boolean;
  lookup(
    selector: CanvasResultApplicationSelector,
    signal: AbortSignal,
  ): Promise<CanvasResultApplicationLookup>;
}

export interface CanvasResultApplicationsPort {
  beginRun(context: CanvasResultApplicationRunContext): CanvasResultApplicationRun;
}

export type CanvasResultApplicationFailureKind =
  | "not-sent"
  | "read-error"
  | "unauthorized"
  | "forbidden"
  | "not-found"
  | "unready"
  | "unavailable"
  | "invalid-response";

export class CanvasResultApplicationError extends Error {
  constructor(
    readonly kind: CanvasResultApplicationFailureKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "CanvasResultApplicationError";
  }
}
