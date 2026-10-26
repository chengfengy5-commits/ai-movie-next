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
  readonly referenceImages?: readonly string[];
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

const REFERENCE_RAW_SPACE_OR_CONTROL = /[\u0000-\u0020\u007f-\u009f]/u;
const ENCODED_REFERENCE_CONTROL_OR_BACKSLASH = /%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)/iu;
const DATA_IMAGE_REFERENCE_PREFIX = /^data:image\/(png|jpeg|webp|gif|bmp);base64,/u;
const ABSOLUTE_HTTP_REFERENCE = /^https?:\/\//iu;
const MAX_IMAGE_REFERENCE_COUNT = 8;
const MAX_IMAGE_REFERENCE_BYTES = 10 * 1024 * 1024;
const MAX_IMAGE_REFERENCE_DATA_URL_LENGTH = Math.ceil(MAX_IMAGE_REFERENCE_BYTES / 3) * 4 + 64;

function base64Sextet(characterCode: number): number {
  if (characterCode >= 65 && characterCode <= 90) return characterCode - 65;
  if (characterCode >= 97 && characterCode <= 122) return characterCode - 97 + 26;
  if (characterCode >= 48 && characterCode <= 57) return characterCode - 48 + 52;
  if (characterCode === 43) return 62;
  if (characterCode === 47) return 63;
  return -1;
}

function decodedReferenceDataBytes(value: string): number | null {
  const match = DATA_IMAGE_REFERENCE_PREFIX.exec(value);
  if (match === null) return null;
  const base64 = value.slice(match[0].length);
  if (base64 === "" || base64.length % 4 !== 0) return null;

  let paddingStart = -1;
  let padding = 0;
  for (let index = 0; index < base64.length; index += 1) {
    const characterCode = base64.charCodeAt(index);
    if (characterCode === 61) {
      if (paddingStart === -1) paddingStart = index;
      padding += 1;
      if (padding > 2) return null;
      continue;
    }
    if (paddingStart !== -1 || base64Sextet(characterCode) === -1) return null;
  }
  if ((padding === 1 && paddingStart % 4 !== 3)
    || (padding === 2 && paddingStart % 4 !== 2)) return null;

  const finalSextet = base64Sextet(base64.charCodeAt(base64.length - padding - 1));
  if ((padding === 2 && (finalSextet & 0x0f) !== 0) || (padding === 1 && (finalSextet & 0x03) !== 0)) {
    return null;
  }
  return (base64.length / 4) * 3 - padding;
}

export function isValidCanvasImageReference(value: unknown): value is string {
  if (typeof value !== "string" || value.trim() !== value || value === ""
    || REFERENCE_RAW_SPACE_OR_CONTROL.test(value) || value.includes("\\")
    || ENCODED_REFERENCE_CONTROL_OR_BACKSLASH.test(value)) return false;

  if (value.startsWith("data:")) {
    if (value.length > MAX_IMAGE_REFERENCE_DATA_URL_LENGTH) return false;
    const bytes = decodedReferenceDataBytes(value);
    return bytes !== null && bytes <= MAX_IMAGE_REFERENCE_BYTES;
  }

  if (!ABSOLUTE_HTTP_REFERENCE.test(value) || value.includes("#")) return false;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:")
      && url.hostname !== ""
      && url.username === ""
      && url.password === ""
      && url.hash === ""
      && value.length <= 8192;
  } catch {
    return false;
  }
}

export function normalizeCanvasImageReferences(value: unknown): readonly string[] | null {
  if (!Array.isArray(value) || value.length > MAX_IMAGE_REFERENCE_COUNT) return null;
  let totalDataBytes = 0;
  const references: string[] = [];
  for (const reference of value) {
    if (!isValidCanvasImageReference(reference)) return null;
    if (reference.startsWith("data:")) {
      const bytes = decodedReferenceDataBytes(reference);
      if (bytes === null) return null;
      totalDataBytes += bytes;
      if (totalDataBytes > MAX_IMAGE_REFERENCE_COUNT * MAX_IMAGE_REFERENCE_BYTES) return null;
    }
    references.push(reference);
  }
  return Object.freeze(references);
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
