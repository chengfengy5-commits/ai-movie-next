export interface CanvasSaveGuard {
  mode: "guarded-v1";
  id: string | null;
  version: number;
}

export interface CanvasSaveReceipt {
  mode: "guarded-v1";
  id: string;
  version: number;
}

export interface CanvasEnvelope {
  id: string | null;
  chapter_id: string;
  version: number;
  document_json: unknown;
  updated_by: string | null;
  updated_at: string | null;
  save_capability?: "guarded-v1";
  save_receipt?: CanvasSaveReceipt;
}

export type CanvasDataErrorKind = "unsupported" | "conflict" | "unknown" | "rejected" | "unauthorized";

export class CanvasDataError extends Error {
  constructor(
    readonly kind: CanvasDataErrorKind,
    message: string,
    readonly status: number | null = null,
  ) {
    super(message);
    this.name = "CanvasDataError";
  }
}

export interface CanvasDataPort {
  get(chapterId: string, signal: AbortSignal): Promise<CanvasEnvelope>;
  save(
    chapterId: string,
    document: Record<string, unknown>,
    guard: CanvasSaveGuard,
    signal: AbortSignal,
  ): Promise<CanvasEnvelope>;
  readImage(imageUrl: string, signal: AbortSignal): Promise<Blob>;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function parseCanvasEnvelope(value: unknown, chapterId: string): CanvasEnvelope {
  if (!isRecord(value)
    || value.chapter_id !== chapterId
    || !(value.id === null || typeof value.id === "string")
    || typeof value.version !== "number"
    || !Number.isSafeInteger(value.version)
    || value.version <= 0
    || !(value.updated_by === null || typeof value.updated_by === "string")
    || !(value.updated_at === null || typeof value.updated_at === "string")) {
    throw new CanvasDataError("unknown", "画布服务返回了无法确认的保存结果。");
  }
  if (value.save_capability !== undefined && value.save_capability !== "guarded-v1") {
    throw new CanvasDataError("unknown", "画布服务返回了无法确认的保存结果。");
  }
  if (value.save_receipt !== undefined) {
    const receipt = value.save_receipt;
    if (!isRecord(receipt)
      || receipt.mode !== "guarded-v1"
      || typeof receipt.id !== "string"
      || typeof receipt.version !== "number"
      || !Number.isSafeInteger(receipt.version)
      || receipt.version <= 0) {
      throw new CanvasDataError("unknown", "画布服务返回了无法确认的保存结果。");
    }
  }
  return value as unknown as CanvasEnvelope;
}
