export type MediaReferenceKind = "image" | "audio" | "video";

export interface MediaReferenceUploadRunContext {
  readonly actorId: string;
  isContextCurrent(): boolean;
}

export interface MediaReferenceUploadRun {
  isCurrent(): boolean;
  upload(kind: MediaReferenceKind, file: File, signal: AbortSignal): Promise<string>;
}

export interface MediaReferenceUploadPort {
  beginRun(context: MediaReferenceUploadRunContext): MediaReferenceUploadRun;
}

export type MediaReferenceUploadFailureKind = "not-sent" | "rejected" | "unknown" | "unauthorized";

export class MediaReferenceUploadError extends Error {
  constructor(
    readonly kind: MediaReferenceUploadFailureKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "MediaReferenceUploadError";
  }
}
