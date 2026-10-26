import { isValidCanvasImageReference } from "../../../shared/api/canvasImageGeneration";

const MAX_LOCAL_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_PROVIDER_REFERENCE_IMAGES = 8;
const IMAGE_MIME_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"]);
const INVALID_CONFIGURATION = "该图片模型的参考图配置无效，无法安全提交。";

export interface CanvasImageReferenceCandidate {
  readonly id: string;
  readonly name: string;
  readonly value: string;
}

export interface CanvasImageReferenceConfiguration {
  readonly enabled: boolean;
  readonly maximum: number;
  readonly error: string | null;
}

interface ReferenceGraphNode {
  readonly ref: string;
  readonly kind: string;
  readonly title: string;
  readonly imageUrl: string | null;
}

interface ReferenceGraphEdge {
  readonly sourceRef: string;
  readonly targetRef: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function noConfiguration(): CanvasImageReferenceConfiguration {
  return Object.freeze({ enabled: true, maximum: MAX_PROVIDER_REFERENCE_IMAGES, error: null });
}

function validCount(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1 && value <= 64;
}

export function projectCanvasImageReferenceConfiguration(params: string | null): CanvasImageReferenceConfiguration {
  if (params === null || params === "") return noConfiguration();

  let parsed: unknown;
  try {
    parsed = JSON.parse(params) as unknown;
  } catch {
    return Object.freeze({ ...noConfiguration(), error: INVALID_CONFIGURATION });
  }
  if (!isRecord(parsed)) return Object.freeze({ ...noConfiguration(), error: INVALID_CONFIGURATION });
  const raw = parsed.max_reference_images;
  if (raw === undefined || raw === null) return noConfiguration();
  if (!isRecord(raw)) return Object.freeze({ ...noConfiguration(), error: INVALID_CONFIGURATION });

  const enabled = raw.enabled === undefined ? true : raw.enabled;
  if (typeof enabled !== "boolean") return Object.freeze({ ...noConfiguration(), error: INVALID_CONFIGURATION });

  const hasOptions = raw.options !== undefined && raw.options !== null;
  let options: readonly number[] = [];
  if (hasOptions) {
    if (!Array.isArray(raw.options) || raw.options.length === 0 || !raw.options.every(validCount)) {
      return Object.freeze({ ...noConfiguration(), error: INVALID_CONFIGURATION });
    }
    options = raw.options;
  }

  const hasDefault = raw.default !== undefined;
  if (hasDefault && !validCount(raw.default)) {
    return Object.freeze({ ...noConfiguration(), error: INVALID_CONFIGURATION });
  }
  const configuredMaximum = hasDefault
    ? raw.default as number
    : hasOptions
      ? options.reduce((maximum, option) => Math.max(maximum, option), 0)
      : 9;
  if (hasDefault && hasOptions && !options.includes(configuredMaximum)) {
    return Object.freeze({ ...noConfiguration(), error: INVALID_CONFIGURATION });
  }
  return Object.freeze({
    enabled,
    maximum: enabled ? Math.min(configuredMaximum, MAX_PROVIDER_REFERENCE_IMAGES) : 0,
    error: null,
  });
}

function candidateValue(value: string | null): value is string {
  return value !== null && (isSafeCanvasUploadPath(value) || isValidCanvasImageReference(value));
}

export function isSafeCanvasUploadPath(value: string): boolean {
  if (!value.startsWith("/uploads/") || value.length > 8192
    || /[\u0000-\u0020\u007f-\u009f\\?#]/u.test(value)) return false;
  let pathname: string;
  try {
    const parsed = new URL(value, "https://canvas-upload.invalid");
    if (parsed.origin !== "https://canvas-upload.invalid" || parsed.search !== "" || parsed.hash !== "") return false;
    pathname = parsed.pathname;
  } catch {
    return false;
  }
  if (!pathname.startsWith("/uploads/")) return false;
  const segments = pathname.slice(1).split("/");
  return !segments.some((part) => part === "" || part === "." || part === ".." || /%2f|%5c|%25/iu.test(part));
}

function safeName(value: string, fallback: string): string {
  const cleaned = value.replace(/[\u0000-\u001f\u007f-\u009f]/gu, "").trim();
  if (cleaned === "" || cleaned.length > 80 || /(?:https?:\/\/|data:|token=|signature=|\/uploads\/|[?&#=])/iu.test(cleaned)) {
    return fallback;
  }
  return cleaned;
}

export function collectCanvasImageReferenceCandidates(
  nodes: readonly ReferenceGraphNode[],
  edges: readonly ReferenceGraphEdge[],
  selectedRef: string,
): readonly CanvasImageReferenceCandidate[] {
  const nodesByRef = new Map(nodes.map((node) => [node.ref, node]));
  const orderedRefs = [selectedRef];
  for (const edge of edges) {
    if (edge.sourceRef === selectedRef) orderedRefs.push(edge.targetRef);
    else if (edge.targetRef === selectedRef) orderedRefs.push(edge.sourceRef);
  }

  const seenValues = new Set<string>();
  const candidates: CanvasImageReferenceCandidate[] = [];
  for (const ref of orderedRefs) {
    const node = nodesByRef.get(ref);
    if (node === undefined || node.kind === "video" || !candidateValue(node.imageUrl) || seenValues.has(node.imageUrl)) continue;
    seenValues.add(node.imageUrl);
    const ordinal = candidates.length + 1;
    candidates.push(Object.freeze({
      id: ref,
      name: safeName(node.title, `参考图 ${ordinal}`),
      value: node.imageUrl,
    }));
  }
  return Object.freeze(candidates);
}

export function safeCanvasImageReferenceName(value: string, fallback: string): string {
  return safeName(value, fallback);
}

export class CanvasImageReferencePreparationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CanvasImageReferencePreparationError";
  }
}

export function isSupportedCanvasImageFile(file: File): boolean {
  return IMAGE_MIME_TYPES.has(file.type.toLowerCase()) && file.size > 0 && file.size <= MAX_LOCAL_IMAGE_BYTES;
}

export function readCanvasImageBlobAsDataUrl(blob: Blob, signal: AbortSignal): Promise<string> {
  if (signal.aborted) return Promise.reject(new CanvasImageReferencePreparationError("参考图读取已取消。"));
  if (blob.size <= 0 || blob.size > MAX_LOCAL_IMAGE_BYTES || !IMAGE_MIME_TYPES.has(blob.type.toLowerCase())) {
    return Promise.reject(new CanvasImageReferencePreparationError("参考图必须是10 MiB以内的 PNG、JPEG、WebP、GIF 或 BMP 图片。"));
  }

  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    const cleanup = () => signal.removeEventListener("abort", abortReader);
    const abortReader = () => reader.abort();
    reader.addEventListener("load", () => {
      cleanup();
      try {
        const result = reader.result;
        const comma = typeof result === "string" ? result.indexOf(",") : -1;
        const normalized = typeof result !== "string" || comma < 0
          ? null
          : `data:${blob.type.toLowerCase()};base64,${result.slice(comma + 1)}`;
        if (normalized === null || !isValidCanvasImageReference(normalized)) {
          reject(new CanvasImageReferencePreparationError("参考图读取结果无法安全确认。"));
          return;
        }
        resolve(normalized);
      } catch {
        reject(new CanvasImageReferencePreparationError("参考图读取结果无法安全确认。"));
      }
    }, { once: true });
    reader.addEventListener("error", () => {
      cleanup();
      reject(new CanvasImageReferencePreparationError("参考图读取失败，请重新选择图片。"));
    }, { once: true });
    reader.addEventListener("abort", () => {
      cleanup();
      reject(new CanvasImageReferencePreparationError("参考图读取已取消。"));
    }, { once: true });
    signal.addEventListener("abort", abortReader, { once: true });
    try {
      reader.readAsDataURL(blob);
    } catch {
      cleanup();
      reject(new CanvasImageReferencePreparationError("参考图读取失败，请重新选择图片。"));
    }
  });
}

export const MAX_CANVAS_IMAGE_REFERENCE_COUNT = MAX_PROVIDER_REFERENCE_IMAGES;
