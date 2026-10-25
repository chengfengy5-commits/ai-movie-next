export type CanvasOwnedImageErrorKind =
  | "not-sent"
  | "read-error"
  | "unauthorized"
  | "forbidden"
  | "not-found"
  | "unready"
  | "unavailable"
  | "invalid-response";

export class CanvasOwnedImageError extends Error {
  constructor(
    readonly kind: CanvasOwnedImageErrorKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "CanvasOwnedImageError";
  }
}

export interface CanvasOwnedImageRunContext {
  readonly actorId: string;
  readonly seriesId: string;
  readonly chapterId: string;
  isContextCurrent(): boolean;
}

export interface CanvasOwnedImageSelector {
  readonly taskId: string;
  readonly resultRef: string;
}

export interface CanvasOwnedImageRun {
  isCurrent(): boolean;
  read(selector: CanvasOwnedImageSelector, signal: AbortSignal): Promise<Blob>;
}

export interface CanvasOwnedImagePort {
  beginRun(context: CanvasOwnedImageRunContext): CanvasOwnedImageRun;
}
