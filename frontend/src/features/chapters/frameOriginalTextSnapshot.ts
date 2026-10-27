import type { Chapter } from "../../shared/api/contracts";

export type FrameOriginalTextSnapshot =
  | { readonly kind: "text"; readonly value: string }
  | { readonly kind: "missing" }
  | { readonly kind: "unrecognized" };

export function captureFrameOriginalTextSnapshot(
  chapter: Chapter,
  position: number,
): FrameOriginalTextSnapshot {
  if (!Number.isInteger(position) || position < 1 || !Array.isArray(chapter.content)) {
    return { kind: "unrecognized" };
  }
  const frame: unknown = chapter.content[position - 1];
  if (frame === null || typeof frame !== "object" || Array.isArray(frame)) {
    return { kind: "unrecognized" };
  }
  if (!Object.prototype.hasOwnProperty.call(frame, "original_text")) {
    return { kind: "missing" };
  }
  const value = (frame as Record<string, unknown>).original_text;
  if (value === undefined) return { kind: "missing" };
  return typeof value === "string"
    ? { kind: "text", value }
    : { kind: "unrecognized" };
}
